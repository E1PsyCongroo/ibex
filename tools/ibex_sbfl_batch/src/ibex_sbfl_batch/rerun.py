"""Resume generation from saved corpora recorded by an earlier batch."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path

from .config import DEFAULT_SIMULATOR_ARGS, DEFAULT_TOP_SCOPE, GenerationConfig
from .errors import SbflBatchError
from .logparse import (
    generation_mode,
    has_flag,
    last_run_argv,
    option_value,
    read_status_cases,
    required_option,
)


def _integer(argv: list[str], option: str, default: int) -> int:
    return int(option_value(argv, option, str(default)))


def _floating(argv: list[str], option: str, default: float) -> float:
    return float(option_value(argv, option, str(default)))


def prepare_rerun(args: Namespace) -> tuple[GenerationConfig, list]:
    run_dir = Path(args.input_logs).expanduser().resolve()
    cases = read_status_cases(run_dir, require_corpus=True)
    commands: list[tuple[Path, list[str]]] = []
    for case in cases:
        assert case.old_logdir is not None
        run_log = case.old_logdir / "run.log"
        if not run_log.is_file():
            raise SbflBatchError(f"run.log not found for case {case.index}: {run_log}")
        if not case.diff.is_file():
            raise SbflBatchError(f"original diff not found for case {case.index}: {case.diff}")
        commands.append((run_log, last_run_argv(run_log)))

    first = commands[0][1]
    mode = generation_mode(first)
    if mode == "psbfl":
        wrong = [
            name
            for name in (
                "max_corpus_size",
                "init_seed_rate",
                "mutate_rate",
                "priority_alpha",
                "failed_reward",
            )
            if getattr(args, name) is not None
        ]
    else:
        wrong = [
            name
            for name in ("mutator_window_size", "mutator_weight_strategy")
            if getattr(args, name) is not None
        ]
    if wrong:
        raise SbflBatchError(f"options do not apply to inherited {mode} mode: {', '.join(wrong)}")
    coverage = required_option(first, "-c")
    state = required_option(first, "-s")
    tracker = _integer(first, "--tracker-window-size", 20)
    expected = (mode, coverage, state, tracker)
    for run_log, command in commands:
        observed = (
            generation_mode(command),
            required_option(command, "-c"),
            required_option(command, "-s"),
            _integer(command, "--tracker-window-size", 20),
        )
        if observed != expected:
            raise SbflBatchError(
                f"checkpoint-critical settings differ in {run_log}: "
                f"expected={expected}, observed={observed}"
            )

    checkpoint = option_value(first, "--checkpoint-interval")
    checkpoint_interval = int(checkpoint) if checkpoint is not None else None
    if args.no_checkpoint_interval:
        checkpoint_interval = None
    elif args.checkpoint_interval is not None:
        checkpoint_interval = args.checkpoint_interval
    if not args.save_corpus:
        if args.checkpoint_interval is not None:
            raise SbflBatchError("--checkpoint-interval conflicts with --no-save-corpus")
        checkpoint_interval = None

    def override(name: str, inherited):
        value = getattr(args, name)
        return inherited if value is None else value

    config = GenerationConfig(
        mode=mode,
        coverage=coverage,
        state=state,
        max_run_timeout=override("max_run_timeout", _integer(first, "--max-run-timeout", 10)),
        max_iters=args.max_iters,
        top_pass=override("top_pass", _integer(first, "--top-pass", 10)),
        selection=override("selection", option_value(first, "--selection", "sort")),
        selection_diversity_weight=override(
            "selection_diversity_weight",
            _floating(first, "--selection-diversity-weight", 0.4),
        ),
        selection_pool_factor=override(
            "selection_pool_factor", _integer(first, "--selection-pool-factor", 3)
        ),
        top_sus=override("top_sus", _integer(first, "--top-sus", 10)),
        tracker_window_size=tracker,
        cover_distance_weight=override(
            "cover_distance_weight", _floating(first, "--cover-distance-weight", 0.5)
        ),
        reduce_cover=override("reduce_cover", has_flag(first, "--reduce-cover")),
        save_corpus=args.save_corpus,
        checkpoint_interval=checkpoint_interval,
        gen_only=override("gen_only", has_flag(first, "--gen-only")),
        save_intermediate=override("save_intermediate", has_flag(first, "--save-intermediate")),
        rtl_path=args.rtl_path,
        include_paths=args.include_paths,
        top_module=override("top_module", option_value(first, "--top-module", "ibex_core")),
        top_scope=override("top_scope", option_value(first, "--top-scope", DEFAULT_TOP_SCOPE)),
        metric=override("metric", option_value(first, "--metric", "ochiai")),
        mutator_window_size=override(
            "mutator_window_size", _integer(first, "--mutator-window-size", 20)
        ),
        mutator_weight_strategy=override(
            "mutator_weight_strategy",
            option_value(first, "--mutator-weight-strategy", "uniform"),
        ),
        max_corpus_size=override("max_corpus_size", _integer(first, "--max-corpus-size", 50)),
        init_seed_rate=override("init_seed_rate", _floating(first, "--init-seed-rate", 0.2)),
        mutate_rate=override("mutate_rate", _floating(first, "--mutate-rate", 0.2)),
        priority_alpha=override("priority_alpha", _floating(first, "--priority-alpha", 0.1)),
        failed_reward=override("failed_reward", _floating(first, "--failed-reward", 5.0)),
        simulator_args=list(
            DEFAULT_SIMULATOR_ARGS if args.simulator_args is None else args.simulator_args
        ),
    )
    return config, cases
