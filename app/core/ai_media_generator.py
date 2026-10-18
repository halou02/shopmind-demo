from __future__ import annotations

import base64
import mimetypes
import os
import time
from pathlib import Path
from typing import Any

import httpx
from PIL import Image

from app.core.llm_config import LLMConfig, load_llm_config, validate_base_url
from app.web.media_generator import save_generated_images

DEFAULT_IMAGE_MODEL = "gpt-image-1"
DEFAULT_VIDEO_MODEL = "sora-2"
DEFAULT_VIDEO_SIZE = "720x1280"
DEFAULT_VIDEO_SECONDS = "8"
IMAGE_EDIT_SIZE = "1024x1536"


class AIMediaError(RuntimeError):
    pass


def _image_model() -> str:
    return os.environ.get("AIPC_IMAGE_MODEL") or DEFAULT_IMAGE_MODEL


def _video_model(cfg: LLMConfig) -> str:
    return cfg.model.strip() or os.environ.get("AIPC_VIDEO_MODEL") or DEFAULT_VIDEO_MODEL


def _video_timeout(cfg: LLMConfig) -> float:
    raw = os.environ.get("AIPC_VIDEO_TIMEOUT", "")
    if raw.strip():
        try:
            return max(float(raw), cfg.timeout_seconds)
        except ValueError:
            pass
    return max(cfg.timeout_seconds * 6, 180.0)


def _is_dashscope(cfg: LLMConfig) -> bool:
    from urllib.parse import urlsplit

    try:
        return urlsplit(validate_base_url(cfg.base_url)).hostname == "dashscope.aliyuncs.com"
    except ValueError:
        return False


def _api_root(base_url: str) -> str:
    return validate_base_url(base_url)


