from __future__ import annotations

import asyncio
import json
import shutil
import subprocess
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import capability_config, llm_config
from app.web import server


LEGACY_BASE = "https://legacy-entry.invalid/v1"
LEGACY_MODEL = "legacy-entry-model"
LEGACY_KEY = "fake-legacy-entry-key-778899"


def seed_legacy_v1() -> Path:
    llm_config.save_llm_settings(base_url=LEGACY_BASE, model=LEGACY_MODEL, timeout_seconds=30,
                                 max_images=4, api_key=LEGACY_KEY)
    return llm_config.CONFIG_PATH


@pytest.fixture
def outbound_guard(monkeypatch):
    """记录并阻断用例期间的真实外发；TestClient 的进程内请求不计入外发。"""
    attempts = []
    original_send = httpx.Client.send

    def is_in_process(client, request) -> bool:
        return (client.__class__.__name__ == "TestClient"
                and request.url.host in {"testserver", "localhost"})

    def blocked(kind, client, request):
        attempts.append((kind, f"{request.method} {request.url}"))
        raise AssertionError("用例禁止外发 HTTP")

    def sync_guard(client, request, *args, **kwargs):
        if is_in_process(client, request):
            return original_send(client, request, *args, **kwargs)
        return blocked("sync", client, request)

    async def async_guard(client, request, *args, **kwargs):
        return blocked("async", client, request)

    monkeypatch.setattr(httpx.Client, "send", sync_guard)
    monkeypatch.setattr(httpx.AsyncClient, "send", async_guard)
    return attempts


def test_legacy_first_enable_of_text_keeps_the_migrated_key(outbound_guard):
    v1 = seed_legacy_v1()
    v1_before = v1.read_bytes()
    v2 = capability_config.capability_config_path()
    assert not v2.exists()
    client = TestClient(server.app)

    initial = client.get("/api/ai-capabilities").json()
    candidate = initial["capabilities"]["text"]
    assert candidate["needs_confirmation"] is True
    assert candidate["adapter"] == ""
    assert candidate["has_api_key"] is True
    assert not v2.exists()
    others_before = {name: initial["capabilities"][name] for name in ("vision", "image", "video")}

    response = client.post("/api/ai-capabilities/text", json={
        "enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
        "base_url": LEGACY_BASE, "model": LEGACY_MODEL, "timeout_seconds": 30,
        "credential_action": "keep"})
    assert response.status_code == 200, response.text
    entry = response.json()["capabilities"]["text"]
    assert entry["enabled"] is True
    assert entry["has_api_key"] is True
    assert entry["needs_confirmation"] is False
    assert entry["adapter"] == "openai_chat"
    assert entry["base_url"] == LEGACY_BASE
    assert entry["execution_location"] == "cloud"
    assert entry["connection_status"] == "not_tested"
    assert LEGACY_KEY not in response.text
    for name, summary in others_before.items():
        assert response.json()["capabilities"][name] == summary

    on_disk = json.loads(v2.read_text(encoding="utf-8"))
    assert on_disk["capabilities"]["text"]["api_key"] == LEGACY_KEY
    assert on_disk["legacy_migration"]["needs_confirmation"] == []
    for name in ("vision", "image", "video"):
        assert on_disk["capabilities"][name] == llm_config.default_capability_settings()["capabilities"][name]
    assert v1.read_bytes() == v1_before
    assert outbound_guard == []


def update(model="vision-test"):
    return {"enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
            "base_url": "https://vision.invalid/v1", "model": model,
            "timeout_seconds": 20, "max_images": 2,
            "credential_action": "replace", "api_key": "fake-vision-entry-key"}


def test_vision_save_preserves_other_capabilities_and_makes_no_model_request(monkeypatch):
    client = TestClient(server.app)
    initial = client.get("/api/ai-capabilities").json()
    outgoing = Mock()
    monkeypatch.setattr("httpx.Client", outgoing)
    response = client.post("/api/ai-capabilities/vision", json=update())
    assert response.status_code == 200
    result = response.json()
    assert "fake-vision-entry-key" not in response.text
    assert result["capabilities"]["vision"]["has_api_key"]
    assert result["capabilities"]["vision"]["connection_status"] == "not_tested"
    for name in ("text", "image", "video"):
        assert result["capabilities"][name] == initial["capabilities"][name]
    outgoing.assert_not_called()


