"""本机图片理解的连接自检：全工程唯一会主动向本机 Ollama 发探测请求的一层。

业务链路（app/core/local_vision_client.py 的 request_local_chat）刻意把「连不上、非 2xx、
被截断」收敛成几句固定的降级话——那是对的：降级时不该把上游原文带出去。但连接自检要的恰好
是相反的东西：用户需要知道该去启动 Ollama、该改地址、还是该改模型名。所以这里重新组织判定，
而不去改那条已经跑在生产路上的收敛。

三条硬约束：
1. 只读配置，绝不写盘。不改 connection_status，不进 public_capability_settings，不建临时文件。
2. 不读任何素材图片。探针只发一段要求回一个短 JSON 的文字消息，因此它证明不了图片理解的质量。
3. 不回显上游正文、异常字符串、绝对路径、配置文件名或密钥。判定只用形状：状态码、正文是不是
   带 error 字段的 JSON、以及 done_reason。

两段短路（按 2026-10-14 本机实测设计）：先 GET {前缀}/api/tags，这一步不载入模型，实测 0.0998
秒返回；服务没开、地址不对、名字不在清单里，都在这一步就能定性。三种都过了才 POST
{前缀}/api/chat 真发一次生成请求——那一步在模型没常驻时是冷载入，可能几十秒。
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

import httpx

from app.core.capability_config import (CapabilityConfigError, capability_config_path,
                                        read_capability_settings, vision_request_config)
from app.core.llm_client import LLMClientError
from app.core.llm_config import LLMConfig, LocalVisionConfig, OLLAMA_CHAT_ADAPTER
from app.core.local_vision_client import (TRUNCATION_REASONS, build_local_client,
                                          local_chat_endpoint)

_CHAT_SUFFIX = "/api/chat"

# 探针的等待预算刻意不用满配置的 timeout_seconds：前端 api() 的 120000 毫秒默认档被守卫钉死，
# 两层相加必须留在地上，让后端先给出「分类结论」而不是让浏览器先超时。
# 用户自己把超时设成 1 秒时这里同样按 1 秒走——真实链路会一样失败，探针的职责是复现现实，
# 不是替配置美化成绩。
PROBE_SERVICE_TIMEOUT_SECONDS = 5.0
PROBE_GENERATION_TIMEOUT_SECONDS = 100.0

STAGE_CONFIG = "config"
STAGE_SERVICE = "service"
STAGE_MODEL = "model"
STAGE_GENERATION = "generation"

REASON_PASSED = "passed"
REASON_SERVICE_NOT_RUNNING = "service_not_running"
REASON_MODEL_NOT_FOUND = "model_not_found"
REASON_ADDRESS_NOT_OLLAMA = "address_not_ollama"
REASON_OUTPUT_TRUNCATED = "output_truncated"
REASON_NO_REPLY = "no_reply"
REASON_UPSTREAM_ERROR = "upstream_error"
REASON_BAD_ENVELOPE = "bad_envelope"
REASON_NOT_TESTABLE = "not_testable"
REASON_ADDRESS_INVALID = "address_invalid"
REASON_UNKNOWN = "unknown_failure"

PROBE_REASONS: tuple[str, ...] = (
    REASON_PASSED, REASON_SERVICE_NOT_RUNNING, REASON_MODEL_NOT_FOUND, REASON_ADDRESS_NOT_OLLAMA,
    REASON_OUTPUT_TRUNCATED, REASON_NO_REPLY, REASON_UPSTREAM_ERROR, REASON_BAD_ENVELOPE,
    REASON_NOT_TESTABLE, REASON_ADDRESS_INVALID, REASON_UNKNOWN,
)

TAGS_MATCHED = "matched"
TAGS_ABSENT = "absent"
TAGS_NOT_JSON = "not_json"
TAGS_UNKNOWN_SHAPE = "unknown_shape"

# 界面文案：一句一行，不含秒数、不含上游原文。本机的冷载入耗时只在这台无独显的机器上有意义，
# 写进文案就会对队友的机器说谎，所以这里一律不出现数字。
_MESSAGES: dict[str, str] = {
    REASON_PASSED: "本次测试通过：本机 Ollama 给出了模型清单，设置里的模型也确实完成了一次输出。"
                   "注意：本次没有发送任何商品图片，这不等于图片理解的结果质量，"
                   "也不代表真实商品理解不会被截断。",
    REASON_SERVICE_NOT_RUNNING: "本机服务没开：这个地址上没有任何程序在应答，Ollama 很可能没启动。"
                                "请启动 Ollama 后再点一次，本次没有收到任何 HTTP 响应。",
    REASON_MODEL_NOT_FOUND: "模型名不存在：本机 Ollama 有应答，但给不出设置里填的这个模型。"
                            "请在终端运行 ollama list 核对名称并原样填回「模型名称」。"
                            "本程序不会替你下载模型，本次也没有载入任何模型。",
    REASON_ADDRESS_NOT_OLLAMA: "地址形状不对：这个地址有应答，但应答的不是 Ollama 的原生接口"
                               "（最常见是把 /v1 之类的后缀一起粘了进来）。本机图片理解的地址只填到端口，"
                               "例如 http://127.0.0.1:11434，程序会自己接上 /api/chat；"
                               "不要带 /v1，也不要带 /api/chat。",
    REASON_OUTPUT_TRUNCATED: "输出被截断：模型连上了、名称也对，但这次撞到内置长度上限就停了，"
                             "没有把内容说完。真实商品理解的输出比这次长得多，同样可能被截断。",
    REASON_NO_REPLY: "请求已发出，但在这次等待上限内没有回话。本机第一次调用要先把模型读进内存，"
                     "可能超过等待上限；请再点一次，模型已在内存里时会快很多，"
                     "也可以把设置里的「超时（秒）」调大。",
    REASON_UPSTREAM_ERROR: "本机 Ollama 回了一个错误状态，这次没拿到结果。先在终端运行 ollama list "
                           "看这个模型还在不在；还在的话重启一次 Ollama 再点一次。"
                           "页面不会显示服务端返回的原文。",
    REASON_BAD_ENVELOPE: "这个地址有应答，但回答的不是模型该有的样子，多半那个端口上跑的不是 Ollama。"
                         "请把地址改成 Ollama 自己的端口，例如 http://127.0.0.1:11434，再点一次。",
    REASON_NOT_TESTABLE: "未发送任何请求：这个按钮只测「图片理解 · 本机 · ollama_chat」这一格，"
                         "且必须是已保存、已启用、已确认的设置。",
    REASON_ADDRESS_INVALID: "未发送任何请求：设置里的地址不是合法的本机回环地址，"
                            "本机这一支不会向外部发送请求。",
    REASON_UNKNOWN: "这次测试没跑完，网站自己碰到了意外，没得出任何结论。"
                    "你的设置和配置文件都没被改动，也没发送商品图片。",
}

_PROBE_USER_TEXT = '只回复一个 JSON 对象：{"probe":"ok"}'


@dataclass(frozen=True)
class ProbeOutcome:
    """一次探测的结论。payload 是给它自己看的，reason 是给机器看的，message 是给人看的。"""

    ok: bool
    reason: str
    stage: str
    status_code: int | None = None

    # 话术表挂在类上而不是函数里：文字探针换一个表就能复用同一套 payload 形状，
    # 不必抄第二份「结论怎么变成给人看的一句话」。无类型注解，所以它不是 dataclass 字段。
    MESSAGES = _MESSAGES

    def message(self) -> str:
        return self.MESSAGES.get(self.reason, self.MESSAGES[REASON_UNKNOWN])

    def payload(self, checked: dict[str, str] | None = None) -> dict[str, Any]:
        return {"ok": self.ok, "reason": self.reason, "stage": self.stage,
                "message": self.message(), "status_code": self.status_code,
                "checked": dict(checked or {})}


def probe_endpoints(base_url: str) -> tuple[str, str]:
    """(聊天地址, 模型清单地址)。清单地址由聊天地址剥掉结尾 /api/chat 再接 /api/tags 得到，
    两步共用同一个已经过校验的挂载前缀，不可能出现一步对一步错。"""
    chat_url = local_chat_endpoint(base_url)
    return chat_url, f"{chat_url[: -len(_CHAT_SUFFIX)]}/api/tags"


def build_probe_body(config: LocalVisionConfig) -> dict:
    """与真实请求同构，但 messages 里没有 images 键，因此不可能碰到素材文件。

    num_ctx / num_predict 照抄配置：探针要撞的就是真实调用同一道引擎上限，否则它报出的
    「没被截断」就没有参考价值。
    """
    return {
        "model": config.model,
        "messages": [{"role": "user", "content": _PROBE_USER_TEXT}],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"num_ctx": config.num_ctx, "num_predict": config.num_predict},
    }


def classify_transport_error(error: BaseException) -> str:
    """连接阶段就失败（没人接受这条连接）归到「服务没开」，连上之后没回话才归到「没回话」。

    ConnectTimeout 必须显式点名：httpx 0.28.1 实测它的 MRO 是 TimeoutException → TransportError，
    并不继承 ConnectError（和 urllib3 那套类树不同）。只判 ConnectError 的话，一次
    「SYN 发出去没人应答直到超时」会被报成「模型太慢」，用户就会去等一个不会来的回答。
    """
    if isinstance(error, (httpx.ConnectError, httpx.ConnectTimeout)):
        return REASON_SERVICE_NOT_RUNNING
    if isinstance(error, httpx.TimeoutException):
        return REASON_NO_REPLY
    return REASON_UPSTREAM_ERROR


def safe_json(response: Any) -> object | None:
    """只问正文能不能解析成 JSON，永不读 .text——解析失败本身就是「这不是 Ollama」的证据之一。"""
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        return None


def carries_error_field(body: object) -> bool:
    """响应体是不是「带 error 字段」的形状。原生口是扁平字符串，OpenAI 兼容口是套起来的对象。"""
    if not isinstance(body, dict):
        return False
    value = body.get("error")
    return (isinstance(value, str) and bool(value.strip())) or isinstance(value, dict)


def classify_http_response(status_code: int, body: object, *, tags_confirmed: bool) -> str:
    """把生成请求的结果归到一个 reason。

    本机实测：模型名不存在和路径没挂对，状态码都是 404，只有正文形状不同——前者是
    {"error":"model ... not found"}，后者是纯文本 404 page not found。所以这里先看正文。
    tags_confirmed 是旁证：同一前缀的 /api/tags 已经 200 给出清单，这个前缀就是对的，
    此时聊天地址上的 404 只剩「模型名」这一种解释。
    """
    if 300 <= status_code < 400:
        return REASON_ADDRESS_NOT_OLLAMA
    if 200 <= status_code < 300:
        return REASON_PASSED
    if status_code == 404:
        if carries_error_field(body):
            return REASON_MODEL_NOT_FOUND
        return REASON_MODEL_NOT_FOUND if tags_confirmed else REASON_ADDRESS_NOT_OLLAMA
    return REASON_UPSTREAM_ERROR


def _without_tag(name: str) -> str:
    value = name.strip()
    return value[: -len(":latest")] if value.endswith(":latest") else value


def classify_tags_body(body: object, model: str) -> str:
    """清单里有没有那个模型。认不出来的形状一律返回 unknown_shape 让上层下探，绝不臆断成失败。"""
    if body is None:
        return TAGS_NOT_JSON
    if not isinstance(body, dict):
        return TAGS_UNKNOWN_SHAPE
    items = body.get("models")
    if not isinstance(items, list):
        items = body.get("tags")
    if not isinstance(items, list):
        return TAGS_UNKNOWN_SHAPE
    entries = [item for item in items if isinstance(item, dict)]
    if not entries and items:
        return TAGS_UNKNOWN_SHAPE
    wanted = _without_tag(model)
    for item in entries:
        for key in ("name", "model"):
            value = item.get(key)
            if isinstance(value, str) and _without_tag(value) == wanted:
                return TAGS_MATCHED
    return TAGS_ABSENT


def classify_tags_response(status_code: int, body: object, model: str) -> tuple[str, bool]:
    """(结论或 passed, 是否已在清单里确认到模型)。结论非 passed 就不再发那次生成请求。"""
    if 300 <= status_code < 400:
        return REASON_ADDRESS_NOT_OLLAMA, False
    if not 200 <= status_code < 300:
        if status_code == 404:
            return REASON_ADDRESS_NOT_OLLAMA, False
        return REASON_UPSTREAM_ERROR, False
    verdict = classify_tags_body(body, model)
    if verdict == TAGS_ABSENT:
        return REASON_MODEL_NOT_FOUND, False
    if verdict == TAGS_NOT_JSON:
        return REASON_ADDRESS_NOT_OLLAMA, False
    return (REASON_PASSED, verdict == TAGS_MATCHED)


def classify_generation_body(body: object) -> str:
    """判定顺序与 local_vision_client.response_to_content 一致，两层不会对同一份正文吵起来。

    eval_count 不参与判定：撞上限只算「可能被截断」的标记，不能凭它否决一个结构完整的结论。
    """
    if not isinstance(body, dict):
        return REASON_BAD_ENVELOPE
    if body.get("done") is not True:
        return REASON_BAD_ENVELOPE
    if str(body.get("done_reason") or "").strip().lower() in TRUNCATION_REASONS:
        return REASON_OUTPUT_TRUNCATED
    message = body.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str) or not content.strip():
        return REASON_BAD_ENVELOPE
    return REASON_PASSED


def _no_legacy_loader() -> LLMConfig:
    """探针只认 v2 的 vision 条目，永远不该拿 v1 兜底。走到这里就说明上面的闸门失效了。"""
    raise AssertionError("连接探针不得回落到 v1 配置")


def probe_local_vision(config: LocalVisionConfig) -> ProbeOutcome:
    """两段短路的执行体：最多两个请求，不重试，共用一个客户端。"""
    try:
        chat_url, tags_url = probe_endpoints(config.base_url)
    except LLMClientError:
        return ProbeOutcome(False, REASON_ADDRESS_INVALID, STAGE_CONFIG)

    timeout = float(config.timeout_seconds)
    stage = STAGE_SERVICE
    tags_confirmed = False
    try:
        with build_local_client(replace(config, timeout_seconds=min(timeout, PROBE_GENERATION_TIMEOUT_SECONDS))) as client:
            tags = client.get(tags_url, timeout=min(timeout, PROBE_SERVICE_TIMEOUT_SECONDS))
            reason, tags_confirmed = classify_tags_response(tags.status_code, safe_json(tags),
                                                             config.model)
            if reason != REASON_PASSED:
                # 名字不在清单里 = 服务与前缀都已经确认过，卡住的是模型名这一步。
                stage = STAGE_MODEL if reason == REASON_MODEL_NOT_FOUND else STAGE_SERVICE
                return ProbeOutcome(False, reason, stage, tags.status_code)
            stage = STAGE_GENERATION
            chat = client.post(chat_url, json=build_probe_body(config))
    except httpx.HTTPError as error:
        return ProbeOutcome(False, classify_transport_error(error), stage)

    body = safe_json(chat)
    reason = classify_http_response(chat.status_code, body, tags_confirmed=tags_confirmed)
    if reason != REASON_PASSED:
        return ProbeOutcome(False, reason, STAGE_GENERATION, chat.status_code)
    verdict = classify_generation_body(body)
    return ProbeOutcome(verdict == REASON_PASSED, verdict, STAGE_GENERATION, chat.status_code)


def run_local_vision_probe() -> dict[str, Any]:
    """取值 → 探测 → 结构化结果。任何异常都收敛掉，绝不让它冒到中间件变成 500 的一句糊话。"""
    checked: dict[str, str] = {}
    try:
        if not capability_config_path().exists():
            return ProbeOutcome(False, REASON_NOT_TESTABLE, STAGE_CONFIG).payload(checked)
        entry = read_capability_settings()["capabilities"]["vision"]
        if not (entry["enabled"] and not entry["needs_confirmation"]
                and entry["execution_location"] == "local"
                and entry["adapter"] == OLLAMA_CHAT_ADAPTER):
            checked = {"base_url": str(entry.get("base_url") or ""),
                       "model": str(entry.get("model") or "")}
            return ProbeOutcome(False, REASON_NOT_TESTABLE, STAGE_CONFIG).payload(checked)
        config = vision_request_config(_no_legacy_loader)
        if not isinstance(config, LocalVisionConfig):
            return ProbeOutcome(False, REASON_NOT_TESTABLE, STAGE_CONFIG).payload(checked)
        outcome = probe_local_vision(config)
        return outcome.payload({"base_url": config.base_url, "model": config.model})
    except (CapabilityConfigError, KeyError, TypeError, ValueError):
        return ProbeOutcome(False, REASON_NOT_TESTABLE, STAGE_CONFIG).payload(checked)
    except Exception:
        return ProbeOutcome(False, REASON_UNKNOWN, STAGE_CONFIG).payload(checked)
