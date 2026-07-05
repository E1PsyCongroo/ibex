#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  run_mutator_window_size_sweep_sbfl.sh --all <bugset_root> --mutator-window-size <LIST> [options] [-- run_bugset_sbfl_args...]
  run_mutator_window_size_sweep_sbfl.sh --case <case_dir_or_diff> --mutator-window-size <LIST> [options] [-- run_bugset_sbfl_args...]

Options:
  --mutator-window-size <LIST>   Comma-separated or quoted space-separated positive integers, e.g. 5,10,20
  --mutator-window-sizes <LIST>  Alias for --mutator-window-size
  --sweep-jobs <N>               Parallel window-size runs, default: 1
  -j, --jobs <N>                 Per-window run_bugset_sbfl.sh jobs, forwarded to run_bugset_sbfl.sh
  -t, --tmp <DIR>                Sweep temporary root, default: /tmp/run_mutator_window_size_sweep_sbfl
  -l, --logs <DIR>               Sweep logs root, default: ./logs/mutator_window_size_sweep
  -w, --workdir <DIR>            Ibex workdir, forwarded to run_bugset_sbfl.sh
  --line-window <N>              summarize_sbfl_blocks.py line window, default: 0
  --summary-bugset-root <DIR>    Bugset root for summarize_sbfl_blocks.py.
                                 Default: --all target, or inferred parent bugset for --case.
  --run-script <PATH>            run_bugset_sbfl.sh path, default: sibling script
  --summarize-script <PATH>      summarize_sbfl_blocks.py path, default: sibling script
  -h, --help                     Show this help

Any arguments after -- are forwarded to run_bugset_sbfl.sh. This is where you
can pass SBFL options such as --max-iters, --top-sus, or run_bugset_sbfl.sh's
own -- separator:

  run_mutator_window_size_sweep_sbfl.sh --all verify_dataset \
    --mutator-window-size 5,10,20 \
    --sweep-jobs 2 -j 4 \
    -- --max-iters 20 --top-sus 50 -- -c 10000000

Outputs:
  <logs>/<sweep_run_id>/sweep_status.tsv
  <logs>/<sweep_run_id>/mutator_window_size_block_summary.tsv
  <logs>/<sweep_run_id>/window_<N>/sbfl_block_summary.tsv
EOF
}

MODE=""
TARGET=""
WINDOW_SIZES=()
SWEEP_JOBS=1
INNER_JOBS=""
TMP_ROOT=""
LOGS_ROOT="./logs/mutator_window_size_sweep"
IBEX_HOME_ARG=""
LINE_WINDOW=0
SUMMARY_BUGSET_ROOT=""
RUN_SCRIPT=""
SUMMARIZE_SCRIPT=""
RUN_ARGS=()

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

append_window_sizes() {
  local list="$1"
  local item

  list="${list//,/ }"
  for item in ${list}; do
    if [[ -n "${item}" ]]; then
      WINDOW_SIZES+=("${item}")
    fi
  done
}

validate_positive_int() {
  local name="$1"
  local value="$2"

  if ! [[ "${value}" =~ ^[0-9]+$ ]] || ((value <= 0)); then
    echo "[ERROR] ${name} must be a positive integer: ${value}" >&2
    exit 1
  fi
}

validate_nonnegative_int() {
  local name="$1"
  local value="$2"

  if ! [[ "${value}" =~ ^[0-9]+$ ]]; then
    echo "[ERROR] ${name} must be a non-negative integer: ${value}" >&2
    exit 1
  fi
}

while [[ "$#" -gt 0 ]]; do
  case "$1" in
  --all)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    MODE="all"
    TARGET="$2"
    shift 2
    ;;
  --case)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    MODE="case"
    TARGET="$2"
    shift 2
    ;;
  --mutator-window-size | --mutator-window-sizes | --mutator-window-size-list)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    append_window_sizes "$2"
    shift 2
    ;;
  --sweep-jobs | --window-jobs)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    SWEEP_JOBS="$2"
    shift 2
    ;;
  -j | --jobs)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    INNER_JOBS="$2"
    shift 2
    ;;
  -t | --tmp)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    TMP_ROOT="$2"
    shift 2
    ;;
  -l | --logs)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    LOGS_ROOT="$2"
    shift 2
    ;;
  -w | --workdir)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    IBEX_HOME_ARG="$2"
    shift 2
    ;;
  --line-window)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    LINE_WINDOW="$2"
    shift 2
    ;;
  --summary-bugset-root)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    SUMMARY_BUGSET_ROOT="$2"
    shift 2
    ;;
  --run-script)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    RUN_SCRIPT="$2"
    shift 2
    ;;
  --summarize-script)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    SUMMARIZE_SCRIPT="$2"
    shift 2
    ;;
  --)
    shift
    RUN_ARGS+=("$@")
    break
    ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    echo "[ERROR] unknown argument: $1" >&2
    usage
    exit 1
    ;;
  esac