def test_vision_keep_clear_and_target_change():
    client = TestClient(server.app)
    assert client.post("/api/ai-capabilities/vision", json=update()).status_code == 200
    assert client.post("/api/ai-capabilities/vision", json={"model": "new", "credential_action": "keep"}).status_code == 200
    path = capability_config.capability_config_path()
    before = path.read_bytes()
    rejected = client.post("/api/ai-capabilities/vision", json={"base_url": "https://other.invalid/v1"})
    assert rejected.status_code == 400 and path.read_bytes() == before
    cleared = client.post("/api/ai-capabilities/vision", json={"enabled": False, "credential_action": "clear"})
    assert cleared.status_code == 200
    entry = cleared.json()["capabilities"]["vision"]
    assert not entry["enabled"] and not entry["has_api_key"]
    assert json.loads(path.read_text(encoding="utf-8"))["capabilities"]["vision"]["api_key"] is None


@pytest.mark.parametrize("changes", [{"max_images": 0}, {"max_images": 21}, {"max_images": "2"},
                                    {"base_url": "http://remote.invalid"}, {"config_revision": 99}])
def test_vision_invalid_update_preserves_private_file(changes):
    client = TestClient(server.app)
    client.post("/api/ai-capabilities/vision", json=update())
    path = capability_config.capability_config_path()
    before = path.read_bytes()
    payload = update()
    payload.update(changes)
    response = client.post("/api/ai-capabilities/vision", json=payload)
    assert response.status_code == 400 and path.read_bytes() == before
    assert "fake-vision-entry-key" not in response.text


@pytest.mark.parametrize("capability", ["unknown"])
def test_unwired_capability_write_is_not_exposed(capability):
    assert TestClient(server.app).post(f"/api/ai-capabilities/{capability}", json={}).status_code == 404


@pytest.mark.parametrize("capability", ["image", "video"])
def test_media_capability_write_requires_fields(capability):
    assert TestClient(server.app).post(f"/api/ai-capabilities/{capability}", json={}).status_code == 400


def test_vision_cross_origin_rejected_before_storage(monkeypatch):
    writer = Mock()
    monkeypatch.setattr(server, "update_capability_settings", writer)
    response = TestClient(server.app).post("/api/ai-capabilities/vision", json=update(), headers={"Origin": "https://other.invalid"})
    assert response.status_code == 403
    writer.assert_not_called()


@pytest.mark.parametrize("payload", [{}, {"cloud_authorized": False}])
def test_empty_update_does_not_create_v2_or_confirm_legacy(payload):
    llm_config.save_llm_settings(base_url="https://legacy.invalid/v1", model="legacy", timeout_seconds=30, max_images=4, api_key="fake-legacy-entry")
    before = llm_config.CONFIG_PATH.read_bytes()
    response = TestClient(server.app).post("/api/ai-capabilities/text", json=payload)
    assert response.status_code == 400
    assert not capability_config.capability_config_path().exists()
    assert llm_config.CONFIG_PATH.read_bytes() == before


def test_legacy_text_can_be_saved_disabled_without_reentering_key():
    llm_config.save_llm_settings(base_url="https://legacy.invalid/v1", model="legacy", timeout_seconds=30, max_images=4, api_key="fake-legacy-entry")
    response = TestClient(server.app).post("/api/ai-capabilities/text", json={"enabled": False, "credential_action": "keep", "adapter": ""})
    assert response.status_code == 200
    entry = response.json()["capabilities"]["text"]
    assert entry["has_api_key"] and not entry["enabled"] and not entry["needs_confirmation"]
    assert "fake-legacy-entry" not in response.text


