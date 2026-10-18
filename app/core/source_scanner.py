from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from app.core.hashing import sha256_file
from app.core.models import Source, SourceKind, Status
from app.core.source_index import (
    INDEX_VERSION,
    SourceIndex,
    library_id_of,
    product_id_of,
    source_id_of,
)

MAX_SCAN_FILES = 2000
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_CONTENT_PROBE_BYTES = 8192
MAX_DEPTH = 12
MAX_UNWRAP_LEVELS = 3
BINARY_PROBE_BYTES = 1024
MULTIBYTE_SAFE_TAIL = 4
MIN_CJK_TOKEN_LENGTH = 2
MIN_LATIN_TOKEN_LENGTH = 3
CJK_TOKEN_SLICE_LENGTH = 3

SUPPORTED_EXTENSIONS = frozenset(
    {"xlsx", "csv", "txt", "md", "jpg", "jpeg", "png", "webp", "mov", "mp4"}
)
TEXT_EXTENSIONS = frozenset({"txt", "md", "csv"})
EXTENSION_KIND: dict[str, SourceKind] = {
    "jpg": SourceKind.IMAGE,
    "jpeg": SourceKind.IMAGE,
    "png": SourceKind.IMAGE,
    "webp": SourceKind.IMAGE,
    "mov": SourceKind.VIDEO,
    "mp4": SourceKind.VIDEO,
}
IGNORED_FILE_NAMES = frozenset(
    {".DS_Store", "Thumbs.db", "desktop.ini", "ehthumbs.db"}
)
IGNORED_NAME_PREFIXES = ("._",)
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        "__MACOSX",
        "$RECYCLE.BIN",
        ".Spotlight-V100",
        ".fseventsd",
        ".Trashes",
        ".Trash",
        ".DocumentRevisions-V100",
    }
)
MACOSX_DIRECTORY_NAME = "__MACOSX"
REASON_BY_IGNORED_FILE_NAME = {
    ".DS_Store": "ds_store",
    "Thumbs.db": "windows_system_file",
    "desktop.ini": "windows_system_file",
    "ehthumbs.db": "windows_system_file",
}
SKIP_UNSUPPORTED_EXTENSION = "unsupported_extension"
SKIP_NO_EXTENSION = "no_extension"
SKIP_EMPTY_FILE = "empty_file"
SKIP_TOO_LARGE = "too_large"
SKIP_UNREADABLE_FILE = "unreadable_file"
SKIP_HIDDEN_FILE = "hidden_file"
SKIP_SYMBOLIC_LINK = "symbolic_link"
SKIP_NOT_REGULAR_FILE = "not_regular_file"
SKIP_DEPTH_EXCEEDED = "depth_exceeded"
SKIP_SCAN_LIMIT = "scan_limit_reached"
SKIP_UNREADABLE_DIRECTORY = "unreadable_directory"
ERROR_ROOT_NOT_FOUND = "material_root_not_found"
ERROR_ROOT_NOT_DIRECTORY = "material_root_not_directory"

