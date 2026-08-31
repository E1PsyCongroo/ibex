#!/usr/bin/env python3
"""Plot Top-N and MAR@10 while sweeping the LLM score weight."""

from __future__ import annotations

import argparse
import csv
import os
import sys
import tempfile
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
for project in ("ibex_sbfl_common", "ibex_sbfl_batch", "ibex_sbfl_rerank"):
    sys.path.insert(0, str(REPO_ROOT / "tools" / project / "src"))

matplotlib_cache = Path(tempfile.gettempdir()) / "ibex-matplotlib-cache"
matplotlib_cache.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("MPLCONFIGDIR", str(matplotlib_cache))
os.environ.setdefault("MPLBACKEND", "Agg")

import matplotlib.pyplot as plt
from ibex_sbfl_batch.statistics import compute_llm_stats
from ibex_sbfl_common.artifacts import (
    find_reranked_bug_rank,
    load_bug_info_from_case_dir,
    parse_status,
    resolve_bugcase_ref,
)
from ibex_sbfl_rerank.artifacts import (
    load_saved_result,
    parse_prompt_candidates,
    parse_raw_assessments,
)
from ibex_sbfl_rerank.ranking import Candidate, RerankError, rank_candidates
from matplotlib.ticker import MaxNLocator


@dataclass(frozen=True)
class PreparedCase:
    bug_info: dict[str, Any]
    candidates: tuple[Candidate, ...]
    assessments: tuple[dict[str, Any], ...]
    top_k: int


@dataclass(frozen=True)
class PreparedExperiment:
    name: str
    cases: tuple[PreparedCase, ...]
    fixed_rows: tuple[dict[str, str], ...]


def decimal_value(value: str) -> Decimal:
    try:
        return Decimal(value)
    except InvalidOperation as exc:
        raise argparse.ArgumentTypeError(f"invalid decimal value: {value!r}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Sweep the unnormalized LLM/SBFL combination weight and plot Top-1/5/10 "
            "and MAR@10 for every experiment directory."
        )
    )
    parser.add_argument(
        "logs_root",
        type=Path,
        help="directory containing experiment subdirectories such as reduce_20",
    )
    parser.add_argument(
        "--experiments",
        nargs="+",
        help="experiment directory names to include (default: discover all)",
    )
    parser.add_argument("--bugset-root", type=Path, help="default: auto-detect verify_dataset")
    parser.add_argument("--input-filename", default="llm_rerank.json")
    parser.add_argument("--line-window", type=int, default=0)
    parser.add_argument("--weight-start", type=decimal_value, default=Decimal(0))
    parser.add_argument("--weight-stop", type=decimal_value, default=Decimal(1))
    parser.add_argument("--weight-step", type=decimal_value, default=Decimal("0.05"))
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        help="PNG output path (default: <logs_root>/llm_weight_sweep.png)",
    )
    parser.add_argument(
        "--csv",
        type=Path,
        help="CSV output path (default: same basename as PNG)",
    )
    parser.add_argument("--title", default="LLM weight sweep")
    parser.add_argument("--dpi", type=int, default=200)
    return parser


def weight_values(start: Decimal, stop: Decimal, step: Decimal) -> list[Decimal]:
    if start < 0 or stop > 1 or start > stop:
        raise ValueError("weight range must satisfy 0 <= start <= stop <= 1")
    if step <= 0:
        raise ValueError("--weight-step must be positive")
    values: list[Decimal] = []
    value = start
    while value <= stop:
        values.append(value)
        value += step
    if not values or values[-1] != stop:
        raise ValueError("weight step must land exactly on --weight-stop")
    return values


def weight_label(weight: Decimal) -> str:
    text = format(weight, "f").rstrip("0").rstrip(".")
    return text or "0"


def find_bugset_root(logs_root: Path, explicit: Path | None) -> Path:
    if explicit is not None:
        candidate = explicit.expanduser().resolve()
        if not candidate.is_dir():
            raise ValueError(f"bugset root is not a directory: {candidate}")
        return candidate
    for parent in (Path.cwd().resolve(), logs_root, *logs_root.parents):
        candidate = parent / "verify_dataset"
        if candidate.is_dir():
            return candidate.resolve()
    raise ValueError("cannot find verify_dataset; pass --bugset-root explicitly")


