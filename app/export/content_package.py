from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.business_judgment import BusinessJudgmentReport
from app.core.content_generator import ContentGenerationReport
from app.core.creative_directions import CreativeDirectionsReport
from app.core.evidence_extractor import EvidenceExtractionReport
from app.core.hashing import sha256_bytes
from app.core.models import ValidationLevel
from app.core.product_understanding import ProductUnderstandingReport
from app.core.source_index import ID_SEPARATOR, SourceIndex, output_is_outside_source
from app.export.manifest import build_manifest, verify_manifest, write_manifest

EXPORT_STATUS_READY = "ready"
EXPORT_STATUS_PARTIAL = "partial"
EXPORT_STATUS_NEEDS_CONFIRMATION = "needs_confirmation"
EXPORT_STATUS_BLOCKED = "blocked"
BLOCKED_OUTPUT_INSIDE_SOURCE = "blocked:output_inside_source_root"
PACKAGE_ID_LENGTH = 12
STAGING_DIR_NAME = ".export_staging"
README_FILE_NAME = "README.md"
MANIFEST_FILE_NAME = "manifest.json"


def package_id_of(product_id: str, direction_id: str) -> str:
    joined = f"{product_id}{ID_SEPARATOR}{direction_id}"
    return "pkg_" + sha256_bytes(joined.encode("utf-8"))[:PACKAGE_ID_LENGTH]


@dataclass(frozen=True)
class ExportPackageReport:
    product_id: str
    product_name: str
    package_id: str
    direction_id: str
    direction_name: str
    export_status: str
    package_root: Path
    manifest: dict[str, Any]
    verification_errors: tuple[str, ...]
    blocked_reasons: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "product_id": self.product_id,
            "product_name": self.product_name,
            "package_id": self.package_id,
            "direction_id": self.direction_id,
            "direction_name": self.direction_name,
            "export_status": self.export_status,
            "package_root": self.package_root.as_posix(),
            "verification_errors": list(self.verification_errors),
            "blocked_reasons": list(self.blocked_reasons),
            "file_count": len(self.manifest.get("files", [])),
        }


def export_content_package(
    *,
    understanding: ProductUnderstandingReport,
    judgment_report: BusinessJudgmentReport,
    directions_report: CreativeDirectionsReport,
    content_report: ContentGenerationReport,
    extraction: EvidenceExtractionReport,
    index: SourceIndex,
    output_root: Path,
    library_root: Path,
) -> ExportPackageReport:
    if not output_is_outside_source(output_root, library_root):
        raise ValueError(BLOCKED_OUTPUT_INSIDE_SOURCE)

    blocked_reasons = _blocked_reasons(content_report, extraction)
    export_status = _resolve_export_status(content_report, blocked_reasons)
    package_id = package_id_of(content_report.product_id, content_report.direction_id)
    staging_root = output_root / content_report.product_id / STAGING_DIR_NAME / package_id
    final_root = output_root / content_report.product_id / "packages" / package_id

    if staging_root.exists():
        shutil.rmtree(staging_root)
    staging_root.mkdir(parents=True, exist_ok=True)

    _write_json(staging_root / "product_understanding.json", understanding.to_dict())
    _write_json(staging_root / "business_judgment.json", judgment_report.to_dict())
    _write_json(staging_root / "creative_directions.json", directions_report.to_dict())
    _write_json(staging_root / "evidence.json", extraction.to_dict())
    _write_json(staging_root / "source_index.json", index.to_dict())
    _write_json(staging_root / "content_package.json", content_report.to_dict())
    _write_content_markdown(staging_root / "content", content_report)
    _write_readme(
        staging_root / README_FILE_NAME,
        product_name=content_report.product_name,
        direction_name=content_report.direction_name,
        export_status=export_status,
        blocked_reasons=blocked_reasons,
    )

    evidence_counts = _evidence_counts(extraction)
    manifest = build_manifest(
        package_id=package_id,
        product_id=content_report.product_id,
        direction_id=content_report.direction_id,
        files=_collect_files(staging_root),
        package_root=staging_root,
        offline=True,
        demo_data=True,
        evidence_count=evidence_counts["total"],
        pending_count=evidence_counts["pending"],
        conflict_count=evidence_counts["conflict"],
        stale_count=evidence_counts["stale"],
    )
    manifest["export_status"] = export_status
    manifest["direction_name"] = content_report.direction_name
    manifest["boundary_notice"] = understanding.boundary_notice
    write_manifest(manifest, staging_root / MANIFEST_FILE_NAME)

    verification_errors = tuple(verify_manifest(staging_root, manifest))
    if export_status == EXPORT_STATUS_BLOCKED or verification_errors:
        shutil.rmtree(staging_root, ignore_errors=True)
        return ExportPackageReport(
            product_id=content_report.product_id,
            product_name=content_report.product_name,
            package_id=package_id,
            direction_id=content_report.direction_id,
            direction_name=content_report.direction_name,
            export_status=EXPORT_STATUS_BLOCKED,
            package_root=final_root,
            manifest=manifest,
            verification_errors=verification_errors,
            blocked_reasons=blocked_reasons,
        )

    final_root.parent.mkdir(parents=True, exist_ok=True)
    if final_root.exists():
        shutil.rmtree(final_root)
    os.replace(staging_root, final_root)
    _cleanup_empty_staging(output_root / content_report.product_id / STAGING_DIR_NAME)

    return ExportPackageReport(
        product_id=content_report.product_id,
        product_name=content_report.product_name,
        package_id=package_id,
        direction_id=content_report.direction_id,
        direction_name=content_report.direction_name,
        export_status=export_status,
        package_root=final_root,
        manifest=manifest,
        verification_errors=(),
        blocked_reasons=(),
    )


