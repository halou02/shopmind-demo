from __future__ import annotations

import json
import os
import re
import shutil
from pathlib import Path
from typing import Any

from app.web.export_download import build_export_zip, list_export_files
from app.web.material_library_store import record_library_files
from app.web.product_summary import build_product_summary, save_product_summary
from app.web.reference_files import (
    IMAGE_SUBDIR,
    REFERENCE_SUBDIR,
    copy_reference_files_into_material,
    ensure_material_library_root,
    is_reference_file,
)
from app.web.run_meta import write_run_meta

CREATIVE_EXPORT_DIR = "创意导出"
GENERATED_IMAGE_SUBDIR = "创意生成"


def _safe_folder_name(name: str) -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\s]+', "_", name.strip())
    return cleaned[:48] or "商品"


def ensure_creative_export_root(workspace: Path) -> Path:
    root = (workspace / CREATIVE_EXPORT_DIR).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_creative_exports(workspace: Path, limit: int = 50) -> list[dict[str, Any]]:
    export_root = ensure_creative_export_root(workspace)
    items: list[dict[str, Any]] = []
    for path in sorted(export_root.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True):
        if path.name.startswith("."):
            continue
        if path.is_dir():
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "kind": "folder",
                    "updated_at": path.stat().st_mtime,
                    "file_count": sum(1 for child in path.rglob("*") if child.is_file()),
                }
            )
        elif path.suffix.lower() == ".zip":
            items.append(
                {
                    "name": path.name,
                    "path": str(path),
                    "kind": "zip",
                    "updated_at": path.stat().st_mtime,
                    "size_bytes": path.stat().st_size,
                }
            )
        if len(items) >= limit:
            break
    return items


def export_package_to_creative_folder(
    workspace: Path,
    package_root: Path,
    *,
    product_name: str,
    package_id: str,
) -> dict[str, Any]:
    if not package_root.is_dir():
        raise FileNotFoundError(str(package_root))

    export_root = ensure_creative_export_root(workspace)
    folder_name = f"{_safe_folder_name(product_name)}_{package_id}"
    destination = export_root / folder_name
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(package_root, destination)

    zip_path = export_root / f"{folder_name}.zip"
    if zip_path.exists():
        zip_path.unlink()
    archive_base = zip_path.with_suffix("")
    shutil.make_archive(str(archive_base), "zip", root_dir=destination)
    if not zip_path.is_file():
        zip_path = build_export_zip(destination)

    return {
        "export_root": str(export_root),
        "folder_path": str(destination),
        "zip_path": str(zip_path),
        "folder_name": folder_name,
        "files": list_export_files(destination),
    }


def save_product_to_library(
    workspace: Path,
    *,
    product_id: str,
    material_path: str,
    product_name: str,
    product_form: dict[str, Any] | None,
    understanding: dict[str, Any] | None,
    judgment: dict[str, Any] | None,
    directions: dict[str, Any] | None,
    package_id: str | None,
    export_folder: str | None,
) -> dict[str, Any]:
    product_dir = workspace / product_id
    product_dir.mkdir(parents=True, exist_ok=True)

    summary = build_product_summary(
        product_id=product_id,
        product_dir=product_dir,
        understanding=understanding,
        judgment=judgment,
        directions=directions,
    )
    save_product_summary(product_dir, summary["sections"])

    form = product_form or {}
    write_run_meta(
        workspace,
        product_id,
        material_path=material_path,
        product_name=form.get("product_name") or product_name,
        category=form.get("product_category"),
        brand=form.get("product_brand"),
        price=form.get("product_price"),
        product_form=form,
    )

    meta_path = product_dir / "run_meta.json"
    meta: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            meta = {}
    meta.update(
        {
            "latest_package_id": package_id,
            "latest_export_folder": export_folder,
            "library_saved_at": summary.get("updated_at"),
            "status": "completed",
        }
    )
    temporary = meta_path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, meta_path)

    return {
        "product_id": product_id,
        "product_dir": str(product_dir),
        "summary_sections": len(summary.get("sections") or []),
        "has_generated_content": summary.get("has_generated_content"),
    }


def _normalize_relative_path(path: str) -> str:
    return str(path or "").replace("\\", "/").strip().lstrip("/")


def _collect_used_material_sources(
    material_path: Path,
    reference_paths: list[str],
    package_root: Path | None,
    exclude_relative_paths: set[str] | None = None,
) -> list[Path]:
    sources: list[Path] = []
    seen: set[str] = set()
    excluded = {_normalize_relative_path(path) for path in (exclude_relative_paths or set()) if path}

    def is_excluded_task_file(path: Path, material_resolved: Path) -> bool:
        if not excluded:
            return False
        try:
            relative = _normalize_relative_path(path.resolve().relative_to(material_resolved).as_posix())
        except ValueError:
            return False
        return relative in excluded

    def add(path: Path, *, material_resolved: Path | None = None) -> None:
        if not path.is_file() or not is_reference_file(path.name):
            return
        if material_resolved is not None and is_excluded_task_file(path, material_resolved):
            return
        key = str(path.resolve())
        if key in seen:
            return
        seen.add(key)
        sources.append(path.resolve())

    material_resolved = material_path.resolve()
    for relative_path in reference_paths:
        add(material_resolved / relative_path, material_resolved=material_resolved)

    if material_resolved.is_dir():
        for path in material_resolved.rglob("*"):
            if path.is_file():
                add(path, material_resolved=material_resolved)

    if package_root and package_root.is_dir():
        media_dir = package_root / "media"
        if media_dir.is_dir():
            for path in media_dir.rglob("*"):
                if path.is_file() and is_reference_file(path.name):
                    add(path)

    return sources


