"""Generate backward-compatible SBFL and LLM summary TSV files."""

from __future__ import annotations

import csv
import sys
from collections import Counter
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Any

from .errors import SummaryError
from .result_io import load_rerank_result
from .sbfl import (
    find_bug_rank,
    find_reranked_bug_rank,
    load_blocks,
    load_bug_info_from_case_dir,
    parse_block_suspiciousness,
    parse_rank,
    parse_status,
    parse_time,
    read_elapsed_time,
    read_fuzzing_time,
    resolve_bugcase_ref,
)

SBFL_FIELDS = [
    "bugset",
    "diff",
    "status",
    "top-k",
    "sus",
    "elapsed_time",
    "fuzzing_time",
]
LLM_FIELDS = [
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
    "llm_score",
    "normalized_sbfl_score",
    "final_score",
    "causal_role",
    "ranking_strategy",
    "patch_state",
    "prompt_sha256",
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "input_tokens",
    "output_tokens",
]


def iter_status_dirs(logs_root: Path):
    for status_path in sorted(logs_root.rglob("status.txt")):
        yield status_path.parent, status_path


def _write_tsv(path: Path, fields: list[str], rows: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def read_summary(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


def _resolve_case(
    bugset_root: Path, logdir: Path, status_path: Path
) -> tuple[str, str, Path, bool, str, str, str] | None:
    parsed = parse_status(status_path)
    if parsed is None:
        return None
    bugcase, is_ok, csv_status = parsed
    elapsed = read_elapsed_time(logdir)
    fuzzing = read_fuzzing_time(logdir)
    resolved = resolve_bugcase_ref(bugset_root, bugcase)
    if resolved is None:
        return bugcase, "", Path(), False, "ERROR(-1)", elapsed, fuzzing
    bugset, diff, case_dir, _ = resolved
    return bugset, diff, case_dir, is_ok, csv_status, elapsed, fuzzing


def _sbfl_error_row(
    bugset: str, diff: str, status: str, elapsed: str, fuzzing: str
) -> dict[str, str]:
    return {
        "bugset": bugset,
        "diff": diff,
        "status": status,
        "top-k": "",
        "sus": "",
        "elapsed_time": elapsed,
        "fuzzing_time": fuzzing,
    }


def process_sbfl_logdir(
    bugset_root: Path, logdir: Path, status_path: Path, line_window: int
) -> dict[str, str] | None:
    resolved = _resolve_case(bugset_root, logdir, status_path)
    if resolved is None:
        return None
    bugset, diff, case_dir, is_ok, status, elapsed, fuzzing = resolved
    if not is_ok:
        return _sbfl_error_row(bugset, diff, status, elapsed, fuzzing)
    bug_info = load_bug_info_from_case_dir(case_dir)
    result_log = logdir / "result.log"
    blocks_json = logdir / "blocks.json"
    if bug_info is None or not result_log.is_file() or not blocks_json.is_file():
        return _sbfl_error_row(bugset, diff, "ERROR(-1)", elapsed, fuzzing)
    ranked = parse_block_suspiciousness(result_log)
    if not ranked:
        top, sus = "over top-0", ""
    else:
        top, sus = find_bug_rank(bug_info, ranked, load_blocks(blocks_json), line_window)
    return {
        "bugset": bugset,
        "diff": diff,
        "status": "OK",
        "top-k": top,
        "sus": sus,
        "elapsed_time": elapsed,
        "fuzzing_time": fuzzing,
    }


def summarize_sbfl(
    bugset_root: Path, logs_root: Path, output: Path, line_window: int
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for logdir, status_path in iter_status_dirs(logs_root):
        row = process_sbfl_logdir(bugset_root, logdir, status_path, line_window)
        if row is not None:
            rows.append(row)
            print(
                f"[ROW] {logdir}: {row['bugset']}/{row['diff']}, "
                f"status={row['status']}, {row['top-k']}"
            )
    _write_tsv(output, SBFL_FIELDS, rows)
    print(f"[DONE] wrote {len(rows)} rows to {output}")
    print_sbfl_stats(rows)
    return rows


def compute_sbfl_stats(rows: Iterable[Mapping[str, str]]) -> dict[str, float | int]:
    result: dict[str, float | int] = {
        "ok": 0,
        "top1": 0,
        "top5": 0,
        "top10": 0,
        "top20": 0,
        "mar_sum": 0.0,
        "elapsed_sum": 0.0,
        "fuzzing_sum": 0.0,
        "time_count": 0,
    }
    for row in rows:
        if row.get("status") != "OK":
            continue
        result["ok"] += 1
        rank = parse_rank(row.get("top-k", ""))
        if rank is not None:
            for limit, field in ((1, "top1"), (5, "top5"), (10, "top10"), (20, "top20")):
                result[field] += rank <= limit
        result["mar_sum"] += min(rank, 11) if rank is not None else 11
        if row.get("elapsed_time"):
            result["elapsed_sum"] += parse_time(row["elapsed_time"])
        if row.get("fuzzing_time"):
            result["fuzzing_sum"] += parse_time(row["fuzzing_time"])
        result["time_count"] += 1
    return result


def print_sbfl_stats(rows: Iterable[Mapping[str, str]]) -> None:
    stats = compute_sbfl_stats(rows)
    ok = int(stats["ok"])
    print(f"OK              : {ok}")
    top_fields = (
        ("Top-1", "top1"),
        ("Top-5", "top5"),
        ("Top-10", "top10"),
        ("Top-20", "top20"),
    )
    for label, field in top_fields:
        print(f"{label:<16}: {stats[field]}")
    mar = f"{float(stats['mar_sum']) / ok:.3f}" if ok else "n/a"
    print(f"MAR@10          : {mar}")
    time_count = int(stats["time_count"])
    if time_count:
        print(f"Average elapsed : {float(stats['elapsed_sum']) / time_count:.2f} s")
        print(f"Average fuzzing : {float(stats['fuzzing_sum']) / time_count:.2f} s")


def _blank_llm_row(
    bugset: str, diff: str, status: str, elapsed: str, fuzzing: str
) -> dict[str, str]:
    row = {field: "" for field in LLM_FIELDS}
    row.update(
        {
            "bugset": bugset,
            "diff": diff,
            "status": status,
            "elapsed_time": elapsed,
            "fuzzing_time": fuzzing,
        }
    )
    return row


def _compute_sbfl_rank(
    logdir: Path, bug_info: Mapping[str, Any], line_window: int
) -> tuple[str, str]:
    result_log = logdir / "result.log"
    blocks_json = logdir / "blocks.json"
    if not result_log.is_file() or not blocks_json.is_file():
        return "", ""
    ranked = parse_block_suspiciousness(result_log)
    if not ranked:
        return "over top-0", ""
    return find_bug_rank(bug_info, ranked, load_blocks(blocks_json), line_window)


def _format_rank_delta(sbfl_top: str, llm_top: str) -> str:
    sbfl_rank = parse_rank(sbfl_top)
    llm_rank = parse_rank(llm_top)
    if sbfl_rank is None or llm_rank is None:
        return ""
    return f"{sbfl_rank - llm_rank:.6f}".rstrip("0").rstrip(".")


def _nested(result: Mapping[str, Any], parent: str, field: str, default: Any = "") -> Any:
    value = result.get(parent, {})
    return value.get(field, default) if isinstance(value, Mapping) else default


def process_llm_logdir(
    bugset_root: Path,
    logdir: Path,
    status_path: Path,
    line_window: int,
    rerank_filename: str,
) -> dict[str, str] | None:
    resolved = _resolve_case(bugset_root, logdir, status_path)
    if resolved is None:
        return None
    bugset, diff, case_dir, is_ok, status, elapsed, fuzzing = resolved
    if not is_ok:
        return _blank_llm_row(bugset, diff, status, elapsed, fuzzing)
    bug_info = load_bug_info_from_case_dir(case_dir)
    if bug_info is None:
        return _blank_llm_row(bugset, diff, "ERROR(-1)", elapsed, fuzzing)
    sbfl_top, _ = _compute_sbfl_rank(logdir, bug_info, line_window)
    rerank_path = logdir / rerank_filename
    if not rerank_path.is_file():
        row = _blank_llm_row(bugset, diff, "LLM_MISSING", elapsed, fuzzing)
        row["sbfl_top-k"] = sbfl_top
        return row
    try:
        result = load_rerank_result(rerank_path)
    except (OSError, SummaryError) as exc:
        print(f"[WARN] {exc}", file=sys.stderr)
        row = _blank_llm_row(bugset, diff, "LLM_ERROR", elapsed, fuzzing)
        row["sbfl_top-k"] = sbfl_top
        return row

    llm_top, matched = find_reranked_bug_rank(bug_info, result["rankings"], line_window)
    row = _blank_llm_row(bugset, diff, "OK", elapsed, fuzzing)
    config = result.get("config", {}) if isinstance(result.get("config"), Mapping) else {}
    request = result.get("request", {}) if isinstance(result.get("request"), Mapping) else {}
    usage = request.get("usage", {}) if isinstance(request.get("usage"), Mapping) else {}
    inputs = result.get("inputs", {}) if isinstance(result.get("inputs"), Mapping) else {}
    row.update(
        {
            "model": str(result.get("model", "")),
            "source_mode": str(result.get("source_mode", config.get("source_mode", ""))),
            "candidate_count": str(
                result.get("candidate_count", config.get("candidate_count", ""))
            ),
            "rerank_top_k": str(result["top_k"]),
            "top-k": llm_top,
            "sbfl_top-k": sbfl_top,
            "rank_delta": _format_rank_delta(sbfl_top, llm_top),
            "llm_elapsed_time": str(
                result.get("llm_elapsed_seconds", request.get("elapsed_seconds", ""))
            ),
            "ranking_strategy": str(config.get("ranking_strategy", "")),
            "patch_state": str(inputs.get("patch_state", "")),
            "prompt_sha256": str(request.get("prompt_sha256", "")),
            "prompt_tokens": str(usage.get("prompt_tokens", usage.get("input_tokens", ""))),
            "completion_tokens": str(
                usage.get("completion_tokens", usage.get("output_tokens", ""))
            ),
            "total_tokens": str(usage.get("total_tokens", "")),
            "input_tokens": str(usage.get("input_tokens", "")),
            "output_tokens": str(usage.get("output_tokens", "")),
        }
    )
    if matched is not None:
        row.update(
            {
                "candidate_id": str(matched.get("candidate_id", "")),
                "original_rank": str(matched.get("original_rank", "")),
                "sus": str(matched.get("suspiciousness", "")),
                "module": str(matched.get("module", "")),
                "scope": str(matched.get("scope", "")),
                "bid": str(matched.get("bid", "")),
                "lines": ",".join(str(line) for line in matched.get("lines", [])),
                "reason": " ".join(str(matched.get("reason", "")).split()),
                "llm_score": str(matched.get("llm_score", "")),
                "normalized_sbfl_score": str(matched.get("normalized_sbfl_score", "")),
                "final_score": str(matched.get("final_score", "")),
                "causal_role": str(matched.get("causal_role", "")),
            }
        )
    return row


def summarize_llm(
    bugset_root: Path,
    logs_root: Path,
    output: Path,
    line_window: int,
    rerank_filename: str,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for logdir, status_path in iter_status_dirs(logs_root):
        row = process_llm_logdir(bugset_root, logdir, status_path, line_window, rerank_filename)
        if row is not None:
            rows.append(row)
            print(
                f"[ROW] {logdir}: {row['bugset']}/{row['diff']}, status={row['status']}, "
                f"llm={row['top-k']}, sbfl={row['sbfl_top-k']}"
            )
    _write_tsv(output, LLM_FIELDS, rows)
    print(f"[DONE] wrote {len(rows)} rows to {output}")
    print_llm_stats(rows)
    return rows


def compute_llm_stats(rows: Iterable[Mapping[str, str]]) -> dict[str, Any]:
    row_list = list(rows)
    ok_rows = [row for row in row_list if row.get("status") == "OK"]
    top1 = top5 = top10 = improved = unchanged = worsened = 0
    reciprocal = mar10 = 0.0
    timings: dict[str, list[float]] = {"llm": [], "elapsed": [], "fuzzing": []}
    for row in ok_rows:
        rank = parse_rank(row.get("top-k", ""))
        sbfl_rank = parse_rank(row.get("sbfl_top-k", ""))
        if rank is not None:
            top1 += rank <= 1
            top5 += rank <= 5
            top10 += rank <= 10
            reciprocal += 1.0 / rank
        mar10 += min(rank, 11) if rank is not None else 11
        if rank is None and sbfl_rank is not None:
            worsened += 1
        elif rank is not None and sbfl_rank is None:
            improved += 1
        elif rank is not None and sbfl_rank is not None:
            improved += rank < sbfl_rank
            unchanged += rank == sbfl_rank
            worsened += rank > sbfl_rank
        time_fields = (
            ("llm_elapsed_time", "llm"),
            ("elapsed_time", "elapsed"),
            ("fuzzing_time", "fuzzing"),
        )
        for field, key in time_fields:
            if row.get(field):
                timings[key].append(parse_time(row[field]))
    count = len(ok_rows)
    return {
        "total": len(row_list),
        "ok": count,
        "statuses": Counter(row.get("status", "") for row in row_list),
        "models": Counter(row.get("model", "") for row in ok_rows),
        "top1": top1,
        "top5": top5,
        "top10": top10,
        "mrr": reciprocal / count if count else None,
        "mar10": mar10 / count if count else None,
        "improved": improved,
        "unchanged": unchanged,
        "worsened": worsened,
        "average_llm": sum(timings["llm"]) / len(timings["llm"]) if timings["llm"] else None,
        "average_elapsed": (
            sum(timings["elapsed"]) / len(timings["elapsed"]) if timings["elapsed"] else None
        ),
        "average_fuzzing": (
            sum(timings["fuzzing"]) / len(timings["fuzzing"]) if timings["fuzzing"] else None
        ),
    }


def print_llm_stats(rows: Iterable[Mapping[str, str]]) -> None:
    stats = compute_llm_stats(rows)
    print(f"{'Total rows':<20}: {stats['total']}")
    print(f"{'Valid LLM results':<20}: {stats['ok']}")
    for status, count in sorted(stats["statuses"].items()):
        if status != "OK":
            print(f"  {status:<18}: {count}")
    models = ", ".join(
        f"{model or '(unknown)'}={count}" for model, count in sorted(stats["models"].items())
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
    average_fields = (
        ("Average LLM", "average_llm"),
        ("Average SBFL", "average_elapsed"),
        ("Average fuzzing", "average_fuzzing"),
    )
    for label, field in average_fields:
        value = stats[field]
        print(f"{label:<20}: {value:.2f} s" if value is not None else f"{label:<20}: n/a")
