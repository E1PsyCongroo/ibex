#!/usr/bin/env bash
# shellcheck disable=SC2034 # Configuration variables are consumed by the sourced common runner.
set -euo pipefail

SCRIPT_NAME="$(basename "$0")"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SBFL_MODE=psbfl

usage() {
  cat >&2 <<EOF
Usage:
  ${SCRIPT_NAME} --all <bugset_root> [options]
  ${SCRIPT_NAME} --case <case_dir_or_diff> [options]

Execution options:
  -j, --jobs <N>                    Parallel jobs, default: nproc
  -t, --tmp <DIR>                   Temporary root, default: /tmp/${SCRIPT_NAME%.sh}
  -l, --logs <DIR>                  Logs root, default: ./logs
  -w, --workdir <DIR>               Ibex workdir, default: \$IBEX_HOME or current directory
  --keep-workdir                    Keep per-case workdirs
  --disassemble                     Disassemble output ELF files
  --sbfl-bin <PATH>                 SBFL binary path

Common SBFL options:
  -r, --reduce-insts, --reduce      Reduce instructions
  --reduce-cover                    Reduce coverage
  -c, --coverage <COVERAGE>         Coverage types
  -s, --state <STATE>               State types
  --max-run-timeout <N>             Default: 60
  --max-iters <N>                   Default: 50
  --top-pass <N>                    Default: 50
  --selection <STRATEGY>            Default: sort
                                      random, sort, diverse
  --selection-diversity-weight <W>  Diverse proximity/diversity tradeoff
                                      Default: 0.4
  --selection-pool-factor <N>       Diverse near-fail pool multiplier
                                      Default: 3
  --top-sus <N>                     Default: 50
  --corpus-input <PATH>             Initial ELF input
  --save-reduce                     Save reduced input, default: enabled
  --save-trace                      Save execution traces
  --tracker-window-size <N>         Default: 20
  --cover-distance-weight <W>       Default: 0.5
  --rtl-path <PATH>                 Default: <case_workdir>/rtl
  --include-paths <PATHS>           Comma-separated RTL include paths
  --top-module <MODULE>             Default: ibex_core
  --top-scope <SCOPE>               Default: TOP.ibex_simple_system.u_top.u_ibex_top.u_ibex_core
  --metric <METRIC>                 Default: ochiai

PSBFL options:
  --mutator-window-size <N>         Default: 20
  --mutator-weight-strategy <S>     Default: uniform
                                      uniform, tail_linear, tail_quad,
                                      head_linear, head_quad

Extra simulator arguments:
  -- [ARGS...]                      Forward ARGS to the SBFL mode's extra_args
                                    Default: -c 5000000; specifying -- overrides it

  -h, --help                        Show this help
EOF
}

need_value() {
  if [[ "$#" -lt 2 ]]; then
    echo "[ERROR] $1 requires a value" >&2
    usage
    exit 1
  fi
}

MODE=""
TARGET=""
JOBS="$(nproc 2>/dev/null || echo 1)"
TMP_ROOT=""
LOGS_ROOT="./logs"
KEEP_WORKDIR=0
DO_DISASSEMBLE=0
IBEX_HOME="${IBEX_HOME:-$(pwd)}"
SBFL_BIN="build/lowrisc_ibex_ibex_simple_system_sbfl_0/sim-verilator/Vibex_simple_system"
SBFL_REDUCE=0
SBFL_REDUCE_COVER=0
SBFL_COVERAGE="verilator.branch,verilator.line"
SBFL_STATE="PCState,ArchIntRegState,CSRState"
SBFL_MAX_RUN_TIMEOUT=60
SBFL_MAX_ITERS=50
SBFL_TOP_PASS=50
SBFL_SELECTION="sort"
SBFL_SELECTION_DIVERSITY_WEIGHT=0.4
SBFL_SELECTION_POOL_FACTOR=3
SBFL_TOP_SUS=50
SBFL_CORPUS_INPUT="examples/sw/benchmarks/coremark/coremark.elf"
SBFL_SAVE_REDUCE=1
SBFL_SAVE_TRACE=0
SBFL_TRACKER_WINDOW_SIZE=20
SBFL_COVER_DISTANCE_WEIGHT=0.5
SBFL_RTL_PATH=""
SBFL_INCLUDE_PATHS=""
SBFL_TOP_MODULE="ibex_core"
SBFL_TOP_SCOPE="TOP.ibex_simple_system.u_top.u_ibex_top.u_ibex_core"
SBFL_METRIC="ochiai"
SBFL_MUTATOR_WINDOW_SIZE=20
SBFL_MUTATOR_WEIGHT_STRATEGY="uniform"
SBFL_EXTRA_ARGS=(-c 5000000)

