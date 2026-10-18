"""默认文字调用链「v2 存在时 v2 是唯一来源」的开发者测试。

只用假凭据 + pytest 临时目录 + mock transport：不读真实私有配置、不调用真实模型、
不发任何网络请求。业务降级都走真实文案入口 build_content_package，而不是只调
text_request_config 就宣称链路已验证。

计数用的是「尝试构造外部 HTTP 客户端 / 发出 POST」的次数，Mock 只记录不抛错：
否则一旦误回落 v1，请求异常会被 except 吞掉，看起来同样是降级，就测不出来了。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from app.core import capability_config, llm_config
from app.core.content_generator import build_content_package
from app.core.llm_client import LLMClientError, build_vision_messages, chat_completion_json
from app.core.llm_config import LLMConfig, LocalTextConfig, cloud_authorization
from tests.test_content_generator import _build_pack, _reports

TEXT_KEY = "text-capability-key-112233"
LEGACY_KEY = "legacy-v1-key-445566"
VISION_KEY = "vision-explicit-key-556677"
ENV_KEY = "fake-environment-key-must-not-be-used-778899"
TAILS = ("112233", "445566", "556677", "778899")

TEXT_BASE = "https://text.invalid/v1"
V1_BASE = "https://legacy.invalid/v1"
VISION_BASE = "https://vision.invalid/v1"
# 本机执行位置只收回环地址，所以切本机的用例一律用这个地址。
LOCAL_TEXT_BASE = "http://127.0.0.1:11434/v1"

CHANNELS = ("xiaohongshu", "douyin", "kuaishou", "shopping", "visual", "video")
CLOUD_REPLY = {channel: {"title": f"云端标题-{channel}", "body": f"云端正文-{channel}"}
               for channel in CHANNELS}


def v2_path() -> Path:
    return capability_config.capability_config_path()


def assert_no_secret(payload: str) -> None:
    for tail in TAILS:
        assert tail not in payload, f"输出里出现了密钥片段：{tail}"


def content_report(tmp_path: Path) -> dict:
    """真实文案入口：扫描素材包 → 证据 → 理解 → 方向 → 内容包。"""
    pack = _build_pack(tmp_path)
    directions, understanding, extraction = _reports(pack)
    return build_content_package(directions, understanding, extraction).to_dict()


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


def record_http(monkeypatch, module: str = "app.core.llm_client"):
    """客户端工厂与传输层都只记录，不抛错，便于把「零请求」当成实测数字而非副作用巧合。"""
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    response = Mock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": json.dumps(
        CLOUD_REPLY, ensure_ascii=False)}}]}
    transport.post.return_value = response
    factory = Mock(return_value=transport)
    monkeypatch.setattr(f"{module}.httpx.Client", factory)
    return factory, transport


def recording_loader(cfg: LLMConfig | None = None) -> Mock:
    return Mock(return_value=cfg or LLMConfig(LEGACY_KEY, V1_BASE, "legacy-model", 30, 4))


def exploding_loader() -> Mock:
    return Mock(side_effect=AssertionError("v2 文件存在时不得加载 v1 配置"))


def disabled_settings() -> dict:
    return llm_config.default_capability_settings()


def unconfirmed_settings(**text_changes) -> dict:
    settings = llm_config.migrate_legacy_settings({"api_key": LEGACY_KEY, "base_url": V1_BASE,
                                                   "model": "legacy-model", "timeout_seconds": 30,
                                                   "max_images": 4})
    settings["capabilities"]["text"].update(text_changes)
    return settings


# --- 1. v2 不存在：迁移前行为，v1 文字链路照旧 ---

def test_without_a_v2_file_the_text_chain_still_uses_v1(tmp_path, monkeypatch):
    write_v1()
    loader = recording_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    report = content_report(tmp_path)
    assert loader.call_count == 1
    assert factory.call_count == 1 and transport.post.call_count == 1
    assert transport.post.call_args.args[0] == f"{V1_BASE}/chat/completions"
    assert transport.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {LEGACY_KEY}"
    assert transport.post.call_args.kwargs["json"]["model"] == "legacy-model"
    assert set(report["generation_sources"].values()) == {"cloud"}
    assert report["source"] == "cloud"
    assert not v2_path().exists(), "选配置这一步不许创建 v2 文件"


def test_without_a_v2_file_and_no_key_configured_the_chain_stays_template(tmp_path, monkeypatch):
    # 迁移前的另一半行为：v1 里没有密钥时照旧落回模板，一次请求都不发。
    loader = recording_loader(LLMConfig(None, V1_BASE, "legacy-model", 30, 4))
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    report = content_report(tmp_path)
    assert loader.call_count == 1
    assert factory.call_count == 0 and transport.post.call_count == 0
    assert report["source"] == "template"


# --- 2. v2 存在但 text 禁用：停止调用，落回模板，不读 v1 ---

def test_disabled_text_degrades_to_template_without_touching_v1(tmp_path, monkeypatch):
    write_v1()
    capability_config.write_capability_settings(disabled_settings())
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        report = content_report(tmp_path)
    assert report["source"] == "template"
    assert set(report["generation_sources"].values()) == {"template"}
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()
    assert_no_secret(json.dumps(report, ensure_ascii=False))


def test_disabled_text_stops_the_low_level_request_too(tmp_path, monkeypatch):
    write_v1()
    capability_config.write_capability_settings(disabled_settings())
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_client.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True), pytest.raises(LLMClientError) as error:
        chat_completion_json([{"role": "user", "content": "hi"}])
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()
    assert_no_secret(str(error.value))
    assert "环境变量" not in str(error.value) and "AIPC" not in str(error.value)


# --- 3. v2 text 待确认：不许调用 ---

def test_pending_confirmation_text_degrades_to_template(tmp_path, monkeypatch):
    write_v1()
    capability_config.write_capability_settings(unconfirmed_settings())
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["text"]
    assert on_disk["needs_confirmation"] is True and on_disk["enabled"] is False
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    for name in ("AIPC_LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(name, ENV_KEY)
    with cloud_authorization(True):
        report = content_report(tmp_path)
    assert report["source"] == "template"
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()
    assert ENV_KEY not in json.dumps(report, ensure_ascii=False)
    assert_no_secret(json.dumps(report, ensure_ascii=False))


def test_enabled_yet_unconfirmed_is_still_refused(tmp_path, monkeypatch):
    # 第一批校验允许 enabled=True 与 needs_confirmation=True 共存，这个形状也必须拒绝调用。
    capability_config.write_capability_settings(unconfirmed_settings(enabled=True,
                                                                      adapter="openai_chat"))
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["text"]
    assert on_disk["enabled"] is True and on_disk["needs_confirmation"] is True
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        report = content_report(tmp_path)
    assert report["source"] == "template"
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()


# --- 4. v2 text 启用且已确认：请求参数全部来自 v2 ---

def test_confirmed_text_sends_the_v2_address_model_key_and_timeout(tmp_path, monkeypatch):
    write_v1()
    enable_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    report = content_report(tmp_path)
    assert factory.call_count == 1 and transport.post.call_count == 1
    kwargs = transport.post.call_args.kwargs
    url = transport.post.call_args.args[0]
    assert url == f"{TEXT_BASE}/chat/completions" and V1_BASE not in url
    assert kwargs["headers"]["Authorization"] == f"Bearer {TEXT_KEY}"
    assert kwargs["json"]["model"] == "text-model"
    assert factory.call_args.kwargs["timeout"] == 45
    assert "image_url" not in json.dumps(kwargs["json"])
    assert LEGACY_KEY not in json.dumps([url, kwargs["headers"], kwargs["json"]])
    loader.assert_not_called()
    assert report["source"] == "cloud"
    assert_no_secret(json.dumps(report, ensure_ascii=False))


def test_confirmed_text_config_carries_zero_image_limit(tmp_path):
    enable_text()
    cfg = capability_config.text_request_config(exploding_loader())
    assert cfg.max_images == 0
    assert cfg == LLMConfig(TEXT_KEY, TEXT_BASE, "text-model", 45, 0)


# --- 5. v2 损坏 / 非法版本 / 非法字段：不回退、不修复 ---

@pytest.mark.parametrize("payload", [
    "{ not json",
    "",
    '{"schema_version": 1, "capabilities": {}}',
    '{"schema_version": 3, "capabilities": {}}',
    json.dumps({"schema_version": 2, "capabilities": {name: llm_config._default_capability_entry(name)
                for name in llm_config.CAPABILITY_NAMES},
                "capabilities_extra": 1}),
    json.dumps({"schema_version": 2, "capabilities": dict(
        {name: llm_config._default_capability_entry(name) for name in llm_config.CAPABILITY_NAMES},
        text=dict(llm_config._default_capability_entry("text"), enabled=True, adapter="openai_chat",
                  base_url=TEXT_BASE, model="text-model", timeout_seconds=0, api_key=TEXT_KEY))}),
])
def test_unreadable_v2_stops_the_text_chain_without_repair(tmp_path, monkeypatch, payload):
    write_v1()
    v1_before = llm_config.CONFIG_PATH.read_bytes()
    v2_path().write_text(payload, encoding="utf-8")
    v2_before = v2_path().read_bytes()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    for name in ("AIPC_LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(name, ENV_KEY)
    with cloud_authorization(True):
        report = content_report(tmp_path)
    assert report["source"] == "template"
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()
    assert v2_path().read_bytes() == v2_before, "坏文件必须原样保留，不许顺手修复"
    assert llm_config.CONFIG_PATH.read_bytes() == v1_before
    rendered = json.dumps(report, ensure_ascii=False)
    assert_no_secret(rendered)
    assert ENV_KEY not in rendered and TEXT_BASE not in rendered


def test_unreadable_v2_error_text_does_not_echo_the_file(tmp_path, monkeypatch):
    v2_path().write_text(json.dumps({"schema_version": 2, "capabilities": {
        "text": dict(llm_config._default_capability_entry("text"), api_key=TEXT_KEY,
                     base_url=TEXT_BASE), "vision": llm_config._default_capability_entry("vision"),
        "image": llm_config._default_capability_entry("image"),
        "video": llm_config._default_capability_entry("video")}, "surprise": TEXT_KEY}),
        encoding="utf-8")
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_client.load_llm_config", loader)
    factory, _ = record_http(monkeypatch)
    with cloud_authorization(True), pytest.raises(LLMClientError) as error:
        chat_completion_json([{"role": "user", "content": "hi"}])
    assert factory.call_count == 0
    loader.assert_not_called()
    assert_no_secret(str(error.value))
    assert TEXT_BASE not in str(error.value) and "surprise" not in str(error.value)


# --- 6. v2 text 选本地执行位置：真发本机请求，但绝不回落云端 v1 ---

def enable_local_text(**changes) -> dict:
    """本机文字的地址必须是回环，且这一格结构上没有密钥。"""
    payload = {"execution_location": "local", "base_url": LOCAL_TEXT_BASE,
               "credential_action": "clear", "api_key": None}
    payload.update(changes)
    return enable_text(**payload)


def test_local_text_calls_the_local_endpoint_and_labels_the_output_local(tmp_path, monkeypatch):
    write_v1()
    enable_local_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    # 云端授权关掉也照发：本机这一支不受 CLOUD_AUTHORIZED 管，这是 LocalTextConfig 不带 .enabled 的含义。
    with cloud_authorization(False):
        report = content_report(tmp_path)
    assert report["source"] == "local"
    assert set(report["generation_sources"].values()) == {"local"}
    assert factory.call_count == 1 and transport.post.call_count == 1
    assert transport.post.call_args.args[0] == f"{LOCAL_TEXT_BASE}/chat/completions"
    assert "Authorization" not in transport.post.call_args.kwargs["headers"]
    body = transport.post.call_args.kwargs["json"]
    assert body["max_tokens"] == llm_config.LOCAL_TEXT_DEFAULT_MAX_TOKENS
    assert body["model"] == "text-model"
    loader.assert_not_called()
    assert_no_secret(json.dumps(report, ensure_ascii=False))


def test_local_text_failure_stops_at_one_attempt_without_cloud_fallback(tmp_path, monkeypatch):
    write_v1()
    enable_local_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    transport.post.side_effect = ConnectionError("本机服务没开")
    with cloud_authorization(True):
        report = content_report(tmp_path)
    # 只尝试一次：本机失败后不追加云端第二次请求，也不借用磁盘上的 v1 密钥。
    assert report["source"] == "template"
    assert set(report["generation_sources"].values()) == {"template"}
    assert factory.call_count == 1 and transport.post.call_count == 1
    loader.assert_not_called()
    assert LEGACY_KEY not in json.dumps(report, ensure_ascii=False)
    assert_no_secret(json.dumps(report, ensure_ascii=False))


def test_local_text_config_still_never_reads_the_v1_loader(tmp_path, monkeypatch):
    write_v1()
    enable_local_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    record_http(monkeypatch)
    with cloud_authorization(True):
        content_report(tmp_path)
    loader.assert_not_called()
    assert not hasattr(capability_config, "_LOCAL_TEXT_NOT_WIRED")


def _reply(transport, content: str, finish_reason: str) -> None:
    """把桩响应换成指定正文与 finish_reason：截断判据只能从响应体里来。"""
    response = Mock(status_code=200)
    response.json.return_value = {"choices": [{"message": {"content": content},
                                               "finish_reason": finish_reason}]}
    transport.post.return_value = response


def test_local_text_hitting_the_output_cap_is_labelled_as_such(tmp_path, monkeypatch):
    write_v1()
    enable_local_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    _reply(transport, json.dumps(CLOUD_REPLY, ensure_ascii=False), "length")
    with cloud_authorization(True):
        report = content_report(tmp_path)
    assert report["source"] == "local_truncated"
    assert set(report["generation_sources"].values()) == {"local_truncated"}
    assert factory.call_count == 1 and transport.post.call_count == 1
    loader.assert_not_called()


def test_cloud_reply_with_length_finish_reason_is_still_adopted(tmp_path, monkeypatch):
    """云端返回 finish_reason=length 是本批没动过的既有行为：不标上限，也不改来源。"""
    write_v1()
    enable_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    _reply(transport, json.dumps(CLOUD_REPLY, ensure_ascii=False), "length")
    with cloud_authorization(True):
        report = content_report(tmp_path)
    assert report["source"] == "cloud"
    assert set(report["generation_sources"].values()) == {"cloud"}
    assert transport.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {TEXT_KEY}"
    assert "max_tokens" not in transport.post.call_args.kwargs["json"]


def test_truncated_json_that_still_parses_is_not_adopted_either(tmp_path, monkeypatch):
    write_v1()
    enable_local_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    # 截点正好落在最后一个渠道正文中间、前面的键还收得住：这种"看起来完整"的输出同样不许当模型成果。
    body = json.dumps(CLOUD_REPLY, ensure_ascii=False)
    _reply(transport, body[:len(body) - 30] + '"}}', "length")
    with cloud_authorization(True):
        report = content_report(tmp_path)
    assert set(report["generation_sources"].values()) == {"local_truncated"}
    for item in report["items"]:
        assert not item["body"].startswith("云端正文")
    assert factory.call_count == 1


# --- 7. 未授权：配置启用不等于请求授权 ---

def test_unauthorized_request_makes_zero_calls_even_with_enabled_text(tmp_path, monkeypatch):
    write_v1()
    enable_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(False):
        report = content_report(tmp_path)
    assert report["source"] == "template"
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()


# --- 8. 显式传入的 config 不被 text 覆盖（商品图片理解走的正是这条路） ---

def test_explicit_config_is_never_replaced_by_the_text_capability(tmp_path, monkeypatch):
    enable_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_client.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    vision = LLMConfig(VISION_KEY, VISION_BASE, "vision-model", 7, 3)
    with cloud_authorization(True):
        chat_completion_json([{"role": "user", "content": "看图"}], config=vision)
    assert factory.call_count == 1
    kwargs = transport.post.call_args.kwargs
    assert transport.post.call_args.args[0] == f"{VISION_BASE}/chat/completions"
    assert kwargs["headers"]["Authorization"] == f"Bearer {VISION_KEY}"
    assert kwargs["json"]["model"] == "vision-model"
    assert factory.call_args.kwargs["timeout"] == 7
    assert TEXT_BASE not in str(transport.post.call_args_list) and TEXT_KEY not in str(
        transport.post.call_args_list)
    loader.assert_not_called()


def test_vision_image_limit_is_unchanged_by_the_text_rule(tmp_path):
    pack = _build_pack(tmp_path)
    images = sorted((pack / "商品图片").glob("*.png"))
    assert len(images) == 4
    messages = build_vision_messages("system", "user", images,
                                     config=LLMConfig(VISION_KEY, VISION_BASE, "vision-model", 7, 3))
    parts = messages[1]["content"]
    assert [part["type"] for part in parts].count("image_url") == 3
    enable_text()
    cfg = capability_config.text_request_config(exploding_loader())
    assert cfg.max_images == 0


# --- 9. 两条支路构造客户端的参数差异（代理行为）：本机不理代理，云端照旧理 ---

def test_local_text_client_is_built_without_proxy_or_redirects(monkeypatch):
    """钉的是构造参数本身：本机文字必须和连接探针、本机图片理解同款，不读系统代理、不跟跳转。

    只断言「请求成功」是不够的——代理环境在测试里并不存在，请求照样能过 Mock 成功。
    """
    factory, transport = record_http(monkeypatch)
    chat_completion_json([{"role": "user", "content": "本机"}],
                         config=LocalTextConfig(LOCAL_TEXT_BASE, "local-text-model", 45))
    assert factory.call_count == 1 and transport.post.call_count == 1
    kwargs = factory.call_args.kwargs
    assert kwargs["timeout"] == 45
    assert kwargs["trust_env"] is False, "本机文字不许读系统代理，否则探针说通了、真实生成仍会被代理半路截走"
    assert kwargs["follow_redirects"] is False, "本机地址不需要跳转，跟跳转等于把请求交给一个配置里没写的主机"


def test_cloud_text_client_still_uses_the_default_proxy_behaviour(monkeypatch):
    """反面对照：云端不许被顺手改成不理代理，否则公司网络里出不去。

    断言的是「没有这一组参数」，不是「值为 True」——缺省才等于改动前后行为逐字相同。
    """
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        chat_completion_json([{"role": "user", "content": "云端"}],
                             config=LLMConfig(TEXT_KEY, TEXT_BASE, "cloud-text-model", 45, 0))
    assert factory.call_count == 1 and transport.post.call_count == 1
    kwargs = factory.call_args.kwargs
    assert kwargs["timeout"] == 45
    assert "trust_env" not in kwargs, "本批只放开本机那一支，云端构造参数必须一个都没多"
    assert "follow_redirects" not in kwargs
