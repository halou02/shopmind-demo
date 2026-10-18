from __future__ import annotations

import re
import shutil
from pathlib import Path

from app.web.image_upload import IMAGE_SUFFIXES, MAX_IMAGES

REFERENCE_SUBDIR = "参考资料"
IMAGE_SUBDIR = "商品图片"
MAX_REFERENCE_FILES = 30
MAX_FILE_BYTES = 50 * 1024 * 1024

VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}

REFERENCE_FILE_SUFFIXES = IMAGE_SUFFIXES | VIDEO_SUFFIXES | {
    ".xlsx",
    ".xls",
    ".csv",
    ".txt",
    ".md",
    ".json",
    ".pdf",
    ".doc",
    ".docx",
}

LIBRARY_DIR_NAME = "material_library"


def _safe_filename(name: str) -> str:
    stem = Path(name).name
    cleaned = re.sub(r"[^\w.\-一-龥]", "_", stem, flags=re.UNICODE)
    return cleaned or "file.bin"


def is_reference_file(filename: str) -> bool:
    return Path(filename).suffix.lower() in REFERENCE_FILE_SUFFIXES


def target_subdir_for_file(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return IMAGE_SUBDIR
    if suffix in VIDEO_SUFFIXES:
        return "视频素材"
    return REFERENCE_SUBDIR


def ensure_material_library_root(workspace: Path) -> Path:
    destination = (workspace / LIBRARY_DIR_NAME).resolve()
    (destination / IMAGE_SUBDIR).mkdir(parents=True, exist_ok=True)
    (destination / REFERENCE_SUBDIR).mkdir(parents=True, exist_ok=True)
    return destination


def _count_reference_files(material_root: Path) -> int:
    count = 0
    for path in material_root.rglob("*"):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() in REFERENCE_FILE_SUFFIXES:
            count += 1
    return count


def _unique_target(folder: Path, filename: str) -> Path:
    target = folder / _safe_filename(filename)
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    index = 1
    while True:
        candidate = folder / f"{stem}_{index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def save_reference_file(material_root: Path, filename: str, content: bytes) -> Path:
    if not content:
        raise ValueError("文件内容为空")
    if len(content) > MAX_FILE_BYTES:
        raise ValueError(f"单个文件不能超过 {MAX_FILE_BYTES // (1024 * 1024)} MB")
    if not is_reference_file(filename):
        raise ValueError(
            "不支持的文件格式，可上传图片、Excel、TXT/MD、CSV、JSON、PDF、Word 等"
        )

    if _count_reference_files(material_root) >= MAX_REFERENCE_FILES:
        raise ValueError(f"参考资料最多 {MAX_REFERENCE_FILES} 个文件")

    subdir = target_subdir_for_file(filename)
    folder = material_root / subdir
    folder.mkdir(parents=True, exist_ok=True)
    target = _unique_target(folder, filename)
    target.write_bytes(content)
    return target.resolve()


def copy_reference_files_into_material(material_root: Path, sources: list[Path]) -> list[Path]:
    if not sources:
        return []

    copied: list[Path] = []
    material_resolved = material_root.resolve()
    remaining = max(0, MAX_REFERENCE_FILES - _count_reference_files(material_root))

    for source in sources:
        if remaining <= 0:
            break
        if not source.is_file():
            continue
        if not is_reference_file(source.name):
            continue
        resolved_source = source.resolve()
        try:
            resolved_source.relative_to(material_resolved)
            copied.append(resolved_source)
            remaining -= 1
            continue
        except ValueError:
            pass

        subdir = target_subdir_for_file(source.name)
        folder = material_root / subdir
        folder.mkdir(parents=True, exist_ok=True)
        target = _unique_target(folder, source.name)
        shutil.copy2(source, target)
        copied.append(target.resolve())
        remaining -= 1

    if not copied and sources:
        raise ValueError("未找到可用的参考资料文件")
    return copied


def copy_relative_files_between_roots(
    source_root: Path,
    destination_root: Path,
    relative_paths: list[str],
) -> list[Path]:
    copied: list[Path] = []
    for relative_path in relative_paths:
        source = (source_root / relative_path).resolve()
        if not source.is_file() or not is_reference_file(source.name):
            continue
        try:
            source.relative_to(source_root.resolve())
        except ValueError:
            continue
        subdir = target_subdir_for_file(source.name)
        folder = destination_root / subdir
        folder.mkdir(parents=True, exist_ok=True)
        target = _unique_target(folder, source.name)
        shutil.copy2(source, target)
        copied.append(target.resolve())
    if not copied and relative_paths:
        raise ValueError("未能从素材库导入有效文件")
    return copied