def test_content_source_labels_name_the_local_output():
    """四个来源档位的中文字都在 app.js 里钉住：本机能跑通不等于界面会说真话。

    这一条是文本不变量，不是渲染实测——真浏览器里那四处标签长什么样，得另起服务亲眼点。
    """
    app_js = (Path(server.STATIC_DIR) / "app.js").read_text(encoding="utf-8")
    for value, label in (("local", "本次本机模型生成"),
                         ("local_truncated", "本机模型没写完，退回本地模板"),
                         ("mixed", "模型生成与本地模板混合"),
                         ("template", "本地模板（未启用、缺密钥或模型调用没成功时降级）")):
        assert f'{value}: "{label}"' in app_js, f"来源 {value} 的中文档位不见了或措辞变了"
    for value, label in (("local", "本机"), ("local_truncated", "本机没写完")):
        assert f'{value}: "{label}"' in app_js, f"渠道卡片的 {value} 档位词不见了"
    # 旧的二档判断必须清干净：留着就会把本机产出说成云端，把本机撞上限说成本地模板。
    assert "云端与本地模板混合" not in app_js, "旧的混合句还在，会把本机说成云端"
    assert 'generation_sources?.[item.channel] === "cloud"' not in app_js, "卡片还在用二档判断"
    assert '=== "cloud" ? "云端生成"' not in app_js, "视觉/视频卡片还在用二档判断"


def test_frontend_saves_only_selected_capability_and_clears_keys():
    script = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const nodes = {};