FILENAME_SIGNALS: tuple[tuple[SourceKind, tuple[str, ...]], ...] = (
    (SourceKind.SHOOTING_PLAN, ("拍摄清单", "拍摄脚本", "分镜")),
    (SourceKind.BRAND_RULE, ("品牌规则", "品牌规范", "品牌手册", "禁用词")),
    (SourceKind.HISTORY, ("历史文案", "历史内容", "历史")),
    (SourceKind.REVIEW, ("评价", "评论", "口碑", "review")),
    (SourceKind.PRODUCT_DATA, ("经营资料", "经营", "商品数据", "参数", "规格")),
)
CONTENT_SIGNALS: tuple[tuple[SourceKind, tuple[str, ...]], ...] = (
    (SourceKind.REVIEW, ("评价洞察", "主要顾虑", "用户真实在意", "用户真正喜欢", "顾虑")),
    (SourceKind.BRAND_RULE, ("品牌名称", "品牌定位", "禁用词", "视觉关键词", "品牌提醒")),
    (SourceKind.HISTORY, ("历史内容", "相似历史款", "历史表达", "主卖点：", "标题：")),
    (SourceKind.SHOOTING_PLAN, ("拍摄清单", "拍摄脚本", "分镜", "9:16", "竖屏短视频")),
    (SourceKind.PRODUCT_DATA, ("商品理解卡", "商品名称", "商品事实", "品类：", "建议价格", "商品参数", "经营资料")),
)
FOLDER_SIGNALS: tuple[tuple[SourceKind, tuple[str, ...]], ...] = (
    (SourceKind.SHOOTING_PLAN, ("拍摄", "脚本", "分镜")),
    (SourceKind.REVIEW, ("评价", "评论", "口碑")),
    (SourceKind.BRAND_RULE, ("品牌",)),
    (SourceKind.HISTORY, ("历史",)),
    (SourceKind.PRODUCT_DATA, ("商品数据", "经营", "参数", "规格", "数据")),
)
TEXT_DECODINGS = ("utf_8_sig", "gbk")
PRODUCT_NAME_PATTERN = re.compile(
    r"^[ \t]*[-*]?[ \t]*商品名称[ \t]*[：:][ \t]*(\S.*?)[ \t]*$", re.MULTILINE
)
TOKEN_SPLIT_PATTERN = re.compile(r"[\s、,，。;；/\\|()（）\[\]【】\-_]+")
CLASSIFICATION_EXTENSION = "extension"
CLASSIFICATION_FILE_NAME = "file_name"
CLASSIFICATION_CONTENT = "content"
CLASSIFICATION_FOLDER = "folder"
CLASSIFICATION_NONE = "none"
UNKNOWN_MULTIPLE_FILE_NAME = "file_name_matches_multiple_kinds"
UNKNOWN_MULTIPLE_CONTENT = "content_matches_multiple_kinds"
UNKNOWN_MULTIPLE_FOLDER = "folder_matches_multiple_kinds"
UNKNOWN_NO_SIGNAL = "no_classification_signal"
ASSOCIATION_FULL_NAME_IN_PATH = "path_full_name"
ASSOCIATION_FULL_NAME_IN_CONTENT = "content_full_name"
ASSOCIATION_TOKEN_IN_PATH = "path_token"
ASSOCIATION_TOKEN_IN_CONTENT = "content_token"
ASSOCIATION_SOLO_CANDIDATE = "sole_candidate_inherited"
ASSOCIATION_NO_PRODUCT_NAME = "no_product_name_found"
ASSOCIATION_UNMATCHED = "unmatched_multiple_product_names"


@dataclass(frozen=True)
class IgnoredEntry:
    relative_path: str
    reason: str


@dataclass(frozen=True)
class SkippedEntry:
    relative_path: str
    reason: str
    detail: str


@dataclass(frozen=True)
class ProductCandidate:
    product_id: str
    product_name: str
    evidence_relative_path: str
    evidence_line: int


@dataclass(frozen=True)
class ScannedSource:
    source: Source
    size_bytes: int
    path_as_given: str
    filename_signals: tuple[str, ...]
    content_signals: tuple[str, ...]
    folder_signals: tuple[str, ...]
    classification_basis: str
    unknown_reason: str | None
    association_basis: str | None
    content_truncated: bool


@dataclass(frozen=True)
class ScanReport:
    library_id: str
    library_name: str
    library_root: str
    given_root: str
    product_id: str
    unwrap_records: tuple[str, ...]
    sources: tuple[ScannedSource, ...]
    skipped: tuple[SkippedEntry, ...]
    ignored: tuple[IgnoredEntry, ...]
    product_candidates: tuple[ProductCandidate, ...]
    truncated: bool
    error_code: str | None

    def index(self) -> SourceIndex:
        return SourceIndex(
            index_version=INDEX_VERSION,
            library_id=self.library_id,
            library_name=self.library_name,
            library_root=self.library_root,
            given_root=self.given_root,
            product_id=self.product_id,
            sources=tuple(item.source for item in self.sources),
        )

    def status_counts(self) -> dict[str, int]:
        counts: dict[str, int] = {status.value: 0 for status in Status}
        for item in self.sources:
            counts[item.source.parse_status.value] += 1
        return {status: count for status, count in counts.items() if count}


