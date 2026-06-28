#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path
from typing import Any


BLOCK_RANK_RE = re.compile(
    r"^top-(?P<rank>\d+):\s+"
    r"Block\(scope:\s+(?P<scope>.*),\s+bid:\s+(?P<bid>\d+)\)\s+"
    r"with suspicious\s+'(?P<sus>[^']+)'"
)

# Examples:
#   [OK] 0/ibex_decoder.sv.diff, status=0, elapsed=00:00:01:234
#   [BUILD_FAIL] 0/ibex_decoder.sv.diff, status=1, elapsed=00:00:05:678
#   [SBFL_FAIL] 0/ibex_decoder.sv.diff, status=101
#   [ERROR] 0/ibex_decoder.sv.diff, status=1
#
# Also compatible with:
#   [BUILD FAIL] 0/ibex_decoder.sv.diff, status=1
STATUS_RE = re.compile(
    r"^\[(?P<kind>[A-Z_ ]+)\]\s+"
    r"(?P<bugcase>[^,\s]+)"
    r"(?:,\s*status=(?P<status>-?\d+))?"
    r"(?:,\s*elapsed=(?P<elapsed>\d+:\d{2}:\d{2}:\d{3}))?"
    r"(?:,\s*elapsed_ms=(?P<elapsed_ms>\d+))?"
    r"\s*$"
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def normalize_status_kind(kind: str) -> str:
    return kind.strip().replace(" ", "_")


def format_elapsed_ms(total_ms: int) -> str:
    hours, remainder = divmod(total_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}:{milliseconds:03d}"


def parse_status(status_path: Path) -> tuple[str, bool, str, str] | None:
    """
    Return:
        (bugcase, True,  "OK",             elapsed_time)
        (bugcase, False, "BUILD_FAIL(1)",  elapsed_time)
        (bugcase, False, "SBFL_FAIL(101)", elapsed_time)
        (bugcase, False, "ERROR(1)",       elapsed_time)

    bugcase example:
        0/ibex_decoder.sv.diff
    """
    text = read_text(status_path).strip()

    m = STATUS_RE.match(text)
    if not m:
        print(f"[WARN] unrecognized status format: {status_path}: {text!r}", file=sys.stderr)
        return None

    kind = normalize_status_kind(m.group("kind"))
    bugcase = m.group("bugcase")
    status = m.group("status")
    elapsed_time = m.group("elapsed")
    elapsed_ms = m.group("elapsed_ms")
    if elapsed_time is None and elapsed_ms is not None:
        elapsed_time = format_elapsed_ms(int(elapsed_ms))
    elapsed_time = elapsed_time or ""

    if kind == "OK":
        return bugcase, True, "OK", elapsed_time

    if status is None:
        print(
            f"[WARN] fail status has no numeric status, use -1: {status_path}: {text!r}",
            file=sys.stderr,
        )
        status = "-1"

    return bugcase, False, f"{kind}({status})", elapsed_time


def resolve_bugcase_ref(
    bugset_root: Path,
    bugcase: str,
) -> tuple[str, str, Path, Path] | None:
    """
    For status line:
        [OK] 0/ibex_decoder.sv.diff, status=0

    Resolve to:
        bugset_name = "0"
        diff_name   = "ibex_decoder.sv.diff"
        case_dir    = bugset_root / "0"
        diff_path   = bugset_root / "0" / "ibex_decoder.sv.diff"

    Return:
        (bugset_name, diff_name, case_dir, diff_path)
    """
    bugcase_path = Path(bugcase)

    if not bugcase_path.name.endswith(".sv.diff"):
        print(
            f"[WARN] bugcase does not end with .sv.diff: {bugcase!r}",
            file=sys.stderr,
        )
        return None

    rel_case_dir = bugcase_path.parent
    diff_name = bugcase_path.name

    if str(rel_case_dir) == ".":
        print(
            f"[WARN] bugcase has no parent directory: {bugcase!r}",
            file=sys.stderr,
        )
        return None

    case_dir = bugset_root / rel_case_dir
    diff_path = case_dir / diff_name

    if not case_dir.is_dir():
        print(f"[WARN] missing case dir: {case_dir}", file=sys.stderr)
        return None

    if not diff_path.is_file():
        print(f"[WARN] missing diff file: {diff_path}", file=sys.stderr)
        return None

    bugset_name = str(rel_case_dir)

    return bugset_name, diff_name, case_dir, diff_path


def load_bug_info_from_case_dir(case_dir: Path) -> dict[str, Any] | None:
    bug_info_path = case_dir / "bug_info.json"

    if not bug_info_path.is_file():
        print(f"[WARN] missing bug_info.json: {bug_info_path}", file=sys.stderr)
        return None

    with bug_info_path.open("r", encoding="utf-8") as f:
        info = json.load(f)

    for key in ("module_name", "scope_name", "modify_line"):
        if key not in info:
            print(f"[WARN] missing key {key!r} in {bug_info_path}", file=sys.stderr)
            return None

    return info


def load_blocks(blocks_path: Path) -> dict[tuple[str, int], dict[str, Any]]:
    """
    Build mapping:
        (scope, bid) -> block_info
    """
    with blocks_path.open("r", encoding="utf-8") as f:
        blocks = json.load(f)

    block_map: dict[tuple[str, int], dict[str, Any]] = {}

    for block in blocks:
        try:
            scope = str(block["scope"])
            bid = int(block["bid"])
        except KeyError:
            continue

        block_map[(scope, bid)] = block

    return block_map


def parse_block_suspiciousness(result_log_path: Path) -> list[dict[str, Any]]:
    """
    Parse section:

        Suspiciousness of block:
        top-1: Block(scope: ..., bid: 74) with suspicious '0.316228'
    """
    ranked: list[dict[str, Any]] = []
    in_block_section = False

    for raw in read_text(result_log_path).splitlines():
        line = raw.strip()

        if line == "Suspiciousness of block:":
            in_block_section = True
            continue

        if not in_block_section:
            continue

        if line.startswith("Suspiciousness of ") and line != "Suspiciousness of block:":
            break

        m = BLOCK_RANK_RE.match(line)
        if not m:
            continue

        ranked.append(
            {
                "rank": int(m.group("rank")),
                "scope": m.group("scope"),
                "bid": int(m.group("bid")),
                "sus": m.group("sus"),
            }
        )

    return ranked


def line_set_with_window(lines: list[int], window: int) -> set[int]:
    """
    window = 0: exact match only
    window = 1: line-1, line, line+1
    """
    result: set[int] = set()

    for line in lines:
        line = int(line)
        for x in range(line - window, line + window + 1):
            result.add(x)

    return result


def find_bug_rank(
    bug_info: dict[str, Any],
    ranked_blocks: list[dict[str, Any]],
    block_map: dict[tuple[str, int], dict[str, Any]],
    line_window: int = 0,
) -> tuple[str, str]:
    """
    Return:
        ("top-k", sus)
    or:
        ("over top-n", "")
    """
    module_name = str(bug_info["module_name"])
    scope_name = str(bug_info["scope_name"])
    modify_lines = [int(x) for x in bug_info["modify_line"]]

    target_lines = line_set_with_window(modify_lines, line_window)

    for item in ranked_blocks:
        rank = int(item["rank"])
        scope = str(item["scope"])
        bid = int(item["bid"])
        sus = str(item["sus"])

        block = block_map.get((scope, bid))
        if block is None:
            continue

        if str(block.get("scope", "")) != scope_name:
            continue

        if str(block.get("module", "")) != module_name:
            continue

        block_lines = {int(x) for x in block.get("lines", [])}

        if block_lines & target_lines:
            return f"top-{rank}", sus

    top_n = len(ranked_blocks)
    return f"over top-{top_n}", ""


def iter_status_dirs(logs_root: Path):
    for status_path in sorted(logs_root.rglob("status.txt")):
        yield status_path.parent, status_path


def error_row(
    bugset: str,
    diff: str,
    status: str = "ERROR(-1)",
    elapsed_time: str = "",
) -> dict[str, str]:
    return {
        "bugset": bugset,
        "diff": diff,
        "status": status,
        "top-k": "",
        "sus": "",
        "elapsed_time": elapsed_time,
    }


def process_one_logdir(
    bugset_root: Path,
    logdir: Path,
    status_path: Path,
    line_window: int,
) -> dict[str, str] | None:
    parsed = parse_status(status_path)

    if parsed is None:
        return None

    bugcase, is_ok, csv_status, elapsed_time = parsed

    resolved = resolve_bugcase_ref(bugset_root, bugcase)
    if resolved is None:
        return error_row(bugcase, "", "ERROR(-1)", elapsed_time)

    bugset_name, diff_name, case_dir, _diff_path = resolved

    # Build/SBFL/ERROR failed:
    # only record status from status.txt, but still verify:
    #   bugset_root/<case>/xxx.sv.diff exists.
    if not is_ok:
        return {
            "bugset": bugset_name,
            "diff": diff_name,
            "status": csv_status,
            "top-k": "",
            "sus": "",
            "elapsed_time": elapsed_time,
        }

    # OK cases:
    # load bugset_root/<case>/bug_info.json.
    bug_info = load_bug_info_from_case_dir(case_dir)
    if bug_info is None:
        return error_row(bugset_name, diff_name, "ERROR(-1)", elapsed_time)

    result_log_path = logdir / "result.log"
    blocks_path = logdir / "blocks.json"

    if not result_log_path.is_file():
        print(f"[WARN] missing result.log: {result_log_path}", file=sys.stderr)
        return error_row(bugset_name, diff_name, "ERROR(-1)", elapsed_time)

    if not blocks_path.is_file():
        print(f"[WARN] missing blocks.json: {blocks_path}", file=sys.stderr)
        return error_row(bugset_name, diff_name, "ERROR(-1)", elapsed_time)

    ranked_blocks = parse_block_suspiciousness(result_log_path)

    if not ranked_blocks:
        print(f"[WARN] no block suspiciousness found in {result_log_path}", file=sys.stderr)
        return {
            "bugset": bugset_name,
            "diff": diff_name,
            "status": "OK",
            "top-k": "over top-0",
            "sus": "",
            "elapsed_time": elapsed_time,
        }

    block_map = load_blocks(blocks_path)

    top_k, sus = find_bug_rank(
        bug_info=bug_info,
        ranked_blocks=ranked_blocks,
        block_map=block_map,
        line_window=line_window,
    )

    return {
        "bugset": bugset_name,
        "diff": diff_name,
        "status": "OK",
        "top-k": top_k,
        "sus": sus,
        "elapsed_time": elapsed_time,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bugset_root", help="bugset directory, e.g. bugset")
    parser.add_argument("logs_root", help="logs directory, e.g. logs")
    parser.add_argument("-o", "--output", default="sbfl_block_summary.tsv")
    parser.add_argument(
        "--line-window",
        type=int,
        default=0,
        help="line matching window. 0 means exact line match; 1 means +/-1 line.",
    )
    args = parser.parse_args()

    bugset_root = Path(args.bugset_root).resolve()
    logs_root = Path(args.logs_root).resolve()
    output_path = Path(args.output)

    if not bugset_root.is_dir():
        print(f"[ERROR] bugset root not found: {bugset_root}", file=sys.stderr)
        return 1

    if not logs_root.is_dir():
        print(f"[ERROR] logs root not found: {logs_root}", file=sys.stderr)
        return 1

    rows: list[dict[str, str]] = []

    for logdir, status_path in iter_status_dirs(logs_root):
        row = process_one_logdir(
            bugset_root=bugset_root,
            logdir=logdir,
            status_path=status_path,
            line_window=args.line_window,
        )

        if row is not None:
            rows.append(row)
            print(
                f"[ROW] {logdir}: "
                f"{row['bugset']}/{row['diff']}, "
                f"status={row['status']}, "
                f"{row['top-k']}, {row['sus']}, "
                f"elapsed={row['elapsed_time']}"
            )

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "bugset",
                "diff",
                "status",
                "top-k",
                "sus",
                "elapsed_time",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"[DONE] wrote {len(rows)} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
