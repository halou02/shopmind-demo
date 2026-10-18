from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.evidence_extractor import (
    BLOCKED_OUTPUT_INSIDE_SOURCE,
    EvidenceExtractionReport,
    extract_evidence_from_index,
)
from app.core.models import Status
from app.core.source_index import SourceIndex, index_from_dict
from app.steps.pipeline import StepResult

MATERIAL_ROOT_KEY = "material_root"
OUTPUT_ROOT_KEY = "output_root"
SCAN_SOURCES_KEY = "scan_sources"
INDEX_KEY = "index"
REPORT_KEY = "report"
ERROR_MISSING_INDEX = "missing_source_index"
ERROR_MISSING_MATERIAL_ROOT = "missing_material_root"
ERROR_OUTPUT_INSIDE_SOURCE_ROOT = "output_inside_source_root"
ERROR_EVIDENCE_WRITE_FAILED = "evidence_write_failed"


class ExtractEvidenceStep:
    name = "extract_evidence"

    def run(self, context: dict[str, Any]) -> StepResult:
        scan_data = context.get(SCAN_SOURCES_KEY)
        if not isinstance(scan_data, dict) or INDEX_KEY not in scan_data:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_INDEX,
                message="需要先完成 scan_sources 并写入 source index。",
            )

        index = _index_from_context(scan_data)
        material_root = context.get(MATERIAL_ROOT_KEY)
        if material_root is None:
            material_root = index.library_root
        library_root = Path(material_root)
        if not library_root.exists():
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_MATERIAL_ROOT,
                message="素材目录不存在，无法读取证据来源。",
            )

        report = extract_evidence_from_index(index, library_root)
        data: dict[str, Any] = {
            REPORT_KEY: report,
            "evidence_count": len(report.evidences),
            "skipped_count": len(report.skipped),
        }

        output_root = context.get(OUTPUT_ROOT_KEY)
        if output_root is not None:
            write_errors = report.write_json(Path(output_root), library_root)
            if BLOCKED_OUTPUT_INSIDE_SOURCE in write_errors:
                return StepResult(
                    self.name,
                    Status.BLOCKED,
                    data,
                    error_code=ERROR_OUTPUT_INSIDE_SOURCE_ROOT,
                    message="证据索引不能写入素材包目录内。",
                )
            if write_errors:
                return StepResult(
                    self.name,
                    Status.FAILED,
                    data,
                    error_code=ERROR_EVIDENCE_WRITE_FAILED,
                    message="; ".join(write_errors),
                )

        status = Status.COMPLETE
        if report.skipped and report.evidences:
            status = Status.PARTIAL
        if report.skipped and not report.evidences:
            status = Status.FAILED

        return StepResult(self.name, status, data)


def _index_from_context(scan_data: dict[str, Any]) -> SourceIndex:
    index_payload = scan_data[INDEX_KEY]
    if isinstance(index_payload, SourceIndex):
        return index_payload
    if isinstance(index_payload, dict):
        return index_from_dict(index_payload)
    raise TypeError("unsupported_index_payload")
