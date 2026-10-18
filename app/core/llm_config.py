from __future__ import annotations

import ipaddress
import json
import math
import os
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass
from pathlib import Path
from threading import RLock
from urllib.parse import urlsplit

CONFIG_PATH = Path(__file__).resolve().parents[2] / ".ai-settings.private.json"
_CONFIG_LOCK = RLock()
# None retains direct core/CLI behavior; Web requests always set an explicit bool.
CLOUD_AUTHORIZED: ContextVar[bool | None] = ContextVar("cloud_authorized", default=None)


@contextmanager
def cloud_authorization(authorized: bool):
    token = CLOUD_AUTHORIZED.set(authorized is True)
    try:
        yield
    finally:
        CLOUD_AUTHORIZED.reset(token)


@dataclass(frozen=True)
class LLMConfig:
    api_key: str | None
    base_url: str
    model: str
    timeout_seconds: float
    max_images: int

    @property
    def enabled(self) -> bool:
        return CLOUD_AUTHORIZED.get() is not False and bool(self.api_key and self.api_key.strip())


# 本机图片理解的引擎参数。1200 的依据是一组本机真请求：合格输出长度 515 到 643 token，旧的 700 在 5 次里
# 撞顶 2 次被截断；输入 836 加输出 1200 仍在 4096 上下文内。那组读数当时记的模型名已从本机消失（ollama show
# 报 not found、真发 /api/chat 回 404），所以它算未复现的旧读数：换模型后按响应里的 eval_count 重测。
LOCAL_VISION_DEFAULT_NUM_CTX = 4096
LOCAL_VISION_DEFAULT_NUM_PREDICT = 1200


@dataclass(frozen=True)
class LocalVisionConfig:
    """本机图片理解的请求配置：独立类型，不带密钥，也不依赖云端授权那个 .enabled。"""

    base_url: str
    model: str
    timeout_seconds: float
    max_images: int
    num_ctx: int = LOCAL_VISION_DEFAULT_NUM_CTX
    num_predict: int = LOCAL_VISION_DEFAULT_NUM_PREDICT


# 本机文字推理的输出上限。参数名只能是 max_tokens：实测 Ollama 的 OpenAI 兼容口会静默忽略
# num_predict（发 num_predict=20 仍回 529 token、finish_reason=stop），而 max_tokens=20 精确停在
# 20 token 并回 finish_reason=length。
# 4000 来自本批两枪端到端真请求（qwen2.5:1.5b-instruct、温度 0.45、真实六平台提示、各一个样本）：
# 上限 1500 那一枪撞顶（122.08 秒），上限 4000 那一枪自然收尾（86.81 秒、正文被采纳）。同一台机器
# 同一个模型两枪的输出长度差出一倍以上，说明 1500 会时不时把写完了的回答误标成没写完。
# 这两个读数只在这台机器成立：换模型、换提示或上下文档位变了都要按响应里的 finish_reason 重测。
LOCAL_TEXT_DEFAULT_MAX_TOKENS = 4000


@dataclass(frozen=True)
class LocalTextConfig:
    """本机文字推理的请求配置：独立类型，不带密钥，也不依赖云端授权那个 .enabled。"""

    base_url: str
    model: str
    timeout_seconds: float
    max_tokens: int = LOCAL_TEXT_DEFAULT_MAX_TOKENS


def _is_loopback_host(host: str | None) -> bool:
    if not host:
        return False
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def is_loopback_base_url(value: str) -> bool:
    """地址的主机必须是字面 localhost 或明确的回环 IP。

    只判「名字里含 localhost」是不够的：localhost.evil.com 会因此漏网；
    127.0.0.1 的整数写法（2130706433）与 0.0.0.0 都不算回环。
    """
    try:
        return _is_loopback_host(urlsplit(value.strip()).hostname)
    except ValueError:
        return False


