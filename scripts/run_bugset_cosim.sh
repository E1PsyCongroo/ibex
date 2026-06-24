#!/usr/bin/env bash
set -euo pipefail

usage() {
    cat >&2 <<'EOF'
Usage:
  run_bugset_cosim.sh \
    -w <workdir> \
    -b <bugset_dir> \
    -t <tmp_dir> \
    -o <out_dir> \
    [-j <jobs>] \
    [--max-run-time <TIME>] \
    [--keep-workdir]

Options:
  -w, --workdir <DIR>       Ibex workdir
  -b, --bugset <DIR>        Bugset directory, recursively search *.sv.diff
  -t, --tmp <DIR>           Temporary root directory
  -o, --output <DIR>        Output directory for failed/error cases
  -j, --jobs <N>            Parallel jobs, default: nproc

  --max-run-time <TIME>     Max simulation runtime, default: 5m
                            Examples: 300s, 5m, 1h
                            This value is passed to GNU timeout.

  --keep-workdir            Keep per-case temporary workdirs for debugging
  -h, --help                Show this help

Example:
  ./run_bugset_cosim.sh \
    -w /home/user/ibex \
    -b /home/user/dataset \
    -t /tmp/ibex_cosim_runs \
    -o /home/user/failed_cases \
    -j 8 \
    --max-run-time 5m
EOF
}

WORKDIR=""
BUGSET_DIR=""
TMP_ROOT=""
OUTDIR=""
JOBS="$(nproc 2>/dev/null || echo 1)"
KEEP_WORKDIR=0
MAX_RUN_TIME="5m"

while [[ "$#" -gt 0 ]]; do
    case "$1" in
        -w|--workdir)
            [[ "$#" -ge 2 ]] || { usage; exit 1; }
            WORKDIR="$2"
            shift 2
            ;;
        -b|--bugset)
            [[ "$#" -ge 2 ]] || { usage; exit 1; }
            BUGSET_DIR="$2"
            shift 2
            ;;
        -t|--tmp)
            [[ "$#" -ge 2 ]] || { usage; exit 1; }
            TMP_ROOT="$2"
            shift 2
            ;;
        -o|--output)
            [[ "$#" -ge 2 ]] || { usage; exit 1; }
            OUTDIR="$2"
            shift 2
            ;;
        -j|--jobs)
            [[ "$#" -ge 2 ]] || { usage; exit 1; }
            JOBS="$2"
            shift 2
            ;;
        --max-run-time)
            [[ "$#" -ge 2 ]] || { usage; exit 1; }
            MAX_RUN_TIME="$2"
            shift 2
            ;;
        --keep-workdir)
            KEEP_WORKDIR=1
            shift
            ;;
        -h|--help)
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

if [[ -z "${WORKDIR}" || -z "${BUGSET_DIR}" || -z "${TMP_ROOT}" || -z "${OUTDIR}" ]]; then
    usage
    exit 1
fi

if ! [[ "${JOBS}" =~ ^[0-9]+$ ]] || (( JOBS <= 0 )); then
    echo "[ERROR] jobs must be a positive integer: ${JOBS}" >&2
    exit 1
fi

WORKDIR="$(realpath "${WORKDIR}")"
BUGSET_DIR="$(realpath "${BUGSET_DIR}")"
TMP_ROOT="$(realpath -m "${TMP_ROOT}")"
OUTDIR="$(realpath -m "${OUTDIR}")"

if [[ ! -d "${WORKDIR}" ]]; then
    echo "[ERROR] workdir does not exist: ${WORKDIR}" >&2
    exit 1
fi

if [[ ! -d "${WORKDIR}/rtl" ]]; then
    echo "[ERROR] workdir seems wrong: ${WORKDIR}" >&2
    echo "        expected directory: ${WORKDIR}/rtl" >&2
    exit 1
fi

if [[ ! -d "${BUGSET_DIR}" ]]; then
    echo "[ERROR] bugset_dir does not exist: ${BUGSET_DIR}" >&2
    exit 1
fi

if ! command -v git >/dev/null 2>&1; then
    echo "[ERROR] git not found in PATH" >&2
    exit 1
fi

