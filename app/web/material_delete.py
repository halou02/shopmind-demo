from __future__ import annotations

import shutil
from pathlib import Path

from app.web.reference_files import is_reference_file

PROTECTED_FILENAMES = {
    "library_index.json",
    "商品资料.xlsx",
    "material_folders.json",
}


def resolve_material_relative_file(material_root: Path, relative_path: str) -> Path:
    root = material_root.resolve()
    candidate = (root / relative_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError("非法文件路径") from exc
    if not candidate.is_file():
        raise ValueError("文件不存在或已被删除")
    if candidate.name in PROTECTED_FILENAMES or candidate.name.startswith("."):
        raise ValueError("系统文件不可删除")
    if not is_reference_file(candidate.name):
        raise ValueError("不支持的文件类型")
    return candidate


def delete_material_relative_file(material_root: Path, relative_path: str) -> Path:
    target = resolve_material_relative_file(material_root, relative_path)
    target.unlink(missing_ok=True)
    return target


def delete_workspace_product(workspace_root: Path, product_id: str) -> Path:
    if product_id.startswith("demo:") or product_id in {"bag", "jeans"}:
        raise ValueError("演示商品不可删除")
    if not product_id.startswith("prod_"):
        raise ValueError("仅支持删除本地工作区商品")
    product_dir = (workspace_root / product_id).resolve()
    workspace_resolved = workspace_root.resolve()
    try:
        product_dir.relative_to(workspace_resolved)
    except ValueError as exc:
        raise ValueError("非法商品路径") from exc
    if not product_dir.is_dir():
        raise ValueError("商品不存在")
    shutil.rmtree(product_dir)
    return product_dir
