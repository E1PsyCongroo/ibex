#!/usr/bin/env python3
"""Shared uv launcher for repository-level SBFL batch entry points."""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path


def exec_sbfl_batch(command: Sequence[str], argv: Sequence[str]) -> None:
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
            *command,
            *argv,
        ],
    )
