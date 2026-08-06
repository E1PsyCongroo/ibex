"""Unified command-line interface for batch execution and statistics."""

from __future__ import annotations

import argparse
import os
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from ibex_sbfl_common.errors import SbflCommonError

from .analysis import AnalysisRunner, prepare_analysis
from .config import (
    DEFAULT_INCLUDES,
    DEFAULT_RTL,
    DEFAULT_SIMULATOR_ARGS,
    DEFAULT_TOP_MODULE,
    DEFAULT_TOP_SCOPE,
    ExecutionConfig,
    GenerationConfig,
)
from .errors import SbflBatchError
from .info import print_info
from .rerun import prepare_rerun
from .runner import GenerationRunner, discover_cases
from .statistics import (
    print_llm_stats,
    print_sbfl_stats,
    read_summary,
    summarize_llm,
    summarize_sbfl,
)
from .sweep import add_sweep_parser, run_sweep


def _add_execution_options(parser: argparse.ArgumentParser, default_logs: str) -> None:
    parser.add_argument("-j", "--jobs", type=int, default=os.cpu_count() or 1)
    parser.add_argument("-t", "--tmp", type=Path)
    parser.add_argument("-l", "--logs", type=Path, default=Path(default_logs))
    parser.add_argument(
        "-w",
        "--workdir",
        type=Path,
        default=Path(os.environ.get("IBEX_HOME", Path.cwd())),
    )
    parser.add_argument("--keep-workdir", action="store_true")
    parser.add_argument("--disassemble", action="store_true")
    parser.add_argument(
        "--sbfl-bin",
        default=("build/lowrisc_ibex_ibex_simple_system_sbfl_0/sim-verilator/Vibex_simple_system"),
    )
    parser.add_argument("--dry-run", action="store_true")


def _add_selection_options(parser: argparse.ArgumentParser, *, inherited: bool = False) -> None:
    parser.add_argument("--top-pass", type=int, default=None if inherited else 120)
    parser.add_argument(
        "--selection",
        choices=["random", "sort", "diverse"],
        default=None if inherited else "sort",
    )
    parser.add_argument(
        "--selection-diversity-weight", type=float, default=None if inherited else 0.4
    )
    parser.add_argument("--selection-pool-factor", type=int, default=None if inherited else 5)
    parser.add_argument("--top-sus", type=int, default=None if inherited else 50)
    parser.add_argument("--cover-distance-weight", type=float, default=None if inherited else 0.5)
    parser.add_argument("--metric", default=None if inherited else "ochiai")


def _add_mode_options(parser: argparse.ArgumentParser, *, inherited: bool = False) -> None:
    parser.add_argument("--mutator-window-size", type=int, default=None if inherited else 5)
    parser.add_argument(
        "--mutator-weight-strategy",
        choices=["uniform", "tail_linear", "tail_quad", "head_linear", "head_quad"],
        default=None if inherited else "uniform",
    )
    parser.add_argument("--max-corpus-size", type=int, default=None if inherited else 500)
    parser.add_argument("--init-seed-rate", type=float, default=None if inherited else 0.2)
    parser.add_argument("--mutate-rate", type=float, default=None if inherited else 0.2)
    parser.add_argument("--priority-alpha", type=float, default=None if inherited else 0.1)
    parser.add_argument("--failed-reward", type=float, default=None if inherited else 5.0)


