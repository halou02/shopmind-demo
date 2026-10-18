from pathlib import Path

from app.core.hashing import sha256_bytes
from app.core.models import (
    BusinessJudgment,
    Direction,
    EvidenceStatus,
    Product,
    Status,
)
from app.export.manifest import build_manifest, verify_manifest, write_manifest
from app.steps.pipeline import Pipeline, StepResult


def test_business_judgment_has_local_boundary_defaults() -> None:
    judgment = BusinessJudgment(
        judgment_id="j-1",
        product_id="p-1",
        recommendation="建议进入内容制作",
        opportunity="久坐场景",
        risks=("尺码资料不足",),
        open_questions=("缺少不同身高实拍",),
        next_actions=("补拍坐姿",),
        evidence_ids=("e-1", "e-2"),
    )
    assert judgment.boundary_notice == "仅基于本地资料"
    assert judgment.not_connected_notice == "未接入，不作判断"


def test_pipeline_stops_after_blocked_step() -> None:
    class Step:
        def __init__(self, name: str, status: Status):
            self.name = name
            self.status = status

        def run(self, context: dict) -> StepResult:
            return StepResult(self.name, self.status, {"ran": True})

    results = Pipeline(
        [Step("ingest", Status.COMPLETE), Step("understand", Status.BLOCKED), Step("export", Status.COMPLETE)]
    ).run({})
    assert [result.step_name for result in results] == ["ingest", "understand"]


def test_manifest_round_trip_detects_tampering(tmp_path: Path) -> None:
    content = tmp_path / "content.md"
    content.write_text("基于本地资料\n", encoding="utf-8")
    manifest = build_manifest(
        package_id="pkg-1",
        product_id="p-1",
        direction_id="d-1",
        files=[content],
        package_root=tmp_path,
        offline=True,
        demo_data=True,
        evidence_count=2,
    )
    write_manifest(manifest, tmp_path / "manifest.json")
    assert verify_manifest(tmp_path, manifest) == []

    content.write_text("被篡改\n", encoding="utf-8")
    assert verify_manifest(tmp_path, manifest) == ["size:content.md", "sha256:content.md"]


def test_hash_is_stable() -> None:
    assert sha256_bytes(b"aipc") == sha256_bytes(b"aipc")
