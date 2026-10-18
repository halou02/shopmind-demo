from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.core import source_scanner
from app.core.hashing import sha256_file
from app.core.models import Source, Status
from app.core.source_index import INDEX_FILE_NAME
from app.core.source_scanner import scan_library
from app.steps.pipeline import Pipeline, StepResult
from app.steps.scan_sources import (
    ERROR_INDEX_WRITE_FAILED,
    ERROR_MISSING_MATERIAL_ROOT,
    ERROR_OUTPUT_INSIDE_SOURCE_ROOT,
    ScanSourcesStep,
    build_index_summary,
)

PRODUCT_NAME = "测试品牌 直筒牛仔裤 01 中靛蓝"
HISTORY_BODY = "以下为虚构演示历史内容。\n标题：一条牛仔裤\n主卖点：显瘦\n"


class _Collector:
    name = "collect"

    def __init__(self) -> None:
        self.context: dict[str, Any] = {}

    def run(self, context: dict[str, Any]) -> StepResult:
        self.context = dict(context)
        return StepResult(self.name, Status.COMPLETE, {})


def _write(directory: Path, relative_path: str, text: str) -> Path:
    target = directory / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text.encode("utf-8"))
    return target


def _build_pack(pack: Path) -> Path:
    pack.mkdir(parents=True, exist_ok=True)
    _write(
        pack,
        "新品内容包.md",
        f"# 商品理解卡\n- 商品名称：{PRODUCT_NAME}\n\n## 评价洞察\n用户的主要顾虑是腰围\n",
    )
    _write(pack, "历史内容.txt", HISTORY_BODY)
    _write(pack, "商品图片/01_主图.png", "a")
    _write(pack, "商品图片/02_细节图.png", "b")
    _write(pack, "短视频素材/拍摄清单.md", "竖屏短视频拍摄清单\n9:16\n")
    return pack


def _pack_files(pack: Path) -> dict[str, tuple[int, str]]:
    return {
        path.relative_to(pack).as_posix(): (path.stat().st_size, sha256_file(path))
        for path in pack.rglob("*")
        if path.is_file()
    }


def test_missing_material_root_fails_before_touching_the_disk() -> None:
    result = ScanSourcesStep().run({})

    assert result.step_name == "scan_sources"
    assert result.status is Status.FAILED
    assert result.error_code == ERROR_MISSING_MATERIAL_ROOT
    assert result.data == {}
    assert result.message is not None


def test_absent_directory_and_file_as_root_both_fail(tmp_path: Path) -> None:
    step = ScanSourcesStep()

    missing = step.run({"material_root": tmp_path / "不存在"})
    assert missing.status is Status.FAILED
    assert missing.error_code == "material_root_not_found"

    a_file = _write(tmp_path, "不是目录.txt", "内容")
    not_a_directory = step.run({"material_root": a_file})
    assert not_a_directory.status is Status.FAILED
    assert not_a_directory.error_code == "material_root_not_directory"


def test_clean_scan_completes_and_feeds_the_next_step(tmp_path: Path) -> None:
    pack = _build_pack(tmp_path / "素材包")
    collector = _Collector()

    results = Pipeline([ScanSourcesStep(), collector]).run({"material_root": pack})

    assert [result.status for result in results] == [Status.COMPLETE, Status.COMPLETE]
    data = collector.context["scan_sources"]
    assert all(isinstance(source, Source) for source in data["sources"])
    assert len(data["sources"]) == 5
    assert data["kind_counts"] == {
        "unknown": 1,
        "history": 1,
        "image": 2,
        "shooting_plan": 1,
    }
    assert data["skipped"] == ()
    assert "index_path" not in data
    assert data["product_id"] == data["index"].product_id


