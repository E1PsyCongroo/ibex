#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable, Mapping


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


def read_fuzzing_time(logdir: Path) -> str:
    """
    Read SBFL fuzzing CPU time from:
        <logdir>/fuzzing_time.txt

    The file is produced by Rust with `{fuzzing_elapsed:?}`, so keep the raw
    Duration debug string, for example: `1.234s`, `123ms`, `42µs`, or `0ns`.
    Missing file is allowed and represented as an empty field.
    """
    fuzzing_time_path = logdir / "fuzzing_time.txt"

    if not fuzzing_time_path.is_file():
        return ""

    return read_text(fuzzing_time_path).strip()


def parse_time(s: str) -> float:
    s = s.strip()

    if not s:
        return 0.0

    if s.endswith("ms"):
        return float(s[:-2]) / 1000.0

    if s.endswith("µs"):
        return float(s[:-2]) / 1_000_000.0

    if s.endswith("us"):
        return float(s[:-2]) / 1_000_000.0

    if s.endswith("ns"):
        return float(s[:-2]) / 1_000_000_000.0

    if s.endswith("s"):
        return float(s[:-1])

    return float(s)


def parse_elapsed_time(s: str) -> float:
    s = s.strip()

    if not s:
        return 0.0

    if re.fullmatch(r"\d+:\d{2}:\d{2}:\d{3}", s):
        hours, minutes, seconds, milliseconds = (int(x) for x in s.split(":"))
        return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0

    return parse_time(s)


def parse_rank(rank_str: str) -> float | None:
    """
    Return:
        float : average rank
        None  : Over
    """
    rank_str = rank_str.strip().lower()

    if rank_str.startswith("over"):
        return None

    if rank_str.startswith("top-"):
        rank_str = rank_str[4:]

    try:
        return float(rank_str)
    except Exception:
        return None


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


def sus_group_key(sus: str) -> tuple[str, Decimal | str]:
    """
    Normalize suspiciousness for tie grouping.

    Numeric strings such as "0.10" and "0.100000" are treated as the
    same suspiciousness value. If parsing fails, fall back to exact string.
    """
    sus = str(sus).strip()
    try:
        return "num", Decimal(sus)
    except InvalidOperation:
        return "str", sus


def format_avg_rank(rank: Fraction) -> str:
    """Format averaged rank for the top-k column."""
    if rank.denominator == 1:
        return str(rank.numerator)

    # Most ties produce .5. Keep a compact decimal representation for TSV.
    value = rank.numerator / rank.denominator
    return f"{value:.6f}".rstrip("0").rstrip(".")


def iter_sus_tie_groups(
    ranked_blocks: list[dict[str, Any]],
) -> list[tuple[list[dict[str, Any]], Fraction]]:
    """
    Group consecutive blocks with the same suspiciousness.

    Each returned group carries its average rank. For example, if top-1, top-2
    and top-3 have the same sus, all three are assigned rank (1+2+3)/3 = 2.
    """
    groups: list[tuple[list[dict[str, Any]], Fraction]] = []
    current_group: list[dict[str, Any]] = []
    current_key: tuple[str, Decimal | str] | None = None

    for item in sorted(ranked_blocks, key=lambda x: int(x["rank"])):
        key = sus_group_key(str(item["sus"]))

        if current_group and key != current_key:
            rank_sum = sum(int(x["rank"]) for x in current_group)
            groups.append((current_group, Fraction(rank_sum, len(current_group))))
            current_group = []

        current_group.append(item)
        current_key = key

    if current_group:
        rank_sum = sum(int(x["rank"]) for x in current_group)
        groups.append((current_group, Fraction(rank_sum, len(current_group))))

    return groups


