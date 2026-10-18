from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from app.core import ai_media_generator, llm_config
from app.core.llm_client import LLMClientError, chat_completion_json
from app.core.llm_config import LLMConfig, cloud_authorization
from app.web import server
from tests.test_web_api import _build_pack

FAKE_KEY = "unit-test-secret-not-real-123456"


def settings(**changes):
    return dict(base_url="https://example.invalid/v1", model="test-model",
                timeout_seconds=30, max_images=3, api_key=FAKE_KEY, **changes)


def test_settings_never_return_key_and_blank_preserves_clear_removes(monkeypatch):
    client = TestClient(server.app)
    response = client.post("/api/ai-settings", json=settings())
    assert response.status_code == 200
    assert response.json()["has_api_key"] is True
    assert response.json()["connection_status"] == "not_tested"
    for response in (response, client.get("/api/ai-settings")):
        assert FAKE_KEY not in response.text
        assert "123456" not in response.text
        assert "api_key" not in response.json()
    body = settings()
    body["api_key"] = ""
    assert client.post("/api/ai-settings", json=body).json()["has_api_key"]
    assert llm_config.load_llm_config().api_key == FAKE_KEY
    monkeypatch.setenv("AIPC_LLM_API_KEY", "fake-environment-key")
    body["clear_api_key"] = True
    assert not client.post("/api/ai-settings", json=body).json()["has_api_key"]
    assert not llm_config.load_llm_config().enabled
    assert not list(llm_config.CONFIG_PATH.parent.glob(".ai-settings-*.tmp"))


@pytest.mark.parametrize("url", [
    "http://example.com/v1", "ftp://example.com", "https://user:secret@example.com/v1",
    "https://example.com/v1?key=secret", "https://example.com/#fragment", "https://example.com/?",
    "https://example.com/#", "https://", "http://127.0.0.1.evil.com", "https://example.com:bad",
    "https://example.com\\@evil.com", "http://0.0.0.0:8000",
])
def test_invalid_urls_are_rejected_without_echo(url):
    with pytest.raises(ValueError):
        llm_config.validate_base_url(url)
    body = settings()
    body["base_url"] = url
    response = TestClient(server.app).post("/api/ai-settings", json=body)
    assert response.status_code == 400
    assert FAKE_KEY not in response.text and url not in response.text


@pytest.mark.parametrize("url", ["https://example.com/v1", "http://localhost:8080/v1",
                                "http://127.0.0.1:9000", "http://[::1]:9000/v1"])
def test_valid_urls(url):
    assert llm_config.validate_base_url(url) == url


def test_cross_origin_rejected_before_settings_access(monkeypatch):
    loader = Mock(side_effect=AssertionError("Must not read settings"))
    monkeypatch.setattr(server, "public_llm_settings", loader)
    client = TestClient(server.app)
    for path in ("/api/ai-settings", "/api/health"):
        assert client.get(path, headers={"Origin": "https://evil.invalid"}).status_code == 403
    assert client.post("/api/ai-settings", json=settings(), headers={"Origin": "null"}).status_code == 403
    loader.assert_not_called()
    assert client.get("/api/health", headers={"Origin": "http://testserver"}).status_code == 200


def test_validation_error_and_storage_error_do_not_echo_key(monkeypatch):
    client = TestClient(server.app)
    body = settings()
    body["max_images"] = FAKE_KEY
    response = client.post("/api/ai-settings", json=body)
    assert response.status_code == 422 and FAKE_KEY not in response.text
    monkeypatch.setattr(server, "save_llm_settings", Mock(side_effect=RuntimeError(FAKE_KEY)))
    response = client.post("/api/ai-settings", json=settings())
    assert response.status_code == 400 and FAKE_KEY not in response.text


def test_private_config_is_not_static_or_material_download():
    client = TestClient(server.app)
    assert client.post("/api/ai-settings", json=settings()).status_code == 200
    server.SESSION.register_root(llm_config.CONFIG_PATH.parent)
    for endpoint in ("material-file", "media-file"):
        response = client.get(f"/api/{endpoint}", params={"path": str(llm_config.CONFIG_PATH)})
        assert response.status_code == 403
        assert FAKE_KEY not in response.text
    assert client.get("/.ai-settings.private.json").status_code == 404


