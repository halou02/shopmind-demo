from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.business_judgment import BusinessJudgmentReport
from app.core.content_generator import ContentGenerationReport
from app.core.creative_directions import CreativeDirectionsReport
from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.models import Status
from app.core.product_understanding import ProductUnderstandingReport
from app.export.content_package import ExportPackageReport
from app.steps.export_content_package import ExportContentPackageStep
from app.steps.extract_evidence import ExtractEvidenceStep
from app.steps.generate_content import GenerateContentStep
from app.steps.generate_directions import GenerateDirectionsStep
from app.steps.judge_business import JudgeBusinessStep
from app.steps.pipeline import Pipeline, StepResult
from app.steps.scan_sources import ScanSourcesStep
from app.steps.understand_product import UnderstandProductStep

BOUNDARY_NOTICE = "仅基于本地资料"
NOT_CONNECTED_NOTICE = "未接入，不作判断"
DEMO_DATA_NOTICE = "演示数据"


@dataclass(frozen=True)
class AnalysisBundle:
    context: dict[str, Any]
    results: tuple[StepResult, ...]
    understanding: ProductUnderstandingReport | None
    judgment: BusinessJudgmentReport | None
    directions: CreativeDirectionsReport | None
    extraction: EvidenceExtractionReport | None
    stopped_at: str
    ok: bool
    message: str | None


@dataclass(frozen=True)
class PublishBundle:
    context: dict[str, Any]
    results: tuple[StepResult, ...]
    content: ContentGenerationReport | None
    export: ExportPackageReport | None
    ok: bool
    message: str | None


def default_workspace_root() -> Path:
    return Path.cwd() / "workspace"


def resolve_material_root(raw_path: str) -> Path:
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (Path.cwd() / path).resolve()
    return path


def run_analysis(
    material_root: Path,
    workspace_root: Path | None = None,
    product_form: dict[str, Any] | None = None,
) -> AnalysisBundle:
    workspace = workspace_root or default_workspace_root()
    context: dict[str, Any] = {
        "material_root": material_root,
        "output_root": workspace,
    }
    if product_form:
        context["product_form"] = product_form
    results = Pipeline(
        [
            ScanSourcesStep(),
            ExtractEvidenceStep(),
            UnderstandProductStep(),
            JudgeBusinessStep(),
            GenerateDirectionsStep(),
        ]
    ).run(context)
    last = results[-1]
    ok = last.status in {Status.COMPLETE, Status.PARTIAL}
    return AnalysisBundle(
        context=context,
        results=tuple(results),
        understanding=_report(context, "understand_product"),
        judgment=_report(context, "judge_business"),
        directions=_report(context, "generate_directions"),
        extraction=_report(context, "extract_evidence"),
        stopped_at=last.step_name,
        ok=ok,
        message=last.message,
    )


def run_publish(
    context: dict[str, Any],
    *,
    selected_direction_id: str,
    workspace_root: Path | None = None,
) -> PublishBundle:
    publish_context = dict(context)
    publish_context["selected_direction_id"] = selected_direction_id
    if workspace_root is not None:
        publish_context["output_root"] = workspace_root
    results = Pipeline(
        [
            GenerateContentStep(),
            ExportContentPackageStep(),
        ]
    ).run(publish_context)
    last = results[-1]
    ok = last.status in {Status.COMPLETE, Status.PARTIAL}
    return PublishBundle(
        context=publish_context,
        results=tuple(results),
        content=_report(publish_context, "generate_content"),
        export=_report(publish_context, "export_content_package"),
        ok=ok,
        message=last.message,
    )


def _report(context: dict[str, Any], step_name: str):
    payload = context.get(step_name)
    if not isinstance(payload, dict):
        return None
    return payload.get("report")
