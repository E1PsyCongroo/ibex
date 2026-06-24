#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage:
  run_bugset_sbfl.sh --all <bugset_root> [options]
  run_bugset_sbfl.sh --case <case_dir_or_diff> [options]

Options:
  -j, --jobs <N>        Parallel jobs, default: nproc
  -t, --tmp <DIR>       Temporary root directory, default: /tmp/run_bugset_sbfl
  -l, --logs <DIR>      Logs root directory, default: ./logs
  -w, --workdir <DIR>   Ibex workdir, default: $IBEX_HOME or current directory
  --keep-workdir        Keep per-case temporary workdirs for debugging
  -h, --help            Show this help

Examples:
  run_bugset_sbfl.sh --all bugset -j 8
  run_bugset_sbfl.sh --case bugset/dataset_0/0
  run_bugset_sbfl.sh --case bugset/dataset_0/0/ibex_decoder.sv.diff
  run_bugset_sbfl.sh --all dataset -j 4 -t /tmp/ibex_sbfl_tmp -l ./logs
EOF
}

MODE=""
TARGET=""

JOBS="$(nproc 2>/dev/null || echo 1)"
TMP_ROOT=""
LOGS_ROOT="./logs"
KEEP_WORKDIR=0
IBEX_HOME="${IBEX_HOME:-$(pwd)}"

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
  -j | --jobs)
    [[ "$#" -ge 2 ]] || {
      usage
      exit 1
    }
    JOBS="$2"
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
    IBEX_HOME="$2"
    shift 2
    ;;
  --keep-workdir)
    KEEP_WORKDIR=1
    shift
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

if [[ -z "${MODE}" || -z "${TARGET}" ]]; then
  usage
  exit 1
fi

if ! [[ "${JOBS}" =~ ^[0-9]+$ ]] || ((JOBS <= 0)); then
  echo "[ERROR] jobs must be a positive integer: ${JOBS}" >&2
  exit 1
fi

if ! command -v realpath >/dev/null 2>&1; then
  echo "[ERROR] realpath not found in PATH" >&2
  exit 1
fi

IBEX_HOME="$(realpath "${IBEX_HOME}")"
TARGET="$(realpath "${TARGET}")"

if [[ -z "${TMP_ROOT}" ]]; then
  TMP_ROOT="${TMPDIR:-/tmp}/run_bugset_sbfl"
fi

mkdir -p "${TMP_ROOT}"
mkdir -p "${LOGS_ROOT}"

TMP_ROOT="$(realpath "${TMP_ROOT}")"
LOGS_ROOT="$(realpath "${LOGS_ROOT}")"

RUN_ID="$(date +"%Y-%m-%d-%H-%M-%S")_$$"
RUN_TMP="${TMP_ROOT}/${RUN_ID}"
WORK_ROOT="${RUN_TMP}/work"
LOCK_FILE="${RUN_TMP}/status.lock"
SUMMARY_FILE="${LOGS_ROOT}/run_${RUN_ID}_status.tsv"

mkdir -p "${WORK_ROOT}"

case "${RUN_TMP}/" in
"${IBEX_HOME}/"*)
  echo "[ERROR] tmp directory must not be inside IBEX_HOME" >&2
  echo "        IBEX_HOME=${IBEX_HOME}" >&2
  echo "        RUN_TMP=${RUN_TMP}" >&2
  exit 1
  ;;
esac

BUGSET_ROOT=""

SBFL_BIN="build/lowrisc_ibex_ibex_simple_system_sbfl_0/sim-verilator/Vibex_simple_system"
OBJDUMP_BIN="${OBJDUMP_BIN:-riscv32-unknown-elf-objdump}"

FUSESOC_CMD=(
  fusesoc --cores-root=. run
  --target=sim
  --setup
  --build
  lowrisc:ibex:ibex_simple_system_sbfl
  --RV32E=0
  --RV32M=ibex_pkg::RV32MFast
)

CHILD_PIDS=()