def _mime_type(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(path.name)
    return guessed or "image/png"


def _resize_image_file(source: Path, target: Path, size: tuple[int, int]) -> Path:
    target.parent.mkdir(parents=True, exist_ok=True)
    with Image.open(source) as image:
        fitted = image.convert("RGB")
        fitted.thumbnail(size, Image.Resampling.LANCZOS)
        canvas = Image.new("RGB", size, (255, 255, 255))
        offset = ((size[0] - fitted.width) // 2, (size[1] - fitted.height) // 2)
        canvas.paste(fitted, offset)
        canvas.save(target, format="PNG")
    return target


def _parse_size(size: str) -> tuple[int, int]:
    try:
        width, height = size.lower().split("x", 1)
        return int(width), int(height)
    except (ValueError, AttributeError):
        return 720, 1280


def generate_images_from_prompt(
    *,
    prompt: str,
    output_dir: Path,
    reference_images: list[Path],
    negative_prompt: str = "",
    count: int = 1,
    config: LLMConfig | None = None,
) -> tuple[list[Path], str]:
    """Generate images via GPT/OpenAI edit API (with references) or DashScope text2image."""
    cfg = config or load_llm_config()
    prompt = prompt.strip()
    if not prompt:
        raise AIMediaError("请输入图片生成提示词")

    failure_note = ""
    if cfg.enabled:
        try:
            if _is_dashscope(cfg):
                paths = _try_dashscope_image(prompt, output_dir, cfg, negative_prompt, count)
            elif reference_images:
                paths = _try_openai_image_edit(
                    prompt,
                    output_dir,
                    cfg,
                    reference_images[: min(len(reference_images), 4)],
                )
            else:
                paths = _try_openai_image(prompt, output_dir, cfg, count)
            if paths:
                return paths, "ai_image"
        except AIMediaError as exc:
            failure_note = str(exc)
        except Exception:
            failure_note = "云端绘图调用失败"

    if reference_images:
        saved = save_generated_images(reference_images[:count], output_dir)
        readme = output_dir / "generation_note.txt"
        readme.write_text(
            "未调用成功 AI 绘图 API，已保存参考图副本。\n"
            f"提示词：{prompt}\n"
            + (f"原因：{failure_note}\n" if failure_note else "")
            + "请确认 .env 中 DASHSCOPE_API_KEY 有效，并等待百炼绘图 2–5 分钟完成。\n",
            encoding="utf-8",
        )
        return saved, "fallback_copy"

    raise AIMediaError("图片生成失败：无参考图且 AI 绘图不可用")


def generate_video_from_prompt(
    *,
    prompt: str,
    output_mp4: Path,
    reference_images: list[Path],
    config: LLMConfig | None = None,
) -> Path:
    """Generate video via OpenAI-compatible /videos API using the first reference image."""
    cfg = config or load_llm_config()
    prompt = prompt.strip()
    if not prompt:
        raise AIMediaError("请输入视频生成提示词")
    if not reference_images:
        raise AIMediaError("请至少上传一张参考图用于视频生成")
    if not cfg.enabled:
        raise AIMediaError("视频生成未启用或未授权云端调用")
    if _is_dashscope(cfg):
        raise AIMediaError("当前配置为 DashScope，请改用 OpenAI 兼容地址以使用 GPT 视频生成")

    video_size = os.environ.get("AIPC_VIDEO_SIZE") or DEFAULT_VIDEO_SIZE
    seconds = os.environ.get("AIPC_VIDEO_SECONDS") or DEFAULT_VIDEO_SECONDS
    prepared = _prepare_video_reference(reference_images[0], output_mp4.parent, video_size)
    return _try_openai_video(prompt, output_mp4, cfg, prepared, video_size, seconds)


def _prepare_video_reference(source: Path, scratch_dir: Path, video_size: str) -> Path:
    scratch_dir.mkdir(parents=True, exist_ok=True)
    target = scratch_dir / f"video_ref_{source.stem}.png"
    return _resize_image_file(source, target, _parse_size(video_size))


def _try_openai_image_edit(
    prompt: str,
    output_dir: Path,
    cfg: LLMConfig,
    reference_images: list[Path],
) -> list[Path]:
    url = f"{_api_root(cfg.base_url)}/images/edits"
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for image_path in reference_images:
        files.append(("image[]", (image_path.name, image_path.read_bytes(), _mime_type(image_path))))
    data = {
        "model": _image_model(),
        "prompt": prompt,
        "size": IMAGE_EDIT_SIZE,
        "n": "1",
    }
    with httpx.Client(timeout=cfg.timeout_seconds) as client:
        response = client.post(url, headers=headers, data=data, files=files)
        if response.status_code >= 400:
            raise AIMediaError(response.text[:300])
        payload = response.json()
        saved = _save_image_payload(payload, output_dir, client)
        if saved:
            return saved
    raise AIMediaError("未下载到生成图片")


def _try_openai_image(prompt: str, output_dir: Path, cfg: LLMConfig, count: int) -> list[Path]:
    url = f"{_api_root(cfg.base_url)}/images/generations"
    headers = {"Authorization": f"Bearer {cfg.api_key}", "Content-Type": "application/json"}
    body = {
        "model": _image_model(),
        "prompt": prompt,
        "n": min(count, 1),
        "size": IMAGE_EDIT_SIZE,
    }
    with httpx.Client(timeout=cfg.timeout_seconds) as client:
        response = client.post(url, headers=headers, json=body)
        if response.status_code >= 400:
            raise AIMediaError(response.text[:300])
        saved = _save_image_payload(response.json(), output_dir, client)
        if saved:
            return saved
    raise AIMediaError("未下载到生成图片")


def _save_image_payload(payload: dict[str, Any], output_dir: Path, client: httpx.Client) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for index, item in enumerate(payload.get("data") or [], start=1):
        if item.get("b64_json"):
            raw = base64.b64decode(item["b64_json"])
            target = output_dir / f"ai_{index:02d}.png"
            target.write_bytes(raw)
            saved.append(target.resolve())
            continue
        url = item.get("url")
        if not url:
            continue
        response = client.get(url)
        if response.status_code >= 400:
            continue
        suffix = ".png" if "png" in response.headers.get("content-type", "") else ".jpg"
        target = output_dir / f"ai_{index:02d}{suffix}"
        target.write_bytes(response.content)
        saved.append(target.resolve())
    return saved


def _try_dashscope_image(
    prompt: str,
    output_dir: Path,
    cfg: LLMConfig,
    negative_prompt: str,
    count: int,
) -> list[Path]:
    image_model = os.environ.get("AIPC_IMAGE_MODEL") or "wan2.2-t2i-flash"
    url = "https://dashscope.aliyuncs.com/api/v1/services/aigc/text2image/image-synthesis"
    headers = {
        "Authorization": f"Bearer {cfg.api_key}",
        "Content-Type": "application/json",
        "X-DashScope-Async": "enable",
    }
    body: dict[str, Any] = {
        "model": image_model,
        "input": {"prompt": prompt},
        "parameters": {"size": "768*1344", "n": min(count, 4)},
    }
    if negative_prompt.strip():
        body["input"]["negative_prompt"] = negative_prompt.strip()

    request_timeout = httpx.Timeout(30.0, read=max(cfg.timeout_seconds, 120.0))
    with httpx.Client(timeout=request_timeout) as client:
        create = client.post(url, headers=headers, json=body)
        if create.status_code >= 400:
            raise AIMediaError(create.text[:300])
        task_id = create.json().get("output", {}).get("task_id")
        if not task_id:
            raise AIMediaError("DashScope 未返回 task_id")

        status_url = f"https://dashscope.aliyuncs.com/api/v1/tasks/{task_id}"
        image_timeout = float(os.environ.get("AIPC_IMAGE_TIMEOUT", "600"))
        deadline = time.time() + max(cfg.timeout_seconds, image_timeout)
        poll_interval = 3.0
        while time.time() < deadline:
            time.sleep(poll_interval)
            poll = client.get(
                status_url,
                headers={"Authorization": f"Bearer {cfg.api_key}"},
                timeout=30.0,
            )
            if poll.status_code >= 400:
                raise AIMediaError(poll.text[:300])
            output = poll.json().get("output", {})
            status = output.get("task_status")
            if status == "SUCCEEDED":
                results = output.get("results") or []
                return _download_image_urls(results, output_dir, client)
            if status in {"FAILED", "CANCELED"}:
                raise AIMediaError(str(output.get("message") or "图像任务失败"))
    raise AIMediaError("图像生成超时，百炼绘图通常需 2–5 分钟，请稍后重试或增大 AIPC_IMAGE_TIMEOUT")


def _try_openai_video(
    prompt: str,
    output_mp4: Path,
    cfg: LLMConfig,
    reference_image: Path,
    video_size: str,
    seconds: str,
) -> Path:
    root = _api_root(cfg.base_url)
    headers = {"Authorization": f"Bearer {cfg.api_key}"}
    create_url = f"{root}/videos"
    files = {
        "input_reference": (reference_image.name, reference_image.read_bytes(), "image/png"),
    }
    data = {
        "model": _video_model(cfg),
        "prompt": prompt,
        "size": video_size,
        "seconds": seconds,
    }
    timeout = _video_timeout(cfg)
    with httpx.Client(timeout=timeout) as client:
        create = client.post(create_url, headers=headers, data=data, files=files)
        if create.status_code >= 400:
            raise AIMediaError(create.text[:300])
        job = create.json()
        video_id = job.get("id")
        if not video_id:
            raise AIMediaError("视频任务未返回 id")

        status_url = f"{root}/videos/{video_id}"
        deadline = time.time() + timeout
        while time.time() < deadline:
            poll = client.get(status_url, headers=headers, timeout=30.0)
            if poll.status_code >= 400:
                raise AIMediaError(poll.text[:300])
            payload = poll.json()
            status = payload.get("status")
            if status == "completed":
                break
            if status == "failed":
                error = payload.get("error") or {}
                raise AIMediaError(str(error.get("message") or "视频生成失败"))
            time.sleep(3)
        else:
            raise AIMediaError("视频生成超时")

        content = client.get(f"{status_url}/content", headers=headers, timeout=120.0)
        if content.status_code >= 400:
            raise AIMediaError(content.text[:300])
        output_mp4.parent.mkdir(parents=True, exist_ok=True)
        output_mp4.write_bytes(content.content)
        if not output_mp4.is_file():
            raise AIMediaError("视频文件未生成")
        return output_mp4.resolve()


def _download_image_urls(
    results: list[dict[str, Any]],
    output_dir: Path,
    client: httpx.Client,
) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for index, item in enumerate(results, start=1):
        url = item.get("url")
        if not url:
            continue
        response = client.get(url)
        if response.status_code >= 400:
            continue
        suffix = ".png" if "png" in response.headers.get("content-type", "") else ".jpg"
        target = output_dir / f"ai_{index:02d}{suffix}"
        target.write_bytes(response.content)
        saved.append(target.resolve())
    if not saved:
        raise AIMediaError("未下载到生成图片")
    return saved