while (($#)); do
  case "$1" in
  --all) need_value "$@"; MODE=all; TARGET="$2"; shift 2 ;;
  --case) need_value "$@"; MODE=case; TARGET="$2"; shift 2 ;;
  -j | --jobs) need_value "$@"; JOBS="$2"; shift 2 ;;
  -t | --tmp) need_value "$@"; TMP_ROOT="$2"; shift 2 ;;
  -l | --logs) need_value "$@"; LOGS_ROOT="$2"; shift 2 ;;
  -w | --workdir) need_value "$@"; IBEX_HOME="$2"; shift 2 ;;
  --keep-workdir) KEEP_WORKDIR=1; shift ;;
  --disassemble) DO_DISASSEMBLE=1; shift ;;
  --sbfl-bin) need_value "$@"; SBFL_BIN="$2"; shift 2 ;;
  -r | --reduce | --reduce-insts) SBFL_REDUCE=1; shift ;;
  --reduce-cover) SBFL_REDUCE_COVER=1; shift ;;
  -c | --coverage) need_value "$@"; SBFL_COVERAGE="$2"; shift 2 ;;
  -s | --state) need_value "$@"; SBFL_STATE="$2"; shift 2 ;;
  --max-run-timeout) need_value "$@"; SBFL_MAX_RUN_TIMEOUT="$2"; shift 2 ;;
  --max-iters) need_value "$@"; SBFL_MAX_ITERS="$2"; shift 2 ;;
  --top-pass) need_value "$@"; SBFL_TOP_PASS="$2"; shift 2 ;;
  --selection) need_value "$@"; SBFL_SELECTION="$2"; shift 2 ;;
  --selection-diversity-weight) need_value "$@"; SBFL_SELECTION_DIVERSITY_WEIGHT="$2"; shift 2 ;;
  --selection-pool-factor) need_value "$@"; SBFL_SELECTION_POOL_FACTOR="$2"; shift 2 ;;
  --top-sus) need_value "$@"; SBFL_TOP_SUS="$2"; shift 2 ;;
  --corpus-input) need_value "$@"; SBFL_CORPUS_INPUT="$2"; shift 2 ;;
  --save-reduce) SBFL_SAVE_REDUCE=1; shift ;;
  --save-trace) SBFL_SAVE_TRACE=1; shift ;;
  --tracker-window-size) need_value "$@"; SBFL_TRACKER_WINDOW_SIZE="$2"; shift 2 ;;
  --cover-distance-weight) need_value "$@"; SBFL_COVER_DISTANCE_WEIGHT="$2"; shift 2 ;;
  --rtl-path) need_value "$@"; SBFL_RTL_PATH="$2"; shift 2 ;;
  --include-paths) need_value "$@"; SBFL_INCLUDE_PATHS="$2"; shift 2 ;;
  --top-module) need_value "$@"; SBFL_TOP_MODULE="$2"; shift 2 ;;
  --top-scope) need_value "$@"; SBFL_TOP_SCOPE="$2"; shift 2 ;;
  --metric) need_value "$@"; SBFL_METRIC="$2"; shift 2 ;;
  --mutator-window-size) need_value "$@"; SBFL_MUTATOR_WINDOW_SIZE="$2"; shift 2 ;;
  --mutator-weight-strategy) need_value "$@"; SBFL_MUTATOR_WEIGHT_STRATEGY="$2"; shift 2 ;;
  --) shift; SBFL_EXTRA_ARGS=("$@"); break ;;
  -h | --help) usage; exit 0 ;;
  *) echo "[ERROR] unknown PSBFL argument: $1" >&2; usage; exit 1 ;;
  esac
done

# shellcheck source=run_bugset_common.sh
source "${SCRIPT_DIR}/run_bugset_sbfl.sh"
bugset_main
