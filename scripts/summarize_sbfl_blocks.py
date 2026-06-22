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

STATUS_RE = re.compile(
    r"^\[(?P<kind>OK|BUILD FAIL|SBFL FAIL|ERROR)\]\s+"
    r"(?P<bugset>[^,\s]+)"
    r"(?:,\s*status=(?P<status>-?\d+))?"
    r"\s*$"
)


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def parse_status(status_path: Path) -> tuple[str, bool, str] | None:
    """
    Return:
        (bugset, True,  "OK")
        (bugset, False, "BUILD FAIL(1)")
        (bugset, False, "SBFL FAIL(101)")
        (bugset, False, "ERROR(1)")
    """
    text = read_text(status_path).strip()

    m = STATUS_RE.match(text)
    if not m:
        print(f"[WARN] unrecognized status format: {status_path}: {text!r}", file=sys.stderr)
        return None

    kind = m.group("kind")
    bugset = m.group("bugset")
    status = m.group("status")

    if kind == "OK":
        return bugset, True, "OK"

    if status is None:
        print(
            f"[WARN] fail status has no numeric status, use -1: {status_path}: {text!r}",
            file=sys.stderr,
        )
        status = "-1"

    return bugset, False, f"{kind}({status})"


def load_bug_info(dataset_root: Path, bugset: str) -> dict[str, Any] | None:
    bug_info_path = dataset_root / bugset / "bug_info.json"

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

        # If another section starts after block ranking, stop.
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


def process_one_logdir(
    dataset_root: Path,
    logdir: Path,
    status_path: Path,
    line_window: int,
) -> dict[str, str] | None:
    parsed = parse_status(status_path)

    if parsed is None:
        return None

    bugset, is_ok, csv_status = parsed

    # Build/SBFL/ERROR failed: only record status from status.txt.
    if not is_ok:
        return {
            "bugset": bugset,
            "status": csv_status,
            "top-k": "",
            "sus": "",
        }

    bug_info = load_bug_info(dataset_root, bugset)
    if bug_info is None:
        return {
            "bugset": bugset,
            "status": "ERROR(-1)",
            "top-k": "",
            "sus": "",
        }

    result_log_path = logdir / "result.log"
    blocks_path = logdir / "blocks.json"

    if not result_log_path.is_file():
        print(f"[WARN] missing result.log: {result_log_path}", file=sys.stderr)
        return {
            "bugset": bugset,
            "status": "ERROR(-1)",
            "top-k": "",
            "sus": "",
        }

    if not blocks_path.is_file():
        print(f"[WARN] missing blocks.json: {blocks_path}", file=sys.stderr)
        return {
            "bugset": bugset,
            "status": "ERROR(-1)",
            "top-k": "",
            "sus": "",
        }

    ranked_blocks = parse_block_suspiciousness(result_log_path)

    if not ranked_blocks:
        print(f"[WARN] no block suspiciousness found in {result_log_path}", file=sys.stderr)
        return {
            "bugset": bugset,
            "status": "OK",
            "top-k": "over top-0",
            "sus": "",
        }

    block_map = load_blocks(blocks_path)

    top_k, sus = find_bug_rank(
        bug_info=bug_info,
        ranked_blocks=ranked_blocks,
        block_map=block_map,
        line_window=line_window,
    )

    return {
        "bugset": bugset,
        "status": "OK",
        "top-k": top_k,
        "sus": sus,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_root", help="dataset directory, e.g. dataset")
    parser.add_argument("logs_root", help="logs directory, e.g. logs")
    parser.add_argument("-o", "--output", default="sbfl_block_summary.csv")
    parser.add_argument(
        "--line-window",
        type=int,
        default=0,
        help="line matching window. 0 means exact line match; 1 means +/-1 line.",
    )
    args = parser.parse_args()

    dataset_root = Path(args.dataset_root)
    logs_root = Path(args.logs_root)
    output_path = Path(args.output)

    if not dataset_root.is_dir():
        print(f"[ERROR] dataset root not found: {dataset_root}", file=sys.stderr)
        return 1

    if not logs_root.is_dir():
        print(f"[ERROR] logs root not found: {logs_root}", file=sys.stderr)
        return 1

    rows: list[dict[str, str]] = []

    for logdir, status_path in iter_status_dirs(logs_root):
        row = process_one_logdir(
            dataset_root=dataset_root,
            logdir=logdir,
            status_path=status_path,
            line_window=args.line_window,
        )

        if row is not None:
            rows.append(row)
            print(
                f"[ROW] {logdir}: "
                f"{row['bugset']}, status={row['status']}, "
                f"{row['top-k']}, {row['sus']}"
            )

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["bugset", "status", "top-k", "sus"])
        writer.writeheader()
        writer.writerows(rows)

    print(f"[DONE] wrote {len(rows)} rows to {output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
