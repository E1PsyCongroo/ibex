"""Parallel generation runner for patched Ibex bug cases."""

from __future__ import annotations

import csv
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .config import CaseResult, CaseSpec, ExecutionConfig, GenerationConfig
from .errors import SbflBatchError
from .info import print_config, print_info
from .workspace import (
    FUSESOC_COMMAND,
    ProcessRegistry,
    append_text,
    copy_workdir,
    disassemble_elfs,
    require_commands,
    resolve_workdir_path,
    write_command,
)

GENERATION_FIELDS = [
    "case_index",
    "rel_dir",
    "diff_name",
    "case_dir",
    "diff",
    "status",
    "rc",
    "elapsed_time",
    "logdir",
    "workdir",
]


def safe_name(value: str) -> str:
    cleaned = "".join(
        char if char.isascii() and (char.isalnum() or char in "._-") else "_" for char in value
    )
    return cleaned or "root"


def format_elapsed(seconds: float) -> str:
    milliseconds = max(0, round(seconds * 1000))
    hours, milliseconds = divmod(milliseconds, 3_600_000)
    minutes, milliseconds = divmod(milliseconds, 60_000)
    secs, milliseconds = divmod(milliseconds, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}:{milliseconds:03d}"


def discover_cases(target_mode: str, target: Path) -> list[CaseSpec]:
    target = target.resolve()
    if target_mode == "all":
        if not target.is_dir():
            raise SbflBatchError(f"bugset root not found: {target}")
        diffs = sorted(target.rglob("*.sv.diff"))
        return [
            CaseSpec(
                index=str(index),
                rel_dir=str(diff.parent.relative_to(target)) or diff.parent.name,
                diff=diff,
                case_dir=diff.parent,
                diff_name=diff.name,
            )
            for index, diff in enumerate(diffs)
        ]
    if target.is_file() and target.name.endswith(".sv.diff"):
        diff = target
    elif target.is_dir():
        try:
            diff = next(iter(sorted(target.rglob("*.sv.diff"))))
        except StopIteration as exc:
            raise SbflBatchError(f"no .sv.diff found under: {target}") from exc
    else:
        raise SbflBatchError(f"case target is neither .sv.diff nor directory: {target}")
    return [
        CaseSpec(
            index="0",
            rel_dir=diff.parent.name,
            diff=diff,
            case_dir=diff.parent,
            diff_name=diff.name,
        )
    ]


