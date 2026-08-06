from __future__ import annotations

import os
from dataclasses import fields
from pathlib import Path

from ibex_sbfl_batch.cli import main
from ibex_sbfl_batch.config import AnalysisConfig, ExecutionConfig, GenerationConfig
from ibex_sbfl_batch.workspace import WORKDIR_DIRECTORIES


def _make_executable(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o755)


def _prepare_fixture(tmp_path: Path, monkeypatch) -> tuple[Path, Path, Path]:
    home = tmp_path / "ibex"
    for name in WORKDIR_DIRECTORIES:
        (home / name).mkdir(parents=True)
    (home / "Cargo.lock").write_text("lock", encoding="utf-8")
    (home / "Cargo.toml").write_text("[workspace]\n", encoding="utf-8")
    (home / "ibex.core").write_text("CAPI=2:\n", encoding="utf-8")
    (home / "rtl" / "test.sv").write_text("module test;\nendmodule\n", encoding="utf-8")
    input_elf = home / "input.elf"
    input_elf.write_text("elf", encoding="utf-8")
    _make_executable(
        home / "dv" / "fake_sbfl",
        "#!/usr/bin/env bash\n"
        "previous=\n"
        'for argument in "$@"; do\n'
        '  if [[ "$previous" == --save-corpus ]]; then\n'
        '    printf checkpoint >"$argument"\n'
        "  fi\n"
        '  previous="$argument"\n'
        "done\n"
        "printf '%s\\n' \"$@\"\n",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _make_executable(bin_dir / "fusesoc", "#!/usr/bin/env bash\nexit 0\n")
    monkeypatch.setenv("PATH", f"{bin_dir}:{os.environ['PATH']}")

    case_dir = tmp_path / "bugset" / "case"
    case_dir.mkdir(parents=True)
    diff = case_dir / "bug.sv.diff"
    diff.write_text(
        "--- a/rtl/test.sv\n"
        "+++ b/rtl/test.sv\n"
        "@@ -1,2 +1,3 @@\n"
        " module test;\n"
        "+  logic changed;\n"
        " endmodule\n",
        encoding="utf-8",
    )
    return home, input_elf, diff


def _only_run_dir(root: Path) -> Path:
    return next(path for path in root.iterdir() if path.is_dir())


def _assert_config_reported(output: str, prefix: str, config_type: type) -> None:
    for field in fields(config_type):
        assert f"{prefix}.{field.name}" in output


def test_generation_rerun_and_analysis_argv(tmp_path: Path, monkeypatch, capsys) -> None:
    home, input_elf, diff = _prepare_fixture(tmp_path, monkeypatch)
    logs = tmp_path / "logs"
    assert (
        main(
            [
                "generation",
                "psbfl",
                "--case",
                str(diff),
                "--workdir",
                str(home),
                "--input",
                str(input_elf),
                "--sbfl-bin",
                "dv/fake_sbfl",
                "--logs",
                str(logs),
                "--tmp",
                str(tmp_path / "tmp"),
                "--jobs",
                "1",
                "--save-corpus",
                "--checkpoint-interval",
                "5",
                "--selection",
                "diverse",
                "--mutator-window-size",
                "21",
                "--",
                "-c",
                "999",
            ]
        )
        == 0
    )
    generation_info = capsys.readouterr().out
    assert "command" in generation_info and "generation" in generation_info
    assert "cli.argv" in generation_info and "--checkpoint-interval 5" in generation_info
    _assert_config_reported(generation_info, "execution", ExecutionConfig)
    _assert_config_reported(generation_info, "generation", GenerationConfig)
    old_run = _only_run_dir(logs)
    case_log = next(path for path in old_run.iterdir() if path.is_dir())
    command = (case_log / "run.log").read_text(encoding="utf-8")
    assert "--selection diverse" in command
    assert "--mutator-window-size 21" in command
    assert "--save-corpus" in command
    assert "--checkpoint-interval 5" in command
    assert "-- -c 999" in command
    assert (case_log / "saved_corpus").is_file()

    rerun_logs = tmp_path / "rerun-logs"
    assert (
        main(
            [
                "rerun",
                "--input-logs",
                str(old_run),
                "--max-iters",
                "7",
                "--no-save-corpus",
                "--dry-run",
                "--workdir",
                str(home),
                "--logs",
                str(rerun_logs),
                "--tmp",
                str(tmp_path / "rerun-tmp"),
            ]
        )
        == 0
    )
    rerun_info = capsys.readouterr().out
    assert "command" in rerun_info and "rerun" in rerun_info
    _assert_config_reported(rerun_info, "execution", ExecutionConfig)
    _assert_config_reported(rerun_info, "generation", GenerationConfig)
    rerun_command = next(rerun_logs.rglob("run.log")).read_text(encoding="utf-8")
    assert "--max-iters 7" in rerun_command
    assert "--resume-corpus" in rerun_command
    assert "--save-corpus" not in rerun_command
    assert "--checkpoint-interval" not in rerun_command

    analysis_logs = tmp_path / "analysis-logs"
    assert (
        main(
            [
                "analysis",
                "--input-logs",
                str(old_run),
                "--dry-run",
                "--workdir",
                str(home),
                "--logs",
                str(analysis_logs),
                "--tmp",
                str(tmp_path / "analysis-tmp"),
                "--selection",
                "random",
            ]
        )
        == 0
    )
    analysis_info = capsys.readouterr().out
    assert "command" in analysis_info and "analysis" in analysis_info
    _assert_config_reported(analysis_info, "execution", ExecutionConfig)
    _assert_config_reported(analysis_info, "analysis", AnalysisConfig)
    analysis_command = next(analysis_logs.rglob("run.log")).read_text(encoding="utf-8")
    assert " analysis " in analysis_command
    assert "--selection random" in analysis_command
    assert f"--input {case_log / 'saved_corpus'}" in analysis_command


def test_random_generation_argv(tmp_path: Path, monkeypatch, capsys) -> None:
    home, input_elf, diff = _prepare_fixture(tmp_path, monkeypatch)
    logs = tmp_path / "random-logs"

    assert (
        main(
            [
                "generation",
                "random",
                "--case",
                str(diff),
                "--workdir",
                str(home),
                "--input",
                str(input_elf),
                "--logs",
                str(logs),
                "--tmp",
                str(tmp_path / "random-tmp"),
                "--selection",
                "random",
                "--dry-run",
                "--",
                "-c",
                "321",
            ]
        )
        == 0
    )
    capsys.readouterr()

    command = next(logs.rglob("run.log")).read_text(encoding="utf-8")
    assert " generation " in command
    assert "--selection random" in command
    assert " random -- -c 321" in command
    assert " psbfl " not in command
    assert " wit-hw " not in command
