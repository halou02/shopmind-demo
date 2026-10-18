"""本机图片理解（Ollama 原生 /api/chat）的独立客户端。

这一层只封装本机协议：地址、请求形状、单张图的内存缩放、响应结构判定与错误收敛。
商品理解的提示词、JSON 解析、结果转换、合并与离线降级仍然复用 app.core 里已有的实现，
不在这里重做。本模块没有任何密钥概念，也不读云端授权状态。
"""
from __future__ import annotations

import base64
from collections.abc import Iterable
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import httpx
from PIL import Image

from app.core.llm_client import IMAGE_SUFFIXES, LLMClientError, parse_json_object
from app.core.llm_config import LocalVisionConfig, is_loopback_base_url

# 送模型前先等比缩到最长边不超过该值；小图不放大。原图文件始终只读，不落任何中间文件。
LOCAL_IMAGE_MAX_SIDE = 448

# 只按图片真实解码后的格式重编码，避免「扩展名是 png、内容是 jpeg」这种声明与字节不一致。
_RE_ENCODABLE = {"JPEG", "PNG", "WEBP", "GIF", "BMP"}

# 明确的截断原因判失败。done_reason 缺失或来自更新版本的其他取值都不单独判失败，
# 因此这里用白名单式的「已知截断」而不是「允许值列表」。连接探针共用这一份判据。
TRUNCATION_REASONS = frozenset({"length"})


# 用户常把接口文档里的后缀一起粘进来（Ollama 的聊天接口就是 /api/chat）。这些后缀要认下来，
# 否则会在后面再补一段 /api/chat，拼出 /api/api/chat 这种必然 404 的地址。
_REDUNDANT_SUFFIXES = ("/api/chat", "/api")


def _strip_redundant_suffix(path: str) -> str:
    """去掉路径末尾那截「已经是接口本身」的后缀，只保留真正的挂载前缀。

    循环是为了 /api/api/chat 这种粘两次的写法；每轮至少去掉 4 个字符，不会转不停。
    """
    value = path.rstrip("/")
    changed = True
    while changed:
        changed = False
        for suffix in _REDUNDANT_SUFFIXES:
            if value.lower().endswith(suffix):
                value = value[: -len(suffix)].rstrip("/")
                changed = True
    return value


def local_chat_endpoint(base_url: str) -> str:
    """把配置里的地址换成本机回环的 /api/chat 完整 URL。

    字面 localhost 会被换成 127.0.0.1：只靠「名字里含 localhost」并不能证明连接落在回环上，
    hosts 或 DNS 可以把它解析到远端。换成显式回环 IP 后，实际连接目标与名字解析无关。
    """
    value = base_url.strip().rstrip("/")
    try:
        parsed = urlsplit(value)
        port = parsed.port
    except ValueError:
        raise LLMClientError("本机图片理解的地址无效，未发送请求") from None
    host = (parsed.hostname or "").lower()
    if not host:
        raise LLMClientError("本机图片理解的地址无效，未发送请求")
    if (parsed.username is not None or parsed.password is not None
            or parsed.query or parsed.fragment or "?" in value or "#" in value):
        raise LLMClientError("本机图片理解的地址无效，未发送请求")
    if parsed.scheme not in {"http", "https"}:
        raise LLMClientError("本机图片理解的地址协议不受支持，未发送请求")
    if not is_loopback_base_url(value):
        raise LLMClientError("本机图片理解的地址不是本机回环地址，未发送请求")
    connect_host = {"localhost": "127.0.0.1", "::1": "[::1]"}.get(host, host)
    netloc = f"{connect_host}:{port}" if port is not None else connect_host
    prefix = _strip_redundant_suffix(parsed.path)
    return f"{urlunsplit((parsed.scheme, netloc, prefix, '', ''))}/api/chat"


def build_local_client(config: LocalVisionConfig) -> httpx.Client:
    """本地客户端：不走系统代理、不跟随跳转，超时用配置里的 timeout_seconds。

    trust_env=False 是必需的：httpx 默认 True，会去读 Windows 注册表里的系统代理，
    那样「只连本机」这句话就并不成立。follow_redirects 默认就是 False，这里显式写住。
    """
    return httpx.Client(timeout=config.timeout_seconds, trust_env=False, follow_redirects=False)


