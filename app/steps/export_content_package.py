from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.business_judgment import BusinessJudgmentReport
from app.core.content_generator import ContentGenerationReport
from app.core.creative_directions import CreativeDirectionsReport
from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.models import Status, ValidationLevel
from app.core.product_understanding import ProductUnderstandingReport
from app.export.content_package import (
    BLOCKED_OUTPUT_INSIDE_SOURCE,
    EXPORT_STATUS_BLOCKED,
    export_content_package,
)
from app.core.source_index import SourceIndex, index_from_dict
from app.steps.pipeline import StepResult

SCAN_SOURCES_KEY = "scan_sources"
EXTRACT_EVIDENCE_KEY = "extract_evidence"
UNDERSTAND_PRODUCT_KEY = "understand_product"
JUDGE_BUSINESS_KEY = "judge_business"
GENERATE_DIRECTIONS_KEY = "generate_directions"
GENERATE_CONTENT_KEY = "generate_content"
MATERIAL_ROOT_KEY = "material_root"
OUTPUT_ROOT_KEY = "output_root"
REPORT_KEY = "report"
INDEX_KEY = "index"
ERROR_MISSING_CONTENT = "missing_content_package"
ERROR_MISSING_DIRECTIONS = "missing_creative_directions"
ERROR_MISSING_JUDGMENT = "missing_business_judgment"
ERROR_MISSING_UNDERSTANDING = "missing_product_understanding"
ERROR_MISSING_EVIDENCE = "missing_evidence_report"
ERROR_MISSING_INDEX = "missing_source_index"
ERROR_MISSING_OUTPUT_ROOT = "missing_output_root"
ERROR_MISSING_MATERIAL_ROOT = "missing_material_root"
ERROR_OUTPUT_INSIDE_SOURCE_ROOT = "output_inside_source_root"
ERROR_EXPORT_BLOCKED = "export_blocked"


class ExportContentPackageStep:
    name = "export_content_package"

    def run(self, context: dict[str, Any]) -> StepResult:
        missing = _missing_prerequisite(self.name, context)
        if missing is not None:
            return missing

        output_root = context.get(OUTPUT_ROOT_KEY)
        if output_root is None:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_OUTPUT_ROOT,
                message="导出需要指定 output_root。",
            )

        try:
            library_root = _library_root_from_context(context)
        except FileNotFoundError:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_MATERIAL_ROOT,
                message="素材目录不存在，无法导出内容包。",
            )

        understanding = _understanding_from_context(context[UNDERSTAND_PRODUCT_KEY])
        judgment_report = _judgment_from_context(context[JUDGE_BUSINESS_KEY])
        directions_report = _directions_from_context(context[GENERATE_DIRECTIONS_KEY])
        content_report = _content_from_context(context[GENERATE_CONTENT_KEY])
        extraction = _extraction_from_context(context[EXTRACT_EVIDENCE_KEY])
        index = _index_from_context(context[SCAN_SOURCES_KEY])

        try:
            report = export_content_package(
                understanding=understanding,
                judgment_report=judgment_report,
                directions_report=directions_report,
                content_report=content_report,
                extraction=extraction,
                index=index,
                output_root=Path(output_root),
                library_root=library_root,
            )
        except ValueError as error:
            if str(error) == BLOCKED_OUTPUT_INSIDE_SOURCE:
                return StepResult(
                    self.name,
                    Status.BLOCKED,
                    {},
                    error_code=ERROR_OUTPUT_INSIDE_SOURCE_ROOT,
                    message="内容包不能写入素材包目录内。",
                )
            raise

        data: dict[str, Any] = {
            REPORT_KEY: report,
            "export_status": report.export_status,
            "package_id": report.package_id,
            "package_root": report.package_root.as_posix(),
        }

        if report.export_status == EXPORT_STATUS_BLOCKED:
            return StepResult(
                self.name,
                Status.BLOCKED,
                data,
                error_code=ERROR_EXPORT_BLOCKED,
                message=_blocked_message(report),
            )

        status = Status.COMPLETE
        if any(
            item.validation_level is ValidationLevel.WARN
            for item in content_report.items
        ):
            status = Status.PARTIAL
        return StepResult(self.name, status, data)


def _blocked_message(report) -> str:
    parts = list(report.blocked_reasons)
    parts.extend(report.verification_errors)
    return "内容包导出被阻断：" + "；".join(parts) if parts else "内容包导出被阻断。"


def _missing_prerequisite(step_name: str, context: dict[str, Any]) -> StepResult | None:
    checks = [
        (GENERATE_CONTENT_KEY, ERROR_MISSING_CONTENT, "需要先完成 generate_content。"),
        (GENERATE_DIRECTIONS_KEY, ERROR_MISSING_DIRECTIONS, "需要先完成 generate_directions。"),
        (JUDGE_BUSINESS_KEY, ERROR_MISSING_JUDGMENT, "需要先完成 judge_business。"),
        (UNDERSTAND_PRODUCT_KEY, ERROR_MISSING_UNDERSTANDING, "需要先完成 understand_product。"),
        (EXTRACT_EVIDENCE_KEY, ERROR_MISSING_EVIDENCE, "需要先完成 extract_evidence。"),
        (SCAN_SOURCES_KEY, ERROR_MISSING_INDEX, "需要先完成 scan_sources。"),
    ]
    for key, error_code, message in checks:
        payload = context.get(key)
        if not isinstance(payload, dict):
            return StepResult(step_name, Status.FAILED, {}, error_code=error_code, message=message)
        if key == SCAN_SOURCES_KEY:
            if INDEX_KEY not in payload:
                return StepResult(step_name, Status.FAILED, {}, error_code=error_code, message=message)
        elif REPORT_KEY not in payload:
            return StepResult(step_name, Status.FAILED, {}, error_code=error_code, message=message)
    return None


def _understanding_from_context(data: dict[str, Any]) -> ProductUnderstandingReport:
    payload = data[REPORT_KEY]
    if isinstance(payload, ProductUnderstandingReport):
        return payload
    raise TypeError("unsupported_product_understanding_payload")


def _judgment_from_context(data: dict[str, Any]) -> BusinessJudgmentReport:
    payload = data[REPORT_KEY]
    if isinstance(payload, BusinessJudgmentReport):
        return payload
    raise TypeError("unsupported_business_judgment_payload")


def _directions_from_context(data: dict[str, Any]) -> CreativeDirectionsReport:
    payload = data[REPORT_KEY]
    if isinstance(payload, CreativeDirectionsReport):
        return payload
    raise TypeError("unsupported_creative_directions_payload")


def _content_from_context(data: dict[str, Any]) -> ContentGenerationReport:
    payload = data[REPORT_KEY]
    if isinstance(payload, ContentGenerationReport):
        return payload
    raise TypeError("unsupported_content_generation_payload")


def _extraction_from_context(data: dict[str, Any]) -> EvidenceExtractionReport:
    payload = data[REPORT_KEY]
    if isinstance(payload, EvidenceExtractionReport):
        return payload
    raise TypeError("unsupported_evidence_report_payload")


def _index_from_context(data: dict[str, Any]) -> SourceIndex:
    payload = data[INDEX_KEY]
    if isinstance(payload, SourceIndex):
        return payload
    if isinstance(payload, dict):
        return index_from_dict(payload)
    raise TypeError("unsupported_index_payload")


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
