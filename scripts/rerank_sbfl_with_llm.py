#!/usr/bin/env python3
"""Rerank one SBFL result directory or every result below a parent directory."""

from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence


REQUIRED_FILES = ("blocks.json", "run.log", "result.log")
BATCH_FLAGS = {"--skip-existing", "--stop-on-error", "--list-only"}


def print_usage() -> None:
    print(
        """\
Usage:
  rerank_sbfl_with_llm.py RTL_SOURCE RESULT_OR_PARENT [OPTIONS]

RESULT_OR_PARENT behavior:
  - If it is a result directory containing blocks.json, run.log, and result.log,
    rerank that directory once.
  - If it is a result.log file, rerank its containing directory once.
  - Otherwise, recursively discover and sequentially rerank all complete result
    directories below it.

Batch options:
  --skip-existing  Skip directories that already contain llm_rerank.json.
  --stop-on-error  Stop the batch after the first failed rerank.
  --list-only      Print commands without invoking uv or the model API.

All other options are forwarded to `ibex-sbfl rerank`. The model defaults to
gpt-5.5 when --model is omitted. In particular, the forwarded --dry-run option
builds and prints each actual model prompt; use --list-only to preview commands.

Examples:
  python3 scripts/rerank_sbfl_with_llm.py rtl logs/run/case --model gpt-5.5
  python3 scripts/rerank_sbfl_with_llm.py rtl logs/run --skip-existing
  python3 scripts/rerank_sbfl_with_llm.py rtl logs/run --list-only
"""
    )


def split_arguments(argv: Sequence[str]) -> tuple[str, Path, list[str], set[str]]:
    if not argv or any(argument in {"-h", "--help"} for argument in argv):
        print_usage()
        raise SystemExit(0)
    if len(argv) < 2 or argv[0].startswith("-") or argv[1].startswith("-"):
        print_usage()
        raise ValueError(
            "RTL_SOURCE and RESULT_OR_PARENT must be the first two arguments"
        )

    rtl_source = argv[0]
    target = Path(argv[1]).expanduser().resolve()
    forwarded: list[str] = []
    batch_flags: set[str] = set()
    for argument in argv[2:]:
        if argument in BATCH_FLAGS:
            batch_flags.add(argument)
        else:
            forwarded.append(argument)
    return rtl_source, target, forwarded, batch_flags


def has_option(arguments: Sequence[str], name: str) -> bool:
    return any(
        argument == name or argument.startswith(f"{name}=") for argument in arguments
    )


def with_default_model(arguments: Sequence[str]) -> list[str]:
    result = list(arguments)
    if not has_option(result, "--model"):
        result.extend(["--model", "gpt-5.5"])
    return result


def is_result_dir(path: Path) -> bool:
    return path.is_dir() and all((path / name).is_file() for name in REQUIRED_FILES)


def discover_result_dirs(parent: Path) -> tuple[list[Path], list[Path]]:
    complete: set[Path] = set()
    incomplete: set[Path] = set()
    for blocks_json in parent.rglob("blocks.json"):
        result_dir = blocks_json.parent
        if not (result_dir / "run.log").is_file():
            continue
        if (result_dir / "result.log").is_file():
            complete.add(result_dir.resolve())
        else:
            incomplete.add(result_dir.resolve())
    return sorted(complete, key=str), sorted(incomplete, key=str)


def build_command(rtl_source: str, result: Path, forwarded: Sequence[str]) -> list[str]:
    return [
        "uv",
        "run",
        "--project",
        "tools/ibex_sbfl_llm",
        "--frozen",
        "ibex-sbfl",
        "rerank",
        rtl_source,
        str(result),
        *forwarded,
    ]


def execute(command: Sequence[str], repo_root: Path) -> int:
    try:
        return subprocess.run(command, cwd=repo_root, check=False).returncode
    except OSError as exc:
        print(f"[ERROR] cannot start command: {exc}", file=sys.stderr)
        return 127


def run_single(
    rtl_source: str,
    result: Path,
    forwarded: Sequence[str],
    batch_flags: set[str],
    repo_root: Path,
) -> int:
    result_dir = result.parent if result.is_file() else result
    if "--skip-existing" in batch_flags and (result_dir / "llm_rerank.json").is_file():
        print(f"[SKIP] existing result: {result_dir / 'llm_rerank.json'}")
        return 0
    command = build_command(rtl_source, result, forwarded)
    if "--list-only" in batch_flags:
        print(f"[LIST] {shlex.join(command)}")
        return 0
    print(f"[RUN] {shlex.join(command)}", flush=True)
    returncode = execute(command, repo_root)
    label = "OK" if returncode == 0 else "FAIL"
    stream = sys.stdout if returncode == 0 else sys.stderr
    print(f"[{label}] exit={returncode} {result_dir}", file=stream)
    return returncode


