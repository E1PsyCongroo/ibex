# Ibex SBFL Batch Tools

`ibex_sbfl_batch` owns non-interactive Ibex SBFL workflows that do not call an LLM:

- PSBFL, Random, and WitHW bugset generation;
- parallel PSBFL parameter sweeps;
- generation resume from `saved_corpus`;
- checkpoint analysis after rebuilding patched simulators;
- SBFL/LLM-result summary TSV generation and descriptive statistics.

The LLM request, prompting, and reranking implementation remains in
`tools/ibex_sbfl_llm`. Generic artifact parsing comes from
[`tools/ibex_sbfl_common`](../ibex_sbfl_common/README.md).

## Environment

```bash
uv sync --project tools/ibex_sbfl_batch --all-groups --frozen
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch --help
```

The repository-level Python scripts are compatibility entry points and invoke
this uv project automatically. FuseSoC and Edalize are pinned to the same
versions as the Ibex Python tool environment so FuseSoC pre-build scripts use a
`python3` that can import Edalize.

## Commands

```bash
# Generation
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch generation psbfl \
  --all verify_dataset --max-iters 100 --save-corpus

# Random generation
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch generation random \
  --all verify_dataset --max-iters 100 --save-corpus

# Resume generation
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch rerun \
  --input-logs logs/psbfl/<run-id> --max-iters 100 --no-save-corpus

# Analyze checkpoints
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch analysis \
  --input-logs logs/psbfl/<run-id> --selection diverse

# Parameter sweep
scripts/run_args_sweep_sbfl.py --all verify_dataset \
  --top-pass 10,20 --mutator-window-size 5,10 \
  --mutator-weight-strategy uniform,tail_linear --sweep-jobs 2 \
  -- --max-iters 20

# Summaries and statistics
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch summarize sbfl \
  verify_dataset logs/psbfl -o sbfl_summary.tsv
uv run --project tools/ibex_sbfl_batch --frozen ibex-sbfl-batch stats sbfl \
  sbfl_summary.tsv
```

Arguments after `--` are forwarded to the simulator. `analysis --dry-run`
parses old logs and writes planned commands without copying, patching, or
building workdirs.

At startup, each command prints the raw CLI argv plus every resolved execution
and workflow configuration field with an `[INFO]` prefix. This includes paths,
selection and mode settings, boolean switches, inherited rerun/analysis values,
and simulator arguments.

## Workdir and outputs

Each execution workdir contains only `dv/`, `vendor/`, `rtl/`, `shared/`, root
`*.core` files, `Cargo.lock`, and `Cargo.toml`. Every run produces a timestamped
log directory and `run_status.tsv`. Child process groups are tracked so an
interrupt can terminate active patch/build/SBFL processes.

## Development

```bash
uv run --project tools/ibex_sbfl_batch --all-groups --frozen \
  pytest -q tools/ibex_sbfl_batch/tests
uv run --project tools/ibex_sbfl_batch --all-groups --frozen ruff check tools/ibex_sbfl_batch
uv run --project tools/ibex_sbfl_batch --all-groups --frozen ruff format --check tools/ibex_sbfl_batch
```
