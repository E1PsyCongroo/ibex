"""Parsing and rank evaluation for SBFL result artifacts."""

from __future__ import annotations

import json
import re
import sys
from collections.abc import Iterable, Mapping, Sequence
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any

from .errors import SbflCommonError
from .io_utils import read_text

BLOCK_RANK_RE = re.compile(
    r"^top-(?P<rank>\d+):\s+"
    r"Block\(scope:\s+(?P<scope>.*),\s+bid:\s+(?P<bid>\d+)\)\s+"
    r"with suspicious\s+'(?P<sus>[^']+)'"
)
STATUS_RE = re.compile(
    r"^\[(?P<kind>[A-Z_ ]+)\]\s+"
    r"(?P<bugcase>[^,\s]+)"
    r"(?:,\s*status=(?P<status>-?\d+))?"
    r"(?:,\s*elapsed=(?P<elapsed>\d+:\d{2}:\d{2}:\d{3}))?"
    r"(?:,\s*elapsed_ms=(?P<elapsed_ms>\d+))?"
    r"\s*$"
)


def resolve_input_files(sbfl_path: Path) -> tuple[Path, Path, Path]:
    if sbfl_path.is_dir():
        result_log = sbfl_path / "result.log"
        blocks_json = sbfl_path / "blocks.json"
        output_dir = sbfl_path
    elif sbfl_path.name == "result.log":
        result_log = sbfl_path
        blocks_json = sbfl_path.with_name("blocks.json")
        output_dir = sbfl_path.parent
    else:
        raise SbflCommonError("SBFL path must be a result directory or a result.log file")

    for path in (result_log, blocks_json):
        if not path.is_file():
            raise SbflCommonError(f"required input file does not exist: {path}")
    return result_log, blocks_json, output_dir


def parse_block_suspiciousness(result_log_path: Path) -> list[dict[str, Any]]:
    ranked: list[dict[str, Any]] = []
    in_block_section = False
    for raw in read_text(result_log_path).splitlines():
        line = raw.strip()
        if line == "Suspiciousness of block:":
            in_block_section = True
            continue
        if not in_block_section:
            continue
        if line.startswith("Suspiciousness of "):
            break
        match = BLOCK_RANK_RE.match(line)
        if match:
            ranked.append(
                {
                    "rank": int(match.group("rank")),
                    "scope": match.group("scope"),
                    "bid": int(match.group("bid")),
                    "sus": match.group("sus"),
                }
            )
    return ranked


