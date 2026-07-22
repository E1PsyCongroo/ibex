#!/usr/bin/env python3
"""Generic compatibility entry point for SBFL bugset generation."""

from __future__ import annotations

import sys

from _sbfl_batch_entry import exec_sbfl_batch


def main() -> None:
    exec_sbfl_batch(["generation"], sys.argv[1:])


if __name__ == "__main__":
    main()
