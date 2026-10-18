from __future__ import annotations

import json
from pathlib import Path

from app.web.creative_archive import (
    CREATIVE_EXPORT_DIR,
    finalize_creative_task,
    import_used_materials_to_library,
    list_creative_exports,
)
from app.web.reference_files import ensure_material_library_root


def _write_package(package_root: Path) -> None:
    package_root.mkdir(parents=True, exist_ok=True)
    (package_root / "content_package.json").write_text("{}", encoding="utf-8")
    (package_root / "content").mkdir()
    (package_root / "content" / "xiaohongshu.md").write_text("# 小红书文案", encoding="utf-8")
    media_dir = package_root / "media" / "generated_images"
    media_dir.mkdir(parents=True)
    (media_dir / "01_demo.png").write_bytes(b"\x89PNG\r\n\x1a\n")


def test_finalize_creative_task_exports_and_archives(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    workspace = tmp_path / "workspace"
    product_id = "prod_archive_test"
    product_dir = workspace / product_id
    product_dir.mkdir(parents=True)
    (product_dir / "product_understanding.json").write_text(
        json.dumps({"product_name": "归档测试商品", "sections": []}, ensure_ascii=False),
        encoding="utf-8",
    )
    (product_dir / "business_judgment.json").write_text(
        json.dumps({"core_opportunity": "测试机会"}, ensure_ascii=False),
        encoding="utf-8",
    )
    (product_dir / "creative_directions.json").write_text(
        json.dumps({"directions": [{"direction_id": "d1", "name": "方向A"}]}, ensure_ascii=False),
        encoding="utf-8",
    )

    material_root = workspace / "uploads" / "task1"
    (material_root / "商品图片").mkdir(parents=True)
    (material_root / "商品图片" / "主图.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (material_root / "参考资料").mkdir()
    (material_root / "参考资料" / "说明.txt").write_text("测试资料", encoding="utf-8")

    package_root = product_dir / "packages" / "pkg_test001"
    _write_package(package_root)

    result = finalize_creative_task(
        workspace,
        product_id=product_id,
        product_name="归档测试商品",
        material_path=str(material_root),
        reference_paths=["商品图片/主图.png"],
        product_form={"product_name": "归档测试商品", "product_category": "服装"},
        understanding={"product_name": "归档测试商品", "sections": []},
        judgment={"core_opportunity": "测试机会"},
        directions={"directions": [{"direction_id": "d1", "name": "方向A"}]},
        package_root=package_root,
        package_id="pkg_test001",
    )

    export_root = workspace / CREATIVE_EXPORT_DIR
    assert export_root.is_dir()
    assert Path(result["export"]["folder_path"]).is_dir()
    assert Path(result["export"]["zip_path"]).is_file()
    assert (product_dir / "product_summary.json").is_file()
    assert result["product_library"]["product_id"] == product_id

    library = ensure_material_library_root(workspace)
    assert (library / "商品图片" / "主图.png").is_file() or any(
        path.name.startswith("主图") for path in (library / "商品图片").iterdir()
    )
    assert (library / "参考资料" / "说明.txt").is_file() or any(
        path.name.startswith("说明") for path in (library / "参考资料").iterdir()
    )
    assert (library / "商品图片" / "创意生成" / "01_demo.png").is_file()

    exports = list_creative_exports(workspace)
    assert exports
    assert any(item["kind"] == "zip" for item in exports)


def test_import_used_materials_skips_library_imported_files(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    library = ensure_material_library_root(workspace)
    library_image = library / "商品图片" / "library_only.png"
    library_image.parent.mkdir(parents=True, exist_ok=True)
    library_image.write_bytes(b"\x89PNG\r\n\x1a\n")

    material_root = workspace / "uploads" / "task_mix"
    material_root.mkdir(parents=True)
    (material_root / "商品图片").mkdir()
    (material_root / "商品图片" / "library_only.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    (material_root / "商品图片" / "local_only.png").write_bytes(b"\x89PNG\r\n\x1a\n")

    result = import_used_materials_to_library(
        workspace,
        material_path=str(material_root),
        reference_paths=["商品图片/library_only.png", "商品图片/local_only.png"],
        package_root=None,
        exclude_relative_paths=["商品图片/library_only.png"],
    )

    assert (library / "商品图片" / "local_only.png").is_file()
    assert not (library / "商品图片" / "library_only_1.png").exists()
    assert result["imported_count"] == 1


def test_import_used_materials_skips_missing_material_path(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    package_root = workspace / "prod_x" / "packages" / "pkg_x"
    _write_package(package_root)

    result = import_used_materials_to_library(
        workspace,
        material_path=None,
        reference_paths=[],
        package_root=package_root,
    )
    assert result["imported_count"] >= 1
