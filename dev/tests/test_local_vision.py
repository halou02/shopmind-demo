"""第二批 2B：本机图片理解最小接入的隔离测试。

只用假凭据、pytest 临时目录、本文件用 Pillow 现造的假图片，以及 Mock 传输层：
不读真实私有配置、不调用真实模型（包括 Ollama）、不发任何网络请求、不碰仓库里的真实素材包。

计数口径是「尝试构造 HTTP 客户端 / 调用 post」的次数，Mock 只记录不抛错。
一个必须说清的事实：app.core.llm_client.httpx 与 app.core.local_vision_client.httpx 是同一个模块
对象，所以 patch httpx.Client 只能挂一个工厂，云端与本机共用它，区分去向靠 record 下来的 URL：
/api/chat 是本机，/chat/completions 是云端。「本机跑了几次」和「云端有没有被偷偷打」两条
由此都是实测数字，不是推论。

假的 Ollama 响应只证明接线通了，不证明真实模型的回答质量、速度或成功率。
"""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.core import ai_product_analyzer, capability_config, llm_config
from app.core.ai_product_analyzer import (
    AIProductAnalysis,
    _analysis_schema_hint,
    _LOCAL_LIST_FIELDS,
    _LOCAL_STR_FIELDS,
    analyze_product,
    read_text_materials,
    summarize_local_evidence,
)
from app.core.content_generator import build_content_package
from app.core.evidence_extractor import extract_evidence_from_index
from app.core.llm_client import LLMClientError
from app.core.llm_config import LocalVisionConfig, cloud_authorization
from app.core.local_vision_client import (
    LOCAL_IMAGE_MAX_SIDE,
    build_chat_body,
    build_local_client,
    encode_local_image,
    local_chat_completion_json,
    local_chat_endpoint,
    response_to_content,
    select_first_valid_image,
)
from app.core.llm_content_generator import _build_context_payload
from app.core.source_scanner import scan_library
from app.web import server
from tests.test_content_generator import _build_pack, _reports

LOCAL_BASE = "http://127.0.0.1:11434"
LOCAL_MODEL = "qwen3-vl:2b"
CLOUD_VISION_BASE = "https://vision.invalid/v1"
TEXT_BASE = "https://text.invalid/v1"
TEXT_KEY = "text-capability-key-112233"
VISION_KEY = "vision-capability-key-998877"
IMAGE_KEY = "image-capability-key-223344"
LEGACY_KEY = "legacy-v1-key-445566"
TAILS = ("112233", "998877", "223344", "445566")

TRUNCATION_MARKER = ai_product_analyzer._LOCAL_TEXT_TRUNCATED.strip()
REPEAT_UNIT = "补充说明"


def valid_reply(**changes) -> dict:
    """13 个字段的完整假响应：只用来证明接线，不代表真实模型给出的内容质量。"""
    data = {
        "product_name": "苔绿通勤托特包",
        "category_positioning": "给通勤族的轻量大容量包",
        "product_facts": {"颜色": "苔绿色", "材质": "尼龙面料"},
        "selling_points": ["容量大", "配色耐看"],
        "target_audience": "25-35 岁通勤人群",
        "usage_scenarios": ["通勤", "短途出差"],
        "user_likes": ["轻便"],
        "user_concerns": ["防水存疑"],
        "content_opportunities": "以颜色切入",
        "brand_tone": "克制专业",
        "visual_keywords": ["苔绿", "尼龙"],
        "core_opportunity": "把容量讲成日常确定性",
        "data_gaps": ["缺尺寸标注"],
    }
    for dropped in changes.pop("drop", ()) or ():
        data.pop(dropped)
    data.update(changes)
    return data


EMPTY_REPLY = dict.fromkeys(valid_reply(), "") | {"product_facts": {}, "selling_points": [],
                                                  "usage_scenarios": [], "user_likes": [],
                                                  "user_concerns": [], "visual_keywords": [],
                                                  "data_gaps": []}


def v2_path() -> Path:
    return capability_config.capability_config_path()


def assert_no_secret(payload: str) -> None:
    for tail in TAILS:
        assert tail not in payload, f"输出里出现了密钥片段：{tail}"


def local_config(**changes) -> LocalVisionConfig:
    data = {"base_url": LOCAL_BASE, "model": LOCAL_MODEL, "timeout_seconds": 45, "max_images": 1}
    data.update(changes)
    return LocalVisionConfig(**data)


def enable_local_vision(**changes) -> dict:
    payload = {"enabled": True, "execution_location": "local", "adapter": "ollama_chat",
               "base_url": LOCAL_BASE, "model": LOCAL_MODEL, "timeout_seconds": 45,
               "max_images": 1, "credential_action": "keep"}
    payload.update(changes)
    return capability_config.update_capability_settings("vision", payload)


def enable_cloud_vision(**changes) -> dict:
    payload = {"enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
               "base_url": CLOUD_VISION_BASE, "model": "vision-model", "timeout_seconds": 45,
               "max_images": 2, "credential_action": "replace", "api_key": VISION_KEY}
    payload.update(changes)
    return capability_config.update_capability_settings("vision", payload)


def enable_text(**changes) -> dict:
    payload = {"enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
               "base_url": TEXT_BASE, "model": "text-model", "timeout_seconds": 45,
               "credential_action": "replace", "api_key": TEXT_KEY}
    payload.update(changes)
    return capability_config.update_capability_settings("text", payload)


def enable_image() -> dict:
    return capability_config.update_capability_settings("image", {
        "enabled": True, "execution_location": "cloud", "adapter": "openai_images",
        "base_url": "https://image.invalid/v1", "model": "image-model", "timeout_seconds": 45,
        "credential_action": "replace", "api_key": IMAGE_KEY})


def clear_key_and_disable() -> dict:
    """页面上「清除密钥并关闭当前能力」那一步实际发出的载荷形状。

    不是只发 credential_action：表单在 forcedOff 时把 enabled 勾强制取消并一起提交，
    而「云端 + 已启用 + 无密钥」本来就是第一批定的非法形状，只发清除会被校验挡下。
    """
    return capability_config.update_capability_settings("vision", {
        "enabled": False, "execution_location": "cloud", "adapter": "openai_chat",
        "base_url": CLOUD_VISION_BASE, "model": "vision-model", "timeout_seconds": 45,
        "max_images": 2, "credential_action": "clear"})


def write_v1() -> None:
    llm_config.save_llm_settings(base_url="https://legacy.invalid/v1", model="legacy-model",
                                 timeout_seconds=30, max_images=4, api_key=LEGACY_KEY)