def _copy_generated_images_to_target(generated_dir: Path, target_dir: Path) -> list[Path]:
    if not generated_dir.is_dir():
        return []
    target_dir.mkdir(parents=True, exist_ok=True)
    copied: list[Path] = []
    for source in sorted(generated_dir.rglob("*")):
        if not source.is_file() or source.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            continue
        destination = target_dir / source.name
        if destination.exists():
            stem = destination.stem
            suffix = destination.suffix
            index = 1
            while destination.exists():
                destination = target_dir / f"{stem}_{index}{suffix}"
                index += 1
        shutil.copy2(source, destination)
        copied.append(destination.resolve())
    return copied


def _copy_generated_images_to_library(library_root: Path, package_root: Path) -> list[Path]:
    generated_dir = package_root / "media" / "generated_images"
    if not generated_dir.is_dir():
        return []

    target_dir = library_root / IMAGE_SUBDIR / GENERATED_IMAGE_SUBDIR
    return _copy_generated_images_to_target(generated_dir, target_dir)


def sync_generated_media_to_stores(
    workspace: Path,
    *,
    package_root: Path,
    product_id: str | None,
    material_path: str | None,
    reference_paths: list[str],
    export_folder: str | None = None,
    exclude_relative_paths: list[str] | None = None,
) -> dict[str, Any]:
    """图片在文案导出之后才生成时，把 AI 图同步到导出目录、商品库与素材库。"""
    generated_dir = package_root / "media" / "generated_images"
    if not generated_dir.is_dir():
        return {"synced_images": 0, "product_images": 0, "library_images": 0}

    export_count = 0
    if export_folder:
        export_target = Path(export_folder) / "media" / "generated_images"
        export_count = len(_copy_generated_images_to_target(generated_dir, export_target))

    product_count = 0
    if product_id:
        from app.web.product_store import PRODUCT_FILES_DIR, ensure_product_files_root

        product_dir = workspace / product_id
        ensure_product_files_root(product_dir)
        product_target = product_dir / PRODUCT_FILES_DIR / IMAGE_SUBDIR / GENERATED_IMAGE_SUBDIR
        product_count = len(_copy_generated_images_to_target(generated_dir, product_target))

    library_info = import_used_materials_to_library(
        workspace,
        material_path=material_path,
        reference_paths=reference_paths,
        package_root=package_root,
        exclude_relative_paths=exclude_relative_paths,
    )
    library_count = len(library_info.get("imported_files") or [])
    return {
        "synced_images": max(export_count, product_count, library_count),
        "export_images": export_count,
        "product_images": product_count,
        "library_images": library_count,
        "material_library": library_info,
    }


def import_used_materials_to_library(
    workspace: Path,
    *,
    material_path: str | None,
    reference_paths: list[str],
    package_root: Path | None,
    exclude_relative_paths: list[str] | None = None,
) -> dict[str, Any]:
    library_root = ensure_material_library_root(workspace)
    if not material_path:
        generated_only = (
            _copy_generated_images_to_library(library_root, package_root)
            if package_root
            else []
        )
        recorded = record_library_files(library_root, generated_only, source="creative_task")
        return {
            "library_root": str(library_root),
            "imported_count": len(recorded),
            "imported_files": [item["name"] for item in recorded],
            "categories": sorted({item["category"] for item in recorded}),
        }

    material_root = Path(material_path).expanduser()
    if not material_root.is_absolute():
        material_root = (Path.cwd() / material_root).resolve()
    if not material_root.is_dir():
        raise FileNotFoundError(str(material_root))

    excluded = {_normalize_relative_path(path) for path in (exclude_relative_paths or []) if path}
    sources = _collect_used_material_sources(
        material_root,
        reference_paths,
        package_root,
        exclude_relative_paths=excluded,
    )
    copied = copy_reference_files_into_material(library_root, sources)
    copied.extend(_copy_generated_images_to_library(library_root, package_root) if package_root else [])
    recorded = record_library_files(library_root, copied, source="creative_task")

    by_category: dict[str, int] = {}
    for item in recorded:
        category = str(item.get("category") or "reference")
        by_category[category] = by_category.get(category, 0) + 1

    return {
        "library_root": str(library_root),
        "imported_count": len(recorded),
        "imported_files": [item["name"] for item in recorded[:20]],
        "categories": by_category,
        "image_dir": str(library_root / IMAGE_SUBDIR),
        "reference_dir": str(library_root / REFERENCE_SUBDIR),
    }


def finalize_creative_task(
    workspace: Path,
    *,
    product_id: str,
    product_name: str,
    material_path: str | None,
    reference_paths: list[str],
    product_form: dict[str, Any] | None,
    understanding: dict[str, Any] | None,
    judgment: dict[str, Any] | None,
    directions: dict[str, Any] | None,
    package_root: Path,
    package_id: str,
    exclude_relative_paths: list[str] | None = None,
) -> dict[str, Any]:
    export_info = export_package_to_creative_folder(
        workspace,
        package_root,
        product_name=product_name,
        package_id=package_id,
    )
    product_info = save_product_to_library(
        workspace,
        product_id=product_id,
        material_path=material_path or "",
        product_name=product_name,
        product_form=product_form,
        understanding=understanding,
        judgment=judgment,
        directions=directions,
        package_id=package_id,
        export_folder=export_info["folder_path"],
    )
    material_info = import_used_materials_to_library(
        workspace,
        material_path=material_path,
        reference_paths=reference_paths,
        package_root=package_root,
        exclude_relative_paths=exclude_relative_paths,
    )
    return {
        "export": export_info,
        "product_library": product_info,
        "material_library": material_info,
    }
