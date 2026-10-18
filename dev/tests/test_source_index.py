from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from app.core.hashing import sha256_bytes, sha256_file
from app.core.models import Source, SourceKind, Status
from app.core.source_index import (
    BLOCKED_OUTPUT_INSIDE_SOURCE,
    INDEX_FILE_NAME,
    SourceIndex,
    index_from_dict,
    library_id_of,
    output_is_outside_source,
    product_id_of,
    source_from_dict,
    source_id_of,
)
from app.core.source_scanner import scan_library

HISTORY_BODY = b"\xe4\xbb\xa5\xe4\xb8\x8b\xe4\xb8\xba\xe6\xbc\x94\xe7\xa4\xba\xe5\x86\x85\xe5\xae\xb9\n"


def _write(directory: Path, relative_path: str, content: bytes = b"a") -> Path:
    target = directory / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    return target


def _build_pack(root: Path) -> Path:
    _write(root, "历史内容.txt", HISTORY_BODY)
    _write(root, "商品图片/01_主图.png")
    return root


def test_business_id_formulas_are_pinned() -> None:
    assert library_id_of("素材包") == "lib_" + sha256_bytes(
        "素材包".encode("utf-8")
    )[:12]
    assert product_id_of("轻序 托特包") == "prod_" + sha256_bytes(
        "轻序 托特包".encode("utf-8")
    )[:12]
    assert source_id_of("lib_abc", "商品图片/01.png") == "src_" + sha256_bytes(
        "lib_abc\x1f商品图片/01.png".encode("utf-8")
    )[:16]


def test_absolute_path_never_enters_business_identity(tmp_path: Path) -> None:
    first = _build_pack(tmp_path / "甲位置" / "素材包")
    second = _build_pack(tmp_path / "乙位置" / "素材包")
    report_a = scan_library(first)
    report_b = scan_library(second)

    assert report_a.library_id == report_b.library_id
    assert [
        (item.source.relative_path, item.source.source_id) for item in report_a.sources
    ] == [
        (item.source.relative_path, item.source.source_id) for item in report_b.sources
    ]
    serialized = json.dumps(report_a.index().to_dict()["sources"], ensure_ascii=False)
    assert tmp_path.as_posix() not in serialized
    assert str(tmp_path.drive) not in serialized


def test_identical_content_in_two_paths_shares_hash_but_not_id(tmp_path: Path) -> None:
    payload = "同一张图".encode("utf-8")
    _write(tmp_path / "素材包", "商品图片/01_主图.png", payload)
    _write(tmp_path / "素材包", "其他/副本.png", payload)
    index = scan_library(tmp_path / "素材包").index()

    assert len(index.sources) == 2
    assert index.sources[0].file_hash == index.sources[1].file_hash
    assert index.sources[0].source_id != index.sources[1].source_id
    assert len(index.source_ids_by_hash(sha256_bytes(payload))) == 2


def test_content_edit_changes_hash_and_keeps_source_id(tmp_path: Path) -> None:
    pack = _build_pack(tmp_path / "素材包")
    target = pack / "历史内容.txt"
    before = scan_library(pack).index().by_relative_path("历史内容.txt")
    assert before is not None
    assert before.file_hash == sha256_bytes(HISTORY_BODY)

    target.write_bytes("内容改了\n".encode("utf-8"))
    after = scan_library(pack).index().by_relative_path("历史内容.txt")
    assert after is not None

    assert after.source_id == before.source_id
    assert after.file_hash != before.file_hash
    assert after.file_hash == sha256_file(target)


def test_index_lookups(tmp_path: Path) -> None:
    index = scan_library(_build_pack(tmp_path / "素材包")).index()

    first = index.sources[0]
    assert index.get(first.source_id) == first
    assert index.get("src_missing") is None
    assert index.by_relative_path("历史内容.txt") is not None
    assert index.by_relative_path("不存在.md") is None
    assert len(index.source_ids_by_kind(SourceKind.IMAGE)) == 1
    assert index.source_ids_by_kind(SourceKind.REVIEW) == ()
    assert index.kind_counts() == {"history": 1, "image": 1}
    assert index.source_ids() == tuple(source.source_id for source in index.sources)


