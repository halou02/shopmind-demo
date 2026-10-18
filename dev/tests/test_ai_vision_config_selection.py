"""商品理解（图片理解）链路「v2 存在时 v2 vision 是唯一来源」的开发者测试。

只用假凭据 + pytest 临时目录 + 假图片 + mock transport：不读真实私有配置、不调用真实
模型、不发任何网络请求、不碰仓库里的真实素材包（下面的 pack 都是本文件在 tmp_path 里
现造的）。降级断言出自真实商品理解入口 `analyze_product` 与 `/api/analyze`，而不是只调
`vision_request_config` 就宣称链路已验证。

计数用的是「尝试构造外部 HTTP 客户端 / 发出 POST」的次数，Mock 只记录不抛错：
否则一旦误回落 v1，请求异常会被 except 吞掉，看起来同样是降级，就测不出来了。
"""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core import ai_product_analyzer, capability_config, llm_config
from app.core.ai_product_analyzer import (
    AIProductAnalysis,
    analyze_product,
    read_text_materials,
)
from app.core.capability_config import CapabilityConfigError, vision_request_config
from app.core.evidence_extractor import extract_evidence_from_index
from app.core.llm_client import build_vision_messages, encode_image_data_url
from app.core.llm_config import LLMConfig, LocalVisionConfig, cloud_authorization
from app.core.source_scanner import scan_library
from app.web import server
from tests.test_content_generator import _build_pack

VISION_KEY = "vision-capability-key-998877"
TEXT_KEY = "text-capability-key-112233"
IMAGE_KEY = "image-capability-key-223344"
LEGACY_KEY = "legacy-v1-key-445566"
ENV_KEY = "fake-environment-key-must-not-be-used-778899"
TAILS = ("998877", "112233", "223344", "445566", "778899")

VISION_BASE = "https://vision.invalid/v1"
TEXT_BASE = "https://text.invalid/v1"
IMAGE_BASE = "https://image.invalid/v1"
V1_BASE = "https://legacy.invalid/v1"
# 本机图片理解只接受回环地址，所以这里的假地址也用 127.0.0.1；测试不发真实请求。
LOCAL_VISION_BASE = "http://127.0.0.1:11434"
# 非本机 loopback 的 http 地址：validate_base_url 只放行 https 或 http 回环，所以这条落盘即坏。
BAD_VISION_BASE = "http://vision-public.invalid/v1"

CLOUD_REPLY = {"product_name": "云端识别商品名", "category_positioning": "云端品类定位",
               "product_facts": {"颜色": "云苔绿"}}


def v2_path() -> Path:
    return capability_config.capability_config_path()


def assert_no_secret(payload: str) -> None:
    for tail in TAILS:
        assert tail not in payload, f"输出里出现了密钥片段：{tail}"


def enable_vision(**changes) -> dict:
    payload = {"enabled": True, "execution_location": "cloud", "adapter": "openai_chat",
               "base_url": VISION_BASE, "model": "vision-model", "timeout_seconds": 45,
               "max_images": 2, "credential_action": "replace", "api_key": VISION_KEY}
    payload.update(changes)
    return capability_config.update_capability_settings("vision", payload)


def enable_local_vision(**changes) -> dict:
    """本机图片理解的合法启用形状：ollama_chat + 回环地址 + 单次 1 张 + 不带密钥。"""
    payload = {"enabled": True, "execution_location": "local", "adapter": "ollama_chat",
               "base_url": LOCAL_VISION_BASE, "model": "qwen3-vl:2b", "timeout_seconds": 45,
               "max_images": 1, "credential_action": "keep"}
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
        "base_url": IMAGE_BASE, "model": "image-model", "timeout_seconds": 45,
        "credential_action": "replace", "api_key": IMAGE_KEY})


def write_v1(*, base_url: str = V1_BASE, model: str = "legacy-model",
             timeout: float = 30, max_images: int = 4, api_key: str = LEGACY_KEY) -> None:
    llm_config.save_llm_settings(base_url=base_url, model=model, timeout_seconds=timeout,
                                 max_images=max_images, api_key=api_key)


def vision_settings_with_a_broken_vision_entry(*, drop: str | None = None,
                                               **vision_changes) -> str:
    """整份结构合法、只有 vision 那一格坏掉的 v2 文本，密钥就埋在坏的那格里。"""
    entries = {name: llm_config._default_capability_entry(name) for name in llm_config.CAPABILITY_NAMES}
    broken = dict(entries["vision"], enabled=True, adapter="openai_chat", base_url=VISION_BASE,
                  model="vision-model", timeout_seconds=45, max_images=2, api_key=VISION_KEY)
    broken.update(vision_changes)
    if drop is not None:
        broken.pop(drop, None)
    entries["vision"] = broken
    return json.dumps({"schema_version": 2, "capabilities": entries})


