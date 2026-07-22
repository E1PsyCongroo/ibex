#!/usr/bin/env python3
"""Compatibility entry point for the unified LLM summary command."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def translated_args(argv: list[str]) -> list[str]:
    if "--stats-only" in argv:
        index = argv.index("--stats-only")
        try:
            summary = argv[index + 1]
        except IndexError as exc:
            raise SystemExit("--stats-only requires a TSV path") from exc
        return ["stats", "llm", summary]
    return ["summarize", "llm", *argv]


def main() -> None:
    project = Path(__file__).resolve().parents[1] / "tools" / "ibex_sbfl_batch"
    os.execvp(
        "uv",
        [
            "uv",
            "run",
            "--project",
            str(project),
            "--frozen",
            "ibex-sbfl-batch",
            *translated_args(sys.argv[1:]),
        ],
    )


if __name__ == "__main__":
    main()
