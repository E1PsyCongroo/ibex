#!/usr/bin/env bash
set -euo pipefail

bugset_main() {
is_nonnegative_number() {
  [[ "$1" =~ ^([0-9]+([.][0-9]*)?|[.][0-9]+)$ ]]
}

validate_probability() {
  local name="$1"
  local value="$2"

  if ! is_nonnegative_number "${value}" || ! awk -v value="${value}" 'BEGIN { exit !(value >= 0 && value <= 1) }'; then
    echo "[ERROR] ${name} must be in range [0, 1]: ${value}" >&2
    exit 1
  fi
}

if [[ -z "${MODE}" || -z "${TARGET}" ]]; then
  usage
  exit 1
fi

if ! [[ "${JOBS}" =~ ^[0-9]+$ ]] || ((JOBS <= 0)); then
  echo "[ERROR] jobs must be a positive integer: ${JOBS}" >&2
  exit 1
fi

for pair in \
  "SBFL_MAX_RUN_TIMEOUT:${SBFL_MAX_RUN_TIMEOUT}" \
  "SBFL_MAX_ITERS:${SBFL_MAX_ITERS}" \
  "SBFL_TOP_PASS:${SBFL_TOP_PASS}" \
  "SBFL_TOP_SUS:${SBFL_TOP_SUS}" \
  "SBFL_TRACKER_WINDOW_SIZE:${SBFL_TRACKER_WINDOW_SIZE}"; do
  name="${pair%%:*}"
  value="${pair#*:}"
  if ! [[ "${value}" =~ ^[0-9]+$ ]] || ((value <= 0)); then
    echo "[ERROR] ${name} must be a positive integer: ${value}" >&2
    exit 1
  fi
done

case "${SBFL_MODE}" in
psbfl)
  if ! [[ "${SBFL_MUTATOR_WINDOW_SIZE}" =~ ^[0-9]+$ ]] || ((SBFL_MUTATOR_WINDOW_SIZE <= 0)); then
    echo "[ERROR] SBFL_MUTATOR_WINDOW_SIZE must be a positive integer: ${SBFL_MUTATOR_WINDOW_SIZE}" >&2
    exit 1
  fi
  case "${SBFL_MUTATOR_WEIGHT_STRATEGY}" in
  uniform | tail_linear | tail_quad | head_linear | head_quad) ;;
  *)
    echo "[ERROR] invalid mutator weight strategy: ${SBFL_MUTATOR_WEIGHT_STRATEGY}" >&2
    exit 1
    ;;
  esac
  ;;
withw)
  if ! [[ "${SBFL_MAX_CORPUS_SIZE}" =~ ^[0-9]+$ ]] || ((SBFL_MAX_CORPUS_SIZE <= 0)); then
    echo "[ERROR] SBFL_MAX_CORPUS_SIZE must be a positive integer: ${SBFL_MAX_CORPUS_SIZE}" >&2
    exit 1
  fi
  validate_probability SBFL_INIT_SEED_RATE "${SBFL_INIT_SEED_RATE}"
  validate_probability SBFL_MUTATE_RATE "${SBFL_MUTATE_RATE}"
  validate_probability SBFL_PRIORITY_ALPHA "${SBFL_PRIORITY_ALPHA}"
  if ! is_nonnegative_number "${SBFL_FAILED_REWARD}"; then
    echo "[ERROR] SBFL_FAILED_REWARD must be non-negative: ${SBFL_FAILED_REWARD}" >&2
    exit 1
  fi
  ;;
*)
  echo "[ERROR] unsupported SBFL mode: ${SBFL_MODE}" >&2
  exit 1
  ;;
esac

validate_probability SBFL_COVER_DISTANCE_WEIGHT "${SBFL_COVER_DISTANCE_WEIGHT}"

case "${SBFL_SELECTION}" in
random | sort)
  ;;
*)
  echo "[ERROR] SBFL_SELECTION has invalid value: ${SBFL_SELECTION}" >&2
  echo "        expected one of: random, sort" >&2
  exit 1
  ;;
