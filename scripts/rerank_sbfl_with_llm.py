#!/usr/bin/env python3
"""Rerank one or more SBFL results, optionally selecting LLM_MISSING cases."""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence


REQUIRED_FILES = ("blocks.json", "run.log", "result.log")
RUN_DIR_RE = re.compile(r"^[^_]+_(?P<bugset>\d+)_(?P<module>.+)$")
RUN_LOG_FIELD_RE = re.compile(r"^\[(?P<field>[A-Z ]+)\]\s*(?P<value>.*)$")
WRAPPER_VALUE_OPTIONS = {
    "--results-root",
    "--summary",
    "--rtl-path",
    "--rtl_path",
    "--patchs-path",
    "--patchs_path",
    "-j",
    "--jobs",
}
WRAPPER_FLAGS = {
    "-h",
    "--help",
    "--skip-existing",
    "--stop-on-error",
    "--list-only",
}


@dataclass(frozen=True)
class RunMetadata:
    diff_path: str | None
    bugset: str | None


@dataclass(frozen=True)
class Job:
    label: str
    result_dir: Path
    rtl_path: Path
    patch_path: Path


@dataclass(frozen=True)
class Selection:
    label: str
    result_dir: Path
    bugset: str | None = None
    diff_name: str | None = None


class HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """Preserve examples while showing defaults in option help."""

    def _get_help_string(self, action: argparse.Action) -> str:
        if action.default is None or action.default is False:
            return action.help or ""
        return super()._get_help_string(action)


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be at least 1")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Rerank one SBFL result or a batch using a required, unpatched RTL "
            "source. Patch metadata is read from each result's run.log."
        ),
        epilog=(
            "Unknown options are forwarded to `ibex-sbfl rerank`. Examples:\n"
            "  %(prog)s logs/run/case --rtl-path rtl --list-only\n"
            "  %(prog)s logs/run --rtl-path rtl --patchs-path verify_dataset -j 4\n"
            "  %(prog)s logs/run --rtl-path rtl "
            "--summary llm_rerank_summary.tsv --list-only\n"
            "  %(prog)s --results-root logs/run --summary summary.tsv "
            "--rtl-path rtl --patchs-path verify_dataset\n"
            "  %(prog)s logs/run --rtl-path rtl -- "
            "--candidate-count 80 --top-k 30 --include-reason\n\n"
            "Arguments after `--` are passed verbatim to `ibex-sbfl rerank`. "
            "Unknown options before `--` are also forwarded, but use `--` when "
            "an option name could conflict with this wrapper."
        ),
        formatter_class=HelpFormatter,
    )
    parser.add_argument(
        "target",
        nargs="?",
        type=Path,
        help="result.log, one result directory, or a parent containing result directories",
    )
    parser.add_argument(
        "--results-root",
        type=Path,
        help="result parent directory; alternative to the positional target",
    )
    parser.add_argument(
        "--summary",
        type=Path,
        help="rerank only rows whose status is LLM_MISSING in this TSV",
    )
    parser.add_argument(
        "--rtl-path",
        "--rtl_path",
        dest="rtl_path",
        type=Path,
        required=True,
        help="required path to the original, unpatched RTL source",
    )
    parser.add_argument(
        "--patchs-path",
        "--patchs_path",
        dest="patchs_path",
        type=Path,
        help=(
            "optional patch-set root such as verify_dataset; patches are resolved "
            "as <root>/<bugset_id>/<DIFF filename>"
        ),
    )
    parser.add_argument(
        "-j",
        "--jobs",
        type=positive_int,
        default=1,
        help="maximum concurrent rerank commands",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        help="skip results that already contain llm_rerank.json",
    )
    parser.add_argument(
        "--stop-on-error",
        action="store_true",
        help=(
            "stop scheduling new jobs after a failure; already running jobs finish"
        ),
    )
    parser.add_argument(
        "--list-only",
        action="store_true",
        help="validate and print commands without invoking the model",
    )
    return parser


def parse_args(
    argv: Sequence[str] | None = None,
) -> tuple[argparse.Namespace, list[str]]:
    values = list(sys.argv[1:] if argv is None else argv)
    passthrough: list[str] = []
    if "--" in values:
        separator = values.index("--")
        passthrough = values[separator + 1 :]
        values = values[:separator]

    target: str | None = None
    if values and not values[0].startswith("-"):
        target = values.pop(0)

    wrapper_arguments = [target] if target is not None else []
    forwarded: list[str] = []
    index = 0
    while index < len(values):
        argument = values[index]
        option = argument.split("=", 1)[0]
        if option in WRAPPER_FLAGS:
            wrapper_arguments.append(argument)
            index += 1
            continue
        if option in WRAPPER_VALUE_OPTIONS:
            wrapper_arguments.append(argument)
            if "=" not in argument:
                if index + 1 >= len(values):
                    build_parser().error(f"argument {argument}: expected one argument")
                wrapper_arguments.append(values[index + 1])
                index += 2
            else:
                index += 1
            continue
        forwarded.append(argument)
        index += 1

    forwarded.extend(passthrough)
    parser = build_parser()
    args = parser.parse_args(wrapper_arguments)
    if args.target is None and args.results_root is None:
        parser.error("provide TARGET or --results-root")
    if args.target is not None and args.results_root is not None:
        parser.error("TARGET and --results-root are mutually exclusive")
    return args, forwarded