if ! command -v fusesoc >/dev/null 2>&1; then
    echo "[ERROR] fusesoc not found in PATH" >&2
    exit 1
fi

if ! command -v timeout >/dev/null 2>&1; then
    echo "[ERROR] timeout not found in PATH" >&2
    echo "        Please install GNU coreutils." >&2
    exit 1
fi

mkdir -p "${TMP_ROOT}"
mkdir -p "${OUTDIR}"

if [[ -n "$(find "${OUTDIR}" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    echo "[ERROR] output directory is not empty: ${OUTDIR}" >&2
    echo "        please use an empty output directory to avoid overwriting old results" >&2
    exit 1
fi

RUN_ID="$(date +"%Y-%m-%d-%H-%M-%S")_$$"
RUN_TMP="${TMP_ROOT}/cosim_run_${RUN_ID}"
WORK_ROOT="${RUN_TMP}/work"
LOG_ROOT="${RUN_TMP}/logs"
STAGE_ROOT="${RUN_TMP}/fail_stage"
LOCK_FILE="${RUN_TMP}/status.lock"

mkdir -p "${WORK_ROOT}" "${LOG_ROOT}" "${STAGE_ROOT}"

case "${RUN_TMP}/" in
    "${WORKDIR}/"*)
        echo "[ERROR] tmp directory must not be inside workdir" >&2
        echo "        WORKDIR=${WORKDIR}" >&2
        echo "        RUN_TMP=${RUN_TMP}" >&2
        exit 1
        ;;
esac

RESULTS_TSV="${OUTDIR}/status.tsv"
KEPT_TSV="${OUTDIR}/kept.tsv"

printf "case_index\tdiff_path\tstatus\trc\tlogdir\tworkdir\n" > "${RESULTS_TSV}"
printf "output_index\tcase_index\tdiff_path\tstatus\trc\n" > "${KEPT_TSV}"

COSIM_BIN="build/lowrisc_ibex_ibex_simple_system_cosim_0/sim-verilator/Vibex_simple_system"
COREMARK_ELF="./examples/sw/benchmarks/coremark/coremark.elf"

FUSESOC_CMD=(
    fusesoc --cores-root=. run
    --target=sim
    --setup
    --build
    lowrisc:ibex:ibex_simple_system_cosim
    --RV32E=0
    --RV32M=ibex_pkg::RV32MFast
)

CHILD_PIDS=()

on_interrupt() {
    echo
    echo "[INTERRUPT] Ctrl-C received, stopping children..."

    trap - INT TERM

    if (( ${#CHILD_PIDS[@]} > 0 )); then
        kill "${CHILD_PIDS[@]}" 2>/dev/null || true
        wait 2>/dev/null || true
    fi

    exit 130
}

trap on_interrupt INT TERM

copy_workdir() {
    local dst="$1"

    mkdir -p "${dst}"

    if command -v rsync >/dev/null 2>&1; then
        rsync -a --delete \
            --exclude '/build/' \
            --exclude '/logs/' \
            --exclude '/.git/' \
            --exclude '/target/' \
            "${WORKDIR}/" "${dst}/"
    else
        cp -a "${WORKDIR}/." "${dst}/"
        rm -rf -- "${dst}/build" "${dst}/logs" "${dst}/.git" "${dst}/target"
    fi
}

cleanup_case_workdir() {
    local workdir="$1"
    local logdir="$2"

    if [[ "${KEEP_WORKDIR}" -eq 1 ]]; then
        echo "[KEEP] workdir: ${workdir}" >> "${logdir}/run.log"
        return 0
    fi

    if [[ -n "${workdir}" && -d "${workdir}" ]]; then
        echo "[CLEAN] remove workdir: ${workdir}" >> "${logdir}/run.log"
        rm -rf -- "${workdir}"
    fi
}

append_result() {
    local idx="$1"
    local diff="$2"
    local status="$3"
    local rc="$4"
    local logdir="$5"
    local workdir="$6"

    {
        flock 200
        printf "%s\t%s\t%s\t%s\t%s\t%s\n" \
            "${idx}" \
            "${diff}" \
            "${status}" \
            "${rc}" \
            "${logdir}" \
            "${workdir}" >> "${RESULTS_TSV}"
    } 200>"${LOCK_FILE}"
}

finish_case() {
    local idx="$1"
    local diff="$2"
    local status="$3"
    local rc="$4"
    local logdir="$5"
    local workdir="$6"

    echo "[${status}] case=${idx}, status=${rc}" | tee -a "${logdir}/status.txt"

    append_result \
        "${idx}" \
        "${diff}" \
        "${status}" \
        "${rc}" \
        "${logdir}" \
        "${workdir}"

    echo "[DONE][${idx}] ${status}, status=${rc}"
}

run_one_diff() (
    local idx="$1"
    local diff="$2"

    diff="$(realpath "${diff}")"

    local case_dir
    case_dir="$(dirname "${diff}")"

    local logdir="${LOG_ROOT}/${idx}"
    local workdir="${WORK_ROOT}/${idx}"

    mkdir -p "${logdir}"

    trap 'cleanup_case_workdir "${workdir}" "${logdir}"' EXIT

    echo "============================================================" > "${logdir}/run.log"
    echo "[CASE] ${idx}" >> "${logdir}/run.log"
    echo "[DIFF] ${diff}" >> "${logdir}/run.log"
    echo "[DIR ] ${case_dir}" >> "${logdir}/run.log"
    echo "[LOG ] ${logdir}" >> "${logdir}/run.log"
    echo "[WORK] ${workdir}" >> "${logdir}/run.log"
    echo "[MAX_RUN_TIME] ${MAX_RUN_TIME}" >> "${logdir}/run.log"
    echo "============================================================" >> "${logdir}/run.log"

    echo "[START][${idx}] ${diff}"

    echo "[COPY] ${WORKDIR} -> ${workdir}" >> "${logdir}/run.log"

    if copy_workdir "${workdir}" > "${logdir}/copy.log" 2>&1; then
        :
    else
        local rc=$?
        finish_case "${idx}" "${diff}" "COPY_FAIL" "${rc}" "${logdir}" "${workdir}"
        return 0
    fi

    echo "[APPLY] ${diff}" >> "${logdir}/run.log"

    if (
        cd "${workdir}"
        git apply --whitespace=nowarn "${diff}"
    ) > "${logdir}/git_apply.log" 2>&1; then
        :
    else
        local rc=$?
        finish_case "${idx}" "${diff}" "APPLY_FAIL" "${rc}" "${logdir}" "${workdir}"
        return 0
    fi

    echo "[BUILD] fusesoc cosim build" >> "${logdir}/run.log"

    if (
        cd "${workdir}"
        "${FUSESOC_CMD[@]}"
    ) > "${logdir}/fusesoc.log" 2>&1; then
        :
    else
        local rc=$?
        finish_case "${idx}" "${diff}" "BUILD_FAIL" "${rc}" "${logdir}" "${workdir}"
        return 0
    fi

    if [[ ! -x "${workdir}/${COSIM_BIN}" ]]; then
        echo "[ERROR] cosim binary not found or not executable: ${workdir}/${COSIM_BIN}" >> "${logdir}/run.log"
        finish_case "${idx}" "${diff}" "COSIM_BIN_MISSING" 127 "${logdir}" "${workdir}"
        return 0
    fi

    if [[ ! -f "${workdir}/${COREMARK_ELF}" ]]; then
        echo "[ERROR] coremark elf not found: ${workdir}/${COREMARK_ELF}" >> "${logdir}/run.log"
        finish_case "${idx}" "${diff}" "ELF_MISSING" 127 "${logdir}" "${workdir}"
        return 0
    fi

    echo "[RUN] cosim with timeout=${MAX_RUN_TIME}" >> "${logdir}/run.log"

    set +e
    (
        cd "${workdir}"

        timeout \
            --preserve-status \
            --kill-after=10s \
            "${MAX_RUN_TIME}" \
            "${COSIM_BIN}" -E "${COREMARK_ELF}"
    ) > "${logdir}/sim.log" 2>&1
    local sim_rc=$?
    set -e

    if (( sim_rc == 124 || sim_rc == 137 || sim_rc == 143 )); then
        echo "[TIMEOUT] simulation exceeded max runtime: ${MAX_RUN_TIME}" >> "${logdir}/run.log"
        mkdir -p "${STAGE_ROOT}/${idx}"
        cp -a "${case_dir}/." "${STAGE_ROOT}/${idx}/"
        finish_case "${idx}" "${diff}" "TIMEOUT" "${sim_rc}" "${logdir}" "${workdir}"
        return 0
    fi

    if (( sim_rc != 0 )); then
        mkdir -p "${STAGE_ROOT}/${idx}"
        cp -a "${case_dir}/." "${STAGE_ROOT}/${idx}/"
        finish_case "${idx}" "${diff}" "SIM_FAIL" "${sim_rc}" "${logdir}" "${workdir}"
        return 0
    fi

    finish_case "${idx}" "${diff}" "OK" 0 "${logdir}" "${workdir}"
    return 0
)

mapfile -d '' DIFFS < <(
    find "${BUGSET_DIR}" \
        -type f \
        -name "*.sv.diff" \
        -print0 | sort -z
)

if (( ${#DIFFS[@]} == 0 )); then
    echo "[WARN] no .sv.diff files found under: ${BUGSET_DIR}"
    exit 0
fi

echo "[INFO] diffs       : ${#DIFFS[@]}"
echo "[INFO] jobs        : ${JOBS}"
echo "[INFO] max run time: ${MAX_RUN_TIME}"
echo "[INFO] workdir     : ${WORKDIR}"
echo "[INFO] bugset      : ${BUGSET_DIR}"
echo "[INFO] tmp         : ${RUN_TMP}"
echo "[INFO] output      : ${OUTDIR}"
echo "[INFO] keep workdir: ${KEEP_WORKDIR}"

running=0

for idx in "${!DIFFS[@]}"; do
    run_one_diff "${idx}" "${DIFFS[$idx]}" &
    CHILD_PIDS+=("$!")
    running=$((running + 1))

    if (( running >= JOBS )); then
        wait -n || true
        running=$((running - 1))
    fi
done

while (( running > 0 )); do
    wait -n || true
    running=$((running - 1))
done

kept=0

while IFS=$'\t' read -r idx diff status rc logdir workdir; do
    [[ "${idx}" == "case_index" ]] && continue

    if [[ "${status}" == "SIM_FAIL" || "${status}" == "TIMEOUT" ]]; then
        dest="${OUTDIR}/${kept}"
        mkdir -p "${dest}"
        cp -a "${STAGE_ROOT}/${idx}/." "${dest}/"
        printf "%s\t%s\t%s\t%s\t%s\n" \
            "${kept}" \
            "${idx}" \
            "${diff}" \
            "${status}" \
            "${rc}" >> "${KEPT_TSV}"
        kept=$((kept + 1))
    fi
done < <(sort -n -k1,1 "${RESULTS_TSV}")

total="$(awk -F'\t' 'NR > 1 { c++ } END { print c + 0 }' "${RESULTS_TSV}")"
failed="$(awk -F'\t' 'NR > 1 && $3 != "OK" { c++ } END { print c + 0 }' "${RESULTS_TSV}")"
timeout_count="$(awk -F'\t' 'NR > 1 && $3 == "TIMEOUT" { c++ } END { print c + 0 }' "${RESULTS_TSV}")"

echo "============================================================"
echo "[SUMMARY] total diffs       : ${total}"
echo "[SUMMARY] non-OK cases      : ${failed}"
echo "[SUMMARY] timeout cases     : ${timeout_count}"
echo "[SUMMARY] error cases kept  : ${kept}"
echo "[SUMMARY] max run time      : ${MAX_RUN_TIME}"
echo "[SUMMARY] output directory  : ${OUTDIR}"
echo "[SUMMARY] status file       : ${RESULTS_TSV}"
echo "[SUMMARY] kept mapping      : ${KEPT_TSV}"
echo "[SUMMARY] tmp dir           : ${RUN_TMP}"
echo "============================================================"

if (( failed != 0 )); then
    echo "[DONE] some cases failed"
    exit 1
fi

echo "[DONE] all cases passed"
exit 0
