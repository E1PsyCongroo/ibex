"""Small text helpers shared by artifact readers."""

from pathlib import Path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")