def test_no_authorization_all_model_routes_make_zero_http(tmp_path, monkeypatch):
    client = TestClient(server.app)
    client.post("/api/ai-settings", json=settings())
    # The already-created TestClient keeps its in-process transport. Any new
    # HTTP client constructed by the real model pipeline is an outbound attempt.
    outgoing = Mock(side_effect=AssertionError("No external HTTP allowed"))
    monkeypatch.setattr("httpx.Client", outgoing)
    pack = _build_pack(tmp_path)
    body = {"material_path": str(pack), "workspace_path": str(tmp_path / "workspace"),
            "product_form": {"product_name": "Test"}}
    data = client.post("/api/analyze", json=body).json()
    assert data["ok"] and data["ai_analysis"]["source"] == "offline"
    assert "未授权" in data["ai_analysis"]["fallback_reason"]
    assert data["direction_source"] == "local_blueprint"
    direction = data["directions"]["directions"][0]["direction_id"]
    assert client.post("/api/regenerate-directions", json={}).json()["ok"]
    for path in ("publish", "regenerate-content"):
        response = client.post(f"/api/{path}", json={"direction_id": direction})
        assert response.status_code == 200
        assert response.json()["content"]["source"] == "template"
    client.post("/api/select-reference-images", json={"relative_paths": ["商品图片/01_主图.png"]})
    media = client.post("/api/generate-media", json={"generate_image": True}).json()
    assert media["image_source"] == "fallback_copy"
    assert media["generated_media"]["image_source"] == "fallback_copy"
    assert "参考图副本" in media["message"]
    product_id = data["understanding"]["product_id"]
    history = client.post("/api/load-project", json={"product_id": product_id, "rerun_analysis": False}).json()
    assert history["analysis"]["ai_analysis"]["source"] == "history"
    rerun = client.post("/api/load-project", json={"product_id": product_id, "rerun_analysis": True}).json()
    assert rerun["analysis"]["ai_analysis"]["source"] == "offline"
    outgoing.assert_not_called()


def test_authorized_missing_key_also_makes_zero_http(monkeypatch):
    cfg = LLMConfig(None, "https://example.invalid/v1", "test", 5, 1)
    outgoing = Mock()
    monkeypatch.setattr("httpx.Client.post", outgoing)
    with cloud_authorization(True), pytest.raises(LLMClientError):
        chat_completion_json([], config=cfg)
    outgoing.assert_not_called()


def test_authorization_restores_and_isolates_concurrent_contexts():
    cfg = LLMConfig(FAKE_KEY, "https://example.invalid/v1", "test", 5, 1)
    assert llm_config.CLOUD_AUTHORIZED.get() is None
    with pytest.raises(RuntimeError):
        with cloud_authorization(False):
            assert not cfg.enabled
            with cloud_authorization(True):
                assert cfg.enabled
            assert not cfg.enabled
            raise RuntimeError("test")
    assert cfg.enabled and llm_config.CLOUD_AUTHORIZED.get() is None
    async def check(authorized):
        with cloud_authorization(authorized):
            await asyncio.sleep(0)
            return cfg.enabled
    async def run():
        return await asyncio.gather(check(True), check(False), check(True), check(False))
    assert asyncio.run(run()) == [True, False, True, False]


def test_web_request_authorization_never_inherits(tmp_path, monkeypatch):
    client = TestClient(server.app)
    client.post("/api/ai-settings", json=settings())
    pack = _build_pack(tmp_path)
    observed = []
    original = server.run_analysis
    def run(*args, **kwargs):
        observed.append(llm_config.load_llm_config().enabled)
        # Real pipeline, with only the AI response mocked.
        return original(*args, **kwargs)
    monkeypatch.setattr(server, "run_analysis", run)
    monkeypatch.setattr("app.core.ai_product_analyzer.chat_completion_json", Mock(return_value={
        "product_name": "Test", "product_facts": {"颜色": "green"}}))
    body = {"material_path": str(pack), "workspace_path": str(tmp_path / "workspace"),
            "product_form": {"product_name": "Test"}, "cloud_authorized": True}
    result = client.post("/api/analyze", json=body).json()
    assert result["ai_analysis"]["source"] == "llm"
    body.pop("cloud_authorized")
    assert client.post("/api/analyze", json=body).json()["ai_analysis"]["source"] == "offline"
    assert observed == [True, False]
    assert llm_config.CLOUD_AUTHORIZED.get() is None