def has_option(arguments: Sequence[str], name: str) -> bool:
    return any(
        argument == name or argument.startswith(f"{name}=") for argument in arguments
    )


def option_value(arguments: Sequence[str], name: str) -> str | None:
    for index, argument in enumerate(arguments):
        if argument.startswith(f"{name}="):
            return argument.split("=", 1)[1]
        if argument == name and index + 1 < len(arguments):
            return arguments[index + 1]
    return None


def with_default_model(arguments: Sequence[str]) -> list[str]:
    result = list(arguments)
    if not has_option(result, "--model"):
        result.extend(["--model", "gpt-5.5"])
    return result


def with_model_api_compatibility(arguments: Sequence[str]) -> list[str]:
    result = list(arguments)
    model = option_value(result, "--model")
    if model is None or has_option(result, "--api-protocol"):
        return result
    model_name = model.lower().rsplit("/", 1)[-1]
    if model_name.startswith("claude-"):
        result.extend(["--api-protocol", "anthropic"])
    elif model_name.startswith("glm-"):
        result.extend(["--api-protocol", "zai"])
    elif model_name.startswith("gpt-"):
        result.extend(["--api-protocol", "openai-responses"])
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


def read_run_metadata(run_log: Path) -> RunMetadata:
    diff_path: str | None = None
    bugset: str | None = None
    for line in run_log.read_text(encoding="utf-8").splitlines():
        match = RUN_LOG_FIELD_RE.match(line)
        if match is None:
            continue
        field = match.group("field").strip()
        value = match.group("value").strip()
        if field == "DIFF":
            diff_path = value or None
        elif field == "REL":
            bugset = value or None
    return RunMetadata(diff_path=diff_path, bugset=bugset)


def _rebase_recorded_path(path: Path, repo_root: Path, anchor: str) -> Path:
    if not path.is_absolute():
        return (repo_root / path).resolve()
    if path.exists():
        return path.resolve()
    if anchor in path.parts:
        anchor_index = path.parts.index(anchor)
        return repo_root.joinpath(*path.parts[anchor_index:]).resolve()
    return path


def resolve_rtl_path(explicit_rtl: Path, repo_root: Path) -> Path:
    path = explicit_rtl.expanduser()
    return (repo_root / path).resolve() if not path.is_absolute() else path.resolve()


