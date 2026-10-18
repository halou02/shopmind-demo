from __future__ import annotations

from pathlib import Path

from app.web.materials_flow import auto_confirm_reference_images, build_material_catalog


def test_auto_confirm_picks_catalog_images(tmp_path: Path) -> None:
    root = tmp_path / "task"
    images = root / "商品图片"
    images.mkdir(parents=True)
    (images / "01.png").write_bytes(b"png")
    (images / "02.jpg").write_bytes(b"jpg")

    confirmed = auto_confirm_reference_images(root, [])

    assert confirmed == ["商品图片/01.png", "商品图片/02.jpg"]


def test_build_material_catalog_flags_ready(tmp_path: Path) -> None:
    root = tmp_path / "task"
    images = root / "商品图片"
    images.mkdir(parents=True)
    (images / "01.png").write_bytes(b"png")

    payload = build_material_catalog(root, ["商品图片/01.png"])

    assert payload["materials_ready"] is True
    assert payload["images_confirmed"] is True
    assert len(payload["reference_images"]) == 1
