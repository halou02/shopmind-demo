from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from app.core.models import Run, Status


@dataclass(frozen=True)
class StepResult:
    step_name: str
    status: Status
    data: dict[str, Any]
    error_code: str | None = None
    message: str | None = None


class PipelineStep(Protocol):
    name: str

    def run(self, context: dict[str, Any]) -> StepResult:
        ...


class Pipeline:
    def __init__(self, steps: list[PipelineStep]):
        self.steps = steps

    def run(self, context: dict[str, Any]) -> list[StepResult]:
        results: list[StepResult] = []
        for step in self.steps:
            result = step.run(context)
            results.append(result)
            context[step.name] = result.data
            if result.status in {Status.FAILED, Status.BLOCKED}:
                break
        return results


def initial_run(run_id: str, product_id: str, model_mode: str = "offline_snapshot") -> Run:
    from datetime import datetime, timezone

    return Run(
        run_id=run_id,
        product_id=product_id,
        current_step="",
        status=Status.READY,
        model_mode=model_mode,
        started_at=datetime.now(timezone.utc),
    )
