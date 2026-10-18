"""四能力 AI 配置（schema_version=2）数据契约的开发者测试。

全部使用假凭据与纯数据函数：不读真实私有配置、不调用 load_llm_config、不发任何网络请求。
"""
from __future__ import annotations

import copy
import json
from unittest.mock import Mock

import pytest

from app.core import llm_config

FAKE_KEY = "unit-test-fake-secret-900123"
FAKE_TAIL = "900123"
FAKE_FILE_KEY = "key-from-disk-file-must-be-ignored-445566"
FAKE_ENV_KEY = "fake-environment-key-must-not-be-used-778899"


def legacy(**changes) -> dict:
    data = {"api_key": FAKE_KEY, "base_url": "https://legacy.invalid/v1",
            "model": "legacy-model", "timeout_seconds": 30, "max_images": 4}
    data.update(changes)
    return data


def cap(settings: dict, capability: str) -> dict:
    return settings["capabilities"][capability]


def enable(capability: str, settings: dict | None = None, *, key: str | None = FAKE_KEY,
           adapter: str | None = None, **changes) -> dict:
    adapters = llm_config.SUPPORTED_ADAPTERS[capability]
    assert adapters, f"{capability} 应当没有已接入协议，不能用于启用测试"
    payload = {"enabled": True, "execution_location": "cloud",
               "adapter": adapter or adapters[0], "base_url": "https://api.invalid/v1",
               "model": "chosen-model", "credential_action": "replace" if key else "keep"}
    if key:
        payload["api_key"] = key
    if capability == "vision":
        payload["max_images"] = 3
    payload.update(changes)
    return llm_config.apply_capability_update(
        settings if settings is not None else llm_config.default_capability_settings(), capability, payload)


def text_variant(**changes) -> dict:
    settings = llm_config.default_capability_settings()
    return {**settings, "capabilities": {**settings["capabilities"],
                                         "text": {**cap(settings, "text"), **changes}}}


def assert_no_secret(text: str) -> None:
    assert FAKE_KEY not in text and FAKE_TAIL not in text


# --- 1. 默认新版配置 ---

def test_default_settings_disable_every_capability_without_credentials():
    settings = llm_config.default_capability_settings()
    assert settings["schema_version"] == 2
    assert list(settings["capabilities"]) == list(llm_config.CAPABILITY_NAMES)
    for name in llm_config.CAPABILITY_NAMES:
        entry = cap(settings, name)
        assert entry["enabled"] is False
        assert entry["api_key"] is None
        assert entry["adapter"] == "" and entry["base_url"] == "" and entry["model"] == ""
        assert entry["config_revision"] == 0 and entry["needs_confirmation"] is False
        assert entry["execution_location"] in llm_config.EXECUTION_LOCATIONS
        assert 1 <= entry["timeout_seconds"] <= 600
    assert cap(settings, "vision")["max_images"] == 0
    assert "max_images" not in cap(settings, "text")
    public = llm_config.public_capability_settings(settings)
    for name in llm_config.CAPABILITY_NAMES:
        assert public["capabilities"][name]["has_api_key"] is False
        assert public["capabilities"][name]["needs_confirmation"] is False
    assert "legacy_migration" not in settings


# --- 2. 旧格式迁移 ---

def test_migration_preserves_legacy_values_into_text_only():
    defaults = llm_config.default_capability_settings()
    migrated = llm_config.migrate_legacy_settings(legacy())
    text = cap(migrated, "text")
    assert text["base_url"] == "https://legacy.invalid/v1"
    assert text["model"] == "legacy-model"
    assert text["timeout_seconds"] == 30
    assert text["api_key"] == FAKE_KEY
    assert text["enabled"] is False and text["needs_confirmation"] is True
    assert text["config_revision"] == 0
    for name in ("vision", "image", "video"):
        assert cap(migrated, name) == cap(defaults, name)
    assert cap(migrated, "vision")["max_images"] == 0
    assert migrated["legacy_migration"] == {"source_schema_version": 1,
                                            "needs_confirmation": ["text"],
                                            "unmapped": {"max_images": 4}}
    public = llm_config.public_capability_settings(migrated)
    assert public["capabilities"]["text"]["needs_confirmation"] is True
    assert public["capabilities"]["text"]["has_api_key"] is True
    assert public["capabilities"]["vision"]["needs_confirmation"] is False
    assert public["capabilities"]["vision"]["has_api_key"] is False
    assert public["legacy_migration"] == migrated["legacy_migration"]


