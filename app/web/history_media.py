from __future__ import annotations

import shutil
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse, parse_qs

from app.web.history_store import get_history_item

_IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif"}
HISTORY_ASSETS_DIR = "history_assets"


def history_media_url(task_id: str, relative_path: str) -> str:
    cleaned = relative_path.replace("\\", "/").lstrip("/")
    return (
        f"/api/history-media?task_id={quote(task_id, safe='')}"
        f"&file={quote(cleaned, safe='')}"
    )


def history_assets_root(workspace: Path) -> Path:
    root = (workspace / HISTORY_ASSETS_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def product_media_url(product_id: str, relative_path: str) -> str:
    cleaned = relative_path.replace("\\", "/").lstrip("/")
    return (
        f"/api/product-media?product_id={quote(product_id, safe='')}"
        f"&file={quote(cleaned, safe='')}"
    )


def _legacy_path_from_url(url: str | None, record: dict[str, Any] | None = None) -> Path | None:
    if not url:
        return None
    raw = str(url).strip()
    if not raw:
        return None
    if raw.startswith("/api/media-file"):
        parsed = urlparse(raw)
        values = parse_qs(parsed.query).get("path") or []
        if values:
            return Path(unquote(values[0]))
    if raw.startswith("/api/package-media") and record:
        parsed = urlparse(raw)
        values = parse_qs(parsed.query).get("file") or []
        if values:
            rel = unquote(values[0]).replace("\\", "/").lstrip("/")
            package_root = record.get("package_root")
            if package_root:
                root = Path(str(package_root))
                for candidate in (root / "media" / rel, root / rel):
                    if candidate.is_file():
                        return candidate
    if raw.startswith("/api/history-media") and record:
        parsed = urlparse(raw)
        query = parse_qs(parsed.query)
        task_values = query.get("task_id") or []
        file_values = query.get("file") or []
        task_id = task_values[0] if task_values else str(record.get("id") or "")
        rel = file_values[0] if file_values else ""
        if task_id and rel:
            stored = history_assets_root(Path(record.get("_workspace") or ".")) / task_id / unquote(rel)
            if stored.is_file():
                return stored
    candidate = Path(raw)
    if candidate.is_file():
        return candidate
    return None


def _image_relative_path(image: dict[str, Any]) -> str:
    relative = str(image.get("relative_path") or "").strip()
    if not relative and image.get("name"):
        relative = f"generated_images/{image['name']}"
    return relative.replace("\\", "/").lstrip("/")


def _find_media_source(workspace: Path, item: dict[str, Any], relative: str) -> Path | None:
    for candidate in _candidate_media_paths(workspace, item, relative):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and resolved.suffix.lower() in _IMAGE_SUFFIXES:
            return resolved
    return None


def _candidate_media_paths(workspace: Path, item: dict[str, Any], rel: str) -> list[Path]:
    rel_path = rel.replace("\\", "/").lstrip("/")
    candidates: list[Path] = []

    task_id = str(item.get("id") or "").strip()
    if task_id:
        candidates.append(history_assets_root(workspace) / task_id / rel_path)

    package_root = item.get("package_root")
    if package_root:
        root = Path(str(package_root))
        candidates.extend(
            [
                root / "media" / rel_path,
                root / rel_path,
            ]
        )

    export_folder = item.get("export_folder")
    if export_folder:
        export = Path(str(export_folder))
        candidates.extend(
            [
                export / "media" / rel_path,
                export / rel_path,
            ]
        )

    product_id = item.get("product_id")
    if product_id:
        from app.web.creative_archive import GENERATED_IMAGE_SUBDIR
        from app.web.product_store import PRODUCT_FILES_DIR
        from app.web.reference_files import IMAGE_SUBDIR

        product_root = workspace / str(product_id)
        stored = (
            product_root
            / PRODUCT_FILES_DIR
            / IMAGE_SUBDIR
            / GENERATED_IMAGE_SUBDIR
            / Path(rel_path).name
        )
        candidates.append(stored)

    for image in item.get("generated_images") or []:
        if not isinstance(image, dict):
            continue
        legacy = _legacy_path_from_url(image.get("url"), item)
        if legacy is not None:
            candidates.append(legacy)

    media = item.get("generated_media")
    if isinstance(media, dict):
        for image in media.get("images") or []:
            if not isinstance(image, dict):
                continue
            legacy = _legacy_path_from_url(image.get("url"), item)
            if legacy is not None:
                candidates.append(legacy)

    return candidates


def _validate_product_id(product_id: str) -> str:
    cleaned = (product_id or "").strip()
    if not cleaned or cleaned != Path(cleaned).name:
        raise ValueError("invalid_product_id")
    if ".." in cleaned or not cleaned.startswith("prod_"):
        raise ValueError("invalid_product_id")
    return cleaned


def resolve_product_media_file(workspace: Path, product_id: str, file: str) -> Path:
    product_key = _validate_product_id(product_id)
    rel = file.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel:
        raise ValueError("invalid_relative_path")

    from app.web.product_store import PRODUCT_FILES_DIR

    product_dir = workspace / product_key
    if not product_dir.is_dir():
        raise FileNotFoundError(product_key)

    rel_path = Path(rel)
    candidates = [
        product_dir / rel_path,
        product_dir / PRODUCT_FILES_DIR / rel_path,
    ]
    if rel_path.parts[:1] == ("packages",):
        candidates.insert(0, product_dir / rel_path)

    product_resolved = product_dir.resolve()
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(product_resolved)
        except (OSError, ValueError):
            continue
        if resolved.is_file() and resolved.suffix.lower() in _IMAGE_SUFFIXES:
            return resolved
    raise FileNotFoundError(rel)


def resolve_history_media_file(workspace: Path, task_id: str, file: str) -> Path:
    item = get_history_item(workspace, task_id)
    if item is None:
        raise FileNotFoundError(task_id)
    rel = file.replace("\\", "/").lstrip("/")
    if not rel or ".." in rel:
        raise ValueError("invalid_relative_path")
    for candidate in _candidate_media_paths(workspace, item, rel):
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.is_file() and resolved.suffix.lower() in _IMAGE_SUFFIXES:
            return resolved
    raise FileNotFoundError(rel)


def attach_history_media_urls(record: dict[str, Any]) -> dict[str, Any]:
    task_id = str(record.get("id") or "").strip()
    if not task_id:
        return record

    def fix_media_block(block: dict[str, Any]) -> dict[str, Any]:
        updated = dict(block)
        images = updated.get("images")
        if isinstance(images, list):
            updated["images"] = [fix_image(image) for image in images if isinstance(image, dict)]
        video = updated.get("video")
        if isinstance(video, dict):
            updated["video"] = fix_video(video)
        return updated

    def fix_image(image: dict[str, Any]) -> dict[str, Any]:
        relative = str(image.get("relative_path") or "").strip()
        if not relative and image.get("name"):
            relative = f"generated_images/{image['name']}"
        if not relative:
            return image
        return {
            **image,
            "relative_path": relative.replace("\\", "/"),
            "url": history_media_url(task_id, relative),
        }

    def fix_video(video: dict[str, Any]) -> dict[str, Any]:
        relative = str(video.get("relative_path") or "").strip()
        if not relative and video.get("name"):
            relative = video["name"]
        if not relative:
            return video
        return {
            **video,
            "relative_path": relative.replace("\\", "/"),
            "url": history_media_url(task_id, relative),
        }

    payload = dict(record)
    media = payload.get("generated_media")
    if isinstance(media, dict):
        payload["generated_media"] = fix_media_block(media)
    images = payload.get("generated_images")
    if isinstance(images, list):
        payload["generated_images"] = [
            fix_image(image) for image in images if isinstance(image, dict)
        ]
    video = payload.get("generated_video")
    if isinstance(video, dict):
        payload["generated_video"] = fix_video(video)
    return payload


def _persist_image_file(
    workspace: Path,
    task_id: str,
    image: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, Any]:
    relative = _image_relative_path(image)
    if not relative:
        return image

    destination = history_assets_root(workspace) / task_id / relative
    if not destination.is_file():
        source = _find_media_source(workspace, record, relative)
        if source is None:
            return image
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)

    return {
        **image,
        "relative_path": relative,
        "stored_in_history": True,
        "size_bytes": destination.stat().st_size if destination.is_file() else image.get("size_bytes", 0),
    }


def persist_history_media_assets(workspace: Path, record: dict[str, Any]) -> dict[str, Any]:
    """把生成图片复制到 history_assets，避免内容包删除后历史记录无法预览。"""
    task_id = str(record.get("id") or "").strip()
    if not task_id:
        return record

    payload = dict(record)

    def persist_images(images: list[Any]) -> list[dict[str, Any]]:
        updated: list[dict[str, Any]] = []
        for image in images:
            if not isinstance(image, dict):
                continue
            updated.append(_persist_image_file(workspace, task_id, image, payload))
        return updated

    media = payload.get("generated_media")
    if isinstance(media, dict):
        images = media.get("images")
        if isinstance(images, list):
            media = {**media, "images": persist_images(images)}
        payload["generated_media"] = media

    images = payload.get("generated_images")
    if isinstance(images, list):
        payload["generated_images"] = persist_images(images)

    return payload
