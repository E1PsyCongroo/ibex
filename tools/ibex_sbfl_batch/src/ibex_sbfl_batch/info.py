"""Consistent startup configuration reporting for batch commands."""

from __future__ import annotations

import shlex
from collections.abc import Sequence
from dataclasses import fields, is_dataclass
from pathlib import Path
from typing import Any


def format_info_value(value: Any) -> str:
    if value is None:
        return "<none>"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return shlex.join([str(item) for item in value]) if value else "<empty>"
    return str(value)


def print_info(name: str, value: Any) -> None:
    print(f"[INFO] {name:<36}: {format_info_value(value)}")


def print_config(prefix: str, config: object) -> None:
    if not is_dataclass(config):
        raise TypeError(f"configuration is not a dataclass: {type(config).__name__}")
    for field in fields(config):
        print_info(f"{prefix}.{field.name}", getattr(config, field.name))
