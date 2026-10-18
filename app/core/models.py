from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Status(str, Enum):
    READY = "ready"
    PROCESSING = "processing"
    COMPLETE = "complete"
    PARTIAL = "partial"
    NEEDS_CONFIRMATION = "needs_confirmation"
    BLOCKED = "blocked"
    FAILED = "failed"
    STALE = "stale"


class SourceKind(str, Enum):
    PRODUCT_DATA = "product_data"
    REVIEW = "review"
    HISTORY = "history"
    BRAND_RULE = "brand_rule"
    IMAGE = "image"
    VIDEO = "video"
    SHOOTING_PLAN = "shooting_plan"
    UNKNOWN = "unknown"


class EvidenceStatus(str, Enum):
    CONFIRMED = "confirmed"
    PENDING = "pending"
    CONFLICT = "conflict"
    STALE = "stale"


class ValidationLevel(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    category: str
    workspace_id: str
    demo_data: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    version: int = 1


@dataclass(frozen=True)
class Source:
    source_id: str
    product_id: str
    file_name: str
    relative_path: str
    source_kind: SourceKind
    file_type: str
    file_hash: str
    parse_status: Status = Status.READY
    associated_product_id: str | None = None
    source_folder: str | None = None


@dataclass(frozen=True)
class Evidence:
    evidence_id: str
    product_id: str
    source_id: str
    field_id: str
    raw_text: str
    value: Any
    scope: str | None = None
    unit: str | None = None
    source_locator: str | None = None
    status: EvidenceStatus = EvidenceStatus.PENDING
    source_hash: str | None = None
    invalidated_reason: str | None = None


@dataclass(frozen=True)
class Asset:
    asset_id: str
    product_id: str
    source_id: str
    relative_path: str
    asset_type: str
    role: str
    file_hash: str
    processing_status: Status = Status.READY
    used_in_direction_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class BusinessJudgment:
    judgment_id: str
    product_id: str
    recommendation: str
    opportunity: str
    risks: tuple[str, ...]
    open_questions: tuple[str, ...]
    next_actions: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    boundary_notice: str = "仅基于本地资料"
    not_connected_notice: str = "未接入，不作判断"


@dataclass(frozen=True)
class Direction:
    direction_id: str
    product_id: str
    name: str
    audience: str
    scenes: tuple[str, ...]
    main_selling_point: str
    platform_expression: str
    evidence_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]
    risks: tuple[str, ...]
    open_questions: tuple[str, ...]
    avoid_expression: str


@dataclass(frozen=True)
class ContentItem:
    content_id: str
    product_id: str
    direction_id: str
    channel: str
    title: str
    body: str
    evidence_ids: tuple[str, ...]
    asset_ids: tuple[str, ...]
    validation_level: ValidationLevel = ValidationLevel.WARN
    version: int = 1


@dataclass(frozen=True)
class Run:
    run_id: str
    product_id: str
    current_step: str
    status: Status
    model_mode: str
    started_at: datetime
    ended_at: datetime | None = None
    error_code: str | None = None


@dataclass(frozen=True)
class ValidationResult:
    level: ValidationLevel
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
