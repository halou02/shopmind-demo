"""四能力配置（schema_version=2）的落盘与读取层。

app/core/llm_config.py 的第一批代码只定义数据契约：纯函数，不碰文件、不读环境变量。
本模块把那份契约接到磁盘上——独立的私有文件、原子写入、脱敏的读取错误，以及 text 与
vision 能力到请求配置的取值规则。v1 的 load_llm_config/save_llm_settings 行为保持原样。
"""
from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from threading import RLock
from typing import Any

from app.core import llm_config
from app.core.llm_config import (
    LOCAL_TEXT_DEFAULT_MAX_TOKENS,
    LLMConfig,
    LocalTextConfig,
    LocalVisionConfig,
)

CAPABILITY_CONFIG_FILENAME = ".ai-capabilities.private.json"
_TEMP_PREFIX = ".ai-capabilities-"
_WRITE_LOCK = RLock()

# 固定文案：不拼接文件内容、异常文本、密钥或地址，也不建议用环境变量绕过已关闭的 v2 text。
_UNREADABLE = "AI 能力配置不可读取，已保留原文件且未回退到其他配置"
_WRITE_FAILED = "AI 能力配置写入失败，原文件保持不变"
_TEXT_DISABLED = "text 能力已在配置中关闭，已停止云端调用"
_TEXT_NEEDS_CONFIRMATION = "text 能力配置尚未确认，已停止云端调用"
_VISION_DISABLED = "vision 能力已在配置中关闭，商品理解已停止云端调用"
_VISION_NEEDS_CONFIRMATION = "vision 能力配置尚未确认，商品理解已停止云端调用"
_IMAGE_DISABLED = "image 能力已在配置中关闭，已停止云端图片生成"
_IMAGE_NEEDS_CONFIRMATION = "image 能力配置尚未确认，已停止云端图片生成"
_VIDEO_DISABLED = "video 能力已在配置中关闭，已停止云端视频生成"
_VIDEO_NEEDS_CONFIRMATION = "video 能力配置尚未确认，已停止云端视频生成"


class CapabilityConfigError(RuntimeError):
    """存储层错误，只携带上面这组固定文案。"""


def capability_config_path() -> Path:
    # 随 llm_config.CONFIG_PATH 现取现算：测试把 v1 路径重定向到临时目录时，v2 一并离开真实目录。
    return llm_config.CONFIG_PATH.parent / CAPABILITY_CONFIG_FILENAME


def read_legacy_data() -> dict[str, Any] | None:
    """旧配置数据：仅当 v1 文件在磁盘上存在才返回，绝不使用环境变量密钥。"""
    if not llm_config.CONFIG_PATH.exists():
        return None
    try:
        return asdict(llm_config.load_llm_config())
    except Exception:
        return None


def migration_candidate(legacy: dict[str, Any]) -> dict[str, Any]:
    """从调用方给的旧字典生成迁移候选；不写盘，候选里的 text 始终待确认且禁用。"""
    try:
        return llm_config.migrate_legacy_settings(legacy)
    except ValueError:
        raise CapabilityConfigError("旧版 AI 配置无法迁移，请重新填写文字能力设置") from None


def read_capability_settings() -> dict[str, Any]:
    """读取 v2；文件缺失时返回迁移候选或默认值，且不会创建文件。"""
    path = capability_config_path()
    if not path.exists():
        legacy = read_legacy_data()
        return (llm_config.default_capability_settings() if legacy is None
                else migration_candidate(legacy))
    try:
        return llm_config.validate_capability_settings(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        raise CapabilityConfigError(_UNREADABLE) from None


def write_capability_settings(settings: dict[str, Any]) -> dict[str, Any]:
    """校验后原子写入同目录临时文件再替换；失败时目标文件与临时文件都不留残。"""
    try:
        validated = llm_config.validate_capability_settings(settings)
    except ValueError:
        raise CapabilityConfigError("AI 能力配置校验未通过，未写入文件") from None
    path = capability_config_path()
    with _WRITE_LOCK:
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent,
                                             prefix=_TEMP_PREFIX, suffix=".tmp", delete=False) as stream:
                temporary = Path(stream.name)
                json.dump(validated, stream, ensure_ascii=False, indent=2)
                stream.flush()
                os.fsync(stream.fileno())
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        except Exception:
            raise CapabilityConfigError(_WRITE_FAILED) from None
        finally:
            if temporary is not None:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
    return validated


def update_capability_settings(capability: str, changes: dict[str, Any]) -> dict[str, Any]:
    """读当前配置 → 复用第一批的 apply_capability_update → 写盘。任何一步失败都不落盘。"""
    current = read_capability_settings()
    updated = llm_config.apply_capability_update(current, capability, changes)
    return write_capability_settings(updated)


