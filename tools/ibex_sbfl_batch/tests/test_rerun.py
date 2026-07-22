from pathlib import Path

from ibex_sbfl_batch.cli import build_parser
from ibex_sbfl_batch.rerun import prepare_rerun


def _old_run(tmp_path: Path) -> Path:
    run_dir = tmp_path / "old"
    case_log = run_dir / "case_log"
    case_dir = tmp_path / "bugset" / "case"
    case_log.mkdir(parents=True)
    case_dir.mkdir(parents=True)
    corpus = case_log / "saved_corpus"
    corpus.write_text("checkpoint", encoding="utf-8")
    diff = case_dir / "bug.sv.diff"
    diff.write_text("patch", encoding="utf-8")
    (case_log / "run.log").write_text(
        "[RUN] /tmp/sbfl -c branch -s PCState generation "
        "--max-run-timeout 9 --max-iters 20 --top-pass 7 --selection sort "
        "--selection-diversity-weight 0.25 --selection-pool-factor 4 "
        "--top-sus 6 --tracker-window-size 33 --cover-distance-weight 0.45 "
        "--output /tmp/out --resume-corpus /tmp/old --save-corpus /tmp/saved "
        "--checkpoint-interval 8 --save-intermediate psbfl "
        "--mutator-window-size 12 --mutator-weight-strategy tail_quad -- -c 111\n",
        encoding="utf-8",
    )
    (run_dir / "run_status.tsv").write_text(
        "case_index\trel_dir\tdiff_name\tcase_dir\tdiff\tstatus\trc\t"
        "elapsed_time\tlogdir\tworkdir\n"
        f"3\tcase\tbug.sv.diff\t{case_dir}\t{diff}\tOK\t0\t00:00:01:000\t"
        "/old/location/case_log\t/old/work\n",
        encoding="utf-8",
    )
    return run_dir


def test_rerun_no_save_corpus_drops_inherited_checkpoint(tmp_path: Path) -> None:
    run_dir = _old_run(tmp_path)
    parser = build_parser()
    args = parser.parse_args(
        [
            "rerun",
            "--input-logs",
            str(run_dir),
            "--max-iters",
            "77",
            "--selection",
            "diverse",
            "--no-save-corpus",
        ]
    )
    args.simulator_args = ["-c", "999"]
    config, cases = prepare_rerun(args)
    assert len(cases) == 1
    assert config.max_iters == 77
    assert config.selection == "diverse"
    assert config.tracker_window_size == 33
    assert config.save_corpus is False
    assert config.checkpoint_interval is None
    assert config.mutator_window_size == 12
    assert config.mutator_weight_strategy == "tail_quad"
    assert config.simulator_args == ["-c", "999"]