done

if [[ -z "${MODE}" || -z "${TARGET}" || "${#WINDOW_SIZES[@]}" -eq 0 ]]; then
  usage
  exit 1
fi

validate_positive_int "sweep jobs" "${SWEEP_JOBS}"
validate_nonnegative_int "line window" "${LINE_WINDOW}"

if [[ -n "${INNER_JOBS}" ]]; then
  validate_positive_int "jobs" "${INNER_JOBS}"
fi

for size in "${WINDOW_SIZES[@]}"; do
  validate_positive_int "mutator window size" "${size}"
done

declare -A SEEN_WINDOW_SIZES=()
for size in "${WINDOW_SIZES[@]}"; do
  if [[ -n "${SEEN_WINDOW_SIZES[${size}]:-}" ]]; then
    echo "[ERROR] duplicate mutator window size: ${size}" >&2
    exit 1
  fi

  SEEN_WINDOW_SIZES["${size}"]=1
done

if ! command -v realpath >/dev/null 2>&1; then
  echo "[ERROR] realpath not found in PATH" >&2
  exit 1
fi

if ! command -v flock >/dev/null 2>&1; then
  echo "[ERROR] flock not found in PATH" >&2
  exit 1
fi

RUN_SCRIPT="${RUN_SCRIPT:-${SCRIPT_DIR}/run_bugset_sbfl.sh}"
SUMMARIZE_SCRIPT="${SUMMARIZE_SCRIPT:-${SCRIPT_DIR}/summarize_sbfl_blocks.py}"

RUN_SCRIPT="$(realpath "${RUN_SCRIPT}")"
SUMMARIZE_SCRIPT="$(realpath "${SUMMARIZE_SCRIPT}")"
TARGET="$(realpath "${TARGET}")"

if [[ ! -f "${RUN_SCRIPT}" ]]; then
  echo "[ERROR] run script not found: ${RUN_SCRIPT}" >&2
  exit 1
fi

if [[ ! -f "${SUMMARIZE_SCRIPT}" ]]; then
  echo "[ERROR] summarize script not found: ${SUMMARIZE_SCRIPT}" >&2
  exit 1
fi

if [[ -z "${TMP_ROOT}" ]]; then
  TMP_ROOT="${TMPDIR:-/tmp}/run_mutator_window_size_sweep_sbfl"
fi

mkdir -p "${TMP_ROOT}" "${LOGS_ROOT}"
TMP_ROOT="$(realpath "${TMP_ROOT}")"
LOGS_ROOT="$(realpath "${LOGS_ROOT}")"

detect_summary_bugset_root() {
  if [[ -n "${SUMMARY_BUGSET_ROOT}" ]]; then
    realpath "${SUMMARY_BUGSET_ROOT}"
    return 0
  fi

  if [[ "${MODE}" == "all" ]]; then
    realpath "${TARGET}"
    return 0
  fi

  if [[ -f "${TARGET}" && "${TARGET}" == *.sv.diff ]]; then
    dirname "$(dirname "${TARGET}")"
    return 0
  fi

  if [[ -d "${TARGET}" ]]; then
    local first_diff
    first_diff="$(find "${TARGET}" -type f -name "*.sv.diff" | sort | head -n 1)"

    if [[ -z "${first_diff}" ]]; then
      echo "[ERROR] cannot infer summary bugset root; no .sv.diff found under: ${TARGET}" >&2
      return 1
    fi

    dirname "$(dirname "$(realpath "${first_diff}")")"
    return 0
  fi

  echo "[ERROR] cannot infer summary bugset root for target: ${TARGET}" >&2
  return 1
}

SUMMARY_BUGSET_ROOT="$(detect_summary_bugset_root)"
SUMMARY_BUGSET_ROOT="$(realpath "${SUMMARY_BUGSET_ROOT}")"

if [[ ! -d "${SUMMARY_BUGSET_ROOT}" ]]; then
  echo "[ERROR] summary bugset root not found: ${SUMMARY_BUGSET_ROOT}" >&2
  exit 1
