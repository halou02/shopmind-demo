from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.core.hashing import sha256_file
from app.core.models import SourceKind, Status
from app.core.source_index import INDEX_FILE_NAME, library_id_of, product_id_of
from app.core.source_scanner import IgnoredEntry, scan_library
from app.steps.scan_sources import build_index_summary

from app.demo_paths import BAG_PACK, JEANS_PACK as CLOTHING_PACK, REPO_ROOT as REPOSITORY_ROOT
CLOTHING_NAME = "AIPC 女装牛仔裤 Demo 素材包（服装）"
BAG_NAME = "AIPC Demo素材包（包包）"
CLOTHING_PRODUCT_NAME = "界线 直筒牛仔裤 01 中靛蓝"
BAG_PRODUCT_NAME = "轻序 Daily Tote 01 云苔绿"
WRITE_REAL_INDEX_ENV = "AIPC_WRITE_REAL_INDEX"

CLOTHING_EXPECTED = {
    "Demo经营资料.xlsx": ("product_data", "xlsx", None),
    "历史内容.txt": ("history", "txt", None),
    "品牌规则.txt": ("brand_rule", "txt", None),
    "新品内容包.md": ("unknown", "md", None),
    "短视频素材/拍摄清单.md": ("shooting_plan", "md", "短视频素材"),
    "商品图片/01_牛仔裤平铺主图.png": ("image", "png", "商品图片"),
    "商品图片/02_正面上身图.png": ("image", "png", "商品图片"),
    "商品图片/03_腰部细节图.png": ("image", "png", "商品图片"),
    "商品图片/04_久坐咖啡馆场景图.png": ("image", "png", "商品图片"),
}
BAG_EXPECTED = {
    "Demo经营资料.xlsx": ("product_data", "xlsx", None),
    "历史内容.txt": ("history", "txt", None),
    "品牌规则.txt": ("brand_rule", "txt", None),
    "新品内容包.md": ("unknown", "md", None),
    "短视频素材/拍摄清单.md": ("shooting_plan", "md", "短视频素材"),
    "商品图片/01_商品主图_云苔绿.png": ("image", "png", "商品图片"),
    "商品图片/02_容量细节图.png": ("image", "png", "商品图片"),
    "商品图片/03_通勤场景图.png": ("image", "png", "商品图片"),
    "商品图片/04_咖啡馆场景图.png": ("image", "png", "商品图片"),
}
EXPECTED_KIND_COUNTS = {
    "image": 4,
    "product_data": 1,
    "history": 1,
    "brand_rule": 1,
    "shooting_plan": 1,
    "unknown": 1,
}
EXPECTED_TOP_LEVEL_ENTRIES = {
    "Demo经营资料.xlsx",
    "历史内容.txt",
    "品牌规则.txt",
    "新品内容包.md",
    "商品图片",
    "短视频素材",
}
EXPECTED_TOP_LEVEL_ENTRIES = {
    "Demo经营资料.xlsx",
    "历史内容.txt",
    "品牌规则.txt",
    "新品内容包.md",
    "商品图片",
    "短视频素材",
}


def _scan(pack: Path):
    if not pack.is_dir():
        pytest.skip(f"真实素材包不存在：{pack}")
    return scan_library(pack)


def _census(pack: Path) -> dict[str, tuple[int, str, int, int]]:
    snapshot: dict[str, tuple[int, str, int, int]] = {}
    for directory, _, file_names in os.walk(pack):
        for name in file_names:
            path = Path(directory, name)
            stat = path.stat()
            snapshot[path.relative_to(pack).as_posix()] = (
                stat.st_size,
                sha256_file(path),
                stat.st_mtime_ns,
                stat.st_ctime_ns,
            )
    return snapshot


def _directory_names(pack: Path) -> set[str]:
    names: set[str] = set()
    for directory, dir_names, _ in os.walk(pack):
        for name in dir_names:
            names.add((Path(directory) / name).relative_to(pack).as_posix())
    return names


def _table(report) -> dict[str, tuple[str, str, str | None]]:
    return {
        item.source.relative_path: (
            item.source.source_kind.value,
            item.source.file_type,
            item.source.source_folder,
        )
        for item in report.sources
    }


@pytest.mark.parametrize(
    ("pack", "pack_name", "product_name", "expected", "ignored_entry"),
    [
        (
            CLOTHING_PACK,
            CLOTHING_NAME,
            CLOTHING_PRODUCT_NAME,
            CLOTHING_EXPECTED,
            IgnoredEntry("__MACOSX", "ignored_directory"),
        ),
        (
            BAG_PACK,
            BAG_NAME,
            BAG_PRODUCT_NAME,
            BAG_EXPECTED,
            IgnoredEntry(".DS_Store", "ds_store"),
        ),
    ],
    ids=["服装包", "包包包"],
)
def test_real_pack_index_matches_the_expected_table(
    pack: Path,
    pack_name: str,
    product_name: str,
    expected: dict[str, tuple[str, str, None | str]],
    ignored_entry: IgnoredEntry,
) -> None:
    report = _scan(pack)
    index = report.index()

    assert _table(report) == expected
    assert len(report.sources) == 9
    assert index.kind_counts() == EXPECTED_KIND_COUNTS
    assert report.status_counts() == {"ready": 8, "needs_confirmation": 1}
    assert report.skipped == ()
    assert report.ignored == (ignored_entry,)
    assert report.error_code is None
    assert report.truncated is False
    assert report.library_id == library_id_of(pack_name)
    assert report.product_id == product_id_of(product_name)
    assert [candidate.product_name for candidate in report.product_candidates] == [
        product_name
    ]
    assert all(
        item.source.product_id == report.product_id for item in report.sources
    )
    assert all(item.source.file_hash for item in report.sources)