def load_blocks(blocks_path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    try:
        data = json.loads(read_text(blocks_path))
    except json.JSONDecodeError as exc:
        raise SbflCommonError(f"invalid JSON in {blocks_path}: {exc}") from exc
    if not isinstance(data, list):
        raise SbflCommonError(f"{blocks_path} must contain a JSON array")

    block_map: dict[tuple[str, int], dict[str, Any]] = {}
    for raw in data:
        if not isinstance(raw, dict):
            continue
        try:
            key = (str(raw["scope"]), int(raw["bid"]))
        except (KeyError, TypeError, ValueError):
            continue
        block_map[key] = raw
    return block_map


def read_gen_time(logdir: Path) -> str:
    path = logdir / "elapsed" / "gen_time.txt"
    return read_text(path).strip() if path.is_file() else ""


def read_sbfl_time(logdir: Path) -> str:
    path = logdir / "elapsed" / "sbfl_time.txt"
    return read_text(path).strip() if path.is_file() else ""


def parse_time(value: str) -> float:
    value = value.strip()
    if not value:
        return 0.0
    units = (("ms", 1_000), ("µs", 1_000_000), ("us", 1_000_000), ("ns", 1_000_000_000))
    for suffix, divisor in units:
        if value.endswith(suffix):
            return float(value[: -len(suffix)]) / divisor
    if value.endswith("s"):
        return float(value[:-1])
    return float(value)


def parse_rank(rank_str: str) -> float | None:
    value = rank_str.strip().lower()
    if value.startswith("over"):
        return None
    if value.startswith("top-"):
        value = value[4:]
    try:
        return float(value)
    except ValueError:
        return None


def parse_status(status_path: Path) -> tuple[str, bool, str] | None:
    text = read_text(status_path).strip()
    match = STATUS_RE.match(text)
    if not match:
        print(f"[WARN] unrecognized status format: {status_path}: {text!r}", file=sys.stderr)
        return None
    kind = match.group("kind").strip().replace(" ", "_")
    bugcase = match.group("bugcase")
    if kind == "OK":
        return bugcase, True, "OK"
    status = match.group("status") or "-1"
    return bugcase, False, f"{kind}({status})"


def resolve_bugcase_ref(bugset_root: Path, bugcase: str) -> tuple[str, str, Path, Path] | None:
    bugcase_path = Path(bugcase)
    if not bugcase_path.name.endswith(".sv.diff") or str(bugcase_path.parent) == ".":
        print(f"[WARN] invalid bugcase reference: {bugcase!r}", file=sys.stderr)
        return None
    case_dir = bugset_root / bugcase_path.parent
    diff_path = case_dir / bugcase_path.name
    if not case_dir.is_dir() or not diff_path.is_file():
        print(f"[WARN] missing bugcase data: {diff_path}", file=sys.stderr)
        return None
    return str(bugcase_path.parent), bugcase_path.name, case_dir, diff_path


def load_bug_info_from_case_dir(case_dir: Path) -> dict[str, Any] | None:
    path = case_dir / "bug_info.json"
    if not path.is_file():
        print(f"[WARN] missing bug_info.json: {path}", file=sys.stderr)
        return None
    try:
        value = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        print(f"[WARN] invalid bug_info.json {path}: {exc}", file=sys.stderr)
        return None
    if not isinstance(value, dict):
        return None
    for key in ("module_name", "scope_name", "modify_line"):
        if key not in value:
            print(f"[WARN] missing key {key!r} in {path}", file=sys.stderr)
            return None
    return value


def line_set_with_window(lines: Iterable[Any], window: int) -> set[int]:
    result: set[int] = set()
    for raw_line in lines:
        line = int(raw_line)
        result.update(range(line - window, line + window + 1))
    return result


def suspiciousness_key(value: str) -> tuple[str, Decimal | str]:
    value = str(value).strip()
    try:
        return "num", Decimal(value)
    except InvalidOperation:
        return "str", value


def format_average_rank(rank: Fraction) -> str:
    if rank.denominator == 1:
        return str(rank.numerator)
    return f"{rank.numerator / rank.denominator:.6f}".rstrip("0").rstrip(".")


def iter_suspiciousness_tie_groups(
    ranked_blocks: Sequence[Mapping[str, Any]],
) -> list[tuple[list[Mapping[str, Any]], Fraction]]:
    groups: list[tuple[list[Mapping[str, Any]], Fraction]] = []
    current: list[Mapping[str, Any]] = []
    current_key: tuple[str, Decimal | str] | None = None
    for item in sorted(ranked_blocks, key=lambda value: int(value["rank"])):
        key = suspiciousness_key(str(item["sus"]))
        if current and key != current_key:
            groups.append((current, Fraction(sum(int(x["rank"]) for x in current), len(current))))
            current = []
        current.append(item)
        current_key = key
    if current:
        groups.append((current, Fraction(sum(int(x["rank"]) for x in current), len(current))))
    return groups


def is_bug_block(
    item: Mapping[str, Any],
    block_map: Mapping[tuple[str, int], Mapping[str, Any]],
    module_name: str,
    scope_name: str,
    target_lines: set[int],
) -> bool:
    block = block_map.get((str(item["scope"]), int(item["bid"])))
    if block is None:
        return False
    if str(block.get("scope", "")) != scope_name or str(block.get("module", "")) != module_name:
        return False
    return bool({int(line) for line in block.get("lines", [])} & target_lines)


def find_bug_rank(
    bug_info: Mapping[str, Any],
    ranked_blocks: Sequence[Mapping[str, Any]],
    block_map: Mapping[tuple[str, int], Mapping[str, Any]],
    line_window: int = 0,
) -> tuple[str, str]:
    target_lines = line_set_with_window(bug_info["modify_line"], line_window)
    top_n = len(ranked_blocks)
    groups = iter_suspiciousness_tie_groups(ranked_blocks)
    if top_n > 0 and len(groups) == 1:
        return f"over top-{top_n}", ""
    if top_n == 0:
        return "over top-0", ""

    boundary = max(ranked_blocks, key=lambda item: int(item["rank"]))
    boundary_key = suspiciousness_key(str(boundary["sus"]))
    for group, average_rank in groups:
        group_reaches_boundary = suspiciousness_key(str(group[0]["sus"])) == boundary_key
        for item in group:
            if not is_bug_block(
                item,
                block_map,
                str(bug_info["module_name"]),
                str(bug_info["scope_name"]),
                target_lines,
            ):
                continue
            rank = int(item["rank"])
            if group_reaches_boundary and rank != top_n:
                return f"over top-{top_n}", ""
            if group_reaches_boundary:
                return f"top-{top_n}", str(item["sus"])
            return f"top-{format_average_rank(average_rank)}", str(item["sus"])
    return f"over top-{top_n}", ""


def find_reranked_bug_rank(
    bug_info: Mapping[str, Any],
    rankings: Sequence[Mapping[str, Any]],
    line_window: int = 0,
) -> tuple[str, Mapping[str, Any] | None]:
    target_lines = line_set_with_window(bug_info["modify_line"], line_window)
    for item in sorted(rankings, key=lambda value: int(value["reranked_rank"])):
        if str(item.get("module", "")) != str(bug_info["module_name"]):
            continue
        if str(item.get("scope", "")) != str(bug_info["scope_name"]):
            continue
        if {int(line) for line in item.get("lines", [])} & target_lines:
            return f"top-{int(item['reranked_rank'])}", item
    return f"over top-{len(rankings)}", None
