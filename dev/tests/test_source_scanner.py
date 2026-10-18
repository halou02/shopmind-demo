from __future__ import annotations

from pathlib import Path

import pytest

from app.core import source_scanner
from app.core.hashing import sha256_file
from app.core.models import SourceKind, Status
from app.core.source_index import product_id_of
from app.core.source_scanner import (
    ERROR_ROOT_NOT_DIRECTORY,
    ERROR_ROOT_NOT_FOUND,
    SKIP_DEPTH_EXCEEDED,
    SKIP_EMPTY_FILE,
    SKIP_HIDDEN_FILE,
    SKIP_NO_EXTENSION,
    SKIP_SCAN_LIMIT,
    SKIP_TOO_LARGE,
    SKIP_UNREADABLE_FILE,
    SKIP_UNSUPPORTED_EXTENSION,
    scan_library,
)

PRODUCT_NAME = "测试品牌 直筒牛仔裤 01 中靛蓝"
HISTORY_BODY = "以下为虚构演示历史内容。\n标题：一条牛仔裤\n主卖点：显瘦\n"
REVIEW_BODY = "## 评价洞察\n用户的主要顾虑是腰围。\n"
BRAND_BODY = "品牌名称：轻序\n禁用词：闭眼入\n"


def _write(directory: Path, relative_path: str, content: bytes = b"a") -> Path:
    target = directory / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _kinds_by_path(report) -> dict[str, str]:
    return {
        item.source.relative_path: item.source.source_kind.value
        for item in report.sources
    }


def _item_by_path(report, relative_path: str):
    for item in report.sources:
        if item.source.relative_path == relative_path:
            return item
    raise AssertionError(f"{relative_path} 不在索引里")


def test_every_supported_extension_becomes_a_source(tmp_path: Path) -> None:
    for extension in ("xlsx", "csv", "txt", "md", "jpg", "jpeg", "png", "webp", "mp4", "mov"):
        _write(tmp_path, f"素材.{extension}")
    report = scan_library(tmp_path)

    assert len(report.sources) == 10
    assert sorted(item.source.file_type for item in report.sources) == sorted(
        ["xlsx", "csv", "txt", "md", "jpg", "jpeg", "png", "webp", "mp4", "mov"]
    )
    kinds = _kinds_by_path(report)
    assert kinds["素材.jpg"] == SourceKind.IMAGE.value
    assert kinds["素材.jpeg"] == SourceKind.IMAGE.value
    assert kinds["素材.png"] == SourceKind.IMAGE.value
    assert kinds["素材.webp"] == SourceKind.IMAGE.value
    assert kinds["素材.mp4"] == SourceKind.VIDEO.value
    assert kinds["素材.mov"] == SourceKind.VIDEO.value
    assert report.skipped == ()


def test_uppercase_extension_is_matched_case_insensitively(tmp_path: Path) -> None:
    _write(tmp_path, "主图.PNG")
    _write(tmp_path, "短片.MP4")
    report = scan_library(tmp_path)

    assert {item.source.file_name: item.source.file_type for item in report.sources} == {
        "主图.PNG": "png",
        "短片.MP4": "mp4",
    }
    assert _kinds_by_path(report) == {"主图.PNG": "image", "短片.MP4": "video"}


def test_macos_and_windows_junk_is_ignored_at_every_depth(tmp_path: Path) -> None:
    _write(tmp_path, ".DS_Store", b"junk")
    _write(tmp_path, "商品图片/.DS_Store", b"junk")
    _write(tmp_path, "._历史内容.txt", b"junk")
    _write(tmp_path, "__MACOSX/._商品图片/._a.png", b"junk")
    _write(tmp_path, "Thumbs.db", b"junk")
    _write(tmp_path, "desktop.ini", b"junk")
    _write(tmp_path, "历史内容.txt", HISTORY_BODY.encode("utf-8"))
    report = scan_library(tmp_path)

    assert len(report.sources) == 1
    assert report.sources[0].source.relative_path == "历史内容.txt"
    assert report.skipped == ()
    assert {entry.reason for entry in report.ignored} == {
        "ds_store",
        "macos_apple_double",
        "ignored_directory",
        "windows_system_file",
    }
    assert {entry.relative_path for entry in report.ignored} == {
        ".DS_Store",
        "商品图片/.DS_Store",
        "._历史内容.txt",
        "__MACOSX",
        "Thumbs.db",
        "desktop.ini",
    }