def run_batch(
    rtl_source: str,
    parent: Path,
    forwarded: Sequence[str],
    batch_flags: set[str],
    repo_root: Path,
) -> int:
    if has_option(forwarded, "--output"):
        print(
            "[ERROR] --output cannot be shared by a parent-directory batch; "
            "each result uses its default llm_rerank.json",
            file=sys.stderr,
        )
        return 2
    try:
        result_dirs, incomplete_dirs = discover_result_dirs(parent)
    except OSError as exc:
        print(f"[ERROR] failed to scan {parent}: {exc}", file=sys.stderr)
        return 2

    for result_dir in incomplete_dirs:
        print(
            f"[WARN] skip incomplete SBFL directory without result.log: {result_dir}",
            file=sys.stderr,
        )
    if not result_dirs:
        print(
            f"[ERROR] no directories containing {', '.join(REQUIRED_FILES)} found below {parent}",
            file=sys.stderr,
        )
        return 1

    selected: list[Path] = []
    skipped = 0
    for result_dir in result_dirs:
        if (
            "--skip-existing" in batch_flags
            and (result_dir / "llm_rerank.json").is_file()
        ):
            skipped += 1
            print(f"[SKIP] existing result: {result_dir / 'llm_rerank.json'}")
        else:
            selected.append(result_dir)

    print(
        f"[INFO] discovered={len(result_dirs)}, selected={len(selected)}, "
        f"skipped={skipped}, incomplete={len(incomplete_dirs)}"
    )
    if "--list-only" in batch_flags:
        for index, result_dir in enumerate(selected, 1):
            command = build_command(rtl_source, result_dir, forwarded)
            print(f"[LIST {index}/{len(selected)}] {shlex.join(command)}")
        return 0

    succeeded = 0
    failures: list[tuple[Path, int]] = []
    for index, result_dir in enumerate(selected, 1):
        command = build_command(rtl_source, result_dir, forwarded)
        print(f"[RUN {index}/{len(selected)}] {shlex.join(command)}", flush=True)
        returncode = execute(command, repo_root)
        if returncode == 0:
            succeeded += 1
            print(f"[OK] {result_dir}")
            continue
        failures.append((result_dir, returncode))
        print(f"[FAIL] {result_dir}: exit={returncode}", file=sys.stderr)
        if "--stop-on-error" in batch_flags:
            break

    print(
        f"[SUMMARY] succeeded={succeeded}, failed={len(failures)}, "
        f"skipped={skipped}, incomplete={len(incomplete_dirs)}"
    )
    for result_dir, returncode in failures:
        print(f"[FAILED] exit={returncode} {result_dir}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        rtl_source, target, forwarded, batch_flags = split_arguments(
            sys.argv[1:] if argv is None else argv
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if not target.exists():
        print(
            f"[ERROR] result or parent path does not exist: {target}", file=sys.stderr
        )
        return 2
    if target.is_file() and target.name != "result.log":
        print(
            f"[ERROR] the only supported result file is result.log: {target}",
            file=sys.stderr,
        )
        return 2
    if target.is_file() and not is_result_dir(target.parent):
        print(
            f"[ERROR] result.log directory is missing one of {', '.join(REQUIRED_FILES)}: "
            f"{target.parent}",
            file=sys.stderr,
        )
        return 2

    repo_root = Path(__file__).resolve().parents[1]
    if not (repo_root / "tools" / "ibex_sbfl_llm").is_dir():
        print(f"[ERROR] unified project is missing below {repo_root}", file=sys.stderr)
        return 2

    forwarded = with_default_model(forwarded)
    single = target.is_file() or is_result_dir(target)
    if single:
        return run_single(rtl_source, target, forwarded, batch_flags, repo_root)
    if not target.is_dir():
        print(f"[ERROR] parent path is not a directory: {target}", file=sys.stderr)
        return 2
    return run_batch(rtl_source, target, forwarded, batch_flags, repo_root)


if __name__ == "__main__":
    raise SystemExit(main())