def test_migration_never_mutates_input_and_never_writes_files(tmp_path, monkeypatch):
    assert llm_config.CONFIG_PATH.parent == tmp_path
    loader = Mock(side_effect=AssertionError("本批函数不得读取本机私有配置"))
    monkeypatch.setattr(llm_config, "load_llm_config", loader)
    source = legacy()
    snapshot = copy.deepcopy(source)

    def untouched() -> None:
        assert source == snapshot
        assert list(tmp_path.iterdir()) == []
        assert not llm_config.CONFIG_PATH.exists()

    untouched()
    settings = llm_config.migrate_legacy_settings(source)
    untouched()
    for call in (llm_config.default_capability_settings,
                 lambda: llm_config.validate_capability_settings(settings),
                 lambda: llm_config.public_capability_settings(settings),
                 lambda: llm_config.apply_capability_update(settings, "text", {"model": "next-model"}),
                 lambda: llm_config.migrate_legacy_settings(source)):
        call()
        untouched()
    loader.assert_not_called()


def test_migration_uses_only_the_argument_not_any_file_on_disk(tmp_path):
    assert llm_config.CONFIG_PATH.parent == tmp_path
    disk = legacy(api_key=FAKE_FILE_KEY, model="disk-model")
    llm_config.CONFIG_PATH.write_text(json.dumps(disk), encoding="utf-8")
    before = llm_config.CONFIG_PATH.read_bytes()
    migrated = llm_config.migrate_legacy_settings(legacy())
    assert cap(migrated, "text")["api_key"] == FAKE_KEY
    assert cap(migrated, "text")["model"] == "legacy-model"
    assert FAKE_FILE_KEY not in json.dumps(migrated, default=str)
    assert llm_config.CONFIG_PATH.read_bytes() == before


def test_migration_requires_the_legacy_shape():
    for bad in ("x", None, [], {"base_url": "https://legacy.invalid/v1"},
                {**legacy(), "unexpected": FAKE_KEY},
                {**legacy(), "base_url": "http://legacy.invalid/v1"},
                {**legacy(), "timeout_seconds": 900},
                {**legacy(), "max_images": 99}):
        with pytest.raises(ValueError) as error:
            llm_config.migrate_legacy_settings(bad)
        assert_no_secret(str(error.value))


# --- 3. 单能力更新与隔离 ---

def test_single_capability_update_leaves_other_capabilities_untouched():
    settings = llm_config.migrate_legacy_settings(legacy())
    updated = llm_config.apply_capability_update(settings, "vision", {
        "enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
        "base_url": "https://vision.invalid/v1", "model": "vision-model",
        "max_images": 2, "timeout_seconds": 45,
        "credential_action": "replace", "api_key": "vision-only-key-112233"})
    for name in ("image", "video"):
        assert cap(updated, name) == cap(settings, name)
    assert cap(updated, "text") == cap(settings, "text")
    assert cap(updated, "vision")["api_key"] == "vision-only-key-112233"
    assert cap(updated, "text")["needs_confirmation"] is True
    assert cap(updated, "vision")["needs_confirmation"] is False
    assert cap(updated, "vision")["config_revision"] == 1
    assert cap(updated, "text")["config_revision"] == 0
    assert updated["legacy_migration"]["needs_confirmation"] == ["text"]
    assert updated["legacy_migration"]["unmapped"] == {"max_images": 4}
    no_copy = llm_config.apply_capability_update(settings, "image", {"model": "image-model"})
    assert cap(no_copy, "image")["api_key"] is None
    assert cap(no_copy, "text")["api_key"] == FAKE_KEY


