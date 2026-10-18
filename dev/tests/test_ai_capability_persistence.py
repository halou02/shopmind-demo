"""v2 四能力配置的落盘、读取、迁移候选与 text 调用链接线的开发者测试。

只用假凭据 + pytest 临时目录 + mock transport：不读真实私有配置、不调用真实模型、
不发任何网络请求。v2 路径随 tests/conftest.py 对 llm_config.CONFIG_PATH 的重定向一起
落到临时目录（capability_config.capability_config_path() 现取父目录）。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core import capability_config, llm_config
from app.core.capability_config import CapabilityConfigError
from app.core.llm_client import LLMClientError, chat_completion_json
from app.core.llm_config import LLMConfig, LocalTextConfig, cloud_authorization

FAKE_KEY = "unit-test-fake-secret-900123"
TEXT_KEY = "text-capability-key-112233"
LEGACY_KEY = "legacy-v1-key-445566"
ENV_KEY = "fake-environment-key-must-not-be-used-778899"
TAILS = ("900123", "112233", "445566", "778899")

V1_BASE = "https://legacy.invalid/v1"
TEXT_BASE = "https://text.invalid/v1"
# 本机执行位置只收回环地址，所以凡是要把 text 切到本机的用例都得换成这个地址。
LOCAL_TEXT_BASE = "http://127.0.0.1:11434/v1"
REAL_REPO_ROOT = Path(llm_config.__file__).resolve().parents[2]


def v2_path() -> Path:
    return capability_config.capability_config_path()


def assert_no_secret(text: str) -> None:
    assert FAKE_KEY not in text
    for tail in TAILS:
        assert tail not in text


def enable_text(**changes) -> dict:
    payload = {"enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
               "base_url": TEXT_BASE, "model": "text-model", "timeout_seconds": 45,
               "credential_action": "replace", "api_key": TEXT_KEY}
    payload.update(changes)
    return capability_config.update_capability_settings("text", payload)


def write_v1(*, base_url: str = V1_BASE, model: str = "legacy-model",
             timeout: float = 30, max_images: int = 4, api_key: str = LEGACY_KEY) -> None:
    llm_config.save_llm_settings(base_url=base_url, model=model, timeout_seconds=timeout,
                                 max_images=max_images, api_key=api_key)


def fake_transport(monkeypatch, *, reply: str = '{"product_name": "包"}'):
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    response = Mock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": reply}}]}
    transport.post.return_value = response
    factory = Mock(return_value=transport)
    monkeypatch.setattr("app.core.llm_client.httpx.Client", factory)
    return transport, factory


# --- 1. 路径与隔离 ---

def test_v2_has_its_own_private_path_next_to_v1_inside_tmp(tmp_path):
    assert capability_config.CAPABILITY_CONFIG_FILENAME == ".ai-capabilities.private.json"
    assert v2_path() == tmp_path / ".ai-capabilities.private.json"
    assert v2_path() != llm_config.CONFIG_PATH
    assert tmp_path in v2_path().parents


def test_the_real_workspace_capability_file_is_never_created(tmp_path):
    # 不断言真实根目录「没有」v2 文件（正常使用就会产出它），只断言本测试没有碰真实根目录。
    def snapshot():
        return sorted((entry.name, entry.stat().st_size, entry.stat().st_mtime_ns)
                      for entry in os.scandir(REAL_REPO_ROOT) if entry.name.startswith(".ai-"))

    assert v2_path().parent == tmp_path
    before = snapshot()
    enable_text()
    assert snapshot() == before
    assert v2_path().is_file()


# --- 2. v2 缺失：默认值或迁移候选，且绝不写盘 ---

def test_reading_without_any_private_file_keeps_every_capability_disabled(tmp_path, monkeypatch):
    for name in ("AIPC_LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(name, ENV_KEY)
    settings = capability_config.read_capability_settings()
    assert settings["schema_version"] == 2
    assert set(settings["capabilities"]) == set(llm_config.CAPABILITY_NAMES)
    for name in llm_config.CAPABILITY_NAMES:
        assert settings["capabilities"][name]["enabled"] is False
        assert settings["capabilities"][name]["api_key"] is None
    assert ENV_KEY not in json.dumps(settings)
    assert not v2_path().exists() and not llm_config.CONFIG_PATH.exists()
    assert not list(tmp_path.iterdir())


def test_v1_on_disk_becomes_an_unconfirmed_candidate_that_is_not_written(tmp_path):
    write_v1(max_images=6)
    settings = capability_config.read_capability_settings()
    text = settings["capabilities"]["text"]
    assert text["api_key"] == LEGACY_KEY and text["base_url"] == V1_BASE
    assert text["model"] == "legacy-model" and text["timeout_seconds"] == 30
    assert text["enabled"] is False and text["needs_confirmation"] is True
    assert settings["legacy_migration"] == {"source_schema_version": 1,
                                           "needs_confirmation": ["text"],
                                           "unmapped": {"max_images": 6}}
    defaults = llm_config.default_capability_settings()["capabilities"]
    for name in ("vision", "image", "video"):
        assert settings["capabilities"][name] == defaults[name]
        assert settings["capabilities"][name]["api_key"] is None
    assert settings["capabilities"]["vision"]["max_images"] == 0
    assert not v2_path().exists()


def test_migration_candidate_never_opens_the_v1_loader(tmp_path, monkeypatch):
    loader = Mock(side_effect=AssertionError("迁移函数不得自行读取 v1 配置"))
    monkeypatch.setattr(llm_config, "load_llm_config", loader)
    candidate = capability_config.migration_candidate({"base_url": V1_BASE, "model": "legacy-model",
                                                       "timeout_seconds": 30, "api_key": LEGACY_KEY,
                                                       "max_images": 4})
    assert candidate["capabilities"]["text"]["needs_confirmation"] is True
    loader.assert_not_called()


def test_v1_absent_but_environment_present_yields_no_candidate(tmp_path, monkeypatch):
    for name in ("AIPC_LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(name, ENV_KEY)
    assert capability_config.read_legacy_data() is None
    assert capability_config.read_capability_settings()["capabilities"]["text"]["api_key"] is None
    assert not v2_path().exists()


def test_migration_failure_writes_no_v2_file(tmp_path):
    # 直接写一个地址非法的 v1 文件：load_llm_config 不校验地址，迁移校验会拒绝它
    llm_config.CONFIG_PATH.write_text(json.dumps({"api_key": LEGACY_KEY,
                                                  "base_url": "http://not-loopback.invalid/v1",
                                                  "model": "legacy-model", "timeout_seconds": 30,
                                                  "max_images": 4}), encoding="utf-8")
    with pytest.raises(CapabilityConfigError) as error:
        capability_config.read_capability_settings()
    assert_no_secret(str(error.value))
    assert "http://not-loopback.invalid" not in str(error.value)
    assert not v2_path().exists()


# --- 3. 复用第一批契约，而不是另写一套规则 ---

def test_store_reuses_the_batch_one_contract_functions(tmp_path, monkeypatch):
    calls = {"validate": 0, "apply": 0, "migrate": 0}
    real_validate = llm_config.validate_capability_settings
    real_apply = llm_config.apply_capability_update
    real_migrate = llm_config.migrate_legacy_settings
    monkeypatch.setattr(llm_config, "validate_capability_settings",
                        lambda s: calls.__setitem__("validate", calls["validate"] + 1) or real_validate(s))
    monkeypatch.setattr(llm_config, "apply_capability_update",
                        lambda s, c, ch: calls.__setitem__("apply", calls["apply"] + 1) or real_apply(s, c, ch))
    monkeypatch.setattr(llm_config, "migrate_legacy_settings",
                        lambda legacy: calls.__setitem__("migrate", calls["migrate"] + 1) or real_migrate(legacy))
    write_v1()
    written = enable_text()
    assert calls["migrate"] == 1 and calls["apply"] == 1 and calls["validate"] >= 2
    assert written["capabilities"]["text"]["config_revision"] == 1


# --- 4. 原子写入 ---

def test_write_lands_as_schema_two_with_four_capabilities(tmp_path):
    written = enable_text()
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))
    assert on_disk == written
    assert on_disk["schema_version"] == 2
    assert set(on_disk["capabilities"]) == set(llm_config.CAPABILITY_NAMES)
    assert on_disk["capabilities"]["text"]["api_key"] == TEXT_KEY
    assert v2_path().read_text(encoding="utf-8")
    assert not list(tmp_path.glob(".ai-capabilities-*.tmp"))


def test_write_uses_temp_flush_fsync_replace_and_0600(tmp_path, monkeypatch):
    events = []
    real_fsync, real_replace, real_chmod = os.fsync, os.replace, os.chmod
    monkeypatch.setattr(os, "fsync", lambda fd: (events.append("fsync"), real_fsync(fd))[1])
    monkeypatch.setattr(os, "replace", lambda src, dst: (events.append(("replace", Path(src), Path(dst))),
                                                          real_replace(src, dst))[1])
    monkeypatch.setattr(os, "chmod", lambda p, mode: (events.append(("chmod", Path(p), mode)),
                                                      real_chmod(p, mode))[1])
    enable_text()
    assert "fsync" in events
    replace = next(item for item in events if isinstance(item, tuple) and item[0] == "replace")
    assert replace[1].parent == tmp_path and replace[1].name.startswith(".ai-capabilities-")
    assert replace[2] == v2_path()
    chmod = next(item for item in events if isinstance(item, tuple) and item[0] == "chmod")
    assert chmod[1] == replace[1] and chmod[2] == 0o600
    assert not list(tmp_path.glob(".ai-capabilities-*.tmp"))


def test_replace_failure_leaves_the_original_file_and_removes_the_temp(tmp_path, monkeypatch):
    enable_text(model="first-model")
    before = v2_path().read_text(encoding="utf-8")

    def refuse(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", refuse)
    with pytest.raises(CapabilityConfigError) as error:
        enable_text(model="second-model")
    assert_no_secret(str(error.value))
    assert v2_path().read_text(encoding="utf-8") == before
    assert "second-model" not in before
    assert not list(tmp_path.glob(".ai-capabilities-*.tmp"))


def test_validation_rejection_writes_nothing(tmp_path):
    enable_text()
    before = v2_path().read_text(encoding="utf-8")
    with pytest.raises(ValueError):
        capability_config.update_capability_settings("text", {"timeout_seconds": 0})
    with pytest.raises(ValueError):
        capability_config.update_capability_settings("text", {"unknown_field": "x"})
    assert v2_path().read_text(encoding="utf-8") == before
    assert not list(tmp_path.glob(".ai-capabilities-*.tmp"))


def test_v1_file_stays_untouched_after_a_v2_write(tmp_path):
    write_v1()
    before = llm_config.CONFIG_PATH.read_text(encoding="utf-8")
    enable_text()
    assert llm_config.CONFIG_PATH.read_text(encoding="utf-8") == before
    assert before.count(LEGACY_KEY) == 1
    assert json.loads(before)["max_images"] == 4


# --- 5. 坏文件：脱敏、不覆盖、不回退 ---

@pytest.mark.parametrize("payload", [
    "{ not json",
    "",
    "[]",
    "{}",
    '{"schema_version": 1, "capabilities": {}}',
    '{"schema_version": 3, "capabilities": {}}',
    json.dumps({"capabilities": {}}),
    json.dumps({"schema_version": 2, "capabilities": {"text": {}}}),
    json.dumps({"schema_version": 2, "extra": 1, "capabilities": {name: llm_config._default_capability_entry(name)
                for name in llm_config.CAPABILITY_NAMES}}),
    json.dumps({"schema_version": 2, "capabilities": dict(
        {name: llm_config._default_capability_entry(name) for name in llm_config.CAPABILITY_NAMES},
        text={"enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
              "base_url": TEXT_BASE, "model": "text-model", "timeout_seconds": 45,
              "api_key": FAKE_KEY, "config_revision": 0, "needs_confirmation": False,
              "surprise": FAKE_KEY})}),
])
def test_broken_v2_is_reported_without_echo_and_is_not_repaired(tmp_path, payload):
    v2_path().write_text(payload, encoding="utf-8")
    before = v2_path().read_bytes()
    with pytest.raises(CapabilityConfigError) as error:
        capability_config.read_capability_settings()
    message = str(error.value)
    assert message == capability_config._UNREADABLE
    assert_no_secret(message)
    assert TEXT_BASE not in message and "surprise" not in message
    with pytest.raises(CapabilityConfigError):
        capability_config.update_capability_settings("text", {"model": "new-model"})
    assert v2_path().read_bytes() == before
    assert not list(tmp_path.glob(".ai-capabilities-*.tmp"))


def test_broken_v2_never_falls_back_to_environment_or_v1(tmp_path, monkeypatch):
    write_v1()
    v1_before = llm_config.CONFIG_PATH.read_text(encoding="utf-8")
    v2_path().write_text("{ broken", encoding="utf-8")
    for name in ("AIPC_LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(name, ENV_KEY)
    outgoing = Mock(side_effect=AssertionError("No external HTTP allowed"))
    monkeypatch.setattr("httpx.Client", outgoing)
    loader = Mock(side_effect=AssertionError("坏文件不能变成读 v1 的理由"))
    monkeypatch.setattr("app.core.llm_client.load_llm_config", loader)
    with cloud_authorization(True), pytest.raises(LLMClientError) as error:
        chat_completion_json([], config=None)
    assert_no_secret(str(error.value))
    assert ENV_KEY not in str(error.value)
    assert "环境变量" not in str(error.value)
    assert llm_config.CONFIG_PATH.read_text(encoding="utf-8") == v1_before
    outgoing.assert_not_called()
    loader.assert_not_called()


# --- 6. text 请求配置取值规则 ---

def legacy_config() -> LLMConfig:
    return LLMConfig(LEGACY_KEY, V1_BASE, "legacy-model", 30, 4)


def refusing_loader() -> Mock:
    """v2 已落盘时的 v1 取配置方式：一旦被调用就说明回落了旧配置。"""
    return Mock(side_effect=AssertionError("v2 文件存在时不得加载 v1 配置"))


def test_absent_v2_keeps_the_v1_text_behavior(tmp_path):
    loader = Mock(return_value=legacy_config())
    assert capability_config.text_request_config(loader) == legacy_config()
    assert loader.call_count == 1
    assert not v2_path().exists()


def test_disabled_text_stops_instead_of_reusing_v1(tmp_path):
    capability_config.write_capability_settings(llm_config.default_capability_settings())
    loader = refusing_loader()
    with pytest.raises(CapabilityConfigError) as error:
        capability_config.text_request_config(loader)
    assert error.value.args[0] == capability_config._TEXT_DISABLED
    assert_no_secret(str(error.value))
    assert "环境变量" not in str(error.value) and "AIPC" not in str(error.value)
    loader.assert_not_called()
    assert not llm_config.CONFIG_PATH.exists()


def test_needs_confirmation_text_stops_even_when_enabled(tmp_path):
    # 第一批校验允许 enabled=True 与 needs_confirmation=True 共存，这种形状同样不能发出请求。
    settings = llm_config.migrate_legacy_settings({"api_key": LEGACY_KEY, "base_url": V1_BASE,
                                                   "model": "legacy-model", "timeout_seconds": 30,
                                                   "max_images": 4})
    settings["capabilities"]["text"].update({"enabled": True, "adapter": "openai_chat"})
    capability_config.write_capability_settings(settings)
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))
    assert on_disk["capabilities"]["text"]["enabled"] is True
    assert on_disk["capabilities"]["text"]["needs_confirmation"] is True
    loader = refusing_loader()
    with pytest.raises(CapabilityConfigError) as error:
        capability_config.text_request_config(loader)
    assert error.value.args[0] == capability_config._TEXT_NEEDS_CONFIRMATION
    assert_no_secret(str(error.value))
    loader.assert_not_called()


def test_enabled_text_replaces_url_model_key_and_timeout(tmp_path):
    enable_text()
    result = capability_config.text_request_config(refusing_loader())
    assert result.api_key == TEXT_KEY and result.base_url == TEXT_BASE
    assert result.model == "text-model" and result.timeout_seconds == 45
    assert result.max_images == 0
    assert result.enabled is True


def test_local_text_returns_a_keyless_config_without_touching_v1(tmp_path):
    # 旧口径是抛「本批尚未接入」；本机文字接通后这里改钉三件事：取到的是无密钥类型、不读 v1、上限用默认档。
    enable_text(execution_location="local", base_url=LOCAL_TEXT_BASE,
                credential_action="clear", api_key=None)
    loader = refusing_loader()
    result = capability_config.text_request_config(loader)
    assert isinstance(result, LocalTextConfig)
    assert result.base_url == LOCAL_TEXT_BASE and result.model == "text-model"
    assert result.timeout_seconds == 45
    assert result.max_tokens == llm_config.LOCAL_TEXT_DEFAULT_MAX_TOKENS
    assert not hasattr(result, "api_key") and not hasattr(result, "enabled")
    loader.assert_not_called()
    assert_no_secret(repr(result))


def test_chat_completion_request_actually_uses_the_text_capability(tmp_path, monkeypatch):
    enable_text()
    write_v1()
    transport, factory = fake_transport(monkeypatch)
    with cloud_authorization(True):
        assert chat_completion_json([{"role": "user", "content": "hi"}]) == {"product_name": "包"}
    url = transport.post.call_args.args[0]
    headers = transport.post.call_args.kwargs["headers"]
    body = transport.post.call_args.kwargs["json"]
    assert url == f"{TEXT_BASE}/chat/completions"
    assert headers["Authorization"] == f"Bearer {TEXT_KEY}"
    assert body["model"] == "text-model"
    assert factory.call_args.kwargs["timeout"] == 45
    assert LEGACY_KEY not in json.dumps([url, headers, body])
    assert V1_BASE not in url


def test_disabled_text_stops_the_default_request_path(tmp_path, monkeypatch):
    write_v1()
    capability_config.write_capability_settings(llm_config.default_capability_settings())
    transport, factory = fake_transport(monkeypatch)
    loader = Mock(side_effect=AssertionError("v2 存在时默认文字链路不得加载 v1"))
    monkeypatch.setattr("app.core.llm_client.load_llm_config", loader)
    with cloud_authorization(True), pytest.raises(LLMClientError) as error:
        chat_completion_json([{"role": "user", "content": "hi"}])
    assert transport.post.call_count == 0 and factory.call_count == 0
    loader.assert_not_called()
    assert_no_secret(str(error.value))
    assert "环境变量" not in str(error.value)


def test_content_generator_passes_the_text_capability_down(tmp_path, monkeypatch):
    from app.core.content_generator import build_content_package
    from tests.test_content_generator import _build_pack as build_pack, _reports

    enable_text()
    write_v1()
    directions, understanding, extraction = _reports(build_pack(tmp_path))
    seen = []

    def spy(messages, *, config=None, temperature=0.3, meta=None):
        seen.append(config)
        return {"xiaohongshu": {"title": "云端", "body": "生成的文案"},
                "douyin": {"title": "云端", "body": "生成的口播"}}
    monkeypatch.setattr("app.core.llm_content_generator.chat_completion_json", spy)
    report = build_content_package(directions, understanding, extraction).to_dict()
    assert len(seen) == 1
    for cfg in seen:
        assert cfg.base_url == TEXT_BASE and cfg.model == "text-model"
        assert cfg.api_key == TEXT_KEY and cfg.timeout_seconds == 45
        assert cfg.max_images == 0
    assert report["source"] == "mixed"
    assert LEGACY_KEY not in json.dumps(report, ensure_ascii=False)
