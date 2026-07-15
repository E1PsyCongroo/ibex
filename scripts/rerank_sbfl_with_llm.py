#!/usr/bin/env python3
"""Compatibility entry point for ``ibex-sbfl rerank``."""

from __future__ import annotations

import os
import sys
from pathlib import Path


def main() -> None:
    project = Path(__file__).resolve().parents[1] / "tools" / "sbfl_llm"
    os.execvp(
        "uv",
        ["uv", "run", "--project", str(project), "ibex-sbfl", "rerank", *sys.argv[1:]],
    )


if __name__ == "__main__":
    main()
