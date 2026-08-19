"""Unified command-line interface."""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from ibex_sbfl_common.errors import SbflCommonError

from .errors import SbflLlmError
from .rerank import run_rerank


class HelpFormatter(
    argparse.ArgumentDefaultsHelpFormatter,
    argparse.RawDescriptionHelpFormatter,
):
    """Preserve examples while showing defaults in option help."""

    def _get_help_string(self, action: argparse.Action) -> str:
        if action.default is None or action.default is False:
            return action.help or ""
        return super()._get_help_string(action)


def _add_rerank_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "rerank",
        help="score and rerank SBFL block candidates with an LLM",
        description=(
            "Apply a bug patch to a temporary copy of the RTL source, collect source "
            "context for suspicious SBFL blocks, and ask an LLM to rerank them."
        ),
        epilog=(
            "Examples:\n"
            "  ibex-sbfl rerank rtl logs/sbfl/case \\\n"
            "    --patch verify_dataset/7/ibex_multdiv_fast.sv.diff \\\n"
            "    --model gpt-5.5\n"
            "  ibex-sbfl rerank rtl logs/sbfl/case --patch bug.diff --dry-run\n"
            "  ibex-sbfl rerank rtl logs/sbfl/case --patch bug.diff \\\n"
            "    --model gpt-5.5 --candidate-count 80 --top-k 30 --include-reason"
        ),
        formatter_class=HelpFormatter,
    )

    inputs = parser.add_argument_group("inputs and patching")
    inputs.add_argument(
        "rtl_source",
        type=Path,
        help="path to the original, unpatched RTL source directory",
    )
    inputs.add_argument(
        "sbfl_result",
        type=Path,
        help="SBFL result directory or its result.log file",
    )
    inputs.add_argument(
        "--patch",
        type=Path,
        help="diff to apply to a temporary RTL copy (default: discover [DIFF] in run.log)",
    )
    inputs.add_argument(
        "--allow-unpatched-source",
        action="store_true",
        help="allow execution when no patch is found; use only for intentional unpatched runs",
    )
    test_info = inputs.add_mutually_exclusive_group()
    test_info.add_argument(
        "--test-info",
        help="inline failure or test context to include in the prompt",
    )
    test_info.add_argument(
        "--test-info-file",
        type=Path,
        help="UTF-8 file containing failure or test context",
    )

    model = parser.add_argument_group("model and API")
    model.add_argument(
        "--model",
        help="model name; required unless --dry-run is used",
    )
    model.add_argument(
        "--api-base",
        help=(
            "API URL; defaults to ANTHROPIC_BASE_URL for the anthropic protocol "
            "or OPENAI_BASE_URL for OpenAI protocols"
        ),
    )
    model.add_argument(
        "--api-protocol",
        choices=["anthropic", "responses", "chat-completions"],
        default="responses",
        help="request protocol: Claude Messages or an OpenAI-compatible API",
    )
    model.add_argument(
        "--api-key-env",
        help=(
            "environment variable containing the API key; defaults to "
            "ANTHROPIC_API_KEY or OPENAI_API_KEY based on the protocol; pass an "
            "empty value for no auth"
        ),
    )
    model.add_argument(
        "--timeout",
        type=float,
        default=180.0,
        metavar="SECONDS",
        help="timeout for each model request",
    )
    model.add_argument(
        "--temperature",
        type=float,
        help="sampling temperature (default: provider/model default)",
    )
    model.add_argument(
        "--max-output-tokens",
        type=int,
        default=8192,
        metavar="N",
        help="maximum output tokens for the Claude Messages API",
    )
    model.add_argument(
        "--reasoning-effort",
        choices=["low", "medium", "high"],
        default="high",
        help="model reasoning effort shared by Anthropic and OpenAI APIs",
    )
    model.add_argument(
        "--retries",
        type=int,
        default=2,
        help="number of retries after the initial model request",
    )
    model.add_argument(
        "--retry-delay",
        type=float,
        default=2.0,
        metavar="SECONDS",
        help="base delay between model request retries",
    )
    model.add_argument(
        "--structured-output",
        choices=["auto", "strict", "off"],
        default="auto",
        help="structured response mode: auto fallback, strict requirement, or disabled",
    )

    candidates = parser.add_argument_group("candidates and source context")
    candidates.add_argument(
        "--candidate-count",
        type=int,
        default=50,
        metavar="N",
        help="number of highest-ranked SBFL candidates sent to the model",
    )
    candidates.add_argument(
        "--top-k",
        type=int,
        default=20,
        metavar="N",
        help="number of final ranked candidates written to the output",
    )
    candidates.add_argument(
        "--source-mode",
        choices=["snippets", "full", "auto"],
        default="auto",
        help="RTL context mode; auto uses full source when it fits, otherwise snippets",
    )
    candidates.add_argument(
        "--max-source-chars",
        type=int,
        default=240_000,
        metavar="N",
        help="maximum RTL source characters included in the prompt",
    )
    candidates.add_argument(
        "--snippet-radius",
        type=int,
        default=30,
        metavar="LINES",
        help="source lines before and after each candidate in snippet mode",
    )

    ranking = parser.add_argument_group("ranking")
    ranking.add_argument(
        "--ranking-strategy",
        choices=["weighted", "llm-only", "rrf"],
        default="weighted",
        help="method used to combine SBFL order and model scores",
    )
    ranking.add_argument(
        "--llm-weight",
        type=float,
        default=0.75,
        metavar="FLOAT",
        help="LLM contribution for the weighted strategy, from 0 to 1",
    )
    ranking.add_argument(
        "--include-reason",
        action="store_true",
        help="request and output one concise reason for each model score",
    )

    output = parser.add_argument_group("output and diagnostics")
    output.add_argument(
        "--output",
        type=Path,
        help="output JSON path (default: <SBFL result directory>/llm_rerank.json)",
    )
    output.add_argument(
        "--save-prompt",
        action="store_true",
        help="save the rendered prompt beside the output JSON",
    )
    output.add_argument(
        "--dry-run",
        action="store_true",
        help="print the prompt without calling the model or writing rerank JSON",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ibex-sbfl",
        description="LLM-assisted reranking for Ibex SBFL localization results.",
        epilog="Run `ibex-sbfl rerank --help` for rerank options and examples.",
        formatter_class=HelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_rerank_parser(subparsers)
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
        if (
            args.retries < 0
            or args.retry_delay < 0
            or args.timeout <= 0
            or args.max_output_tokens <= 0
        ):
            parser.error("retry and timeout values are invalid")
        if not args.dry_run and not args.model:
            parser.error("--model is required unless --dry-run is used")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _validate_args(parser, args)
    try:
        if args.command == "rerank":
            return run_rerank(args)
    except (OSError, ValueError, SbflCommonError, SbflLlmError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
