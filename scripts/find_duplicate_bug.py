#!/usr/bin/env python3

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class DiffSignature:
    digest: str
    valid: bool
    note: str


@dataclass(frozen=True)
class BugInfoCase:
    case_dir: Path
    bug_info_path: Path
    rel_case: str
    rel_bug_info: str
    diff_paths: tuple[Path, ...]
    diff_names: tuple[str, ...]
    module_name: str
    scope_name: str
    modify_line: str
    canonical_bug_info: str
    bug_info_digest: str
    diff_sig: DiffSignature


def read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def stable_digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def stable_digest_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def json_list(values: list[str]) -> str:
    return json.dumps(values, ensure_ascii=False)


def canonicalize_json_file(path: Path) -> tuple[str, dict[str, Any]] | None:
    """
    Default bug_info.json comparison mode:
      - ignores whitespace
      - ignores object key order
      - preserves array order
    """
    try:
        raw = read_bytes(path)
        data = json.loads(raw.decode("utf-8"))
    except Exception as err:
        print(f"[WARN] failed to parse JSON: {path}: {err}", file=sys.stderr)
        return None

    if not isinstance(data, dict):
        print(f"[WARN] bug_info.json is not a JSON object: {path}", file=sys.stderr)
        return None

    canonical = json.dumps(
        data,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )

    return canonical, data


def canonicalize_text_file(path: Path) -> tuple[str, dict[str, Any]]:
    """
    Optional bug_info.json comparison mode.

    Enabled by:
        --bug-info-text-exact

    In this mode, bug_info.json files must have exactly the same text.
    """
    raw = read_bytes(path)
    text = raw.decode("utf-8", errors="replace")

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            data = {}
    except Exception:
        data = {}

    return text, data


def json_field(data: dict[str, Any], key: str) -> str:
    if key not in data:
        return ""

    value = data[key]

    if isinstance(value, str):
        return value

    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def collect_diff_paths(case_dir: Path) -> tuple[Path, ...]:
    return tuple(sorted(case_dir.glob("*.sv.diff")))


def diff_signature(diff_paths: tuple[Path, ...]) -> DiffSignature:
    """
    Computes an exact signature for the corresponding .sv.diff files.

    Common case:
      - one .sv.diff file in each case directory
      - compare raw bytes directly

    If no .sv.diff exists:
      - valid=False
      - this case cannot be considered diff-identical

    If multiple .sv.diff files exist:
      - compare the whole bundle of file names and raw contents
    """
    if len(diff_paths) == 0:
        return DiffSignature(
            digest="NO_DIFF",
            valid=False,
            note="no .sv.diff found",
        )

    if len(diff_paths) == 1:
        diff_path = diff_paths[0]

        try:
            data = read_bytes(diff_path)
        except Exception as err:
            print(f"[WARN] failed to read diff: {diff_path}: {err}", file=sys.stderr)
            return DiffSignature(
                digest="READ_DIFF_ERROR",
                valid=False,
                note=f"read diff error: {err}",
            )

        return DiffSignature(
            digest=stable_digest_bytes(data),
            valid=True,
            note="single diff",
        )

    h = hashlib.sha256()

    for diff_path in diff_paths:
        try:
            data = read_bytes(diff_path)
        except Exception as err:
            print(f"[WARN] failed to read diff: {diff_path}: {err}", file=sys.stderr)
            return DiffSignature(
                digest="READ_DIFF_ERROR",
                valid=False,
                note=f"read diff error: {err}",
            )

        name_bytes = diff_path.name.encode("utf-8", errors="replace")

        h.update(b"NAME\0")
        h.update(str(len(name_bytes)).encode("ascii"))
        h.update(b"\0")
        h.update(name_bytes)

        h.update(b"\0DATA\0")
        h.update(str(len(data)).encode("ascii"))
        h.update(b"\0")
        h.update(data)

        h.update(b"\0END\0")

    return DiffSignature(
        digest=h.hexdigest(),
        valid=True,
        note=f"multiple diffs: {len(diff_paths)}",
    )


def iter_bug_info_paths(root: Path):
    yield from sorted(root.rglob("bug_info.json"))