on_interrupt() {
  echo
  echo "[INTERRUPT] Ctrl-C received, stopping children..."

  trap - INT TERM

  if ((${#CHILD_PIDS[@]} > 0)); then
    kill "${CHILD_PIDS[@]}" 2>/dev/null || true
    wait 2>/dev/null || true
  fi

  exit 130
}

trap on_interrupt INT TERM

printf "case_index\trel_dir\tdiff_name\tcase_dir\tdiff\tstatus\trc\tlogdir\tworkdir\n" >"${SUMMARY_FILE}"

append_result() {
  local idx="$1"
  local rel_dir="$2"
  local diff_name="$3"
  local case_dir="$4"
  local diff="$5"
  local status="$6"
  local rc="$7"
  local logdir="$8"
  local workdir="$9"

  {
    flock 200
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "${idx}" \
      "${rel_dir}" \
      "${diff_name}" \
      "${case_dir}" \
      "${diff}" \
      "${status}" \
      "${rc}" \
      "${logdir}" \
      "${workdir}" >>"${SUMMARY_FILE}"
  } 200>"${LOCK_FILE}"
}

copy_workdir() {
  local dst="$1"

  mkdir -p "${dst}"

  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete \
      --exclude '/build/' \
      --exclude '/logs/' \
      --exclude '/.git/' \
      --exclude '/target/' \
      "${IBEX_HOME}/" "${dst}/"
  else
    cp -a "${IBEX_HOME}/." "${dst}/"
    rm -rf -- "${dst}/build" "${dst}/logs" "${dst}/.git" "${dst}/target"
  fi
}

cleanup_case_workdir() {
  local workdir="$1"
  local logdir="$2"

  if [[ "${KEEP_WORKDIR}" -eq 1 ]]; then
    echo "[KEEP] workdir: ${workdir}" >>"${logdir}/run.log"
    return 0
  fi

  if [[ -n "${workdir}" && -d "${workdir}" ]]; then
    echo "[CLEAN] remove workdir: ${workdir}" >>"${logdir}/run.log"
    rm -rf -- "${workdir}"
  fi
}

disassemble_elfs() {
  local root="$1"

  if ! command -v "${OBJDUMP_BIN}" >/dev/null 2>&1; then
    echo "[WARN] ${OBJDUMP_BIN} not found in PATH, skip disassemble"
    return 0
  fi

  local found=0

  while IFS= read -r -d '' elf; do
    found=1

    local dis
    case "${elf}" in
    *.elf)
      dis="${elf%.elf}.dis"
      ;;
    *.ELF)
      dis="${elf%.ELF}.dis"
      ;;
    *)
      dis="${elf}.dis"
      ;;
    esac

    echo "[OBJDUMP] ${elf} -> ${dis}"

    if ! "${OBJDUMP_BIN}" -SD "${elf}" >"${dis}"; then
      echo "[WARN] objdump failed: ${elf}" >&2
      rm -f "${dis}"
    fi
  done < <(
    find "${root}" \
      -type f \
      \( -name "*.elf" -o -name "*.ELF" \) \
      -print0 | sort -z
  )

  if [[ "${found}" -eq 0 ]]; then
    echo "[OBJDUMP] no ELF files found under ${root}"
  fi
}

safe_path_name() {
  local s="$1"

  s="$(echo "${s}" | sed 's#[/[:space:]]#_#g')"
  s="$(echo "${s}" | sed 's#[^A-Za-z0-9._-]#_#g')"

  if [[ -z "${s}" || "${s}" == "." ]]; then
    s="root"
  fi

  echo "${s}"
}

finish_case() {
  local idx="$1"
  local rel_dir="$2"
  local diff_name="$3"
  local case_dir="$4"
  local diff="$5"
  local status="$6"
  local rc="$7"
  local logdir="$8"
  local workdir="$9"

  echo "[${status}] ${rel_dir}/${diff_name}, status=${rc}" | tee -a "${logdir}/status.txt"

  append_result \
    "${idx}" \
    "${rel_dir}" \
    "${diff_name}" \
    "${case_dir}" \
    "${diff}" \
    "${status}" \
    "${rc}" \
    "${logdir}" \
    "${workdir}"

  echo "[DONE][${idx}] ${rel_dir}/${diff_name}: ${status}, status=${rc}"
}