def test_update_clears_needs_confirmation_for_the_edited_capability():
    settings = llm_config.migrate_legacy_settings(legacy())
    updated = llm_config.apply_capability_update(settings, "text", {"adapter": "openai_chat",
                                                                   "credential_action": "clear"})
    assert cap(updated, "text")["needs_confirmation"] is False
    assert cap(updated, "text")["api_key"] is None
    assert cap(updated, "text")["config_revision"] == 1
    assert updated["legacy_migration"]["needs_confirmation"] == []


# --- 4. credential_action ---

def test_credential_action_keep_replace_clear():
    settings = llm_config.migrate_legacy_settings(legacy())
    kept = llm_config.apply_capability_update(settings, "text", {"credential_action": "keep", "model": "other"})
    assert cap(kept, "text")["api_key"] == FAKE_KEY
    replaced = llm_config.apply_capability_update(settings, "text", {
        "credential_action": "replace", "api_key": " replaced-key-998877 "})
    assert cap(replaced, "text")["api_key"] == "replaced-key-998877"
    cleared = llm_config.apply_capability_update(settings, "text", {"credential_action": "clear"})
    assert cap(cleared, "text")["api_key"] is None
    assert llm_config.public_capability_settings(cleared)["capabilities"]["text"]["has_api_key"] is False
    assert cleared["schema_version"] == 2


@pytest.mark.parametrize("action", ["keep", "clear"])
def test_keep_and_clear_reject_a_smuggled_key(action):
    settings = llm_config.migrate_legacy_settings(legacy())
    with pytest.raises(ValueError) as error:
        llm_config.apply_capability_update(settings, "text", {
            "credential_action": action, "api_key": "smuggled-key-334455", "model": "other"})
    assert_no_secret(str(error.value))
    blank = llm_config.apply_capability_update(settings, "text", {
        "credential_action": action, "api_key": "   ", "model": "other"})
    expected = FAKE_KEY if action == "keep" else None
    assert cap(blank, "text")["api_key"] == expected


@pytest.mark.parametrize("key", ["", "   ", None, 12345, "bad\u0007key", FAKE_KEY * 200])
def test_replace_requires_a_non_empty_valid_key(key):
    settings = llm_config.migrate_legacy_settings(legacy())
    with pytest.raises(ValueError) as error:
        llm_config.apply_capability_update(settings, "text", {"credential_action": "replace", "api_key": key})
    assert_no_secret(str(error.value))


@pytest.mark.parametrize("action", ["rotate", "", None, "KEEP", 3])
def test_unknown_credential_action_is_rejected(action):
    settings = llm_config.migrate_legacy_settings(legacy())
    with pytest.raises(ValueError) as error:
        llm_config.apply_capability_update(settings, "text", {"credential_action": action})
    assert_no_secret(str(error.value))


@pytest.mark.parametrize("changes", [
    {"base_url": "https://other.invalid/v1"},
    {"adapter": "openai_chat"},
    {"execution_location": "local"}])
def test_changing_target_or_location_cannot_silently_keep_the_key(changes):
    settings = llm_config.migrate_legacy_settings(legacy())
    assert cap(settings, "text")["api_key"] == FAKE_KEY
    for payload in (dict(changes), dict(changes, credential_action="keep")):
        with pytest.raises(ValueError) as error:
            llm_config.apply_capability_update(settings, "text", payload)
        assert_no_secret(str(error.value))
    if changes == {"execution_location": "local"}:
        # 本机这一格结构上没有密钥：连 replace 也被拒，想切本机只能先 clear。
        with pytest.raises(ValueError) as error:
            llm_config.apply_capability_update(settings, "text", dict(
                changes, credential_action="replace", api_key="key-for-new-target-223344"))
        assert "不能保存密钥" in str(error.value)
        assert_no_secret(str(error.value))
    else:
        replaced = llm_config.apply_capability_update(settings, "text", dict(
            changes, credential_action="replace", api_key="key-for-new-target-223344"))
        assert cap(replaced, "text")["api_key"] == "key-for-new-target-223344"
    cleared = llm_config.apply_capability_update(settings, "text", dict(changes, credential_action="clear"))
    assert cap(cleared, "text")["api_key"] is None
    assert cap(cleared, "text")["config_revision"] == 1


