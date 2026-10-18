from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.core.source_index import product_id_of
from app.web.catalog import categorize_file
from app.web.product_summary import SECTION_SPECS, save_product_summary
from app.web.creative_archive import GENERATED_IMAGE_SUBDIR
from app.web.history_media import product_media_url
from app.web.reference_files import (
    IMAGE_SUBDIR,
    REFERENCE_SUBDIR,
    is_reference_file,
    save_reference_file,
)
from app.web.product_categories import normalize_category_key, resolve_product_category_fields
from app.web.run_meta import write_run_meta

PROFILE_FILE_NAME = "product_profile.json"
PRODUCT_FILES_DIR = "商品素材"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".json.tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def ensure_product_files_root(product_dir: Path) -> Path:
    root = (product_dir / PRODUCT_FILES_DIR).resolve()
    (root / IMAGE_SUBDIR).mkdir(parents=True, exist_ok=True)
    (root / REFERENCE_SUBDIR).mkdir(parents=True, exist_ok=True)
    return root


def default_profile(product_name: str = "") -> dict[str, Any]:
    return {
        "product_name": product_name,
        "product_category": "",
        "product_category_key": "",
        "product_price": "",
        "product_brand": "",
        "product_desc": "",
        "user_notes": "",
        "updated_at": _now_iso(),
    }


def load_product_profile(product_dir: Path) -> dict[str, Any]:
    profile = _read_json(product_dir / PROFILE_FILE_NAME)
    if profile:
        return profile
    run_meta = _read_json(product_dir / "run_meta.json") or {}
    form = run_meta.get("product_form") or {}
    return {
        "product_name": run_meta.get("product_name") or form.get("product_name") or "",
        "product_category": run_meta.get("category") or form.get("product_category") or "",
        "product_category_key": form.get("product_category_key")
        or normalize_category_key(run_meta.get("category") or form.get("product_category")),
        "product_price": str(run_meta.get("price") or form.get("product_price") or ""),
        "product_brand": run_meta.get("brand") or form.get("product_brand") or "",
        "product_desc": form.get("product_desc") or "",
        "user_notes": "",
        "updated_at": None,
    }


def save_product_profile(product_dir: Path, profile: dict[str, Any]) -> dict[str, Any]:
    cat_key = str(profile.get("product_category_key") or "").strip()
    cat_label = str(profile.get("product_category") or "").strip()
    if not cat_key:
        cat_key, cat_label = resolve_product_category_fields(
            {"product_category": cat_label, "product_category_key": profile.get("product_category_key")}
        )
    elif cat_key == "custom" and not cat_label:
        cat_label = "自定义"
    elif cat_key != "custom" and not cat_label:
        _, cat_label = resolve_product_category_fields({"product_category_key": cat_key})
    normalized = {
        "product_name": str(profile.get("product_name") or "").strip(),
        "product_category": cat_label,
        "product_category_key": cat_key if cat_key != "all" else normalize_category_key(cat_label),
        "product_price": str(profile.get("product_price") or "").strip(),
        "product_brand": str(profile.get("product_brand") or "").strip(),
        "product_desc": str(profile.get("product_desc") or "").strip(),
        "user_notes": str(profile.get("user_notes") or "").strip(),
        "updated_at": _now_iso(),
    }
    _write_json(product_dir / PROFILE_FILE_NAME, normalized)
    existing_meta = _read_json(product_dir / "run_meta.json") or {}
    write_run_meta(
        product_dir.parent,
        product_dir.name,
        material_path=str(existing_meta.get("material_path") or ""),
        product_name=normalized["product_name"] or product_dir.name,
        category=normalized["product_category"] or None,
        brand=normalized["product_brand"] or None,
        price=normalized["product_price"] or None,
        product_form={
            "product_name": normalized["product_name"],
            "product_category": normalized["product_category"],
            "product_category_key": normalized["product_category_key"],
            "product_price": normalized["product_price"],
            "product_brand": normalized["product_brand"],
            "product_desc": normalized["product_desc"],
        },
    )
    return normalized


def create_product(workspace: Path, form: dict[str, Any]) -> dict[str, Any]:
    name = str(form.get("product_name") or "").strip()
    if not name:
        raise ValueError("请填写商品名称")
    product_id = product_id_of(name)
    product_dir = workspace / product_id
    if product_dir.exists():
        raise ValueError(f"商品「{name}」已存在于商品库")

    product_dir.mkdir(parents=True, exist_ok=True)
    ensure_product_files_root(product_dir)
    profile = save_product_profile(
        product_dir,
        {
            **default_profile(name),
            "product_category": str(form.get("product_category") or "").strip(),
            "product_category_key": str(form.get("product_category_key") or "").strip(),
            "product_price": str(form.get("product_price") or "").strip(),
            "product_brand": str(form.get("product_brand") or "").strip(),
            "product_desc": str(form.get("product_desc") or "").strip(),
        },
    )
    empty_sections = [{"id": section_id, "title": title, "body": ""} for section_id, title in SECTION_SPECS]
    save_product_summary(product_dir, empty_sections)
    return {"product_id": product_id, "profile": profile}


def list_product_generated_images(product_dir: Path, product_id: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_image(path: Path, relative_path: str) -> None:
        key = path.name.lower()
        if key in seen or not path.is_file():
            return
        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp", ".gif"}:
            return
        seen.add(key)
        items.append(
            {
                "name": path.name,
                "relative_path": relative_path.replace("\\", "/"),
                "url": product_media_url(product_id, relative_path),
                "size_bytes": path.stat().st_size,
                "source": "ai_generated",
            }
        )

    stored_root = product_dir / PRODUCT_FILES_DIR / IMAGE_SUBDIR / GENERATED_IMAGE_SUBDIR
    if stored_root.is_dir():
        for path in sorted(stored_root.rglob("*")):
            if path.is_file():
                add_image(path, f"{PRODUCT_FILES_DIR}/{IMAGE_SUBDIR}/{GENERATED_IMAGE_SUBDIR}/{path.name}")

    packages_root = product_dir / "packages"
    if packages_root.is_dir():
        for path in sorted(packages_root.glob("*/media/generated_images/*")):
            if path.is_file():
                package_id = path.parent.parent.parent.name
                add_image(
                    path,
                    f"packages/{package_id}/media/generated_images/{path.name}",
                )

    return items


def list_product_files(product_dir: Path) -> list[dict[str, Any]]:
    files_root = product_dir / PRODUCT_FILES_DIR
    if not files_root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(files_root.rglob("*")):
        if not path.is_file() or path.name.startswith("."):
            continue
        if not is_reference_file(path.name):
            continue
        relative_path = path.relative_to(files_root).as_posix()
        items.append(
            {
                "name": path.name,
                "relative_path": relative_path,
                "category": categorize_file(path, files_root),
                "size_bytes": path.stat().st_size,
                "absolute_path": str(path.resolve()),
            }
        )
    return items


def save_product_file(product_dir: Path, filename: str, content: bytes) -> Path:
    files_root = ensure_product_files_root(product_dir)
    return save_reference_file(files_root, filename, content)


def delete_product_file(product_dir: Path, relative_path: str) -> Path:
    files_root = (product_dir / PRODUCT_FILES_DIR).resolve()
    candidate = (files_root / relative_path).resolve()
    try:
        candidate.relative_to(files_root)
    except ValueError as exc:
        raise ValueError("非法文件路径") from exc
    if not candidate.is_file():
        raise ValueError("文件不存在或已被删除")
    candidate.unlink(missing_ok=True)
    return candidate
