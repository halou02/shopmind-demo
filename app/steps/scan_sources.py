from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.models import Source, Status
from app.core.source_index import BLOCKED_OUTPUT_INSIDE_SOURCE, INDEX_FILE_NAME
from app.core.source_scanner import (
    SKIP_DEPTH_EXCEEDED,
    SKIP_EMPTY_FILE,
    SKIP_NOT_REGULAR_FILE,
    SKIP_SCAN_LIMIT,
    SKIP_SYMBOLIC_LINK,
    SKIP_TOO_LARGE,
    SKIP_UNREADABLE_DIRECTORY,
    SKIP_UNREADABLE_FILE,
    ScanReport,
    scan_library,
)
from app.steps.pipeline import StepResult

MATERIAL_ROOT_KEY = "material_root"
PRODUCT_ID_KEY = "product_id"
LIBRARY_ID_KEY = "library_id"
OUTPUT_ROOT_KEY = "output_root"
ERROR_MISSING_MATERIAL_ROOT = "missing_material_root"
ERROR_OUTPUT_INSIDE_SOURCE_ROOT = "output_inside_source_root"
ERROR_INDEX_WRITE_FAILED = "index_write_failed"
TECHNICAL_SKIP_REASONS = frozenset(
    {
        SKIP_UNREADABLE_FILE,
        SKIP_UNREADABLE_DIRECTORY,
        SKIP_EMPTY_FILE,
        SKIP_TOO_LARGE,
        SKIP_DEPTH_EXCEEDED,
        SKIP_SCAN_LIMIT,
        SKIP_SYMBOLIC_LINK,
        SKIP_NOT_REGULAR_FILE,
    }
)


def build_index_summary(report: ScanReport) -> dict[str, Any]:
    index = report.index()
    return {
        "说明": "本地素材来源索引。source_id 由库 ID 与库内相对路径算出，绝对路径不参与业务身份。",
        "只读声明": "扫描只读取素材包内文件，未在素材包目录内写入任何文件。",
        "用户所选目录": report.given_root,
        "索引库根目录": report.library_root,
        "解套记录": list(report.unwrap_records),
        "文件计数": {
            "已索引": len(report.sources),
            "跳过": len(report.skipped),
            "忽略": len(report.ignored),
        },
        "分类计数": index.kind_counts(),
        "状态计数": report.status_counts(),
        "商品名候选": [
            {
                "product_id": candidate.product_id,
                "商品名称": candidate.product_name,
                "证据文件": candidate.evidence_relative_path,
                "证据行号": candidate.evidence_line,
            }
            for candidate in report.product_candidates
        ],
        "跳过清单": [
            {"路径": entry.relative_path, "原因": entry.reason, "说明": entry.detail}
            for entry in report.skipped
        ],
        "忽略清单": [
            {"路径": entry.relative_path, "原因": entry.reason}
            for entry in report.ignored
        ],
        "素材明细": [
            {
                "source_id": item.source.source_id,
                "相对路径": item.source.relative_path,
                "所选目录内路径": item.path_as_given,
                "所属文件夹": item.source.source_folder,
                "分类": item.source.source_kind.value,
                "分类依据": item.classification_basis,
                "未能归类原因": item.unknown_reason,
                "文件类型": item.source.file_type,
                "字节": item.size_bytes,
                "解析状态": item.source.parse_status.value,
                "关联商品依据": item.association_basis,
                "文件名命中信号": list(item.filename_signals),
                "内容命中信号": list(item.content_signals),
                "目录命中信号": list(item.folder_signals),
            }
            for item in report.sources
        ],
    }


class ScanSourcesStep:
    name = "scan_sources"

    def run(self, context: dict[str, Any]) -> StepResult:
        raw_root = context.get(MATERIAL_ROOT_KEY)
        if raw_root is None:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_MATERIAL_ROOT,
                message="上下文里没有 material_root，无法扫描本地素材。",
            )
        report = scan_library(
            Path(raw_root),
            product_id=context.get(PRODUCT_ID_KEY),
            library_id=context.get(LIBRARY_ID_KEY),
        )
        sources: tuple[Source, ...] = tuple(
            item.source for item in report.sources
        )
        index = report.index()
        data: dict[str, Any] = {
            "library_id": report.library_id,
            "library_name": report.library_name,
            "product_id": report.product_id,
            "index": index,
            "sources": sources,
            "scanned": report.sources,
            "skipped": report.skipped,
            "ignored": report.ignored,
            "product_candidates": report.product_candidates,
            "unwrap_records": report.unwrap_records,
            "kind_counts": index.kind_counts(),
            "status_counts": report.status_counts(),
        }
        if report.error_code is not None:
            return StepResult(
                self.name,
                Status.FAILED,
                data,
                error_code=report.error_code,
                message=f"素材目录不可用：{report.given_root}",
            )

        raw_output_root = context.get(OUTPUT_ROOT_KEY)
        if raw_output_root is not None:
            output_root = Path(raw_output_root)
            problems = index.write_json(
                output_root,
                summary=build_index_summary(report),
            )
            if problems == [BLOCKED_OUTPUT_INSIDE_SOURCE]:
                return StepResult(
                    self.name,
                    Status.BLOCKED,
                    data,
                    error_code=ERROR_OUTPUT_INSIDE_SOURCE_ROOT,
                    message="索引输出目录落在素材包内，素材包必须保持只读，已拒绝写入。",
                )
            if problems:
                return StepResult(
                    self.name,
                    Status.FAILED,
                    data,
                    error_code=ERROR_INDEX_WRITE_FAILED,
                    message="；".join(problems),
                )
            data["index_path"] = (
                output_root / report.product_id / INDEX_FILE_NAME
            ).as_posix()

        technical_skips = [
            entry
            for entry in report.skipped
            if entry.reason in TECHNICAL_SKIP_REASONS
        ]
        if report.truncated or technical_skips:
            reasons = sorted({entry.reason for entry in technical_skips})
            return StepResult(
                self.name,
                Status.PARTIAL,
                data,
                message="部分文件没能进索引：" + "，".join(reasons),
            )
        return StepResult(self.name, Status.COMPLETE, data)
