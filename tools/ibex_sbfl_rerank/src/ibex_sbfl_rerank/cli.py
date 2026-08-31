"""Command-line interface for offline reranking."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ibex_sbfl_common.errors import SbflCommonError

from .ranking import RerankError
from .workflow import (
    default_output_filename,
    find_bugset_root,
    run_directory,
)


def _weight(value: str) -> float:
    try:
        weight = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not 0.0 <= weight <= 1.0:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return weight


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if number <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ibex-sbfl-rerank",
        description=(
            "Rerank saved llm_rerank.json files from prompt candidates and raw model scores, "
            "then print batch localization statistics."
        ),
    )
    parser.add_argument(
        "logs_root",
        type=Path,
        help="case directory or logs tree containing llm_rerank.json/prompt pairs",
    )
    parser.add_argument(
        "--llm-weight",
        type=_weight,
        default=0.75,
        help="LLM contribution; 1.0 uses only the LLM score (default: 0.75)",
    )
    parser.add_argument("--top-k", type=_positive_int, help="override each source result's top_k")
    parser.add_argument("--bugset-root", type=Path, help="default: auto-detect verify_dataset")
    parser.add_argument("--line-window", type=int, default=0)
    parser.add_argument("--input-filename", default="llm_rerank.json")
    parser.add_argument(
        "--output-filename",
        help="default: llm_rerank.w<weight>.json",
    )
    parser.add_argument("--summary", type=Path, help="output statistics TSV path")
    return parser


def _plain_filename(value: str, option: str) -> str:
    if not value or Path(value).name != value or value in {".", ".."}:
        raise RerankError(f"{option} must be a plain filename, got {value!r}")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.line_window < 0:
            raise RerankError("--line-window must be non-negative")
        logs_root = args.logs_root.expanduser().resolve()
        if not logs_root.is_dir():
            raise RerankError(f"logs root is not a directory: {logs_root}")
        input_filename = _plain_filename(args.input_filename, "--input-filename")
        output_filename = _plain_filename(
            args.output_filename or default_output_filename(args.llm_weight),
            "--output-filename",
        )
        if input_filename == output_filename:
            raise RerankError("input and output filenames must differ")
        bugset_root = find_bugset_root(logs_root, args.bugset_root)
        summary_path = (
            args.summary.expanduser().resolve()
            if args.summary is not None
            else logs_root / f"{Path(output_filename).stem}.summary.tsv"
        )
        print(f"[CONFIG] logs_root={logs_root}")
        print(f"[CONFIG] bugset_root={bugset_root}")
        print(f"[CONFIG] llm_weight={args.llm_weight}")
        print(f"[CONFIG] output_filename={output_filename}")
        return run_directory(
            logs_root,
            llm_weight=args.llm_weight,
            input_filename=input_filename,
            output_filename=output_filename,
            top_k=args.top_k,
            bugset_root=bugset_root,
            summary_path=summary_path,
            line_window=args.line_window,
        )
    except (OSError, RerankError, SbflCommonError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
