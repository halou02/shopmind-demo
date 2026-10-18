from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp"}
VIDEO_SUFFIXES = {".mp4", ".mov", ".avi", ".mkv", ".webm"}
TEXT_SUFFIXES = {".xlsx", ".xls", ".txt", ".md", ".csv", ".json", ".pdf", ".doc", ".docx"}
LIBRARY_UI_HIDDEN_NAMES = frozenset({"library_index.json", "商品资料.xlsx", "material_folders.json"})


def normalize_material_category(category: str | None) -> str:
    if not category:
        return "text"
    value = str(category).strip().lower()
    if value in {"copy", "brand", "reference"}:
        return "text"
    if value in {"image", "video", "text"}:
        return value
    return "text"


def categorize_file(path: Path, root: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    return "text"


@dataclass(frozen=True)
class MaterialItem:
    name: str
    relative_path: str
    category: str
    size_bytes: int
    absolute_path: Path

    def to_dict(self, material_root: Path) -> dict[str, Any]:
        category = normalize_material_category(self.category)
        return {
            "name": self.name,
            "relative_path": self.relative_path,
            "category": category,
            "size_bytes": self.size_bytes,
            "url": f"/api/material-file?path={self.absolute_path.as_posix()}",
            "is_image": category == "image",
        }


def scan_material_catalog(root: Path, limit: int = 500) -> list[MaterialItem]:
    if not root.is_dir():
        return []
    items: list[MaterialItem] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if path.name.startswith(".") or "__MACOSX" in path.parts:
            continue
        try:
            relative = path.relative_to(root)
        except ValueError:
            continue
        items.append(
            MaterialItem(
                name=path.name,
                relative_path=relative.as_posix(),
                category=normalize_material_category(categorize_file(path, root)),
                size_bytes=path.stat().st_size,
                absolute_path=path.resolve(),
            )
        )
        if len(items) >= limit:
            break
    return items


def filter_library_catalog_for_ui(catalog: dict[str, Any]) -> dict[str, Any]:
    """Hide index/system files from material library grid and stats."""

    def visible(entry: dict[str, Any]) -> bool:
        name = entry.get("name") or Path(str(entry.get("relative_path") or "")).name
        return name not in LIBRARY_UI_HIDDEN_NAMES

    items = []
    for entry in catalog.get("items", []):
        if not visible(entry):
            continue
        normalized = dict(entry)
        normalized["category"] = normalize_material_category(entry.get("category"))
        normalized["is_image"] = normalized["category"] == "image"
        items.append(normalized)
    stats_items = [
        MaterialItem(
            name=entry["name"],
            relative_path=entry["relative_path"],
            category=entry["category"],
            size_bytes=int(entry.get("size_bytes") or 0),
            absolute_path=Path(entry.get("relative_path") or entry["name"]),
        )
        for entry in items
    ]
    return {
        **catalog,
        "items": items,
        "images": [entry for entry in catalog.get("images", []) if visible(entry)],
        "reference_files": [entry for entry in catalog.get("reference_files", []) if visible(entry)],
        "stats": material_stats(stats_items),
    }


def material_stats(items: list[MaterialItem]) -> dict[str, Any]:
    stats = {
        "image": {"count": 0, "bytes": 0},
        "video": {"count": 0, "bytes": 0},
        "text": {"count": 0, "bytes": 0},
    }
    for item in items:
        bucket = stats[normalize_material_category(item.category)]
        bucket["count"] += 1
        bucket["bytes"] += item.size_bytes
    return {
        "total_count": len(items),
        "categories": stats,
    }


from app.web.product_categories import resolve_product_category_fields


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def list_workspace_products(workspace_root: Path) -> list[dict[str, Any]]:
    if not workspace_root.is_dir():
        return []
    products: list[dict[str, Any]] = []
    for product_dir in sorted(workspace_root.glob("prod_*")):
        if not product_dir.is_dir():
            continue
        understanding = _read_json(product_dir / "product_understanding.json")
        run_meta = _read_json(product_dir / "run_meta.json")
        profile = _read_json(product_dir / "product_profile.json")
        form = (run_meta or {}).get("product_form") or {}
        category_payload: dict[str, Any] = {}
        if profile:
            category_payload.update(profile)
        if form:
            for key in ("product_category", "product_category_key"):
                if form.get(key):
                    category_payload[key] = form[key]
        if run_meta and run_meta.get("category") and not category_payload.get("product_category"):
            category_payload["product_category"] = run_meta["category"]
        package_count = len(list((product_dir / "packages").glob("pkg_*"))) if (product_dir / "packages").is_dir() else 0
        product_name = (
            (understanding or {}).get("product_name")
            or (profile or {}).get("product_name")
            or (run_meta or {}).get("product_name")
            or product_dir.name
        )
        cat_key, cat_label = resolve_product_category_fields(category_payload)
        if cat_key == "all":
            cat_key, cat_label = resolve_product_category_fields({"product_category": product_name})
        status = "completed" if package_count else ("analyzed" if understanding else "manual")
        products.append(
            {
                "id": product_dir.name,
                "source": "workspace",
                "name": product_name,
                "category": cat_key,
                "category_label": cat_label,
                "brand": (run_meta or {}).get("brand") or (profile or {}).get("product_brand") or "本地商品",
                "price": (run_meta or {}).get("price") or (profile or {}).get("product_price"),
                "status": status,
                "package_count": package_count,
                "material_path": (run_meta or {}).get("material_path"),
                "evidence_count": (understanding or {}).get("evidence_count"),
            }
        )
    return products


def list_projects(workspace_root: Path, demo_packs: list[dict[str, str]]) -> list[dict[str, Any]]:
    projects: list[dict[str, Any]] = []
    for pack in demo_packs:
        projects.append(
            {
                "id": pack["id"],
                "title": pack["name"],
                "category": pack["category"],
                "status": "demo",
                "package_count": 0,
                "material_path": pack["path"],
                "icon": pack.get("icon", "📦"),
            }
        )
    for product in list_workspace_products(workspace_root):
        projects.append(
            {
                "id": product["id"],
                "title": product["name"],
                "category": product["category"],
                "status": product["status"],
                "package_count": product["package_count"],
                "material_path": product.get("material_path"),
                "icon": "📦",
            }
        )
    return projects


def demo_products(demo_packs: list[dict[str, str]]) -> list[dict[str, Any]]:
    mapping = {
        "bag": ("bag", "箱包", "轻序 QINGXU", 259, "👜"),
        "jeans": ("clothing", "服饰", "界线 JIEXIAN", 329, "👖"),
    }
    products: list[dict[str, Any]] = []
    for pack in demo_packs:
        category, label, brand, price, icon = mapping.get(
            pack["id"],
            ("all", pack["category"], "Demo", None, pack.get("icon", "📦")),
        )
        products.append(
            {
                "id": f"demo:{pack['id']}",
                "source": "demo",
                "demo_id": pack["id"],
                "name": pack["name"],
                "category": category,
                "category_label": label,
                "brand": brand,
                "price": price,
                "status": "demo",
                "material_path": pack["path"],
                "icon": icon,
            }
        )
    return products


def list_products(workspace_root: Path, demo_packs: list[dict[str, str]]) -> list[dict[str, Any]]:
    products: list[dict[str, Any]] = []
    for item in list_workspace_products(workspace_root):
        products.append(
            {
                "id": item["id"],
                "source": "workspace",
                "name": item["name"],
                "category": item.get("category") or "all",
                "category_label": item.get("category_label") or item.get("category") or "商品",
                "brand": item["brand"],
                "price": item.get("price"),
                "status": item["status"],
                "material_path": item.get("material_path"),
                "package_count": item.get("package_count", 0),
                "evidence_count": item.get("evidence_count"),
                "icon": "🛍️",
            }
        )
    return products


def product_detail(
    product_key: str,
    workspace_root: Path,
    demo_packs: list[dict[str, str]],
) -> dict[str, Any] | None:
    from app.web.product_store import (
        list_product_files,
        list_product_generated_images,
        load_product_profile,
    )
    from app.web.product_summary import build_product_summary

    if product_key.startswith("demo:"):
        demo_id = product_key.split(":", 1)[1]
        for product in demo_products(demo_packs):
            if product["demo_id"] == demo_id:
                hint = (
                    f"这是官方 Demo 素材包「{product.get('name', demo_id)}」。\n"
                    "点击「创作」可基于素材包生成完整的 AI 理解与多平台文案，"
                    "生成后详情将自动更新为创意任务总结。"
                )
                return {
                    **product,
                    "summary": build_product_summary(
                        product_id=product["id"],
                        product_dir=None,
                        understanding=None,
                        judgment=None,
                        directions=None,
                        demo_hint=hint,
                    ),
                }
        return None
    product_dir = workspace_root / product_key
    if not product_dir.is_dir():
        return None
    understanding = _read_json(product_dir / "product_understanding.json")
    judgment = _read_json(product_dir / "business_judgment.json")
    directions = _read_json(product_dir / "creative_directions.json")
    run_meta = _read_json(product_dir / "run_meta.json")
    profile = load_product_profile(product_dir)
    display_name = (
        (understanding or {}).get("product_name")
        or profile.get("product_name")
        or (run_meta or {}).get("product_name")
        or product_key
    )
    return {
        "id": product_key,
        "source": "workspace",
        "name": display_name,
        "material_path": (run_meta or {}).get("material_path"),
        "understanding": understanding,
        "judgment": judgment,
        "directions": directions,
        "run_meta": run_meta,
        "profile": profile,
        "files": list_product_files(product_dir),
        "generated_images": list_product_generated_images(product_dir, product_key),
        "summary": build_product_summary(
            product_id=product_key,
            product_dir=product_dir,
            understanding=understanding,
            judgment=judgment,
            directions=directions,
        ),
    }