def collect_cases(
    bugset_root: Path,
    bug_info_text_exact: bool,
) -> list[BugInfoCase]:
    cases: list[BugInfoCase] = []

    for bug_info_path in iter_bug_info_paths(bugset_root):
        case_dir = bug_info_path.parent

        if bug_info_text_exact:
            canonical_bug_info, data = canonicalize_text_file(bug_info_path)
        else:
            parsed = canonicalize_json_file(bug_info_path)
            if parsed is None:
                continue
            canonical_bug_info, data = parsed

        bug_info_digest = stable_digest_text(canonical_bug_info)

        try:
            rel_case = str(case_dir.relative_to(bugset_root))
        except ValueError:
            rel_case = str(case_dir)

        try:
            rel_bug_info = str(bug_info_path.relative_to(bugset_root))
        except ValueError:
            rel_bug_info = str(bug_info_path)

        diff_paths = collect_diff_paths(case_dir)
        diff_names = tuple(path.name for path in diff_paths)
        sig = diff_signature(diff_paths)

        cases.append(
            BugInfoCase(
                case_dir=case_dir,
                bug_info_path=bug_info_path,
                rel_case=rel_case,
                rel_bug_info=rel_bug_info,
                diff_paths=diff_paths,
                diff_names=diff_names,
                module_name=json_field(data, "module_name"),
                scope_name=json_field(data, "scope_name"),
                modify_line=json_field(data, "modify_line"),
                canonical_bug_info=canonical_bug_info,
                bug_info_digest=bug_info_digest,
                diff_sig=sig,
            )
        )

    return cases


def group_by_bug_info(
    cases: list[BugInfoCase],
) -> list[list[BugInfoCase]]:
    groups: dict[str, list[BugInfoCase]] = defaultdict(list)

    for case in cases:
        groups[case.bug_info_digest].append(case)

    duplicate_groups = [
        sorted(group, key=lambda x: x.rel_case)
        for group in groups.values()
        if len(group) > 1
    ]

    duplicate_groups.sort(key=lambda group: (-len(group), group[0].rel_case))
    return duplicate_groups


def group_by_diff(
    group: list[BugInfoCase],
) -> dict[str, list[BugInfoCase]]:
    diff_groups: dict[str, list[BugInfoCase]] = defaultdict(list)

    for case in group:
        diff_groups[case.diff_sig.digest].append(case)

    return diff_groups


def build_rows(
    duplicate_groups: list[list[BugInfoCase]],
) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
    same_rows: list[dict[str, str]] = []
    same_position_rows: list[dict[str, str]] = []

    for bug_info_group_id, group in enumerate(duplicate_groups, start=1):
        bug_info_group_count = len(group)
        first = group[0]

        diff_groups = group_by_diff(group)

        # Output 1:
        # same_cases.tsv
        #
        # Condition:
        #   same bug_info.json
        #   same .sv.diff
        #
        # Each identical-diff subgroup becomes one row.
        for diff_digest, diff_group in sorted(
            diff_groups.items(),
            key=lambda item: (-len(item[1]), item[0]),
        ):
            if len(diff_group) <= 1:
                continue

            if not all(case.diff_sig.valid for case in diff_group):
                continue

            diff_group = sorted(diff_group, key=lambda x: x.rel_case)
            diff_first = diff_group[0]

            same_rows.append(
                {
                    "bug_info_group_id": str(bug_info_group_id),
                    "bug_info_group_count": str(bug_info_group_count),
                    "same_count": str(len(diff_group)),
                    "bug_info_digest": first.bug_info_digest,
                    "diff_digest": diff_digest,
                    "diff_note": diff_first.diff_sig.note,
                    "cases": json_list([case.rel_case for case in diff_group]),
                    "bug_infos": json_list([case.rel_bug_info for case in diff_group]),
                    "diffs": json_list([",".join(case.diff_names) for case in diff_group]),
                    "module_name": first.module_name,
                    "scope_name": first.scope_name,
                    "modify_line": first.modify_line,
                }
            )

        # Output 2:
        # same_position_cases.tsv
        #
        # Condition:
        #   same bug_info.json
        #
        # This does not require .sv.diff to be different.
        group = sorted(group, key=lambda x: x.rel_case)

        same_position_rows.append(
            {
                "bug_info_group_id": str(bug_info_group_id),
                "bug_info_group_count": str(bug_info_group_count),
                "bug_info_digest": first.bug_info_digest,
                "diff_group_count": str(len(diff_groups)),
                "cases": json_list([case.rel_case for case in group]),
                "bug_infos": json_list([case.rel_bug_info for case in group]),
                "diffs": json_list([",".join(case.diff_names) for case in group]),
                "diff_digests": json_list([case.diff_sig.digest for case in group]),
                "diff_notes": json_list([case.diff_sig.note for case in group]),
                "module_name": first.module_name,
                "scope_name": first.scope_name,
                "modify_line": first.modify_line,
            }
        )

    return same_rows, same_position_rows


