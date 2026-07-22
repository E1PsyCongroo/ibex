# CPU SBFL Integration for the Ibex Simple System

[中文](README_CN.md) | English

This directory integrates the standalone [`cpusbfl` Rust project](sbfl/README.md)
with the Ibex Verilator Simple System and Spike co-simulation. It supplies the
host simulator, coverage/state C ABI, FuseSoC build description, and Ibex
experiment workflow that are intentionally outside the generic SBFL project.

## Integration Layout

```text
dv/verilator/simple_system_sbfl/
├── ibex_simple_system_sbfl.core    # Verilator/FuseSoC simulation target
├── ibex_sbfl_setup.core            # dependency checks and Rust build hooks
├── util/                           # setup/build hook scripts
├── src/csrc/                       # simulator, Spike, coverage, and state bridge
├── sbfl/                           # standalone CPU SBFL project
└── docs/                           # Ibex-specific technical documentation
```

The runtime path is:

```text
RISC-V ELF
  -> cpusbfl LibAFL executor
  -> simple_system_sbfl.cc::sim_main()
  -> Ibex RTL + Spike lockstep comparison
  -> Verilator coverage + Spike architectural state
  -> cpusbfl selection and SBFL analysis
  -> coverage-point / RTL-block ranking
```

## Prerequisites

The integration requires:

- the Rust toolchain and `cargo-make`;
- the FuseSoC and Verilator versions required by Ibex;
- the Ibex co-simulation build of Spike;
- `pkg-config` entries for `riscv-riscv`, `riscv-disasm`, and `riscv-fdt`.

For a Spike installation under `/opt/spike-cosim`:

```bash
export PKG_CONFIG_PATH=/opt/spike-cosim/lib/pkgconfig:${PKG_CONFIG_PATH}
cargo install cargo-make
```

Set `IBEX_HOME` to the Ibex repository root. The FuseSoC pre-build hook runs
`cargo make build-all` there and links `target/release/libcpusbfl.so` into the
simulator.

## Build

From the Ibex repository root:

```bash
export IBEX_HOME="$PWD"

fusesoc --cores-root=. run \
  --target=sim \
  --setup \
  --build \
  lowrisc:ibex:ibex_simple_system_sbfl \
  --RV32E=0 \
  --RV32M=ibex_pkg::RV32MFast
```

The executable is normally generated at:

```text
build/lowrisc_ibex_ibex_simple_system_sbfl_0/sim-verilator/Vibex_simple_system
```

```bash
SBFL_BIN=build/lowrisc_ibex_ibex_simple_system_sbfl_0/sim-verilator/Vibex_simple_system
"$SBFL_BIN" --help
```

## Run One Generation

The initial ELF must reproduce an Ibex/Spike mismatch.

```bash
"$SBFL_BIN" \
  --coverage verilator.branch,verilator.line \
  --state PCState,ArchIntRegState,CSRState \
  generation \
  --input examples/sw/benchmarks/coremark/coremark.elf \
  --output logs/manual \
  --max-iters 100 \
  --top-pass 10 \
  --selection diverse \
  --save-corpus logs/manual/saved_corpus \
  psbfl \
  --mutator-window-size 20 \
  --mutator-weight-strategy uniform \
  -- -c 5000000
```

The root `--coverage`/`--state` arguments must precede `generation`.
Generation-wide arguments precede `psbfl` or `wit-hw`; mode-specific arguments
follow the mode.

## Bugset Runners

The Ibex repository provides batch wrappers under `scripts/`:

- [`run_bugset_psbfl.py`](../../../scripts/run_bugset_psbfl.py) selects
  `GenerationMode::PSBFL`;
- [`run_bugset_withw.py`](../../../scripts/run_bugset_withw.py) selects
  `GenerationMode::WitHW`;
- [`run_bugset_sbfl.py`](../../../scripts/run_bugset_sbfl.py) is the generic
  compatibility entry point;
- [`rerun_bugset_corpus.py`](../../../scripts/rerun_bugset_corpus.py) resumes
  generation from each saved corpus in an earlier bugset run;
- [`analyze_bugset_corpus.py`](../../../scripts/analyze_bugset_corpus.py)
  rebuilds recorded bug cases and invokes `analysis` for each `saved_corpus`.
- [`run_args_sweep_sbfl.py`](../../../scripts/run_args_sweep_sbfl.py) runs
  PSBFL parameter combinations concurrently and combines their summaries.

These entry points run the uv-managed Python implementation in
[`tools/ibex_sbfl_batch`](../../../tools/ibex_sbfl_batch/README.md).

Example:

```bash
scripts/run_bugset_psbfl.py \
  --all verify_dataset \
  --input examples/sw/benchmarks/coremark/coremark.elf \
  --max-iters 100 \
  --jobs 4 \
  --save-corpus \
  --logs logs/psbfl
```

For the wrappers, `--save-corpus` is a boolean flag. Each case writes its
checkpoint to `<case_logdir>/saved_corpus`. Workdirs contain only `dv/`,
`vendor/`, `rtl/`, `shared/`, root `.core` files, `Cargo.lock`, and
`Cargo.toml` before the bug diff is applied.

## Reanalyze Saved Corpora

```bash
scripts/analyze_bugset_corpus.py \
  --input-logs logs/psbfl/<run-id> \
  --top-pass 50 \
  --selection diverse \
  --metric ochiai \
  --jobs 4 \
  --logs logs/analysis
```

The analysis runner reads the original `run_status.tsv` and `run.log`, extracts
coverage/state/tracker settings, rebuilds each patched design, and uses the old
checkpoint as `analysis --input`. It writes results to a fresh log tree and
does not overwrite the checkpoint. Use `--dry-run` to inspect the plan.

## Documentation

- [Integration documentation index](docs/README.md)
- [Build and runtime integration](docs/01-build-and-runtime.md)
- [Batch generation and analysis runners](docs/02-batch-runners.md)
- [Simulator, coverage, and state adapter](docs/03-simulator-adapter.md)
- [Standalone CPU SBFL documentation](sbfl/docs/README.md)

The generic SBFL algorithms, checkpoint format, CLI semantics, and host ABI are
documented inside `sbfl/`. Ibex/Spike/FuseSoC and bugset-specific instructions
are documented here.
