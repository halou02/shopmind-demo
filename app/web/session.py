from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.ui.pipeline_runner import AnalysisBundle, PublishBundle


@dataclass
class AppSession:
    material_path: Path | None = None
    workspace_path: Path | None = None
    analysis: AnalysisBundle | None = None
    publish: PublishBundle | None = None
    context: dict[str, Any] = field(default_factory=dict)
    allowed_roots: list[Path] = field(default_factory=list)
    product_form: dict[str, Any] = field(default_factory=dict)
    understanding_notes: str = ""
    reference_image_paths: list[str] = field(default_factory=list)
    image_prompts: list[dict[str, Any]] = field(default_factory=list)
    generated_media: dict[str, Any] = field(default_factory=dict)
    active_media_dir: Path | None = None
    selected_direction_id: str | None = None
    last_history_id: str | None = None
    creative_archive: dict[str, Any] | None = None
    library_imported_paths: list[str] = field(default_factory=list)

    def set_material(self, path: Path) -> None:
        self.material_path = path.resolve()
        if self.material_path not in self.allowed_roots:
            self.allowed_roots.append(self.material_path)

    def register_root(self, path: Path) -> None:
        resolved = path.resolve()
        if resolved not in self.allowed_roots:
            self.allowed_roots.append(resolved)

    def is_allowed_path(self, candidate: Path) -> bool:
        resolved = candidate.resolve()
        for root in self.allowed_roots:
            try:
                resolved.relative_to(root.resolve())
                return True
            except ValueError:
                continue
        return False

    def reset_creative_task(self) -> None:
        self.material_path = None
        self.analysis = None
        self.publish = None
        self.context = {}
        self.product_form = {}
        self.understanding_notes = ""
        self.reference_image_paths = []
        self.image_prompts = []
        self.generated_media = {}
        self.active_media_dir = None
        self.selected_direction_id = None
        self.creative_archive = None
        self.library_imported_paths = []


SESSION = AppSession()
