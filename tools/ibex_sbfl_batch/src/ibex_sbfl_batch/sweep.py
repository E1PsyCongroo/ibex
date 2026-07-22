"""Parallel parameter sweeps for PSBFL bugset generation."""

from __future__ import annotations

import argparse
import csv
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .errors import SbflBatchError
from .info import print_config, print_info

WEIGHT_STRATEGIES = frozenset({"uniform", "tail_linear", "tail_quad", "head_linear", "head_quad"})
STATUS_FIELDS = (
    "top_pass",
    "mutator_weight_strategy",
    "mutator_window_size",
    "status",
    "run_rc",
    "summary_rc",
    "elapsed_time",
    "logs_root",
    "tmp_root",
    "run_log",
    "summary_log",
    "summary_tsv",
)
EMPTY_SUMMARY_FIELDS = (
    "top_pass",
    "mutator_weight_strategy",
    "mutator_window_size",
    "bugset",
    "diff",
    "status",
    "top-k",
    "sus",
    "elapsed_time",
    "fuzzing_time",
)


def add_sweep_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "sweep",
        help="sweep PSBFL selection and mutator parameters",
        description=(
            "Run the Cartesian product of top-pass, mutator strategy, and mutator "
            "window settings. Arguments after -- are forwarded to each generation run."
        ),
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", dest="all_cases", type=Path)
    target.add_argument("--case", type=Path)
    parser.add_argument(
        "--top-pass",
        "--top-passes",
        "--top-pass-list",
        dest="top_pass_values",
        action="append",
        required=True,
        metavar="LIST",
        help="comma- or space-separated positive integers",
    )
    parser.add_argument(
        "--mutator-window-size",
        "--mutator-window-sizes",
        "--mutator-window-size-list",
        dest="window_size_values",
        action="append",
        required=True,
        metavar="LIST",
        help="comma- or space-separated positive integers",
    )
    parser.add_argument(
        "--mutator-weight-strategy",
        "--mutator-weight-strategies",
        "--mutator-weight-strategy-list",
        dest="weight_strategy_values",
        action="append",
        metavar="LIST",
        help="comma- or space-separated strategy names; default: uniform",
    )
    parser.add_argument("--sweep-jobs", "--window-jobs", type=int, default=1)
    parser.add_argument("-j", "--jobs", type=int, help="jobs for each bugset run")
    parser.add_argument("-t", "--tmp", type=Path, help="sweep temporary root")
    parser.add_argument("-l", "--logs", type=Path, default=Path("./logs/args_sweep"))
    parser.add_argument("-w", "--workdir", type=Path)
    parser.add_argument("--line-window", type=int, default=0)
    parser.add_argument("--summary-bugset-root", type=Path)
    parser.add_argument("--run-script", type=Path)
    parser.add_argument("--summarize-script", type=Path)


def _split_values(values: list[str] | None, default: list[str] | None = None) -> list[str]:
    result = [item for value in values or [] for item in value.replace(",", " ").split()]
    return result or list(default or [])


def _positive_ints(name: str, values: list[str]) -> list[int]:
    result: list[int] = []
    for value in values:
        if not value.isascii() or not value.isdigit() or int(value) <= 0:
            raise SbflBatchError(f"{name} must be a positive integer: {value}")
        result.append(int(value))
    _reject_duplicates(name, result)
    return result


def _reject_duplicates(name: str, values: list[object]) -> None:
    seen: set[object] = set()
    for value in values:
        if value in seen:
            raise SbflBatchError(f"duplicate {name}: {value}")
        seen.add(value)


