from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path

from app.ui.pipeline_runner import default_workspace_root

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
MAX_IMAGES = 10
UPLOADS_DIR_NAME = "uploads"


def _safe_filename(name: str) -> str:
    stem = Path(name).name
    cleaned = re.sub(r"[^\w.\-一-龥]", "_", stem, flags=re.UNICODE)
    return cleaned or "image.jpg"


def is_managed_upload_root(material_root: Path, workspace: Path) -> bool:
    uploads_root = (workspace / UPLOADS_DIR_NAME).resolve()
    try:
        material_root.resolve().relative_to(uploads_root)
        return True
    except ValueError:
        return False


def can_write_to_folder(folder: Path) -> bool:
    if not folder.is_dir():
        return False
    probe = folder / ".aipc_write_probe"
    try:
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def resolve_image_staging_root(
    material_root: Path | None,
    workspace: Path,
    *,
    force_new: bool = False,
) -> Path:
    if not force_new and material_root is not None and material_root.is_dir():
        if is_managed_upload_root(material_root, workspace) or can_write_to_folder(material_root):
            (material_root / "商品图片").mkdir(parents=True, exist_ok=True)
            return material_root.resolve()
    return ensure_upload_material_root(material_root, workspace, force_new=True)


def ensure_upload_material_root(
    material_root: Path | None,
    workspace: Path,
    *,
    force_new: bool = False,
) -> Path:
    workspace = workspace.resolve()
    uploads_root = workspace / UPLOADS_DIR_NAME
    uploads_root.mkdir(parents=True, exist_ok=True)

    if (
        not force_new
        and material_root is not None
        and material_root.is_dir()
        and is_managed_upload_root(material_root, workspace)
    ):
        return material_root.resolve()

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    destination = uploads_root / stamp
    (destination / "商品图片").mkdir(parents=True, exist_ok=True)
    return destination.resolve()


def _existing_images(material_root: Path) -> list[Path]:
    images: list[Path] = []
    for path in sorted(material_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if path.name.startswith("."):
            continue
        images.append(path.resolve())
    return images


def copy_images_into_material(material_root: Path, sources: list[Path]) -> list[Path]:
    if not sources:
        return []
    images_dir = material_root / "商品图片"
    images_dir.mkdir(parents=True, exist_ok=True)

    existing = _existing_images(material_root)
    remaining = max(0, MAX_IMAGES - len(existing))
    if remaining <= 0:
        raise ValueError(f"最多只能上传 {MAX_IMAGES} 张图片")

    copied: list[Path] = []
    material_resolved = material_root.resolve()
    for index, source in enumerate(sources[:remaining]):
        if not source.is_file():
            continue
        suffix = source.suffix.lower()
        if suffix not in IMAGE_SUFFIXES:
            continue
        resolved_source = source.resolve()
        try:
            resolved_source.relative_to(material_resolved)
            copied.append(resolved_source)
            continue
        except ValueError:
            pass
        target = images_dir / _safe_filename(source.name)
        if target.exists():
            target = images_dir / f"{target.stem}_{index + 1}{target.suffix}"
        shutil.copy2(source, target)
        copied.append(target.resolve())
    if not copied and sources:
        raise ValueError("未找到可用的图片文件（支持 JPG / PNG / WEBP / GIF / BMP）")
    return copied


def save_uploaded_bytes(material_root: Path, filename: str, content: bytes) -> Path:
    images_dir = material_root / "商品图片"
    images_dir.mkdir(parents=True, exist_ok=True)
    existing = [
        path
        for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_SUFFIXES
    ]
    if len(existing) >= MAX_IMAGES:
        raise ValueError(f"最多只能上传 {MAX_IMAGES} 张图片")

    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError("仅支持 JPG / PNG / WEBP / GIF / BMP 图片")

    target = images_dir / _safe_filename(filename)
    if target.exists():
        target = images_dir / f"{target.stem}_{len(existing) + 1}{target.suffix}"
    target.write_bytes(content)
    return target.resolve()