def test_dict_round_trip_keeps_every_source_field(tmp_path: Path) -> None:
    index = scan_library(_build_pack(tmp_path / "素材包")).index()
    payload = index.to_dict()

    assert set(payload["sources"][0]) == {
        "source_id",
        "product_id",
        "file_name",
        "relative_path",
        "source_kind",
        "file_type",
        "file_hash",
        "parse_status",
        "associated_product_id",
        "source_folder",
    }
    assert payload["source_count"] == len(index.sources)
    assert index_from_dict(payload) == index
    assert source_from_dict(payload["sources"][0]) == index.sources[0]
    assert isinstance(index_from_dict(payload).sources[0].source_kind, SourceKind)
    assert isinstance(index_from_dict(payload).sources[0].parse_status, Status)


def test_output_guard_accepts_only_paths_outside_the_source(tmp_path: Path) -> None:
    source = _build_pack(tmp_path / "素材包")

    assert output_is_outside_source(tmp_path / "输出", source) is True
    assert output_is_outside_source(tmp_path, source) is True
    assert output_is_outside_source(source, source) is False
    assert output_is_outside_source(source / "商品图片", source) is False
    assert output_is_outside_source(source / "商品图片" / "..", source) is False


@pytest.mark.skipif(os.name != "nt", reason="盘符大小写等价只在 Windows 上成立")
def test_output_guard_treats_drive_letter_case_as_the_same_path(tmp_path: Path) -> None:
    source = _build_pack(tmp_path / "素材包")
    inside = source / "商品图片"

    assert output_is_outside_source(Path(str(inside).swapcase()), source) is False


def test_write_json_lands_outside_the_pack(tmp_path: Path) -> None:
    pack = _build_pack(tmp_path / "素材包")
    report = scan_library(pack)
    output_root = tmp_path / "工作区"

    assert report.index().write_json(output_root, summary={"说明": "测试"}) == []
    destination = output_root / report.product_id / INDEX_FILE_NAME
    assert destination.is_file()

    payload = json.loads(destination.read_text(encoding="utf-8"))
    assert payload["_summary"] == {"说明": "测试"}
    assert payload["library_id"] == report.library_id
    assert len(payload["sources"]) == 2
    assert list(pack.rglob(f"*.json")) == []


def test_write_json_refuses_to_write_inside_the_pack(tmp_path: Path) -> None:
    pack = _build_pack(tmp_path / "素材包")
    index = scan_library(pack).index()
    before = sorted(item.as_posix() for item in pack.rglob("*"))

    problems = index.write_json(pack / "商品图片")

    assert problems == [BLOCKED_OUTPUT_INSIDE_SOURCE]
    assert sorted(item.as_posix() for item in pack.rglob("*")) == before
    assert list(pack.rglob(INDEX_FILE_NAME)) == []


def test_write_json_failure_returns_error_code_not_exception(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pack = _build_pack(tmp_path / "素材包")
    index = scan_library(pack).index()
    output_root = tmp_path / "工作区"

    def refuse(*args: object, **kwargs: object) -> None:
        raise OSError(13, "磁盘不可写")

    monkeypatch.setattr(Path, "write_text", refuse)
    problems = index.write_json(output_root)

    assert problems != []
    assert problems[0].startswith("write_failed:")
    assert not (output_root / index.product_id / INDEX_FILE_NAME).exists()


def test_scanner_is_repeatable(tmp_path: Path) -> None:
    pack = _build_pack(tmp_path / "素材包")
    first = scan_library(pack).index()
    second = scan_library(pack).index()

    assert first == second