def discover_experiments(logs_root: Path, requested: Sequence[str] | None) -> list[Path]:
    if requested:
        experiments = [logs_root / name for name in requested]
        missing = [str(path) for path in experiments if not path.is_dir()]
        if missing:
            raise ValueError(f"experiment directories not found: {', '.join(missing)}")
    else:
        experiments = [
            path
            for path in sorted(logs_root.iterdir())
            if path.is_dir() and any(path.rglob("status.txt"))
        ]
    if not experiments:
        raise ValueError(f"no experiment directories found under {logs_root}")
    return experiments


def source_top_k(result: dict[str, Any], candidate_count: int, result_path: Path) -> int:
    config = result.get("config")
    configured = config.get("top_k") if isinstance(config, dict) else None
    raw_top_k = result.get("top_k", configured if configured is not None else candidate_count)
    try:
        top_k = int(raw_top_k)
    except (TypeError, ValueError) as exc:
        raise RerankError(f"top_k in {result_path} is not an integer") from exc
    if not 0 < top_k <= candidate_count:
        raise RerankError(
            f"top_k={top_k} in {result_path} is outside 1..{candidate_count}"
        )
    return top_k


def prepare_experiment(
    experiment_path: Path,
    bugset_root: Path,
    input_filename: str,
) -> PreparedExperiment:
    cases: list[PreparedCase] = []
    fixed_rows: list[dict[str, str]] = []
    statuses: Counter[str] = Counter()
    for status_path in sorted(experiment_path.rglob("status.txt")):
        parsed = parse_status(status_path)
        if parsed is None:
            statuses["STATUS_INVALID"] += 1
            fixed_rows.append({"status": "STATUS_INVALID"})
            continue
        bugcase, is_ok, status = parsed
        if not is_ok:
            statuses[status] += 1
            fixed_rows.append({"status": status})
            continue
        resolved = resolve_bugcase_ref(bugset_root, bugcase)
        if resolved is None:
            statuses["ERROR(-1)"] += 1
            fixed_rows.append({"status": "ERROR(-1)"})
            continue
        _, _, case_dir, _ = resolved
        bug_info = load_bug_info_from_case_dir(case_dir)
        if bug_info is None:
            statuses["ERROR(-1)"] += 1
            fixed_rows.append({"status": "ERROR(-1)"})
            continue

        logdir = status_path.parent
        result_path = logdir / input_filename
        prompt_path = result_path.with_suffix(result_path.suffix + ".prompt.md")
        if not result_path.is_file() or not prompt_path.is_file():
            statuses["LLM_MISSING"] += 1
            fixed_rows.append({"status": "LLM_MISSING"})
            continue
        try:
            result = load_saved_result(result_path)
            candidates = tuple(parse_prompt_candidates(prompt_path))
            assessments = tuple(parse_raw_assessments(result, result_path))
            top_k = source_top_k(result, len(candidates), result_path)
            # Validate the raw response once before the sweep.
            rank_candidates(candidates, assessments, 0.0)
        except (OSError, RerankError, ValueError) as exc:
            print(f"[WARN] {result_path}: {exc}", file=sys.stderr)
            statuses["LLM_ERROR"] += 1
            fixed_rows.append({"status": "LLM_ERROR"})
            continue
        cases.append(PreparedCase(bug_info, candidates, assessments, top_k))
        statuses["OK"] += 1

    if not statuses:
        raise ValueError(f"no status.txt files found in {experiment_path}")
    status_text = ", ".join(f"{key}={value}" for key, value in sorted(statuses.items()))
    print(f"[LOAD] {experiment_path.name}: {status_text}")
    return PreparedExperiment(experiment_path.name, tuple(cases), tuple(fixed_rows))


def evaluate_weight(
    experiment: PreparedExperiment,
    weight: Decimal,
    line_window: int,
) -> dict[str, Any]:
    rows: list[dict[str, str]] = [dict(row) for row in experiment.fixed_rows]
    for case in experiment.cases:
        assessments = rank_candidates(case.candidates, case.assessments, float(weight))
        rankings = assessments[: case.top_k]
        top, _ = find_reranked_bug_rank(case.bug_info, rankings, line_window)
        rows.append({"status": "OK", "top-k": top, "sbfl-top-k": ""})
    stats = compute_llm_stats(rows)
    return {
        "experiment": experiment.name,
        "weight": weight_label(weight),
        "valid_results": stats["ok"],
        "top1": stats["top1"],
        "top5": stats["top5"],
        "top10": stats["top10"],
        "mar10": stats["mar10"],
    }


