from pathlib import Path

from ibex_sbfl_batch.logparse import generation_mode, last_run_argv, option_value, trailing_args


def test_parse_shell_escaped_run_command(tmp_path: Path) -> None:
    run_log = tmp_path / "run.log"
    run_log.write_text(
        "[RUN] SBFL\n"
        "[RUN] /tmp/sbfl -c verilator.branch\\,verilator.line "
        "-s PCState\\,CSRState generation --selection diverse psbfl "
        "-- -c 123 -x\n",
        encoding="utf-8",
    )
    argv = last_run_argv(run_log)
    assert option_value(argv, "-c") == "verilator.branch,verilator.line"
    assert option_value(argv, "-s") == "PCState,CSRState"
    assert option_value(argv, "--selection") == "diverse"
    assert generation_mode(argv) == "psbfl"
    assert trailing_args(argv) == ["-c", "123", "-x"]