def test_files_that_only_look_like_junk_are_still_indexed(tmp_path: Path) -> None:
    _write(tmp_path, "备注._.txt", HISTORY_BODY.encode("utf-8"))
    _write(tmp_path, "_内部备注.txt", "随手记的一句话\n".encode("utf-8"))
    report = scan_library(tmp_path)

    assert len(report.sources) == 2
    assert report.skipped == ()
    assert _kinds_by_path(report)["备注._.txt"] == SourceKind.HISTORY.value


def test_hidden_supported_file_is_a_visible_skip(tmp_path: Path) -> None:
    _write(tmp_path, ".草稿.txt", HISTORY_BODY.encode("utf-8"))
    report = scan_library(tmp_path)

    assert report.sources == ()
    assert [(entry.reason, entry.relative_path) for entry in report.skipped] == [
        (SKIP_HIDDEN_FILE, ".草稿.txt")
    ]


def test_unsupported_extension_is_a_visible_skip(tmp_path: Path) -> None:
    _write(tmp_path, "说明书.pdf")
    _write(tmp_path, "配置.json")
    report = scan_library(tmp_path)

    assert report.sources == ()
    assert {
        entry.relative_path: entry.reason for entry in report.skipped
    } == {
        "说明书.pdf": SKIP_UNSUPPORTED_EXTENSION,
        "配置.json": SKIP_UNSUPPORTED_EXTENSION,
    }
    assert any("pdf" in entry.detail for entry in report.skipped)


def test_empty_and_extensionless_files_are_visible_skips(tmp_path: Path) -> None:
    _write(tmp_path, "README", b"readme")
    _write(tmp_path, "占位.md", b"")
    _write(tmp_path, "另一个占位.md", b"")
    report = scan_library(tmp_path)

    assert report.sources == ()
    reasons = [entry.reason for entry in report.skipped]
    assert reasons.count(SKIP_NO_EXTENSION) == 1
    assert reasons.count(SKIP_EMPTY_FILE) == 2


def test_file_in_unexpected_folder_is_kept_with_its_original_path(tmp_path: Path) -> None:
    _write(tmp_path, "商品图片/随手.txt", REVIEW_BODY.encode("utf-8"))
    _write(tmp_path, "乱放的/notes.txt", BRAND_BODY.encode("utf-8"))
    report = scan_library(tmp_path)

    assert _kinds_by_path(report) == {
        "乱放的/notes.txt": SourceKind.BRAND_RULE.value,
        "商品图片/随手.txt": SourceKind.REVIEW.value,
    }
    assert _item_by_path(report, "商品图片/随手.txt").source.source_folder == "商品图片"
    assert _item_by_path(report, "商品图片/随手.txt").classification_basis == "content"


def test_folder_token_is_only_a_last_resort(tmp_path: Path) -> None:
    _write(tmp_path, "评价/notes.txt", "今天天气不错\n".encode("utf-8"))
    report = scan_library(tmp_path)

    item = _item_by_path(report, "评价/notes.txt")
    assert item.source.source_kind is SourceKind.REVIEW
    assert item.classification_basis == "folder"


def test_short_plan_in_video_folder_is_not_misread_as_video(tmp_path: Path) -> None:
    _write(tmp_path, "短视频素材/拍摄清单.md", "# 竖屏短视频拍摄清单\n每条 3 至 5 秒，9:16 竖屏。\n".encode("utf-8"))
    report = scan_library(tmp_path)

    item = _item_by_path(report, "短视频素材/拍摄清单.md")
    assert item.source.source_kind is SourceKind.SHOOTING_PLAN
    assert item.classification_basis == "file_name"
    assert item.unknown_reason is None


