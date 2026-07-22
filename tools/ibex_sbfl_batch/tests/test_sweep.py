from __future__ import annotations

import csv
from dataclasses import fields
from pathlib import Path

import pytest

from ibex_sbfl_batch.cli import main
from ibex_sbfl_batch.errors import SbflBatchError
from ibex_sbfl_batch.sweep import SweepConfig, _positive_ints


def _write_script(path: Path, source: str) -> None:
    path.write_text(source, encoding="utf-8")
    path.chmod(0o755)


def test_sweep_runs_cartesian_product_and_combines_summaries(tmp_path: Path, capsys) -> None:
    bugset = tmp_path / "bugset"
    case = bugset / "case"
    case.mkdir(parents=True)
    (case / "bug.sv.diff").write_text("diff", encoding="utf-8")
    run_script = tmp_path / "run.py"
    summary_script = tmp_path / "summary.py"
    _write_script(
        run_script,
        """import pathlib
import sys
args = sys.argv[1:]
logs = pathlib.Path(args[args.index('-l') + 1])
logs.mkdir(parents=True, exist_ok=True)
(logs / 'forwarded.txt').write_text(' '.join(args))
""",
    )
    _write_script(
        summary_script,
        """import csv
import pathlib
import sys
args = sys.argv[1:]
output = pathlib.Path(args[args.index('-o') + 1])
output.parent.mkdir(parents=True, exist_ok=True)
with output.open('w', newline='') as handle:
    writer = csv.writer(handle, delimiter='\\t')
    writer.writerow(['bugset', 'diff', 'status', 'top-k'])
    writer.writerow(['case', 'bug.sv.diff', 'OK', 'top-1'])
""",
    )
    logs = tmp_path / "logs"
    assert (
        main(
            [
                "sweep",
                "--all",
                str(bugset),
                "--top-pass",
                "5,10",
                "--mutator-window-size",
                "20 30",
                "--mutator-weight-strategy",
                "uniform,tail_linear",
                "--sweep-jobs",
                "3",
                "--run-script",
                str(run_script),
                "--summarize-script",
                str(summary_script),
                "--logs",
                str(logs),
                "--tmp",
                str(tmp_path / "tmp"),
                "--",
                "--max-iters",
                "7",
                "--",
                "-c",
                "999",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    assert "command" in output and "sweep" in output
    for field in fields(SweepConfig):
        assert f"sweep.{field.name}" in output

    sweep_root = next(logs.iterdir())
    with (sweep_root / "sweep_status.tsv").open(newline="", encoding="utf-8") as handle:
        statuses = list(csv.DictReader(handle, delimiter="\t"))
    assert len(statuses) == 8
    assert all(None not in row for row in statuses)
    assert {row["status"] for row in statuses} == {"OK"}
    for row in statuses:
        forwarded = (Path(row["logs_root"]) / "forwarded.txt").read_text()
        assert "--max-iters 7 -- -c 999" in forwarded

    with (sweep_root / "args_sweep_sbfl_block_summary.tsv").open(
        newline="", encoding="utf-8"
    ) as handle:
        combined = list(csv.DictReader(handle, delimiter="\t"))
    assert len(combined) == 8
    assert {row["top_pass"] for row in combined} == {"5", "10"}
    assert {row["mutator_weight_strategy"] for row in combined} == {
        "uniform",
        "tail_linear",
    }
    assert {row["mutator_window_size"] for row in combined} == {"20", "30"}


def test_sweep_rejects_duplicate_values() -> None:
    with pytest.raises(SbflBatchError, match="duplicate top pass"):
        _positive_ints("top pass", ["10", "10"])