def test_keyless_capability_may_change_target_with_keep():
    settings = llm_config.default_capability_settings()
    moved = llm_config.apply_capability_update(settings, "text", {"base_url": "https://other.invalid/v1"})
    assert cap(moved, "text")["base_url"] == "https://other.invalid/v1"
    assert cap(moved, "text")["config_revision"] == 1


def test_clear_does_not_fall_back_to_an_environment_key(monkeypatch):
    monkeypatch.setenv("AIPC_LLM_API_KEY", FAKE_ENV_KEY)
    monkeypatch.setenv("OPENAI_API_KEY", FAKE_ENV_KEY)
    monkeypatch.setenv("DASHSCOPE_API_KEY", FAKE_ENV_KEY)
    settings = llm_config.migrate_legacy_settings(legacy())
    cleared = llm_config.apply_capability_update(settings, "text", {"credential_action": "clear"})
    assert cap(cleared, "text")["api_key"] is None
    assert llm_config.public_capability_settings(cleared)["capabilities"]["text"]["has_api_key"] is False
    assert FAKE_ENV_KEY not in json.dumps(cleared, default=str)
    assert FAKE_ENV_KEY not in json.dumps(llm_config.public_capability_settings(cleared), default=str)
    without_key = llm_config.migrate_legacy_settings(legacy(api_key=None))
    assert cap(without_key, "text")["api_key"] is None
    assert llm_config.public_capability_settings(without_key)["capabilities"]["text"]["has_api_key"] is False


# --- 5. 公共脱敏输出 ---

def test_public_output_exposes_only_has_api_key():
    settings = enable("text")
    public = llm_config.public_capability_settings(settings)
    assert public["schema_version"] == 2
    assert list(public["capabilities"]) == list(llm_config.CAPABILITY_NAMES)
    assert public["capabilities"]["text"]["has_api_key"] is True
    dumped = json.dumps(public, ensure_ascii=False)
    assert_no_secret(dumped)
    for name, entry in public["capabilities"].items():
        assert "api_key" not in entry
        assert set(entry) >= {"enabled", "execution_location", "adapter", "base_url", "model",
                              "timeout_seconds", "has_api_key", "needs_confirmation",
                              "connection_status", "integration_status", "config_revision"}
        assert not (set(entry) & {"suffix", "fingerprint", "last4", "key", "secret"})
        if name == "vision":
            assert "max_images" in entry


def test_saved_config_is_never_reported_as_a_successful_connection():
    settings = enable("text")
    public = llm_config.public_capability_settings(settings)
    assert public["capabilities"]["text"]["enabled"] is True
    assert public["capabilities"]["text"]["connection_status"] == "not_tested"
    dumped = json.dumps(public, ensure_ascii=False).lower()
    for token in ("connected", "success", "verified", "reachable", "healthy"):
        assert token not in dumped


def test_unintegrated_capability_is_reported_separately_from_connection_state():
    public = llm_config.public_capability_settings(llm_config.default_capability_settings())
    assert llm_config.SUPPORTED_ADAPTERS["video"] == ("openai_video",)
    for name in llm_config.CAPABILITY_NAMES:
        assert public["capabilities"][name]["integration_status"] == "adapter_ready"
    for name in llm_config.CAPABILITY_NAMES:
        assert public["capabilities"][name]["connection_status"] == "not_tested"


# --- 6. 启用与协议支持 ---

@pytest.mark.parametrize("capability,adapter", [
    ("video", "dashscope_video"), ("video", ""),
    ("text", "ollama"), ("text", "openai_images"), ("text", "LOCAL"),
    ("vision", "dashscope_images"), ("vision", "openai_video"),
    ("image", "openai_chat"), ("image", "stable_diffusion"), ("image", "")])