def test_multiple_kinds_in_one_file_become_unknown(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "新品内容包.md",
        (
            f"- 商品名称：{PRODUCT_NAME}\n"
            "- 商品事实：约 380 g\n"
            "## 评价洞察\n主要顾虑是容量\n"
            "品牌名称：轻序\n"
            "以下为虚构演示历史内容。\n"
            "# 竖屏短视频拍摄清单，9:16\n"
        ).encode("utf-8"),
    )
    report = scan_library(tmp_path)

    item = _item_by_path(report, "新品内容包.md")
    assert item.source.source_kind is SourceKind.UNKNOWN
    assert item.source.parse_status is Status.NEEDS_CONFIRMATION
    assert item.unknown_reason is not None
    assert item.unknown_reason.startswith("content_matches_multiple_kinds:")
    assert len(item.content_signals) >= 4


def test_multiple_kinds_in_one_file_name_become_unknown(tmp_path: Path) -> None:
    _write(tmp_path, "历史评价汇总.txt", "随手\n".encode("utf-8"))
    report = scan_library(tmp_path)

    item = _item_by_path(report, "历史评价汇总.txt")
    assert item.source.source_kind is SourceKind.UNKNOWN
    assert item.unknown_reason is not None
    assert item.unknown_reason.startswith("file_name_matches_multiple_kinds:")
    assert item.classification_basis == "file_name"


def test_product_id_comes_from_the_product_name_bullet(tmp_path: Path) -> None:
    _write(tmp_path, "新品内容包.md", f"- 商品名称：{PRODUCT_NAME}\n".encode("utf-8"))
    _write(tmp_path, "商品图片/01_牛仔裤平铺主图.png")
    _write(tmp_path, "商品图片/02_正面上身图.png")
    report = scan_library(tmp_path)

    assert report.product_id == product_id_of(PRODUCT_NAME)
    assert [(c.product_name, c.evidence_relative_path, c.evidence_line) for c in report.product_candidates] == [
        (PRODUCT_NAME, "新品内容包.md", 1)
    ]
    assert all(item.source.product_id == report.product_id for item in report.sources)
    assert _item_by_path(report, "商品图片/01_牛仔裤平铺主图.png").association_basis == "path_token"
    assert _item_by_path(report, "商品图片/01_牛仔裤平铺主图.png").source.associated_product_id == report.product_id
    assert _item_by_path(report, "商品图片/02_正面上身图.png").association_basis == "sole_candidate_inherited"
    assert report.status_counts() == {"ready": 3}


def test_two_product_names_make_unmatched_files_need_confirmation(tmp_path: Path) -> None:
    _write(tmp_path, "甲.md", "- 商品名称：甲款云苔绿托特包\n".encode("utf-8"))
    _write(tmp_path, "乙.md", "- 商品名称：乙款雾灰日常包\n".encode("utf-8"))
    _write(tmp_path, "商品图片/02_正面上身图.png")
    report = scan_library(tmp_path)

    assert len(report.product_candidates) == 2
    assert report.product_id == product_id_of(tmp_path.name)
    item = _item_by_path(report, "商品图片/02_正面上身图.png")
    assert item.source.associated_product_id is None
    assert item.source.parse_status is Status.NEEDS_CONFIRMATION
    assert item.association_basis == "unmatched_multiple_product_names"


def test_explicit_product_id_and_library_id_are_respected(tmp_path: Path) -> None:
    _write(tmp_path, "历史内容.txt", HISTORY_BODY.encode("utf-8"))
    report = scan_library(tmp_path, product_id="prod-explicit", library_id="lib-explicit")

    assert report.product_id == "prod-explicit"
    assert report.library_id == "lib-explicit"
    assert report.sources[0].source.product_id == "prod-explicit"


def test_single_child_wrapper_directory_is_unwrapped(tmp_path: Path) -> None:
    pack = tmp_path / "素材包"
    _write(pack / "素材包", "历史内容.txt", HISTORY_BODY.encode("utf-8"))
    (pack / "__MACOSX").mkdir()
    _write(pack / "__MACOSX", "._素材包", b"junk")
    report = scan_library(pack)

    assert report.unwrap_records == ("素材包",)
    assert report.sources[0].source.relative_path == "历史内容.txt"
    assert report.sources[0].path_as_given == "素材包/历史内容.txt"
    assert report.ignored[0].relative_path == "__MACOSX"


