from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.web.image_upload import IMAGE_SUFFIXES, _existing_images


@dataclass(frozen=True)
class ImagePromptItem:
    title: str
    scene: str
    prompt: str
    negative_prompt: str
    aspect_ratio: str

    def to_dict(self) -> dict[str, str]:
        return {
            "title": self.title,
            "scene": self.scene,
            "prompt": self.prompt,
            "negative_prompt": self.negative_prompt,
            "aspect_ratio": self.aspect_ratio,
        }


def ffmpeg_available() -> bool:
    try:
        subprocess.run(
            ["ffmpeg", "-version"],
            capture_output=True,
            check=True,
            timeout=5,
        )
        return True
    except (FileNotFoundError, subprocess.CalledProcessError, OSError):
        return False


def resolve_video_images(material_root: Path, relative_paths: list[str], limit: int = 8) -> list[Path]:
    images: list[Path] = []
    if relative_paths:
        for relative in relative_paths[:limit]:
            candidate = (material_root / relative).resolve()
            if candidate.is_file() and candidate.suffix.lower() in IMAGE_SUFFIXES:
                images.append(candidate)
    if images:
        return images
    return _existing_images(material_root)[:limit]


def build_image_prompts(
    *,
    product_name: str,
    direction_name: str,
    main_selling_point: str,
    scenes: list[str],
    avoid_expression: str,
    brand: str,
    image_names: list[str],
    visual_hint: str = "",
) -> list[ImagePromptItem]:
    prompts: list[ImagePromptItem] = []
    scene_list = scenes or ["日常使用场景"]
    sources = image_names or [f"场景{i + 1}" for i in range(len(scene_list))]
    for index, scene in enumerate(scene_list[:6]):
        ref = sources[min(index, len(sources) - 1)]
        prompts.append(
            ImagePromptItem(
                title=f"参考图 {index + 1}",
                scene=scene,
                prompt=(
                    f"电商商品摄影，{product_name}，品牌 {brand}，场景：{scene}，"
                    f"卖点：{main_selling_point}，"
                    f"{'AI分析视觉：' + visual_hint + '，' if visual_hint else ''}"
                    f"参考素材：{ref}，"
                    f"柔光棚拍，细节清晰，真实质感，9:16 竖图，留白适合文案"
                ),
                negative_prompt=f"{avoid_expression}，过度磨皮，夸张失真，低清晰度",
                aspect_ratio="9:16",
            )
        )
    prompts.append(
        ImagePromptItem(
            title="主视觉 KV",
            scene=direction_name,
            prompt=(
                f"电商 KV 主视觉，{product_name}，创意方向「{direction_name}」，"
                f"核心卖点 {main_selling_point}，干净背景，品牌感强，适合小红书封面"
            ),
            negative_prompt=f"{avoid_expression}，杂乱背景，低质感",
            aspect_ratio="3:4",
        )
    )
    return prompts


def save_image_prompts(output_dir: Path, prompts: list[ImagePromptItem]) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    json_path = output_dir / "image_prompts.json"
    md_path = output_dir / "image_prompts.md"
    payload = [item.to_dict() for item in prompts]
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = ["# 图片生成提示词", ""]
    for item in prompts:
        lines.extend(
            [
                f"## {item.title}",
                f"- 场景：{item.scene}",
                f"- 画幅：{item.aspect_ratio}",
                "",
                "**正向提示词**",
                item.prompt,
                "",
                "**负向提示词**",
                item.negative_prompt,
                "",
            ]
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")
    return json_path


def generate_slideshow_video(
    image_paths: list[Path],
    output_mp4: Path,
    *,
    caption: str = "",
) -> Path:
    if not image_paths:
        raise ValueError("没有可用的参考图片，无法生成视频")
    if not ffmpeg_available():
        raise RuntimeError("未检测到 ffmpeg，请先安装 ffmpeg 后再生成视频")

    output_mp4.parent.mkdir(parents=True, exist_ok=True)
    list_file = output_mp4.with_suffix(".ffmpeg.txt")
    lines: list[str] = []
    for image in image_paths:
        escaped = str(image.resolve()).replace("'", "'\\''")
        lines.append(f"file '{escaped}'")
        lines.append("duration 2.5")
    lines.append(f"file '{str(image_paths[-1].resolve()).replace(chr(39), chr(39)+chr(92)+chr(39)+chr(39))}'")
    list_file.write_text("\n".join(lines), encoding="utf-8")

    vf = (
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2:color=white"
    )
    safe_caption = caption.replace(":", "\\:").replace("'", "\\'")[:80]
    if safe_caption:
        vf += f",drawtext=text='{safe_caption}':fontsize=36:fontcolor=white:x=(w-text_w)/2:y=h-120:box=1:boxcolor=black@0.45"

    command = [
        "ffmpeg",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(list_file),
        "-vf",
        vf,
        "-pix_fmt",
        "yuv420p",
        str(output_mp4),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    list_file.unlink(missing_ok=True)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "ffmpeg failed").strip()[-400:]
        raise RuntimeError(f"视频生成失败：{detail}")
    if not output_mp4.is_file():
        raise RuntimeError("视频文件未生成")
    return output_mp4.resolve()


def save_generated_images(image_paths: list[Path], output_dir: Path) -> list[Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    saved: list[Path] = []
    for index, image in enumerate(image_paths, start=1):
        target = output_dir / f"{index:02d}_{image.name}"
        shutil.copy2(image, target)
        saved.append(target.resolve())
    return saved


def save_storyboard_fallback(image_paths: list[Path], output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    for index, image in enumerate(image_paths, start=1):
        target = output_dir / f"{index:02d}_{image.name}"
        shutil.copy2(image, target)
        copied.append(target.name)
    readme = output_dir / "README.txt"
    readme.write_text(
        "未安装 ffmpeg，已保存分镜图片。\n"
        "安装 ffmpeg 后可在页面重新点击「生成视频」。\n"
        f"图片数量：{len(copied)}\n",
        encoding="utf-8",
    )
    return output_dir.resolve()


def media_payload(path: Path, media_root: Path) -> dict[str, Any]:
    from urllib.parse import quote

    relative = path.resolve().relative_to(media_root.resolve()).as_posix()
    return {
        "name": path.name,
        "relative_path": relative,
        "url": f"/api/package-media?file={quote(relative, safe='')}",
        "size_bytes": path.stat().st_size if path.is_file() else 0,
    }