def test_not_integrated_protocol_or_capability_cannot_be_enabled(capability, adapter):
    changes = {"enabled": True, "adapter": adapter, "base_url": "https://api.invalid/v1",
               "model": "chosen-model", "api_key": FAKE_KEY}
    if capability == "vision":
        changes["max_images"] = 1
    with pytest.raises(ValueError) as error:
        llm_config.apply_capability_update(llm_config.default_capability_settings(), capability, changes)
    assert_no_secret(str(error.value))
    settings = llm_config.default_capability_settings()
    broken = {**settings, "capabilities": {**settings["capabilities"],
                                           capability: {**cap(settings, capability), **changes}}}
    with pytest.raises(ValueError):
        llm_config.validate_capability_settings(broken)
    with pytest.raises(ValueError):
        llm_config.public_capability_settings(broken)


def test_enabled_capability_must_carry_the_fields_the_protocol_needs():
    settings = llm_config.default_capability_settings()
    for changes in ({"enabled": True, "adapter": "openai_chat", "model": "m", "api_key": FAKE_KEY},
                    {"enabled": True, "adapter": "openai_chat", "base_url": "https://api.invalid/v1"},
                    {"enabled": True, "adapter": "openai_chat", "base_url": "https://api.invalid/v1",
                     "model": "m", "api_key": "   "},
                    {"enabled": True, "adapter": "", "base_url": "https://api.invalid/v1",
                     "model": "m", "api_key": FAKE_KEY}):
        with pytest.raises(ValueError) as error:
            llm_config.apply_capability_update(settings, "text", dict(changes, credential_action="replace")
                                               if changes.get("api_key") else changes)
        assert_no_secret(str(error.value))
    ok = enable("text", key=None, execution_location="local",
                base_url="http://127.0.0.1:11434/v1", credential_action="keep")
    assert cap(ok, "text")["enabled"] is True and cap(ok, "text")["api_key"] is None


def test_local_text_rejects_a_non_loopback_url_and_a_stowed_key():
    # 两条新规则各钉一条：执行位置写本机、地址却不是回环，那就不是本机执行；本机这一格结构上没有密钥。
    with pytest.raises(ValueError) as url_error:
        enable("text", key=None, execution_location="local")
    assert "回环" in str(url_error.value)
    assert_no_secret(str(url_error.value))
    with pytest.raises(ValueError) as key_error:
        enable("text", execution_location="local", base_url="http://127.0.0.1:11434/v1")
    assert "不能保存密钥" in str(key_error.value)
    assert_no_secret(str(key_error.value))


def test_disabled_capability_may_have_empty_url_and_model():
    settings = llm_config.default_capability_settings()
    validated = llm_config.validate_capability_settings(settings)
    assert cap(validated, "video")["base_url"] == "" and cap(validated, "video")["model"] == ""
    partially = llm_config.apply_capability_update(settings, "image", {"base_url": "https://img.invalid/v1"})
    assert cap(partially, "image")["enabled"] is False
    assert cap(partially, "image")["base_url"] == "https://img.invalid/v1"


def test_vision_enabled_requires_at_least_one_image():
    settings = llm_config.default_capability_settings()
    with pytest.raises(ValueError) as error:
        enable("vision", settings, max_images=0)
    assert_no_secret(str(error.value))
    for value in (1, 20):
        enabled = enable("vision", max_images=value)
        assert cap(enabled, "vision")["max_images"] == value
        assert cap(enabled, "vision")["enabled"] is True


# --- 7. 校验：类型、范围、未知字段 ---