@dataclass(frozen=True)
class _WalkedFile:
    path: Path
    library_relative_path: str
    given_relative_path: str
    source_folder: str | None
    file_name: str
    file_type: str
    size_bytes: int


@dataclass(frozen=True)
class _Classification:
    source_kind: SourceKind
    classification_basis: str
    unknown_reason: str | None
    filename_signals: tuple[str, ...]
    content_signals: tuple[str, ...]
    folder_signals: tuple[str, ...]


def _join(prefix: str, name: str) -> str:
    return f"{prefix}/{name}" if prefix else name


def _parent_of(relative_path: str) -> str:
    if "/" not in relative_path:
        return ""
    return relative_path.rsplit("/", 1)[0]


def _file_type_of(file_name: str) -> str:
    suffix = Path(file_name).suffix
    if not suffix:
        return ""
    return suffix[1:].lower()


def _ignored_file_reason(file_name: str) -> str | None:
    if file_name in IGNORED_FILE_NAMES:
        return REASON_BY_IGNORED_FILE_NAME[file_name]
    if file_name.startswith(IGNORED_NAME_PREFIXES):
        return "macos_apple_double"
    return None


def _scan_entries(directory: Path) -> tuple[list[os.DirEntry], str | None]:
    try:
        with os.scandir(directory) as iterator:
            return list(iterator), None
    except OSError as error:
        return [], f"{type(error).__name__}:{error.strerror or error}"


def _unwrap_given_root(
    given_root: Path,
) -> tuple[
    Path,
    str,
    tuple[str, ...],
    tuple[IgnoredEntry, ...],
    tuple[SkippedEntry, ...],
]:
    unwrap_records: list[str] = []
    ignored: list[IgnoredEntry] = []
    skipped: list[SkippedEntry] = []
    root = given_root
    prefix = ""
    for _ in range(MAX_UNWRAP_LEVELS):
        entries, error = _scan_entries(root)
        if error is not None:
            break
        candidate_directories: list[str] = []
        level_ignored: list[IgnoredEntry] = []
        level_skipped: list[SkippedEntry] = []
        sibling_files = 0
        sibling_macosx = False
        for entry in entries:
            name = entry.name
            outside_path = _join(prefix, name)
            is_directory = entry.is_dir(follow_symlinks=False)
            if name in IGNORED_DIRECTORY_NAMES and is_directory:
                level_ignored.append(IgnoredEntry(outside_path, "ignored_directory"))
                if name == MACOSX_DIRECTORY_NAME:
                    sibling_macosx = True
                continue
            ignore_reason = _ignored_file_reason(name)
            if ignore_reason is not None:
                level_ignored.append(IgnoredEntry(outside_path, ignore_reason))
                continue
            if name.startswith("."):
                if is_directory:
                    level_ignored.append(IgnoredEntry(outside_path, "ignored_directory"))
                else:
                    level_skipped.append(
                        SkippedEntry(outside_path, SKIP_HIDDEN_FILE, "hidden file")
                    )
                continue
            if is_directory and not entry.is_symlink():
                candidate_directories.append(name)
            else:
                sibling_files += 1
        if len(candidate_directories) != 1 or sibling_files != 0:
            break
        only_directory = candidate_directories[0]
        if only_directory != root.name and not sibling_macosx:
            break
        # 只在真的下移一层后才记账：这一层的条目从此位于索引库根之外，
        # 不记进产物就会静默消失；没下移时交给 _walk_library 在同一层报告。
        ignored.extend(level_ignored)
        skipped.extend(level_skipped)
        root = root / only_directory
        prefix = _join(prefix, only_directory)
        unwrap_records.append(prefix)
    return root, prefix, tuple(unwrap_records), tuple(ignored), tuple(skipped)


