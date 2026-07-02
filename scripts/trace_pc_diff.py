#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TextIO


@dataclass(frozen=True)
class TraceEntry:
    file: str
    line_no: int
    trace_idx: int

    pc: str
    insn: str
    decoded: str
    regmem: str

    raw_line: str


@dataclass(frozen=True)
class DiffEntry:
    compare_idx_after_skip: int
    diff_fields: list[str]
    a: TraceEntry | None
    b: TraceEntry | None
    reason: str


def normalize_ws(s: str) -> str:
    return " ".join(s.strip().split())


def parse_trace_log(path: Path, *, exact_space: bool = False) -> list[TraceEntry]:
    entries: list[TraceEntry] = []

    with path.open("r", encoding="utf-8", errors="replace") as f:
        header = f.readline()

        if not header:
            return entries

        for line_no, line in enumerate(f, start=2):
            raw_line = line.rstrip("\n")

            if not raw_line.strip():
                continue

            parts = raw_line.split("\t")

            # Expected logical columns:
            #
            # Time
            # Cycle
            # PC
            # Insn
            # Decoded instruction
            # Register and memory contents
            #
            # Actual rows may be:
            #
            # Time  Cycle  PC  Insn  mnemonic  operands  regmem
            #
            # So:
            #   PC      = parts[2]
            #   Insn    = parts[3]
            #   Decoded = parts[4:-1]
            #   RegMem  = parts[-1]
            if len(parts) < 6:
                print(
                    f"[WARN] skip malformed line {path}:{line_no}: {raw_line!r}",
                    file=sys.stderr,
                )
                continue

            pc = parts[2].strip().lower()
            insn = parts[3].strip().lower()
            decoded = "\t".join(parts[4:-1])
            regmem = parts[-1]

            if exact_space:
                decoded = decoded.strip()
                regmem = regmem.strip()
            else:
                decoded = normalize_ws(decoded)
                regmem = normalize_ws(regmem)

            entries.append(
                TraceEntry(
                    file=str(path),
                    line_no=line_no,
                    trace_idx=len(entries),
                    pc=pc,
                    insn=insn,
                    decoded=decoded,
                    regmem=regmem,
                    raw_line=raw_line,
                )
            )

    return entries


def get_diff_fields(a: TraceEntry, b: TraceEntry) -> list[str]:
    fields: list[str] = []

    if a.pc != b.pc:
        fields.append("PC")

    if a.insn != b.insn:
        fields.append("Insn")

    if a.decoded != b.decoded:
        fields.append("Decoded instruction")

    if a.regmem != b.regmem:
        fields.append("Register and memory contents")

    return fields


def collect_diffs(
    entries_a: list[TraceEntry],
    skip_a: int,
    entries_b: list[TraceEntry],
    skip_b: int,
    *,
    max_diffs: int | None = None,
) -> list[DiffEntry]:
    if skip_a < 0:
        raise ValueError(f"skip_a must be non-negative, got {skip_a}")

    if skip_b < 0:
        raise ValueError(f"skip_b must be non-negative, got {skip_b}")

    if skip_a > len(entries_a):
        raise ValueError(
            f"skip_a={skip_a} exceeds parsed entries of file A: {len(entries_a)}"
        )

    if skip_b > len(entries_b):
        raise ValueError(
            f"skip_b={skip_b} exceeds parsed entries of file B: {len(entries_b)}"
        )

    diffs: list[DiffEntry] = []

    len_a_after_skip = len(entries_a) - skip_a
    len_b_after_skip = len(entries_b) - skip_b
    compare_len = max(len_a_after_skip, len_b_after_skip)

    for i in range(compare_len):
        idx_a = skip_a + i
        idx_b = skip_b + i

        has_a = idx_a < len(entries_a)
        has_b = idx_b < len(entries_b)

        if has_a and has_b:
            a = entries_a[idx_a]
            b = entries_b[idx_b]

            diff_fields = get_diff_fields(a, b)

            if diff_fields:
                diffs.append(
                    DiffEntry(
                        compare_idx_after_skip=i,
                        diff_fields=diff_fields,
                        a=a,
                        b=b,
                        reason="entry mismatch",
                    )
                )

        elif has_a and not has_b:
            diffs.append(
                DiffEntry(
                    compare_idx_after_skip=i,
                    diff_fields=["EOF"],
                    a=entries_a[idx_a],
                    b=None,
                    reason="file B reached EOF first",
                )
            )

        elif not has_a and has_b:
            diffs.append(
                DiffEntry(
                    compare_idx_after_skip=i,
                    diff_fields=["EOF"],
                    a=None,
                    b=entries_b[idx_b],
                    reason="file A reached EOF first",
                )
            )

        if max_diffs is not None and len(diffs) >= max_diffs:
            break

    return diffs


