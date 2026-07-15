#!/usr/bin/env python3
"""Compatibility entry point for the unified SBFL summary command."""

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
        return ["stats", "sbfl", summary]
    return ["summarize", "sbfl", *argv]


def main() -> None:
    project = Path(__file__).resolve().parents[1] / "tools" / "sbfl_llm"
    os.execvp(
        "uv",
        ["uv", "run", "--project", str(project), "ibex-sbfl", *translated_args(sys.argv[1:])],
    )


if __name__ == "__main__":
    main()