@pytest.mark.parametrize("changes", [
    {"enabled": "true"}, {"enabled": 1}, {"enabled": None},
    {"execution_location": "LOCAL"}, {"execution_location": "edge"}, {"execution_location": None},
    {"timeout_seconds": 0}, {"timeout_seconds": 601}, {"timeout_seconds": float("inf")},
    {"timeout_seconds": float("nan")}, {"timeout_seconds": "30"}, {"timeout_seconds": None},
    {"timeout_seconds": True},
    {"api_key": 12345}, {"api_key": FAKE_KEY * 200}, {"api_key": "ctl\u0000key"},
    {"model": "x" * 201}, {"model": 7},
    {"base_url": 1}, {"base_url": "http://api.invalid/v1"}, {"base_url": "https://u:p@api.invalid/v1"},
    {"adapter": None}, {"adapter": 5},
    {"config_revision": -1}, {"config_revision": 1.5}, {"config_revision": "0"}, {"config_revision": True},
    {"needs_confirmation": "yes"}, {"needs_confirmation": 0},
    {"max_images": 1}, {"unexpected": FAKE_KEY}])
def test_entry_field_type_and_range_rules(changes):
    with pytest.raises(ValueError) as error:
        llm_config.validate_capability_settings(text_variant(**changes))
    assert_no_secret(str(error.value))


@pytest.mark.parametrize("value", [-1, 21, 1.5, "3", None, True, False])
def test_vision_max_images_type_and_range(value):
    settings = llm_config.default_capability_settings()
    broken = {**settings, "capabilities": {**settings["capabilities"],
                                           "vision": {**cap(settings, "vision"), "max_images": value}}}
    with pytest.raises(ValueError) as error:
        llm_config.validate_capability_settings(broken)
    assert_no_secret(str(error.value))


def test_unknown_capability_and_unknown_root_fields_are_rejected():
    settings = llm_config.default_capability_settings()
    extra = {**settings["capabilities"], "audio": cap(settings, "text")}
    with pytest.raises(ValueError) as error:
        llm_config.validate_capability_settings({**settings, "capabilities": extra})
    assert_no_secret(str(error.value))
    missing = {name: entry for name, entry in settings["capabilities"].items() if name != "video"}
    with pytest.raises(ValueError):
        llm_config.validate_capability_settings({**settings, "capabilities": missing})
    for bad in ("text", None, [], {"capabilities": {}}):
        with pytest.raises(ValueError):
            llm_config.validate_capability_settings(bad)
    with pytest.raises(ValueError):
        llm_config.validate_capability_settings({**settings, "api_key": FAKE_KEY})
    for version in (1, 3, None, "2"):
        with pytest.raises(ValueError):
            llm_config.validate_capability_settings({**settings, "schema_version": version})
    for capability in ("text", "vision"):
        with pytest.raises(ValueError):
            llm_config.validate_capability_settings({**settings, "capabilities": {
                **settings["capabilities"], capability: "not-an-object"}})
    with pytest.raises(ValueError):
        llm_config.validate_capability_settings({**settings, "legacy_migration": {"source_schema_version": 2}})
    with pytest.raises(ValueError):
        llm_config.validate_capability_settings({**settings, "legacy_migration": {
            "source_schema_version": 1, "needs_confirmation": ["audio"]}})
    with pytest.raises(ValueError):
        llm_config.validate_capability_settings({**settings, "legacy_migration": {"source_schema_version": 1,
                                                                                  "unexpected": FAKE_KEY}})


@pytest.mark.parametrize("changes", [{"config_revision": 9}, {"needs_confirmation": False},
                                     {"needs_confirmation": True}, {"credential_action": "keep",
                                                                    "config_revision": 0}])
def test_server_managed_fields_cannot_be_supplied_by_a_client(changes):
    settings = llm_config.migrate_legacy_settings(legacy())
    with pytest.raises(ValueError) as error:
        llm_config.apply_capability_update(settings, "text", changes)
    assert_no_secret(str(error.value))
    for capability in ("text", "image"):
        with pytest.raises(ValueError):
            llm_config.apply_capability_update(settings, capability, {"unknown_field": FAKE_KEY})
    with pytest.raises(ValueError):
        llm_config.apply_capability_update(settings, "audio", {"model": "m"})
    with pytest.raises(ValueError):
        llm_config.apply_capability_update(settings, "text", "not-a-dict")