esac

if ! command -v realpath >/dev/null 2>&1; then
  echo "[ERROR] realpath not found in PATH" >&2
  exit 1
fi

IBEX_HOME="$(realpath "${IBEX_HOME}")"
TARGET="$(realpath "${TARGET}")"

if [[ -z "${TMP_ROOT}" ]]; then
  TMP_ROOT="${TMPDIR:-/tmp}/${SCRIPT_NAME%.sh}"
fi

mkdir -p "${TMP_ROOT}"
mkdir -p "${LOGS_ROOT}"

TMP_ROOT="$(realpath "${TMP_ROOT}")"
LOGS_ROOT="$(realpath "${LOGS_ROOT}")"

RUN_ID="$(date +"%Y-%m-%d-%H-%M-%S")_$$"
RUN_TMP="${TMP_ROOT}/${RUN_ID}"
WORK_ROOT="${RUN_TMP}/work"
LOCK_FILE="${RUN_TMP}/status.lock"
SUMMARY_FILE="${LOGS_ROOT}/${RUN_ID}/run_status.tsv"

mkdir -p "${WORK_ROOT}"
mkdir -p "${LOGS_ROOT}/${RUN_ID}"

case "${RUN_TMP}/" in
"${IBEX_HOME}/"*)
  echo "[ERROR] tmp directory must not be inside IBEX_HOME" >&2
  echo "        IBEX_HOME=${IBEX_HOME}" >&2
  echo "        RUN_TMP=${RUN_TMP}" >&2
  exit 1
  ;;
esac

BUGSET_ROOT=""

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

printf "case_index\trel_dir\tdiff_name\tcase_dir\tdiff\tstatus\trc\telapsed_time\tlogdir\tworkdir\n" >"${SUMMARY_FILE}"