fi

SWEEP_RUN_ID="$(date +"%Y-%m-%d-%H-%M-%S")_$$"
SWEEP_LOGS_ROOT="${LOGS_ROOT}/${SWEEP_RUN_ID}"
SWEEP_TMP_ROOT="${TMP_ROOT}/${SWEEP_RUN_ID}"
STATUS_FILE="${SWEEP_LOGS_ROOT}/sweep_status.tsv"
STATUS_LOCK="${SWEEP_LOGS_ROOT}/sweep_status.lock"
COMBINED_SUMMARY="${SWEEP_LOGS_ROOT}/mutator_window_size_block_summary.tsv"

mkdir -p "${SWEEP_LOGS_ROOT}" "${SWEEP_TMP_ROOT}"

printf "mutator_window_size\tstatus\trun_rc\tsummary_rc\telapsed_time\tlogs_root\ttmp_root\trun_log\tsummary_log\tsummary_tsv\n" >"${STATUS_FILE}"

format_elapsed_ms() {
  local total_ms="$1"
  local hours=$((total_ms / 3600000))
  local minutes=$(((total_ms % 3600000) / 60000))
  local seconds=$(((total_ms % 60000) / 1000))
  local milliseconds=$((total_ms % 1000))

  printf "%02d:%02d:%02d:%03d" \
    "${hours}" "${minutes}" "${seconds}" "${milliseconds}"
}

append_status() {
  local window_size="$1"
  local status="$2"
  local run_rc="$3"
  local summary_rc="$4"
  local elapsed_time="$5"
  local logs_root="$6"
  local tmp_root="$7"
  local run_log="$8"
  local summary_log="$9"
  local summary_tsv="${10}"

  {
    flock 200
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "${window_size}" \
      "${status}" \
      "${run_rc}" \
      "${summary_rc}" \
      "${elapsed_time}" \
      "${logs_root}" \
      "${tmp_root}" \
      "${run_log}" \
      "${summary_log}" \
      "${summary_tsv}" >>"${STATUS_FILE}"
  } 200>"${STATUS_LOCK}"
}