def test_real_packs_record_the_product_name_evidence_line() -> None:
    clothing = _scan(CLOTHING_PACK)
    bag = _scan(BAG_PACK)

    assert [
        (candidate.evidence_relative_path, candidate.evidence_line)
        for candidate in clothing.product_candidates
    ] == [("新品内容包.md", 11)]
    assert [
        (candidate.evidence_relative_path, candidate.evidence_line)
        for candidate in bag.product_candidates
    ] == [("新品内容包.md", 7)]


def test_mixed_content_file_is_the_only_needs_confirmation_entry() -> None:
    for pack in (CLOTHING_PACK, BAG_PACK):
        report = _scan(pack)
        flagged = [
            item
            for item in report.sources
            if item.source.parse_status is Status.NEEDS_CONFIRMATION
        ]

        assert [item.source.relative_path for item in flagged] == ["新品内容包.md"]
        item = flagged[0]
        assert item.source.source_kind is SourceKind.UNKNOWN
        assert item.unknown_reason is not None
        assert item.unknown_reason.startswith("content_matches_multiple_kinds:")
        assert len(item.content_signals) >= 4


def test_unwrapping_gives_both_packs_the_same_top_level_skeleton_but_not_ids() -> None:
    clothing = _scan(CLOTHING_PACK)
    bag = _scan(BAG_PACK)

    assert clothing.unwrap_records == (CLOTHING_NAME,)
    assert bag.unwrap_records == ()
    assert {
        item.source.relative_path.split("/")[0] for item in clothing.sources
    } == EXPECTED_TOP_LEVEL_ENTRIES == {
        item.source.relative_path.split("/")[0] for item in bag.sources
    }
    assert set(clothing.index().source_ids()).isdisjoint(bag.index().source_ids())


def test_picking_the_wrapper_or_the_inner_directory_yields_the_same_ids() -> None:
    outer = _scan(CLOTHING_PACK)
    inner = _scan(CLOTHING_PACK / CLOTHING_NAME)

    assert inner.library_id == outer.library_id == library_id_of(CLOTHING_NAME)
    assert [
        (item.source.relative_path, item.source.source_id) for item in outer.sources
    ] == [
        (item.source.relative_path, item.source.source_id) for item in inner.sources
    ]


def test_product_name_token_in_a_filename_associates_that_file() -> None:
    bag = _scan(BAG_PACK)
    matched = next(
        item
        for item in bag.sources
        if item.source.relative_path == "商品图片/01_商品主图_云苔绿.png"
    )

    assert matched.association_basis == "path_token"
    assert matched.source.associated_product_id == bag.product_id


def test_scanning_both_packs_leaves_them_byte_identical(tmp_path: Path) -> None:
    before_clothing = _census(CLOTHING_PACK)
    before_bag = _census(BAG_PACK)
    before_dirs_clothing = _directory_names(CLOTHING_PACK)
    before_dirs_bag = _directory_names(BAG_PACK)

    for pack in (CLOTHING_PACK, BAG_PACK):
        report = _scan(pack)
        assert report.index().write_json(
            tmp_path / "工作区",
            summary=build_index_summary(report),
        ) == []

    # 故意不比对 atime：Win10+ 默认只在系统卷更新最后访问时间，比对它会跨卷抖动
    assert _census(CLOTHING_PACK) == before_clothing
    assert _census(BAG_PACK) == before_bag
    assert _directory_names(CLOTHING_PACK) == before_dirs_clothing
    assert _directory_names(BAG_PACK) == before_dirs_bag

    for pack in (CLOTHING_PACK, BAG_PACK):
        for path in pack.rglob("*"):
            if path.is_file():
                assert path.suffix.lower() in {
                    ".xlsx",
                    ".txt",
                    ".md",
                    ".png",
                    ".ds_store",
                    "",
                }
        assert list(pack.rglob(INDEX_FILE_NAME)) == []
        assert list(pack.rglob("*.json")) == []
        assert list(pack.rglob("*.db")) == []
        assert list(pack.rglob("*.log")) == []
        assert list(pack.rglob("__pycache__")) == []
    assert len(list((tmp_path / "工作区").rglob(INDEX_FILE_NAME))) == 2


def test_review_artifacts_are_written_outside_the_material_packs(tmp_path: Path) -> None:
    output_root = (
        REPOSITORY_ROOT / "app" / "data" / "workspace"
        if os.environ.get(WRITE_REAL_INDEX_ENV) == "1"
        else tmp_path / "工作区"
    )
    written: list[Path] = []
    for pack in (CLOTHING_PACK, BAG_PACK):
        report = _scan(pack)
        assert report.index().write_json(
            output_root,
            summary=build_index_summary(report),
        ) == []
        destination = output_root / report.product_id / INDEX_FILE_NAME
        assert destination.is_file()
        payload = json.loads(destination.read_text(encoding="utf-8"))
        assert payload["_summary"]["文件计数"] == {"已索引": 9, "跳过": 0, "忽略": 1}
        assert len(payload["sources"]) == 9
        written.append(destination)

    assert len({path.parent.name for path in written}) == 2
    for pack in (CLOTHING_PACK, BAG_PACK):
        assert list(pack.rglob(INDEX_FILE_NAME)) == []