def unconfirmed_vision_settings(**vision_changes) -> dict:
    settings = llm_config.migrate_legacy_settings({"api_key": LEGACY_KEY, "base_url": V1_BASE,
                                                   "model": "legacy-model", "timeout_seconds": 30,
                                                   "max_images": 4})
    entry = dict(llm_config._default_capability_entry("vision"), enabled=True, adapter="openai_chat",
                 base_url=VISION_BASE, model="vision-model", timeout_seconds=45, max_images=2,
                 api_key=VISION_KEY, needs_confirmation=True)
    entry.update(vision_changes)
    settings["capabilities"]["vision"] = entry
    return settings


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


def exploding_loader() -> Mock:
    return Mock(side_effect=AssertionError("v2 文件存在时不得加载 v1 配置"))


def sent_images(transport) -> list[str]:
    body = transport.post.call_args.kwargs["json"]
    urls: list[str] = []
    for message in body["messages"]:
        content = message.get("content")
        if isinstance(content, list):
            urls.extend(part["image_url"]["url"] for part in content
                        if part.get("type") == "image_url")
    return urls


def image_parts(messages: list[dict]) -> int:
    return sum(1 for part in messages[1]["content"] if part["type"] == "image_url")


def analyze_pack(pack: Path, product_form: dict | None = None, config: LLMConfig | None = None):
    scan_report = scan_library(pack)
    index = scan_report.index()
    extraction = extract_evidence_from_index(index, scan_report.library_root)
    return analyze_product(material_root=pack, index=index,
                           product_form=product_form or {"product_category": "箱包"},
                           extraction=extraction, config=config)


def pack_images(pack: Path) -> list[Path]:
    return sorted((pack / "商品图片").glob("*.png"))


def text_only_pack(tmp_path: Path) -> Path:
    """没有图片、只有可读文本资料的素材包：走的是商品理解里的文字降级那一支。"""
    pack = tmp_path / "textpack"
    docs = pack / "参考资料"
    docs.mkdir(parents=True)
    (docs / "brief.txt").write_text("材质：尼龙面料\n卖点：大容量通勤", encoding="utf-8")
    return pack


def mixed_pack(tmp_path: Path) -> Path:
    """同时含商品图片与可读文字资料的素材包，失败路径用例都从这里进入。

    文字资料必须确实在包里，否则「图片失败后不追加文字请求」这条断言会恒绿：
    旧行为追加的那一发请求，前提就是还有文字可读。
    """
    pack = _build_pack(tmp_path)
    assert pack_images(pack), "素材包必须含商品图片"
    assert read_text_materials(pack), "素材包必须含可读文字资料"
    return pack


def completion_response(content: str, *, status_code: int = 200) -> Mock:
    """假的 chat completion 响应，content 由用例指定（可以是非法 JSON 原文）。"""
    return Mock(status_code=status_code, text=content,
                json=Mock(return_value={"choices": [{"message": {"content": content}}]}))


def branch_spy(monkeypatch, name: str, marker: str) -> Mock:
    """把分析器内部另一条请求分支换成只记录的假分支。

    它返回一个 source="llm" 的假结果，所以旧行为一旦追加，除了调用次数变 1，
    结果来源也会从 offline 变成 llm——两条断言互相独立，不会同 falsely 绿。
    """
    spy = Mock(return_value=AIProductAnalysis(source="llm", product_name=marker, model=marker))
    monkeypatch.setattr(f"app.core.ai_product_analyzer.{name}", spy)
    return spy


def assert_single_attempt_offline(analysis, factory, transport, unused_branch) -> str:
    """失败路径共用的判定面：恰好一次尝试、第二条分支没被走、结果落回离线。"""
    assert factory.call_count == 1, "一次商品理解最多构造一个 HTTP 客户端"
    assert transport.post.call_count == 1, "一次商品理解最多发出一次 POST 尝试"
    assert unused_branch.call_count == 0, "同一次商品理解里不得出现第二条请求分支"
    assert analysis.source == "offline" and analysis.model == ""
    assert analysis.fallback_reason == "云端分析未成功或资料不足，已降级到本地规则"
    rendered = json.dumps(analysis.to_dict(), ensure_ascii=False)
    assert_no_secret(rendered)
    return rendered


# --- 1. v2 不存在：迁移前的旧行为，vision 仍按 v1 商品理解配置运行 ---