def write_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = ("experiment", "weight", "valid_results", "top1", "top5", "top10", "mar10")
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def plot_results(
    path: Path,
    rows: Sequence[dict[str, Any]],
    experiment_names: Sequence[str],
    title: str,
    dpi: int,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    figure, axes = plt.subplots(2, 2, figsize=(13, 8.5), sharex=True)
    metric_axes = (
        ("top1", "Top-1"),
        ("top5", "Top-5"),
        ("top10", "Top-10"),
        ("mar10", "MAR@10"),
    )
    color_map = plt.get_cmap("tab10")
    colors = {name: color_map(index % 10) for index, name in enumerate(experiment_names)}
    for axis, (field, label) in zip(axes.flat, metric_axes, strict=True):
        for name in experiment_names:
            experiment_rows = [row for row in rows if row["experiment"] == name]
            axis.plot(
                [float(row["weight"]) for row in experiment_rows],
                [float(row[field]) for row in experiment_rows],
                color=colors[name],
                marker="o",
                markersize=3,
                linewidth=1.6,
                label=name,
            )
        axis.set_title(label)
        axis.set_ylabel(label)
        axis.grid(True, alpha=0.3)
        if field != "mar10":
            axis.yaxis.set_major_locator(MaxNLocator(integer=True))
    for axis in axes[-1]:
        axis.set_xlabel("LLM weight")
    figure.suptitle(title)
    handles, labels = axes[0, 0].get_legend_handles_labels()
    figure.legend(
        handles,
        labels,
        loc="upper center",
        bbox_to_anchor=(0.5, 0.95),
        ncol=min(3, len(labels)),
        frameon=False,
    )
    figure.tight_layout(rect=(0, 0, 1, 0.88))
    figure.savefig(path, dpi=dpi, bbox_inches="tight")
    plt.close(figure)


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        logs_root = args.logs_root.expanduser().resolve()
        if not logs_root.is_dir():
            raise ValueError(f"logs root is not a directory: {logs_root}")
        if Path(args.input_filename).name != args.input_filename:
            raise ValueError("--input-filename must be a plain filename")
        if args.line_window < 0:
            raise ValueError("--line-window must be non-negative")
        if args.dpi <= 0:
            raise ValueError("--dpi must be positive")
        weights = weight_values(args.weight_start, args.weight_stop, args.weight_step)
        experiment_paths = discover_experiments(logs_root, args.experiments)
        bugset_root = find_bugset_root(logs_root, args.bugset_root)
        output_path = (
            args.output.expanduser().resolve()
            if args.output is not None
            else logs_root / "llm_weight_sweep.png"
        )
        csv_path = (
            args.csv.expanduser().resolve()
            if args.csv is not None
            else output_path.with_suffix(".csv")
        )

        print(f"[CONFIG] logs_root={logs_root}")
        print(f"[CONFIG] bugset_root={bugset_root}")
        print(
            f"[CONFIG] weights={weight_label(weights[0])}..{weight_label(weights[-1])}, "
            f"step={weight_label(args.weight_step)}"
        )
        experiments = [
            prepare_experiment(path, bugset_root, args.input_filename)
            for path in experiment_paths
        ]
        rows: list[dict[str, Any]] = []
        for experiment in experiments:
            for weight in weights:
                row = evaluate_weight(experiment, weight, args.line_window)
                rows.append(row)
                mar10 = row["mar10"]
                mar_text = f"{mar10:.3f}" if mar10 is not None else "n/a"
                print(
                    f"[STAT] {experiment.name:<24} weight={row['weight']:<4} "
                    f"Top-1={row['top1']:<3} Top-5={row['top5']:<3} "
                    f"Top-10={row['top10']:<3} MAR@10={mar_text}"
                )
        names = [experiment.name for experiment in experiments]
        write_csv(csv_path, rows)
        plot_results(output_path, rows, names, args.title, args.dpi)
        print(f"[DONE] CSV: {csv_path}")
        print(f"[DONE] plot: {output_path}")
        return 0
    except (OSError, ValueError, RerankError) as exc:
        print(f"[ERROR] {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
