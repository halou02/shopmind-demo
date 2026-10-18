from __future__ import annotations

from pathlib import Path
from typing import Any

from app.core.ai_product_analyzer import analyze_product, merge_ai_analysis, read_text_materials
from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.models import Status
from app.core.product_understanding import (
    BLOCKED_OUTPUT_INSIDE_SOURCE,
    ProductUnderstandingReport,
    apply_product_form,
    build_product_understanding,
)
from app.core.source_index import SourceIndex, index_from_dict
from app.steps.pipeline import StepResult

EXTRACT_EVIDENCE_KEY = "extract_evidence"
SCAN_SOURCES_KEY = "scan_sources"
MATERIAL_ROOT_KEY = "material_root"
OUTPUT_ROOT_KEY = "output_root"
PRODUCT_FORM_KEY = "product_form"
REPORT_KEY = "report"
INDEX_KEY = "index"
ERROR_MISSING_EVIDENCE = "missing_evidence_report"
ERROR_MISSING_INDEX = "missing_source_index"
ERROR_MISSING_MATERIAL_ROOT = "missing_material_root"
ERROR_OUTPUT_INSIDE_SOURCE_ROOT = "output_inside_source_root"
ERROR_UNDERSTANDING_WRITE_FAILED = "understanding_write_failed"


class UnderstandProductStep:
    name = "understand_product"

    def run(self, context: dict[str, Any]) -> StepResult:
        extract_data = context.get(EXTRACT_EVIDENCE_KEY)
        if not isinstance(extract_data, dict) or REPORT_KEY not in extract_data:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_EVIDENCE,
                message="需要先完成 extract_evidence 并生成 Evidence。",
            )

        scan_data = context.get(SCAN_SOURCES_KEY)
        if not isinstance(scan_data, dict) or INDEX_KEY not in scan_data:
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_INDEX,
                message="需要先完成 scan_sources 并写入 source index。",
            )

        extraction = _report_from_context(extract_data)
        index = _index_from_context(scan_data)
        material_root = context.get(MATERIAL_ROOT_KEY, index.library_root)
        library_root = Path(material_root)
        if not library_root.exists():
            return StepResult(
                self.name,
                Status.FAILED,
                {},
                error_code=ERROR_MISSING_MATERIAL_ROOT,
                message="素材目录不存在，无法生成商品理解。",
            )

        report = build_product_understanding(extraction, index)
        product_form = context.get(PRODUCT_FORM_KEY)

        ai_analysis = analyze_product(
            material_root=library_root,
            index=index,
            product_form=product_form if isinstance(product_form, dict) else None,
            extraction=extraction,
        )
        text_materials = read_text_materials(library_root)
        should_merge_ai = ai_analysis.has_content() and (
            ai_analysis.source == "llm"
            or bool(text_materials)
            or (
                isinstance(product_form, dict)
                and any(str(value).strip() for value in product_form.values())
            )
        )
        if should_merge_ai:
            report = merge_ai_analysis(report, ai_analysis)
        if isinstance(product_form, dict) and product_form:
            report = apply_product_form(report, product_form)

        data: dict[str, Any] = {
            REPORT_KEY: report,
            "product_name": report.product_name,
            "section_count": len(report.sections),
            "asset_count": len(report.assets),
            "gap_count": len(report.global_gaps),
        }
        data["ai_analysis"] = ai_analysis.to_dict()

        output_root = context.get(OUTPUT_ROOT_KEY)
        if output_root is not None:
            write_errors = report.write_json(Path(output_root), library_root)
            if BLOCKED_OUTPUT_INSIDE_SOURCE in write_errors:
                return StepResult(
                    self.name,
                    Status.BLOCKED,
                    data,
                    error_code=ERROR_OUTPUT_INSIDE_SOURCE_ROOT,
                    message="商品理解结果不能写入素材包目录内。",
                )
            if write_errors:
                return StepResult(
                    self.name,
                    Status.FAILED,
                    data,
                    error_code=ERROR_UNDERSTANDING_WRITE_FAILED,
                    message="; ".join(write_errors),
                )

        status = Status.COMPLETE
        if report.global_gaps:
            status = Status.PARTIAL
        return StepResult(self.name, status, data)


def _report_from_context(data: dict[str, Any]) -> EvidenceExtractionReport:
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
