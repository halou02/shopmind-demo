from __future__ import annotations

import os
from pathlib import Path

import pytest

from app.core.evidence_extractor import extract_evidence_from_index
from app.core.source_scanner import scan_library

from app.demo_paths import BAG_PACK, JEANS_PACK as CLOTHING_PACK


def _scan(pack: Path):
    if not pack.is_dir():
        pytest.skip(f"真实素材包不存在：{pack}")
    return scan_library(pack)


def _extract(pack: Path):
    report = _scan(pack)
    return extract_evidence_from_index(report.index(), report.library_root)


def _field_values(extraction, field_id: str):
    return extraction.values_for_field(field_id)


def _text_values(extraction):
    return [str(item.value) for item in extraction.evidences]


@pytest.mark.parametrize(
    (
        "pack",
        "price",
        "material_field_id",
        "material",
        "length_hint",
        "waist_hint",
        "review_prefix",
    ),
    [
        (
            CLOTHING_PACK,
            329,
            "excel.商品参数.材质",
            "99% 棉 1% 氨纶",
            ("98", "102", "106"),
            "约 2 cm",
            "J",
        ),
        (
            BAG_PACK,
            259,
            "excel.商品参数.材质",
            "再生尼龙外层 聚酯纤维填充",
            ("34", "27", "12"),
            None,
            "R",
        ),
    ],
)
def test_real_pack_excel_facts_are_extracted(
    pack: Path,
    price: int,
    material_field_id: str,
    material: str,
    length_hint: tuple[str, ...],
    waist_hint: str | None,
    review_prefix: str,
) -> None:
    extraction = _extract(pack)

    assert price in _field_values(extraction, "excel.商品参数.建议价格")
    assert any(material in str(value) for value in _field_values(extraction, material_field_id))
    length_value = " ".join(str(value) for value in _field_values(extraction, "excel.商品参数.裤长"))
    size_value = " ".join(str(value) for value in _field_values(extraction, "excel.商品参数.尺寸"))
    combined = f"{length_value} {size_value}"
    assert all(token in combined for token in length_hint)
    if waist_hint is not None:
        assert any(waist_hint in str(value) for value in _field_values(extraction, "excel.商品参数.腰部活动余量"))

    review_ids = {
        item.field_id.split(".")[2]
        for item in extraction.evidences
        if item.field_id.startswith("excel.用户评价.")
    }
    assert len(review_ids) == 15
    assert all(item.startswith(review_prefix) for item in review_ids)


def test_real_jeans_review_and_locator_examples() -> None:
    extraction = _extract(CLOTHING_PACK)
    j01 = [
        item
        for item in extraction.evidences
        if item.field_id == "excel.用户评价.J01.评价文本"
    ]
    assert len(j01) == 1
    assert j01[0].source_locator == "Demo经营资料.xlsx!用户评价!C5"
    assert "腰头" in str(j01[0].value)


def test_real_bag_laptop_capacity_fact() -> None:
    extraction = _extract(BAG_PACK)
    weight_values = _field_values(extraction, "excel.商品参数.重量")
    size_values = _field_values(extraction, "excel.商品参数.尺寸")
    size_hints = _field_values(extraction, "excel.商品参数.尺寸.数据使用提示")
    assert any(value == 380 for value in weight_values)
    assert any("34" in str(value) for value in size_values)
    assert any("13" in str(value) for value in size_hints)


def test_real_text_and_markdown_sources_are_extracted() -> None:
    extraction = _extract(CLOTHING_PACK)
    values = _text_values(extraction)

    assert any("显瘦神裤" in value for value in values)
    assert any("显瘦" in value and "4 次" in value for value in values)
    assert any("329 元" in value for value in values)
    assert any("坐下不勒" in value for value in values)
    assert any("商品理解卡" in (item.source_locator or "") for item in extraction.evidences)


def test_extraction_leaves_material_packs_unchanged(tmp_path: Path) -> None:
    before = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    _extract(CLOTHING_PACK)
    after = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    assert before == after
    assert list(CLOTHING_PACK.rglob("evidence.json")) == []
    assert list(CLOTHING_PACK.rglob("source_index.json")) == []
