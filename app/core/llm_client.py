from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import httpx

from app.core.capability_config import (
    CapabilityConfigError,
    text_request_config,
    vision_request_config,
)
from app.core.llm_config import LLMConfig, LocalTextConfig, load_llm_config, validate_base_url

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


class LLMClientError(RuntimeError):
    pass


def encode_image_data_url(path: Path) -> str:
    suffix = path.suffix.lower()
    mime = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
        ".gif": "image/gif",
        ".bmp": "image/bmp",
    }.get(suffix, "image/jpeg")
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def chat_completion_json(
    messages: list[dict[str, Any]],
    *,
    config: LLMConfig | LocalTextConfig | None = None,
    temperature: float = 0.3,
    meta: dict[str, bool] | None = None,
) -> dict[str, Any]:
    try:
        # 传入 v1 取配置的方式本身：v2 存在时不会被调用。
        cfg = config or text_request_config(load_llm_config)
    except CapabilityConfigError:
        raise LLMClientError("AI 能力配置不可用，已停止云端调用") from None
    # isinstance 而不是鸭子类型：调用方传进来的 Mock 恰好也有 base_url 和 model 时，不能被当成本机分支。
    is_local = isinstance(cfg, LocalTextConfig)
    if not is_local and not cfg.enabled:
        raise LLMClientError("未配置 LLM API Key，请设置环境变量 AIPC_LLM_API_KEY")

    try:
        url = f"{validate_base_url(cfg.base_url)}/chat/completions"
    except ValueError:
        raise LLMClientError("AI 服务地址无效，未发送请求") from None
    headers = {"Content-Type": "application/json"}
    if not is_local:
        headers["Authorization"] = f"Bearer {cfg.api_key}"
    body: dict[str, Any] = {
        "model": cfg.model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    if is_local:
        # 上限只能写成 max_tokens：实测 Ollama 的 OpenAI 兼容口会静默忽略 num_predict，照抄视觉那一套等于没设。
        body["max_tokens"] = cfg.max_tokens
    # 本机只连回环：不许读系统代理（环境变量与注册表两条路都算）、不许跟跳转；云端保持原样，企业网络要靠代理出网。
    client_kwargs: dict[str, Any] = {"timeout": cfg.timeout_seconds}
    if is_local:
        client_kwargs["trust_env"] = False
        client_kwargs["follow_redirects"] = False
    try:
        with httpx.Client(**client_kwargs) as client:
            response = client.post(url, headers=headers, json=body)
    except Exception:
        raise LLMClientError("LLM 请求失败，已停止云端调用") from None

    if response.status_code >= 400:
        raise LLMClientError(f"LLM 返回错误 {response.status_code}")

    try:
        payload = response.json()
        choice = payload["choices"][0]
        content = choice["message"]["content"]
        if meta is not None:
            # 截断判据记在解析之前：撞上限的输出多半连 JSON 都收不住，判据不能跟着一起丢。
            meta["truncated"] = choice.get("finish_reason") == "length"
        return parse_json_object(content)
    except Exception:
        raise LLMClientError("LLM 响应格式异常，已降级到本地规则") from None


def parse_json_object(text: str) -> dict[str, Any]:
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError as error:
        raise LLMClientError("LLM 未返回合法 JSON") from None
    if not isinstance(parsed, dict):
        raise LLMClientError("LLM JSON 根节点必须是对象")
    return parsed


def build_vision_messages(
    system_prompt: str,
    user_text: str,
    image_paths: list[Path],
    *,
    config: LLMConfig | None = None,
) -> list[dict[str, Any]]:
    # 图片张数只能来自 vision：显式传入优先，v2 文件存在时不退到 v1 或环境变量。
    cfg = config or vision_request_config(load_llm_config)
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for path in image_paths[: cfg.max_images]:
        if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": encode_image_data_url(path)},
            }
        )
    return [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": content},
    ]