class GenerationRunner:
    def __init__(
        self,
        execution: ExecutionConfig,
        generation: GenerationConfig,
        cases: list[CaseSpec],
        *,
        additional_iterations: bool = False,
    ) -> None:
        self.execution = execution
        self.generation = generation
        self.cases = cases
        self.additional_iterations = additional_iterations
        self.processes = ProcessRegistry()
        self._summary_lock = threading.Lock()

        run_id = f"{datetime.now():%Y-%m-%d-%H-%M-%S}_{__import__('os').getpid()}"
        self.run_tmp = execution.tmp_root / run_id
        self.work_root = self.run_tmp / "work"
        self.run_log_root = execution.logs_root / run_id
        self.summary_file = self.run_log_root / "run_status.tsv"

    def _validate(self) -> None:
        cfg = self.generation
        if self.execution.jobs <= 0:
            raise SbflBatchError("jobs must be a positive integer")
        if not self.execution.ibex_home.joinpath("rtl").is_dir():
            raise SbflBatchError(f"IBEX_HOME seems wrong: {self.execution.ibex_home}")
        if self.run_tmp.is_relative_to(self.execution.ibex_home):
            raise SbflBatchError("temporary directory must not be inside IBEX_HOME")
        if cfg.selection not in {"random", "sort", "diverse"}:
            raise SbflBatchError(f"invalid selection: {cfg.selection}")
        for name, value in (
            ("selection diversity weight", cfg.selection_diversity_weight),
            ("cover distance weight", cfg.cover_distance_weight),
        ):
            if not 0 <= value <= 1:
                raise SbflBatchError(f"{name} must be in range [0, 1]")
        if cfg.selection_pool_factor <= 0:
            raise SbflBatchError("selection pool factor must be positive")
        if cfg.max_run_timeout <= 0 or cfg.tracker_window_size <= 0:
            raise SbflBatchError("timeout and tracker window must be positive")
        if cfg.top_sus < 0:
            raise SbflBatchError("top-sus must be non-negative")
        if cfg.checkpoint_interval is not None:
            if cfg.checkpoint_interval <= 0:
                raise SbflBatchError("checkpoint interval must be positive")
            if not cfg.save_corpus:
                raise SbflBatchError("--checkpoint-interval requires --save-corpus")
        if cfg.save_reduce and not cfg.reduce_insts:
            raise SbflBatchError("--save-reduce requires --reduce-insts")
        if cfg.mode == "psbfl":
            if cfg.mutator_window_size <= 0:
                raise SbflBatchError("mutator window size must be positive")
            valid = {"uniform", "tail_linear", "tail_quad", "head_linear", "head_quad"}
            if cfg.mutator_weight_strategy not in valid:
                raise SbflBatchError(
                    f"invalid mutator weight strategy: {cfg.mutator_weight_strategy}"
                )
        elif cfg.mode == "withw":
            if cfg.max_corpus_size <= 0:
                raise SbflBatchError("max corpus size must be positive")
            for name, value in (
                ("init seed rate", cfg.init_seed_rate),
                ("mutate rate", cfg.mutate_rate),
                ("priority alpha", cfg.priority_alpha),
            ):
                if not 0 <= value <= 1:
                    raise SbflBatchError(f"{name} must be in range [0, 1]")
            if cfg.failed_reward < 0:
                raise SbflBatchError("failed reward must be non-negative")
        else:
            raise SbflBatchError(f"unsupported generation mode: {cfg.mode}")
        if not self.cases:
            raise SbflBatchError("no bug cases selected")

    def _initialize(self) -> None:
        self._validate()
        if not self.execution.dry_run:
            require_commands("fusesoc", "patch", "git")
        self.work_root.mkdir(parents=True)
        self.run_log_root.mkdir(parents=True)
        with self.summary_file.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=GENERATION_FIELDS, delimiter="\t").writeheader()

    def _case_paths(self, case: CaseSpec) -> tuple[Path, Path]:
        stem = case.diff_name.removesuffix(".sv.diff")
        name = f"{case.index}_{safe_name(case.rel_dir)}_{safe_name(stem)}"
        return self.run_log_root / name, self.work_root / name

    def _generation_args(self, case: CaseSpec, logdir: Path, workdir: Path) -> list[str]:
        cfg = self.generation
        argv = ["-c", cfg.coverage, "-s", cfg.state, "generation"]
        if cfg.reduce_insts:
            argv.append("--reduce-insts")
        if cfg.reduce_cover:
            argv.append("--reduce-cover")
        argv.extend(
            [
                "--max-run-timeout",
                str(cfg.max_run_timeout),
                "--max-iters",
                str(cfg.max_iters),
                "--top-pass",
                str(cfg.top_pass),
                "--selection",
                cfg.selection,
                "--selection-diversity-weight",
                str(cfg.selection_diversity_weight),
                "--selection-pool-factor",
                str(cfg.selection_pool_factor),
                "--top-sus",
                str(cfg.top_sus),
                "--tracker-window-size",
                str(cfg.tracker_window_size),
                "--cover-distance-weight",
                str(cfg.cover_distance_weight),
                "--output",
                str(logdir),
            ]
        )
        if case.corpus is not None:
            argv.extend(["--resume-corpus", str(case.corpus)])
        elif cfg.input_path is not None:
            argv.extend(["--input", str(cfg.input_path)])
        else:
            raise SbflBatchError("generation requires an input or resume corpus")
        if cfg.save_corpus:
            argv.extend(["--save-corpus", str(logdir / "saved_corpus")])
        if cfg.checkpoint_interval is not None:
            argv.extend(["--checkpoint-interval", str(cfg.checkpoint_interval)])
        if cfg.gen_only:
            argv.append("--gen-only")
        if cfg.save_reduce:
            argv.append("--save-reduce")
        if cfg.save_intermediate:
            argv.append("--save-intermediate")
        argv.extend(
            [
                "--rtl-path",
                str(resolve_workdir_path(workdir, cfg.rtl_path)),
                "--include-paths",
                ",".join(str(resolve_workdir_path(workdir, p)) for p in cfg.include_paths.split(",")),
                "--top-module",
                cfg.top_module,
                "--top-scope",
                cfg.top_scope,
                "--metric",
                cfg.metric,
            ]
        )
        if cfg.mode == "psbfl":
            argv.extend(
                [
                    "psbfl",
                    "--mutator-window-size",
                    str(cfg.mutator_window_size),
                    "--mutator-weight-strategy",
                    cfg.mutator_weight_strategy,
                ]
            )
        else:
            argv.extend(
                [
                    "wit-hw",
                    "--max-corpus-size",
                    str(cfg.max_corpus_size),
                    "--init-seed-rate",
                    str(cfg.init_seed_rate),
                    "--mutate-rate",
                    str(cfg.mutate_rate),
                    "--priority-alpha",
                    str(cfg.priority_alpha),
                    "--failed-reward",
                    str(cfg.failed_reward),
                ]
            )
        if cfg.simulator_args:
            argv.extend(["--", *cfg.simulator_args])
        return argv

    def _finish(
        self,
        case: CaseSpec,
        status: str,
        return_code: int,
        started: float,
        logdir: Path,
        workdir: Path,
    ) -> CaseResult:
        elapsed = format_elapsed(time.monotonic() - started)
        line = (
            f"[{status}] {case.rel_dir}/{case.diff_name}, status={return_code}, elapsed={elapsed}\n"
        )
        append_text(logdir / "status.txt", line)
        append_text(logdir / "run.log", f"[TIME] elapsed={elapsed}\n")
        print(line.rstrip())
        return CaseResult(case, status, return_code, elapsed, logdir, workdir)

    def _run_case(self, case: CaseSpec) -> CaseResult:
        started = time.monotonic()
        logdir, workdir = self._case_paths(case)
        logdir.mkdir(parents=True)
        run_log = logdir / "run.log"
        run_log.write_text(
            "\n".join(
                [
                    f"[CASE] {case.index}",
                    f"[REL ] {case.rel_dir}",
                    f"[DIR ] {case.case_dir}",
                    f"[DIFF] {case.diff}",
                    f"[CORP] {case.corpus or ''}",
                    f"[LOG ] {logdir}",
                    f"[WORK] {workdir}",
                    "",
                ]
            ),
            encoding="utf-8",
        )
        print(f"[START][{case.index}] {case.rel_dir}/{case.diff_name}")
        try:
            if not case.diff.is_file():
                return self._finish(case, "DIFF_MISSING", 1, started, logdir, workdir)
            if case.corpus is not None and not case.corpus.is_file():
                return self._finish(case, "CORPUS_MISSING", 1, started, logdir, workdir)
            if self.execution.dry_run:
                executable = resolve_workdir_path(workdir, self.execution.sbfl_bin)
                write_command(run_log, executable, self._generation_args(case, logdir, workdir))
                return self._finish(case, "OK", 0, started, logdir, workdir)
            try:
                copy_workdir(self.execution.ibex_home, workdir)
            except (OSError, SbflBatchError) as exc:
                (logdir / "copy.log").write_text(f"{exc}\n", encoding="utf-8")
                return self._finish(case, "COPY_FAIL", 1, started, logdir, workdir)
            rc = self.processes.run(
                ["patch", "-p1", "--forward", "--batch", "--input", str(case.diff)],
                workdir,
                logdir / "patch.log",
            )
            if rc:
                return self._finish(case, "APPLY_FAIL", rc, started, logdir, workdir)
            rc = self.processes.run(FUSESOC_COMMAND, workdir, logdir / "build.log")
            if rc:
                return self._finish(case, "BUILD_FAIL", rc, started, logdir, workdir)
            executable = resolve_workdir_path(workdir, self.execution.sbfl_bin)
            if not executable.is_file() or not executable.stat().st_mode & 0o111:
                append_text(run_log, f"[ERROR] SBFL binary missing: {executable}\n")
                return self._finish(case, "SBFL_BIN_MISSING", 1, started, logdir, workdir)
            argv = self._generation_args(case, logdir, workdir)
            write_command(run_log, executable, argv)
            rc = self.processes.run([str(executable), *argv], workdir, logdir / "sbfl.log")
            if self.execution.disassemble:
                with (logdir / "objdump.log").open("w", encoding="utf-8") as output:
                    disassemble_elfs(logdir, self.execution.objdump_bin, output)
            status = "OK" if rc == 0 else "SBFL_FAIL"
            return self._finish(case, status, rc, started, logdir, workdir)
        finally:
            if workdir.is_dir() and not self.execution.keep_workdir:
                append_text(run_log, f"[CLEAN] remove workdir: {workdir}\n")
                shutil.rmtree(workdir)

    def _append_result(self, result: CaseResult) -> None:
        row = {
            "case_index": result.case.index,
            "rel_dir": result.case.rel_dir,
            "diff_name": result.case.diff_name,
            "case_dir": str(result.case.case_dir),
            "diff": str(result.case.diff),
            "status": result.status,
            "rc": result.return_code,
            "elapsed_time": result.elapsed,
            "logdir": str(result.logdir),
            "workdir": str(result.workdir),
        }
        with (
            self._summary_lock,
            self.summary_file.open("a", newline="", encoding="utf-8") as handle,
        ):
            csv.DictWriter(handle, fieldnames=GENERATION_FIELDS, delimiter="\t").writerow(row)

    def run(self) -> int:
        self._initialize()
        print_info("command", "rerun" if self.additional_iterations else "generation")
        print_info("cases", len(self.cases))
        print_info("resume_corpus_cases", sum(case.corpus is not None for case in self.cases))
        print_info("additional_iterations", self.additional_iterations)
        print_config("execution", self.execution)
        print_info("resolved.run_logs", self.run_log_root)
        print_info("resolved.run_tmp", self.run_tmp)
        print_info("resolved.status_file", self.summary_file)
        print_config("generation", self.generation)
        results: list[CaseResult] = []
        executor = ThreadPoolExecutor(max_workers=self.execution.jobs)
        futures = []
        try:
            futures = [executor.submit(self._run_case, case) for case in self.cases]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                self._append_result(result)
        except KeyboardInterrupt:
            self.processes.terminate_all()
            for future in futures:
                future.cancel()
            executor.shutdown(wait=False, cancel_futures=True)
            raise
        except BaseException:
            self.processes.terminate_all()
            executor.shutdown(cancel_futures=True)
            raise
        else:
            executor.shutdown()
        failed = sum(result.status != "OK" for result in results)
        print(f"[SUMMARY] total={len(results)}, failed={failed}")
        print(f"[SUMMARY] status file: {self.summary_file}")
        return 1 if failed else 0
