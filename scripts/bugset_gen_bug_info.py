#!/usr/bin/env python3

from pathlib import Path
import argparse
import json
import re
import sys


HUNK_RE = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


def parse_modified_lines(diff_path: Path) -> list[int]:
    """
    Parse unified diff and return modified line numbers.

    Rules:
    - replacement: record new-file line number
    - pure addition: record new-file line number, unless it is a blank line
    - pure deletion: record old-file line number
    - ignore final blank-line additions such as '+'
    """
    old_lineno = None
    new_lineno = None

    modify_lines: list[int] = []
    pending_deletes: list[tuple[int, str]] = []

    def flush_pending_deletes():
        nonlocal pending_deletes
        for old_no, old_text in pending_deletes:
            if old_text.strip():
                modify_lines.append(old_no)
        pending_deletes = []

    with diff_path.open("r", encoding="utf-8", errors="replace") as f:
        for raw in f:
            line = raw.rstrip("\n")

            m = HUNK_RE.match(line)
            if m:
                flush_pending_deletes()
                old_lineno = int(m.group(1))
                new_lineno = int(m.group(2))
                continue

            if old_lineno is None or new_lineno is None:
                continue

            if line.startswith(" "):
                flush_pending_deletes()
                old_lineno += 1
                new_lineno += 1

            elif line.startswith("-") and not line.startswith("---"):
                pending_deletes.append((old_lineno, line[1:]))
                old_lineno += 1

            elif line.startswith("+") and not line.startswith("+++"):
                new_text = line[1:]

                if pending_deletes:
                    # A deletion followed by an addition is treated as a modification.
                    old_no, old_text = pending_deletes.pop(0)

                    # Ignore blank-only changes, especially the trailing '+' at EOF.
                    if old_text.strip() or new_text.strip():
                        modify_lines.append(new_lineno)
                else:
                    # Pure addition. Ignore blank line addition.
                    if new_text.strip():
                        modify_lines.append(new_lineno)

                new_lineno += 1

            elif line.startswith("\\"):
                # "\ No newline at end of file"
                continue

        flush_pending_deletes()

    # Deduplicate while preserving order
    seen = set()
    result = []
    for line_no in modify_lines:
        if line_no not in seen:
            seen.add(line_no)
            result.append(line_no)

    return result


def get_module_name_from_diff(diff_path: Path) -> str:
    name = diff_path.name

    if not name.endswith(".sv.diff"):
        raise ValueError(f"diff file is not '*.sv.diff': {diff_path}")

    return name.removesuffix(".sv.diff")


def process_case(case_dir: Path, output_name: str, strict: bool = True) -> bool:
    diff_files = sorted(case_dir.glob("*.sv.diff"))
    oracle_path = case_dir / "oracle_info.json"

    if not diff_files:
        print(f"[SKIP] no .sv.diff in {case_dir}")
        return False

    if len(diff_files) > 1:
        print(f"[WARN] multiple .sv.diff files in {case_dir}, use first: {diff_files[0]}")

    if not oracle_path.is_file():
        print(f"[SKIP] missing oracle_info.json in {case_dir}")
        return False

    diff_path = diff_files[0]

    with oracle_path.open("r", encoding="utf-8") as f:
        oracle = json.load(f)

    module_name = oracle.get("module_name")
    scope_name = oracle.get("scope_name")

    if not module_name:
        raise ValueError(f"missing module_name in {oracle_path}")

    if not scope_name:
        raise ValueError(f"missing scope_name in {oracle_path}")

    module_from_diff = get_module_name_from_diff(diff_path)

    if module_name != module_from_diff:
        msg = (
            f"module_name mismatch in {case_dir}: "
            f"oracle_info.json has {module_name!r}, "
            f"diff file has {module_from_diff!r}"
        )
        if strict:
            raise ValueError(msg)
        else:
            print(f"[WARN] {msg}")

    info = {
        "module_name": module_name,
        "scope_name": scope_name,
        "modify_line": parse_modified_lines(diff_path),
    }

    output_path = case_dir / output_name

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(info, f, indent=2, ensure_ascii=False)
        f.write("\n")

    print(f"[OK] {output_path}")
    return True


def iter_case_dirs(dataset_root: Path):
    for dataset_dir in sorted(dataset_root.glob("dataset_*")):
        if not dataset_dir.is_dir():
            continue

        for case_dir in sorted(dataset_dir.iterdir(), key=lambda p: int(p.name) if p.name.isdigit() else p.name):
            if case_dir.is_dir():
                yield case_dir


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("dataset_root", nargs="?", default="dataset")
    parser.add_argument("--output-name", default="bug_info.json")
    parser.add_argument("--no-strict", action="store_true", help="do not fail when module name mismatches")
    args = parser.parse_args()

    dataset_root = Path(args.dataset_root)

    if not dataset_root.is_dir():
        print(f"[ERROR] dataset root not found: {dataset_root}", file=sys.stderr)
        sys.exit(1)

    count = 0

    for case_dir in iter_case_dirs(dataset_root):
        if process_case(case_dir, args.output_name, strict=not args.no_strict):
            count += 1

    print(f"[DONE] generated {count} json files")


if __name__ == "__main__":
    main()
