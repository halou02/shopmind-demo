"""「文字推理 · 本机」连接自检：最多两个请求，任何失败都收敛成一句人话。

与 local_vision_probe 同一套口径：reason 给机器看，message 给人看，正文不外泄
（不带上游原文、异常文本、路径、文件名、密钥）。话术表刻意独立一份而不是并进视觉那张：
tests/test_local_vision_probe.py 直接读 probe._MESSAGES 并钉着「恰好 11 个」，
合并就会把那条断言弄红，两套能力也就不再能各自演进。

地址少写 /v1 这一条不需要发任何请求就能判：真实调用走的是
f"{validate_base_url(base_url)}/chat/completions"（app/core/llm_client.py:57），
保存的地址路径不以 /v1 结尾，拼出来的路径必然不是 Ollama 的兼容接口。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.capability_config import (
    CapabilityConfigError,
    capability_config_path,
    read_capability_settings,
    text_request_config,
)
from app.core.llm_client import LLMClientError
from app.core.llm_config import (
    LLMConfig,
    LocalTextConfig,
    is_loopback_base_url,
    validate_base_url,
)
from app.core.local_vision_client import build_local_client
from app.core.local_vision_probe import (
    PROBE_GENERATION_TIMEOUT_SECONDS,
    PROBE_SERVICE_TIMEOUT_SECONDS,
    REASON_BAD_ENVELOPE,
    REASON_MODEL_NOT_FOUND,
    REASON_SERVICE_NOT_RUNNING,
    REASON_UNKNOWN,
    STAGE_CONFIG,
    STAGE_GENERATION,
    STAGE_MODEL,
    STAGE_SERVICE,
    TAGS_ABSENT,
    TAGS_NOT_JSON,
    ProbeOutcome,
    carries_error_field,
    classify_tags_body,
    classify_transport_error,
    safe_json,
)
from app.core.local_vision_probe import _MESSAGES as VISION_MESSAGES

REASON_PASSED = "passed"
REASON_ADDRESS_MISSING_V1 = "address_missing_v1"
REASON_NO_REPLY = "no_reply"
REASON_UPSTREAM_ERROR = "upstream_error"
REASON_NOT_TESTABLE = "not_testable"
REASON_ADDRESS_INVALID = "address_invalid"

TEXT_PROBE_REASONS: tuple[str, ...] = (
    REASON_PASSED, REASON_SERVICE_NOT_RUNNING, REASON_MODEL_NOT_FOUND, REASON_ADDRESS_MISSING_V1,
    REASON_NO_REPLY, REASON_UPSTREAM_ERROR, REASON_BAD_ENVELOPE, REASON_NOT_TESTABLE,
    REASON_ADDRESS_INVALID, REASON_UNKNOWN,
)

# 探针只让模型写几个字：64 足够它给出一个短回答，又不会把一次自检变成一次真实文案生成。
TEXT_PROBE_MAX_TOKENS = 64
_PROBE_USER_TEXT = '只回复一个 JSON 对象：{"probe":"ok"}'

# 六句与图片理解完全同义，直接从那张表里取，避免同一句话在两个文件里各自漂移；
# 四句是文字专属：视觉那句「地址改成 http://127.0.0.1:11434」对文字是反的，照抄会把人带错方向。
_REUSED = (REASON_SERVICE_NOT_RUNNING, REASON_MODEL_NOT_FOUND, REASON_NO_REPLY,
           REASON_UPSTREAM_ERROR, REASON_ADDRESS_INVALID, REASON_UNKNOWN)

_TEXT_MESSAGES: dict[str, str] = {
    **{reason: VISION_MESSAGES[reason] for reason in _REUSED},
    REASON_PASSED: "本次测试通过：本机 Ollama 给出了模型清单，设置里填的模型也确实完成了一次很短的输出。"
                   "注意：这次只让模型写了几个字，它证明不了文案的质量，"
                   "也证明不了真实生成不会被输出上限截断。",
    REASON_NOT_TESTABLE: "未发送任何请求：这个按钮只测「文字推理 · 本机 · openai_chat」这一格，"
                         "且必须是已保存、已启用、已确认的设置。",
    REASON_ADDRESS_MISSING_V1: "API 地址少写了 /v1：这个地址上本机 Ollama 有应答，模型名也在清单里，"
                               "但按现在的地址拼出来的请求路径不是它的 OpenAI 兼容接口。"
                               "请把「API 地址」写成 http://127.0.0.1:11434/v1 这种带 /v1 的形式再点一次，"
                               "本程序不会替你补这个后缀。",
    REASON_BAD_ENVELOPE: "这个地址有应答，但回答的不是模型该有的样子，多半那个端口上跑的不是 Ollama。"
                         "请把「API 地址」改成本机 Ollama 自己的端口并带上 /v1，"
                         "例如 http://127.0.0.1:11434/v1，再点一次。",
}


class TextProbeOutcome(ProbeOutcome):
    """换一张话术表，payload 形状与图片理解保持一致，前端一套渲染代码能测两个能力。"""

    MESSAGES = _TEXT_MESSAGES


def text_probe_endpoints(base_url: str) -> tuple[str, str]:
    """(聊天地址, 模型清单地址)。聊天地址与真实调用逐字同构，探针才不会测出一条走不通的路。

    清单地址故意走 Ollama 原生 /api/tags：它与 base_url 末尾挂没挂 /v1 无关，
    这样「服务在不在、模型名对不对」和「兼容口后缀写全了没有」是两件能分开确认的事。
    """
    chat_url = f"{validate_base_url(base_url)}/chat/completions"
    parsed = urlsplit(validate_base_url(base_url))
    return chat_url, f"{parsed.scheme}://{parsed.netloc}/api/tags"


def compat_suffix_missing(base_url: str) -> bool:
    """保存的地址路径是否少了 /v1。真实调用不会替用户补这一段。"""
    return not urlsplit(validate_base_url(base_url)).path.rstrip("/").lower().endswith("/v1")


def build_text_probe_body(config: LocalTextConfig) -> dict:
    """带 max_tokens 是因为实测只有这个参数名被兼容口认（num_predict 会被静默忽略）。

    不发 response_format：探针要确认的是连通与模型名，不该因为某个模型不支持 JSON 模式
    就报成连接失败——那会把人引去改一个本来没坏的设置。
    """
    return {
        "model": config.model,
        "messages": [{"role": "user", "content": _PROBE_USER_TEXT}],
        "stream": False,
        "temperature": 0,
        "max_tokens": TEXT_PROBE_MAX_TOKENS,
    }


def classify_text_tags_response(status_code: int, body: object, model: str) -> str:
    """清单这一步的结论。不是 passed 就不再发那次生成请求。"""
    if 300 <= status_code < 400:
        return REASON_BAD_ENVELOPE
    if not 200 <= status_code < 300:
        return REASON_BAD_ENVELOPE if status_code == 404 else REASON_UPSTREAM_ERROR
    verdict = classify_tags_body(body, model)
    if verdict == TAGS_ABSENT:
        return REASON_MODEL_NOT_FOUND
    if verdict == TAGS_NOT_JSON:
        return REASON_BAD_ENVELOPE
    return REASON_PASSED


def classify_text_completion(status_code: int, body: object) -> str:
    """判定生成请求的结局。404 有两种，状态码分不开：模型名不存在是带 error 对象的 JSON，
    路径没挂对是纯文本 404 page not found（本机实测），所以只能看正文形状。

    这里不再报「少写 /v1」：那条已经由 compat_suffix_missing 在零请求阶段拦下，
    走到这一步说明地址路径确实以 /v1 结尾，再说人少写了就是假话，只能归到「应答不像 Ollama」。
    """
    if 300 <= status_code < 400:
        return REASON_BAD_ENVELOPE
    if 200 <= status_code < 300:
        if not isinstance(body, dict):
            return REASON_BAD_ENVELOPE
        choices = body.get("choices")
        first = choices[0] if isinstance(choices, list) and choices else None
        message = first.get("message") if isinstance(first, dict) else None
        content = message.get("content") if isinstance(message, dict) else None
        if not isinstance(content, str) or not content.strip():
            return REASON_BAD_ENVELOPE
        return REASON_PASSED
    if status_code == 404:
        return REASON_MODEL_NOT_FOUND if carries_error_field(body) else REASON_BAD_ENVELOPE
    return REASON_UPSTREAM_ERROR


def _no_legacy_loader() -> LLMConfig:
    """探针只认 v2 的 text 条目，永远不该拿 v1 兜底。走到这里就说明上面的闸门失效了。"""
    raise AssertionError("连接探针不得回落到 v1 配置")


def probe_local_text(config: LocalTextConfig) -> TextProbeOutcome:
    """两段短路的执行体：最多两个请求，不重试，共用一个客户端。"""
    try:
        chat_url, tags_url = text_probe_endpoints(config.base_url)
    except (LLMClientError, ValueError):
        return TextProbeOutcome(False, REASON_ADDRESS_INVALID, STAGE_CONFIG)
    if not is_loopback_base_url(config.base_url):
        return TextProbeOutcome(False, REASON_ADDRESS_INVALID, STAGE_CONFIG)

    timeout = float(config.timeout_seconds)
    stage = STAGE_SERVICE
    try:
        with build_local_client(
                LocalTextConfig(config.base_url, config.model,
                                min(timeout, PROBE_GENERATION_TIMEOUT_SECONDS),
                                config.max_tokens)) as client:
            tags = client.get(tags_url, timeout=min(timeout, PROBE_SERVICE_TIMEOUT_SECONDS))
            reason = classify_text_tags_response(tags.status_code, safe_json(tags), config.model)
            if reason != REASON_PASSED:
                # 清单已经拿到、里面没有这个名字：服务与前缀都没问题，卡住的是模型名这一步。
                stage = STAGE_MODEL if reason == REASON_MODEL_NOT_FOUND else STAGE_SERVICE
                return TextProbeOutcome(False, reason, stage, tags.status_code)
            if compat_suffix_missing(config.base_url):
                # 服务与模型名都已经确认过，卡住的是兼容口后缀：这一步零请求就能下结论。
                return TextProbeOutcome(False, REASON_ADDRESS_MISSING_V1, STAGE_MODEL)
            stage = STAGE_GENERATION
            chat = client.post(chat_url, json=build_text_probe_body(config))
    except httpx.HTTPError as error:
        return TextProbeOutcome(False, classify_transport_error(error), stage)

    verdict = classify_text_completion(chat.status_code, safe_json(chat))
    return TextProbeOutcome(verdict == REASON_PASSED, verdict, STAGE_GENERATION, chat.status_code)


def run_local_text_probe() -> dict[str, Any]:
    """取值 → 探测 → 结构化结果。任何异常都收敛掉，绝不让它冒到中间件变成 500 的一句糊话。"""
    checked: dict[str, str] = {}
    try:
        if not capability_config_path().exists():
            return TextProbeOutcome(False, REASON_NOT_TESTABLE, STAGE_CONFIG).payload(checked)
        entry = read_capability_settings()["capabilities"]["text"]
        if not (entry["enabled"] and not entry["needs_confirmation"]
                and entry["execution_location"] == "local"):
            checked = {"base_url": str(entry.get("base_url") or ""),
                       "model": str(entry.get("model") or "")}
            return TextProbeOutcome(False, REASON_NOT_TESTABLE, STAGE_CONFIG).payload(checked)
        config = text_request_config(_no_legacy_loader)
        if not isinstance(config, LocalTextConfig):
            return TextProbeOutcome(False, REASON_NOT_TESTABLE, STAGE_CONFIG).payload(checked)
        outcome = probe_local_text(config)
        return outcome.payload({"base_url": config.base_url, "model": config.model})
    except (CapabilityConfigError, KeyError, TypeError, ValueError):
        return TextProbeOutcome(False, REASON_NOT_TESTABLE, STAGE_CONFIG).payload(checked)
    except Exception:
        return TextProbeOutcome(False, REASON_UNKNOWN, STAGE_CONFIG).payload(checked)