def _walk_library(library_root: Path, unwrap_prefix: str) -> tuple[
    list[_WalkedFile],
    list[SkippedEntry],
    list[IgnoredEntry],
    bool,
]:
    files: list[_WalkedFile] = []
    skipped: list[SkippedEntry] = []
    ignored: list[IgnoredEntry] = []
    truncated = False
    stack: list[tuple[Path, str, int]] = [(library_root, "", 0)]
    while stack:
        directory, prefix, depth = stack.pop()
        entries, error = _scan_entries(directory)
        if error is not None:
            skipped.append(
                SkippedEntry(prefix or ".", SKIP_UNREADABLE_DIRECTORY, error)
            )
            continue
        for entry in entries:
            name = entry.name
            library_relative_path = _join(prefix, name)
            given_relative_path = _join(unwrap_prefix, library_relative_path)
            is_directory = entry.is_dir(follow_symlinks=False)
            if entry.is_symlink():
                skipped.append(
                    SkippedEntry(
                        given_relative_path,
                        SKIP_SYMBOLIC_LINK,
                        "symbolic links and junctions are not followed",
                    )
                )
                continue
            if is_directory:
                if name in IGNORED_DIRECTORY_NAMES:
                    ignored.append(IgnoredEntry(given_relative_path, "ignored_directory"))
                    continue
                if name in IGNORED_FILE_NAMES or name.startswith(IGNORED_NAME_PREFIXES):
                    ignored.append(IgnoredEntry(given_relative_path, "ignored_directory"))
                    continue
                if depth + 1 > MAX_DEPTH:
                    skipped.append(
                        SkippedEntry(
                            given_relative_path,
                            SKIP_DEPTH_EXCEEDED,
                            f"depth exceeds MAX_DEPTH={MAX_DEPTH}",
                        )
                    )
                    continue
                stack.append((Path(entry.path), library_relative_path, depth + 1))
                continue
            if not entry.is_file(follow_symlinks=False):
                skipped.append(
                    SkippedEntry(given_relative_path, SKIP_NOT_REGULAR_FILE, "not a regular file")
                )
                continue
            ignore_reason = _ignored_file_reason(name)
            if ignore_reason is not None:
                ignored.append(IgnoredEntry(given_relative_path, ignore_reason))
                continue
            if name.startswith("."):
                skipped.append(
                    SkippedEntry(given_relative_path, SKIP_HIDDEN_FILE, "hidden file")
                )
                continue
            file_type = _file_type_of(name)
            if not file_type:
                skipped.append(SkippedEntry(given_relative_path, SKIP_NO_EXTENSION, name))
                continue
            if file_type not in SUPPORTED_EXTENSIONS:
                skipped.append(
                    SkippedEntry(
                        given_relative_path,
                        SKIP_UNSUPPORTED_EXTENSION,
                        f".{file_type} is not a supported type",
                    )
                )
                continue
            try:
                size_bytes = entry.stat(follow_symlinks=False).st_size
            except OSError as stat_error:
                skipped.append(
                    SkippedEntry(
                        given_relative_path,
                        SKIP_UNREADABLE_FILE,
                        f"{type(stat_error).__name__}:{stat_error.strerror or stat_error}",
                    )
                )
                continue
            if size_bytes == 0:
                skipped.append(
                    SkippedEntry(
                        given_relative_path,
                        SKIP_EMPTY_FILE,
                        "0-byte files carry no evidence and would share the empty-content hash",
                    )
                )
                continue
            if size_bytes > MAX_FILE_BYTES:
                skipped.append(
                    SkippedEntry(
                        given_relative_path,
                        SKIP_TOO_LARGE,
                        f"{size_bytes} bytes exceeds MAX_FILE_BYTES={MAX_FILE_BYTES}",
                    )
                )
                continue
            files.append(
                _WalkedFile(
                    path=Path(entry.path),
                    library_relative_path=library_relative_path,
                    given_relative_path=given_relative_path,
                    source_folder=_parent_of(library_relative_path) or None,
                    file_name=name,
                    file_type=file_type,
                    size_bytes=size_bytes,
                )
            )
            if len(files) >= MAX_SCAN_FILES:
                return files, skipped, ignored, True
    return files, skipped, ignored, truncated