def make_image(path: Path, *, size: tuple[int, int] = (900, 300),
               color: tuple[int, int, int] = (120, 40, 200)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def local_pack(tmp_path: Path, *, names: tuple[str, ...] = ("01_主图.png", "02_细节.png", "03_场景.png"),
               material_chars: int = 0) -> Path:
    """本文件现造的素材包：真图片 + 可控长度的文字资料，绝不读仓库里的真实素材。"""
    pack = tmp_path / "localpack"
    images = pack / "商品图片"
    images.mkdir(parents=True)
    palette = [(10, 200, 30), (200, 30, 10), (30, 10, 200), (180, 180, 20)]
    for index, name in enumerate(names):
        make_image(images / name, size=(900 - 100 * index, 300 + 50 * index),
                   color=palette[index % len(palette)])
    docs = pack / "参考资料"
    docs.mkdir(parents=True)
    (docs / "brief.txt").write_text("材质：尼龙面料\n卖点：大容量通勤" + "补充说明" * (material_chars // 4),
                                    encoding="utf-8")
    return pack


def text_only_pack(tmp_path: Path) -> Path:
    pack = tmp_path / "onlytext"
    (pack / "参考资料").mkdir(parents=True)
    (pack / "参考资料" / "brief.txt").write_text("材质：尼龙面料\n卖点：大容量通勤", encoding="utf-8")
    return pack


def corrupt_images(pack: Path) -> list[Path]:
    broken = []
    for path in sorted((pack / "商品图片").glob("*")):
        path.write_bytes(b"png")
        broken.append(path)
    return broken


def analyze(pack: Path, **kwargs) -> AIProductAnalysis:
    report = scan_library(pack)
    index = report.index()
    extraction = extract_evidence_from_index(index, report.library_root)
    return analyze_product(material_root=pack, index=index,
                           product_form=kwargs.pop("product_form", {"product_category": "箱包"}),
                           extraction=extraction, **kwargs)


def decoded(payload: str) -> Image.Image:
    return Image.open(io.BytesIO(base64.b64decode(payload)))


def ollama_payload(reply: object, *, drop: str | None = None, raw: object = None,
                   **envelope) -> dict:
    content = json.dumps(reply, ensure_ascii=False) if raw is None else raw
    payload = {"model": LOCAL_MODEL, "message": {"role": "assistant", "content": content},
               "done": True, "done_reason": "stop", "eval_count": 120}
    payload.update(envelope)
    if drop is not None:
        payload.pop(drop, None)
    return payload


def ollama_response(reply: object, *, status_code: int = 200, **kwargs) -> Mock:
    payload = ollama_payload(reply, **kwargs)
    return Mock(status_code=status_code, text=json.dumps(payload, ensure_ascii=False, default=str),
                json=Mock(return_value=payload))


def completion_response(content: str, *, status_code: int = 200) -> Mock:
    """云端 OpenAI 兼容形状，用来对照云端那两支没被本批改动。"""
    return Mock(status_code=status_code, text=content,
                json=Mock(return_value={"choices": [{"message": {"content": content}}]}))


def record_http(monkeypatch, response: Mock | None = None, *, side_effect=None):
    factory = Mock()
    transport = Mock()
    transport.__enter__ = Mock(return_value=transport)
    transport.__exit__ = Mock(return_value=False)
    if side_effect is not None:
        transport.post.side_effect = side_effect
    else:
        transport.post.return_value = response if response is not None else ollama_response(valid_reply())
    factory.return_value = transport
    # 两处 httpx 是同一个模块对象：挂一次就同时覆盖本机与云端，之后用 URL 分辨去向。
    monkeypatch.setattr("app.core.local_vision_client.httpx.Client", factory)
    return factory, transport


def local_calls(transport) -> list:
    return [call for call in transport.post.call_args_list if call.args[0].endswith("/api/chat")]


def cloud_calls(transport) -> list:
    return [call for call in transport.post.call_args_list
            if call.args[0].endswith("/chat/completions")]


def sent_body(transport) -> dict:
    assert len(local_calls(transport)) == 1, f"本机 POST 次数应为 1，实测 {len(local_calls(transport))}"
    return local_calls(transport)[0].kwargs["json"]


def sent_prompt(transport) -> str:
    return next(message["content"] for message in sent_body(transport)["messages"]
                if message["role"] == "user")


def assert_attempted_once_and_offline(analysis, factory, transport) -> str:
    """失败路径判定面：恰好一次本机尝试、零次云端尝试、落回离线、说的是本机的话。"""
    assert factory.return_value is not None
    assert factory.call_count == 1, "一次商品理解最多构造一个 HTTP 客户端"
    assert len(local_calls(transport)) == 1, "本机这一支只许发出一次 POST"
    assert cloud_calls(transport) == [], "本机图片理解失败后不得转云端"
    assert analysis.source == "offline" and analysis.model == ""
    assert analysis.fallback_reason == ai_product_analyzer._LOCAL_ANALYSIS_FAILED
    assert analysis.execution_location == "local"
    assert analysis.analyzed_image == ""
    rendered = json.dumps(analysis.to_dict(), ensure_ascii=False)
    assert_no_secret(rendered)
    return rendered


# --- 1. 配置边界：本机条目的合法形状与四种非法形状（§二.9、§九.1、§九.2）---

def test_local_vision_saves_without_a_key_and_constructs_zero_clients(monkeypatch):
    outgoing = Mock(side_effect=AssertionError("保存配置不得构造任何 HTTP 客户端"))
    monkeypatch.setattr("app.core.local_vision_client.httpx.Client", outgoing)
    settings = enable_local_vision()
    assert v2_path().exists()
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["vision"]
    assert on_disk["enabled"] is True and on_disk["api_key"] is None
    assert on_disk["adapter"] == "ollama_chat" and on_disk["max_images"] == 1
    public = llm_config.public_capability_settings(settings)["capabilities"]["vision"]
    assert public["has_api_key"] is False and public["connection_status"] == "not_tested"
    assert public["adapter"] == "ollama_chat" and "api_key" not in public
    assert_no_secret(json.dumps(public, ensure_ascii=False))
    outgoing.assert_not_called()


@pytest.mark.parametrize("changes", [
    pytest.param({"base_url": "https://remote.invalid/v1"}, id="非回环-https远端"),
    pytest.param({"base_url": "http://192.168.1.5:11434"}, id="局域网"),
    pytest.param({"base_url": "http://localhost.evil.com:11434"}, id="名字含localhost的远端"),
    pytest.param({"base_url": "http://2130706433:11434"}, id="回环IP的整数写法"),
    pytest.param({"base_url": "http://0.0.0.0:11434"}, id="0.0.0.0"),
    pytest.param({"adapter": "openai_chat"}, id="本机配错协议"),
    pytest.param({"max_images": 2}, id="本机想发多张"),
    pytest.param({"credential_action": "replace", "api_key": VISION_KEY}, id="本机带密钥")])
def test_illegal_local_shapes_are_rejected_without_touching_the_file(changes):
    """基线是一份合法的本机配置：这样每一条失败都只能是因为它自己那一形状不合格。"""
    enable_local_vision()
    before = v2_path().read_bytes()
    with pytest.raises(ValueError):
        enable_local_vision(**changes)
    assert v2_path().read_bytes() == before


def test_the_position_and_protocol_pairing_is_checked_in_both_directions():
    """配对规则单独成测：云端那一格留密钥，本机那一格不带密钥，两条失败都只能来自配对本身。"""
    defaults = llm_config.default_capability_settings()
    for location, adapter, key in (("cloud", "ollama_chat", VISION_KEY), ("local", "openai_chat", None)):
        entry = {**defaults["capabilities"]["vision"],
                 "enabled": True, "execution_location": location, "adapter": adapter,
                 "base_url": LOCAL_BASE if location == "local" else CLOUD_VISION_BASE,
                 "model": LOCAL_MODEL, "max_images": 1, "api_key": key}
        settings = {**defaults, "capabilities": {**defaults["capabilities"], "vision": entry}}
        with pytest.raises(ValueError) as error:
            llm_config.validate_capability_settings(settings)
        assert "协议" in str(error.value), f"{location}+{adapter} 的失败文案不含配对规则：{error.value}"
        assert_no_secret(str(error.value))


def test_local_can_stay_disabled_as_a_draft_without_the_enable_checks():
    """未启用的草稿只受「本机不许存密钥」管：地址没填也不报错，保存路径不被卡死。"""
    settings = enable_local_vision(enabled=False, base_url="", model="", max_images=0)
    entry = settings["capabilities"]["vision"]
    assert entry["enabled"] is False and entry["execution_location"] == "local"
    assert entry["base_url"] == "" and entry["max_images"] == 0
    with pytest.raises(ValueError):
        enable_local_vision(enabled=False, credential_action="replace", api_key=VISION_KEY)


# --- 2. 回环判定：字面 localhost 与显式回环 IP，拒绝伪 localhost（§六）---

@pytest.mark.parametrize("value,expected", [
    ("http://127.0.0.1:11434", "http://127.0.0.1:11434/api/chat"),
    ("http://127.0.0.1:11434/", "http://127.0.0.1:11434/api/chat"),
    ("http://localhost:11434", "http://127.0.0.1:11434/api/chat"),
    ("http://LOCALHOST:11434/v1", "http://127.0.0.1:11434/v1/api/chat"),
    ("http://127.0.0.42:11434", "http://127.0.0.42:11434/api/chat"),
    ("http://[::1]:11434", "http://[::1]:11434/api/chat"),
    ("https://localhost:11434", "https://127.0.0.1:11434/api/chat"),
    # 把接口文档里的后缀一起粘进来：这些写法必须落到同一个地址，不能拼成 /api/api/chat
    ("http://localhost:11434/api", "http://127.0.0.1:11434/api/chat"),
    ("http://localhost:11434/api/", "http://127.0.0.1:11434/api/chat"),
    ("http://localhost:11434/api/chat", "http://127.0.0.1:11434/api/chat"),
    ("http://localhost:11434/API/Chat/", "http://127.0.0.1:11434/api/chat"),
    ("http://localhost:11434/api/api/chat", "http://127.0.0.1:11434/api/chat"),
    ("http://localhost:11434/ollama/api", "http://127.0.0.1:11434/ollama/api/chat")])
def test_loopback_endpoints_always_connect_to_a_loopback_target(value, expected):
    """字面 localhost 一律换成 127.0.0.1：实际连接目标不能依赖 hosts 或 DNS 怎么解析这个名字。"""
    assert local_chat_endpoint(value) == expected


@pytest.mark.parametrize("value", [
    "http://localhost:11434/api", "http://localhost:11434/api/", "http://localhost:11434/api/chat"])
def test_a_pasted_interface_suffix_reaches_the_same_endpoint_as_the_bare_host(value):
    """真实事故：界面里存了 http://localhost:11434/api，旧实现拼出 /api/api/chat，Ollama 回 404。"""
    assert local_chat_endpoint(value) == local_chat_endpoint("http://localhost:11434")


@pytest.mark.parametrize("value", [
    "http://localhost.evil.com:11434", "http://notlocalhost:11434", "http://2130706433:11434",
    "http://0.0.0.0:11434", "http://192.168.0.9:11434", "http://user:pw@localhost:11434",
    "http://localhost:11434?token=1", "http://localhost:11434#frag", "http://[::]:11434",
    "ftp://localhost:11434", "", "not a url"])
def test_non_loopback_or_shaped_like_loopback_is_refused_before_any_socket(value):
    with pytest.raises(LLMClientError):
        local_chat_endpoint(value)


def test_the_loopback_rule_is_enforced_at_the_config_boundary_not_only_in_the_client():
    for value in ("http://localhost.evil.com:11434", "http://2130706433:11434", "http://0.0.0.0:11434"):
        assert llm_config.is_loopback_base_url(value) is False
        with pytest.raises(ValueError):
            enable_local_vision(base_url=value)
    saved = enable_local_vision(base_url="http://localhost:11434")
    assert saved["capabilities"]["vision"]["base_url"] == "http://localhost:11434"


# --- 3. 两步切换与中间态（§二.8、§九.3）---

def test_switching_an_existing_key_to_local_needs_an_explicit_clear_first():
    enable_cloud_vision()
    assert json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["vision"]["api_key"] \
        == VISION_KEY
    with pytest.raises(ValueError):
        enable_local_vision()
    cleared = clear_key_and_disable()
    entry = cleared["capabilities"]["vision"]
    assert entry["api_key"] is None and entry["enabled"] is False
    assert entry["execution_location"] == "cloud", "清除这一步不会顺手改执行位置"
    moved = enable_local_vision()
    assert moved["capabilities"]["vision"]["enabled"] is True
    assert moved["capabilities"]["vision"]["execution_location"] == "local"
    assert moved["capabilities"]["vision"]["api_key"] is None


def test_local_can_be_enabled_directly_when_no_key_was_ever_saved():
    entry = enable_local_vision()["capabilities"]["vision"]
    assert entry["enabled"] is True and entry["execution_location"] == "local"
    assert json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["vision"]["api_key"] is None


def test_switching_back_to_cloud_requires_re_entering_the_key():
    """切回云端必须重新填密钥：本机那一格从来存不了密钥，keep 无密钥可留。"""
    enable_local_vision()
    with pytest.raises(ValueError) as error:
        enable_cloud_vision(credential_action="keep", api_key=None)
    assert "密钥" in str(error.value)
    assert_no_secret(str(error.value))
    entry = enable_cloud_vision()["capabilities"]["vision"]
    assert entry["execution_location"] == "cloud" and entry["adapter"] == "openai_chat"
    assert entry["api_key"] == VISION_KEY


def test_a_failed_local_enable_after_a_successful_clear_shows_the_real_middle_state():
    """清除已落盘、启用本机又失败：磁盘上就是「能力关闭 + 没有密钥」，不能拿草稿冒充已保存状态。"""
    enable_cloud_vision()
    clear_key_and_disable()
    with pytest.raises(ValueError):
        enable_local_vision(base_url="http://evil.invalid:11434")
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["vision"]
    assert on_disk["enabled"] is False and on_disk["api_key"] is None


def test_the_v2_api_answers_the_local_shape_masked_and_makes_zero_calls(monkeypatch):
    client = TestClient(server.app)
    outgoing = Mock(side_effect=AssertionError("保存设置不得发出任何请求"))
    monkeypatch.setattr("app.core.local_vision_client.httpx.Client", outgoing)
    response = client.post("/api/ai-capabilities/vision", json={
        "enabled": True, "execution_location": "local", "adapter": "ollama_chat",
        "base_url": LOCAL_BASE, "model": LOCAL_MODEL, "timeout_seconds": 45,
        "max_images": 1, "credential_action": "keep"})
    assert response.status_code == 200
    vision = response.json()["capabilities"]["vision"]
    assert vision["execution_location"] == "local" and vision["adapter"] == "ollama_chat"
    assert vision["has_api_key"] is False and "api_key" not in vision
    assert vision["connection_status"] == "not_tested"
    assert_no_secret(response.text)
    outgoing.assert_not_called()


@pytest.mark.parametrize("payload,reason", [
    ({"max_images": 3}, "本机只支持 1 张"),
    ({"credential_action": "replace", "api_key": VISION_KEY}, "本机不许存密钥"),
    ({"base_url": "http://192.168.1.9:11434"}, "非回环地址"),
    ({"adapter": "openai_chat"}, "协议与位置不配对")])
def test_the_v2_api_refuses_illegal_local_shapes_without_echo_or_write(monkeypatch, payload, reason):
    assert reason
    client = TestClient(server.app)
    monkeypatch.setattr("app.core.local_vision_client.httpx.Client", Mock(side_effect=AssertionError))
    assert client.post("/api/ai-capabilities/vision", json={
        "enabled": True, "execution_location": "local", "adapter": "ollama_chat",
        "base_url": LOCAL_BASE, "model": LOCAL_MODEL, "timeout_seconds": 45,
        "max_images": 1, "credential_action": "keep"}).status_code == 200
    before = v2_path().read_bytes()
    body = {"enabled": True, "execution_location": "local", "adapter": "ollama_chat",
            "base_url": LOCAL_BASE, "model": LOCAL_MODEL, "timeout_seconds": 45,
            "max_images": 1, "credential_action": "keep"}
    body.update(payload)
    if payload.get("adapter"):
        body["adapter"] = payload["adapter"]
    response = client.post("/api/ai-capabilities/vision", json=body)
    assert response.status_code == 400
    assert_no_secret(response.text)
    assert v2_path().read_bytes() == before


# --- 4. 请求形状：原生协议、单图、无密钥、无代理、不跟随跳转（§六、§九.4、§九.5）---

def test_local_client_is_built_without_proxy_and_without_redirects():
    """直接量真实对象：httpx 的 trust_env 默认 True（Windows 会读系统代理），本机这一支必须关掉。"""
    client = build_local_client(local_config(timeout_seconds=77))
    control = httpx.Client()
    try:
        assert client.trust_env is False
        assert client.follow_redirects is False
        assert client.timeout.connect == 77
        assert control.trust_env is True, "对照组：默认值确实会去读系统代理"
    finally:
        client.close()
        control.close()


def test_body_is_one_bare_image_with_the_declared_engine_options():
    payload = base64.b64encode(b"raw-bytes").decode("ascii")
    body = build_chat_body(local_config(), system_prompt="SYS", user_text="USER", image_base64=payload)
    assert set(body) == {"model", "messages", "stream", "think", "format", "options"}
    assert body["stream"] is False and body["think"] is False
    assert body["format"] == "json"
    assert body["options"] == {"num_ctx": 4096, "num_predict": 1200}
    assert body["messages"][0] == {"role": "system", "content": "SYS"}
    user = body["messages"][1]
    assert user["content"] == "USER" and user["images"] == [payload]
    assert not any(item.startswith("data:") for item in user["images"])
    assert "temperature" not in body and "response_format" not in body


def test_engine_options_are_the_declared_candidates_and_survive_the_config(monkeypatch, tmp_path):
    """4096 / 1200 是本机实测定的：合格输出实测 515~643 token，旧的 700 会把 JSON 写截断。"""
    assert local_config().num_ctx == 4096 and local_config().num_predict == 1200
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    analyze(local_pack(tmp_path))
    assert sent_body(transport)["options"] == {"num_ctx": 4096, "num_predict": 1200}


def test_local_run_never_touches_the_stored_cloud_keys(monkeypatch, tmp_path):
    """text 与 image 两格留着假密钥，本机跑一遍：请求与结果里都不许出现它们的片段。"""
    enable_text()
    enable_image()
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    analysis = analyze(local_pack(tmp_path))
    assert analysis.source == "llm" and analysis.execution_location == "local"
    assert len(local_calls(transport)) == 1 and cloud_calls(transport) == []
    dumped = json.dumps([str(call) for call in transport.post.call_args_list], ensure_ascii=False)
    dumped += json.dumps(analysis.to_dict(), ensure_ascii=False)
    assert_no_secret(dumped)
    for call in transport.post.call_args_list:
        assert "headers" not in call.kwargs, "本机请求不该由这一层拼 Authorization"
        assert "auth" not in call.kwargs


@pytest.mark.parametrize("status_code", [301, 302, 307, 308])
def test_redirect_status_fails_without_a_second_request(monkeypatch, status_code):
    factory, transport = record_http(monkeypatch, ollama_response(valid_reply(), status_code=status_code))
    with pytest.raises(LLMClientError) as error:
        local_chat_completion_json(local_config(), system_prompt="s", user_text="u", image_base64="eA=")
    assert "重定向" in str(error.value)
    assert transport.post.call_count == 1


@pytest.mark.parametrize("status_code", [400, 404, 500, 503])
def test_non_2xx_reports_only_the_status_code(monkeypatch, status_code):
    response = Mock(status_code=status_code, text="secret upstream detail",
                    json=Mock(side_effect=ValueError("secret upstream detail")))
    factory, transport = record_http(monkeypatch, response)
    with pytest.raises(LLMClientError) as error:
        local_chat_completion_json(local_config(), system_prompt="s", user_text="u", image_base64="eA=")
    assert str(status_code) in str(error.value)
    assert "secret upstream detail" not in str(error.value)
    assert transport.post.call_count == 1


@pytest.mark.parametrize("error", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"),
                                   httpx.ConnectError("c"), httpx.ProtocolError("p")])
def test_transport_failures_become_one_safe_message(monkeypatch, error):
    factory, transport = record_http(monkeypatch, side_effect=error)
    with pytest.raises(LLMClientError) as raised:
        local_chat_completion_json(local_config(), system_prompt="s", user_text="u",
                                   image_base64=base64.b64encode(b"x").decode())
    assert "本机" in str(raised.value) and "降级到本地规则" in str(raised.value)
    assert transport.post.call_count == 1


@pytest.mark.parametrize("envelope,expected", [
    ({"drop": "done_reason"}, None),
    ({"done_reason": "unload"}, None),
    ({"done": False}, "未完成"),
    ({"done": "true"}, "未完成"),
    ({"done_reason": "length"}, "截断"),
    ({"message": "not a dict"}, "缺少内容"),
    ({"message": {"role": "assistant", "content": 12345}}, "格式异常")])
def test_response_shape_rules(envelope, expected):
    """只喂 response_to_content：本层管信封，JSON 是否成对象归 parse_json_object，两层分开测。"""
    payload = ollama_payload(valid_reply(), **envelope)
    if expected is None:
        assert response_to_content(payload) == json.dumps(valid_reply(), ensure_ascii=False)
        return
    with pytest.raises(LLMClientError) as error:
        response_to_content(payload)
    assert expected in str(error.value)


@pytest.mark.parametrize("raw,expected", [
    ("这不是 JSON", "未返回合法 JSON"),
    (json.dumps([1, 2]), "必须是对象"),
    (json.dumps(valid_reply(), ensure_ascii=False), None)])
def test_content_is_parsed_by_the_shared_json_parser_after_the_envelope_check(monkeypatch, raw, expected):
    """信封合格之后才谈 JSON：解析仍用 llm_client.parse_json_object，本机没有另起一套。"""
    factory, transport = record_http(monkeypatch, ollama_response(valid_reply(), raw=raw))
    if expected is None:
        assert local_chat_completion_json(local_config(), system_prompt="s", user_text="u",
                                          image_base64="eA=") == valid_reply()
        return
    with pytest.raises(LLMClientError) as error:
        local_chat_completion_json(local_config(), system_prompt="s", user_text="u", image_base64="eA=")
    assert expected in str(error.value)
    assert transport.post.call_count == 1, "JSON 不合不许追加第二次请求"


def test_a_non_dict_payload_is_refused():
    with pytest.raises(LLMClientError):
        response_to_content("整段文本")
    with pytest.raises(LLMClientError):
        response_to_content(None)


def test_a_json_body_that_arrives_unparseable_fails_once(monkeypatch):
    """响应体本身不是 JSON：httpx 侧抛 ValueError，收敛成一条安全文案，且只有一次 POST。"""
    response = Mock(status_code=200, text="<html>not json</html>",
                    json=Mock(side_effect=ValueError("<html>not json</html>")))
    factory, transport = record_http(monkeypatch, response)
    with pytest.raises(LLMClientError) as error:
        local_chat_completion_json(local_config(), system_prompt="s", user_text="u", image_base64="eA=")
    assert "格式异常" in str(error.value)
    assert "<html>" not in str(error.value)
    assert transport.post.call_count == 1


# --- 5. 图片：内存缩放、不放大、原图不动、先过滤后发送（§二.5、§二.7、§九.6、§九.7）---

def test_original_file_is_untouched_and_the_copy_is_scaled(tmp_path):
    path = make_image(tmp_path / "包" / "01.png", size=(900, 300))
    before = path.read_bytes()
    payload = encode_local_image(path)
    assert payload and path.read_bytes() == before
    image = decoded(payload)
    assert max(image.size) == LOCAL_IMAGE_MAX_SIDE
    assert image.size == (448, 149)


def test_small_images_are_not_upscaled(tmp_path):
    path = make_image(tmp_path / "small.png", size=(120, 60))
    before = path.read_bytes()
    assert decoded(encode_local_image(path)).size == (120, 60)
    assert path.read_bytes() == before


def test_encoding_follows_the_bytes_not_the_extension(tmp_path):
    """扩展名写 .png 但内容其实是 JPEG：重编码按真实内容走，声明与字节不能互相矛盾。"""
    buffer = io.BytesIO()
    Image.new("RGB", (600, 200), (10, 20, 30)).save(buffer, format="JPEG")
    path = tmp_path / "mislabelled.png"
    path.write_bytes(buffer.getvalue())
    image = decoded(encode_local_image(path))
    assert image.format == "JPEG"
    assert max(image.size) == LOCAL_IMAGE_MAX_SIDE


@pytest.mark.parametrize("size,expected", [
    ((448, 448), (448, 448)), ((449, 1), (448, 1)), ((1, 449), (1, 448)),
    ((1000, 700), (448, 314)), ((2000, 2000), (448, 448))])
def test_scaling_keeps_the_aspect_ratio(tmp_path, size, expected):
    path = make_image(tmp_path / f"r-{size[0]}x{size[1]}.png", size=size)
    assert decoded(encode_local_image(path)).size == expected


@pytest.mark.parametrize("content", [b"png", b"", b"\x89PNG\r\n\x1a\n" + b"truncated-tail",
                                     b"%PDF-1.4\n", b"GIF89a" + b"\x00" * 8])
def test_undecodable_files_return_none_instead_of_being_sent(tmp_path, content):
    path = tmp_path / f"broken-{len(content)}.png"
    path.write_bytes(content)
    assert encode_local_image(path) is None


def test_selection_follows_the_given_order_and_skips_the_invalid_first(tmp_path):
    pack = local_pack(tmp_path, names=("01_主图.png", "02_细节.png"))
    first, second = sorted((pack / "商品图片").glob("*"))
    first.write_bytes(b"png")
    picked = select_first_valid_image([first, second])
    assert picked is not None and picked[0] == second
    # 第二张是 800x350，等比缩到最长边 448 后是 448x196，不放大也不改变比例。
    assert decoded(picked[1]).size == (448, 196)


def test_a_directory_or_unrelated_suffix_is_skipped_without_reading(tmp_path):
    (tmp_path / "商品图片").mkdir()
    (tmp_path / "notes.txt").write_text("不是图片", encoding="utf-8")
    other = make_image(tmp_path / "真实图片.tiff", size=(600, 600))
    missing = tmp_path / "不存在.png"
    picked = select_first_valid_image([tmp_path / "商品图片", tmp_path / "notes.txt", other, missing])
    assert picked is None


def test_no_valid_image_means_zero_requests_for_the_whole_flow(monkeypatch, tmp_path):
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    pack = local_pack(tmp_path, names=("01_主图.png", "02_细节.png"))
    assert corrupt_images(pack)
    analysis = analyze(pack)
    assert factory.call_count == 0 and transport.post.call_count == 0
    assert analysis.source == "offline"
    assert analysis.fallback_reason == ai_product_analyzer._LOCAL_NO_VALID_IMAGE
    assert "未发送模型请求" in analysis.fallback_reason


def test_the_first_valid_image_is_the_one_the_result_names(monkeypatch, tmp_path):
    """发的是哪张、结果就说哪张：用颜色确认送出去的图片与 relative path 指向同一个文件。"""
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    pack = local_pack(tmp_path, names=("01_主图.png", "02_细节.png"))
    first = pack / "商品图片" / "01_主图.png"
    first.write_bytes(b"png")
    analysis = analyze(pack)
    assert analysis.analyzed_image == "商品图片/02_细节.png"
    assert decoded(sent_body(transport)["messages"][1]["images"][0]).getpixel((5, 5)) == (200, 30, 10)


# --- 6. 分析器接入：一次尝试、单张实发、相对文件名、位置元数据（§二.2-6、§九.7-9）---

def test_local_success_reports_local_and_the_relative_image_name(monkeypatch, tmp_path):
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    pack = local_pack(tmp_path, names=("01_主图.png", "02_细节.png", "03_场景.png"))
    analysis = analyze(pack)
    assert analysis.source == "llm" and analysis.execution_location == "local"
    assert analysis.model == LOCAL_MODEL
    assert len(local_calls(transport)) == 1 and cloud_calls(transport) == []
    assert len(sent_body(transport)["messages"][1]["images"]) == 1
    assert analysis.analyzed_image == "商品图片/01_主图.png"
    rendered = json.dumps(analysis.to_dict(), ensure_ascii=False)
    assert not analysis.analyzed_image.startswith("/")
    assert "\\" not in analysis.analyzed_image
    assert str(tmp_path) not in rendered and ":" not in analysis.analyzed_image
    assert_no_secret(rendered)


def test_the_prompt_declares_exactly_one_image_even_for_a_three_image_pack(monkeypatch, tmp_path):
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    analyze(local_pack(tmp_path, names=("01_a.png", "02_b.png", "03_c.png")))
    prompt = sent_prompt(transport)
    assert "已上传 1 张商品图片。" in prompt
    assert "已上传 3 张" not in prompt


def test_the_local_text_cap_does_not_change_the_cloud_material_limit(monkeypatch, tmp_path):
    """本机把附带文本裁到 4000 字，云端那一支一个字都不裁：两条支路的资料上限互不影响。"""
    units = 1500  # 1500 x len("补充说明") = 6000 字，远超本机的 4000，但仍在云端的 12000 之内
    pack = local_pack(tmp_path, names=("01_a.png",), material_chars=units * len(REPEAT_UNIT))
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    analyze(pack)
    local_prompt = sent_prompt(transport)
    assert TRUNCATION_MARKER in local_prompt
    local_units = local_prompt.count(REPEAT_UNIT)
    assert local_units <= ai_product_analyzer.LOCAL_PROMPT_TEXT_LIMIT // len(REPEAT_UNIT)
    assert local_units < units

    v2_path().unlink()
    enable_cloud_vision()
    reply = json.dumps(valid_reply(), ensure_ascii=False)
    factory2, transport2 = record_http(monkeypatch, completion_response(reply))
    analysis = analyze(pack)
    assert analysis.source == "llm" and len(cloud_calls(transport2)) == 1
    assert local_calls(transport2) == []
    cloud_prompt = cloud_calls(transport2)[0].kwargs["json"]["messages"][1]["content"][0]["text"]
    assert TRUNCATION_MARKER not in cloud_prompt
    assert "已上传 1 张商品图片。" in cloud_prompt
    # 云端那一支一个字都没截：用同一份素材现算总次数，不预设「应该出现几次」。
    report = scan_library(pack)
    index = report.index()
    available = (read_text_materials(pack).count(REPEAT_UNIT)
                 + summarize_local_evidence(
                     extract_evidence_from_index(index, report.library_root).evidences
                 ).count(REPEAT_UNIT))
    assert cloud_prompt.count(REPEAT_UNIT) == available
    assert available > local_units


@pytest.mark.parametrize("reply,envelope", [
    (valid_reply(drop=("core_opportunity",)), {}),
    (valid_reply(product_facts={"颜色": 12}), {}),
    (valid_reply(product_facts=["不是对象"]), {}),
    ({}, {"raw": json.dumps(valid_reply(selling_points="一条字符串"), ensure_ascii=False)}),
    ({}, {"raw": json.dumps(valid_reply(selling_points=[1, 2]), ensure_ascii=False)}),
    ({}, {"raw": json.dumps(valid_reply(brand_tone=None), ensure_ascii=False)}),
    ({}, {"raw": "这不是 JSON"}),
    ({}, {"raw": json.dumps([1, 2])}),
    (EMPTY_REPLY, {}),
    ({"product_name": "只有名字"}, {})])
def test_each_bad_reply_costs_exactly_one_attempt_and_no_cloud(monkeypatch, tmp_path, reply, envelope):
    enable_local_vision()
    factory, transport = record_http(monkeypatch, ollama_response(reply, **envelope))
    analysis = analyze(local_pack(tmp_path))
    assert_attempted_once_and_offline(analysis, factory, transport)


@pytest.mark.parametrize("status_code,envelope", [
    (302, {}), (500, {}), (200, {"done": False}), (200, {"done_reason": "length"})])
def test_status_and_completion_failures_cost_exactly_one_attempt(monkeypatch, tmp_path,
                                                                 status_code, envelope):
    enable_local_vision()
    factory, transport = record_http(monkeypatch,
                                     ollama_response(valid_reply(), status_code=status_code, **envelope))
    assert_attempted_once_and_offline(analyze(local_pack(tmp_path)), factory, transport)


@pytest.mark.parametrize("error", [httpx.ConnectTimeout("t"), httpx.ReadTimeout("t"),
                                   httpx.ConnectError("c")])
def test_timeout_and_connect_failures_count_as_the_one_attempt(monkeypatch, tmp_path, error):
    """超时/连不上算那唯一的一次；文字资料真的在包里，才排掉「误发第二次文字请求」这条路。"""
    enable_local_vision()
    factory, transport = record_http(monkeypatch, side_effect=error)
    pack = local_pack(tmp_path)
    assert "材质" in (pack / "参考资料" / "brief.txt").read_text(encoding="utf-8")
    assert_attempted_once_and_offline(analyze(pack), factory, transport)


def test_eval_count_at_the_predict_budget_does_not_reject_a_valid_result(monkeypatch, tmp_path):
    """撞上限只是「可能被截断」的标记：结构完整、内容非空的结果不能单凭它否决。"""
    enable_local_vision()
    factory, transport = record_http(monkeypatch, ollama_response(valid_reply(), eval_count=700))
    analysis = analyze(local_pack(tmp_path))
    assert analysis.source == "llm" and analysis.execution_location == "local"
    assert len(local_calls(transport)) == 1
    assert "eval_count" not in json.dumps(analysis.to_dict())


def test_missing_done_reason_with_a_valid_result_still_succeeds(monkeypatch, tmp_path):
    enable_local_vision()
    factory, transport = record_http(monkeypatch, ollama_response(valid_reply(), drop="done_reason"))
    analysis = analyze(local_pack(tmp_path))
    assert analysis.source == "llm" and len(local_calls(transport)) == 1


def test_the_placeholder_only_lives_in_data_gaps(monkeypatch, tmp_path):
    """提示词要求 3 的落点：值等于那个占位原词的条目从 product_facts 挪进 data_gaps。"""
    reply = valid_reply(product_facts={"颜色": "苔绿色", "尺寸规格": "未能从图片确认",
                                       "容量/规格": " 未能从图片确认 "},
                        selling_points=["未能从图片确认", "轻"],
                        data_gaps=[])
    enable_local_vision()
    factory, transport = record_http(monkeypatch, ollama_response(reply))
    analysis = analyze(local_pack(tmp_path))
    assert analysis.source == "llm"
    assert analysis.product_facts == {"颜色": "苔绿色"}
    assert analysis.selling_points == ("轻",)
    assert any("尺寸规格" in gap and "未能从图片确认" in gap for gap in analysis.data_gaps)
    assert any("容量/规格" in gap for gap in analysis.data_gaps)
    assert not any(gap == "未能从图片确认" for gap in analysis.data_gaps)


def test_the_checked_field_list_matches_the_schema_the_prompt_publishes():
    """本地校验的字段表必须和提示词里的 schema 同步：漂移了这条就红。"""
    keys = set(json.loads(_analysis_schema_hint()))
    checked = set(_LOCAL_STR_FIELDS) | set(_LOCAL_LIST_FIELDS) | {"product_facts"}
    assert keys == checked
    assert len(checked) == 13


def test_the_system_prompt_is_reused_verbatim_and_not_rewritten(monkeypatch, tmp_path):
    """复用现有提示词，不在本机这一支另起一套：占位原词还在原文里。"""
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    analyze(local_pack(tmp_path))
    system = next(message["content"] for message in sent_body(transport)["messages"]
                  if message["role"] == "system")
    assert system == ai_product_analyzer.SYSTEM_PROMPT
    assert "未能从图片确认" in system


# --- 7. 位置来源与授权（§八、§九.8、§九.11）---

def test_local_needs_no_cloud_authorization_and_makes_no_cloud_call(monkeypatch, tmp_path):
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(False):
        analysis = analyze(local_pack(tmp_path))
    assert analysis.source == "llm" and analysis.execution_location == "local"
    assert len(local_calls(transport)) == 1 and cloud_calls(transport) == []


def test_local_with_no_image_but_text_is_zero_requests(monkeypatch, tmp_path):
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    analysis = analyze(text_only_pack(tmp_path))
    assert factory.call_count == 0 and transport.post.call_count == 0
    assert analysis.source == "offline"
    assert analysis.fallback_reason == ai_product_analyzer._LOCAL_NEEDS_IMAGE
    assert analysis.execution_location == "local"


def test_the_cloud_text_branch_is_unchanged_when_vision_is_cloud(monkeypatch, tmp_path):
    """云端「无图片、有文字资料」那一支保持原样：一次纯文字请求，措辞也是原来的。"""
    enable_cloud_vision()
    reply = json.dumps(valid_reply(), ensure_ascii=False)
    factory, transport = record_http(monkeypatch, completion_response(reply))
    analysis = analyze(text_only_pack(tmp_path))
    assert len(cloud_calls(transport)) == 1 and local_calls(transport) == []
    assert analysis.source == "llm"
    assert "当前没有商品图片" in cloud_calls(transport)[0].kwargs["json"]["messages"][1]["content"]


def test_an_explicit_local_config_is_used_verbatim_and_text_cannot_override_it(monkeypatch, tmp_path):
    enable_text()
    enable_local_vision()
    loader = Mock(side_effect=AssertionError("显式传入配置时不得再去读私有配置"))
    monkeypatch.setattr(ai_product_analyzer, "load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    analysis = analyze(local_pack(tmp_path), config=local_config(model="explicit-model"))
    assert analysis.model == "explicit-model"
    assert local_calls(transport)[0].args[0] == f"{LOCAL_BASE}/api/chat"
    assert sent_body(transport)["model"] == "explicit-model"
    assert cloud_calls(transport) == [] and len(local_calls(transport)) == 1
    loader.assert_not_called()


def test_a_disabled_or_unconfirmed_vision_gate_still_blocks_every_request(monkeypatch, tmp_path):
    enable_local_vision(enabled=False)
    factory, transport = record_http(monkeypatch)
    analysis = analyze(local_pack(tmp_path))
    assert factory.call_count == 0
    assert analysis.fallback_reason == capability_config._VISION_DISABLED


def test_the_web_analyze_endpoint_returns_local_metadata_without_cloud_authorization(tmp_path, monkeypatch):
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    response = TestClient(server.app).post("/api/analyze", json={
        "material_path": str(local_pack(tmp_path)), "workspace_path": str(tmp_path / "workspace"),
        "product_form": {"product_category": "箱包"}})
    assert response.status_code == 200
    data = response.json()["ai_analysis"]
    assert data["source"] == "llm" and data["execution_location"] == "local"
    assert data["analyzed_image"] == "商品图片/01_主图.png"
    assert len(local_calls(transport)) == 1 and cloud_calls(transport) == []
    assert_no_secret(response.text)
    assert str(tmp_path) not in response.text


def test_a_history_pack_without_the_new_keys_is_shown_as_unlabelled():
    """旧历史里没有这两个元数据键：默认 unknown，页面据此显示「未标注位置」而不是「云端」。"""
    legacy = AIProductAnalysis(**{"source": "llm", "model": "vision-model",
                                  "category_positioning": "旧结果"})
    assert legacy.execution_location == "unknown" and legacy.analyzed_image == ""
    assert json.loads(json.dumps(legacy.to_dict(), ensure_ascii=False))["execution_location"] == "unknown"


# --- 8. 过渡与不回归（§九.11）---

def test_v1_transitional_path_still_reports_unknown_location(monkeypatch, tmp_path):
    write_v1()
    loader = Mock(return_value=llm_config.LLMConfig(LEGACY_KEY, "https://legacy.invalid/v1",
                                                    "legacy-model", 30, 4))
    monkeypatch.setattr(ai_product_analyzer, "load_llm_config", loader)
    reply = json.dumps(valid_reply(), ensure_ascii=False)
    factory, transport = record_http(monkeypatch, completion_response(reply))
    analysis = analyze(local_pack(tmp_path))
    assert loader.call_count == 1
    assert len(cloud_calls(transport)) == 1 and local_calls(transport) == []
    assert analysis.source == "llm"
    assert analysis.execution_location == "unknown", "v1 过渡配置证明不了自己跑在哪"


def test_a_loopback_cloud_vision_entry_is_not_advertised_as_remote(monkeypatch, tmp_path):
    """v2 声明云端但地址其实是本机回环的兼容服务：位置只能说未标注，不能宣传成远端执行。"""
    enable_cloud_vision(base_url="http://127.0.0.1:11434/v1")
    reply = json.dumps(valid_reply(), ensure_ascii=False)
    factory, transport = record_http(monkeypatch, completion_response(reply))
    analysis = analyze(local_pack(tmp_path))
    assert analysis.source == "llm" and analysis.execution_location == "unknown"


def test_local_config_never_reaches_the_v1_loader(monkeypatch, tmp_path):
    write_v1()
    enable_local_vision()
    loader = Mock(side_effect=AssertionError("v2 存在时不得加载 v1 配置"))
    monkeypatch.setattr(ai_product_analyzer, "load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    assert analyze(local_pack(tmp_path)).source == "llm"
    loader.assert_not_called()
    assert cloud_calls(transport) == []


def test_the_local_flow_leaves_the_text_capability_config_alone(monkeypatch, tmp_path):
    enable_text()
    before = json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["text"]
    enable_local_vision()
    factory, transport = record_http(monkeypatch)
    assert analyze(local_pack(tmp_path)).source == "llm"
    assert json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["text"] == before
    assert cloud_calls(transport) == []


# --- 9. 网络守卫本身会响（§九.13）---

def test_the_offline_guard_really_blocks_a_loopback_ollama_attempt():
    """故意让它响一次：证明前面那些「0 次 / 1 次」是拦截出来的，不是环境本来就通不了。

    守卫在真正 send 之前抛出，所以不会创建 socket，也就不可能碰到真实的 Ollama 端口。
    """
    with pytest.raises(AssertionError, match="actual network is forbidden"):
        with httpx.Client(trust_env=False) as client:
            client.post("http://127.0.0.1:11434/api/chat", json={})


def test_the_offline_guard_is_not_loopback_only():
    with pytest.raises(AssertionError, match="actual network is forbidden"):
        with httpx.Client(trust_env=False) as client:
            client.post("http://example.invalid/api/chat", json={})


def test_the_guard_still_lets_the_same_origin_test_client_through():
    """对照组：守卫不是一律抛错，否则上面两条的「响」说明不了任何事。"""
    assert TestClient(server.app).get("/api/ai-capabilities").status_code == 200


# --- 10. 已知缺口：这一元数据目前会随整份 ai_analysis 进入云端文案请求（§八）---

def test_known_gap_the_relative_image_name_rides_into_the_cloud_copy_payload(tmp_path):
    """钉住今天的事实，不是认可它：修复点在 llm_content_generator.py，超出本批允许改动的文件。

    要收口需要扩大的范围就是那一个文件里的一处取值——把 execution_location / analyzed_image
    两个元数据键从云端文案请求的上下文里剔掉。本批不偷改未授权文件，所以把这条留在测试面上；
    修好的那天应当把它反转成「不在 payload 里」。
    """
    directions, understanding, extraction = _reports(_build_pack(tmp_path))
    pack = build_content_package(directions, understanding, extraction)
    analysis = AIProductAnalysis(
        source="llm", product_name="苔绿托特包", category_positioning="通勤大容量",
        execution_location="local", analyzed_image="商品图片/01_主图.png").to_dict()
    payload = _build_context_payload(
        direction=directions.directions[0], understanding=understanding,
        product_evidence="本地证据摘要", ai_analysis=analysis,
        template_items={item.channel: item for item in pack.items})
    dumped = json.dumps(payload, ensure_ascii=False)
    assert "商品图片/01_主图.png" in dumped, "本批未修复：元数据仍会整份进入云端文案请求"


# --- 11. 第三批改动守卫：废模型名残留 + 前端等待档位 ---

# 拆成两段拼接：写成完整字面量会让这条守卫扫到自己这一行。
DEAD_MODEL_FRAGMENT = "qwen3" + ".5:2b"
_SOURCE_SUFFIXES = {".py", ".js", ".html", ".css", ".md", ".txt", ".bat", ".json"}
_EXCLUDED_DIRS = {"__pycache__", ".venv", "node_modules", ".git", "workspace"}
from app.demo_paths import REPO_ROOT as _REPO_ROOT


def _collect_source_files(roots):
    """后缀白名单 + 目录段排除；排除只看「相对各自 root 的路径段」，不看绝对路径。"""
    collected = []
    for root in roots:
        for path in sorted(root.rglob("*")):
            if not path.is_file() or path.suffix.lower() not in _SOURCE_SUFFIXES:
                continue
            if _EXCLUDED_DIRS.intersection(path.relative_to(root).parts[:-1]):
                continue
            collected.append(path)
    return collected


def _files_containing(files, needle):
    hits = []
    for path in files:
        try:
            if needle in path.read_text(encoding="utf-8"):
                hits.append(path)
        except (OSError, UnicodeDecodeError):
            continue
    return hits


def test_shipped_source_has_no_dead_model_name_left():
    """那个模型名对应的模型已从本机 Ollama 消失（真发 /api/chat 回 404），源码里留着只会把人引回静默降级。

    实测扫描面：app/ + tests/ + scripts/ 三个目录，按后缀白名单收集到 104 个文件。
    根目录的历史交接 md 仍写着旧名，那是当时真发生过的记录，故意不改，所以扫描面也不含它们。
    """
    files = _collect_source_files((_REPO_ROOT / "app", _REPO_ROOT / "dev" / "tests", _REPO_ROOT / "dev" / "scripts"))
    assert len(files) >= 100, f"只收集到 {len(files)} 个文件，这一轮的 0 命中可能只是扫了个空目录"
    scanned = {path.relative_to(_REPO_ROOT).as_posix() for path in files}
    for expected in ("app/web/static/app.js", "app/core/llm_config.py",
                     "app/core/local_vision_client.py", "tests/test_local_vision.py",
                     "tests/test_ai_vision_config_selection.py"):
        assert expected in scanned, f"{expected} 没进扫描面，遍历那层没工作"
    assert _files_containing(files, DEAD_MODEL_FRAGMENT) == []


def test_the_dead_name_scanner_is_not_vacuous(tmp_path):
    """故意让它响一次：证明上一条的「0 命中」是扫描真起了作用，不是判据写窄了或目录没进去。

    一个嵌套子目录里的真命中 + 一个 __pycache__ 里的干扰命中 + 一个合法模型名做对照。
    """
    (tmp_path / "app" / "core").mkdir(parents=True)
    (tmp_path / "app" / "__pycache__").mkdir()
    (tmp_path / "tests").mkdir()
    culprit = tmp_path / "app" / "core" / "s.py"
    culprit.write_text(f'LOCAL_MODEL = "{DEAD_MODEL_FRAGMENT}"\n', encoding="utf-8")
    noise = tmp_path / "app" / "__pycache__" / "s.py"
    noise.write_text(DEAD_MODEL_FRAGMENT, encoding="utf-8")
    legit = tmp_path / "tests" / "ok.py"
    legit.write_text('LOCAL_MODEL = "qwen3-vl:2b"\n', encoding="utf-8")

    files = _collect_source_files((tmp_path / "app", tmp_path / "tests"))
    assert culprit in files and legit in files, "子目录遍历没走进去"
    assert noise not in files, "__pycache__ 排除规则失效，守卫会误伤编译产物"
    assert _files_containing(files, DEAD_MODEL_FRAGMENT) == [culprit]


def test_frontend_waits_longer_only_for_the_named_model_endpoints():
    """浏览器侧默认仍是 120 秒，只有点名在下面这个元组里的接口拿到放宽档位。

    上限从 AISettingsRequest 的 Pydantic 约束现取，不在这里抄一个 600：后端一旦改动上界，
    这条会跟着变，界面输入框的 max 也由同一个数字钉住。
    """
    app_js = (_REPO_ROOT / "app" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    constraints = server.AISettingsRequest.model_fields["timeout_seconds"].metadata
    ceiling = max(getattr(rule, "le", 0) for rule in constraints)

    declared = re.search(r"const MODEL_HEAVY_TIMEOUT_MS\s*=\s*(\d+)\s*;", app_js)
    assert declared, "前端放宽档位的那个常量不见了"
    budget_ms = int(declared.group(1))
    assert ceiling * 1000 < budget_ms <= (ceiling + 120) * 1000, (
        f"前端 {budget_ms} 毫秒与后端上限 {ceiling} 秒的余量不在 0–120 秒之间")

    # 放宽范围只认这个元组：想再多放宽一个接口，必须先往这里加一行；不加就下面的数字也不会跟着漂。
    heavy_routes = ("/api/analyze", "/api/regenerate-directions", "/api/publish", "/api/regenerate-content")
    call_sites = list(re.finditer(r'await api\("(/api/[^"]+)"', app_js))
    heavy = [m for m in call_sites if m.group(1) in heavy_routes]
    assert len(heavy) == len(heavy_routes), (
        f"白名单接口的前端调用点应有 {len(heavy_routes)} 处，实测 {len(heavy)} 处：{[m.group(1) for m in heavy]}")
    for match in heavy:
        tail = app_js[match.start():app_js.index(");", match.end()) + 2]
        assert "MODEL_HEAVY_TIMEOUT_MS" in tail, f"{match.group(1)} 的前端调用没拿到放宽档位"

    # 1 处声明 + 白名单里每个接口一处调用点：数字由元组长度推导，不再抄一个字的字面量。
    assert app_js.count("MODEL_HEAVY_TIMEOUT_MS") == 1 + len(heavy_routes)

    assert re.search(r"async function api\(path, options = \{\}, timeoutMs = 120000\)", app_js), (
        "api() 的默认等待档位不再是 120 秒：其余接口被一起放宽了")


def test_timeout_input_range_still_matches_the_backend_ceiling():
    """界面上那句「1–600」和输入框的 max 必须跟后端真实上界一致，不能显示一个存不进配置的范围。"""
    index_html = (_REPO_ROOT / "app" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    constraints = server.AISettingsRequest.model_fields["timeout_seconds"].metadata
    ceiling = max(getattr(rule, "le", 0) for rule in constraints)
    floor = min(getattr(rule, "ge", ceiling) for rule in constraints)
    assert f'max="{int(ceiling)}"' in index_html, "超时输入框的 max 对不上后端上界"
    assert f"超时（秒，{int(floor)}–{int(ceiling)}）" in index_html, "界面上的范围文字对不上后端上界"


def test_loading_clock_starts_and_always_stops():
    """等待中的秒表只放数字不配文案；计时器必须起在 showLoading、清在 hideLoading，否则换页面后还在跑。

    遮罩只经这两个函数开关（app.js 里 9 个等待流程全部走它们，没有绕过直接切 active 的代码），
    所以起停配对是这一行字能否可信的全部条件。
    """
    app_js = (_REPO_ROOT / "app" / "web" / "static" / "app.js").read_text(encoding="utf-8")
    index_html = (_REPO_ROOT / "app" / "web" / "static" / "index.html").read_text(encoding="utf-8")
    css = (_REPO_ROOT / "app" / "web" / "static" / "app-overrides.css").read_text(encoding="utf-8")

    assert 'id="loadingElapsed"' in index_html and 'id="sidebarTiming"' in index_html
    show_body = re.search(r"function showLoading\(text, subtext\) \{.*?\n\}", app_js, re.S)
    hide_body = re.search(r"function hideLoading\(\) \{.*?\n\}", app_js, re.S)
    assert show_body and hide_body, "showLoading / hideLoading 的形状变了，这条守卫要看的是这两段"
    assert "loadingElapsedTimer = setInterval(" in show_body.group(0)
    assert "clearInterval(loadingElapsedTimer)" in hide_body.group(0)
    assert hide_body.group(0).index("clearInterval") < hide_body.group(0).index("remove(\"active\")"), (
        "hideLoading 先关遮罩再清计时器：中间那段时间数字还在跳")

    # 只有真会发本机识图请求的两个流程报总用时。
    assert app_js.count("showElapsedSince(loadingStartedAt,") == 2

    assert "min-height" in re.search(r"\.loading-elapsed \{[^}]*\}", css).group(0), (
        "秒表没有预留高度：数字一出现就把下面那行顶下去")


def test_timing_line_is_readable_on_desktop_and_hidden_on_narrow_screens():
    """900px 断点把侧栏压成 72px 的图标列（app-overrides.css:1642），那行总用时会被挤成一竖条，所以在断点里收起它。
     640px 以下另有既有规则连 .sidebar-footer 整块隐藏，不靠这条。

    判据靠缩进＋位置：顶格那条是常态规则，缩进两格的那条必须在 900px 断点块里。
    不能按「整份 CSS 里有几个 @media (max-width: 900px)」去切——实测这份文件里有三处，split 会从错的那处开始。
    """
    css = (_REPO_ROOT / "app" / "web" / "static" / "app-overrides.css").read_text(encoding="utf-8")
    assert len(re.findall(r"@media \(max-width: 900px\)", css)) == 3, "断点数量变了，这条判据要跟着改"
    base = re.search(r"^\.sidebar-timing \{([^}]*)\}", css, re.M)
    assert base and "font-size" in base.group(1), "常态下这行没有自己的字号"
    assert "display: none" not in base.group(1), "常态规则里不许直接隐藏"

    narrow = re.search(r"^  \.sidebar-timing \{([^}]*)\}", css, re.M)
    assert narrow and "display: none" in narrow.group(1), "缩进两格的那条没把它收起"
    assert css.rfind("@media (max-width: 900px)", 0, narrow.start()) != -1, "收起那条不在 900px 断点里"
    assert css.find("@media (max-width: 640px)", 0, narrow.start()) == -1, "它落在 640px 断点之后，判据会看错块"
    assert css.count(".sidebar-timing") == 2, "这行样式出现了第三种规则，先后顺序会打架"


def test_the_stale_seconds_claim_is_gone_from_shipped_source():
    """静态 HTML 里那句写死"预计需要…秒"的话对本机识图彻底不成立（实测中位 24.84 秒、冷启动 85.49 秒）。

    判据必须拼接：连写两次都命中过——第一次命中这行 assert，第二次命中本段说明文字。
    那两次失败恰好证明扫描面真打开了这个文件，不是扫了个空。
    """
    stale = "预计需要 " + "5-10 秒"
    files = _collect_source_files((_REPO_ROOT / "app", _REPO_ROOT / "dev" / "tests", _REPO_ROOT / "dev" / "scripts"))
    assert _files_containing(files, stale) == []
