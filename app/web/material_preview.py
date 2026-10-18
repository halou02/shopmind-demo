from __future__ import annotations

from pathlib import Path

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}


def list_image_files(root: Path, limit: int = 12) -> list[Path]:
    if not root.is_dir():
        return []
    images: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        if path.name.startswith("."):
            continue
        images.append(path.resolve())
        if len(images) >= limit:
            break
    return images


def is_path_under_root(candidate: Path, root: Path) -> bool:
    try:
        candidate.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False