def resolve_patch_path(
    metadata: RunMetadata,
    patchs_root: Path | None,
    repo_root: Path,
    *,
    bugset_override: str | None = None,
    diff_name_override: str | None = None,
) -> Path:
    if patchs_root is None:
        if metadata.diff_path is None:
            raise ValueError("run.log does not contain [DIFF]")
        return _rebase_recorded_path(
            Path(metadata.diff_path).expanduser(),
            repo_root,
            "verify_dataset",
        )

    path = patchs_root.expanduser()
    resolved = (repo_root / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_dir():
        raise ValueError(f"--patchs-path directory does not exist: {resolved}")

    bugset = bugset_override or metadata.bugset
    diff_name = diff_name_override
    if diff_name is None and metadata.diff_path is not None:
        diff_name = Path(metadata.diff_path).name
    if bugset is None:
        raise ValueError("run.log does not contain [REL ] bugset_id")
    if diff_name is None:
        raise ValueError("run.log does not contain [DIFF]")
    if Path(diff_name).name != diff_name:
        raise ValueError(
            f"DIFF filename must not contain a directory: {diff_name}"
        )
    return resolved / bugset / diff_name


def display_path(path: Path, repo_root: Path) -> str:
    try:
        return str(path.relative_to(repo_root))
    except ValueError:
        return str(path)


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


def module_from_diff(diff_name: str) -> str:
    suffix = ".sv.diff"
    if not diff_name.endswith(suffix):
        raise ValueError(f"unexpected diff name, expected *.sv.diff: {diff_name}")
    return diff_name[: -len(suffix)]


def select_missing_result_dirs(
    result_dirs: Sequence[Path],
    missing_cases: Sequence[tuple[str, str]],
) -> list[Selection]:
    index: dict[tuple[str, str], Path] = {}
    for result_dir in result_dirs:
        match = RUN_DIR_RE.fullmatch(result_dir.name)
        if match is None:
            continue
        key = (match.group("bugset"), match.group("module"))
        if key in index:
            raise ValueError(
                f"duplicate result directories for bugset/module {key}: "
                f"{index[key]} and {result_dir}"
            )
        index[key] = result_dir

    selected: list[Selection] = []
    for bugset, diff_name in missing_cases:
        module = module_from_diff(diff_name)
        result_dir = index.get((bugset, module))
        if result_dir is None:
            raise ValueError(
                f"no result directory matching *_{bugset}_{module} "
                f"for {bugset}/{diff_name}"
            )
        selected.append(
            Selection(
                label=f"{bugset}/{diff_name}",
                result_dir=result_dir,
                bugset=bugset,
                diff_name=diff_name,
            )
        )
    return selected


def collect_selected_results(
    target: Path,
    summary: Path | None,
) -> tuple[list[Selection], list[Path]]:
    if target.is_file():
        if target.name != "result.log":
            raise ValueError(f"the only supported result file is result.log: {target}")
        if not is_result_dir(target.parent):
            raise ValueError(
                f"result.log directory is missing one of {', '.join(REQUIRED_FILES)}: "
                f"{target.parent}"
            )
        result_dirs = [target.parent.resolve()]
        incomplete: list[Path] = []
    elif is_result_dir(target):
        result_dirs = [target.resolve()]
        incomplete = []
    elif target.is_dir():
        result_dirs, incomplete = discover_result_dirs(target)
    else:
        raise ValueError(f"result or parent path does not exist: {target}")

    if not result_dirs:
        raise ValueError(
            f"no directories containing {', '.join(REQUIRED_FILES)} found below {target}"
        )
    if summary is None:
        return [Selection(str(path), path) for path in result_dirs], incomplete

    missing_cases = read_missing_cases(summary)
    if not missing_cases:
        return [], incomplete
    return select_missing_result_dirs(result_dirs, missing_cases), incomplete


def build_jobs(
    selected_results: Sequence[Selection],
    explicit_rtl: Path,
    patchs_root: Path | None,
    repo_root: Path,
) -> list[Job]:
    jobs: list[Job] = []
    errors: list[str] = []
    rtl_path = resolve_rtl_path(explicit_rtl, repo_root)
    if not rtl_path.is_dir():
        raise ValueError(f"RTL directory does not exist: {rtl_path}")
    for selection in selected_results:
        label = selection.label
        result_dir = selection.result_dir
        try:
            metadata = read_run_metadata(result_dir / "run.log")
            patch_path = resolve_patch_path(
                metadata,
                patchs_root,
                repo_root,
                bugset_override=selection.bugset,
                diff_name_override=selection.diff_name,
            )
            if not patch_path.is_file():
                raise ValueError(f"patch file does not exist: {patch_path}")
            jobs.append(Job(label, result_dir, rtl_path, patch_path))
        except (OSError, ValueError) as exc:
            errors.append(f"{label}: {exc}")
    if errors:
        raise ValueError("\n".join(errors))
    return jobs


def build_command(
    job: Job,
    forwarded: Sequence[str],
    repo_root: Path,
) -> list[str]:
    return [
        "uv",
        "run",
        "--project",
        "tools/ibex_sbfl_llm",
        "--frozen",
        "ibex-sbfl",
        "rerank",
        display_path(job.rtl_path, repo_root),
        display_path(job.result_dir, repo_root),
        "--patch",
        display_path(job.patch_path, repo_root),
        *forwarded,
    ]


def execute(command: Sequence[str], repo_root: Path) -> int:
    try:
        return subprocess.run(command, cwd=repo_root, check=False).returncode
    except OSError as exc:
        print(f"[ERROR] cannot start command: {exc}", file=sys.stderr)
        return 127


def run_job(
    index: int,
    total: int,
    job: Job,
    forwarded: Sequence[str],
    repo_root: Path,
) -> tuple[Job, int]:
    command = build_command(job, forwarded, repo_root)
    print(
        f"[RUN {index}/{total}] {job.label}\n  {shlex.join(command)}",
        flush=True,
    )
    return job, execute(command, repo_root)


def run_jobs(
    jobs: Sequence[Job],
    forwarded: Sequence[str],
    repo_root: Path,
    *,
    max_workers: int,
    stop_on_error: bool,
) -> int:
    succeeded = 0
    failures: list[tuple[Job, int]] = []
    next_job = 0
    stop_scheduling = False
    workers = min(max_workers, len(jobs)) if jobs else 1
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
        pending: dict[concurrent.futures.Future[tuple[Job, int]], int] = {}

        def submit_one(job_index: int) -> None:
            future = executor.submit(
                run_job,
                job_index + 1,
                len(jobs),
                jobs[job_index],
                forwarded,
                repo_root,
            )
            pending[future] = job_index

        while next_job < len(jobs) and len(pending) < workers:
            submit_one(next_job)
            next_job += 1

        while pending:
            done, _ = concurrent.futures.wait(
                pending,
                return_when=concurrent.futures.FIRST_COMPLETED,
            )
            for future in done:
                pending.pop(future)
                job, returncode = future.result()
                if returncode == 0:
                    succeeded += 1
                    print(f"[OK] {job.label}")
                else:
                    failures.append((job, returncode))
                    print(
                        f"[FAIL] exit={returncode} {job.label}",
                        file=sys.stderr,
                    )
                    if stop_on_error:
                        stop_scheduling = True

            while (
                not stop_scheduling
                and next_job < len(jobs)
                and len(pending) < workers
            ):
                submit_one(next_job)
                next_job += 1

    not_run = len(jobs) - succeeded - len(failures)
    print(
        f"[SUMMARY] succeeded={succeeded}, failed={len(failures)}, "
        f"not_run={not_run}"
    )
    for job, returncode in failures:
        print(f"[FAILED] exit={returncode} {job.label}", file=sys.stderr)
    return 1 if failures else 0


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args, forwarded = parse_args(argv)
    except SystemExit as exc:
        return int(exc.code)

    repo_root = Path(__file__).resolve().parents[1]
    if not (repo_root / "tools" / "ibex_sbfl_llm").is_dir():
        print(f"[ERROR] unified project is missing below {repo_root}", file=sys.stderr)
        return 2

    target_value = args.results_root if args.results_root is not None else args.target
    assert target_value is not None
    target = target_value.expanduser().resolve()
    summary = args.summary.expanduser().resolve() if args.summary else None
    if summary is not None and not summary.is_file():
        print(f"[ERROR] summary file does not exist: {summary}", file=sys.stderr)
        return 2
    if has_option(forwarded, "--patch"):
        print(
            "[ERROR] pass the patch-set root with --patchs-path, not --patch",
            file=sys.stderr,
        )
        return 2
    if has_option(forwarded, "--patch-path") or has_option(
        forwarded, "--patch_path"
    ):
        print(
            "[ERROR] --patch-path is no longer supported; use --patchs-path "
            "with a verify_dataset root",
            file=sys.stderr,
        )
        return 2
    if has_option(forwarded, "--allow-unpatched-source"):
        print(
            "[ERROR] --allow-unpatched-source is not supported: this wrapper always "
            "requires and applies a patch",
            file=sys.stderr,
        )
        return 2

    try:
        selected_results, incomplete_dirs = collect_selected_results(target, summary)
        jobs = build_jobs(
            selected_results,
            args.rtl_path,
            args.patchs_path,
            repo_root,
        )
    except (OSError, ValueError) as exc:
        for message in str(exc).splitlines():
            print(f"[ERROR] {message}", file=sys.stderr)
        return 2

    for result_dir in incomplete_dirs:
        print(
            f"[WARN] skip incomplete SBFL directory without result.log: {result_dir}",
            file=sys.stderr,
        )
    if summary is not None and not selected_results:
        print(f"[INFO] no LLM_MISSING rows in {summary}")
        return 0

    selected: list[Job] = []
    skipped = 0
    for job in jobs:
        output = job.result_dir / "llm_rerank.json"
        if args.skip_existing and output.is_file():
            skipped += 1
            print(f"[SKIP] existing result: {output}")
        else:
            selected.append(job)

    if len(selected) > 1 and has_option(forwarded, "--output"):
        print(
            "[ERROR] --output cannot be shared by a multi-result batch; "
            "each result uses its default llm_rerank.json",
            file=sys.stderr,
        )
        return 2

    forwarded = with_model_api_compatibility(with_default_model(forwarded))
    print(
        f"[INFO] discovered={len(selected_results)}, selected={len(selected)}, "
        f"skipped={skipped}, incomplete={len(incomplete_dirs)}"
    )
    if args.list_only:
        for index, job in enumerate(selected, 1):
            command = build_command(job, forwarded, repo_root)
            print(f"[LIST {index}/{len(selected)}] {job.label}\n  {shlex.join(command)}")
        return 0
    return run_jobs(
        selected,
        forwarded,
        repo_root,
        max_workers=args.jobs,
        stop_on_error=args.stop_on_error,
    )


if __name__ == "__main__":
    raise SystemExit(main())