def test_update_rejections_never_echo_the_rejected_value():
    settings = llm_config.migrate_legacy_settings(legacy())
    for changes in ({"credential_action": "replace", "api_key": FAKE_KEY + "\u0000"},
                    {"base_url": f"https://api.invalid/v1?token={FAKE_KEY}"},
                    {"model": FAKE_KEY + "y" * 200},
                    {"api_key": FAKE_KEY, "credential_action": "keep"},
                    {"timeout_seconds": FAKE_KEY}):
        with pytest.raises(ValueError) as error:
            llm_config.apply_capability_update(settings, "text", changes)
        assert_no_secret(str(error.value))


# --- 8. config_revision 语义 ---

def test_config_revision_bumps_only_on_a_real_change():
    settings = enable("text")
    assert cap(settings, "text")["config_revision"] == 1
    noop = llm_config.apply_capability_update(settings, "text", {
        "credential_action": "replace", "api_key": FAKE_KEY, "timeout_seconds": 90, "model": "chosen-model"})
    assert noop == settings
    assert cap(noop, "text")["config_revision"] == 1
    first = llm_config.apply_capability_update(settings, "text", {"timeout_seconds": 60})
    assert cap(first, "text")["config_revision"] == 2
    assert cap(first, "text")["timeout_seconds"] == 60
    second = llm_config.apply_capability_update(first, "text", {"model": "next-model"})
    assert cap(second, "text")["config_revision"] == 3
    for name in ("vision", "image", "video"):
        assert cap(second, name) == cap(llm_config.default_capability_settings(), name)
    alpha = llm_config.apply_capability_update(settings, "text", {
        "credential_action": "replace", "api_key": "key-alpha-000111"})
    beta = llm_config.apply_capability_update(settings, "text", {
        "credential_action": "replace", "api_key": "key-bravo-222333"})
    assert cap(alpha, "text")["config_revision"] == cap(beta, "text")["config_revision"] == 2
    assert isinstance(cap(alpha, "text")["config_revision"], int)
    assert cap(alpha, "text")["config_revision"] < 1000


def test_confirming_a_migrated_capability_counts_as_a_real_change():
    migrated = llm_config.migrate_legacy_settings(legacy())
    unchanged_key = llm_config.apply_capability_update(migrated, "text", {
        "credential_action": "replace", "api_key": FAKE_KEY})
    assert cap(unchanged_key, "text")["api_key"] == FAKE_KEY
    assert cap(unchanged_key, "text")["needs_confirmation"] is False
    assert cap(unchanged_key, "text")["config_revision"] == 1


def test_revision_is_per_capability():
    settings = enable("text")
    assert cap(settings, "text")["config_revision"] == 1
    assert cap(settings, "text")["enabled"] is True
    vision = enable("vision", settings)
    assert cap(vision, "vision")["config_revision"] == 1
    assert cap(vision, "text") == cap(settings, "text")


# --- 9. 结构与既有行为 ---

def test_private_structure_survives_a_json_round_trip():
    settings = enable("vision")
    payload = json.loads(json.dumps(settings, ensure_ascii=False))
    assert payload["schema_version"] == 2
    assert set(payload["capabilities"]) == set(llm_config.CAPABILITY_NAMES)
    assert llm_config.validate_capability_settings(payload) == settings
    assert llm_config.public_capability_settings(payload) == llm_config.public_capability_settings(settings)


def test_legacy_single_config_api_is_unchanged():
    for name in ("LLMConfig", "load_llm_config", "save_llm_settings", "public_llm_settings",
                 "validate_base_url", "cloud_authorization", "CONFIG_PATH", "CLOUD_AUTHORIZED"):
        assert hasattr(llm_config, name)
    assert llm_config.public_llm_settings(llm_config.LLMConfig(
        FAKE_KEY, "https://api.invalid/v1", "legacy-model", 30, 4)) == {
        "base_url": "https://api.invalid/v1", "model": "legacy-model", "timeout_seconds": 30,
        "max_images": 4, "has_api_key": True, "connection_status": "not_tested"}
    assert_no_secret(json.dumps(llm_config.public_llm_settings(llm_config.LLMConfig(
        FAKE_KEY, "https://api.invalid/v1", "legacy-model", 30, 4))))