def _resolve_file(path: Path, description: str) -> Path:
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SbflBatchError(f"{description} not found: {resolved}")
    return resolved


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _infer_summary_bugset_root(mode: str, target: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        root = explicit.expanduser().resolve()
    elif mode == "all":
        root = target
    elif target.is_file() and target.name.endswith(".sv.diff"):
        root = target.parent.parent
    elif target.is_dir():
        first_diff = next(iter(sorted(target.rglob("*.sv.diff"))), None)
        if first_diff is None:
            raise SbflBatchError(
                f"cannot infer summary bugset root; no .sv.diff found under: {target}"
            )
        root = first_diff.resolve().parent.parent
    else:
        raise SbflBatchError(f"cannot infer summary bugset root for target: {target}")
    if not root.is_dir():
        raise SbflBatchError(f"summary bugset root not found: {root}")
    return root


@dataclass(frozen=True)
class SweepConfig:
    mode: str
    target: Path
    top_passes: list[int]
    window_sizes: list[int]
    weight_strategies: list[str]
    sweep_jobs: int
    inner_jobs: int | None
    tmp_root: Path
    logs_root: Path
    workdir: Path | None
    line_window: int
    summary_bugset_root: Path
    run_script: Path
    summarize_script: Path
    forwarded_args: list[str]

    @classmethod
    def from_args(cls, args: argparse.Namespace, forwarded_args: list[str]) -> SweepConfig:
        mode = "all" if args.all_cases is not None else "case"
        target_arg = args.all_cases if args.all_cases is not None else args.case
        target = target_arg.expanduser().resolve()
        if not target.exists():
            raise SbflBatchError(f"target not found: {target}")

        top_passes = _positive_ints("top pass", _split_values(args.top_pass_values))
        window_sizes = _positive_ints("mutator window size", _split_values(args.window_size_values))
        strategies = _split_values(args.weight_strategy_values, ["uniform"])
        invalid = [strategy for strategy in strategies if strategy not in WEIGHT_STRATEGIES]
        if invalid:
            expected = ", ".join(sorted(WEIGHT_STRATEGIES))
            raise SbflBatchError(
                f"invalid mutator weight strategy: {invalid[0]}; expected one of: {expected}"
            )
        _reject_duplicates("mutator weight strategy", strategies)
        if args.sweep_jobs <= 0:
            raise SbflBatchError(f"sweep jobs must be a positive integer: {args.sweep_jobs}")
        if args.jobs is not None and args.jobs <= 0:
            raise SbflBatchError(f"jobs must be a positive integer: {args.jobs}")
        if args.line_window < 0:
            raise SbflBatchError(f"line window must be a non-negative integer: {args.line_window}")

        scripts = _repository_root() / "scripts"
        run_script = _resolve_file(args.run_script or scripts / "run_bugset_psbfl.py", "run script")
        summarize_script = _resolve_file(
            args.summarize_script or scripts / "summarize_sbfl_blocks.py",
            "summarize script",
        )
        tmp_root = (args.tmp or Path(tempfile.gettempdir()) / "run_args_sweep_sbfl").resolve()
        logs_root = args.logs.expanduser().resolve()
        tmp_root.mkdir(parents=True, exist_ok=True)
        logs_root.mkdir(parents=True, exist_ok=True)

        return cls(
            mode=mode,
            target=target,
            top_passes=top_passes,
            window_sizes=window_sizes,
            weight_strategies=strategies,
            sweep_jobs=args.sweep_jobs,
            inner_jobs=args.jobs,
            tmp_root=tmp_root,
            logs_root=logs_root,
            workdir=args.workdir.expanduser().resolve() if args.workdir else None,
            line_window=args.line_window,
            summary_bugset_root=_infer_summary_bugset_root(mode, target, args.summary_bugset_root),
            run_script=run_script,
            summarize_script=summarize_script,
            forwarded_args=forwarded_args,
        )


@dataclass(frozen=True)
class SweepPoint:
    top_pass: int
    weight_strategy: str
    window_size: int


@dataclass(frozen=True)
class SweepResult:
    point: SweepPoint
    status: str
    run_rc: int
    summary_rc: int
    elapsed_time: str
    logs_root: Path
    tmp_root: Path
    run_log: Path
    summary_log: Path
    summary_tsv: Path

    def status_row(self) -> list[object]:
        return [
            self.point.top_pass,
            self.point.weight_strategy,
            self.point.window_size,
            self.status,
            self.run_rc,
            self.summary_rc,
            self.elapsed_time,
            self.logs_root,
            self.tmp_root,
            self.run_log,
            self.summary_log,
            self.summary_tsv,
        ]


def _format_elapsed_ms(total_ms: int) -> str:
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{milliseconds:03d}"


def _raise_keyboard_interrupt(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


class SweepRunner:
    def __init__(self, config: SweepConfig) -> None:
        self.config = config
        run_id = f"{datetime.now():%Y-%m-%d-%H-%M-%S}_{os.getpid()}"
        self.sweep_logs_root = config.logs_root / run_id
        self.sweep_tmp_root = config.tmp_root / run_id
        self.status_file = self.sweep_logs_root / "sweep_status.tsv"
        self.combined_summary = self.sweep_logs_root / "args_sweep_sbfl_block_summary.tsv"
        self._status_lock = threading.Lock()
        self._process_lock = threading.Lock()
        self._print_lock = threading.Lock()
        self._processes: set[subprocess.Popen[str]] = set()
        self._stopping = threading.Event()

    def _points(self) -> list[SweepPoint]:
        return [
            SweepPoint(top_pass, strategy, window_size)
            for top_pass in self.config.top_passes
            for strategy in self.config.weight_strategies
            for window_size in self.config.window_sizes
        ]

    def _run_logged(self, command: list[str], path: Path, mode: str = "w") -> int:
        with path.open(mode, encoding="utf-8") as handle:
            try:
                process = subprocess.Popen(
                    command,
                    stdout=handle,
                    stderr=subprocess.STDOUT,
                    text=True,
                    start_new_session=True,
                )
            except OSError as exc:
                handle.write(f"[ERROR] failed to start {shlex.join(command)}: {exc}\n")
                return 127
            with self._process_lock:
                self._processes.add(process)
            try:
                return process.wait()
            finally:
                with self._process_lock:
                    self._processes.discard(process)

    def _commands(
        self, point: SweepPoint, sweep_logs: Path, sweep_tmp: Path, summary_tsv: Path
    ) -> tuple[list[str], list[str]]:
        run_command = [
            sys.executable,
            str(self.config.run_script),
            f"--{self.config.mode}",
            str(self.config.target),
            "--top-pass",
            str(point.top_pass),
            "--mutator-weight-strategy",
            point.weight_strategy,
            "--mutator-window-size",
            str(point.window_size),
            "-t",
            str(sweep_tmp),
            "-l",
            str(sweep_logs),
        ]
        if self.config.inner_jobs is not None:
            run_command.extend(["-j", str(self.config.inner_jobs)])
        if self.config.workdir is not None:
            run_command.extend(["-w", str(self.config.workdir)])
        run_command.extend(self.config.forwarded_args)
        summary_command = [
            sys.executable,
            str(self.config.summarize_script),
            str(self.config.summary_bugset_root),
            str(sweep_logs),
            "-o",
            str(summary_tsv),
            "--line-window",
            str(self.config.line_window),
        ]
        return run_command, summary_command

    def _run_point(self, point: SweepPoint) -> SweepResult:
        if self._stopping.is_set():
            raise SbflBatchError("sweep interrupted")
        start = time.monotonic()
        relative = Path(f"top_pass_{point.top_pass}") / f"strategy_{point.weight_strategy}"
        relative /= f"window_{point.window_size}"
        sweep_root = self.sweep_logs_root / relative
        sweep_logs = sweep_root / "sbfl_logs"
        sweep_tmp = self.sweep_tmp_root / relative
        run_log = sweep_root / "run_bugset_psbfl.log"
        summary_log = sweep_root / "summarize_sbfl_blocks.log"
        summary_tsv = sweep_root / "sbfl_block_summary.tsv"
        sweep_logs.mkdir(parents=True, exist_ok=True)
        sweep_tmp.mkdir(parents=True, exist_ok=True)
        run_command, summary_command = self._commands(point, sweep_logs, sweep_tmp, summary_tsv)

        run_log.write_text(
            f"[TOP-PASS] top-pass={point.top_pass}\n"
            f"[MUTATOR-STRATEGY] mutator-weight-strategy={point.weight_strategy}\n"
            f"[MUTATOR-WINDOW] mutator-window-size={point.window_size}\n"
            f"[RUN] {shlex.join(run_command)}\n",
            encoding="utf-8",
        )
        run_rc = self._run_logged(run_command, run_log, "a")
        with run_log.open("a", encoding="utf-8") as handle:
            handle.write(f"[RUN_RC] {run_rc}\n[SUMMARY] {shlex.join(summary_command)}\n")

        summary_rc = (
            130 if self._stopping.is_set() else self._run_logged(summary_command, summary_log)
        )
        status = (
            "OK"
            if run_rc == 0 and summary_rc == 0
            else "SUMMARY_FAIL"
            if summary_rc != 0
            else "RUN_FAIL"
        )
        elapsed = _format_elapsed_ms(round((time.monotonic() - start) * 1000))
        result = SweepResult(
            point,
            status,
            run_rc,
            summary_rc,
            elapsed,
            sweep_logs,
            sweep_tmp,
            run_log,
            summary_log,
            summary_tsv,
        )
        self._append_status(result)
        with self._print_lock:
            print(
                f"[DONE][top-pass={point.top_pass}]"
                f"[mutator-weight-strategy={point.weight_strategy}]"
                f"[mutator-window-size={point.window_size}] {status}, "
                f"run_rc={run_rc}, summary_rc={summary_rc}, elapsed={elapsed}"
            )
        return result

    def _append_status(self, result: SweepResult) -> None:
        with self._status_lock, self.status_file.open("a", newline="", encoding="utf-8") as handle:
            csv.writer(handle, delimiter="\t", lineterminator="\n").writerow(result.status_row())

    def stop(self) -> None:
        self._stopping.set()
        with self._process_lock:
            processes = list(self._processes)
        for process in processes:
            if process.poll() is not None:
                continue
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass

    def _write_combined_summary(self) -> None:
        header_written = False
        with self.combined_summary.open("w", newline="", encoding="utf-8") as output:
            writer = csv.writer(output, delimiter="\t", lineterminator="\n")
            with self.status_file.open(newline="", encoding="utf-8") as status_handle:
                for status in csv.DictReader(status_handle, delimiter="\t"):
                    summary_path = Path(status["summary_tsv"])
                    if not summary_path.is_file():
                        continue
                    with summary_path.open(newline="", encoding="utf-8") as summary_handle:
                        reader = csv.reader(summary_handle, delimiter="\t")
                        header = next(reader, None)
                        if header is None:
                            continue
                        prefix = [
                            status["top_pass"],
                            status["mutator_weight_strategy"],
                            status["mutator_window_size"],
                        ]
                        if not header_written:
                            writer.writerow([*STATUS_FIELDS[:3], *header])
                            header_written = True
                        for row in reader:
                            writer.writerow([*prefix, *row])
            if not header_written:
                writer.writerow(EMPTY_SUMMARY_FIELDS)

    def run(self) -> int:
        self.sweep_logs_root.mkdir(parents=True)
        self.sweep_tmp_root.mkdir(parents=True)
        with self.status_file.open("w", newline="", encoding="utf-8") as handle:
            csv.writer(handle, delimiter="\t", lineterminator="\n").writerow(STATUS_FIELDS)

        print_info("command", "sweep")
        print_config("sweep", self.config)
        print_info("resolved.run_logs", self.sweep_logs_root)
        print_info("resolved.run_tmp", self.sweep_tmp_root)
        print_info("resolved.status_file", self.status_file)
        print_info("resolved.combined_summary", self.combined_summary)

        futures: list[Future[SweepResult]] = []
        previous_sigterm = signal.signal(signal.SIGTERM, _raise_keyboard_interrupt)
        try:
            with ThreadPoolExecutor(max_workers=self.config.sweep_jobs) as executor:
                futures = [executor.submit(self._run_point, point) for point in self._points()]
                results = [future.result() for future in as_completed(futures)]
        except KeyboardInterrupt:
            self.stop()
            for future in futures:
                future.cancel()
            raise
        finally:
            signal.signal(signal.SIGTERM, previous_sigterm)

        self._write_combined_summary()
        failed = sum(result.status != "OK" for result in results)
        print("=" * 60)
        print(f"[SUMMARY] sweep runs      : {len(results)}")
        print(f"[SUMMARY] failed          : {failed}")
        print(f"[SUMMARY] status file     : {self.status_file}")
        print(f"[SUMMARY] combined summary: {self.combined_summary}")
        print(f"[SUMMARY] logs root       : {self.sweep_logs_root}")
        print("=" * 60)
        return int(failed != 0)


def run_sweep(args: argparse.Namespace, forwarded_args: list[str]) -> int:
    return SweepRunner(SweepConfig.from_args(args, forwarded_args)).run()
