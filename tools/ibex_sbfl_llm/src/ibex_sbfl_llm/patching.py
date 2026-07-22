"""Discover and apply the bug-insertion patch without mutating the source tree."""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .errors import PatchError
from .io_utils import read_text, sha256_file

DIFF_LINE_RE = re.compile(r"^\[DIFF\]\s+(?P<path>.+?)\s*$")
PATCH_HEADER_RE = re.compile(r"^(?:---|\+\+\+)\s+(?P<path>\S+)")


@dataclass(frozen=True)
class PatchWorkspace:
    rtl_root: Path
    patch_path: Path | None
    patch_sha256: str | None
    patch_state: str
    changed_files: tuple[str, ...]
    before_hashes: dict[str, str]
    after_hashes: dict[str, str]


def discover_patch(run_log: Path) -> Path | None:
    if not run_log.is_file():
        return None
    matches: list[str] = []
    for line in read_text(run_log).splitlines():
        match = DIFF_LINE_RE.match(line.strip())
        if match:
            matches.append(match.group("path"))
    if not matches:
        return None
    unique = list(dict.fromkeys(matches))
    if len(unique) != 1:
        raise PatchError(f"run.log declares multiple [DIFF] paths: {unique}")
    raw_path = Path(unique[0]).expanduser()
    if not raw_path.is_absolute():
        raw_path = run_log.parent / raw_path
    return raw_path.resolve()


def patch_changed_files(patch_path: Path) -> tuple[str, ...]:
    changed: list[str] = []
    for line in read_text(patch_path).splitlines():
        match = PATCH_HEADER_RE.match(line)
        if not match:
            continue
        raw = match.group("path")
        if raw == "/dev/null":
            continue
        if raw.startswith(("a/", "b/")):
            raw = raw[2:]
        path = PurePosixPath(raw)
        if path.is_absolute() or ".." in path.parts:
            raise PatchError(f"patch contains unsafe path: {raw!r}")
        if len(path.parts) < 2 or path.parts[0] != "rtl":
            raise PatchError(f"patch may only modify files below rtl/: {raw!r}")
        normalized = path.as_posix()
        if normalized not in changed:
            changed.append(normalized)
    if not changed:
        raise PatchError(f"patch contains no files below rtl/: {patch_path}")
    return tuple(changed)


def _run_git_apply(
    workspace: Path, patch_path: Path, *args: str
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "apply", "--whitespace=nowarn", *args, str(patch_path)],
        cwd=workspace,
        text=True,
        capture_output=True,
        check=False,
    )


def _hash_changed(workspace: Path, changed_files: tuple[str, ...]) -> dict[str, str]:
    hashes: dict[str, str] = {}
    for relative in changed_files:
        path = workspace / relative
        if path.is_file():
            hashes[relative] = sha256_file(path)
    return hashes


@contextmanager
def prepare_rtl_workspace(
    rtl_root: Path,
    sbfl_dir: Path,
    explicit_patch: Path | None,
    allow_unpatched: bool,
) -> Iterator[PatchWorkspace]:
    rtl_root = rtl_root.resolve()
    if not rtl_root.is_dir():
        raise PatchError(f"RTL source directory does not exist: {rtl_root}")

    patch_path = (
        explicit_patch.resolve()
        if explicit_patch is not None
        else discover_patch(sbfl_dir / "run.log")
    )
    if patch_path is None:
        if not allow_unpatched:
            raise PatchError(
                f"cannot discover a [DIFF] patch in {sbfl_dir / 'run.log'}; "
                "pass --patch or explicitly use --allow-unpatched-source"
            )
        yield PatchWorkspace(rtl_root, None, None, "not_available", (), {}, {})
        return
    if not patch_path.is_file():
        raise PatchError(f"declared patch does not exist: {patch_path}")

    changed_files = patch_changed_files(patch_path)
    with tempfile.TemporaryDirectory(prefix="ibex-sbfl-llm-") as temporary:
        workspace = Path(temporary) / "workspace"
        copied_rtl = workspace / "rtl"
        workspace.mkdir(parents=True)
        shutil.copytree(rtl_root, copied_rtl, symlinks=True)
        before_hashes = _hash_changed(workspace, changed_files)

        check = _run_git_apply(workspace, patch_path, "--check")
        if check.returncode == 0:
            applied = _run_git_apply(workspace, patch_path)
            if applied.returncode != 0:
                raise PatchError(f"failed to apply {patch_path}: {applied.stderr.strip()}")
            patch_state = "applied"
        else:
            reverse = _run_git_apply(workspace, patch_path, "--reverse", "--check")
            if reverse.returncode != 0:
                detail = check.stderr.strip() or reverse.stderr.strip()
                raise PatchError(f"patch does not match RTL source {rtl_root}: {detail}")
            patch_state = "already_patched"

        after_hashes = _hash_changed(workspace, changed_files)
        if patch_state == "applied" and before_hashes == after_hashes:
            raise PatchError(f"patch application did not change any tracked RTL file: {patch_path}")

        yield PatchWorkspace(
            rtl_root=copied_rtl,
            patch_path=patch_path,
            patch_sha256=sha256_file(patch_path),
            patch_state=patch_state,
            changed_files=changed_files,
            before_hashes=before_hashes,
            after_hashes=after_hashes,
        )