run_one_diff() (
  local idx="$1"
  local diff="$2"

  diff="$(realpath "${diff}")"

  local case_dir
  case_dir="$(dirname "${diff}")"

  local diff_name
  diff_name="$(basename "${diff}")"

  local rel_dir
  if [[ -n "${BUGSET_ROOT}" ]]; then
    rel_dir="$(realpath --relative-to="${BUGSET_ROOT}" "${case_dir}" 2>/dev/null || basename "${case_dir}")"
  else
    rel_dir="$(basename "${case_dir}")"
  fi

  if [[ -z "${rel_dir}" || "${rel_dir}" == "." ]]; then
    rel_dir="$(basename "${case_dir}")"
  fi

  local safe_rel_dir
  local safe_diff_name
  safe_rel_dir="$(safe_path_name "${rel_dir}")"
  safe_diff_name="$(safe_path_name "${diff_name%.sv.diff}")"

  local logdir="${LOGS_ROOT}/${safe_rel_dir}/${idx}_${safe_diff_name}/${RUN_ID}"
  local workdir="${WORK_ROOT}/${idx}_${safe_rel_dir}_${safe_diff_name}"

  mkdir -p "${logdir}"

  trap 'cleanup_case_workdir "${workdir}" "${logdir}"' EXIT

  echo "============================================================" >"${logdir}/run.log"
  echo "[CASE] ${idx}" >>"${logdir}/run.log"
  echo "[REL ] ${rel_dir}" >>"${logdir}/run.log"
  echo "[DIR ] ${case_dir}" >>"${logdir}/run.log"
  echo "[DIFF] ${diff}" >>"${logdir}/run.log"
  echo "[LOG ] ${logdir}" >>"${logdir}/run.log"
  echo "[WORK] ${workdir}" >>"${logdir}/run.log"
  echo "============================================================" >>"${logdir}/run.log"

  echo "[START][${idx}] ${rel_dir}/${diff_name}"

  echo "[COPY] ${IBEX_HOME} -> ${workdir}" >>"${logdir}/run.log"

  if copy_workdir "${workdir}" >"${logdir}/copy.log" 2>&1; then
    :
  else
    local rc=$?
    finish_case "${idx}" "${rel_dir}" "${diff_name}" "${case_dir}" "${diff}" "COPY_FAIL" "${rc}" "${logdir}" "${workdir}"
    return 0
  fi

  echo "[APPLY] ${diff}" >>"${logdir}/run.log"

  if (
    cd "${workdir}"
    git apply --whitespace=nowarn "${diff}"
  ) >"${logdir}/git_apply.log" 2>&1; then
    :
  else
    local rc=$?
    finish_case "${idx}" "${rel_dir}" "${diff_name}" "${case_dir}" "${diff}" "APPLY_FAIL" "${rc}" "${logdir}" "${workdir}"
    return 0
  fi

  echo "[BUILD] fusesoc build after applying patch" >>"${logdir}/run.log"

  if (
    cd "${workdir}"
    "${FUSESOC_CMD[@]}"
  ) >"${logdir}/build.log" 2>&1; then
    :
  else
    local rc=$?
    finish_case "${idx}" "${rel_dir}" "${diff_name}" "${case_dir}" "${diff}" "BUILD_FAIL" "${rc}" "${logdir}" "${workdir}"
    return 0
  fi

  if [[ ! -x "${workdir}/${SBFL_BIN}" ]]; then
    echo "[ERROR] SBFL binary not found or not executable: ${workdir}/${SBFL_BIN}" >>"${logdir}/run.log"
    finish_case "${idx}" "${rel_dir}" "${diff_name}" "${case_dir}" "${diff}" "SBFL_BIN_MISSING" 1 "${logdir}" "${workdir}"
    return 0
  fi

  echo "[RUN] SBFL" >>"${logdir}/run.log"

  local include_paths
  include_paths="${workdir}/vendor/lowrisc_ip/ip/prim/rtl/,${workdir}/vendor/lowrisc_ip/dv/sv/dv_utils/"

  set +e
  (
    cd "${workdir}"

    "${SBFL_BIN}" \
      -f \
      -r \
      -c "verilator.branch,verilator.line" \
      --max-run-timeout 60 \
      --max-iters 50 \
      --top-pass 100 \
      --top-sus 50 \
      --corpus-input examples/sw/benchmarks/coremark/coremark.elf \
      --output "${logdir}" \
      --save-reduce \
      --rtl-path "${workdir}/rtl" \
      --include-paths "${include_paths}" \
      --top-module ibex_core \
      --top-scope TOP.ibex_simple_system.u_top.u_ibex_top.u_ibex_core \
      -- -c 1000000
  ) >"${logdir}/sbfl.log" 2>&1
  local sbfl_status=$?
  set -e

  echo "[POST] disassemble ELF files under ${logdir}" >>"${logdir}/run.log"
  disassemble_elfs "${logdir}" >"${logdir}/objdump.log" 2>&1 || true

  if [[ "${sbfl_status}" -ne 0 ]]; then
    finish_case "${idx}" "${rel_dir}" "${diff_name}" "${case_dir}" "${diff}" "SBFL_FAIL" "${sbfl_status}" "${logdir}" "${workdir}"
    return 0
  fi

  finish_case "${idx}" "${rel_dir}" "${diff_name}" "${case_dir}" "${diff}" "OK" 0 "${logdir}" "${workdir}"
  return 0
)