def _add_generation_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("generation", help="run generation on patched bug cases")
    parser.add_argument("mode", choices=["psbfl", "random", "withw"])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--all", dest="all_cases", type=Path)
    target.add_argument("--case", type=Path)
    _add_execution_options(parser, "./logs")
    _add_selection_options(parser)
    _add_mode_options(parser, inherited=True)
    parser.add_argument("-r", "--reduce-insts", "--reduce", action="store_true")
    parser.add_argument("--reduce-cover", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("-c", "--coverage", default="verilator.branch,verilator.line")
    parser.add_argument("-s", "--state", default="PCState,ArchIntRegState,CSRState")
    parser.add_argument("--max-run-timeout", type=int, default=180)
    parser.add_argument("--max-iters", type=int, default=20)
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--input", type=Path, default=Path("examples/sw/benchmarks/coremark/coremark.elf")
    )
    source.add_argument("--resume-corpus", type=Path)
    parser.add_argument("--save-corpus", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--checkpoint-interval", type=int)
    parser.add_argument("--gen-only", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--save-reduce", action="store_true")
    parser.add_argument("--save-intermediate", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument("--tracker-window-size", type=int, default=20)
    parser.add_argument("--rtl-path", default=DEFAULT_RTL)
    parser.add_argument("--include-paths", default=DEFAULT_INCLUDES)
    parser.add_argument("--top-module", default=DEFAULT_TOP_MODULE)
    parser.add_argument("--top-scope", default=DEFAULT_TOP_SCOPE)


def _add_rerun_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("rerun", help="resume generation from saved corpora")
    parser.add_argument("--input-logs", type=Path, required=True)
    parser.add_argument("--max-iters", type=int, required=True)
    _add_execution_options(parser, "./logs/rerun")
    _add_selection_options(parser, inherited=True)
    _add_mode_options(parser, inherited=True)
    parser.add_argument("--max-run-timeout", type=int)
    checkpoint = parser.add_mutually_exclusive_group()
    checkpoint.add_argument("--checkpoint-interval", type=int)
    checkpoint.add_argument("--no-checkpoint-interval", action="store_true")
    parser.add_argument("--save-corpus", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--gen-only", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--save-intermediate", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--reduce-cover", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--rtl-path", default=DEFAULT_RTL)
    parser.add_argument("--include-paths", default=DEFAULT_INCLUDES)
    parser.add_argument("--top-module", default=DEFAULT_TOP_MODULE)
    parser.add_argument("--top-scope", default=DEFAULT_TOP_SCOPE)


def _add_analysis_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("analysis", help="reanalyze saved corpora")
    parser.add_argument("--input-logs", type=Path, required=True)
    _add_execution_options(parser, "./logs/analysis")
    _add_selection_options(parser, inherited=True)
    parser.add_argument("--reduce-cover", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--save-intermediate", action=argparse.BooleanOptionalAction, default=None)
    parser.add_argument("--rtl-path", default=DEFAULT_RTL)
    parser.add_argument("--include-paths", default=DEFAULT_INCLUDES)
    parser.add_argument("--top-module", default=DEFAULT_TOP_MODULE)
    parser.add_argument("--top-scope", default=DEFAULT_TOP_SCOPE)


def _add_summary_parser(subparsers: argparse._SubParsersAction) -> None:
    summary = subparsers.add_parser("summarize", help="summarize localization results")
    kinds = summary.add_subparsers(dest="summary_kind", required=True)
    for kind, output in (("sbfl", "sbfl_block_summary.tsv"), ("llm", "llm_rerank_summary.tsv")):
        parser = kinds.add_parser(kind)
        parser.add_argument("bugset_root", type=Path)
        parser.add_argument("logs_root", type=Path)
        parser.add_argument("-o", "--output", type=Path, default=Path(output))
        parser.add_argument("--line-window", type=int, default=0)
        if kind == "llm":
            parser.add_argument("--rerank-filename", default="llm_rerank.json")


def _add_stats_parser(subparsers: argparse._SubParsersAction) -> None:
    stats = subparsers.add_parser("stats", help="print statistics from an existing TSV")
    kinds = stats.add_subparsers(dest="stats_kind", required=True)
    for kind in ("sbfl", "llm"):
        parser = kinds.add_parser(kind)
        parser.add_argument("summary", type=Path)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ibex-sbfl-batch")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_generation_parser(subparsers)
    _add_rerun_parser(subparsers)
    _add_analysis_parser(subparsers)
    add_sweep_parser(subparsers)
    _add_summary_parser(subparsers)
    _add_stats_parser(subparsers)
    return parser


def _split_simulator_args(argv: list[str]) -> tuple[list[str], list[str] | None]:
    try:
        separator = argv.index("--")
    except ValueError:
        return argv, None
    return argv[:separator], argv[separator + 1 :]


def _execution(args: argparse.Namespace, tmp_name: str) -> ExecutionConfig:
    ibex_home = args.workdir.expanduser().resolve()
    logs_root = args.logs.expanduser().resolve()
    tmp_root = (
        args.tmp.expanduser().resolve()
        if args.tmp is not None
        else Path(tempfile.gettempdir()).joinpath(tmp_name).resolve()
    )
    return ExecutionConfig(
        ibex_home=ibex_home,
        logs_root=logs_root,
        tmp_root=tmp_root,
        jobs=args.jobs,
        sbfl_bin=args.sbfl_bin,
        keep_workdir=args.keep_workdir,
        disassemble=args.disassemble,
        objdump_bin=os.environ.get("OBJDUMP_BIN", "riscv32-unknown-elf-objdump"),
        dry_run=args.dry_run,
    )


def _run_generation(args: argparse.Namespace) -> int:
    target_mode = "all" if args.all_cases is not None else "case"
    target = args.all_cases if args.all_cases is not None else args.case
    cases = discover_cases(target_mode, target)
    input_path: Path | None = None
    if args.resume_corpus is not None:
        if args.reduce_insts:
            raise SbflBatchError("--reduce-insts conflicts with --resume-corpus")
        resume_corpus = args.resume_corpus.expanduser().resolve()
        if not resume_corpus.is_file():
            raise SbflBatchError(f"resume corpus not found: {resume_corpus}")
        cases = [replace(case, corpus=resume_corpus) for case in cases]
    else:
        input_path = args.input.expanduser()
        if not input_path.is_absolute():
            input_path = args.workdir.expanduser() / input_path
        input_path = input_path.resolve()
        if not input_path.is_file():
            raise SbflBatchError(f"input path not found: {input_path}")
    psbfl_options = ("mutator_window_size", "mutator_weight_strategy")
    withw_options = (
        "max_corpus_size",
        "init_seed_rate",
        "mutate_rate",
        "priority_alpha",
        "failed_reward",
    )
    if args.mode == "psbfl":
        wrong = [name for name in withw_options if getattr(args, name) is not None]
        if wrong:
            raise SbflBatchError(f"WitHW options are not valid for PSBFL: {', '.join(wrong)}")
    elif args.mode == "withw":
        wrong = [name for name in psbfl_options if getattr(args, name) is not None]
        if wrong:
            raise SbflBatchError(f"PSBFL options are not valid for WitHW: {', '.join(wrong)}")
    else:
        wrong = [
            name for name in (*psbfl_options, *withw_options) if getattr(args, name) is not None
        ]
        if wrong:
            raise SbflBatchError(
                f"mode-specific options are not valid for Random: {', '.join(wrong)}"
            )
    config = GenerationConfig(
        mode=args.mode,
        coverage=args.coverage,
        state=args.state,
        max_run_timeout=args.max_run_timeout,
        max_iters=args.max_iters,
        top_pass=args.top_pass,
        selection=args.selection,
        selection_diversity_weight=args.selection_diversity_weight,
        selection_pool_factor=args.selection_pool_factor,
        top_sus=args.top_sus,
        tracker_window_size=args.tracker_window_size,
        cover_distance_weight=args.cover_distance_weight,
        reduce_insts=args.reduce_insts,
        reduce_cover=args.reduce_cover,
        input_path=input_path,
        save_corpus=args.save_corpus,
        checkpoint_interval=args.checkpoint_interval,
        gen_only=args.gen_only,
        save_reduce=args.save_reduce,
        save_intermediate=args.save_intermediate,
        rtl_path=args.rtl_path,
        include_paths=args.include_paths,
        top_module=args.top_module,
        top_scope=args.top_scope,
        metric=args.metric,
        mutator_window_size=(20 if args.mutator_window_size is None else args.mutator_window_size),
        mutator_weight_strategy=args.mutator_weight_strategy or "uniform",
        max_corpus_size=50 if args.max_corpus_size is None else args.max_corpus_size,
        init_seed_rate=0.2 if args.init_seed_rate is None else args.init_seed_rate,
        mutate_rate=0.2 if args.mutate_rate is None else args.mutate_rate,
        priority_alpha=0.1 if args.priority_alpha is None else args.priority_alpha,
        failed_reward=5.0 if args.failed_reward is None else args.failed_reward,
        simulator_args=list(
            DEFAULT_SIMULATOR_ARGS if args.simulator_args is None else args.simulator_args
        ),
    )
    tmp_name = f"run_bugset_{args.mode}"
    return GenerationRunner(_execution(args, tmp_name), config, cases).run()


def main(argv: Sequence[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    command_args, simulator_args = _split_simulator_args(raw)
    parser = build_parser()
    args = parser.parse_args(command_args)
    args.simulator_args = simulator_args
    print_info("cli.argv", raw)
    try:
        if args.command == "generation":
            return _run_generation(args)
        if args.command == "rerun":
            config, cases = prepare_rerun(args)
            return GenerationRunner(
                _execution(args, "rerun_bugset_corpus"),
                config,
                cases,
                additional_iterations=True,
            ).run()
        if args.command == "analysis":
            config, cases = prepare_analysis(args)
            return AnalysisRunner(_execution(args, "analyze_bugset_corpus"), config, cases).run()
        if args.command == "sweep":
            return run_sweep(args, args.simulator_args or [])
        if args.command == "summarize":
            print_info("command", f"summarize {args.summary_kind}")
            print_info("summary.bugset_root", args.bugset_root.expanduser().resolve())
            print_info("summary.logs_root", args.logs_root.expanduser().resolve())
            print_info("summary.output", args.output.expanduser().resolve())
            print_info("summary.line_window", args.line_window)
            if args.summary_kind == "llm":
                print_info("summary.rerank_filename", args.rerank_filename)
            if args.line_window < 0:
                raise SbflBatchError("--line-window must be non-negative")
            bugset_root = args.bugset_root.expanduser().resolve()
            logs_root = args.logs_root.expanduser().resolve()
            if not bugset_root.is_dir() or not logs_root.is_dir():
                raise SbflBatchError("bugset_root and logs_root must both be directories")
            if args.summary_kind == "sbfl":
                summarize_sbfl(bugset_root, logs_root, args.output, args.line_window)
            else:
                summarize_llm(
                    bugset_root,
                    logs_root,
                    args.output,
                    args.line_window,
                    args.rerank_filename,
                )
            return 0
        if args.command == "stats":
            print_info("command", f"stats {args.stats_kind}")
            print_info("stats.summary", args.summary.expanduser().resolve())
            rows = read_summary(args.summary)
            if args.stats_kind == "sbfl":
                print_sbfl_stats(rows)
            else:
                print_llm_stats(rows)
            return 0
        parser.error(f"unsupported command: {args.command}")
    except KeyboardInterrupt:
        print("[INTERRUPT] stopping active cases", file=sys.stderr)
        return 130
    except (OSError, ValueError, SbflCommonError, SbflBatchError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