def _blocked_reasons(
    content_report: ContentGenerationReport,
    extraction: EvidenceExtractionReport,
) -> tuple[str, ...]:
    reasons: list[str] = []
    for item in content_report.items:
        if item.validation_level is ValidationLevel.FAIL:
            reasons.append(f"content_failed:{item.channel}")
    stale = [
        item.evidence_id
        for item in extraction.evidences
        if item.status.value == "stale"
    ]
    if stale:
        reasons.append("stale_evidence_present")
    return tuple(reasons)


def _resolve_export_status(
    content_report: ContentGenerationReport,
    blocked_reasons: tuple[str, ...],
) -> str:
    if blocked_reasons:
        return EXPORT_STATUS_BLOCKED
    levels = {item.validation_level for item in content_report.items}
    if ValidationLevel.FAIL in levels:
        return EXPORT_STATUS_BLOCKED
    if ValidationLevel.WARN in levels:
        return EXPORT_STATUS_PARTIAL
    return EXPORT_STATUS_READY


def _evidence_counts(extraction: EvidenceExtractionReport) -> dict[str, int]:
    counts = {"total": len(extraction.evidences), "pending": 0, "conflict": 0, "stale": 0}
    for item in extraction.evidences:
        if item.status.value == "pending":
            counts["pending"] += 1
        elif item.status.value == "conflict":
            counts["conflict"] += 1
        elif item.status.value == "stale":
            counts["stale"] += 1
    return counts


def _collect_files(package_root: Path) -> list[Path]:
    files = [
        path
        for path in package_root.rglob("*")
        if path.is_file() and path.name != MANIFEST_FILE_NAME
    ]
    return sorted(files)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _write_content_markdown(directory: Path, content_report: ContentGenerationReport) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    for item in content_report.items:
        filename = f"{item.channel}.md"
        body = "\n".join(
            [
                f"# {item.title}",
                "",
                f"方向：{content_report.direction_name}",
                f"校验：{item.validation_level.value}",
                "",
                item.body,
                "",
                "## 依据引用",
                f"- evidence_ids: {', '.join(item.evidence_ids)}",
                f"- asset_ids: {', '.join(item.asset_ids)}",
            ]
        )
        (directory / filename).write_text(body + "\n", encoding="utf-8")


def _write_readme(
    path: Path,
    *,
    product_name: str,
    direction_name: str,
    export_status: str,
    blocked_reasons: tuple[str, ...],
) -> None:
    lines = [
        f"# {product_name} 新品内容包",
        "",
        "说明：本包为比赛演示数据生成结果，仅基于本地资料，不代表真实经营结果。",
        "",
        f"选定方向：{direction_name}",
        f"导出状态：{export_status}",
        "",
        "边界声明：",
        "- 仅基于本地资料",
        "- 未接入市场、利润、广告和实时竞品数据，不作相关判断",
        "",
        "包内文件：",
        "- product_understanding.json",
        "- business_judgment.json",
        "- creative_directions.json",
        "- evidence.json",
        "- source_index.json",
        "- content/*.md",
        "- manifest.json",
        "",
        "版权与使用：",
        "- 演示素材与生成内容仅供比赛 Demo 使用",
        "- 请勿将演示数据当作真实用户反馈或市场结论",
    ]
    if blocked_reasons:
        lines.extend(["", "阻断原因：", *[f"- {reason}" for reason in blocked_reasons]])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _cleanup_empty_staging(staging_parent: Path) -> None:
    if staging_parent.exists() and not any(staging_parent.iterdir()):
        staging_parent.rmdir()
