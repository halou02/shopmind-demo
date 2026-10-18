from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.content_generator import (
    BLOCKED_OUTPUT_INSIDE_SOURCE,
    CHANNELS,
    ContentGenerationReport,
    build_content_package,
)
from app.core.creative_directions import CreativeDirectionsReport
from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.models import Status, ValidationLevel
from app.core.product_understanding import ProductUnderstandingReport
from app.core.source_index import SourceIndex, index_from_dict
from app.steps.pipeline import StepResult

GENERATE_DIRECTIONS_KEY = "generate_directions"
UNDERSTAND_PRODUCT_KEY = "understand_product"
GENERATE_CONTENT_KEY = "generate_content"
EXTRACT_EVIDENCE_KEY = "extract_evidence"
SCAN_SOURCES_KEY = "scan_sources"
MATERIAL_ROOT_KEY = "material_root"
OUTPUT_ROOT_KEY = "output_root"
SELECTED_DIRECTION_ID_KEY = "selected_direction_id"
REPORT_KEY = "report"
INDEX_KEY = "index"
ERROR_MISSING_DIRECTIONS = "missing_creative_directions"
ERROR_MISSING_UNDERSTANDING = "missing_product_understanding"
ERROR_MISSING_EVIDENCE = "missing_evidence_report"
ERROR_UNKNOWN_DIRECTION = "unknown_direction_id"
ERROR_MISSING_MATERIAL_ROOT = "missing_material_root"
ERROR_OUTPUT_INSIDE_SOURCE_ROOT = "output_inside_source_root"
ERROR_CONTENT_WRITE_FAILED = "content_write_failed"


class GenerateContentStep:
    name = "generate_content"

    def run(self, context: dict[str, Any]) -> StepResult:
        directions_data = context.get(GENERATE_DIRECTIONS_KEY)
        understanding_data = context.get(UNDERSTAND_PRODUCT_KEY)
        extract_data = context.get(EXTRACT_EVIDENCE_KEY)
        if not isinstance(directions_data, dict) or REPORT_KEY not in directions_data:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_DIRECTIONS,
                message="需要先完成 generate_directions 并生成创意方向。",
            )
        if not isinstance(understanding_data, dict) or REPORT_KEY not in understanding_data:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_UNDERSTANDING,
                message="需要先完成 understand_product 并生成商品理解卡。",
            )
        if not isinstance(extract_data, dict) or REPORT_KEY not in extract_data:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_EVIDENCE,
                message="需要先完成 extract_evidence 并生成 Evidence。",
            )

        directions_report = _directions_from_context(directions_data)
        understanding = _understanding_from_context(understanding_data)
        extraction = _extraction_from_context(extract_data)
        selected_direction_id = context.get(SELECTED_DIRECTION_ID_KEY)

        try:
            library_root = _library_root_from_context(context)
        except FileNotFoundError:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_MATERIAL_ROOT,
                message="素材目录不存在，无法生成内容。",
            )
        if not library_root.exists():
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_MATERIAL_ROOT,
                message="素材目录不存在，无法生成内容。",
            )

        understand_data = context.get(UNDERSTAND_PRODUCT_KEY)
        ai_analysis = None
        if isinstance(understand_data, dict):
            ai_analysis = understand_data.get("ai_analysis")

        try:
            report = build_content_package(
                directions_report,
                understanding,
                extraction,
                selected_direction_id=selected_direction_id,
                ai_analysis=ai_analysis if isinstance(ai_analysis, dict) else None,
            )
        except ValueError as error:
            if str(error).startswith("unknown_direction_id:"):
                return StepResult(
                    self.name,
                    Status.FAILED,
                    {},
                    error_code=ERROR_UNKNOWN_DIRECTION,
                    message="所选创意方向不存在。",
                )
            raise

        fail_count = sum(
            1 for item in report.items if item.validation_level is ValidationLevel.FAIL
        )
        warn_count = sum(
            1 for item in report.items if item.validation_level is ValidationLevel.WARN
        )
        data: dict[str, Any] = {
            REPORT_KEY: report,
            "direction_id": report.direction_id,
            "direction_name": report.direction_name,
            "channel_count": len(report.items),
            "channels": list(CHANNELS),
            "fail_count": fail_count,
            "warn_count": warn_count,
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
                    message="内容包不能写入素材包目录内。",
                )
            if write_errors:
                return StepResult(
                    self.name,
                    Status.FAILED,
                    data,
                    error_code=ERROR_CONTENT_WRITE_FAILED,
                    message="; ".join(write_errors),
                )

        status = Status.COMPLETE
        if fail_count:
            status = Status.FAILED
        elif warn_count:
            status = Status.PARTIAL
        return StepResult(self.name, status, data)


def _directions_from_context(data: dict[str, Any]) -> CreativeDirectionsReport:
    payload = data[REPORT_KEY]
    if isinstance(payload, CreativeDirectionsReport):
        return payload
    raise TypeError("unsupported_creative_directions_payload")


def _understanding_from_context(data: dict[str, Any]) -> ProductUnderstandingReport:
    payload = data[REPORT_KEY]
    if isinstance(payload, ProductUnderstandingReport):
        return payload
    raise TypeError("unsupported_product_understanding_payload")


def _extraction_from_context(data: dict[str, Any]) -> EvidenceExtractionReport:
    payload = data[REPORT_KEY]
    if isinstance(payload, EvidenceExtractionReport):
        return payload
    raise TypeError("unsupported_evidence_report_payload")


def _library_root_from_context(context: dict[str, Any]) -> Path:
    raw_root = context.get(MATERIAL_ROOT_KEY)
    if raw_root is not None:
        return Path(raw_root)
    scan_data = context.get(SCAN_SOURCES_KEY)
    if isinstance(scan_data, dict) and INDEX_KEY in scan_data:
        index_payload = scan_data[INDEX_KEY]
        if isinstance(index_payload, SourceIndex):
            return Path(index_payload.library_root)
        if isinstance(index_payload, dict):
            return Path(index_from_dict(index_payload).library_root)
    raise FileNotFoundError("missing_material_root")