def write_same_tsv(output_path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "bug_info_group_id",
        "bug_info_group_count",
        "same_count",
        "bug_info_digest",
        "diff_digest",
        "diff_note",
        "cases",
        "bug_infos",
        "diffs",
        "module_name",
        "scope_name",
        "modify_line",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def write_same_position_tsv(output_path: Path, rows: list[dict[str, str]]) -> None:
    fieldnames = [
        "bug_info_group_id",
        "bug_info_group_count",
        "bug_info_digest",
        "diff_group_count",
        "cases",
        "bug_infos",
        "diffs",
        "diff_digests",
        "diff_notes",
        "module_name",
        "scope_name",
        "modify_line",
    ]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, delimiter="\t")
        writer.writeheader()
        writer.writerows(rows)


def print_summary(
    cases: list[BugInfoCase],
    duplicate_groups: list[list[BugInfoCase]],
    same_rows: list[dict[str, str]],
    same_position_rows: list[dict[str, str]],
    same_output: Path,
    same_position_output: Path,
) -> None:
    duplicated_bug_info_cases = sum(len(group) for group in duplicate_groups)
    same_case_count = sum(int(row["same_count"]) for row in same_rows)
    same_position_case_count = sum(
        int(row["bug_info_group_count"]) for row in same_position_rows
    )

    print("============================================================")
    print(f"[SUMMARY] total bug_info.json          : {len(cases)}")
    print(f"[SUMMARY] duplicate bug_info groups   : {len(duplicate_groups)}")
    print(f"[SUMMARY] duplicated bug_info cases   : {duplicated_bug_info_cases}")
    print(f"[SUMMARY] same groups                 : {len(same_rows)}")
    print(f"[SUMMARY] same cases                  : {same_case_count}")
    print(f"[SUMMARY] same-position groups        : {len(same_position_rows)}")
    print(f"[SUMMARY] same-position cases         : {same_position_case_count}")
    print(f"[SUMMARY] same output                 : {same_output}")
    print(f"[SUMMARY] same-position output        : {same_position_output}")
    print("============================================================")

    if same_rows:
        print("[SAME] identical bug_info.json and identical .sv.diff:")
        for row in same_rows:
            print(
                f"  - group={row['bug_info_group_id']} "
                f"count={row['same_count']} "
                f"cases={row['cases']}"
            )

    if same_position_rows:
        print("[SAME-POSITION] identical bug_info.json:")
        for row in same_position_rows:
            print(
                f"  - group={row['bug_info_group_id']} "
                f"count={row['bug_info_group_count']} "
                f"cases={row['cases']}"
            )

    if not same_rows and not same_position_rows:
        print("[OK] no duplicated bug_info.json found")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Find duplicated bug_info.json cases. "
            "Groups with identical bug_info.json and identical .sv.diff are written "
            "to the same output file. "
            "Groups with identical bug_info.json are written to the same-position "
            "output file, regardless of whether .sv.diff is identical."
        )
    )
    parser.add_argument(
        "bugset_root",
        help="bugset root directory, e.g. dataset or bugset",
    )
    parser.add_argument(
        "--same-output",
        default="same_cases.tsv",
        help="output TSV for identical bug_info.json and identical .sv.diff groups, default: same_cases.tsv",
    )
    parser.add_argument(
        "--same-position-output",
        default="same_position_cases.tsv",
        help="output TSV for identical bug_info.json groups, default: same_position_cases.tsv",
    )
    parser.add_argument(
        "--bug-info-text-exact",
        action="store_true",
        help=(
            "compare raw bug_info.json text instead of parsed JSON. "
            "Default compares JSON semantically with sorted keys."
        ),
    )

    args = parser.parse_args()

    bugset_root = Path(args.bugset_root).resolve()
    same_output = Path(args.same_output)
    same_position_output = Path(args.same_position_output)

    if not bugset_root.is_dir():
        print(f"[ERROR] bugset root not found: {bugset_root}", file=sys.stderr)
        return 1

    cases = collect_cases(
        bugset_root=bugset_root,
        bug_info_text_exact=args.bug_info_text_exact,
    )

    duplicate_groups = group_by_bug_info(cases)

    same_rows, same_position_rows = build_rows(duplicate_groups)

    write_same_tsv(same_output, same_rows)
    write_same_position_tsv(same_position_output, same_position_rows)

    print_summary(
        cases=cases,
        duplicate_groups=duplicate_groups,
        same_rows=same_rows,
        same_position_rows=same_position_rows,
        same_output=same_output,
        same_position_output=same_position_output,
    )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