def is_bug_block(
    item: dict[str, Any],
    block_map: dict[tuple[str, int], dict[str, Any]],
    module_name: str,
    scope_name: str,
    target_lines: set[int],
) -> bool:
    scope = str(item["scope"])
    bid = int(item["bid"])

    block = block_map.get((scope, bid))
    if block is None:
        return False

    if str(block.get("scope", "")) != scope_name:
        return False

    if str(block.get("module", "")) != module_name:
        return False

    block_lines = {int(x) for x in block.get("lines", [])}
    return bool(block_lines & target_lines)


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

    Tie rule:
        Blocks with the same suspiciousness share the average of their printed
        ranks. For example, top-1 and top-2 with the same sus are both top-1.5.

    Degenerate rule:
        If all printed n blocks have the same suspiciousness and there are only
        these n blocks in the ranking, treat the result as "over top-n".

    Boundary tie rule:
        If a bug block has the same suspiciousness as top-n, but the bug block
        itself is not the printed top-n item, treat it as "over top-n".

        Example:
            top-49 sus=0.1
            top-50 sus=0.1

        If the bug block is top-49, return:
            over top-50

        If the bug block is exactly top-50, return:
            top-50
    """
    module_name = str(bug_info["module_name"])
    scope_name = str(bug_info["scope_name"])
    modify_lines = [int(x) for x in bug_info["modify_line"]]

    target_lines = line_set_with_window(modify_lines, line_window)
    top_n = len(ranked_blocks)

    tie_groups = iter_sus_tie_groups(ranked_blocks)

    # If all n printed blocks have exactly the same suspiciousness, this ranking
    # carries no useful ordering information, so do not count it as top-(n+1)/2.
    if top_n > 0 and len(tie_groups) == 1:
        return f"over top-{top_n}", ""

    # Suspiciousness of the printed boundary item: top-n.
    #
    # If a tie reaches this boundary, then any earlier item in the same tie group
    # may actually be tied with unseen items beyond top-n. Therefore, those items
    # should not be counted as top-k.
    top_n_item = max(ranked_blocks, key=lambda item: int(item["rank"]))
    top_n_sus_key = sus_group_key(str(top_n_item["sus"]))

    for group, avg_rank in tie_groups:
        group_sus_key = sus_group_key(str(group[0]["sus"]))
        group_has_top_n_sus = group_sus_key == top_n_sus_key

        for item in group:
            if not is_bug_block(
                item=item,
                block_map=block_map,
                module_name=module_name,
                scope_name=scope_name,
                target_lines=target_lines,
            ):
                continue

            rank = int(item["rank"])
            sus = str(item["sus"])

            # New rule:
            # If this block has the same sus as top-n, but it is not the printed
            # top-n item, treat it as over top-n.
            if group_has_top_n_sus and rank != top_n:
                return f"over top-{top_n}", ""

            # If the bug block is exactly the printed top-n item, keep it as top-n.
            # Do not average it with previous tied boundary items.
            if group_has_top_n_sus and rank == top_n:
                return f"top-{top_n}", sus

            return f"top-{format_avg_rank(avg_rank)}", sus

    return f"over top-{top_n}", ""


def iter_status_dirs(logs_root: Path):
    for status_path in sorted(logs_root.rglob("status.txt")):
        yield status_path.parent, status_path


def error_row(
    bugset: str,
    diff: str,
    status: str = "ERROR(-1)",
    elapsed_time: str = "",
    fuzzing_time: str = "",
) -> dict[str, str]:
    return {
        "bugset": bugset,
        "diff": diff,
        "status": status,
        "top-k": "",
        "sus": "",
        "elapsed_time": elapsed_time,
        "fuzzing_time": fuzzing_time,
    }


def compute_summary_stats(rows: Iterable[Mapping[str, str]]) -> dict[str, float | int]:
    top1 = 0
    top5 = 0
    top10 = 0
    top20 = 0

    mar_sum = 0.0
    mar_cnt = 0

    elapsed_sum = 0.0
    fuzzing_sum = 0.0
    time_cnt = 0

    ok_cnt = 0

    for row in rows:
        if row.get("status") != "OK":
            continue

        ok_cnt += 1

        rank = parse_rank(row.get("top-k", row.get("top", "")))

        if rank is not None:
            if rank <= 1:
                top1 += 1
            if rank <= 5:
                top5 += 1
            if rank <= 10:
                top10 += 1
            if rank <= 20:
                top20 += 1

            mar_sum += rank if rank <= 10 else 11
        else:
            mar_sum += 11

        mar_cnt += 1

        if row.get("elapsed_time", ""):
            elapsed_sum += parse_elapsed_time(row["elapsed_time"])

        if row.get("fuzzing_time", ""):
            fuzzing_sum += parse_time(row["fuzzing_time"])

        time_cnt += 1

    return {
        "ok_cnt": ok_cnt,
        "top1": top1,
        "top5": top5,
        "top10": top10,
        "top20": top20,
        "mar_sum": mar_sum,
        "mar_cnt": mar_cnt,
        "elapsed_sum": elapsed_sum,
        "fuzzing_sum": fuzzing_sum,
        "time_cnt": time_cnt,
    }


def print_summary_stats(rows: Iterable[Mapping[str, str]]) -> None:
    stats = compute_summary_stats(rows)

    print(f"OK              : {stats['ok_cnt']}")
    print(f"Top-1           : {stats['top1']}")
    print(f"Top-5           : {stats['top5']}")
    print(f"Top-10          : {stats['top10']}")
    print(f"Top-20          : {stats['top20']}")

    mar_cnt = int(stats["mar_cnt"])
    if mar_cnt:
        print(f"MAR@10          : {float(stats['mar_sum']) / mar_cnt:.3f}")
    else:
        print("MAR@10          : n/a")

    time_cnt = int(stats["time_cnt"])
    if time_cnt:
        print(f"Average elapsed : {float(stats['elapsed_sum']) / time_cnt:.2f} s")
        print(f"Average fuzzing : {float(stats['fuzzing_sum']) / time_cnt:.2f} s")


def print_summary_stats_from_file(summary_path: Path) -> None:
    with summary_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        print_summary_stats(reader)


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
    fuzzing_time = read_fuzzing_time(logdir)

    resolved = resolve_bugcase_ref(bugset_root, bugcase)
    if resolved is None:
        return error_row(bugcase, "", "ERROR(-1)", elapsed_time, fuzzing_time)

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
            "fuzzing_time": fuzzing_time,
        }

    # OK cases:
    # load bugset_root/<case>/bug_info.json.
    bug_info = load_bug_info_from_case_dir(case_dir)
    if bug_info is None:
        return error_row(bugset_name, diff_name, "ERROR(-1)", elapsed_time, fuzzing_time)

    result_log_path = logdir / "result.log"
    blocks_path = logdir / "blocks.json"

    if not result_log_path.is_file():
        print(f"[WARN] missing result.log: {result_log_path}", file=sys.stderr)
        return error_row(bugset_name, diff_name, "ERROR(-1)", elapsed_time, fuzzing_time)

    if not blocks_path.is_file():
        print(f"[WARN] missing blocks.json: {blocks_path}", file=sys.stderr)
        return error_row(bugset_name, diff_name, "ERROR(-1)", elapsed_time, fuzzing_time)

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
            "fuzzing_time": fuzzing_time,
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
        "fuzzing_time": fuzzing_time,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bugset_root", nargs="?", help="bugset directory, e.g. bugset")
    parser.add_argument("logs_root", nargs="?", help="logs directory, e.g. logs")
    parser.add_argument("-o", "--output", default="sbfl_block_summary.tsv")
    parser.add_argument(
        "--stats-only",
        type=Path,
        help="print Top-k/MAR/time stats for an existing summary TSV and exit",
    )
    parser.add_argument(
        "--line-window",
        type=int,
        default=0,
        help="line matching window. 0 means exact line match; 1 means +/-1 line.",
    )
    args = parser.parse_args()

    if args.stats_only is not None:
        print_summary_stats_from_file(args.stats_only)
        return 0

    if args.bugset_root is None or args.logs_root is None:
        parser.error("bugset_root and logs_root are required unless --stats-only is used")

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
                f"elapsed={row['elapsed_time']}, "
                f"fuzzing_time={row['fuzzing_time']}"
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
                "fuzzing_time",
            ],
            delimiter="\t",
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"[DONE] wrote {len(rows)} rows to {output_path}")
    print_summary_stats(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
