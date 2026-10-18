from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.web.catalog import LIBRARY_UI_HIDDEN_NAMES, material_stats, scan_material_catalog
from app.web.reference_files import is_reference_file

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def image_payload(material_root: Path, relative_path: str) -> dict[str, str] | None:
    candidate = (material_root / relative_path).resolve()
    material_resolved = material_root.resolve()
    try:
        candidate.relative_to(material_resolved)
    except ValueError:
        return None
    if not candidate.is_file() or candidate.suffix.lower() not in IMAGE_SUFFIXES:
        return None
    return {
        "name": candidate.name,
        "relative_path": relative_path,
        "url": f"/api/material-asset?rel={quote(relative_path, safe='')}",
    }


def auto_confirm_reference_images(
    material_root: Path,
    current_paths: list[str],
    *,
    relative_paths: list[str] | None = None,
) -> list[str]:
    """Keep or replace confirmed reference images. Defaults to all catalog images (max 10)."""
    if relative_paths is None:
        items = scan_material_catalog(material_root)
        relative_paths = [
            item.relative_path for item in items if item.category == "image"
        ][:10]

    validated: list[str] = []
    for relative_path in relative_paths[:10]:
        if image_payload(material_root, relative_path) is not None:
            validated.append(relative_path)

    if validated:
        return validated

    if current_paths:
        kept = [
            path
            for path in current_paths
            if image_payload(material_root, path) is not None
        ]
        if kept:
            return kept[:10]

    return []


def build_material_catalog(
    material_root: Path,
    reference_image_paths: list[str],
    *,
    added_count: int = 0,
    added_files: list[str] | None = None,
) -> dict[str, Any]:
    items = scan_material_catalog(material_root)
    images = [
        {
            "name": item.name,
            "relative_path": item.relative_path,
            "url": f"/api/material-file?path={item.absolute_path.as_posix()}",
        }
        for item in items
        if item.category == "image"
    ]
    reference_files = [
        {
            "name": item.name,
            "relative_path": item.relative_path,
            "url": f"/api/material-file?path={item.absolute_path.as_posix()}",
            "is_image": item.category == "image",
            "category": item.category,
        }
        for item in items
        if is_reference_file(item.name) and item.name not in LIBRARY_UI_HIDDEN_NAMES
    ]
    reference_images = [
        payload
        for rel in reference_image_paths
        if (payload := image_payload(material_root, rel)) is not None
    ]
    materials_ready = bool(reference_files or images)
    payload: dict[str, Any] = {
        "ok": True,
        "material_path": str(material_root),
        "items": [item.to_dict(material_root) for item in items],
        "images": images,
        "reference_files": reference_files,
        "reference_images": reference_images,
        "stats": material_stats(items),
        "materials_ready": materials_ready,
        "images_confirmed": bool(reference_images) or (materials_ready and not images),
        "added_count": added_count,
    }
    if added_files is not None:
        payload["added_files"] = added_files
    return payload
