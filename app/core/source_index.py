from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.core.hashing import sha256_bytes
from app.core.models import Source, SourceKind, Status

INDEX_VERSION = "1.0"
INDEX_FILE_NAME = "source_index.json"
SOURCE_ID_LENGTH = 16
LIBRARY_ID_LENGTH = 12
PRODUCT_ID_LENGTH = 12
ID_SEPARATOR = "\x1f"
BLOCKED_OUTPUT_INSIDE_SOURCE = "blocked:output_inside_source_root"


def library_id_of(library_name: str) -> str:
    return "lib_" + sha256_bytes(library_name.encode("utf-8"))[:LIBRARY_ID_LENGTH]


def product_id_of(product_name: str) -> str:
    return "prod_" + sha256_bytes(product_name.encode("utf-8"))[:PRODUCT_ID_LENGTH]


def source_id_of(library_id: str, relative_path: str) -> str:
    joined = f"{library_id}{ID_SEPARATOR}{relative_path}"
    return "src_" + sha256_bytes(joined.encode("utf-8"))[:SOURCE_ID_LENGTH]


def _resolved(path: Path) -> Path:
    return Path(os.path.normcase(os.path.realpath(os.path.abspath(str(path)))))


def output_is_outside_source(output_root: Path, source_root: Path) -> bool:
    output_path = _resolved(output_root)
    source_path = _resolved(source_root)
    if output_path == source_path:
        return False
    return source_path not in output_path.parents


@dataclass(frozen=True)
class SourceIndex:
    index_version: str
    library_id: str
    library_name: str
    library_root: str
    given_root: str
    product_id: str
    sources: tuple[Source, ...]

    def source_ids(self) -> tuple[str, ...]:
        return tuple(source.source_id for source in self.sources)

    def get(self, source_id: str) -> Source | None:
        for source in self.sources:
            if source.source_id == source_id:
                return source
        return None

    def by_relative_path(self, relative_path: str) -> Source | None:
        for source in self.sources:
            if source.relative_path == relative_path:
                return source
        return None

    def source_ids_by_hash(self, file_hash: str) -> tuple[str, ...]:
        return tuple(
            source.source_id
            for source in self.sources
            if source.file_hash == file_hash
        )

    def source_ids_by_kind(self, source_kind: SourceKind) -> tuple[str, ...]:
        return tuple(
            source.source_id
            for source in self.sources
            if source.source_kind is source_kind
        )

    def kind_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {kind.value: 0 for kind in SourceKind}
        for source in self.sources:
            counts[source.source_kind.value] += 1
        return {kind: count for kind, count in counts.items() if count}

    def to_dict(self) -> dict[str, Any]:
        return {
            "index_version": self.index_version,
            "library_id": self.library_id,
            "library_name": self.library_name,
            "library_root": self.library_root,
            "given_root": self.given_root,
            "product_id": self.product_id,
            "source_count": len(self.sources),
            "sources": [source_to_dict(source) for source in self.sources],
        }

    def write_json(
        self,
        output_root: Path,
        *,
        summary: dict[str, Any] | None = None,
    ) -> list[str]:
        if not output_is_outside_source(output_root, Path(self.library_root)):
            return [BLOCKED_OUTPUT_INSIDE_SOURCE]
        destination = output_root / self.product_id / INDEX_FILE_NAME
        payload = self.to_dict()
        if summary is not None:
            payload = {"_summary": summary, **payload}
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            temporary = destination.with_name(destination.name + ".tmp")
            temporary.write_text(
                json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(temporary, destination)
        except OSError as error:
            return [f"write_failed:{destination.as_posix()}:{type(error).__name__}"]
        return []


def source_to_dict(source: Source) -> dict[str, Any]:
    return {
        "source_id": source.source_id,
        "product_id": source.product_id,
        "file_name": source.file_name,
        "relative_path": source.relative_path,
        "source_kind": source.source_kind.value,
        "file_type": source.file_type,
        "file_hash": source.file_hash,
        "parse_status": source.parse_status.value,
        "associated_product_id": source.associated_product_id,
        "source_folder": source.source_folder,
    }


def source_from_dict(payload: dict[str, Any]) -> Source:
    return Source(
        source_id=str(payload["source_id"]),
        product_id=str(payload["product_id"]),
        file_name=str(payload["file_name"]),
        relative_path=str(payload["relative_path"]),
        source_kind=SourceKind(payload["source_kind"]),
        file_type=str(payload["file_type"]),
        file_hash=str(payload["file_hash"]),
        parse_status=Status(payload["parse_status"]),
        associated_product_id=payload.get("associated_product_id"),
        source_folder=payload.get("source_folder"),
    )


def index_from_dict(payload: dict[str, Any]) -> SourceIndex:
    return SourceIndex(
        index_version=str(payload["index_version"]),
        library_id=str(payload["library_id"]),
        library_name=str(payload["library_name"]),
        library_root=str(payload["library_root"]),
        given_root=str(payload["given_root"]),
        product_id=str(payload["product_id"]),
        sources=tuple(source_from_dict(item) for item in payload["sources"]),
    )
