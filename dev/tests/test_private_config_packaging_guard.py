"""私有 AI 配置不得进入版本库。

忽略规则由真实 git 判定（git check-ignore、git status），本文件不自写匹配正则。
本文件不依赖真实工作区当前有哪些私有文件（存在与否都不该让测试变红），
也不为人工右键压缩提供任何保护——那需要打包入口自己过滤，本批没有这个入口。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from app.demo_paths import REPO_ROOT
GITIGNORE_PATH = REPO_ROOT / ".gitignore"
GIT = shutil.which("git")

PLACEHOLDER = b"packaging-guard placeholder, no credential\n"

REQUIRED_PATTERNS = (
    ".ai-settings.private.json",
    ".ai-settings-*.tmp",
    ".ai-capabilities.private.json",
    ".ai-capabilities-*.tmp",
)
PRESERVED_RULES = (".venv/", "__pycache__/", "*.py[cod]", ".pytest_cache/", ".env", "*.log")

SENSITIVE_CASES = (
    (".ai-settings.private.json", (".ai-settings.private.json", ".ai-*.private.json")),
    (".ai-capabilities.private.json", (".ai-capabilities.private.json", ".ai-*.private.json")),
    (".ai-other.private.json", (".ai-*.private.json",)),
    (".ai-capabilities-2f4c1a.tmp", (".ai-capabilities-*.tmp",)),
    (".ai-capabilities-test.tmp", (".ai-capabilities-*.tmp",)),
    (".ai-settings-7b3e9c.tmp", (".ai-settings-*.tmp",)),
)
ORDINARY_NAMES = (
    "README.md",
    "pyproject.toml",
    "workspace/example.json",
    "app/core/llm_config.py",
    ".ai-settings.json",
    "ai-capabilities.private.json",
    "notes.private.json",
)

_OPENED_WATCHED: list[str] = []
_WATCHED = {
    str((REPO_ROOT / name).resolve())
    for name in (".ai-settings.private.json", ".ai-capabilities.private.json", ".env")
}


def _audit(event: str, args: tuple[object, ...]) -> None:
    if event != "open" or not args:
        return
    raw = args[0]
    if isinstance(raw, bytes):
        raw = os.fsdecode(raw)
    elif isinstance(raw, Path):
        raw = str(raw)
    if not isinstance(raw, str):
        return
    if ".ai-" not in raw and ".env" not in raw:
        return
    try:
        key = str(Path(raw).resolve())
    except OSError:
        return
    if key in _WATCHED:
        _OPENED_WATCHED.append(key)


sys.addaudithook(_audit)


@pytest.fixture(scope="module", autouse=True)
def real_root_untouched():
    def listing() -> list[tuple[str, int, int]]:
        rows: list[tuple[str, int, int]] = []
        for entry in os.scandir(REPO_ROOT):
            if entry.name.startswith(".ai-") or entry.name == ".env":
                stat = entry.stat()
                rows.append((entry.name, stat.st_size, stat.st_mtime_ns))
        return sorted(rows)

    before = listing()
    yield
    assert listing() == before, "本测试文件改动了真实工作区根目录下的私有配置文件"


@pytest.fixture(autouse=True)
def never_opens_real_private_config():
    _OPENED_WATCHED.clear()
    yield
    assert _OPENED_WATCHED == [], f"测试打开了真实工作区私有配置：{_OPENED_WATCHED}"


def _git_or_fail() -> str:
    if GIT is None:
        pytest.fail("本机没有 git：忽略规则不能只靠人眼看文本，禁止退化成自写正则发证")
    return GIT


def _run_git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.update({"GIT_CONFIG_NOSYSTEM": "1", "HOME": str(repo.parent), "USERPROFILE": str(repo.parent)})
    return subprocess.run(
        [_git_or_fail(), "-C", str(repo), "-c", "safe.directory=*", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
        check=False,
    )


def _scratch_repo(tmp_path: Path, names: tuple[str, ...]) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    shutil.copyfile(GITIGNORE_PATH, repo / ".gitignore")
    for name in names:
        target = repo / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(PLACEHOLDER)
    assert _run_git(repo, "init", "-q").returncode == 0
    return repo


def _ignore_rule(repo: Path, name: str) -> str | None:
    result = _run_git(repo, "check-ignore", "-v", "--no-index", "--", name)
    if result.returncode == 1:
        return None
    assert result.returncode == 0, f"git check-ignore 执行失败：{result.stderr.strip()}"
    return result.stdout.splitlines()[0].split("\t")[0]


def _untracked(repo: Path) -> set[str]:
    result = _run_git(repo, "status", "--porcelain", "--untracked-files=all")
    assert result.returncode == 0, f"git status 执行失败：{result.stderr.strip()}"
    return {line[3:].strip() for line in result.stdout.splitlines() if line.startswith("?? ")}


def _gitignore_lines() -> list[str]:
    return [
        line.strip()
        for line in GITIGNORE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


# --- 一、忽略规则文本 ---

def test_gitignore_sits_at_the_workspace_root():
    assert GITIGNORE_PATH.is_file()
    assert GITIGNORE_PATH.parent == REPO_ROOT


@pytest.mark.parametrize("pattern", REQUIRED_PATTERNS)
def test_gitignore_text_declares_every_private_and_temp_pattern(pattern: str):
    assert pattern in _gitignore_lines(), f".gitignore 缺少规则：{pattern}"


@pytest.mark.parametrize("rule", PRESERVED_RULES)
def test_pre_existing_ignore_rules_are_still_there(rule: str):
    assert rule in _gitignore_lines(), f"既有忽略规则被删掉了：{rule}"


def test_v1_and_v2_private_files_are_listed_side_by_side():
    lines = _gitignore_lines()
    assert ".ai-settings.private.json" in lines
    assert ".ai-capabilities.private.json" in lines


# --- 二、真实 git 对根目录文件名的判定 ---

@pytest.mark.parametrize(("name", "accepted_patterns"), SENSITIVE_CASES)
def test_git_actually_ignores_private_and_temp_names(tmp_path: Path, name: str, accepted_patterns: tuple[str, ...]):
    rule = _ignore_rule(_scratch_repo(tmp_path, (name,)), name)
    assert rule is not None, f"git 认为 {name} 未被忽略"
    matched = rule.rsplit(":", 1)[-1]
    assert matched in accepted_patterns, f"{name} 命中的是意料之外的规则：{rule}"


@pytest.mark.parametrize("name", ORDINARY_NAMES)
def test_git_does_not_misjudge_ordinary_project_files(tmp_path: Path, name: str):
    assert _ignore_rule(_scratch_repo(tmp_path, (name,)), name) is None, f"{name} 被误判为私有配置"


def test_private_names_are_excluded_from_the_untracked_listing(tmp_path: Path):
    repo = _scratch_repo(tmp_path, tuple(case[0] for case in SENSITIVE_CASES))
    untracked = _untracked(repo)
    for name, _ in SENSITIVE_CASES:
        assert name not in untracked, f"{name} 仍会被 git add -A 收进去"
    assert ".gitignore" in untracked


def test_ordinary_files_remain_in_the_untracked_listing(tmp_path: Path):
    repo = _scratch_repo(tmp_path, ORDINARY_NAMES)
    untracked = _untracked(repo)
    for name in ORDINARY_NAMES:
        assert name in untracked, f"{name} 不该被忽略却消失了"


def test_private_names_are_ignored_at_any_depth(tmp_path: Path):
    repo = _scratch_repo(tmp_path, ("workspace/.ai-capabilities.private.json",))
    assert _ignore_rule(repo, "workspace/.ai-capabilities.private.json") is not None


def test_the_rules_only_cover_ai_prefixed_names(tmp_path: Path):
    repo = _scratch_repo(tmp_path, ("notes.private.json", "secret.json"))
    assert _ignore_rule(repo, "notes.private.json") is None
    assert _ignore_rule(repo, "secret.json") is None


def test_renaming_the_temp_prefix_out_of_the_ai_family_escapes_the_rule(tmp_path: Path):
    repo = _scratch_repo(tmp_path, (".other-capabilities-1.tmp",))
    assert _ignore_rule(repo, ".other-capabilities-1.tmp") is None


# --- 三、测试自身的外发物内容 ---

def test_scratch_placeholders_hold_no_credential_text(tmp_path: Path):
    names = tuple(case[0] for case in SENSITIVE_CASES) + ORDINARY_NAMES
    repo = _scratch_repo(tmp_path, names)
    written = [
        path
        for path in repo.rglob("*")
        if path.is_file()
        and path.name != ".gitignore"
        and ".git" not in path.relative_to(repo).parts
    ]
    assert len(written) == len(names)
    for path in written:
        assert path.read_bytes() == PLACEHOLDER, f"{path.name} 的内容不是无凭据占位文本"
    lowered = PLACEHOLDER.decode("utf-8").lower()
    for marker in ("sk" + "-", "bear" + "er", "proje" + "ct_id"):
        assert marker not in lowered, f"占位文本里混进了凭据形状：{marker}"


def test_this_test_file_itself_contains_no_key_literal():
    source = Path(__file__).read_text(encoding="utf-8")
    for marker in ("sk" + "-", "Bear" + "er "):
        assert marker not in source, f"本测试文件里出现了可疑凭据片段：{marker}"


# --- 四、哨兵有效性（否则上面的断言是空转） ---

def test_the_open_sentinel_reacts_when_a_watched_path_is_opened(tmp_path: Path):
    decoy = tmp_path / ".ai-capabilities.private.json"
    decoy.write_bytes(PLACEHOLDER)
    watched = str(decoy.resolve())
    _WATCHED.add(watched)
    try:
        decoy.read_text(encoding="utf-8")
        assert watched in _OPENED_WATCHED, "哨兵没响，那么「测试未读取真实私有配置」的断言就是空转"
    finally:
        _WATCHED.discard(watched)
        _OPENED_WATCHED.clear()


def test_the_open_sentinel_reacts_to_the_builtin_open_call(tmp_path: Path):
    decoy = tmp_path / ".ai-settings.private.json"
    decoy.write_bytes(PLACEHOLDER)
    watched = str(decoy.resolve())
    _WATCHED.add(watched)
    try:
        with open(decoy, "rb"):
            pass
        assert watched in _OPENED_WATCHED
    finally:
        _WATCHED.discard(watched)
        _OPENED_WATCHED.clear()


# --- 五、内容导出压缩：它自己不过滤，保护只来自 root_dir 范围 ---

def test_content_export_zip_applies_no_filter_of_its_own(tmp_path: Path):
    from app.web.export_download import build_export_zip

    package = tmp_path / "prod_placeholder"
    package.mkdir()
    (package / "content.json").write_bytes(PLACEHOLDER)
    (package / ".ai-capabilities.private.json").write_bytes(PLACEHOLDER)
    zip_path = build_export_zip(package)
    try:
        names = set(zipfile.ZipFile(zip_path).namelist())
        assert ".ai-capabilities.private.json" in names, (
            "导出压缩现在会过滤私有文件名了，请同步更新本用例的口径"
        )
    finally:
        zip_path.unlink(missing_ok=True)
