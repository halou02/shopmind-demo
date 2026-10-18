"""v2 text 设置 API、脱敏输出与私有文件保护的开发者测试。

只用假凭据 + pytest 临时目录：不读真实私有配置、不调用真实模型、不发任何网络请求。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.core import capability_config, llm_config
from app.web import server

TEXT_KEY = "text-capability-key-112233"
LEGACY_KEY = "legacy-v1-key-445566"
TEXT_BASE = "https://text.invalid/v1"
V1_BASE = "https://legacy.invalid/v1"
SECRET_TAILS = ("112233", "445566")

PUBLIC_FIELDS = {"enabled", "execution_location", "adapter", "base_url", "model", "timeout_seconds",
                 "has_api_key", "needs_confirmation", "connection_status", "integration_status",
                 "config_revision"}


def v2_path() -> Path:
    return capability_config.capability_config_path()


def text_update(**changes) -> dict:
    payload = {"enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
               "base_url": TEXT_BASE, "model": "text-model", "timeout_seconds": 45,
               "credential_action": "replace", "api_key": TEXT_KEY}
    payload.update(changes)
    return payload


def assert_no_secret(response) -> None:
    for secret in (TEXT_KEY, LEGACY_KEY):
        assert secret not in response.text
    for tail in SECRET_TAILS:
        assert tail not in response.text


# --- 1. GET /api/ai-capabilities ---

def test_get_lists_four_capabilities_with_no_credential_fields(tmp_path):
    response = TestClient(server.app).get("/api/ai-capabilities")
    assert response.status_code == 200
    body = response.json()
    assert set(body) == {"schema_version", "capabilities"}
    assert body["schema_version"] == 2
    assert set(body["capabilities"]) == set(llm_config.CAPABILITY_NAMES)
    for name, entry in body["capabilities"].items():
        assert set(entry) == (PUBLIC_FIELDS | ({"max_images"} if name == "vision" else set()))
        assert "api_key" not in entry
        assert entry["enabled"] is False and entry["has_api_key"] is False
        assert entry["needs_confirmation"] is False
        assert entry["connection_status"] == "not_tested"
        assert entry["integration_status"] == "adapter_ready"
    assert_no_secret(response)
    assert not v2_path().exists()


def test_get_exposes_the_migration_candidate_without_writing_v2(tmp_path):
    client = TestClient(server.app)
    body = {"base_url": V1_BASE, "model": "legacy-model", "timeout_seconds": 30,
            "max_images": 4, "api_key": LEGACY_KEY}
    assert client.post("/api/ai-settings", json=body).status_code == 200
    response = client.get("/api/ai-capabilities")
    assert response.status_code == 200
    payload = response.json()
    assert payload["legacy_migration"] == {"source_schema_version": 1,
                                           "needs_confirmation": ["text"],
                                           "unmapped": {"max_images": 4}}
    text = payload["capabilities"]["text"]
    assert text["base_url"] == V1_BASE and text["model"] == "legacy-model"
    assert text["enabled"] is False and text["needs_confirmation"] is True
    assert text["has_api_key"] is True and text["config_revision"] == 0
    for name in ("vision", "image", "video"):
        assert payload["capabilities"][name]["has_api_key"] is False
        assert payload["capabilities"][name]["needs_confirmation"] is False
    assert_no_secret(response)
    assert not v2_path().exists()


# --- 2. POST /api/ai-capabilities/text ---

def test_post_text_writes_the_private_v2_file_and_answers_masked(tmp_path):
    client = TestClient(server.app)
    assert client.post("/api/ai-settings", json={"base_url": V1_BASE, "model": "legacy-model",
                                                 "timeout_seconds": 30, "max_images": 4,
                                                 "api_key": LEGACY_KEY}).status_code == 200
    assert client.get("/api/ai-capabilities").json()["capabilities"]["text"]["needs_confirmation"] is True
    assert not v2_path().exists()
    response = client.post("/api/ai-capabilities/text", json=text_update())
    assert response.status_code == 200
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 2
    assert set(on_disk["capabilities"]) == set(llm_config.CAPABILITY_NAMES)
    assert on_disk["capabilities"]["text"]["api_key"] == TEXT_KEY
    assert on_disk["capabilities"]["text"]["enabled"] is True
    for name in ("vision", "image", "video"):
        assert on_disk["capabilities"][name]["api_key"] is None
    assert on_disk["legacy_migration"] == {"source_schema_version": 1,
                                          "needs_confirmation": [],
                                          "unmapped": {"max_images": 4}}
    payload = response.json()
    text = payload["capabilities"]["text"]
    assert "api_key" not in text
    assert text["has_api_key"] is True
    assert text["needs_confirmation"] is False and text["config_revision"] == 1
    assert text["connection_status"] == "not_tested"
    assert payload["legacy_migration"]["needs_confirmation"] == []
    assert_no_secret(response)


def test_post_text_ignores_the_transport_authorization_envelope(tmp_path):
    response = TestClient(server.app).post("/api/ai-capabilities/text",
                                           json=text_update(cloud_authorized=True))
    assert response.status_code == 200
    assert response.json()["capabilities"]["text"]["config_revision"] == 1


def test_saving_the_text_capability_makes_zero_http_calls(tmp_path, monkeypatch):
    client = TestClient(server.app)
    outgoing = Mock(side_effect=AssertionError("No external HTTP allowed"))
    monkeypatch.setattr("httpx.Client", outgoing)
    assert client.post("/api/ai-capabilities/text", json=text_update()).status_code == 200
    assert client.get("/api/ai-capabilities").status_code == 200
    assert client.post("/api/ai-settings", json={"base_url": V1_BASE, "model": "legacy-model",
                                                 "timeout_seconds": 30, "max_images": 4,
                                                 "api_key": LEGACY_KEY}).status_code == 200
    outgoing.assert_not_called()


@pytest.mark.parametrize("changes", [
    {"credential_action": "keep", "api_key": TEXT_KEY},
    {"credential_action": "clear", "api_key": TEXT_KEY},
    {"credential_action": "rotate"},
    {"replace": TEXT_KEY},
    {"unknown_field": "x"},
    {"config_revision": 99},
    {"needs_confirmation": False},
    {"timeout_seconds": 0},
    {"timeout_seconds": "45"},
    {"model": "x" * 201},
    {"api_key": "bad\r\nkey"},
    {"adapter": "ollama_chat"},
    {"enabled": "yes"},
    {"enabled": "true"},
])
def test_post_text_rejects_invalid_updates_without_echo_or_write(tmp_path, changes):
    payload = {"enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
               "base_url": TEXT_BASE, "model": "text-model", "timeout_seconds": 45}
    payload.update(changes)
    response = TestClient(server.app).post("/api/ai-capabilities/text", json=payload)
    assert response.status_code == 400
    detail = response.json()["detail"]
    assert TEXT_KEY not in detail and TEXT_BASE not in detail
    assert_no_secret(response)
    assert not v2_path().exists()
    assert not list(tmp_path.glob(".ai-capabilities-*.tmp"))


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://user:secret@example.com/v1",
                                 "https://example.com/v1?key=secret", "https://example.com/#frag"])
def test_post_text_rejects_unsafe_urls_without_echo(tmp_path, url):
    response = TestClient(server.app).post("/api/ai-capabilities/text",
                                           json=text_update(base_url=url, api_key=TEXT_KEY))
    assert response.status_code == 400
    assert url not in response.text
    assert_no_secret(response)
    assert not v2_path().exists()


@pytest.mark.parametrize("payload", [
    json.dumps([{"base_url": TEXT_BASE}]),
    json.dumps("just a string"),
    json.dumps(123),
    json.dumps(None),
    json.dumps(True),
])
def test_non_object_bodies_are_rejected_by_the_generic_handler(tmp_path, monkeypatch, payload):
    client = TestClient(server.app)
    outgoing = Mock(side_effect=AssertionError("No external HTTP allowed"))
    monkeypatch.setattr("httpx.Client", outgoing)
    response = client.post("/api/ai-capabilities/text", content=payload,
                           headers={"content-type": "application/json"})
    assert response.status_code == 422
    assert response.json() == {"detail": "请求字段无效，请检查输入"}
    assert TEXT_BASE not in response.text
    assert not v2_path().exists()
    outgoing.assert_not_called()


def test_post_text_without_a_model_cannot_be_enabled(tmp_path):
    response = TestClient(server.app).post("/api/ai-capabilities/text", json=text_update(model=""))
    assert response.status_code == 400
    assert "模型" in response.json()["detail"]
    assert_no_secret(response)


def test_post_text_changing_target_while_a_key_exists_requires_an_explicit_action(tmp_path):
    client = TestClient(server.app)
    assert client.post("/api/ai-capabilities/text", json=text_update()).status_code == 200
    before = v2_path().read_text(encoding="utf-8")
    response = client.post("/api/ai-capabilities/text",
                           json={"base_url": "https://other.invalid/v1", "cloud_authorized": False})
    assert response.status_code == 400
    assert "密钥" in response.json()["detail"]
    assert_no_secret(response)
    assert v2_path().read_text(encoding="utf-8") == before


# --- 3. 坏文件与写失败 ---

def test_broken_v2_answers_masked_and_is_never_repaired(tmp_path):
    v2_path().write_text(json.dumps({"schema_version": 2, "capabilities": {"text": {"api_key": TEXT_KEY}}}),
                         encoding="utf-8")
    before = v2_path().read_bytes()
    client = TestClient(server.app)
    for response in (client.get("/api/ai-capabilities"),
                     client.post("/api/ai-capabilities/text", json=text_update())):
        assert response.status_code == 400
        assert response.json()["detail"] == capability_config._UNREADABLE
        assert "api_key" not in response.text
        assert_no_secret(response)
    assert v2_path().read_bytes() == before
    assert not list(tmp_path.glob(".ai-capabilities-*.tmp"))


@pytest.mark.parametrize("content", ["{ not json", "", json.dumps({"schema_version": 3}),
                                     json.dumps({"api_key": LEGACY_KEY, "base_url": V1_BASE,
                                                 "model": "legacy-model", "timeout_seconds": 30})])
def test_unreadable_or_unsupported_v2_is_reported_the_same_way(tmp_path, content):
    v2_path().write_text(content, encoding="utf-8")
    response = TestClient(server.app).get("/api/ai-capabilities")
    assert response.status_code == 400
    assert response.json()["detail"] == capability_config._UNREADABLE
    assert_no_secret(response)


def test_write_failure_leaves_the_previous_file_and_is_masked(tmp_path, monkeypatch):
    client = TestClient(server.app)
    assert client.post("/api/ai-capabilities/text", json=text_update()).status_code == 200
    before = v2_path().read_text(encoding="utf-8")

    def refuse(*_args):
        raise OSError("disk full")
    monkeypatch.setattr(os, "replace", refuse)
    response = client.post("/api/ai-capabilities/text", json=text_update(model="replacement-model"))
    assert response.status_code == 400
    assert response.json()["detail"] == capability_config._WRITE_FAILED
    assert_no_secret(response)
    assert v2_path().read_text(encoding="utf-8") == before
    assert "replacement-model" not in before
    assert not list(tmp_path.glob(".ai-capabilities-*.tmp"))


# --- 4. 同源边界与旧接口 ---

def test_cross_origin_requests_never_reach_the_capability_store(monkeypatch):
    client = TestClient(server.app)
    assert client.get("/api/ai-capabilities", headers={"Origin": "http://testserver"}).status_code == 200
    reader = Mock(side_effect=AssertionError("Must not read capability settings"))
    writer = Mock(side_effect=AssertionError("Must not write capability settings"))
    monkeypatch.setattr(server, "read_capability_settings", reader)
    monkeypatch.setattr(server, "update_capability_settings", writer)
    assert client.get("/api/ai-capabilities", headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.post("/api/ai-capabilities/text", json=text_update(),
                       headers={"Origin": "null"}).status_code == 403
    reader.assert_not_called()
    writer.assert_not_called()
    assert not v2_path().exists()


def test_v1_endpoints_keep_working_and_stay_separate_from_v2(tmp_path):
    client = TestClient(server.app)
    body = {"base_url": V1_BASE, "model": "legacy-model", "timeout_seconds": 30,
            "max_images": 4, "api_key": LEGACY_KEY}
    assert client.post("/api/ai-settings", json=body).status_code == 200
    v1_before = llm_config.CONFIG_PATH.read_text(encoding="utf-8")
    response = client.get("/api/ai-settings")
    assert set(response.json()) == {"base_url", "model", "timeout_seconds", "max_images",
                                    "has_api_key", "connection_status"}
    assert response.json()["model"] == "legacy-model"
    assert client.post("/api/ai-capabilities/text", json=text_update()).status_code == 200
    assert llm_config.CONFIG_PATH.read_text(encoding="utf-8") == v1_before
    assert v1_before.count(LEGACY_KEY) == 1
    again = client.get("/api/ai-settings").json()
    assert again["model"] == "legacy-model" and again["base_url"] == V1_BASE
    capabilities = client.get("/api/ai-capabilities").json()["capabilities"]["text"]
    assert capabilities["model"] == "text-model" and capabilities["base_url"] == TEXT_BASE


def test_text_capability_config_reaches_the_model_request(tmp_path, monkeypatch):
    client = TestClient(server.app)
    assert client.post("/api/ai-settings", json={"base_url": V1_BASE, "model": "legacy-model",
                                                 "timeout_seconds": 30, "max_images": 4,
                                                 "api_key": LEGACY_KEY}).status_code == 200
    assert client.post("/api/ai-capabilities/text", json=text_update()).status_code == 200
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    reply = Mock(status_code=200)
    reply.json.return_value = {"choices": [{"message": {"content": '{"product_name": "包"}'}}]}
    transport.post.return_value = reply
    factory = Mock(return_value=transport)
    monkeypatch.setattr("app.core.llm_client.httpx.Client", factory)
    from app.core.llm_client import chat_completion_json
    with llm_config.cloud_authorization(True):
        assert chat_completion_json([{"role": "user", "content": "hi"}]) == {"product_name": "包"}
    assert transport.post.call_args.args[0] == f"{TEXT_BASE}/chat/completions"
    assert transport.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {TEXT_KEY}"
    assert transport.post.call_args.kwargs["json"]["model"] == "text-model"
    assert factory.call_args.kwargs["timeout"] == 45
    assert LEGACY_KEY not in str(transport.post.call_args_list)


# --- 5. 私有 v2 文件不能被下载或静态服务 ---

def test_private_v2_file_is_not_downloadable_or_served(tmp_path):
    client = TestClient(server.app)
    assert client.post("/api/ai-capabilities/text", json=text_update()).status_code == 200
    server.SESSION.register_root(v2_path().parent)
    assert TEXT_KEY in v2_path().read_text(encoding="utf-8")
    for endpoint in ("material-file", "media-file"):
        for candidate in (v2_path(), tmp_path / ".ai-capabilities-leftover.tmp",
                          llm_config.CONFIG_PATH):
            response = client.get(f"/api/{endpoint}", params={"path": str(candidate)})
            assert response.status_code == 403
            assert TEXT_KEY not in response.text and LEGACY_KEY not in response.text
    assert client.get("/.ai-capabilities.private.json").status_code == 404
