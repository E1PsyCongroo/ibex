"""Unified command-line interface."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from .errors import SbflLlmError
from .rerank import run_rerank
from .summarize import (
    print_llm_stats,
    print_sbfl_stats,
    read_summary,
    summarize_llm,
    summarize_sbfl,
)


def _add_rerank_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("rerank", help="score and rerank SBFL block candidates")
    parser.add_argument("rtl_source", type=Path)
    parser.add_argument("sbfl_result", type=Path)
    parser.add_argument("--model", help="model name (required unless --dry-run)")
    parser.add_argument(
        "--api-base",
        default=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    )
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--candidate-count", type=int, default=50)
    parser.add_argument("--top-k", type=int, default=20)
    parser.add_argument("--source-mode", choices=["snippets", "full", "auto"], default="auto")
    parser.add_argument("--max-source-chars", type=int, default=240_000)
    parser.add_argument("--snippet-radius", type=int, default=30)
    parser.add_argument("--patch", type=Path)
    parser.add_argument("--allow-unpatched-source", action="store_true")
    test_info = parser.add_mutually_exclusive_group()
    test_info.add_argument("--test-info")
    test_info.add_argument("--test-info-file", type=Path)
    parser.add_argument(
        "--ranking-strategy",
        choices=["weighted", "llm-only", "rrf"],
        default="weighted",
    )
    parser.add_argument("--llm-weight", type=float, default=0.75)
    parser.add_argument("--structured-output", choices=["auto", "strict", "off"], default="auto")
    parser.add_argument("--timeout", type=float, default=180.0)
    parser.add_argument("--temperature", type=float)
    parser.add_argument("--retries", type=int, default=2)
    parser.add_argument("--retry-delay", type=float, default=2.0)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--save-prompt", action="store_true")
    parser.add_argument("--dry-run", action="store_true")


def _add_summary_parser(subparsers: argparse._SubParsersAction) -> None:
    summary = subparsers.add_parser("summarize", help="summarize localization results")
    kinds = summary.add_subparsers(dest="summary_kind", required=True)
    outputs = (("sbfl", "sbfl_block_summary.tsv"), ("llm", "llm_rerank_summary.tsv"))
    for kind, default_output in outputs:
        parser = kinds.add_parser(kind)
        parser.add_argument("bugset_root", type=Path)
        parser.add_argument("logs_root", type=Path)
        parser.add_argument("-o", "--output", type=Path, default=Path(default_output))
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
    parser = argparse.ArgumentParser(prog="ibex-sbfl")
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_rerank_parser(subparsers)
    _add_summary_parser(subparsers)
    _add_stats_parser(subparsers)
    return parser


def _validate_args(parser: argparse.ArgumentParser, args: argparse.Namespace) -> None:
    if args.command == "rerank":
        if args.candidate_count <= 0 or args.top_k <= 0:
            parser.error("--candidate-count and --top-k must be positive")
        if args.top_k > args.candidate_count:
            parser.error("--top-k cannot exceed --candidate-count")
        if args.max_source_chars <= 0 or args.snippet_radius < 0:
            parser.error("source limits are invalid")
        if not 0.0 <= args.llm_weight <= 1.0:
            parser.error("--llm-weight must be between 0 and 1")
        if args.retries < 0 or args.retry_delay < 0 or args.timeout <= 0:
            parser.error("retry and timeout values are invalid")
        if not args.dry_run and not args.model:
            parser.error("--model is required unless --dry-run is used")
    elif args.command == "summarize" and args.line_window < 0:
        parser.error("--line-window must be non-negative")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    try:
        if args.command == "rerank":
            return run_rerank(args)
        if args.command == "summarize":
            bugset_root = args.bugset_root.resolve()
            logs_root = args.logs_root.resolve()
            if not bugset_root.is_dir() or not logs_root.is_dir():
                raise SbflLlmError("bugset_root and logs_root must both be directories")
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
        rows = read_summary(args.summary)
        if args.stats_kind == "sbfl":
            print_sbfl_stats(rows)
        else:
            print_llm_stats(rows)
        return 0
    except (OSError, ValueError, SbflLlmError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