run_all_cases() {
  local bugset_root="$1"

  if [[ ! -d "${bugset_root}" ]]; then
    echo "[ERROR] bugset root not found: ${bugset_root}" >&2
    return 1
  fi

  BUGSET_ROOT="$(realpath "${bugset_root}")"

  mapfile -d '' DIFFS < <(
    find "${BUGSET_ROOT}" \
      -type f \
      -name "*.sv.diff" \
      -print0 | sort -z
  )

  if ((${#DIFFS[@]} == 0)); then
    echo "[WARN] no .sv.diff files found under: ${BUGSET_ROOT}"
    return 0
  fi

  echo "[INFO] diffs  : ${#DIFFS[@]}"
  echo "[INFO] jobs   : ${JOBS}"
  echo "[INFO] logs   : ${LOGS_ROOT}"
  echo "[INFO] tmp    : ${RUN_TMP}"
  echo "[INFO] summary: ${SUMMARY_FILE}"
  echo "[INFO] keep workdir: ${KEEP_WORKDIR}"

  local running=0

  for idx in "${!DIFFS[@]}"; do
    run_one_diff "${idx}" "${DIFFS[$idx]}" &
    CHILD_PIDS+=("$!")
    running=$((running + 1))

    if ((running >= JOBS)); then
      wait -n || true
      running=$((running - 1))
    fi
  done

  while ((running > 0)); do
    wait -n || true
    running=$((running - 1))
  done
}

run_single_case() {
  local target="$1"

  echo "[INFO] jobs   : 1"
  echo "[INFO] logs   : ${LOGS_ROOT}"
  echo "[INFO] tmp    : ${RUN_TMP}"
  echo "[INFO] summary: ${SUMMARY_FILE}"
  echo "[INFO] keep workdir: ${KEEP_WORKDIR}"

  if [[ -f "${target}" && "${target}" == *.sv.diff ]]; then
    BUGSET_ROOT="$(dirname "$(realpath "${target}")")"
    run_one_diff 0 "${target}"
    return 0
  fi

  if [[ -d "${target}" ]]; then
    BUGSET_ROOT="$(realpath "${target}")"

    local diff
    diff="$(find "${BUGSET_ROOT}" -type f -name "*.sv.diff" | sort | head -n 1)"

    if [[ -z "${diff}" ]]; then
      echo "[SKIP] no .sv.diff found under: ${target}"
      return 0
    fi

    echo "[INFO] selected diff: ${diff}"
    run_one_diff 0 "${diff}"
    return 0
  fi

  echo "[ERROR] --case target is neither .sv.diff nor directory: ${target}" >&2
  return 1
}

check_final_status() {
  local total
  local failed

  total="$(awk -F'\t' 'NR > 1 { c++ } END { print c + 0 }' "${SUMMARY_FILE}")"
  failed="$(awk -F'\t' 'NR > 1 && $6 != "OK" { c++ } END { print c + 0 }' "${SUMMARY_FILE}")"

  echo "============================================================"
  echo "[SUMMARY] total=${total}, failed=${failed}"
  echo "[SUMMARY] status file: ${SUMMARY_FILE}"
  echo "[SUMMARY] tmp dir    : ${RUN_TMP}"
  echo "============================================================"

  if ((failed != 0)); then
    echo "[DONE] some cases failed"
    return 1
  fi

  echo "[DONE] all cases passed"
  return 0
}

main() {
  if ! command -v fusesoc >/dev/null 2>&1; then
    echo "[ERROR] fusesoc not found in PATH" >&2
    exit 1
  fi

  if ! command -v git >/dev/null 2>&1; then
    echo "[ERROR] git not found in PATH" >&2
    exit 1
  fi

  if [[ ! -d "${IBEX_HOME}/rtl" ]]; then
    echo "[ERROR] IBEX_HOME seems wrong: ${IBEX_HOME}" >&2
    echo "        expected directory: ${IBEX_HOME}/rtl" >&2
    exit 1
  fi

  case "${MODE}" in
  all)
    run_all_cases "${TARGET}"
    ;;
  case)
    run_single_case "${TARGET}"
    ;;
  *)
    usage
    exit 1
    ;;
  esac

  check_final_status
}

main