@pytest.mark.parametrize("base_url,provider", [("https://example.invalid/v1", "openai"),
    ("https://dashscope.aliyuncs.com/compatible-mode/v1", "dashscope"),
    ("https://dashscope.aliyuncs.com.evil.invalid/v1", "openai")])
def test_image_provider_never_falls_back_to_other_provider(tmp_path, monkeypatch, base_url, provider):
    dashscope = Mock(side_effect=ai_media_generator.AIMediaError(FAKE_KEY))
    openai = Mock(side_effect=ai_media_generator.AIMediaError(FAKE_KEY))
    openai_edit = Mock(side_effect=ai_media_generator.AIMediaError(FAKE_KEY))
    monkeypatch.setattr(ai_media_generator, "_try_dashscope_image", dashscope)
    monkeypatch.setattr(ai_media_generator, "_try_openai_image", openai)
    monkeypatch.setattr(ai_media_generator, "_try_openai_image_edit", openai_edit)
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"png")
    cfg = LLMConfig(FAKE_KEY, base_url, "test", 5, 1)
    with cloud_authorization(True):
        paths, source = ai_media_generator.generate_images_from_prompt(
            prompt="test", output_dir=tmp_path / "out", reference_images=[ref], config=cfg)
    assert source == "fallback_copy" and paths
    assert dashscope.call_count == (provider == "dashscope")
    assert (openai.call_count + openai_edit.call_count) == (provider == "openai")
    assert FAKE_KEY not in (tmp_path / "out" / "generation_note.txt").read_text(encoding="utf-8")


def test_content_sources_record_actual_partial_cloud_result(tmp_path, monkeypatch):
    from app.core.content_generator import build_content_package
    from tests.test_content_generator import _build_pack as build_pack, _reports
    directions, understanding, extraction = _reports(build_pack(tmp_path))
    monkeypatch.setattr("app.core.llm_content_generator.load_llm_config", lambda: LLMConfig(
        FAKE_KEY, "https://example.invalid/v1", "test", 5, 1))
    monkeypatch.setattr("app.core.llm_content_generator.chat_completion_json", Mock(return_value={
        "xiaohongshu": {"title": "Cloud", "body": "Generated copy"},
        "douyin": {"title": "Empty", "body": ""}}))
    report = build_content_package(directions, understanding, extraction).to_dict()
    assert report["source"] == "mixed"
    assert report["generation_sources"]["xiaohongshu"] == "cloud"
    assert report["generation_sources"]["douyin"] == "template"
    monkeypatch.setattr("app.core.llm_content_generator.chat_completion_json", Mock(side_effect=LLMClientError(FAKE_KEY)))
    assert build_content_package(directions, understanding, extraction).to_dict()["source"] == "template"


def test_non_dashscope_image_http_stays_on_configured_provider(tmp_path, monkeypatch):
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    transport.post.return_value = Mock(status_code=200)
    transport.post.return_value.json.return_value = {"data": [{"url": "https://images.invalid/test.png"}]}
    transport.get.return_value = Mock(status_code=200, headers={"content-type": "image/png"}, content=b"png")
    monkeypatch.setattr("app.core.ai_media_generator.httpx.Client", Mock(return_value=transport))
    cfg = LLMConfig(FAKE_KEY, "https://provider.invalid/v1", "test", 5, 1)
    with cloud_authorization(True):
        paths, source = ai_media_generator.generate_images_from_prompt(
            prompt="test", output_dir=tmp_path / "out", reference_images=[], config=cfg)
    assert source == "ai_image" and paths
    assert transport.post.call_args.args[0] == "https://provider.invalid/v1/images/generations"
    assert transport.post.call_count == 1
    assert transport.get.call_args.kwargs.get("headers") is None