def test_junk_and_hidden_entries_at_a_consumed_outer_level_are_still_reported(
    tmp_path: Path,
) -> None:
    pack = tmp_path / "素材包"
    _write(pack / "素材包", "历史内容.txt", HISTORY_BODY.encode("utf-8"))
    _write(pack, ".DS_Store", b"junk")
    _write(pack, "._素材包", b"junk")
    _write(pack, ".本地草稿.txt", b"junk")

    report = scan_library(pack)

    assert report.unwrap_records == ("素材包",)
    assert {
        (entry.relative_path, entry.reason) for entry in report.ignored
    } == {(".DS_Store", "ds_store"), ("._素材包", "macos_apple_double")}
    assert [(entry.relative_path, entry.reason) for entry in report.skipped] == [
        (".本地草稿.txt", SKIP_HIDDEN_FILE)
    ]
    assert [item.source.relative_path for item in report.sources] == ["历史内容.txt"]


def test_a_legit_single_folder_pack_is_not_unwrapped(tmp_path: Path) -> None:
    pack = tmp_path / "素材包"
    _write(pack / "商品图片", "01_主图.png")
    report = scan_library(pack)

    assert report.unwrap_records == ()
    assert report.sources[0].source.relative_path == "商品图片/01_主图.png"


def test_missing_root_and_file_root_return_error_codes(tmp_path: Path) -> None:
    missing = scan_library(tmp_path / "不存在")
    assert missing.error_code == ERROR_ROOT_NOT_FOUND
    assert missing.sources == ()

    a_file = _write(tmp_path, "历史内容.txt", HISTORY_BODY.encode("utf-8"))
    not_a_directory = scan_library(a_file)
    assert not_a_directory.error_code == ERROR_ROOT_NOT_DIRECTORY


def test_scan_file_limit_truncates_with_a_visible_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    for index in range(5):
        _write(tmp_path, f"图片{index}.png")
    monkeypatch.setattr(source_scanner, "MAX_SCAN_FILES", 3)
    report = scan_library(tmp_path)

    assert report.truncated is True
    assert len(report.sources) == 3
    assert [entry.reason for entry in report.skipped] == [SKIP_SCAN_LIMIT]


def test_depth_limit_reports_a_visible_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_scanner, "MAX_DEPTH", 2)
    _write(tmp_path, "一层/二层/三层/历史内容.txt", HISTORY_BODY.encode("utf-8"))
    report = scan_library(tmp_path)

    assert report.sources == ()
    assert [entry.reason for entry in report.skipped] == [SKIP_DEPTH_EXCEEDED]


def test_oversize_file_is_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "历史内容.txt", b"a" * 64)
    monkeypatch.setattr(source_scanner, "MAX_FILE_BYTES", 10)
    report = scan_library(tmp_path)

    assert report.sources == ()
    assert [entry.reason for entry in report.skipped] == [SKIP_TOO_LARGE]


def test_unreadable_file_becomes_a_visible_skip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write(tmp_path, "历史内容.txt", HISTORY_BODY.encode("utf-8"))
    _write(tmp_path, "品牌规则.txt", BRAND_BODY.encode("utf-8"))

    def broken_hash(path: Path, chunk_size: int = 1024 * 1024) -> str:
        if path.name == "品牌规则.txt":
            raise PermissionError(13, "另一个程序正在使用此文件")
        return sha256_file(path, chunk_size)

    monkeypatch.setattr(source_scanner, "sha256_file", broken_hash)
    report = scan_library(tmp_path)

    assert [item.source.relative_path for item in report.sources] == ["历史内容.txt"]
    assert [(entry.reason, entry.relative_path) for entry in report.skipped] == [
        (SKIP_UNREADABLE_FILE, "品牌规则.txt")
    ]
    assert report.skipped[0].detail.startswith("PermissionError")


def test_binary_looking_text_file_is_not_content_probed(tmp_path: Path) -> None:
    _write(tmp_path, "伪装.txt", b"\x00\x01\x02" + BRAND_BODY.encode("utf-8"))
    report = scan_library(tmp_path)

    item = _item_by_path(report, "伪装.txt")
    assert item.content_signals == ()
    assert item.source.source_kind is SourceKind.UNKNOWN


def test_gbk_text_file_is_still_classified(tmp_path: Path) -> None:
    _write(tmp_path, "notes.txt", BRAND_BODY.encode("gbk"))
    report = scan_library(tmp_path)

    assert _kinds_by_path(report)["notes.txt"] == SourceKind.BRAND_RULE.value