def test_output_inside_the_pack_is_blocked_and_writes_nothing(tmp_path: Path) -> None:
    pack = _build_pack(tmp_path / "素材包")
    before = _pack_files(pack)

    result = ScanSourcesStep().run(
        {"material_root": pack, "output_root": pack / "索引输出"}
    )

    assert result.status is Status.BLOCKED
    assert result.error_code == ERROR_OUTPUT_INSIDE_SOURCE_ROOT
    assert "只读" in (result.message or "")
    assert _pack_files(pack) == before
    assert list(pack.rglob(INDEX_FILE_NAME)) == []
    assert result.data["index"].source_ids()


def test_index_written_outside_the_pack_reads_back_identical(tmp_path: Path) -> None:
    pack = _build_pack(tmp_path / "素材包")
    output_root = tmp_path / "workspace"

    result = ScanSourcesStep().run(
        {"material_root": pack, "output_root": output_root}
    )

    assert result.status is Status.COMPLETE
    index_path = Path(result.data["index_path"])
    assert index_path.parent == output_root / result.data["product_id"]
    assert len(_pack_files(pack)) == 5
    assert list(pack.rglob(INDEX_FILE_NAME)) == []

    payload = json.loads(index_path.read_text(encoding="utf-8"))
    assert [entry["source_id"] for entry in payload["sources"]] == list(
        result.data["index"].source_ids()
    )
    assert payload["_summary"]["文件计数"] == {"已索引": 5, "跳过": 0, "忽略": 0}
    assert payload["_summary"]["只读声明"]
    assert payload["_summary"]["商品名候选"] == [
        {
            "product_id": result.data["product_id"],
            "商品名称": PRODUCT_NAME,
            "证据文件": "新品内容包.md",
            "证据行号": 2,
        }
    ]


def test_unsupported_extension_stays_complete_while_technical_skip_is_partial(
    tmp_path: Path,
) -> None:
    step = ScanSourcesStep()

    plain = step.run({"material_root": _build_pack(tmp_path / "素材包")})
    assert plain.status is Status.COMPLETE

    damaged = _build_pack(tmp_path / "坏包")
    _write(damaged, "空文件.md", "")
    _write(damaged, "说明书.pdf", "x")

    result = step.run({"material_root": damaged})

    assert result.status is Status.PARTIAL
    assert result.error_code is None
    assert "empty_file" in (result.message or "")
    assert {entry.reason for entry in result.data["skipped"]} == {
        "empty_file",
        "unsupported_extension",
    }


def test_scan_limit_truncation_reports_partial(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(source_scanner, "MAX_SCAN_FILES", 2)
    pack = _build_pack(tmp_path / "素材包")

    result = ScanSourcesStep().run({"material_root": pack})

    assert result.status is Status.PARTIAL
    assert len(result.data["sources"]) == 2


def test_index_write_failure_reports_failed_without_raising(tmp_path: Path) -> None:
    pack = _build_pack(tmp_path / "素材包")
    blocker = _write(tmp_path, "占用文件", "x")

    result = ScanSourcesStep().run({"material_root": pack, "output_root": blocker})

    assert result.status is Status.FAILED
    assert result.error_code == ERROR_INDEX_WRITE_FAILED
    assert "write_failed" in (result.message or "")
    assert result.data["index"].source_ids()


def test_build_index_summary_keeps_given_and_unwrapped_roots_apart(
    tmp_path: Path,
) -> None:
    given = tmp_path / "素材包"
    _build_pack(given / "素材包")
    _write(given, ".DS_Store", "junk")

    summary = build_index_summary(scan_library(given))

    assert summary["解套记录"] == ["素材包"]
    assert summary["索引库根目录"] != summary["用户所选目录"]
    assert summary["忽略清单"] == [{"路径": ".DS_Store", "原因": "ds_store"}]
    assert all(
        entry["所选目录内路径"].startswith("素材包/")
        for entry in summary["素材明细"]
    )
    assert [entry["相对路径"] for entry in summary["素材明细"]] == [
        "历史内容.txt",
        "商品图片/01_主图.png",
        "商品图片/02_细节图.png",
        "新品内容包.md",
        "短视频素材/拍摄清单.md",
    ]
