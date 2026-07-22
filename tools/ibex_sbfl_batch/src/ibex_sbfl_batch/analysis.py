"""Rebuild bug cases and run analysis on their saved corpora."""

from __future__ import annotations

import csv
import shutil
import threading
import time
from argparse import Namespace
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path

from .config import DEFAULT_TOP_SCOPE, AnalysisConfig, CaseResult, CaseSpec, ExecutionConfig
from .errors import SbflBatchError
from .info import print_config, print_info
from .logparse import (
    has_flag,
    last_run_argv,
    option_value,
    read_status_cases,
    required_option,
    trailing_args,
)
from .runner import format_elapsed, safe_name
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

ANALYSIS_FIELDS = [
    "case_index",
    "rel_dir",
    "diff_name",
    "case_dir",
    "diff",
    "corpus",
    "status",
    "rc",
    "elapsed_time",
    "logdir",
    "workdir",
]


def _int_option(argv: list[str], name: str, default: int) -> int:
    return int(option_value(argv, name, str(default)))


def _float_option(argv: list[str], name: str, default: float) -> float:
    return float(option_value(argv, name, str(default)))


def prepare_analysis(args: Namespace) -> tuple[AnalysisConfig, list[CaseSpec]]:
    cases = read_status_cases(Path(args.input_logs).expanduser().resolve(), require_corpus=False)
    first: list[str] | None = None
    for case in cases:
        assert case.old_logdir is not None
        run_log = case.old_logdir / "run.log"
        if run_log.is_file():
            try:
                first = last_run_argv(run_log)
                break
            except SbflBatchError:
                pass
    if first is None:
        raise SbflBatchError(f"no executable [RUN] command found under {args.input_logs}")

    def override(name: str, inherited):
        value = getattr(args, name)
        return inherited if value is None else value

    config = AnalysisConfig(
        top_pass=override("top_pass", _int_option(first, "--top-pass", 10)),
        selection=override("selection", option_value(first, "--selection", "sort")),
        selection_diversity_weight=override(
            "selection_diversity_weight",
            _float_option(first, "--selection-diversity-weight", 0.4),
        ),
        selection_pool_factor=override(
            "selection_pool_factor", _int_option(first, "--selection-pool-factor", 3)
        ),
        reduce_cover=override("reduce_cover", has_flag(first, "--reduce-cover")),
        top_sus=override("top_sus", _int_option(first, "--top-sus", 10)),
        metric=override("metric", option_value(first, "--metric", "ochiai")),
        cover_distance_weight=override(
            "cover_distance_weight", _float_option(first, "--cover-distance-weight", 0.5)
        ),
        save_intermediate=override("save_intermediate", has_flag(first, "--save-intermediate")),
        rtl_path=args.rtl_path,
        include_paths=args.include_paths,
        top_module=override("top_module", option_value(first, "--top-module", "ibex_core")),
        top_scope=override("top_scope", option_value(first, "--top-scope", DEFAULT_TOP_SCOPE)),
        simulator_args=(
            list(args.simulator_args)
            if args.simulator_args is not None
            else trailing_args(first, ["-c", "5000000"])
        ),
    )
    if config.selection not in {"random", "sort", "diverse"}:
        raise SbflBatchError(f"invalid selection: {config.selection}")
    if config.top_pass < 0 or config.top_sus < 0 or config.selection_pool_factor <= 0:
        raise SbflBatchError("invalid top-pass, top-sus, or selection pool factor")
    if not 0 <= config.selection_diversity_weight <= 1:
        raise SbflBatchError("selection diversity weight must be in range [0, 1]")
    if not 0 <= config.cover_distance_weight <= 1:
        raise SbflBatchError("cover distance weight must be in range [0, 1]")
    return config, cases


