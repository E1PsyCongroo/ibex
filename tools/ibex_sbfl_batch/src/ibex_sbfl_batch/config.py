"""Typed configuration shared by batch operations."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_SBFL_BIN = "build/lowrisc_ibex_ibex_simple_system_sbfl_0/sim-verilator/Vibex_simple_system"
DEFAULT_COVERAGE = "verilator.branch,verilator.line"
DEFAULT_STATE = "PCState,ArchIntRegState,CSRState"
DEFAULT_RTL = "rtl/"
DEFAULT_INCLUDES = "vendor/lowrisc_ip/ip/prim/rtl/,vendor/lowrisc_ip/dv/sv/dv_utils/"
DEFAULT_TOP_MODULE = "ibex_core"
DEFAULT_TOP_SCOPE = "TOP.ibex_simple_system.u_top.u_ibex_top.u_ibex_core"
DEFAULT_SIMULATOR_ARGS = ["-c", "5000000"]


@dataclass(slots=True)
class ExecutionConfig:
    ibex_home: Path
    logs_root: Path
    tmp_root: Path
    jobs: int
    sbfl_bin: str = DEFAULT_SBFL_BIN
    keep_workdir: bool = False
    disassemble: bool = False
    objdump_bin: str = "riscv32-unknown-elf-objdump"
    dry_run: bool = False


@dataclass(slots=True)
class GenerationConfig:
    mode: str
    coverage: str = DEFAULT_COVERAGE
    state: str = DEFAULT_STATE
    max_run_timeout: int = 180
    max_iters: int = 20
    top_pass: int = 120
    selection: str = "sort"
    selection_diversity_weight: float = 0.4
    selection_pool_factor: int = 3
    top_sus: int = 50
    tracker_window_size: int = 20
    cover_distance_weight: float = 0.5
    reduce_insts: bool = False
    reduce_cover: bool = False
    input_path: Path | None = None
    save_corpus: bool = False
    checkpoint_interval: int | None = None
    gen_only: bool = False
    save_reduce: bool = False
    save_intermediate: bool = False
    rtl_path: str = DEFAULT_RTL
    include_paths: str = DEFAULT_INCLUDES
    top_module: str = DEFAULT_TOP_MODULE
    top_scope: str = DEFAULT_TOP_SCOPE
    metric: str = "ochiai"
    mutator_window_size: int = 5
    mutator_weight_strategy: str = "uniform"
    max_corpus_size: int = 500
    init_seed_rate: float = 0.2
    mutate_rate: float = 0.2
    priority_alpha: float = 0.1
    failed_reward: float = 5.0
    simulator_args: list[str] = field(default_factory=lambda: list(DEFAULT_SIMULATOR_ARGS))


@dataclass(slots=True)
class AnalysisConfig:
    top_pass: int = 120
    selection: str = "sort"
    selection_diversity_weight: float = 0.4
    selection_pool_factor: int = 5
    reduce_cover: bool = False
    top_sus: int = 50
    metric: str = "ochiai"
    cover_distance_weight: float = 0.5
    save_intermediate: bool = False
    rtl_path: str = DEFAULT_RTL
    include_paths: str = DEFAULT_INCLUDES
    top_module: str = DEFAULT_TOP_MODULE
    top_scope: str = DEFAULT_TOP_SCOPE
    simulator_args: list[str] = field(default_factory=list)


@dataclass(frozen=True, slots=True)
class CaseSpec:
    index: str
    rel_dir: str
    diff: Path
    case_dir: Path
    diff_name: str
    corpus: Path | None = None
    old_logdir: Path | None = None


@dataclass(frozen=True, slots=True)
class CaseResult:
    case: CaseSpec
    status: str
    return_code: int
    elapsed: str
    logdir: Path
    workdir: Path