def test_without_a_v2_file_the_analyzer_still_uses_v1(tmp_path, monkeypatch):
    # 这是「v2 文件不存在时的旧行为」，不代表 vision 已完成 v2 迁移。
    write_v1(max_images=4)
    loader = Mock(return_value=LLMConfig(LEGACY_KEY, V1_BASE, "legacy-model", 30, 4))
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    pack = _build_pack(tmp_path)
    with cloud_authorization(True):
        analysis = analyze_pack(pack)
    assert not v2_path().exists(), "选配置这一步不许创建 v2 文件，也不许把 v1 数据写进 vision"
    assert loader.call_count == 1
    assert factory.call_count == 1 and transport.post.call_count == 1
    assert transport.post.call_args.args[0] == f"{V1_BASE}/chat/completions"
    assert transport.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {LEGACY_KEY}"
    assert transport.post.call_args.kwargs["json"]["model"] == "legacy-model"
    assert len(sent_images(transport)) == 4, "旧行为下图片张数仍由 v1 的 max_images 决定"
    assert analysis.source == "llm"


def test_without_a_v2_file_and_no_v1_key_the_analysis_stays_offline(tmp_path, monkeypatch):
    # 迁移前的另一半行为：v1 里没有密钥时照旧落回离线规则，一次请求都不发。
    loader = Mock(return_value=LLMConfig(None, V1_BASE, "legacy-model", 30, 4))
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analysis = analyze_pack(_build_pack(tmp_path))
    assert loader.call_count == 1
    assert factory.call_count == 0 and transport.post.call_count == 0
    assert analysis.source == "offline"
    assert analysis.fallback_reason == "缺少 API Key，使用本地规则"
    assert not v2_path().exists()


# --- 2. v2 存在但 vision 禁用：图片和文字模型请求都不发，且不读 v1 ---

def test_disabled_vision_sends_no_request_and_does_not_read_v1(tmp_path, monkeypatch):
    write_v1()
    enable_text()  # 只有 text 被启用，vision 保持默认禁用
    assert json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["vision"]["enabled"] is False
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analysis = analyze_pack(_build_pack(tmp_path))
    assert analysis.source == "offline"
    assert analysis.fallback_reason == capability_config._VISION_DISABLED
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()
    rendered = json.dumps(analysis.to_dict(), ensure_ascii=False)
    assert_no_secret(rendered)
    assert "环境变量" not in analysis.fallback_reason and "AIPC" not in analysis.fallback_reason


def test_disabled_vision_blocks_the_product_understanding_text_fallback(tmp_path, monkeypatch):
    pack = text_only_pack(tmp_path)
    write_v1()
    enable_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analysis = analyze_pack(pack)
    assert factory.call_count == 0 and transport.post.call_count == 0
    assert analysis.source == "offline"
    assert analysis.fallback_reason == capability_config._VISION_DISABLED
    loader.assert_not_called()


def test_the_text_fallback_runs_on_the_vision_config_once_vision_is_enabled(tmp_path, monkeypatch):
    """上一条的正向对照：同一份无图片素材，vision 启用后文字那一支确实会发一次请求。"""
    pack = text_only_pack(tmp_path)
    enable_vision()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    invented = branch_spy(monkeypatch, "_analyze_with_vision", "图片分支被凭空追加执行")
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analysis = analyze_pack(pack)
    assert factory.call_count == 1 and transport.post.call_count == 1
    assert invented.call_count == 0, "无图片时不许凭空发一次图片请求"
    assert transport.post.call_args.args[0] == f"{VISION_BASE}/chat/completions"
    assert transport.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {VISION_KEY}"
    assert transport.post.call_args.kwargs["json"]["model"] == "vision-model"
    assert sent_images(transport) == []
    assert analysis.source == "llm" and analysis.model == "vision-model"
    loader.assert_not_called()


# --- 3. v2 vision 待确认：即使 enabled=True 也不许调用 ---

