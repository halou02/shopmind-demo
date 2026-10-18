from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.business_judgment import (
    BLOCKED_OUTPUT_INSIDE_SOURCE,
    BusinessJudgmentReport,
    build_business_judgment,
)
from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.models import Status
from app.core.product_understanding import ProductUnderstandingReport
from app.core.source_index import SourceIndex, index_from_dict
from app.steps.pipeline import StepResult

EXTRACT_EVIDENCE_KEY = "extract_evidence"
UNDERSTAND_PRODUCT_KEY = "understand_product"
SCAN_SOURCES_KEY = "scan_sources"
MATERIAL_ROOT_KEY = "material_root"
INDEX_KEY = "index"
OUTPUT_ROOT_KEY = "output_root"
REPORT_KEY = "report"
ERROR_MISSING_UNDERSTANDING = "missing_product_understanding"
ERROR_MISSING_EVIDENCE = "missing_evidence_report"
ERROR_MISSING_MATERIAL_ROOT = "missing_material_root"
ERROR_OUTPUT_INSIDE_SOURCE_ROOT = "output_inside_source_root"
ERROR_JUDGMENT_WRITE_FAILED = "judgment_write_failed"


class JudgeBusinessStep:
    name = "judge_business"

    def run(self, context: dict[str, Any]) -> StepResult:
        understanding_data = context.get(UNDERSTAND_PRODUCT_KEY)
        extract_data = context.get(EXTRACT_EVIDENCE_KEY)
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

        understanding = _understanding_from_context(understanding_data)
        extraction = _extraction_from_context(extract_data)
        try:
            library_root = _library_root_from_context(context)
        except FileNotFoundError:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_MATERIAL_ROOT,
                message="素材目录不存在，无法生成经营判断。",
            )
        if not library_root.exists():
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_MATERIAL_ROOT,
                message="素材目录不存在，无法生成经营判断。",
            )

        ai_analysis = None
        if isinstance(understanding_data, dict):
            ai_analysis = understanding_data.get("ai_analysis")
        report = build_business_judgment(understanding, extraction, ai_analysis=ai_analysis)
        data: dict[str, Any] = {
            REPORT_KEY: report,
            "judgment_id": report.judgment.judgment_id,
            "risk_count": len(report.judgment.risks),
            "open_question_count": len(report.judgment.open_questions),
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
                    message="经营判断结果不能写入素材包目录内。",
                )
            if write_errors:
                return StepResult(
                    self.name,
                    Status.FAILED,
                    data,
                    error_code=ERROR_JUDGMENT_WRITE_FAILED,
                    message="; ".join(write_errors),
                )

        status = Status.COMPLETE
        if report.judgment.open_questions:
            status = Status.PARTIAL
        return StepResult(self.name, status, data)


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