class AnalysisRunner:
    def __init__(
        self,
        execution: ExecutionConfig,
        analysis: AnalysisConfig,
        cases: list[CaseSpec],
    ) -> None:
        self.execution = execution
        self.analysis = analysis
        self.cases = cases
        self.processes = ProcessRegistry()
        self._summary_lock = threading.Lock()
        run_id = f"{datetime.now():%Y-%m-%d-%H-%M-%S}_{__import__('os').getpid()}"
        self.run_tmp = execution.tmp_root / run_id
        self.work_root = self.run_tmp / "work"
        self.run_log_root = execution.logs_root / run_id
        self.summary_file = self.run_log_root / "run_status.tsv"

    def _initialize(self) -> None:
        if self.execution.jobs <= 0:
            raise SbflBatchError("jobs must be a positive integer")
        if not self.execution.ibex_home.joinpath("rtl").is_dir():
            raise SbflBatchError(f"IBEX_HOME seems wrong: {self.execution.ibex_home}")
        if self.run_tmp.is_relative_to(self.execution.ibex_home):
            raise SbflBatchError("temporary directory must not be inside IBEX_HOME")
        if not self.execution.dry_run:
            require_commands("fusesoc", "patch")
        self.work_root.mkdir(parents=True)
        self.run_log_root.mkdir(parents=True)
        with self.summary_file.open("w", newline="", encoding="utf-8") as handle:
            csv.DictWriter(handle, fieldnames=ANALYSIS_FIELDS, delimiter="\t").writeheader()

    def _paths(self, case: CaseSpec) -> tuple[Path, Path]:
        name = (
            f"{case.index}_{safe_name(case.rel_dir)}_"
            f"{safe_name(case.diff_name.removesuffix('.sv.diff'))}"
        )
        return self.run_log_root / name, self.work_root / name

    def _analysis_args(self, case: CaseSpec, logdir: Path, workdir: Path) -> list[str]:
        assert case.old_logdir is not None and case.corpus is not None
        command = last_run_argv(case.old_logdir / "run.log")
        coverage = required_option(command, "-c")
        state = required_option(command, "-s")
        tracker = _int_option(command, "--tracker-window-size", 20)
        cfg = self.analysis
        argv = [
            "-c",
            coverage,
            "-s",
            state,
            "analysis",
            "--input",
            str(case.corpus),
            "--output",
            str(logdir),
            "--tracker-window-size",
            str(tracker),
            "--cover-distance-weight",
            str(cfg.cover_distance_weight),
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
            "--metric",
            cfg.metric,
        ]
        if cfg.reduce_cover:
            argv.append("--reduce-cover")
        if cfg.save_intermediate:
            argv.append("--save-intermediate")
        if cfg.rtl_path:
            argv.extend(
                [
                    "--rtl-path",
                    cfg.rtl_path,
                    "--include-paths",
                    cfg.include_paths,
                    "--top-module",
                    cfg.top_module,
                    "--top-scope",
                    cfg.top_scope,
                ]
            )
        if cfg.simulator_args:
            argv.extend(["--", *cfg.simulator_args])
        return argv

    def _finish(
        self,
        case: CaseSpec,
        status: str,
        rc: int,
        started: float,
        logdir: Path,
        workdir: Path,
    ) -> CaseResult:
        elapsed = format_elapsed(time.monotonic() - started)
        line = f"[{status}] {case.rel_dir}/{case.diff_name}, status={rc}, elapsed={elapsed}\n"
        append_text(logdir / "status.txt", line)
        append_text(logdir / "run.log", f"[TIME] elapsed={elapsed}\n")
        print(line.rstrip())
        return CaseResult(case, status, rc, elapsed, logdir, workdir)

    def _run_case(self, case: CaseSpec) -> CaseResult:
        started = time.monotonic()
        logdir, workdir = self._paths(case)
        logdir.mkdir(parents=True)
        run_log = logdir / "run.log"
        run_log.write_text(
            f"[CASE] {case.index}\n[REL ] {case.rel_dir}\n[DIFF] {case.diff}\n"
            f"[OLD ] {case.old_logdir}\n[CORP] {case.corpus}\n[WORK] {workdir}\n",
            encoding="utf-8",
        )
        try:
            if case.corpus is None or not case.corpus.is_file():
                return self._finish(case, "CORPUS_MISSING", 1, started, logdir, workdir)
            if case.old_logdir is None or not case.old_logdir.joinpath("run.log").is_file():
                return self._finish(case, "RUN_LOG_MISSING", 1, started, logdir, workdir)
            if not case.diff.is_file():
                return self._finish(case, "DIFF_MISSING", 1, started, logdir, workdir)
            executable = resolve_workdir_path(workdir, self.execution.sbfl_bin)
            try:
                argv = self._analysis_args(case, logdir, workdir)
            except SbflBatchError as exc:
                append_text(run_log, f"[ERROR] {exc}\n")
                return self._finish(case, "CONFIG_FAIL", 1, started, logdir, workdir)
            if self.execution.dry_run:
                write_command(run_log, executable, argv)
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
            if not executable.is_file() or not executable.stat().st_mode & 0o111:
                return self._finish(case, "SBFL_BIN_MISSING", 1, started, logdir, workdir)
            write_command(run_log, executable, argv)
            rc = self.processes.run([str(executable), *argv], workdir, logdir / "analysis.log")
            if self.execution.disassemble:
                with (logdir / "objdump.log").open("w", encoding="utf-8") as output:
                    disassemble_elfs(logdir, self.execution.objdump_bin, output)
            return self._finish(
                case, "OK" if rc == 0 else "ANALYSIS_FAIL", rc, started, logdir, workdir
            )
        finally:
            if workdir.is_dir() and not self.execution.keep_workdir:
                append_text(run_log, f"[CLEAN] remove workdir: {workdir}\n")
                shutil.rmtree(workdir)

    def _append(self, result: CaseResult) -> None:
        row = {
            "case_index": result.case.index,
            "rel_dir": result.case.rel_dir,
            "diff_name": result.case.diff_name,
            "case_dir": str(result.case.case_dir),
            "diff": str(result.case.diff),
            "corpus": str(result.case.corpus or ""),
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
            csv.DictWriter(handle, fieldnames=ANALYSIS_FIELDS, delimiter="\t").writerow(row)

    def run(self) -> int:
        self._initialize()
        print_info("command", "analysis")
        print_info("cases", len(self.cases))
        print_info("corpus_cases", sum(case.corpus is not None for case in self.cases))
        print_info("case_runtime_inputs", "coverage/state/tracker from each original run.log")
        print_config("execution", self.execution)
        print_info("resolved.run_logs", self.run_log_root)
        print_info("resolved.run_tmp", self.run_tmp)
        print_info("resolved.status_file", self.summary_file)
        print_config("analysis", self.analysis)
        results: list[CaseResult] = []
        executor = ThreadPoolExecutor(max_workers=self.execution.jobs)
        futures = []
        try:
            futures = [executor.submit(self._run_case, case) for case in self.cases]
            for future in as_completed(futures):
                result = future.result()
                results.append(result)
                self._append(result)
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