def validate_base_url(value: str) -> str:
    value = value.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        host = parsed.hostname
        port = parsed.port
        if not host or parsed.username is not None or parsed.password is not None:
            raise ValueError()
        if parsed.query or parsed.fragment or "?" in value or "#" in value:
            raise ValueError()
        if any(char.isspace() or ord(char) < 32 for char in value) or "\\" in value:
            raise ValueError()
        loopback = host.lower() == "localhost"
        try:
            loopback = loopback or ipaddress.ip_address(host).is_loopback
        except ValueError:
            pass
        if parsed.scheme != "https" and not (parsed.scheme == "http" and loopback):
            raise ValueError()
        if port is not None and not 1 <= port <= 65535:
            raise ValueError()
    except ValueError:
        raise ValueError("地址仅允许 HTTPS 或 HTTP 本机 loopback，且不能包含账号、查询或片段") from None
    return value


def load_llm_config() -> LLMConfig:
    with _CONFIG_LOCK:
        if CONFIG_PATH.exists():
            # An explicitly cleared key must not fall back to environment secrets.
            try:
                data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
                return LLMConfig(**data)
            except Exception:
                raise RuntimeError("私有 AI 配置不可读取") from None
    return LLMConfig(
        api_key=os.environ.get("AIPC_LLM_API_KEY") or os.environ.get("OPENAI_API_KEY") or os.environ.get("DASHSCOPE_API_KEY"),
        base_url=(os.environ.get("AIPC_LLM_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://dashscope.aliyuncs.com/compatible-mode/v1").rstrip("/"),
        model=os.environ.get("AIPC_LLM_MODEL") or os.environ.get("OPENAI_MODEL") or "qwen-vl-max",
        timeout_seconds=float(os.environ.get("AIPC_LLM_TIMEOUT", "90")),
        max_images=int(os.environ.get("AIPC_LLM_MAX_IMAGES", "6")),
    )


def public_llm_settings(config: LLMConfig | None = None) -> dict:
    cfg = config or load_llm_config()
    return {
        "base_url": cfg.base_url, "model": cfg.model,
        "timeout_seconds": cfg.timeout_seconds, "max_images": cfg.max_images,
        "has_api_key": bool(cfg.api_key and cfg.api_key.strip()),
        "connection_status": "not_tested",
    }


def save_llm_settings(*, base_url: str, model: str, timeout_seconds: float,
                      max_images: int, api_key: str = "", clear_api_key: bool = False) -> dict:
    base_url = validate_base_url(base_url)
    model = model.strip()
    if not model or len(model) > 200:
        raise ValueError("请填写有效模型名称")
    if not math.isfinite(timeout_seconds) or not 1 <= timeout_seconds <= 600:
        raise ValueError("超时应为 1–600 秒")
    if not 0 <= max_images <= 20:
        raise ValueError("图片数量应为 0–20")
    if len(api_key) > 4096 or any(ord(char) < 32 for char in api_key):
        raise ValueError("密钥格式无效")
    with _CONFIG_LOCK:
        current = load_llm_config()
        key = None if clear_api_key else (api_key.strip() or current.api_key)
        cfg = LLMConfig(key, base_url, model, timeout_seconds, max_images)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=CONFIG_PATH.parent,
                                             prefix=".ai-settings-", suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(asdict(cfg), stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, CONFIG_PATH)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
    return public_llm_settings(cfg)


CAPABILITY_NAMES: tuple[str, ...] = ("text", "vision", "image", "video")
SCHEMA_VERSION_V2 = 2
LEGACY_SCHEMA_VERSION = 1
EXECUTION_LOCATIONS: tuple[str, ...] = ("local", "cloud")
CREDENTIAL_ACTIONS: tuple[str, ...] = ("keep", "replace", "clear")
DEFAULT_CAPABILITY_TIMEOUT = 90
OLLAMA_CHAT_ADAPTER = "ollama_chat"
LOCAL_VISION_MAX_IMAGES = 1
# 白名单只收录 app/core/llm_client.py 与 app/core/ai_media_generator.py 中已实现请求路径的协议。
# ollama_chat 只服务本机回环的图片理解，由 app/core/local_vision_client.py 发起。
SUPPORTED_ADAPTERS: dict[str, tuple[str, ...]] = {
    "text": ("openai_chat",),
    "vision": ("openai_chat", "ollama_chat"),
    "image": ("openai_images", "dashscope_images"),
    "video": ("openai_video",),
}
_LEGACY_FIELDS = ("api_key", "base_url", "model", "timeout_seconds", "max_images")
_SERVER_MANAGED_FIELDS = ("config_revision", "needs_confirmation")
_BASE_CREDENTIAL_FIELDS = ("base_url", "adapter", "execution_location")


def _capability_field_names(capability: str) -> tuple[str, ...]:
    names = ("enabled", "execution_location", "adapter", "base_url", "model",
             "timeout_seconds", "api_key", "config_revision") + _SERVER_MANAGED_FIELDS
    return names + (("max_images",) if capability == "vision" else ())


def _default_capability_entry(capability: str) -> dict:
    entry = {
        "enabled": False,
        "execution_location": "cloud",
        "adapter": "",
        "base_url": "",
        "model": "",
        "timeout_seconds": DEFAULT_CAPABILITY_TIMEOUT,
        "api_key": None,
        "config_revision": 0,
        "needs_confirmation": False,
    }
    if capability == "vision":
        entry["max_images"] = 0
    return entry


def _coerce_flag(value, field: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{field} 必须是布尔值")
    return value


def _coerce_execution_location(value) -> str:
    if not isinstance(value, str) or value.strip() not in EXECUTION_LOCATIONS:
        raise ValueError("执行位置仅支持 local 或 cloud")
    return value.strip()


def _coerce_adapter(capability: str, value) -> str:
    if not isinstance(value, str):
        raise ValueError("协议标识必须是文本")
    adapter = value.strip()
    if adapter and adapter not in SUPPORTED_ADAPTERS[capability]:
        raise ValueError(f"{capability} 的协议不在已接入列表中，无法使用")
    return adapter


def _coerce_base_url(value) -> str:
    if not isinstance(value, str):
        raise ValueError("服务地址必须是文本")
    return validate_base_url(value) if value.strip() else ""


def _coerce_model(value) -> str:
    if not isinstance(value, str):
        raise ValueError("模型名称必须是文本")
    model = value.strip()
    if len(model) > 200:
        raise ValueError("请填写有效模型名称")
    return model


def _coerce_timeout(value) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("超时应为 1–600 秒")
    seconds = float(value)
    if not math.isfinite(seconds) or not 1 <= seconds <= 600:
        raise ValueError("超时应为 1–600 秒")
    return seconds


def _coerce_api_key(value) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError("密钥格式无效")
    key = value.strip()
    if len(key) > 4096 or any(ord(char) < 32 for char in key):
        raise ValueError("密钥格式无效")
    return key or None


def _coerce_max_images(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("图片数量应为 0–20 的整数")
    if not 0 <= value <= 20:
        raise ValueError("图片数量应为 0–20 的整数")
    return value


def _coerce_revision(value) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError("配置版本号无效")
    return value


def _require_enabled_fields(capability: str, entry: dict) -> None:
    if not SUPPORTED_ADAPTERS[capability]:
        raise ValueError(f"{capability} 尚无已接入协议，不能启用")
    if not entry["adapter"]:
        raise ValueError(f"启用 {capability} 前必须选择已接入协议")
    if not entry["base_url"]:
        raise ValueError(f"启用 {capability} 需要填写服务地址")
    if not entry["model"]:
        raise ValueError(f"启用 {capability} 需要填写模型名称")
    if entry["execution_location"] == "cloud" and entry["api_key"] is None:
        raise ValueError(f"启用云端 {capability} 需要填写密钥")
    if capability == "vision" and entry["max_images"] < 1:
        raise ValueError("启用图片理解时单次图片数量至少为 1")
    # 执行位置写着本机、地址却是局域网或公网，那就不是本机执行：文字这一支同样只收回环地址。
    if capability == "text" and entry["execution_location"] == "local" \
            and not is_loopback_base_url(entry["base_url"]):
        raise ValueError("本机文字推理的地址只能是本机回环地址")
    if capability != "vision":
        return
    local = entry["execution_location"] == "local"
    if local and entry["adapter"] != OLLAMA_CHAT_ADAPTER:
        raise ValueError("本机图片理解必须使用 ollama_chat 协议")
    if entry["adapter"] == OLLAMA_CHAT_ADAPTER and not local:
        raise ValueError("ollama_chat 协议只能用于本机执行位置")
    if local:
        if not is_loopback_base_url(entry["base_url"]):
            raise ValueError("本机图片理解的地址只能是本机回环地址")
        if entry["max_images"] != LOCAL_VISION_MAX_IMAGES:
            raise ValueError("本机图片理解单次只支持 1 张图片")


def _validate_capability_entry(capability: str, entry) -> dict:
    if not isinstance(entry, dict):
        raise ValueError(f"{capability} 配置必须是对象")
    names = _capability_field_names(capability)
    if set(entry) - set(names):
        raise ValueError(f"{capability} 配置包含未知字段")
    missing = set(names) - set(entry)
    if missing:
        raise ValueError(f"{capability} 配置缺少字段：{'、'.join(sorted(missing))}")
    validated = {
        "enabled": _coerce_flag(entry["enabled"], "enabled"),
        "execution_location": _coerce_execution_location(entry["execution_location"]),
        "adapter": _coerce_adapter(capability, entry["adapter"]),
        "base_url": _coerce_base_url(entry["base_url"]),
        "model": _coerce_model(entry["model"]),
        "timeout_seconds": _coerce_timeout(entry["timeout_seconds"]),
        "api_key": _coerce_api_key(entry["api_key"]),
        "config_revision": _coerce_revision(entry["config_revision"]),
        "needs_confirmation": _coerce_flag(entry["needs_confirmation"], "needs_confirmation"),
    }
    if capability == "vision":
        validated["max_images"] = _coerce_max_images(entry["max_images"])
        # 本机条目任何时候都不许携带密钥：不管是启用状态还是草稿，replace 也别想把云端密钥带进本地。
        if validated["execution_location"] == "local" and validated["api_key"] is not None:
            raise ValueError("本机图片理解不能保存密钥，请先清除该能力的密钥")
    elif capability == "text":
        # 同理：本机文字这一支结构上就没有密钥可发，留着云端密钥只会让界面显示一句「已配置密钥」的假话。
        if validated["execution_location"] == "local" and validated["api_key"] is not None:
            raise ValueError("本机文字推理不能保存密钥，请先清除该能力的密钥")
    if validated["enabled"]:
        _require_enabled_fields(capability, validated)
    return validated


def _validate_legacy_migration(value) -> dict:
    if not isinstance(value, dict):
        raise ValueError("迁移记录必须是对象")
    if set(value) - {"source_schema_version", "needs_confirmation", "unmapped"}:
        raise ValueError("迁移记录包含未知字段")
    if value.get("source_schema_version") != LEGACY_SCHEMA_VERSION:
        raise ValueError("迁移记录的来源版本无效")
    pending = value.get("needs_confirmation", [])
    if not isinstance(pending, list) or any(item not in CAPABILITY_NAMES for item in pending):
        raise ValueError("迁移记录的能力名称无效")
    unmapped = value.get("unmapped", {})
    if not isinstance(unmapped, dict) or set(unmapped) - {"max_images"}:
        raise ValueError("迁移记录包含未知字段")
    record = {"source_schema_version": LEGACY_SCHEMA_VERSION, "needs_confirmation": sorted(pending)}
    if "max_images" in unmapped:
        record["unmapped"] = {"max_images": _coerce_max_images(unmapped["max_images"])}
    return record


def validate_capability_settings(settings: dict) -> dict:
    if not isinstance(settings, dict):
        raise ValueError("AI 能力配置必须是对象")
    if set(settings) - {"schema_version", "capabilities", "legacy_migration"}:
        raise ValueError("AI 能力配置包含未知字段")
    if settings.get("schema_version") != SCHEMA_VERSION_V2:
        raise ValueError(f"AI 能力配置的 schema_version 必须为 {SCHEMA_VERSION_V2}")
    raw_capabilities = settings.get("capabilities")
    if not isinstance(raw_capabilities, dict):
        raise ValueError("AI 能力配置缺少 capabilities")
    if len(raw_capabilities) != len(CAPABILITY_NAMES) or any(
            key not in CAPABILITY_NAMES for key in raw_capabilities):
        raise ValueError(f"capabilities 必须且只能是 {'、'.join(CAPABILITY_NAMES)}")
    validated = {
        "schema_version": SCHEMA_VERSION_V2,
        "capabilities": {
            capability: _validate_capability_entry(capability, raw_capabilities[capability])
            for capability in CAPABILITY_NAMES
        },
    }
    if "legacy_migration" in settings:
        validated["legacy_migration"] = _validate_legacy_migration(settings["legacy_migration"])
    return validated


def default_capability_settings() -> dict:
    return validate_capability_settings({
        "schema_version": SCHEMA_VERSION_V2,
        "capabilities": {name: _default_capability_entry(name) for name in CAPABILITY_NAMES},
    })


def migrate_legacy_settings(legacy: dict) -> dict:
    if not isinstance(legacy, dict):
        raise ValueError("旧版 AI 配置必须是对象")
    if set(legacy) - set(_LEGACY_FIELDS):
        raise ValueError("旧版 AI 配置包含未知字段")
    for name in ("base_url", "model", "timeout_seconds"):
        if name not in legacy:
            raise ValueError(f"旧版 AI 配置缺少字段：{name}")
    settings = default_capability_settings()
    text = settings["capabilities"]["text"]
    text["base_url"] = _coerce_base_url(legacy["base_url"])
    text["model"] = _coerce_model(legacy["model"])
    text["timeout_seconds"] = _coerce_timeout(legacy["timeout_seconds"])
    text["api_key"] = _coerce_api_key(legacy.get("api_key"))
    text["needs_confirmation"] = True
    settings["legacy_migration"] = {
        "source_schema_version": LEGACY_SCHEMA_VERSION,
        "needs_confirmation": ["text"],
        "unmapped": {"max_images": _coerce_max_images(legacy.get("max_images", 0))},
    }
    return validate_capability_settings(settings)


def _migration_backfill_adapter(capability: str, entry: dict, changes: dict, action: str) -> str | None:
    """窄例外：迁移候选在同一次更新里补齐唯一已接入协议并启用它时，允许保留迁移过来的密钥；
    地址、执行位置（前后均为 cloud）、待确认状态任一不符即回到既有的换凭证保护。"""
    supported = SUPPORTED_ADAPTERS[capability]
    enabled = changes["enabled"] if "enabled" in changes else entry["enabled"]
    if (capability != "text" or action != "keep" or enabled is not True
            or not entry["needs_confirmation"] or entry["adapter"]
            or entry["execution_location"] != "cloud"
            or len(supported) != 1 or "adapter" not in changes):
        return None
    adapter = _coerce_adapter(capability, changes["adapter"])
    if adapter != supported[0]:
        return None
    if "execution_location" in changes and _coerce_execution_location(changes["execution_location"]) != "cloud":
        return None
    if "base_url" in changes and _coerce_base_url(changes["base_url"]) != entry["base_url"]:
        return None
    return adapter


def apply_capability_update(settings: dict, capability: str, changes: dict) -> dict:
    validated = validate_capability_settings(settings)
    if capability not in CAPABILITY_NAMES:
        raise ValueError(f"能力必须且只能是 {'、'.join(CAPABILITY_NAMES)}")
    if not isinstance(changes, dict):
        raise ValueError("能力更新内容必须是对象")
    allowed = set(_capability_field_names(capability)) - set(_SERVER_MANAGED_FIELDS) | {"credential_action"}
    if set(changes) - allowed:
        raise ValueError(f"{capability} 更新包含未知或由服务端管理的字段")
    action = changes.get("credential_action", "keep")
    if not isinstance(action, str) or action not in CREDENTIAL_ACTIONS:
        raise ValueError("credential_action 仅支持 keep、replace、clear")
    entry = dict(validated["capabilities"][capability])
    candidate = dict(entry)
    candidate["needs_confirmation"] = False
    supplied_key = _coerce_api_key(changes["api_key"]) if "api_key" in changes else None
    if action == "replace":
        candidate["api_key"] = supplied_key
        if candidate["api_key"] is None:
            raise ValueError("replace 需要提供非空密钥")
    elif action == "clear":
        if supplied_key is not None:
            raise ValueError("clear 不接受同时提供新密钥")
        candidate["api_key"] = None
    elif supplied_key is not None:
        raise ValueError("keep 不接受夹带新密钥，请使用 replace 或 clear")
    for name in _BASE_CREDENTIAL_FIELDS:
        if name not in changes:
            continue
        coerced = (_coerce_base_url(changes[name]) if name == "base_url"
                   else _coerce_adapter(capability, changes[name]) if name == "adapter"
                   else _coerce_execution_location(changes[name]))
        # 逐条比对更新前的 entry，判定与 changes 内部顺序无关；协议例外只在窄条件全部成立时放行。
        exempt = name == "adapter" and coerced == _migration_backfill_adapter(capability, entry, changes, action)
        if entry["api_key"] is not None and action == "keep" and coerced != entry[name] and not exempt:
            raise ValueError("更换地址、协议或执行位置时，请显式 clear 或 replace 该能力的密钥")
        candidate[name] = coerced
    if "enabled" in changes:
        candidate["enabled"] = _coerce_flag(changes["enabled"], "enabled")
    if "model" in changes:
        candidate["model"] = _coerce_model(changes["model"])
    if "timeout_seconds" in changes:
        candidate["timeout_seconds"] = _coerce_timeout(changes["timeout_seconds"])
    if capability == "vision" and "max_images" in changes:
        candidate["max_images"] = _coerce_max_images(changes["max_images"])
    if candidate == entry:
        return validated
    candidate["config_revision"] = entry["config_revision"] + 1
    updated = {"schema_version": SCHEMA_VERSION_V2,
               "capabilities": dict(validated["capabilities"], **{capability: candidate})}
    if "legacy_migration" in validated:
        record = dict(validated["legacy_migration"])
        record["needs_confirmation"] = [name for name in record["needs_confirmation"] if name != capability]
        updated["legacy_migration"] = record
    return validate_capability_settings(updated)


def public_capability_settings(settings: dict) -> dict:
    validated = validate_capability_settings(settings)
    public = {"schema_version": SCHEMA_VERSION_V2, "capabilities": {}}
    for capability in CAPABILITY_NAMES:
        entry = validated["capabilities"][capability]
        summary = {
            "enabled": entry["enabled"],
            "execution_location": entry["execution_location"],
            "adapter": entry["adapter"],
            "base_url": entry["base_url"],
            "model": entry["model"],
            "timeout_seconds": entry["timeout_seconds"],
            "has_api_key": entry["api_key"] is not None,
            "needs_confirmation": entry["needs_confirmation"],
            "connection_status": "not_tested",
            "integration_status": "adapter_ready" if SUPPORTED_ADAPTERS[capability] else "adapter_missing",
            "config_revision": entry["config_revision"],
        }
        if capability == "vision":
            summary["max_images"] = entry["max_images"]
        public["capabilities"][capability] = summary
    if "legacy_migration" in validated:
        public["legacy_migration"] = validated["legacy_migration"]
    return public
