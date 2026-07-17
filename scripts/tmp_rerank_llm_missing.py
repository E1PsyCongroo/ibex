#!/usr/bin/env python3
"""Temporarily rerank only LLM_MISSING cases from llm_rerank_summary.tsv."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Sequence


DEFAULT_RESULTS_ROOT = "2026-07-12-01-01-43_232951"
DEFAULT_SUMMARY = "llm_rerank_summary.tsv"
DEFAULT_BUGSET_ROOT = "verify_dataset"
RUN_DIR_RE = re.compile(r"^[^_]+_(?P<bugset>\d+)_(?P<module>.+)$")
REQUIRED_RESULT_FILES = ("blocks.json", "run.log", "result.log")


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Read llm_rerank_summary.tsv and rerank only rows whose status is "
            "LLM_MISSING."
        )
    )
    parser.add_argument("--summary", type=Path, default=Path(DEFAULT_SUMMARY))
    parser.add_argument("--results-root", type=Path, default=Path(DEFAULT_RESULTS_ROOT))
    parser.add_argument("--bugset-root", type=Path, default=Path(DEFAULT_BUGSET_ROOT))
    parser.add_argument("--model", default="gpt-5.5")
    parser.add_argument(
        "-j",
        "--jobs",
        type=positive_int,
        default=1,
        help="number of rerank commands to run concurrently (default: 1)",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip a selected case when llm_rerank.json already exists",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help=(
            "stop scheduling new reranks after the first failure; already running "
            "reranks are allowed to finish"
        ),
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="validate and print commands without calling the model API",
    )
    return parser.parse_args(argv)


def read_missing_cases(summary_path: Path) -> list[tuple[str, str]]:
    with summary_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        required = {"bugset", "diff", "status"}
        missing_columns = required - set(reader.fieldnames or [])
        if missing_columns:
            raise ValueError(
                f"summary is missing required columns: {sorted(missing_columns)}"
            )
        cases: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for row in reader:
            if row["status"] != "LLM_MISSING":
                continue
            key = (row["bugset"].strip(), row["diff"].strip())
            if not key[0] or not key[1]:
                raise ValueError(f"LLM_MISSING row has empty bugset/diff: {row}")
            if key not in seen:
                seen.add(key)
                cases.append(key)
    return cases


def index_result_dirs(results_root: Path) -> dict[tuple[str, str], Path]:
    result: dict[tuple[str, str], Path] = {}
    for path in sorted(results_root.iterdir(), key=lambda value: value.name):
        if not path.is_dir():
            continue
        match = RUN_DIR_RE.fullmatch(path.name)
        if match is None:
            continue
        key = (match.group("bugset"), match.group("module"))
        if key in result:
            raise ValueError(
                f"duplicate result directories for bugset/module {key}: "
                f"{result[key]} and {path}"
            )
        result[key] = path.resolve()
    return result


def module_from_diff(diff_name: str) -> str:
    suffix = ".sv.diff"
    if not diff_name.endswith(suffix):
        raise ValueError(f"unexpected diff name, expected *.sv.diff: {diff_name}")
    return diff_name[: -len(suffix)]


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


def build_command(
    result_dir: Path,
    patch_path: Path,
    model: str,
    repo_root: Path,
) -> list[str]:
    return [
        "uv",
        "run",
        "--project",
        "tools/sbfl_llm",
        "--frozen",
        "ibex-sbfl",
        "rerank",
        "rtl",
        display_path(result_dir, repo_root),
        "--model",
        model,
        "--patch",
        display_path(patch_path, repo_root),
        "--save-prompt",
    ]


def run_job(
    index: int,
    total: int,
    job: tuple[str, str, Path, Path],
    model: str,
    repo_root: Path,
) -> tuple[str, str, int]:
    bugset, diff_name, result_dir, patch_path = job
    command = build_command(result_dir, patch_path, model, repo_root)
    print(
        f"[RUN {index}/{total}] {bugset}/{diff_name}\n  {shlex.join(command)}",
        flush=True,
    )
    try:
        completed = subprocess.run(command, cwd=repo_root, check=False)
        returncode = completed.returncode
    except OSError as exc:
        print(f"[FAIL] cannot start command: {exc}", file=sys.stderr)
        returncode = 127
    return bugset, diff_name, returncode


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    repo_root = Path(__file__).resolve().parents[1]
    summary_path = args.summary.expanduser().resolve()
    results_root = args.results_root.expanduser().resolve()
    bugset_root = args.bugset_root.expanduser().resolve()

    for label, path, kind in (
        ("summary", summary_path, "file"),
        ("results root", results_root, "directory"),
        ("bugset root", bugset_root, "directory"),
    ):
        valid = path.is_file() if kind == "file" else path.is_dir()
        if not valid:
            print(f"[ERROR] {label} {kind} does not exist: {path}", file=sys.stderr)
            return 2

    try:
        missing_cases = read_missing_cases(summary_path)
        result_index = index_result_dirs(results_root)
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 2

    if not missing_cases:
        print(f"[INFO] no LLM_MISSING rows in {summary_path}")
        return 0

    jobs: list[tuple[str, str, Path, Path]] = []
    validation_errors: list[str] = []
    for bugset, diff_name in missing_cases:
        try:
            module = module_from_diff(diff_name)
        except ValueError as exc:
            validation_errors.append(str(exc))
            continue
        result_dir = result_index.get((bugset, module))
        if result_dir is None:
            validation_errors.append(
                f"no result directory matching *_{bugset}_{module} "
                f"for {bugset}/{diff_name}"
            )
            continue
        missing_files = [
            name for name in REQUIRED_RESULT_FILES if not (result_dir / name).is_file()
        ]
        if missing_files:
            validation_errors.append(
                f"incomplete result directory {result_dir}, missing {missing_files}"
            )
            continue
        patch_path = (bugset_root / bugset / diff_name).resolve()
        if not patch_path.is_file():
            validation_errors.append(f"patch does not exist: {patch_path}")
            continue
        jobs.append((bugset, diff_name, result_dir, patch_path))

    if validation_errors:
        for error in validation_errors:
            print(f"[ERROR] {error}", file=sys.stderr)
        print(
            f"[ERROR] validation failed for {len(validation_errors)} of "
            f"{len(missing_cases)} LLM_MISSING cases; no commands were run",
            file=sys.stderr,
        )
        return 2

    selected: list[tuple[str, str, Path, Path]] = []
    skipped = 0
    for job in jobs:
        result_dir = job[2]
        if args.skip_existing and (result_dir / "llm_rerank.json").is_file():
            skipped += 1
            print(f"[SKIP] existing result: {result_dir / 'llm_rerank.json'}")
        else:
            selected.append(job)

    print(
        f"[INFO] llm_missing={len(missing_cases)}, selected={len(selected)}, "
        f"skipped={skipped}"
    )
    if args.list_only:
        for index, (_, _, result_dir, patch_path) in enumerate(selected, 1):
            command = build_command(result_dir, patch_path, args.model, repo_root)
            print(f"[LIST {index}/{len(selected)}] {shlex.join(command)}")
        return 0

    succeeded = 0
    failures: list[tuple[str, str, int]] = []
    next_job = 0
    stop_scheduling = False
    max_workers = min(args.jobs, len(selected)) if selected else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        pending: dict[concurrent.futures.Future[tuple[str, str, int]], int] = {}

        def submit_one(job_index: int) -> None:
            future = executor.submit(
                run_job,
                job_index + 1,
                len(selected),
                selected[job_index],
                args.model,
                repo_root,
            )
            pending[future] = job_index

        while next_job < len(selected) and len(pending) < max_workers:
            submit_one(next_job)
            next_job += 1

        while pending:
            done, _ = concurrent.futures.wait(
                pending, return_when=concurrent.futures.FIRST_COMPLETED
            )
            for future in done:
                pending.pop(future)
                bugset, diff_name, returncode = future.result()
                if returncode == 0:
                    succeeded += 1
                    print(f"[OK] {bugset}/{diff_name}")
                else:
                    failures.append((bugset, diff_name, returncode))
                    print(
                        f"[FAIL] exit={returncode} {bugset}/{diff_name}",
                        file=sys.stderr,
                    )
                    if args.stop_on_error:
                        stop_scheduling = True

            while (
                not stop_scheduling
                and next_job < len(selected)
                and len(pending) < max_workers
            ):
                submit_one(next_job)
                next_job += 1

    not_run = len(selected) - succeeded - len(failures)
    print(
        f"[SUMMARY] succeeded={succeeded}, failed={len(failures)}, "
        f"not_run={not_run}, skipped={skipped}"
    )
    for bugset, diff_name, returncode in failures:
        print(f"[FAILED] exit={returncode} {bugset}/{diff_name}", file=sys.stderr)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
