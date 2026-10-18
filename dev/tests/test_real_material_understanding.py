from __future__ import annotations

from pathlib import Path

import pytest

from app.core.evidence_extractor import extract_evidence_from_index
from app.core.product_understanding import (
    SECTION_AVAILABLE_ASSETS,
    SECTION_BRAND_MEMORY,
    SECTION_DATA_GAPS,
    SECTION_HISTORY_PATTERNS,
    SECTION_PRODUCT_FACTS,
    SECTION_REVIEW_INSIGHTS,
    build_product_understanding,
)
from app.core.source_scanner import scan_library

from app.demo_paths import BAG_PACK, JEANS_PACK as CLOTHING_PACK


def _understand(pack: Path):
    if not pack.is_dir():
        pytest.skip(f"真实素材包不存在：{pack}")
    scan_report = scan_library(pack)
    extraction = extract_evidence_from_index(scan_report.index(), scan_report.library_root)
    return build_product_understanding(extraction, scan_report.index())


def _fact_summary(understanding, section_id: str, label: str) -> str:
    section = understanding.section(section_id)
    assert section is not None
    for fact in section.facts:
        if fact.label == label:
            return fact.summary
    raise AssertionError(f"missing fact {section_id}.{label}")


@pytest.mark.parametrize(
    ("pack", "price", "material", "dimension_tokens", "weight"),
    [
        (
            CLOTHING_PACK,
            "329 元",
            "99% 棉 1% 氨纶",
            ("98", "102", "106"),
            None,
        ),
        (
            BAG_PACK,
            "259 元",
            "再生尼龙外层 聚酯纤维填充",
            ("34", "27", "12"),
            "380 g",
        ),
    ],
)
def test_real_pack_product_facts(
    pack: Path,
    price: str,
    material: str,
    dimension_tokens: tuple[str, ...],
    weight: str | None,
) -> None:
    understanding = _understand(pack)

    assert price in _fact_summary(understanding, SECTION_PRODUCT_FACTS, "建议价格")
    assert material in _fact_summary(understanding, SECTION_PRODUCT_FACTS, "材质")

    if weight is not None:
        assert weight in _fact_summary(understanding, SECTION_PRODUCT_FACTS, "重量")
    else:
        waist = _fact_summary(understanding, SECTION_PRODUCT_FACTS, "腰部活动余量")
        assert "2 cm" in waist
        length = _fact_summary(understanding, SECTION_PRODUCT_FACTS, "裤长")
        assert all(token in length for token in dimension_tokens)


def test_real_jeans_review_and_history_insights() -> None:
    understanding = _understand(CLOTHING_PACK)

    likes = _fact_summary(understanding, SECTION_REVIEW_INSIGHTS, "用户真正喜欢")
    concerns = _fact_summary(understanding, SECTION_REVIEW_INSIGHTS, "用户主要顾虑")
    assert "J01" in likes and "坐下" in likes
    assert any(review_id in concerns for review_id in ("J11", "J12", "J13"))
    assert "15" in _fact_summary(understanding, SECTION_REVIEW_INSIGHTS, "评价样本量")

    history = _fact_summary(understanding, SECTION_HISTORY_PATTERNS, "历史内容结论")
    assert "显瘦" in history and "百搭" in history

    banned = _fact_summary(understanding, SECTION_BRAND_MEMORY, "禁用词")
    assert "显瘦神裤" in banned


def test_real_bag_laptop_and_asset_index() -> None:
    understanding = _understand(BAG_PACK)

    size_hint_facts = [
        fact
        for fact in understanding.facts_for_section(SECTION_PRODUCT_FACTS)
        if fact.label == "尺寸"
    ]
    assert size_hint_facts
    assert any(fact.evidence_ids for fact in size_hint_facts)

    laptop_hints = [
        item
        for item in understanding.section(SECTION_PRODUCT_FACTS).facts
        if "13" in item.summary
    ]
    assert not laptop_hints or any(
        "13" in locator
        for fact in understanding.section(SECTION_PRODUCT_FACTS).facts
        for locator in fact.source_locators
    )

    assets = understanding.facts_for_section(SECTION_AVAILABLE_ASSETS)
    assert len(assets) == 4
    assert understanding.boundary_notice == "仅基于本地资料"


def test_real_packs_surface_data_gaps_without_inventing_facts() -> None:
    jeans = _understand(CLOTHING_PACK)
    bag = _understand(BAG_PACK)

    jeans_gaps = jeans.section(SECTION_DATA_GAPS).gaps
    bag_gaps = bag.section(SECTION_DATA_GAPS).gaps

    assert any("尺码" in gap or "身高" in gap for gap in jeans_gaps)
    assert any("耐脏" in gap or "肩带" in gap for gap in bag_gaps)
    assert all(gap.endswith("（资料不足）") for gap in jeans_gaps + bag_gaps)


def test_understanding_leaves_material_packs_unchanged() -> None:
    before = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    _understand(CLOTHING_PACK)
    after = {
        path.relative_to(CLOTHING_PACK).as_posix(): path.stat().st_size
        for path in CLOTHING_PACK.rglob("*")
        if path.is_file()
    }
    assert before == after
