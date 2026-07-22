"""Filesystem and subprocess operations for isolated bug-case workdirs."""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import threading
from pathlib import Path
from typing import IO

from .errors import SbflBatchError

WORKDIR_DIRECTORIES = ("dv", "vendor", "rtl", "shared", "examples", "util", "lint")
WORKDIR_FILES = ("Cargo.lock", "Cargo.toml")
FUSESOC_COMMAND = [
    "fusesoc",
    "--cores-root=.",
    "run",
    "--target=sim",
    "--setup",
    "--build",
    "lowrisc:ibex:ibex_simple_system_sbfl",
    "--RV32E=0",
    "--RV32M=ibex_pkg::RV32MFast",
]


class ProcessRegistry:
    """Track child process groups so Ctrl-C can terminate the whole batch."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._processes: set[subprocess.Popen] = set()

    def run(self, argv: list[str], cwd: Path, output: Path) -> int:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("wb") as handle:
            process = subprocess.Popen(
                argv,
                cwd=cwd,
                stdout=handle,
                stderr=subprocess.STDOUT,
                start_new_session=True,
            )
            with self._lock:
                self._processes.add(process)
            try:
                return process.wait()
            finally:
                with self._lock:
                    self._processes.discard(process)

    def terminate_all(self) -> None:
        with self._lock:
            processes = list(self._processes)
        for process in processes:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass


def require_commands(*names: str) -> None:
    missing = [name for name in names if shutil.which(name) is None]
    if missing:
        raise SbflBatchError(f"required commands not found in PATH: {', '.join(missing)}")


def _copy_source(source: Path, destination: Path) -> None:
    if source.is_symlink():
        destination.symlink_to(os.readlink(source))
    elif source.is_dir():
        shutil.copytree(source, destination, symlinks=True)
    else:
        shutil.copy2(source, destination, follow_symlinks=False)


def copy_workdir(ibex_home: Path, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=False)
    for name in WORKDIR_DIRECTORIES:
        source = ibex_home / name
        if not source.exists():
            raise SbflBatchError(f"required workdir source not found: {source}")
        _copy_source(source, destination / name)
    for name in WORKDIR_FILES:
        source = ibex_home / name
        if not source.is_file():
            raise SbflBatchError(f"required workdir file not found: {source}")
        _copy_source(source, destination / name)
    core_files = sorted(ibex_home.glob("*.core"))
    if not core_files:
        raise SbflBatchError(f"no root-level .core files found under: {ibex_home}")
    for source in core_files:
        _copy_source(source, destination / source.name)


def resolve_workdir_path(workdir: Path, path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else workdir / candidate


def append_text(path: Path, text: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(text)


def write_command(run_log: Path, executable: Path, argv: list[str]) -> None:
    import shlex

    append_text(run_log, f"[RUN] {shlex.join([str(executable), *argv])}\n")


def disassemble_elfs(root: Path, objdump_bin: str, output: IO[str]) -> None:
    if shutil.which(objdump_bin) is None:
        print(f"[WARN] {objdump_bin} not found in PATH, skip disassemble", file=output)
        return
    for elf in sorted(path for path in root.rglob("*") if path.suffix.lower() == ".elf"):
        disassembly = elf.with_suffix(".dis")
        print(f"[OBJDUMP] {elf} -> {disassembly}", file=output)
        with disassembly.open("wb") as handle:
            result = subprocess.run(
                [objdump_bin, "-SD", str(elf)],
                stdout=handle,
                stderr=subprocess.STDOUT,
                check=False,
            )
        if result.returncode:
            disassembly.unlink(missing_ok=True)
