#!/usr/bin/env python3
"""Summarize fault-localization ranks from llm_rerank.json files."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from summarize_sbfl_blocks import (
    find_bug_rank,
    load_blocks,
    load_bug_info_from_case_dir,
    parse_block_suspiciousness,
    parse_rank,
    parse_status,
    parse_time,
    read_elpased_time,
    read_fuzzing_time,
    read_text,
    resolve_bugcase_ref,
)


FIELDNAMES = [
    "bugset",
    "diff",
    "status",
    "model",
    "source_mode",
    "candidate_count",
    "rerank_top_k",
    "top-k",
    "sbfl_top-k",
    "rank_delta",
    "candidate_id",
    "original_rank",
    "sus",
    "module",
    "scope",
    "bid",
    "lines",
    "reason",
    "llm_elapsed_time",
    "elapsed_time",
    "fuzzing_time",
]


class LlmSummaryError(RuntimeError):
    """An invalid llm_rerank.json input."""


def blank_row(
    bugset: str,
    diff: str,
    status: str,
    *,
    elapsed_time: str = "",
    fuzzing_time: str = "",
) -> dict[str, str]:
    row = {field: "" for field in FIELDNAMES}
    row.update(
        {
            "bugset": bugset,
            "diff": diff,
            "status": status,
            "elapsed_time": elapsed_time,
            "fuzzing_time": fuzzing_time,
        }
    )
    return row


def load_llm_rerank(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(read_text(path))
    except json.JSONDecodeError as exc:
        raise LlmSummaryError(f"invalid JSON in {path}: {exc}") from exc

    if not isinstance(value, dict):
        raise LlmSummaryError(f"{path} must contain a JSON object")
    rankings = value.get("rankings")
    if not isinstance(rankings, list) or not rankings:
        raise LlmSummaryError(f"{path} must contain a non-empty rankings array")

    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    seen_ranks: set[int] = set()
    required = ("candidate_id", "module", "scope", "bid", "lines", "reranked_rank")
    for index, raw_item in enumerate(rankings, 1):
        if not isinstance(raw_item, dict):
            raise LlmSummaryError(f"ranking {index} in {path} is not an object")
        missing = [field for field in required if field not in raw_item]
        if missing:
            raise LlmSummaryError(
                f"ranking {index} in {path} is missing: {', '.join(missing)}"
            )
        if not isinstance(raw_item["lines"], list):
            raise LlmSummaryError(f"ranking {index} in {path} has non-array lines")
        try:
            reranked_rank = int(raw_item["reranked_rank"])
            bid = int(raw_item["bid"])
            lines = sorted({int(line) for line in raw_item["lines"]})
        except (TypeError, ValueError) as exc:
            raise LlmSummaryError(f"ranking {index} in {path} has invalid numbers") from exc

        candidate_id = str(raw_item["candidate_id"])
        if not candidate_id:
            raise LlmSummaryError(f"ranking {index} in {path} has an empty candidate_id")
        if candidate_id in seen_ids:
            raise LlmSummaryError(f"duplicate candidate_id {candidate_id!r} in {path}")
        if reranked_rank in seen_ranks:
            raise LlmSummaryError(f"duplicate reranked_rank {reranked_rank} in {path}")
        seen_ids.add(candidate_id)
        seen_ranks.add(reranked_rank)

        item = dict(raw_item)
        item.update(
            {
                "candidate_id": candidate_id,
                "module": str(raw_item["module"]),
                "scope": str(raw_item["scope"]),
                "bid": bid,
                "lines": lines,
                "reranked_rank": reranked_rank,
            }
        )
        normalized.append(item)

    normalized.sort(key=lambda item: item["reranked_rank"])
    expected_ranks = list(range(1, len(normalized) + 1))
    actual_ranks = [item["reranked_rank"] for item in normalized]
    if actual_ranks != expected_ranks:
        raise LlmSummaryError(
            f"reranked_rank values in {path} must be contiguous 1..{len(normalized)}"
        )

    declared_top_k = value.get("top_k", len(normalized))
    try:
        declared_top_k = int(declared_top_k)
    except (TypeError, ValueError) as exc:
        raise LlmSummaryError(f"top_k in {path} is not an integer") from exc
    if declared_top_k != len(normalized):
        raise LlmSummaryError(
            f"top_k={declared_top_k} in {path}, but rankings has {len(normalized)} items"
        )

    value["rankings"] = normalized
    value["top_k"] = declared_top_k
    return value


def target_lines_with_window(lines: Iterable[Any], window: int) -> set[int]:
    result: set[int] = set()
    for raw_line in lines:
        line = int(raw_line)
        result.update(range(line - window, line + window + 1))
    return result


def find_llm_bug_rank(
    bug_info: Mapping[str, Any],
    rankings: Sequence[Mapping[str, Any]],
    line_window: int = 0,
) -> tuple[str, Mapping[str, Any] | None]:
    module_name = str(bug_info["module_name"])
    scope_name = str(bug_info["scope_name"])
    target_lines = target_lines_with_window(bug_info["modify_line"], line_window)

    for item in sorted(rankings, key=lambda value: int(value["reranked_rank"])):
        if str(item["module"]) != module_name or str(item["scope"]) != scope_name:
            continue
        block_lines = {int(line) for line in item["lines"]}
        if block_lines & target_lines:
            return f"top-{int(item['reranked_rank'])}", item
    return f"over top-{len(rankings)}", None


def normalize_reason(value: Any) -> str:
    """Keep one TSV record per physical line."""
    return " ".join(str(value or "").split())


def format_rank_delta(sbfl_top: str, llm_top: str) -> str:
    sbfl_rank = parse_rank(sbfl_top)
    llm_rank = parse_rank(llm_top)
    if sbfl_rank is None or llm_rank is None:
        return ""
    delta = sbfl_rank - llm_rank
    return f"{delta:.6f}".rstrip("0").rstrip(".")


def compute_sbfl_rank(
    logdir: Path,
    bug_info: dict[str, Any],
    line_window: int,
) -> tuple[str, str]:
    result_log = logdir / "result.log"
    blocks_json = logdir / "blocks.json"
    if not result_log.is_file() or not blocks_json.is_file():
        return "", ""
    ranked_blocks = parse_block_suspiciousness(result_log)
    if not ranked_blocks:
        return "over top-0", ""
    return find_bug_rank(
        bug_info=bug_info,
        ranked_blocks=ranked_blocks,
        block_map=load_blocks(blocks_json),
        line_window=line_window,
    )


def process_one_logdir(
    bugset_root: Path,
    logdir: Path,
    status_path: Path,
    line_window: int,
    rerank_filename: str,
) -> dict[str, str] | None:
    parsed = parse_status(status_path)
    if parsed is None:
        return None

    bugcase, is_ok, csv_status = parsed
    elapsed_time = read_elpased_time(logdir)
    fuzzing_time = read_fuzzing_time(logdir)
    resolved = resolve_bugcase_ref(bugset_root, bugcase)
    if resolved is None:
        return blank_row(
            bugcase,
            "",
            "ERROR(-1)",
            elapsed_time=elapsed_time,
            fuzzing_time=fuzzing_time,
        )
    bugset_name, diff_name, case_dir, _diff_path = resolved

    if not is_ok:
        return blank_row(
            bugset_name,
            diff_name,
            csv_status,
            elapsed_time=elapsed_time,
            fuzzing_time=fuzzing_time,
        )

    bug_info = load_bug_info_from_case_dir(case_dir)
    if bug_info is None:
        return blank_row(
            bugset_name,
            diff_name,
            "ERROR(-1)",
            elapsed_time=elapsed_time,
            fuzzing_time=fuzzing_time,
        )

    sbfl_top, _sbfl_sus = compute_sbfl_rank(logdir, bug_info, line_window)
    rerank_path = logdir / rerank_filename
    if not rerank_path.is_file():
        row = blank_row(
            bugset_name,
            diff_name,
            "LLM_MISSING",
            elapsed_time=elapsed_time,
            fuzzing_time=fuzzing_time,
        )
        row["sbfl_top-k"] = sbfl_top
        return row

    try:
        rerank = load_llm_rerank(rerank_path)
    except (OSError, LlmSummaryError) as exc:
        print(f"[WARN] {exc}", file=sys.stderr)
        row = blank_row(
            bugset_name,
            diff_name,
            "LLM_ERROR",
            elapsed_time=elapsed_time,
            fuzzing_time=fuzzing_time,
        )
        row["sbfl_top-k"] = sbfl_top
        return row

    llm_top, matched = find_llm_bug_rank(bug_info, rerank["rankings"], line_window)
    row = blank_row(
        bugset_name,
        diff_name,
        "OK",
        elapsed_time=elapsed_time,
        fuzzing_time=fuzzing_time,
    )
    row.update(
        {
            "model": str(rerank.get("model", "")),
            "source_mode": str(rerank.get("source_mode", "")),
            "candidate_count": str(rerank.get("candidate_count", "")),
            "rerank_top_k": str(rerank["top_k"]),
            "top-k": llm_top,
            "sbfl_top-k": sbfl_top,
            "rank_delta": format_rank_delta(sbfl_top, llm_top),
            "llm_elapsed_time": str(rerank.get("llm_elapsed_seconds", "")),
        }
    )
    if matched is not None:
        original_rank = matched.get("original_rank", "")
        row.update(
            {
                "candidate_id": str(matched.get("candidate_id", "")),
                "original_rank": str(original_rank),
                "sus": str(matched.get("suspiciousness", "")),
                "module": str(matched.get("module", "")),
                "scope": str(matched.get("scope", "")),
                "bid": str(matched.get("bid", "")),
                "lines": ",".join(str(line) for line in matched.get("lines", [])),
                "reason": normalize_reason(matched.get("reason", "")),
            }
        )
    return row


def compute_summary_stats(rows: Iterable[Mapping[str, str]]) -> dict[str, Any]:
    row_list = list(rows)
    ok_rows = [row for row in row_list if row.get("status") == "OK"]
    top1 = top5 = top10 = 0
    reciprocal_rank_sum = 0.0
    mar10_sum = 0.0
    improved = unchanged = worsened = 0
    elapsed_values: list[float] = []
    fuzzing_values: list[float] = []
    llm_elapsed_values: list[float] = []

    for row in ok_rows:
        rank = parse_rank(row.get("top-k", ""))
        if rank is not None:
            top1 += rank <= 1
            top5 += rank <= 5
            top10 += rank <= 10
            reciprocal_rank_sum += 1.0 / rank
            mar10_sum += min(rank, 11)
        else:
            mar10_sum += 11

        sbfl_rank = parse_rank(row.get("sbfl_top-k", ""))
        if rank is None and sbfl_rank is not None:
            worsened += 1
        elif rank is not None and sbfl_rank is None:
            improved += 1
        elif rank is not None and sbfl_rank is not None:
            if rank < sbfl_rank:
                improved += 1
            elif rank > sbfl_rank:
                worsened += 1
            else:
                unchanged += 1

        for field, output in (
            ("elapsed_time", elapsed_values),
            ("fuzzing_time", fuzzing_values),
            ("llm_elapsed_time", llm_elapsed_values),
        ):
            value = row.get(field, "")
            if value:
                output.append(parse_time(value))

    return {
        "total": len(row_list),
        "ok": len(ok_rows),
        "status_counts": Counter(row.get("status", "") for row in row_list),
        "model_counts": Counter(row.get("model", "") for row in ok_rows),
        "top1": top1,
        "top5": top5,
        "top10": top10,
        "mrr": reciprocal_rank_sum / len(ok_rows) if ok_rows else None,
        "mar10": mar10_sum / len(ok_rows) if ok_rows else None,
        "improved": improved,
        "unchanged": unchanged,
        "worsened": worsened,
        "average_elapsed": sum(elapsed_values) / len(elapsed_values) if elapsed_values else None,
        "average_fuzzing": sum(fuzzing_values) / len(fuzzing_values) if fuzzing_values else None,
        "average_llm": (
            sum(llm_elapsed_values) / len(llm_elapsed_values) if llm_elapsed_values else None
        ),
    }


def print_optional_seconds(label: str, value: float | None) -> None:
    print(f"{label:<20}: {value:.2f} s" if value is not None else f"{label:<20}: n/a")


def print_summary_stats(rows: Iterable[Mapping[str, str]]) -> None:
    stats = compute_summary_stats(rows)
    print(f"{'Total rows':<20}: {stats['total']}")
    print(f"{'Valid LLM results':<20}: {stats['ok']}")
    for status, count in sorted(stats["status_counts"].items()):
        if status != "OK":
            print(f"  {status:<18}: {count}")
    models = ", ".join(
        f"{model or '(unknown)'}={count}" for model, count in sorted(stats["model_counts"].items())
    )
    print(f"{'Models':<20}: {models or 'n/a'}")
    print(f"{'Top-1':<20}: {stats['top1']}")
    print(f"{'Top-5':<20}: {stats['top5']}")
    print(f"{'Top-10':<20}: {stats['top10']}")
    print(f"{'MRR':<20}: {stats['mrr']:.3f}" if stats["mrr"] is not None else f"{'MRR':<20}: n/a")
    print(
        f"{'MAR@10':<20}: {stats['mar10']:.3f}"
        if stats["mar10"] is not None
        else f"{'MAR@10':<20}: n/a"
    )
    print(f"{'Improved':<20}: {stats['improved']}")
    print(f"{'Unchanged':<20}: {stats['unchanged']}")
    print(f"{'Worsened':<20}: {stats['worsened']}")
    print_optional_seconds("Average LLM", stats["average_llm"])
    print_optional_seconds("Average SBFL", stats["average_elapsed"])
    print_optional_seconds("Average fuzzing", stats["average_fuzzing"])


def read_summary(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def iter_status_dirs(logs_root: Path):
    for status_path in sorted(logs_root.rglob("status.txt")):
        yield status_path.parent, status_path


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Summarize llm_rerank.json fault-localization results."
    )
    parser.add_argument("bugset_root", nargs="?", help="bugset directory, e.g. verify_dataset")
    parser.add_argument("logs_root", nargs="?", help="logs directory, e.g. logs/reduce")
    parser.add_argument("-o", "--output", default="llm_rerank_summary.tsv")
    parser.add_argument(
        "--rerank-filename",
        default="llm_rerank.json",
        help="rerank filename searched beside status.txt (default: %(default)s)",
    )
    parser.add_argument(
        "--stats-only",
        type=Path,
        help="print statistics from an existing summary TSV and exit",
    )
    parser.add_argument(
        "--line-window",
        type=int,
        default=0,
        help="bug-line matching window; 1 means +/-1 line (default: %(default)s)",
    )
    args = parser.parse_args(argv)
    if args.line_window < 0:
        parser.error("--line-window must be non-negative")
    if args.stats_only is None and (args.bugset_root is None or args.logs_root is None):
        parser.error("bugset_root and logs_root are required unless --stats-only is used")
    return args


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if args.stats_only is not None:
            print_summary_stats(read_summary(args.stats_only))
            return 0

        bugset_root = Path(args.bugset_root).resolve()
        logs_root = Path(args.logs_root).resolve()
        if not bugset_root.is_dir():
            print(f"[ERROR] bugset root not found: {bugset_root}", file=sys.stderr)
            return 1
        if not logs_root.is_dir():
            print(f"[ERROR] logs root not found: {logs_root}", file=sys.stderr)
            return 1

        rows: list[dict[str, str]] = []
        for logdir, status_path in iter_status_dirs(logs_root):
            row = process_one_logdir(
                bugset_root,
                logdir,
                status_path,
                args.line_window,
                args.rerank_filename,
            )
            if row is not None:
                rows.append(row)
                print(
                    f"[ROW] {logdir}: {row['bugset']}/{row['diff']}, "
                    f"status={row['status']}, llm={row['top-k']}, "
                    f"sbfl={row['sbfl_top-k']}"
                )

        output_path = Path(args.output)
        with output_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=FIELDNAMES, delimiter="\t")
            writer.writeheader()
            writer.writerows(rows)
        print(f"[DONE] wrote {len(rows)} rows to {output_path}")
        print_summary_stats(rows)
        return 0
    except (OSError, ValueError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
