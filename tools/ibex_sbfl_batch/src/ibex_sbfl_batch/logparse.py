"""Read prior runner logs without relying on Bash dynamic state."""

from __future__ import annotations

import csv
import shlex
from pathlib import Path

from .config import CaseSpec
from .errors import SbflBatchError


def last_run_argv(run_log: Path) -> list[str]:
    command = ""
    try:
        for line in run_log.read_text(encoding="utf-8").splitlines():
            if line.startswith("[RUN] /"):
                command = line.removeprefix("[RUN] ")
    except OSError as exc:
        raise SbflBatchError(f"cannot read run log {run_log}: {exc}") from exc
    if not command:
        raise SbflBatchError(f"no executable [RUN] command found in {run_log}")
    try:
        return shlex.split(command)
    except ValueError as exc:
        raise SbflBatchError(f"cannot parse [RUN] command in {run_log}: {exc}") from exc


def option_value(argv: list[str], option: str, default: str | None = None) -> str | None:
    try:
        index = argv.index(option)
    except ValueError:
        return default
    if index + 1 >= len(argv):
        raise SbflBatchError(f"logged option {option} has no value")
    return argv[index + 1]


def has_flag(argv: list[str], flag: str) -> bool:
    return flag in argv


def generation_mode(argv: list[str]) -> str:
    if "psbfl" in argv:
        return "psbfl"
    if "wit-hw" in argv:
        return "withw"
    raise SbflBatchError("cannot determine generation mode from logged command")


def trailing_args(argv: list[str], fallback: list[str] | None = None) -> list[str]:
    try:
        index = argv.index("--")
    except ValueError:
        return list(fallback or [])
    return argv[index + 1 :]


def required_option(argv: list[str], option: str) -> str:
    value = option_value(argv, option)
    if value is None:
        raise SbflBatchError(f"logged command has no {option} option")
    return value


def read_status_cases(run_dir: Path, *, require_corpus: bool) -> list[CaseSpec]:
    status_file = run_dir / "run_status.tsv"
    if not status_file.is_file():
        raise SbflBatchError(f"run status not found: {status_file}")
    cases: list[CaseSpec] = []
    with status_file.open(newline="", encoding="utf-8") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            index = row.get("case_index", "")
            diff_text = row.get("diff", "")
            old_logdir_text = row.get("logdir", "")
            if not index or not diff_text or not old_logdir_text:
                continue
            old_logdir = run_dir / Path(old_logdir_text).name
            corpus = old_logdir / "saved_corpus"
            diff = Path(diff_text).expanduser().resolve()
            if require_corpus and not corpus.is_file():
                print(f"[WARN] skip case {index}: saved corpus not found: {corpus}")
                continue
            cases.append(
                CaseSpec(
                    index=index,
                    rel_dir=row.get("rel_dir", "") or diff.parent.name,
                    diff=diff,
                    case_dir=Path(row.get("case_dir", "") or diff.parent).resolve(),
                    diff_name=row.get("diff_name", "") or diff.name,
                    corpus=corpus.resolve(),
                    old_logdir=old_logdir.resolve(),
                )
            )
    if not cases:
        raise SbflBatchError(f"no usable cases found in {status_file}")
    return cases
