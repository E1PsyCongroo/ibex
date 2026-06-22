#!/usr/bin/env bash
set -euo pipefail

DATASET_ROOT="${1:-dataset}"

IBEX_HOME="${IBEX_HOME:-$(pwd)}"

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

CURRENT_DIFF=""

cleanup() {
    if [[ -n "${CURRENT_DIFF}" ]]; then
        echo "[CLEANUP] revert ${CURRENT_DIFF}"
        git apply -R "${CURRENT_DIFF}" || true
        CURRENT_DIFF=""
    fi
}

on_interrupt() {
    echo
    echo "[INTERRUPT] Ctrl-C received, exiting..."
    cleanup
    trap - EXIT
    exit 130
}

trap cleanup EXIT
trap on_interrupt INT TERM

check_interrupt_status() {
    local status="$1"

    # 130 = SIGINT, 143 = SIGTERM
    if [[ "${status}" -eq 130 || "${status}" -eq 143 ]]; then
        on_interrupt
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

        if ! "${OBJDUMP_BIN}" -SD "${elf}" > "${dis}"; then
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

run_one_case() {
    local case_dir="$1"

    local diff
    diff="$(find "${case_dir}" -maxdepth 1 -type f -name "*.sv.diff" | sort | head -n 1)"

    if [[ -z "${diff}" ]]; then
        echo "[SKIP] no .sv.diff in ${case_dir}"
        return 0
    fi

    local dataset_name
    local case_name

    dataset_name="$(basename "$(dirname "${case_dir}")")"
    case_name="$(basename "${case_dir}")"

    local logdir
    logdir="./logs/${dataset_name}/${case_name}/$(date +"%Y-%m-%d-%H-%M-%S")"
    mkdir -p "${logdir}"

    echo "============================================================"
    echo "[CASE] ${dataset_name}/${case_name}"
    echo "[DIFF] ${diff}"
    echo "[LOG ] ${logdir}"
    echo "============================================================"

    echo "[APPLY] ${diff}"
    git apply "${diff}"
    CURRENT_DIFF="${diff}"

    echo "[BUILD] fusesoc build after applying patch"

    set +e
    "${FUSESOC_CMD[@]}" 2>&1 | tee "${logdir}/build.log"
    local build_status="${PIPESTATUS[0]}"
    set -e

    check_interrupt_status "${build_status}"

    if [[ "${build_status}" -ne 0 ]]; then
        echo "[BUILD FAIL] ${dataset_name}/${case_name}, status=${build_status}" | tee -a "${logdir}/status.txt"
        cleanup
        return "${build_status}"
    fi

    if [[ ! -x "${SBFL_BIN}" ]]; then
        echo "[ERROR] SBFL binary not found or not executable: ${SBFL_BIN}, status=1" | tee -a "${logdir}/status.txt"
        cleanup
        return 1
    fi

    echo "[RUN] SBFL"

    set +e
    "${SBFL_BIN}" \
        -f \
        -r \
        -c "verilator.branch,verilator.line" \
        --max-iters 50 \
        --top-pass 100 \
        --top-sus 50 \
        --corpus-input examples/sw/benchmarks/coremark/coremark.elf \
        --output "${logdir}" \
        --save-reduce \
        --rtl-path "${IBEX_HOME}/rtl" \
        --include-paths "${IBEX_HOME}/vendor/lowrisc_ip/ip/prim/rtl/,${IBEX_HOME}/vendor/lowrisc_ip/dv/sv/dv_utils/" \
        --top-module ibex_core \
        --top-scope TOP.ibex_simple_system.u_top.u_ibex_top.u_ibex_core \
        -- -c 1000000 \
        2>&1 | tee "${logdir}/sbfl.log"

    local sbfl_status="${PIPESTATUS[0]}"
    set -e

    check_interrupt_status "${sbfl_status}"

    echo "[POST] disassemble ELF files under ${logdir}"
    disassemble_elfs "${logdir}"

    cleanup

    if [[ "${sbfl_status}" -ne 0 ]]; then
        echo "[SBFL FAIL] ${dataset_name}/${case_name}, status=${sbfl_status}" | tee -a "${logdir}/status.txt"
        return "${sbfl_status}"
    fi

    echo "[OK] ${dataset_name}/${case_name}" | tee -a "${logdir}/status.txt"
    return 0
}

main() {
    if [[ ! -d "${DATASET_ROOT}" ]]; then
        echo "[ERROR] dataset root not found: ${DATASET_ROOT}" >&2
        exit 1
    fi

    if ! command -v fusesoc >/dev/null 2>&1; then
        echo "[ERROR] fusesoc not found in PATH" >&2
        exit 1
    fi

    if [[ ! -d "${IBEX_HOME}/rtl" ]]; then
        echo "[ERROR] IBEX_HOME seems wrong: ${IBEX_HOME}" >&2
        echo "        expected directory: ${IBEX_HOME}/rtl" >&2
        exit 1
    fi

    local failed=0

    while IFS= read -r -d '' case_dir; do
        if ! run_one_case "${case_dir}"; then
            failed=1
            echo "[WARN] continue after failed case: ${case_dir}"
        fi
    done < <(
        find "${DATASET_ROOT}" \
            -mindepth 2 \
            -maxdepth 2 \
            -type d \
            -print0 | sort -z
    )

    if [[ "${failed}" -ne 0 ]]; then
        echo "[DONE] some cases failed"
        exit 1
    fi

    echo "[DONE] all cases passed"
}

main "$@"