def _matched(table: tuple[tuple[SourceKind, tuple[str, ...]], ...], haystack: str) -> tuple[SourceKind, ...]:
    kinds: list[SourceKind] = []
    for kind, tokens in table:
        if kind in kinds:
            continue
        if any(token in haystack for token in tokens):
            kinds.append(kind)
    return tuple(kinds)


def _matched_signals(
    table: tuple[tuple[SourceKind, tuple[str, ...]], ...],
    haystack: str,
) -> tuple[str, ...]:
    signals: list[str] = []
    for kind, tokens in table:
        for token in tokens:
            if token in haystack:
                signals.append(f"{kind.value}:{token}")
    return tuple(signals)


def _decode_probe(raw: bytes) -> str:
    for encoding in TEXT_DECODINGS:
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _read_text_probe(path: Path, size_bytes: int) -> tuple[str, bool]:
    truncated = size_bytes > MAX_CONTENT_PROBE_BYTES
    try:
        with path.open("rb") as handle:
            head = handle.read(BINARY_PROBE_BYTES)
            if b"\x00" in head:
                return "", truncated
            tail = handle.read(MAX_CONTENT_PROBE_BYTES - BINARY_PROBE_BYTES)
    except OSError:
        return "", truncated
    raw = head + tail
    if truncated:
        raw = raw[: MAX_CONTENT_PROBE_BYTES - MULTIBYTE_SAFE_TAIL]
    return _decode_probe(raw), truncated


def _classify(
    file_type: str,
    file_name: str,
    source_folder: str | None,
    content_text: str,
) -> _Classification:
    lowered_name = file_name.lower()
    lowered_folder = (source_folder or "").lower()
    filename_signals = _matched_signals(FILENAME_SIGNALS, lowered_name)
    content_signals = _matched_signals(CONTENT_SIGNALS, content_text)
    folder_signals = _matched_signals(FOLDER_SIGNALS, lowered_folder)
    extension_kind = EXTENSION_KIND.get(file_type)
    if extension_kind is not None:
        return _Classification(
            source_kind=extension_kind,
            classification_basis=CLASSIFICATION_EXTENSION,
            unknown_reason=None,
            filename_signals=filename_signals,
            content_signals=(),
            folder_signals=folder_signals,
        )
    name_kinds = _matched(FILENAME_SIGNALS, lowered_name)
    if name_kinds:
        return _Classification(
            source_kind=name_kinds[0] if len(name_kinds) == 1 else SourceKind.UNKNOWN,
            classification_basis=CLASSIFICATION_FILE_NAME,
            unknown_reason=None
            if len(name_kinds) == 1
            else f"{UNKNOWN_MULTIPLE_FILE_NAME}:{','.join(kind.value for kind in name_kinds)}",
            filename_signals=filename_signals,
            content_signals=content_signals,
            folder_signals=folder_signals,
        )
    if file_type in TEXT_EXTENSIONS and content_text:
        content_kinds = _matched(CONTENT_SIGNALS, content_text)
        if content_kinds:
            return _Classification(
                source_kind=content_kinds[0] if len(content_kinds) == 1 else SourceKind.UNKNOWN,
                classification_basis=CLASSIFICATION_CONTENT,
                unknown_reason=None
                if len(content_kinds) == 1
                else f"{UNKNOWN_MULTIPLE_CONTENT}:{','.join(kind.value for kind in content_kinds)}",
                filename_signals=filename_signals,
                content_signals=content_signals,
                folder_signals=folder_signals,
            )
    folder_kinds = _matched(FOLDER_SIGNALS, lowered_folder)
    if folder_kinds:
        return _Classification(
            source_kind=folder_kinds[0] if len(folder_kinds) == 1 else SourceKind.UNKNOWN,
            classification_basis=CLASSIFICATION_FOLDER,
            unknown_reason=None
            if len(folder_kinds) == 1
            else f"{UNKNOWN_MULTIPLE_FOLDER}:{','.join(kind.value for kind in folder_kinds)}",
            filename_signals=filename_signals,
            content_signals=content_signals,
            folder_signals=folder_signals,
        )
    return _Classification(
        source_kind=SourceKind.UNKNOWN,
        classification_basis=CLASSIFICATION_NONE,
        unknown_reason=UNKNOWN_NO_SIGNAL,
        filename_signals=filename_signals,
        content_signals=content_signals,
        folder_signals=folder_signals,
    )