def print_entry(label: str, entry: TraceEntry | None) -> None:
    print(f"\n[{label}]")

    if entry is None:
        print("<EOF>")
        return

    print(f"file: {entry.file}")
    print(f"line_no: {entry.line_no}")
    print(f"trace_idx: {entry.trace_idx}")
    print(f"PC: {entry.pc}")
    print(f"Insn: {entry.insn}")
    print(f"Decoded instruction: {entry.decoded}")
    print(f"Register and memory contents: {entry.regmem}")
    print(f"Raw line: {entry.raw_line}")


def print_human(diffs: list[DiffEntry]) -> None:
    if not diffs:
        print("[OK] no difference found after skips")
        return

    print(f"[DIFF] total differences: {len(diffs)}")

    for diff_id, diff in enumerate(diffs, start=1):
        print("\n" + "=" * 80)
        print(f"Diff #{diff_id}")
        print(f"reason: {diff.reason}")
        print(f"compare_idx_after_skip: {diff.compare_idx_after_skip}")
        print(f"diff_fields: {', '.join(diff.diff_fields)}")

        print_entry("A", diff.a)
        print_entry("B", diff.b)


def write_tsv(out: TextIO, diffs: list[DiffEntry]) -> None:
    writer = csv.writer(out, delimiter="\t", lineterminator="\n")

    writer.writerow(
        [
            "DiffIndex",
            "Result",
            "Reason",
            "CompareIndexAfterSkip",
            "DiffFields",
            "A_File",
            "A_LineNo",
            "A_TraceIndex",
            "A_PC",
            "A_Insn",
            "A_Decoded instruction",
            "A_Register and memory contents",
            "A_RawLine",
            "B_File",
            "B_LineNo",
            "B_TraceIndex",
            "B_PC",
            "B_Insn",
            "B_Decoded instruction",
            "B_Register and memory contents",
            "B_RawLine",
        ]
    )

    if not diffs:
        writer.writerow(
            [
                "",
                "OK",
                "no difference found after skips",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
                "",
            ]
        )
        return

    for diff_index, diff in enumerate(diffs, start=1):
        a = diff.a
        b = diff.b

        writer.writerow(
            [
                diff_index,
                "DIFF",
                diff.reason,
                diff.compare_idx_after_skip,
                ",".join(diff.diff_fields),
                a.file if a else "",
                a.line_no if a else "",
                a.trace_idx if a else "",
                a.pc if a else "",
                a.insn if a else "",
                a.decoded if a else "",
                a.regmem if a else "",
                a.raw_line if a else "",
                b.file if b else "",
                b.line_no if b else "",
                b.trace_idx if b else "",
                b.pc if b else "",
                b.insn if b else "",
                b.decoded if b else "",
                b.regmem if b else "",
                b.raw_line if b else "",
            ]
        )


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two trace logs entry-by-entry after skipping different numbers "
            "of parsed trace entries in each file. Output all mismatches."
        )
    )

    parser.add_argument("file_a", type=Path)
    parser.add_argument("skip_a", type=int)
    parser.add_argument("file_b", type=Path)
    parser.add_argument("skip_b", type=int)

    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="write all differences as TSV",
    )

    parser.add_argument(
        "--exact-space",
        action="store_true",
        help="compare decoded/regmem fields with exact spaces instead of normalized whitespace",
    )

    parser.add_argument(
        "--max-diffs",
        type=int,
        default=None,
        help="stop after collecting this many differences",
    )

    args = parser.parse_args()

    try:
        entries_a = parse_trace_log(args.file_a, exact_space=args.exact_space)
        entries_b = parse_trace_log(args.file_b, exact_space=args.exact_space)

        diffs = collect_diffs(
            entries_a=entries_a,
            skip_a=args.skip_a,
            entries_b=entries_b,
            skip_b=args.skip_b,
            max_diffs=args.max_diffs,
        )

        if args.output is None:
            print_human(diffs)
        else:
            with args.output.open("w", encoding="utf-8", newline="") as out:
                write_tsv(out, diffs)

            if diffs:
                print(
                    f"[DIFF] total differences={len(diffs)}, "
                    f"result written to {args.output}"
                )
            else:
                print(f"[OK] no difference found, result written to {args.output}")

    except Exception as err:
        print(f"[ERROR] {err}", file=sys.stderr)
        return 2

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
