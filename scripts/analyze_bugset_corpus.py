#!/usr/bin/env python3
"""Compatibility entry point for saved-corpus analysis."""

from __future__ import annotations

import sys

from _sbfl_batch_entry import exec_sbfl_batch


def main() -> None:
    exec_sbfl_batch(["analysis"], sys.argv[1:])


if __name__ == "__main__":
    main()