def test_openai_image_edit_uses_reference_images(tmp_path, monkeypatch):
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    transport.post.return_value = Mock(status_code=200)
    transport.post.return_value.json.return_value = {"data": [{"b64_json": "aGVsbG8="}]}
    monkeypatch.setattr("app.core.ai_media_generator.httpx.Client", Mock(return_value=transport))
    ref = tmp_path / "ref.png"
    ref.write_bytes(b"png")
    cfg = LLMConfig(FAKE_KEY, "https://api.openai.com/v1", "test", 5, 1)
    with cloud_authorization(True):
        paths, source = ai_media_generator.generate_images_from_prompt(
            prompt="styled product photo", output_dir=tmp_path / "out", reference_images=[ref], config=cfg)
    assert source == "ai_image" and paths
    assert transport.post.call_args.args[0] == "https://api.openai.com/v1/images/edits"


def test_web_exception_redacted_and_authorization_reset(tmp_path, monkeypatch):
    client = TestClient(server.app)
    client.post("/api/ai-settings", json=settings())
    pack = _build_pack(tmp_path)
    observed = []
    original = server.run_analysis
    def fail(*args, **kwargs):
        observed.append(llm_config.load_llm_config().enabled)
        raise RuntimeError(FAKE_KEY)
    monkeypatch.setattr(server, "run_analysis", fail)
    body = {"material_path": str(pack), "cloud_authorized": True}
    response = client.post("/api/analyze", json=body)
    assert response.status_code == 500 and FAKE_KEY not in response.text
    assert llm_config.CLOUD_AUTHORIZED.get() is None
    def run(*args, **kwargs):
        observed.append(llm_config.load_llm_config().enabled)
        return original(*args, **kwargs)
    monkeypatch.setattr(server, "run_analysis", run)
    body.pop("cloud_authorized")
    assert client.post("/api/analyze", json=body).status_code == 200
    assert observed == [True, False]


def test_frontend_authorization_fresh_each_request_and_media_cancel():
    import shutil
    import subprocess
    node = shutil.which("node")
    assert node, "Node is required for frontend validation"
    script = r'''
const fs = require('fs');
const vm = require('vm');
const assert = require('assert');
const source = fs.readFileSync(process.argv[1], 'utf8');
const requests = [];
const decisions = [true, false, false, false, true, false];
let prompts = 0;
const context = {
  document: { addEventListener() {} },
  window: { confirm(text) { prompts++; assert(text.includes('费用')); return decisions.shift(); } },
  setTimeout, clearTimeout, AbortController,
  fetch: async (path, options) => { requests.push({path, body: JSON.parse(options.body)}); return {ok:true, json:async()=>({})}; },
};
vm.createContext(context);
vm.runInContext(source, context);
(async () => {
  await context.api('/api/analyze', {method:'POST', body:'{}'});
  await context.api('/api/analyze', {method:'POST', body:'{"cloud_authorized":true}'});
  assert.strictEqual(requests[0].body.cloud_authorized, true);
  assert.strictEqual(requests[1].body.cloud_authorized, false);
  await context.api('/api/publish', {method:'POST', body:'{}'});
  await assert.rejects(context.api('/api/generate-media', {method:'POST', body:'{"generate_image":true}'}));
  assert.strictEqual(requests.length, 3);
  await context.api('/api/load-project', {method:'POST', body:'{"rerun_analysis":true}'});
  await context.api('/api/regenerate-content', {method:'POST', body:'{}'});
  const before = prompts;
  await context.api('/api/load-project', {method:'POST', body:'{"rerun_analysis":false}'});
  assert.strictEqual(prompts, before);
  assert.strictEqual(requests.at(-1).body.cloud_authorized, false);
})().catch(error => { console.error(error); process.exitCode = 1; });
'''
    path = Path(server.STATIC_DIR) / "app.js"
    result = subprocess.run([node, "-e", script, str(path)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_client_provider_errors_are_redacted(monkeypatch):
    response = Mock(status_code=401, text=FAKE_KEY)
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    transport.post.return_value = response
    monkeypatch.setattr("app.core.llm_client.httpx.Client", Mock(return_value=transport))
    with pytest.raises(LLMClientError) as error:
        chat_completion_json([], config=LLMConfig(FAKE_KEY, "https://example.invalid/v1", "test", 5, 1))
    assert FAKE_KEY not in str(error.value)