def text_request_config(legacy_loader: Callable[[], LLMConfig]) -> LLMConfig | LocalTextConfig:
    """默认文字调用链的配置来源。

    v2 文件在磁盘上时，v2 是这条链路唯一的配置来源：不读 v1 密钥、不读环境变量、
    不回落 v1。只有「已启用、已确认、字段有效」才发出请求配置，其余状态一律抛
    CapabilityConfigError，由调用方现有的模板降级接手。
    云端给出 LLMConfig，本机给出 LocalTextConfig（不含密钥，也不受云端授权管）：本机这一支
    取到配置后不会回落云端，也不会借用 v1 密钥——两个类型的字段不同，调用方靠 isinstance 分流，
    结构上接不到彼此的凭证。
    v2 文件不存在时仍用调用方给出的 v1 取配置方式，属于迁移前行为，不能据此认为旧模型已关闭。
    """
    if not capability_config_path().exists():
        return legacy_loader()
    entry = read_capability_settings()["capabilities"]["text"]
    if not entry["enabled"]:
        raise CapabilityConfigError(_TEXT_DISABLED)
    if entry["needs_confirmation"]:
        raise CapabilityConfigError(_TEXT_NEEDS_CONFIRMATION)
    if entry["execution_location"] == "local":
        return LocalTextConfig(entry["base_url"], entry["model"], entry["timeout_seconds"],
                               LOCAL_TEXT_DEFAULT_MAX_TOKENS)
    # 文字调用不携带图片：max_images 固定为 0，不为凑参数去读 v1。
    return LLMConfig(entry["api_key"], entry["base_url"], entry["model"],
                     entry["timeout_seconds"], 0)


def vision_request_config(legacy_loader: Callable[[], LLMConfig]) -> LLMConfig | LocalVisionConfig:
    """商品理解（图片理解）调用链的配置来源。

    v2 文件在磁盘上时，v2 的 vision 条目是这条链路唯一的配置来源：不读 v1 密钥、不读环境
    变量、不回落 v1，也不借用 text 或 image 条目。只有「已启用、已确认且字段有效」才发出
    请求配置：云端给出 LLMConfig，本机给出 LocalVisionConfig（不含密钥，也不受云端授权管）。
    其余状态一律抛 CapabilityConfigError，由调用方现有的离线降级接手。
    v2 文件不存在时仍用调用方给出的 v1 取配置方式，属于迁移前行为，不能据此认为旧模型已关闭。
    """
    if not capability_config_path().exists():
        return legacy_loader()
    entry = read_capability_settings()["capabilities"]["vision"]
    if not entry["enabled"]:
        raise CapabilityConfigError(_VISION_DISABLED)
    if entry["needs_confirmation"]:
        raise CapabilityConfigError(_VISION_NEEDS_CONFIRMATION)
    if entry["execution_location"] == "local":
        return LocalVisionConfig(entry["base_url"], entry["model"], entry["timeout_seconds"],
                                 entry["max_images"])
    return LLMConfig(entry["api_key"], entry["base_url"], entry["model"],
                     entry["timeout_seconds"], entry["max_images"])


def _cloud_media_request_config(capability: str, legacy_loader: Callable[[], LLMConfig]) -> LLMConfig:
    if not capability_config_path().exists():
        return legacy_loader()
    entry = read_capability_settings()["capabilities"][capability]
    disabled = _IMAGE_DISABLED if capability == "image" else _VIDEO_DISABLED
    needs_confirmation = _IMAGE_NEEDS_CONFIRMATION if capability == "image" else _VIDEO_NEEDS_CONFIRMATION
    if not entry["enabled"]:
        raise CapabilityConfigError(disabled)
    if entry["needs_confirmation"]:
        raise CapabilityConfigError(needs_confirmation)
    if entry["execution_location"] != "cloud":
        raise CapabilityConfigError(f"{capability} 仅支持云端执行")
    return LLMConfig(entry["api_key"], entry["base_url"], entry["model"],
                     entry["timeout_seconds"], 0)


def image_request_config(legacy_loader: Callable[[], LLMConfig]) -> LLMConfig:
    """图片生成调用链的配置来源。"""
    return _cloud_media_request_config("image", legacy_loader)


def video_request_config(legacy_loader: Callable[[], LLMConfig]) -> LLMConfig:
    """视频生成调用链的配置来源。"""
    return _cloud_media_request_config("video", legacy_loader)


def resolve_vision_request(
    legacy_loader: Callable[[], LLMConfig],
) -> tuple[Any, str]:
    """取值，并给出这一步能证明的执行位置：local / cloud / unknown。

    位置必须在取值时就地确定：LLMConfig 本身没有位置字段，事后拿它反推只能靠重新读私有配置
    或可变全局变量，两条都不允许。v1 过渡与调用方显式传入的配置一律 unknown；v2 声明为云端但
    地址其实是本机回环的兼容服务时，也不能宣传成远端执行。
    """
    had_v2 = capability_config_path().exists()
    cfg = vision_request_config(legacy_loader)
    if not had_v2:
        return cfg, "unknown"
    if isinstance(cfg, LocalVisionConfig):
        return cfg, "local"
    return cfg, ("unknown" if llm_config.is_loopback_base_url(cfg.base_url) else "cloud")