append_result() {
  local idx="$1"
  local rel_dir="$2"
  local diff_name="$3"
  local case_dir="$4"
  local diff="$5"
  local status="$6"
  local rc="$7"
  local elapsed_time="$8"
  local logdir="$9"
  local workdir="${10}"

  {
    flock 200
    printf "%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n" \
      "${idx}" \
      "${rel_dir}" \
      "${diff_name}" \
      "${case_dir}" \
      "${diff}" \
      "${status}" \
      "${rc}" \
      "${elapsed_time}" \
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
      --exclude '/.git' \
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

resolve_workdir_path() {
  local workdir="$1"
  local path="$2"

  if [[ "${path}" = /* ]]; then
    echo "${path}"
  else
    echo "${workdir}/${path}"
  fi
}

format_elapsed_ms() {
  local total_ms="$1"
  local hours=$((total_ms / 3600000))
  local minutes=$(((total_ms % 3600000) / 60000))
  local seconds=$(((total_ms % 60000) / 1000))
  local milliseconds=$((total_ms % 1000))

  printf "%02d:%02d:%02d:%03d" \
    "${hours}" "${minutes}" "${seconds}" "${milliseconds}"
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
  local end_ms
  local elapsed_ms
  local elapsed_time

  end_ms="$(date +%s%3N)"
  elapsed_ms=$((end_ms - case_start_ms))
  elapsed_time="$(format_elapsed_ms "${elapsed_ms}")"

  echo "[${status}] ${rel_dir}/${diff_name}, status=${rc}, elapsed=${elapsed_time}" | tee -a "${logdir}/status.txt"
  echo "[TIME] elapsed=${elapsed_time}" >>"${logdir}/run.log"

  append_result \
    "${idx}" \
    "${rel_dir}" \
    "${diff_name}" \
    "${case_dir}" \
    "${diff}" \
    "${status}" \
    "${rc}" \
    "${elapsed_time}" \
    "${logdir}" \
    "${workdir}"

  echo "[DONE][${idx}] ${rel_dir}/${diff_name}: ${status}, status=${rc}, elapsed=${elapsed_time}"
}

run_one_diff() (
  local idx="$1"
  local diff="$2"
  local case_start_ms
  case_start_ms="$(date +%s%3N)"

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

  local logdir="${LOGS_ROOT}/${RUN_ID}/${idx}_${safe_rel_dir}_${safe_diff_name}"
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
    patch -p1 --forward --batch --input "${diff}"
  ) >"${logdir}/patch.log" 2>&1; then
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

  local sbfl_exe
  sbfl_exe="$(resolve_workdir_path "${workdir}" "${SBFL_BIN}")"

  if [[ ! -x "${sbfl_exe}" ]]; then
    echo "[ERROR] SBFL binary not found or not executable: ${sbfl_exe}" >>"${logdir}/run.log"
    finish_case "${idx}" "${rel_dir}" "${diff_name}" "${case_dir}" "${diff}" "SBFL_BIN_MISSING" 1 "${logdir}" "${workdir}"
    return 0
  fi

  echo "[RUN] SBFL" >>"${logdir}/run.log"

  local rtl_path
  local include_paths
  rtl_path="${SBFL_RTL_PATH:-${workdir}/rtl}"
  include_paths="${SBFL_INCLUDE_PATHS:-${workdir}/vendor/lowrisc_ip/ip/prim/rtl/,${workdir}/vendor/lowrisc_ip/dv/sv/dv_utils/}"

  local sbfl_args=(
    -c "${SBFL_COVERAGE}"
    -s "${SBFL_STATE}"
    sbfl
  )

  if [[ "${SBFL_REDUCE}" -eq 1 ]]; then
    sbfl_args+=(--reduce-insts)
  fi

  if [[ "${SBFL_REDUCE_COVER}" -eq 1 ]]; then
    sbfl_args+=(--reduce-cover)
  fi

  sbfl_args+=(
    --max-run-timeout "${SBFL_MAX_RUN_TIMEOUT}"
    --max-iters "${SBFL_MAX_ITERS}"
    --top-pass "${SBFL_TOP_PASS}"
    --selection "${SBFL_SELECTION}"
    --top-sus "${SBFL_TOP_SUS}"
    --tracker-window-size "${SBFL_TRACKER_WINDOW_SIZE}"
    --cover-distance-weight "${SBFL_COVER_DISTANCE_WEIGHT}"
    --corpus-input "${SBFL_CORPUS_INPUT}"
    --output "${logdir}"
  )

  if [[ "${SBFL_SAVE_REDUCE}" -eq 1 ]]; then
    sbfl_args+=(--save-reduce)
  fi

  if [[ "${SBFL_SAVE_TRACE}" -eq 1 ]]; then
    sbfl_args+=(--save-trace)
  fi

  if [[ -n "${rtl_path}" ]]; then
    sbfl_args+=(--rtl-path "${rtl_path}")
  fi

  if [[ -n "${include_paths}" ]]; then
    sbfl_args+=(--include-paths "${include_paths}")
  fi

  if [[ -n "${SBFL_TOP_MODULE}" ]]; then
    sbfl_args+=(--top-module "${SBFL_TOP_MODULE}")
  fi

  if [[ -n "${SBFL_TOP_SCOPE}" ]]; then
    sbfl_args+=(--top-scope "${SBFL_TOP_SCOPE}")
  fi

  if [[ -n "${SBFL_METRIC}" ]]; then
    sbfl_args+=(--metric "${SBFL_METRIC}")
  fi

  case "${SBFL_MODE}" in
  psbfl)
    sbfl_args+=(
      psbfl
      --mutator-window-size "${SBFL_MUTATOR_WINDOW_SIZE}"
      --mutator-weight-strategy "${SBFL_MUTATOR_WEIGHT_STRATEGY}"
    )
    ;;
  withw)
    sbfl_args+=(
      wit-hw
      --max-corpus-size "${SBFL_MAX_CORPUS_SIZE}"
      --init-seed-rate "${SBFL_INIT_SEED_RATE}"
      --mutate-rate "${SBFL_MUTATE_RATE}"
      --priority-alpha "${SBFL_PRIORITY_ALPHA}"
      --failed-reward "${SBFL_FAILED_REWARD}"
    )
    ;;
  esac

  if ((${#SBFL_EXTRA_ARGS[@]} > 0)); then
    sbfl_args+=(-- "${SBFL_EXTRA_ARGS[@]}")
  fi

  {
    printf '[RUN]'
    printf ' %q' "${sbfl_exe}" "${sbfl_args[@]}"
    printf '\n'
  } >>"${logdir}/run.log"

  set +e
  (
    cd "${workdir}"
    "${sbfl_exe}" "${sbfl_args[@]}"
  ) >"${logdir}/sbfl.log" 2>&1
  local sbfl_status=$?
  set -e

  if [[ "${DO_DISASSEMBLE}" -eq 1 ]]; then
    echo "[POST] disassemble ELF files under ${logdir}" >>"${logdir}/run.log"
    disassemble_elfs "${logdir}" >"${logdir}/objdump.log" 2>&1 || true
  else
    echo "[POST] skip disassemble ELF files (--disassemble not set)" >>"${logdir}/run.log"
  fi

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

  echo "[INFO] diffs       : ${#DIFFS[@]}"
  echo "[INFO] jobs        : ${JOBS}"
  echo "[INFO] logs        : ${LOGS_ROOT}"
  echo "[INFO] tmp         : ${RUN_TMP}"
  echo "[INFO] summary     : ${SUMMARY_FILE}"
  echo "[INFO] keep workdir: ${KEEP_WORKDIR}"
  echo "[INFO] disassemble : ${DO_DISASSEMBLE}"
  echo "[INFO] save trace  : ${SBFL_SAVE_TRACE}"
  echo "[INFO] SBFL mode   : ${SBFL_MODE}"
  echo "[INFO] tracker win : ${SBFL_TRACKER_WINDOW_SIZE}"
  if [[ "${SBFL_MODE}" == "psbfl" ]]; then
    echo "[INFO] mutator win : ${SBFL_MUTATOR_WINDOW_SIZE}"
    echo "[INFO] mutator wgt : ${SBFL_MUTATOR_WEIGHT_STRATEGY}"
  else
    echo "[INFO] corpus limit: ${SBFL_MAX_CORPUS_SIZE}"
    echo "[INFO] seed rate   : ${SBFL_INIT_SEED_RATE}"
    echo "[INFO] mutate rate : ${SBFL_MUTATE_RATE}"
  fi
  echo "[INFO] selection   : ${SBFL_SELECTION}"
  echo "[INFO] cover wgt   : ${SBFL_COVER_DISTANCE_WEIGHT}"

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

  echo "[INFO] jobs        : 1"
  echo "[INFO] logs        : ${LOGS_ROOT}"
  echo "[INFO] tmp         : ${RUN_TMP}"
  echo "[INFO] summary     : ${SUMMARY_FILE}"
  echo "[INFO] keep workdir: ${KEEP_WORKDIR}"
  echo "[INFO] disassemble : ${DO_DISASSEMBLE}"
  echo "[INFO] save trace  : ${SBFL_SAVE_TRACE}"
  echo "[INFO] SBFL mode   : ${SBFL_MODE}"
  echo "[INFO] tracker win : ${SBFL_TRACKER_WINDOW_SIZE}"
  if [[ "${SBFL_MODE}" == "psbfl" ]]; then
    echo "[INFO] mutator win : ${SBFL_MUTATOR_WINDOW_SIZE}"
    echo "[INFO] mutator wgt : ${SBFL_MUTATOR_WEIGHT_STRATEGY}"
  else
    echo "[INFO] corpus limit: ${SBFL_MAX_CORPUS_SIZE}"
    echo "[INFO] seed rate   : ${SBFL_INIT_SEED_RATE}"
    echo "[INFO] mutate rate : ${SBFL_MUTATE_RATE}"
  fi
  echo "[INFO] selection   : ${SBFL_SELECTION}"
  echo "[INFO] cover wgt   : ${SBFL_COVER_DISTANCE_WEIGHT}"

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
}