const node = id => nodes[id] ||= {value:'',checked:false,disabled:false,required:false,hidden:false,textContent:'',classList:{add(){},remove(){}},focus(){},reset(){},reportValidity(){return !Object.values(nodes).some(n=>n.required && !n.disabled && !n.value);}};
const entries = {text:{enabled:false,execution_location:'cloud',base_url:'',model:'',timeout_seconds:90,has_api_key:false},vision:{enabled:false,execution_location:'cloud',base_url:'',model:'',timeout_seconds:90,max_images:0,has_api_key:false}};
const requests=[];
const tabs=['text','vision'].map(capability=>({dataset:{capability},setAttribute(name,value){this[name]=value;}}));
const context={document:{addEventListener(){},getElementById:node,querySelectorAll(){return tabs;}},window:{confirm(){return true;}},setTimeout,clearTimeout,AbortController};
vm.createContext(context);
vm.runInContext(fs.readFileSync(process.argv[1],'utf8'),context);
vm.runInContext('api = async (path, options) => { requests.push({path, body:options ? JSON.parse(options.body):null}); return {capabilities:entries}; }', Object.assign(context,{requests,entries}));
(async()=>{
 await context.showAISettings();
 assert.strictEqual(requests[0].path,'/api/ai-capabilities');
 node('aiKey').value='discard-me';
 context.selectAICapability('vision');
 assert.strictEqual(node('aiKey').value,'');
 node('aiEnabled').checked=true;
 node('aiBaseUrl').value='https://vision.invalid/v1';
 node('aiModel').value='vision-test';
 node('aiMaxImages').value='2';
 node('aiCredentialAction').value='replace';
 node('aiKey').value='fake-ui-key';
 await context.saveAISettings();
 const saved=requests.at(-1);
 assert.strictEqual(saved.path,'/api/ai-capabilities/vision');
 assert.strictEqual(saved.body.max_images,2);
 assert.strictEqual(saved.body.api_key,'fake-ui-key');
 assert.strictEqual(node('aiKey').value,'');
 assert(node('aiSettingsStatus').textContent.includes('尚未测试连接'));
 context.selectAICapability('text');
 node('aiCredentialAction').value='clear';
 await context.saveAISettings();
 assert.strictEqual(requests.at(-1).body.enabled,false);
 assert(!('max_images' in requests.at(-1).body));
 assert(!('api_key' in requests.at(-1).body));
 node('aiKey').value='close-me';
 context.closeAISettings();
 assert.strictEqual(node('aiKey').value,'');
 context.selectAICapability('vision');
 await context.showAISettings();
 node('aiModel').value='unsaved-model';
 context.window.confirm=()=>false;
 context.closeAISettings();
 assert.strictEqual(node('aiModel').value,'unsaved-model');
 context.window.confirm=()=>true;
 context.renderAISettings('text');
 node('aiEnabled').checked=true;
 node('aiCredentialAction').value='clear';
 context.syncAISettingsFields();
 assert.strictEqual(node('aiEnabled').checked,false);
 node('aiCredentialAction').value='keep';
 context.syncAISettingsFields();
 assert.strictEqual(node('aiEnabled').checked,true);
 node('aiLocation').value='local';
 context.syncAISettingsFields();
 assert.strictEqual(node('aiEnabled').checked,true,'文字选本机已不再被强制关闭');
 entries.text.has_api_key=true;
 context.syncAISettingsFields();
 assert.strictEqual(node('aiEnabled').checked,false,'本机这一格留着云端密钥时必须先走清除');
 entries.text.has_api_key=false;
 node('aiLocation').value='cloud';
 context.syncAISettingsFields();
 assert.strictEqual(node('aiEnabled').checked,true);
 assert.strictEqual(tabs[0]['aria-pressed'],'true');
 entries.text.enabled=false;
 entries.text.needs_confirmation=true;
 entries.text.has_api_key=true;
 context.renderAISettings('text');
 assert.strictEqual(node('aiCredentialAction').value,'keep');
 assert.strictEqual(node('aiKey').required,false);
 await context.saveAISettings();
 assert.strictEqual(requests.at(-1).body.adapter,'');
 const beforeInvalid=requests.length;
 node('aiCredentialAction').value='replace';
 node('aiKey').value='';
 await context.saveAISettings();
 assert.strictEqual(requests.length,beforeInvalid);
 vm.runInContext('api = async () => { throw new Error("更换地址、协议或执行位置时，请显式 clear 或 replace 该能力的密钥"); }',context);
 node('aiCredentialAction').value='replace';
 node('aiKey').value='failed-save-key';
 await context.saveAISettings();
 assert.strictEqual(node('aiKey').value,'');
 assert(node('aiSettingsStatus').textContent.includes('保存失败'));
 assert(node('aiSettingsStatus').textContent.includes('请显式 clear 或 replace'));
 assert.strictEqual(node('aiSettingsSave').disabled,false);
 vm.runInContext('aiSettingsEditor.saving = true',context);
 const count=requests.length;
 await context.saveAISettings();
 assert.strictEqual(requests.length,count);
})().catch(error=>{console.error(error);process.exitCode=1;});
'''
    node = shutil.which("node")
    assert node
    result = subprocess.run([node, "-e", script, str(Path(server.STATIC_DIR) / "app.js")], capture_output=True, text=True, errors="replace")
    assert result.returncode == 0, result.stderr


KEY_CHANGE_DETAIL = "更换地址、协议或执行位置时，请显式 clear 或 replace 该能力的密钥"
OTHER_BASE = "https://other-legacy.invalid/v1"
VISION_EMPTY_KEY = "fake-vision-empty-key-223344"


def candidate_enable(**overrides) -> dict:
    payload = {"enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
               "base_url": LEGACY_BASE, "model": LEGACY_MODEL, "timeout_seconds": 30,
               "credential_action": "keep"}
    payload.update(overrides)
    return payload


async def _async_probe():
    async with httpx.AsyncClient() as client:
        return await client.get("https://models.invalid/v1/chat/completions")


def record_changes_order(monkeypatch, seen):
    real = server.update_capability_settings

    def spy(capability, changes):
        seen.append((capability, list(changes)))
        return real(capability, changes)

    monkeypatch.setattr(server, "update_capability_settings", spy)


def test_outbound_guard_blocks_and_records_sync_and_async(outbound_guard):
    with pytest.raises(AssertionError):
        httpx.get("https://models.invalid/v1/chat/completions")
    with pytest.raises(AssertionError):
        asyncio.run(_async_probe())
    assert [kind for kind, _ in outbound_guard] == ["sync", "async"]
    assert all("models.invalid" in url for _, url in outbound_guard)
    outbound_guard.clear()
    client = TestClient(server.app)
    assert client.get("/api/ai-capabilities").status_code == 200
    assert client.post("/api/ai-capabilities/vision", json=update()).status_code == 200
    assert outbound_guard == []


@pytest.mark.parametrize("changes", [
    {"enabled": True, "adapter": "openai_chat", "base_url": OTHER_BASE, "execution_location": "cloud",
     "model": LEGACY_MODEL, "timeout_seconds": 30, "credential_action": "keep"},
    {"credential_action": "keep", "base_url": OTHER_BASE, "model": LEGACY_MODEL,
     "execution_location": "cloud", "enabled": True, "adapter": "openai_chat", "timeout_seconds": 30},
], ids=["adapter-first", "credential-first"])
def test_migration_candidate_with_address_change_still_rejected(outbound_guard, monkeypatch, changes):
    v1 = seed_legacy_v1()
    v1_before = v1.read_bytes()
    v2 = capability_config.capability_config_path()
    seen = []
    record_changes_order(monkeypatch, seen)
    response = TestClient(server.app).post("/api/ai-capabilities/text", json=changes)
    assert response.status_code == 400
    assert response.json()["detail"] == KEY_CHANGE_DETAIL
    assert LEGACY_KEY not in response.text
    assert seen == [("text", list(changes))]
    assert not v2.exists()
    assert v1.read_bytes() == v1_before
    assert outbound_guard == []


@pytest.mark.parametrize("changes,adapter_before_location", [
    ({"enabled": True, "adapter": "openai_chat", "execution_location": "local", "base_url": LEGACY_BASE,
     "model": LEGACY_MODEL, "timeout_seconds": 30, "credential_action": "keep"}, True),
    ({"timeout_seconds": 30, "credential_action": "keep", "execution_location": "local",
     "base_url": LEGACY_BASE, "model": LEGACY_MODEL, "enabled": True, "adapter": "openai_chat"}, False),
], ids=["adapter-first", "location-first"])
def test_migration_candidate_with_location_change_still_rejected(outbound_guard, monkeypatch, changes,
                                                                 adapter_before_location):
    v1 = seed_legacy_v1()
    v1_before = v1.read_bytes()
    v2 = capability_config.capability_config_path()
    seen = []
    record_changes_order(monkeypatch, seen)
    response = TestClient(server.app).post("/api/ai-capabilities/text", json=changes)
    assert response.status_code == 400
    assert response.json()["detail"] == KEY_CHANGE_DETAIL
    assert LEGACY_KEY not in response.text
    assert seen == [("text", list(changes))]
    assert (list(changes).index("adapter") < list(changes).index("execution_location")) is adapter_before_location
    assert not v2.exists()
    assert v1.read_bytes() == v1_before
    assert outbound_guard == []


@pytest.mark.parametrize("changes", [
    {"adapter": "openai_chat", "credential_action": "keep"},
    {"enabled": False, "adapter": "openai_chat", "credential_action": "keep"},
], ids=["no-enabled", "enabled-false"])
def test_migration_candidate_backfill_without_enable_is_still_refused(outbound_guard, changes):
    v1 = seed_legacy_v1()
    v1_before = v1.read_bytes()
    v2 = capability_config.capability_config_path()
    response = TestClient(server.app).post("/api/ai-capabilities/text", json=changes)
    assert response.status_code == 400
    assert response.json()["detail"] == KEY_CHANGE_DETAIL
    assert LEGACY_KEY not in response.text
    assert not v2.exists()
    assert v1.read_bytes() == v1_before
    assert outbound_guard == []


def test_confirmed_text_with_empty_adapter_is_not_exempt(outbound_guard):
    seed_legacy_v1()
    client = TestClient(server.app)
    saved = client.post("/api/ai-capabilities/text",
                        json={"enabled": False, "credential_action": "keep", "adapter": ""})
    assert saved.status_code == 200
    v2 = capability_config.capability_config_path()
    stored = json.loads(v2.read_text(encoding="utf-8"))["capabilities"]["text"]
    assert stored["needs_confirmation"] is False
    assert stored["adapter"] == ""
    assert stored["api_key"] == LEGACY_KEY
    before = v2.read_bytes()
    refused = client.post("/api/ai-capabilities/text",
                          json={"enabled": True, "adapter": "openai_chat", "credential_action": "keep"})
    assert refused.status_code == 400
    assert refused.json()["detail"] == KEY_CHANGE_DETAIL
    assert LEGACY_KEY not in refused.text
    assert v2.read_bytes() == before
    assert outbound_guard == []


def test_other_capability_with_empty_adapter_is_not_exempt(outbound_guard):
    client = TestClient(server.app)
    seeded = client.post("/api/ai-capabilities/vision", json={
        "enabled": False, "execution_location": "cloud", "adapter": "",
        "base_url": "https://vision-entry.invalid/v1", "model": "vision-entry-model",
        "timeout_seconds": 20, "max_images": 0, "credential_action": "replace",
        "api_key": VISION_EMPTY_KEY})
    assert seeded.status_code == 200
    stored = seeded.json()["capabilities"]["vision"]
    assert stored["adapter"] == "" and stored["has_api_key"] is True
    assert stored["needs_confirmation"] is False
    v2 = capability_config.capability_config_path()
    before = v2.read_bytes()
    refused = client.post("/api/ai-capabilities/vision", json={
        "enabled": True, "adapter": "openai_chat", "max_images": 2, "credential_action": "keep"})
    assert refused.status_code == 400
    assert refused.json()["detail"] == KEY_CHANGE_DETAIL
    assert VISION_EMPTY_KEY not in refused.text
    assert v2.read_bytes() == before
    assert outbound_guard == []


def test_confirmed_text_keep_protocol_change_still_rejected_for_legal_scenario(outbound_guard):
    seed_legacy_v1()
    client = TestClient(server.app)
    assert client.post("/api/ai-capabilities/text", json=candidate_enable()).status_code == 200
    v2 = capability_config.capability_config_path()
    before = v2.read_bytes()
    refused = client.post("/api/ai-capabilities/text",
                          json={"enabled": False, "adapter": "", "credential_action": "keep"})
    assert refused.status_code == 400
    assert refused.json()["detail"] == KEY_CHANGE_DETAIL
    assert v2.read_bytes() == before
    # 同一字段集只把 keep 换成 clear 就通过现有配置校验，证明上面拦的是换凭证保护本身，不是另一条独立校验。
    cleared = client.post("/api/ai-capabilities/text",
                          json={"enabled": False, "adapter": "", "credential_action": "clear"})
    assert cleared.status_code == 200
    entry = cleared.json()["capabilities"]["text"]
    assert entry["enabled"] is False and entry["has_api_key"] is False and entry["adapter"] == ""
    assert LEGACY_KEY not in cleared.text
    assert outbound_guard == []


def test_apply_capability_update_rejects_real_adapter_swap_under_keep():
    settings = llm_config.default_capability_settings()
    settings["capabilities"]["image"] = {
        "enabled": True, "execution_location": "cloud", "adapter": "openai_images",
        "base_url": "https://image.invalid/v1", "model": "image-model", "timeout_seconds": 30,
        "api_key": "fake-image-key-556677", "config_revision": 1, "needs_confirmation": False}
    with pytest.raises(ValueError, match="更换地址、协议或执行位置"):
        llm_config.apply_capability_update(settings, "image",
                                           {"adapter": "dashscope_images", "credential_action": "keep"})
    rotated = llm_config.apply_capability_update(settings, "image", {
        "adapter": "dashscope_images", "credential_action": "replace", "api_key": "fake-image-key-889900"})
    assert rotated["capabilities"]["image"]["adapter"] == "dashscope_images"
    assert rotated["capabilities"]["image"]["api_key"] == "fake-image-key-889900"


def test_existing_v2_still_requires_key_action_for_address_or_location(outbound_guard):
    seed_legacy_v1()
    client = TestClient(server.app)
    assert client.post("/api/ai-capabilities/text", json=candidate_enable()).status_code == 200
    v2 = capability_config.capability_config_path()
    before = v2.read_bytes()
    for changes in ({"base_url": OTHER_BASE, "credential_action": "keep"},
                    {"execution_location": "local", "credential_action": "keep"}):
        refused = client.post("/api/ai-capabilities/text", json=changes)
        assert refused.status_code == 400
        assert refused.json()["detail"] == KEY_CHANGE_DETAIL
        assert LEGACY_KEY not in refused.text
        assert v2.read_bytes() == before
    assert outbound_guard == []