def encode_local_image(path: Path) -> str | None:
    """读图、真解码、内存里等比缩放、按实际内容重编码成裸 base64。

    任何读不了、解不开、格式不支持、超尺寸限制的情况返回 None，由调用方过滤到下一张；
    绝不用发模型请求的方式去试探图片是否合格。
    """
    try:
        raw = path.read_bytes()
    except OSError:
        return None
    if not raw:
        return None
    try:
        with Image.open(BytesIO(raw)) as image:
            image.load()
            fmt = (image.format or "").upper()
            if fmt not in _RE_ENCODABLE:
                return None
            if max(image.size) > LOCAL_IMAGE_MAX_SIDE:
                scale = LOCAL_IMAGE_MAX_SIDE / max(image.size)
                size = tuple(max(1, round(edge * scale)) for edge in image.size)
                image = image.resize(size)
            if fmt == "JPEG" and image.mode not in {"RGB", "L"}:
                image = image.convert("RGB")
            elif fmt == "WEBP" and image.mode not in {"RGB", "RGBA"}:
                image = image.convert("RGBA")
            elif fmt == "BMP" and image.mode != "RGB":
                image = image.convert("RGB")
            buffer = BytesIO()
            image.save(buffer, format=fmt)
    except (OSError, ValueError, Image.DecompressionBombError):
        return None
    payload = buffer.getvalue()
    if not payload:
        return None
    return base64.b64encode(payload).decode("ascii")


def select_first_valid_image(image_paths: Iterable[Path]) -> tuple[Path, str] | None:
    """按调用方给定的顺序取第一张「可读且可解码」的图片，返回 (路径, 裸 base64)。

    顺序由上游的 collect_product_image_paths 决定，这里只做过滤，不重排、不探测模型。
    """
    for path in image_paths:
        try:
            if not path.is_file() or path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
        except OSError:
            continue
        payload = encode_local_image(path)
        if payload is not None:
            return path, payload
    return None


def build_chat_body(config: LocalVisionConfig, *, system_prompt: str, user_text: str,
                    image_base64: str) -> dict:
    """Ollama 原生请求体：图片是 messages[].images 里的裸 base64，不是 data URL。

    num_ctx / num_predict 取自 LocalVisionConfig 的默认值（4096 / 1200）。1200 是按本机实测定的：
    合格结果的输出长度在 515 到 643 token 之间，旧的 700 会在写满时把 JSON 截断。
    format 固定 "json"：结构校验放在本地解析之后，格式不合也绝不追加第二次请求。
    """
    return {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_text, "images": [image_base64]},
        ],
        "stream": False,
        "think": False,
        "format": "json",
        "options": {"num_ctx": config.num_ctx, "num_predict": config.num_predict},
    }


def response_to_content(payload: object) -> str:
    """从原生响应里取出文本内容：必须 done=true，且明确的截断原因判失败。

    eval_count 撞上 num_predict 只算「可能被截断」的标记，不能凭它单独否决一个结构完整、
    校验通过的结果，所以这里完全不参与判定。done_reason 缺失不单独判失败。
    """
    if not isinstance(payload, dict):
        raise LLMClientError("本机图片理解响应格式异常，已降级到本地规则")
    if payload.get("done") is not True:
        raise LLMClientError("本机图片理解未完成返回，已降级到本地规则")
    if str(payload.get("done_reason") or "").strip().lower() in TRUNCATION_REASONS:
        raise LLMClientError("本机图片理解返回被截断，已降级到本地规则")
    message = payload.get("message")
    if not isinstance(message, dict):
        raise LLMClientError("本机图片理解响应缺少内容，已降级到本地规则")
    content = message.get("content")
    if not isinstance(content, str):
        raise LLMClientError("本机图片理解响应格式异常，已降级到本地规则")
    return content


def request_local_chat(config: LocalVisionConfig, body: dict) -> object:
    """发出恰好一次 POST：3xx、非 2xx、传输层异常都收敛成安全的 LLMClientError。

    不回显响应原文、不含密钥，也不把用户的绝对路径带进错误文案；请求头不带 Authorization。
    """
    url = local_chat_endpoint(config.base_url)
    try:
        with build_local_client(config) as client:
            response = client.post(url, json=body)
    except httpx.HTTPError:
        raise LLMClientError("本机图片理解请求失败，已降级到本地规则") from None
    if 300 <= response.status_code < 400:
        raise LLMClientError("本机图片理解返回了重定向，已停止请求并降级到本地规则")
    if not 200 <= response.status_code < 300:
        raise LLMClientError(f"本机图片理解返回错误 {response.status_code}，已降级到本地规则")
    try:
        return response.json()
    except (ValueError, UnicodeDecodeError):
        raise LLMClientError("本机图片理解响应格式异常，已降级到本地规则") from None


def local_chat_completion_json(config: LocalVisionConfig, *, system_prompt: str,
                               user_text: str, image_base64: str) -> dict:
    """一次本机调用 → 解析成对象。JSON 解析沿用 llm_client.parse_json_object。"""
    body = build_chat_body(config, system_prompt=system_prompt, user_text=user_text,
                           image_base64=image_base64)
    return parse_json_object(response_to_content(request_local_chat(config, body)))