run_one_window_size() (
  local window_size="$1"
  local start_ms
  local end_ms
  local elapsed_ms
  local elapsed_time
  local window_root="${SWEEP_LOGS_ROOT}/window_${window_size}"
  local window_logs="${window_root}/sbfl_logs"
  local window_tmp="${SWEEP_TMP_ROOT}/window_${window_size}"
  local run_log="${window_root}/run_bugset_sbfl.log"
  local summary_log="${window_root}/summarize_sbfl_blocks.log"
  local summary_tsv="${window_root}/sbfl_block_summary.tsv"
  local run_rc
  local summary_rc
  local status
  local cmd=()

  start_ms="$(date +%s%3N)"

  mkdir -p "${window_logs}" "${window_tmp}"

  cmd=(bash "${RUN_SCRIPT}" "--${MODE}" "${TARGET}" --mutator-window-size "${window_size}" -t "${window_tmp}" -l "${window_logs}")

  if [[ -n "${INNER_JOBS}" ]]; then
    cmd+=(-j "${INNER_JOBS}")
  fi

  if [[ -n "${IBEX_HOME_ARG}" ]]; then
    cmd+=(-w "${IBEX_HOME_ARG}")
  fi

  if ((${#RUN_ARGS[@]} > 0)); then
    cmd+=("${RUN_ARGS[@]}")
  fi

  {
    echo "[WINDOW] mutator-window-size=${window_size}"
    printf '[RUN]'
    printf ' %q' "${cmd[@]}"
    printf '\n'
  } >"${run_log}"

  set +e
  "${cmd[@]}" >>"${run_log}" 2>&1
  run_rc=$?

  {
    printf '[RUN_RC] %s\n' "${run_rc}"
    printf '[SUMMARY] python3 %q %q %q -o %q --line-window %q\n' \
      "${SUMMARIZE_SCRIPT}" \
      "${SUMMARY_BUGSET_ROOT}" \
      "${window_logs}" \
      "${summary_tsv}" \
      "${LINE_WINDOW}"
  } >>"${run_log}"

  python3 "${SUMMARIZE_SCRIPT}" \
    "${SUMMARY_BUGSET_ROOT}" \
    "${window_logs}" \
    -o "${summary_tsv}" \
    --line-window "${LINE_WINDOW}" >"${summary_log}" 2>&1
  summary_rc=$?
  set -e

  if [[ "${run_rc}" -eq 0 && "${summary_rc}" -eq 0 ]]; then
    status="OK"
  elif [[ "${summary_rc}" -ne 0 ]]; then
    status="SUMMARY_FAIL"
  else
    status="RUN_FAIL"
  fi

  end_ms="$(date +%s%3N)"
  elapsed_ms=$((end_ms - start_ms))
  elapsed_time="$(format_elapsed_ms "${elapsed_ms}")"

  append_status \
    "${window_size}" \
    "${status}" \
    "${run_rc}" \
    "${summary_rc}" \
    "${elapsed_time}" \
    "${window_logs}" \
    "${window_tmp}" \
    "${run_log}" \
    "${summary_log}" \
    "${summary_tsv}"

  echo "[DONE][window=${window_size}] ${status}, run_rc=${run_rc}, summary_rc=${summary_rc}, elapsed=${elapsed_time}"
)

write_combined_summary() {
  local header_written=0
  local window_size
  local status
  local run_rc
  local summary_rc
  local elapsed_time
  local logs_root
  local tmp_root
  local run_log
  local summary_log
  local summary_tsv

  : >"${COMBINED_SUMMARY}"

  while IFS=$'\t' read -r window_size status run_rc summary_rc elapsed_time logs_root tmp_root run_log summary_log summary_tsv; do
    if [[ "${window_size}" == "mutator_window_size" ]]; then
      continue
    fi

    if [[ ! -f "${summary_tsv}" ]]; then
      continue
    fi

    if [[ "${header_written}" -eq 0 ]]; then
      awk 'BEGIN { FS = OFS = "\t" } NR == 1 { print "mutator_window_size", $0 }' "${summary_tsv}" >>"${COMBINED_SUMMARY}"
      header_written=1
    fi

    awk -v window_size="${window_size}" 'BEGIN { FS = OFS = "\t" } NR > 1 { print window_size, $0 }' "${summary_tsv}" >>"${COMBINED_SUMMARY}"
  done <"${STATUS_FILE}"

  if [[ "${header_written}" -eq 0 ]]; then
    printf "mutator_window_size\tbugset\tdiff\tstatus\ttop-k\tsus\telapsed_time\tfuzzing_time\n" >"${COMBINED_SUMMARY}"
  fi
}

CHILD_PIDS=()

on_interrupt() {
  echo
  echo "[INTERRUPT] stopping window-size runs..."

  trap - INT TERM

  if ((${#CHILD_PIDS[@]} > 0)); then
    kill "${CHILD_PIDS[@]}" 2>/dev/null || true
    wait 2>/dev/null || true
  fi

  exit 130
}

trap on_interrupt INT TERM

echo "[INFO] target              : ${TARGET}"
echo "[INFO] mode                : ${MODE}"
echo "[INFO] mutator windows     : ${WINDOW_SIZES[*]}"
echo "[INFO] sweep jobs          : ${SWEEP_JOBS}"
echo "[INFO] run_bugset jobs     : ${INNER_JOBS:-run_bugset_sbfl.sh default}"
echo "[INFO] logs root           : ${SWEEP_LOGS_ROOT}"
echo "[INFO] tmp root            : ${SWEEP_TMP_ROOT}"
echo "[INFO] summary bugset root : ${SUMMARY_BUGSET_ROOT}"
echo "[INFO] status file         : ${STATUS_FILE}"

running=0

for window_size in "${WINDOW_SIZES[@]}"; do
  run_one_window_size "${window_size}" &
  CHILD_PIDS+=("$!")
  running=$((running + 1))

  if ((running >= SWEEP_JOBS)); then
    wait -n || true
    running=$((running - 1))
  fi
done

while ((running > 0)); do
  wait -n || true
  running=$((running - 1))
done

write_combined_summary

total="$(awk -F'\t' 'NR > 1 { c++ } END { print c + 0 }' "${STATUS_FILE}")"
failed="$(awk -F'\t' 'NR > 1 && $2 != "OK" { c++ } END { print c + 0 }' "${STATUS_FILE}")"

echo "============================================================"
echo "[SUMMARY] window runs     : ${total}"
echo "[SUMMARY] failed          : ${failed}"
echo "[SUMMARY] status file     : ${STATUS_FILE}"
echo "[SUMMARY] combined summary: ${COMBINED_SUMMARY}"
echo "[SUMMARY] logs root       : ${SWEEP_LOGS_ROOT}"
echo "============================================================"

if ((failed != 0)); then
  exit 1
fi

exit 0
