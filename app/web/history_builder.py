from __future__ import annotations

from pathlib import Path
from typing import Any

from app.ui.pipeline_runner import resolve_material_root
from app.web.catalog import scan_material_catalog
from app.web.reference_files import is_reference_file

CHANNEL_LABELS = {
    "xiaohongshu": "小红书",
    "douyin": "抖音",
    "kuaishou": "快手",
    "shopping": "购物平台",
    "visual": "视觉方案",
    "video": "视频脚本",
}

CATEGORY_LABELS = {
    "image": "图片",
    "video": "视频",
    "copy": "文案",
    "brand": "品牌",
    "reference": "参考",
}


def _truncate(text: str, limit: int = 320) -> str:
    cleaned = str(text or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[:limit] + "…"


def collect_used_files(
    material_path: str | None,
    reference_paths: list[str],
) -> list[dict[str, Any]]:
    if not material_path:
        return []
    root = resolve_material_root(material_path)
    if not root.is_dir():
        return []

    reference_set = set(reference_paths)
    files: list[dict[str, Any]] = []
    for item in scan_material_catalog(root):
        if not is_reference_file(item.name):
            continue
        files.append(
            {
                "name": item.name,
                "relative_path": item.relative_path,
                "category": item.category,
                "category_label": CATEGORY_LABELS.get(item.category, item.category),
                "url": f"/api/material-file?path={item.absolute_path.as_posix()}",
                "is_reference_image": item.relative_path in reference_set,
                "size_bytes": item.size_bytes,
            }
        )
    return files


def collect_generated_text(content: Any | None) -> list[dict[str, Any]]:
    if content is None:
        return []
    items: list[dict[str, Any]] = []
    for item in content.items:
        body = str(item.body or "").strip()
        items.append(
            {
                "channel": item.channel,
                "label": CHANNEL_LABELS.get(item.channel, item.channel),
                "title": str(item.title or "").strip(),
                "body": body,
                "preview": _truncate(body),
            }
        )
    return items


def normalize_generated_media(generated_media: dict[str, Any] | None) -> dict[str, Any]:
    payload = dict(generated_media or {})
    images = payload.get("images") or []
    if isinstance(images, list):
        payload["images"] = images
    video = payload.get("video")
    if isinstance(video, dict):
        payload["video"] = video
    storyboard = payload.get("storyboard")
    if isinstance(storyboard, dict):
        payload["storyboard"] = storyboard
    return payload


def build_history_record(
    *,
    task_id: str | None,
    product_name: str,
    product_category: str | None,
    product_id: str | None,
    direction_id: str | None,
    direction_name: str | None,
    main_selling_point: str | None,
    material_path: str | None,
    reference_paths: list[str],
    package_root: str | None,
    package_id: str | None,
    export_folder: str | None,
    content: Any | None,
    image_prompts: list[dict[str, Any]],
    generated_media: dict[str, Any] | None,
    creative_archive: dict[str, Any] | None = None,
) -> dict[str, Any]:
    used_files = collect_used_files(material_path, reference_paths)
    generated_text = collect_generated_text(content)
    media = normalize_generated_media(generated_media)
    images = media.get("images") or []
    video = media.get("video")
    storyboard = media.get("storyboard")

    return {
        "id": task_id,
        "product_name": product_name,
        "product_category": product_category,
        "product_id": product_id,
        "direction_id": direction_id,
        "direction_name": direction_name,
        "main_selling_point": main_selling_point,
        "material_path": material_path,
        "reference_images": list(reference_paths),
        "used_files": used_files,
        "used_file_count": len(used_files),
        "reference_image_count": len(reference_paths),
        "package_root": package_root,
        "package_id": package_id,
        "export_folder": export_folder,
        "generated_text": generated_text,
        "generated_text_count": len(generated_text),
        "image_prompts": image_prompts,
        "generated_media": media,
        "generated_images": images,
        "generated_video": video,
        "generated_storyboard": storyboard,
        "has_video": bool(video),
        "has_images": bool(images),
        "has_image_prompts": bool(image_prompts),
        "has_storyboard": bool(storyboard),
        "content_summary": {
            "item_count": len(generated_text),
            "channels": [item["channel"] for item in generated_text],
        },
        "creative_archive": creative_archive,
    }