def _has_cjk(value: str) -> bool:
    return any("\u4e00" <= character <= "\u9fff" for character in value)


def _name_tokens(product_name: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for piece in TOKEN_SPLIT_PATTERN.split(product_name):
        if not piece or piece.isdigit():
            continue
        if _has_cjk(piece):
            if len(piece) >= MIN_CJK_TOKEN_LENGTH:
                tokens.append(piece)
            if len(piece) > CJK_TOKEN_SLICE_LENGTH:
                for start in range(len(piece) - CJK_TOKEN_SLICE_LENGTH + 1):
                    tokens.append(piece[start : start + CJK_TOKEN_SLICE_LENGTH])
        elif len(piece) >= MIN_LATIN_TOKEN_LENGTH:
            tokens.append(piece.lower())
    ordered: list[str] = []
    for token in tokens:
        if token not in ordered:
            ordered.append(token)
    return tuple(ordered)


def _collect_product_candidates(
    probes: dict[str, str],
) -> tuple[ProductCandidate, ...]:
    candidates: list[ProductCandidate] = []
    seen: set[str] = set()
    for library_relative_path in sorted(probes):
        text = probes[library_relative_path]
        for match in PRODUCT_NAME_PATTERN.finditer(text):
            name = match.group(1).strip()
            if not name or name in seen:
                continue
            seen.add(name)
            line = text.count("\n", 0, match.start(1)) + 1
            candidates.append(
                ProductCandidate(
                    product_id=product_id_of(name),
                    product_name=name,
                    evidence_relative_path=library_relative_path,
                    evidence_line=line,
                )
            )
    return tuple(candidates)


def _associate(
    library_relative_path: str,
    content_text: str,
    candidates: tuple[ProductCandidate, ...],
) -> tuple[str | None, str | None]:
    if not candidates:
        return None, ASSOCIATION_NO_PRODUCT_NAME
    lowered_path = library_relative_path.lower()
    best_id: str | None = None
    best_basis: str | None = None
    best_strength = 0
    for candidate in candidates:
        name = candidate.product_name.lower()
        strength = 0
        basis: str | None = None
        if name and name in lowered_path:
            strength, basis = 4, ASSOCIATION_FULL_NAME_IN_PATH
        elif content_text and name in content_text.lower():
            strength, basis = 3, ASSOCIATION_FULL_NAME_IN_CONTENT
        else:
            for token in _name_tokens(candidate.product_name):
                lowered_token = token.lower()
                if lowered_token in lowered_path:
                    strength, basis = 2, ASSOCIATION_TOKEN_IN_PATH
                    break
                if content_text and lowered_token in content_text.lower():
                    strength, basis = 1, ASSOCIATION_TOKEN_IN_CONTENT
        if strength > best_strength:
            best_strength, best_id, best_basis = strength, candidate.product_id, basis
    if best_strength == 0 and len(candidates) == 1:
        return candidates[0].product_id, ASSOCIATION_SOLO_CANDIDATE
    if best_strength == 0:
        return None, ASSOCIATION_UNMATCHED
    return best_id, best_basis


def scan_library(
    material_root: Path,
    *,
    product_id: str | None = None,
    library_id: str | None = None,
) -> ScanReport:
    given_root = Path(material_root)
    given_name = given_root.name
    empty_report_kwargs = {
        "library_id": library_id or library_id_of(given_name),
        "library_name": given_name,
        "library_root": given_root.as_posix(),
        "given_root": given_root.as_posix(),
        "product_id": product_id or product_id_of(given_name),
        "unwrap_records": (),
        "sources": (),
        "skipped": (),
        "ignored": (),
        "product_candidates": (),
        "truncated": False,
    }
    if not given_root.exists():
        return ScanReport(error_code=ERROR_ROOT_NOT_FOUND, **empty_report_kwargs)
    if not given_root.is_dir():
        return ScanReport(error_code=ERROR_ROOT_NOT_DIRECTORY, **empty_report_kwargs)

    library_root, unwrap_prefix, unwrap_records, unwrap_ignored, unwrap_skipped = (
        _unwrap_given_root(given_root)
    )
    library_name = library_root.name
    resolved_library_id = (library_id or library_id_of(library_name))
    walked_files, skipped, ignored, truncated = _walk_library(library_root, unwrap_prefix)
    skipped = [*unwrap_skipped, *skipped]

    hashes: dict[str, str] = {}
    readable_files: list[_WalkedFile] = []
    for walked in walked_files:
        try:
            hashes[walked.library_relative_path] = sha256_file(walked.path)
        except OSError as read_error:
            skipped.append(
                SkippedEntry(
                    walked.given_relative_path,
                    SKIP_UNREADABLE_FILE,
                    f"{type(read_error).__name__}:{read_error.strerror or read_error}",
                )
            )
            continue
        readable_files.append(walked)

    probes: dict[str, str] = {}
    truncated_probes: dict[str, bool] = {}
    for walked in readable_files:
        if walked.file_type in TEXT_EXTENSIONS:
            text, was_truncated = _read_text_probe(walked.path, walked.size_bytes)
            probes[walked.library_relative_path] = text
            truncated_probes[walked.library_relative_path] = was_truncated

    candidates = _collect_product_candidates(probes)
    if product_id is None:
        if len(candidates) == 1:
            resolved_product_id = candidates[0].product_id
        else:
            resolved_product_id = product_id_of(library_name)
    else:
        resolved_product_id = product_id

    results: list[ScannedSource] = []
    for walked in readable_files:
        content_text = probes.get(walked.library_relative_path, "")
        classification = _classify(
            walked.file_type,
            walked.file_name,
            walked.source_folder,
            content_text,
        )
        associated_product_id, association_basis = _associate(
            walked.library_relative_path,
            content_text,
            candidates,
        )
        needs_confirmation = (
            classification.source_kind is SourceKind.UNKNOWN
            or (association_basis == ASSOCIATION_UNMATCHED)
        )
        results.append(
            ScannedSource(
                source=Source(
                    source_id=source_id_of(resolved_library_id, walked.library_relative_path),
                    product_id=resolved_product_id,
                    file_name=walked.file_name,
                    relative_path=walked.library_relative_path,
                    source_kind=classification.source_kind,
                    file_type=walked.file_type,
                    file_hash=hashes[walked.library_relative_path],
                    parse_status=Status.NEEDS_CONFIRMATION
                    if needs_confirmation
                    else Status.READY,
                    associated_product_id=associated_product_id,
                    source_folder=walked.source_folder,
                ),
                size_bytes=walked.size_bytes,
                path_as_given=walked.given_relative_path,
                filename_signals=classification.filename_signals,
                content_signals=classification.content_signals,
                folder_signals=classification.folder_signals,
                classification_basis=classification.classification_basis,
                unknown_reason=classification.unknown_reason,
                association_basis=association_basis,
                content_truncated=truncated_probes.get(walked.library_relative_path, False),
            )
        )
    results.sort(key=lambda item: item.source.relative_path)
    skipped.sort(key=lambda item: (item.relative_path, item.reason))
    ignored_all = tuple(
        sorted(
            [*unwrap_ignored, *ignored],
            key=lambda item: (item.relative_path, item.reason),
        )
    )
    if truncated:
        skipped.append(
            SkippedEntry(
                library_name,
                SKIP_SCAN_LIMIT,
                f"scan stopped at MAX_SCAN_FILES={MAX_SCAN_FILES}",
            )
        )
    return ScanReport(
        library_id=resolved_library_id,
        library_name=library_name,
        library_root=library_root.as_posix(),
        given_root=given_root.as_posix(),
        product_id=resolved_product_id,
        unwrap_records=unwrap_records,
        sources=tuple(results),
        skipped=tuple(skipped),
        ignored=ignored_all,
        product_candidates=candidates,
        truncated=truncated,
        error_code=None,
    )