def test_unconfirmed_vision_stops_even_when_enabled(tmp_path, monkeypatch):
    write_v1()
    capability_config.write_capability_settings(unconfirmed_vision_settings())
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["vision"]
    assert on_disk["enabled"] is True and on_disk["needs_confirmation"] is True
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    for name in ("AIPC_LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(name, ENV_KEY)
    with cloud_authorization(True):
        analysis = analyze_pack(_build_pack(tmp_path))
    assert analysis.source == "offline"
    assert analysis.fallback_reason == capability_config._VISION_NEEDS_CONFIRMATION
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()
    rendered = json.dumps(analysis.to_dict(), ensure_ascii=False)
    assert_no_secret(rendered)
    assert ENV_KEY not in rendered


def test_disabled_and_unconfirmed_together_reports_the_disabled_reason(tmp_path, monkeypatch):
    """钉住判定顺序：先 enabled 再 needs_confirmation，两种状态都停，只是文案不同。"""
    write_v1()
    capability_config.write_capability_settings(unconfirmed_vision_settings(enabled=False))
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["vision"]
    assert on_disk["enabled"] is False and on_disk["needs_confirmation"] is True
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analysis = analyze_pack(_build_pack(tmp_path))
    assert factory.call_count == 0 and transport.post.call_count == 0
    assert analysis.fallback_reason == capability_config._VISION_DISABLED
    loader.assert_not_called()


# --- 4. v2 vision 选本地执行位置：已接入，但一张合格图片都没有时模型请求数为 0 ---

def test_local_vision_without_a_decodable_image_sends_zero_requests(tmp_path, monkeypatch):
    """本机分支已接通，这一组仍然只证明「不回落云端」：_build_pack 里的假字节解不开。

    b"png" 这类占位字节不是可解码的图片，所以发送前的过滤会把它们全部筛掉，
    一次 HTTP 客户端都不会被构造——请求数为 0 是实测数字，不是推论。
    """
    write_v1()
    enable_local_vision()
    on_disk = json.loads(v2_path().read_text(encoding="utf-8"))["capabilities"]["vision"]
    assert on_disk["execution_location"] == "local" and on_disk["api_key"] is None
    assert on_disk["adapter"] == "ollama_chat" and on_disk["max_images"] == 1
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    # 一个工厂同时挂在云端与本机上（两处 httpx 是同一个模块对象，分不开），所以 call_count 为 0
    # 就同时覆盖了两条支：既没有云端请求，也没有本机请求。
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analysis = analyze_pack(_build_pack(tmp_path))
    assert analysis.source == "offline"
    assert analysis.fallback_reason == ai_product_analyzer._LOCAL_NO_VALID_IMAGE
    assert "本机" in analysis.fallback_reason and "云端" not in analysis.fallback_reason
    assert analysis.execution_location == "local"
    assert analysis.analyzed_image == ""
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()
    assert_no_secret(json.dumps(analysis.to_dict(), ensure_ascii=False))


# --- 5. v2 坏文件：不发请求、不读 v1、不修不删、错误脱敏 ---

@pytest.mark.parametrize("payload", [
    "{ not json",
    "",
    '{"schema_version": 1, "capabilities": {}}',
    '{"schema_version": 3, "capabilities": {}}',
    json.dumps({"schema_version": 2, "capabilities": {
        name: llm_config._default_capability_entry(name) for name in llm_config.CAPABILITY_NAMES},
        "capabilities_extra": VISION_KEY}),
    vision_settings_with_a_broken_vision_entry(max_images="2"),
    vision_settings_with_a_broken_vision_entry(timeout_seconds=0),
    vision_settings_with_a_broken_vision_entry(surprise=VISION_KEY),
    vision_settings_with_a_broken_vision_entry(drop="model"),
    vision_settings_with_a_broken_vision_entry(base_url=BAD_VISION_BASE),
])
def test_unreadable_v2_stops_the_analyzer_without_repair(tmp_path, monkeypatch, payload):
    write_v1()
    v1_before = llm_config.CONFIG_PATH.read_bytes()
    v2_path().write_text(payload, encoding="utf-8")
    v2_before = v2_path().read_bytes()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    for name in ("AIPC_LLM_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY"):
        monkeypatch.setenv(name, ENV_KEY)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analysis = analyze_pack(_build_pack(tmp_path))
    assert analysis.source == "offline"
    assert analysis.fallback_reason == capability_config._UNREADABLE
    assert factory.call_count == 0 and transport.post.call_count == 0
    loader.assert_not_called()
    assert v2_path().read_bytes() == v2_before, "坏文件必须原样保留，不许修复、删除或覆盖"
    assert llm_config.CONFIG_PATH.read_bytes() == v1_before
    reason = analysis.fallback_reason
    assert_no_secret(reason)
    assert VISION_BASE not in reason and "surprise" not in reason and ENV_KEY not in reason
    # 坏配置的原因只能是那条固定脱敏文案：地址、字段名、校验原文、文件名都不许出现在里面。
    assert reason == capability_config._UNREADABLE
    assert BAD_VISION_BASE not in reason and "loopback" not in reason
    assert "缺少字段" not in reason and "ValueError" not in reason and "Traceback" not in reason
    assert ".private.json" not in reason and str(v2_path()) not in reason
    rendered = json.dumps(analysis.to_dict(), ensure_ascii=False)
    assert_no_secret(rendered)
    assert VISION_BASE not in rendered and BAD_VISION_BASE not in rendered
    assert "缺少字段" not in rendered and "loopback" not in rendered


# --- 6. v2 vision 有效：请求的五个字段全部来自 vision 自己那一格 ---

def test_confirmed_vision_sends_the_v2_url_key_model_and_timeout(tmp_path, monkeypatch):
    write_v1()
    enable_text()
    enable_image()
    enable_vision(timeout_seconds=7)
    monkeypatch.setenv("AIPC_IMAGE_MODEL", "env-image-model-must-not-be-used")
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    text_gate = Mock(side_effect=AssertionError("vision 请求不得经过 text 取值规则"))
    monkeypatch.setattr("app.core.llm_client.text_request_config", text_gate)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analysis = analyze_pack(_build_pack(tmp_path))
    assert factory.call_count == 1 and transport.post.call_count == 1
    url = transport.post.call_args.args[0]
    kwargs = transport.post.call_args.kwargs
    assert url == f"{VISION_BASE}/chat/completions"
    assert kwargs["headers"]["Authorization"] == f"Bearer {VISION_KEY}"
    assert kwargs["json"]["model"] == "vision-model"
    assert factory.call_args.kwargs["timeout"] == 7
    assert analysis.source == "llm" and analysis.model == "vision-model"
    text_gate.assert_not_called()
    loader.assert_not_called()
    request = json.dumps([url, kwargs["headers"], kwargs["json"]], ensure_ascii=False)
    for secret in (LEGACY_KEY, TEXT_KEY, IMAGE_KEY):
        assert secret not in request
    for base in (V1_BASE, TEXT_BASE, IMAGE_BASE):
        assert base not in request
    for model in ("legacy-model", "text-model", "image-model", "env-image-model-must-not-be-used"):
        assert model not in request


def test_vision_max_images_truncates_the_images_actually_sent(tmp_path, monkeypatch):
    enable_vision(max_images=2)
    pack = _build_pack(tmp_path)
    images = pack_images(pack)
    assert len(images) == 4
    bytes_before = {path: path.read_bytes() for path in images}
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analyze_pack(pack)
    urls = sent_images(transport)
    assert len(urls) == 2
    assert urls == [encode_image_data_url(path) for path in images[:2]]
    for path in images[2:]:
        assert encode_image_data_url(path) not in urls, f"第 {images.index(path) + 1} 张图不该出现在请求里"
    assert {path: path.read_bytes() for path in images} == bytes_before, "不许修改原始图片文件"
    assert transport.post.call_count == 1


def test_the_image_limit_comes_from_vision_not_from_v1_text_or_environment(tmp_path, monkeypatch):
    write_v1(max_images=4)
    enable_text()
    enable_vision(max_images=1)
    monkeypatch.setenv("AIPC_LLM_MAX_IMAGES", "6")
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analyze_pack(_build_pack(tmp_path))
    assert len(sent_images(transport)) == 1
    assert factory.call_count == 1
    loader.assert_not_called()


# --- 7. 本次请求授权：配置启用不等于允许外发（走真实 /api/analyze）---

def analyze_body(tmp_path, pack, *, authorized: bool) -> dict:
    body = {"material_path": str(pack), "workspace_path": str(tmp_path / "workspace"),
            "product_form": {"product_category": "箱包", "product_desc": "绿色，容量大"}}
    if authorized:
        body["cloud_authorized"] = True
    return body


def test_authorized_web_analyze_sends_one_request_to_the_vision_address(tmp_path, monkeypatch):
    client = TestClient(server.app)
    write_v1()
    enable_vision()
    factory, transport = record_http(monkeypatch)
    response = client.post("/api/analyze", json=analyze_body(tmp_path, _build_pack(tmp_path),
                                                            authorized=True))
    assert response.status_code == 200
    data = response.json()
    assert factory.call_count == 1 and transport.post.call_count == 1
    assert transport.post.call_args.args[0] == f"{VISION_BASE}/chat/completions"
    assert data["ok"] and data["ai_analysis"]["source"] == "llm"
    assert data["ai_analysis"]["model"] == "vision-model"
    assert_no_secret(response.text)
    assert LEGACY_KEY not in response.text


def test_unauthorized_web_analyze_makes_zero_calls_with_enabled_vision(tmp_path, monkeypatch):
    client = TestClient(server.app)
    write_v1()
    enable_vision()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    factory, transport = record_http(monkeypatch)
    response = client.post("/api/analyze", json=analyze_body(tmp_path, _build_pack(tmp_path),
                                                            authorized=False))
    assert response.status_code == 200
    data = response.json()
    assert factory.call_count == 0 and transport.post.call_count == 0
    assert data["ai_analysis"]["source"] == "offline"
    assert "未授权" in data["ai_analysis"]["fallback_reason"]
    loader.assert_not_called()


def test_a_later_unauthorized_analyze_does_not_reuse_the_previous_authorization(tmp_path, monkeypatch):
    client = TestClient(server.app)
    write_v1()
    enable_vision()
    pack = _build_pack(tmp_path)
    factory, transport = record_http(monkeypatch)
    assert client.post("/api/analyze", json=analyze_body(tmp_path, pack, authorized=True)).status_code == 200
    assert transport.post.call_count == 1
    assert client.post("/api/analyze", json=analyze_body(tmp_path, pack, authorized=False)).status_code == 200
    assert transport.post.call_count == 1, "第二次未授权的请求不得再外发"
    assert factory.call_count == 1


# --- 8. vision 取值规则本身（单元级，与 text 的同源口径对齐）---

def test_absent_v2_keeps_the_v1_vision_behavior(tmp_path):
    loader = Mock(return_value=LLMConfig(LEGACY_KEY, V1_BASE, "legacy-model", 30, 4))
    assert vision_request_config(loader) == LLMConfig(LEGACY_KEY, V1_BASE, "legacy-model", 30, 4)
    assert loader.call_count == 1
    assert not v2_path().exists()


def test_disabled_vision_stops_instead_of_reusing_v1(tmp_path):
    capability_config.write_capability_settings(llm_config.default_capability_settings())
    loader = exploding_loader()
    with pytest.raises(CapabilityConfigError) as error:
        vision_request_config(loader)
    assert error.value.args[0] == capability_config._VISION_DISABLED
    assert_no_secret(str(error.value))
    assert "环境变量" not in str(error.value) and "AIPC" not in str(error.value)
    loader.assert_not_called()
    assert not llm_config.CONFIG_PATH.exists()


def test_needs_confirmation_vision_stops_even_when_enabled(tmp_path):
    capability_config.write_capability_settings(unconfirmed_vision_settings())
    loader = exploding_loader()
    with pytest.raises(CapabilityConfigError) as error:
        vision_request_config(loader)
    assert error.value.args[0] == capability_config._VISION_NEEDS_CONFIRMATION
    assert_no_secret(str(error.value))
    loader.assert_not_called()


def test_local_vision_config_is_returned_without_any_key():
    """本机条目给出的是 LocalVisionConfig：这个类型压根没有 api_key 这一格。

    旧口径是「本机未接入所以抛错」，本批接入后判定面换成成功/失败边界：
    取到配置、不读 v1、输出里没有任何密钥片段，这三条原样保留。
    """
    enable_local_vision()
    loader = exploding_loader()
    cfg = vision_request_config(loader)
    assert isinstance(cfg, LocalVisionConfig)
    assert cfg.base_url == LOCAL_VISION_BASE and cfg.model == "qwen3-vl:2b"
    assert cfg.timeout_seconds == 45 and cfg.max_images == 1
    assert not hasattr(cfg, "api_key") and not hasattr(cfg, "enabled")
    loader.assert_not_called()
    assert_no_secret(repr(cfg))


def test_enabled_vision_yields_the_vision_entry_fields(tmp_path):
    enable_vision()
    cfg = vision_request_config(exploding_loader())
    assert cfg == LLMConfig(VISION_KEY, VISION_BASE, "vision-model", 45, 2)
    assert cfg.enabled is True


def test_broken_v2_raises_the_masked_error_without_echo(tmp_path):
    v2_path().write_text(vision_settings_with_a_broken_vision_entry(surprise=VISION_KEY),
                         encoding="utf-8")
    with pytest.raises(CapabilityConfigError) as error:
        vision_request_config(exploding_loader())
    assert error.value.args[0] == capability_config._UNREADABLE
    assert_no_secret(str(error.value))
    assert VISION_BASE not in str(error.value) and "surprise" not in str(error.value)


# --- 9. 图片张数的兜底来源：只能是 vision，不再是 v1 或环境变量 ---

def test_build_vision_messages_without_a_config_uses_the_vision_limit(tmp_path, monkeypatch):
    enable_vision(max_images=2)
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_client.load_llm_config", loader)
    messages = build_vision_messages("system", "user", pack_images(_build_pack(tmp_path)))
    assert image_parts(messages) == 2
    loader.assert_not_called()


def test_build_vision_messages_without_a_v2_file_keeps_the_v1_limit(tmp_path):
    write_v1(max_images=3)
    messages = build_vision_messages("system", "user", pack_images(_build_pack(tmp_path)))
    assert image_parts(messages) == 3
    assert not v2_path().exists()


def test_build_vision_messages_refuses_when_vision_is_disabled(tmp_path, monkeypatch):
    write_v1()
    enable_text()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.llm_client.load_llm_config", loader)
    with pytest.raises(CapabilityConfigError) as error:
        build_vision_messages("system", "user", pack_images(_build_pack(tmp_path)))
    assert error.value.args[0] == capability_config._VISION_DISABLED
    loader.assert_not_called()


# --- 10. 一次商品理解最多尝试一次模型请求（全部走 analyze_product 真实入口）---

@pytest.mark.parametrize("status_code", [401, 500])
def test_an_http_error_on_the_image_request_does_not_add_a_text_request(tmp_path, monkeypatch, status_code):
    echo = f"云端错误原文里带着 {VISION_KEY} 这段文本"
    write_v1()
    enable_vision()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    appended = branch_spy(monkeypatch, "_analyze_with_text", "文字分支被追加执行")
    factory, transport = record_http(monkeypatch)
    transport.post.return_value = Mock(status_code=status_code, text=echo,
                                       json=Mock(return_value={"error": echo}))
    with cloud_authorization(True):
        analysis = analyze_pack(mixed_pack(tmp_path))
    rendered = assert_single_attempt_offline(analysis, factory, transport, appended)
    assert echo not in rendered and VISION_KEY not in rendered and str(status_code) not in analysis.fallback_reason
    # 那一发被尝试掉的请求：带图片、走 vision 地址与密钥、用 vision 模型。
    assert transport.post.call_args.args[0] == f"{VISION_BASE}/chat/completions"
    assert transport.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {VISION_KEY}"
    assert transport.post.call_args.kwargs["json"]["model"] == "vision-model"
    assert len(sent_images(transport)) == 2
    loader.assert_not_called()


def test_a_response_that_is_not_json_does_not_add_a_text_request(tmp_path, monkeypatch):
    echo = f"响应体连 JSON 都不是，还带着 {VISION_KEY}"
    write_v1()
    enable_vision()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    appended = branch_spy(monkeypatch, "_analyze_with_text", "文字分支被追加执行")
    factory, transport = record_http(monkeypatch)
    transport.post.return_value = Mock(status_code=200,
                                       json=Mock(side_effect=json.JSONDecodeError("Expecting value", echo, 0)))
    with cloud_authorization(True):
        analysis = analyze_pack(mixed_pack(tmp_path))
    rendered = assert_single_attempt_offline(analysis, factory, transport, appended)
    assert echo not in rendered and "Expecting value" not in rendered
    assert len(sent_images(transport)) == 2
    loader.assert_not_called()


@pytest.mark.parametrize("reply", [
    "抱歉，我这次没法给出结构化结果。",
    json.dumps(["核心卖点1", "核心卖点2"], ensure_ascii=False),
    "",
])
def test_a_completion_that_is_not_a_json_object_does_not_add_a_text_request(tmp_path, monkeypatch, reply):
    write_v1()
    enable_vision()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    appended = branch_spy(monkeypatch, "_analyze_with_text", "文字分支被追加执行")
    factory, transport = record_http(monkeypatch)
    transport.post.return_value = completion_response(reply)
    with cloud_authorization(True):
        analysis = analyze_pack(mixed_pack(tmp_path))
    rendered = assert_single_attempt_offline(analysis, factory, transport, appended)
    if reply:
        assert reply not in rendered
    assert len(sent_images(transport)) == 2
    loader.assert_not_called()


def test_a_network_failure_on_the_image_request_still_counts_as_one_attempt(tmp_path, monkeypatch):
    """网络异常这一发必须算成「尝试过一次」，不能因为它没有响应就记成零。"""
    write_v1()
    enable_vision()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    appended = branch_spy(monkeypatch, "_analyze_with_text", "文字分支被追加执行")
    factory, transport = record_http(monkeypatch)
    transport.post.side_effect = httpx.ConnectTimeout(f"connection timed out while reaching {VISION_KEY}")
    with cloud_authorization(True):
        analysis = analyze_pack(mixed_pack(tmp_path))
    rendered = assert_single_attempt_offline(analysis, factory, transport, appended)
    assert "connection timed out" not in rendered and VISION_KEY not in rendered
    assert transport.post.call_count == 1, "超时的那一次 POST 尝试也要计入"
    assert len(sent_images(transport)) == 2
    loader.assert_not_called()


def test_a_successful_image_request_never_reaches_the_text_branch(tmp_path, monkeypatch):
    """同一份「图片 + 文字」素材：这一发成功时，文字分支同样一次都不该被碰。"""
    write_v1()
    enable_vision()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    appended = branch_spy(monkeypatch, "_analyze_with_text", "文字分支被追加执行")
    factory, transport = record_http(monkeypatch)
    with cloud_authorization(True):
        analysis = analyze_pack(mixed_pack(tmp_path))
    assert factory.call_count == 1 and transport.post.call_count == 1
    assert appended.call_count == 0
    assert analysis.source == "llm" and analysis.model == "vision-model"
    assert analysis.product_name == CLOUD_REPLY["product_name"]
    assert len(sent_images(transport)) == 2
    loader.assert_not_called()


def test_a_failed_text_request_with_no_images_stops_at_one_attempt(tmp_path, monkeypatch):
    """无图片那一支的失败面：一次文字尝试后直接离线，不追加任何第二次请求。"""
    write_v1()
    enable_vision()
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    invented = branch_spy(monkeypatch, "_analyze_with_vision", "图片分支被凭空追加执行")
    factory, transport = record_http(monkeypatch)
    transport.post.return_value = completion_response(f"云端拒答 {VISION_KEY}", status_code=500)
    with cloud_authorization(True):
        analysis = analyze_pack(text_only_pack(tmp_path))
    rendered = assert_single_attempt_offline(analysis, factory, transport, invented)
    assert sent_images(transport) == []
    assert transport.post.call_args.args[0] == f"{VISION_BASE}/chat/completions"
    assert VISION_KEY not in rendered
    loader.assert_not_called()


def test_the_v1_transitional_path_also_stops_at_one_attempt(tmp_path, monkeypatch):
    """v2 不存在时仍走 v1，但「一次分析最多一次模型尝试」同样生效，且不许顺手创建 v2。"""
    write_v1()
    assert not v2_path().exists()
    echo = f"旧配置链路上的错误原文 {LEGACY_KEY}"
    appended = branch_spy(monkeypatch, "_analyze_with_text", "文字分支被追加执行")
    factory, transport = record_http(monkeypatch)
    transport.post.return_value = completion_response(echo, status_code=503)
    with cloud_authorization(True):
        analysis = analyze_pack(mixed_pack(tmp_path))
    rendered = assert_single_attempt_offline(analysis, factory, transport, appended)
    assert echo not in rendered and LEGACY_KEY not in rendered
    assert transport.post.call_args.args[0] == f"{V1_BASE}/chat/completions"
    assert transport.post.call_args.kwargs["json"]["model"] == "legacy-model"
    assert len(sent_images(transport)) == 4, "过渡期图片张数仍取自 v1"
    assert not v2_path().exists(), "失败降级不许创建 v2 文件"


def test_an_explicit_config_also_stops_at_one_attempt_and_is_never_replaced_by_text(tmp_path, monkeypatch):
    """契约四.7：调用方显式传入的配置同样只许尝试一次，且不许被 text 取值规则换掉。"""
    loader = exploding_loader()
    monkeypatch.setattr("app.core.ai_product_analyzer.load_llm_config", loader)
    text_gate = Mock(side_effect=AssertionError("显式 config 不得改走 text 取值规则"))
    monkeypatch.setattr("app.core.llm_client.text_request_config", text_gate)
    appended = branch_spy(monkeypatch, "_analyze_with_text", "文字分支被追加执行")
    factory, transport = record_http(monkeypatch)
    echo = f"显式配置那一发的错误原文 {VISION_KEY}"
    transport.post.return_value = completion_response(echo, status_code=500)
    explicit = LLMConfig(VISION_KEY, VISION_BASE, "vision-model", 45, 2)
    with cloud_authorization(True):
        analysis = analyze_pack(mixed_pack(tmp_path), config=explicit)
    rendered = assert_single_attempt_offline(analysis, factory, transport, appended)
    assert echo not in rendered and VISION_KEY not in rendered
    assert transport.post.call_args.args[0] == f"{VISION_BASE}/chat/completions"
    assert transport.post.call_args.kwargs["headers"]["Authorization"] == f"Bearer {VISION_KEY}"
    assert len(sent_images(transport)) == 2
    loader.assert_not_called()
    text_gate.assert_not_called()
