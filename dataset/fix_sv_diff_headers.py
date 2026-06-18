#!/usr/bin/env python3
import argparse
import re
from pathlib import Path


SV_PATH_RE = re.compile(r"^(---|\+\+\+)\s+(.+?\.sv)(?:\s.*)?$")


def extract_sv_path(header_line: str) -> str | None:
    """
    从 diff header 中提取 .sv 路径，去掉后面的时间戳。
    例如：
    --- /home/.../rtl/ibex_decoder.sv  2026-...
    -> /home/.../rtl/ibex_decoder.sv
    """
    line = header_line.rstrip("\n")
    m = SV_PATH_RE.match(line)
    if not m:
        return None
    return m.group(2)


def to_rtl_path(path_str: str) -> str:
    """
    转换成 a/rtl/xxx.sv。
    如果原路径中包含 /rtl/，则保留 rtl 后面的相对路径。
    否则只取文件名。
    """
    normalized = path_str.replace("\\", "/")

    if "/rtl/" in normalized:
        rel = normalized.split("/rtl/", 1)[1]
    else:
        rel = Path(normalized).name

    return f"a/rtl/{rel}"


def fix_one_file(diff_path: Path, dry_run: bool = False) -> bool:
    lines = diff_path.read_text(encoding="utf-8").splitlines(keepends=True)

    changed = False
    i = 0

    while i < len(lines) - 1:
        old_line = lines[i]
        new_line = lines[i + 1]

        # 只处理连续的 unified diff 文件头：
        # --- xxx.sv ...
        # +++ xxx.sv ...
        if old_line.startswith("--- ") and new_line.startswith("+++ "):
            old_path = extract_sv_path(old_line)
            new_path = extract_sv_path(new_line)

            if old_path or new_path:
                # 优先用 --- 行路径，因为它通常包含 rtl/xxx.sv
                src_path = old_path or new_path
                fixed_path = to_rtl_path(src_path)

                old_nl = "\n" if old_line.endswith("\n") else ""
                new_nl = "\n" if new_line.endswith("\n") else ""

                fixed_old = f"--- {fixed_path}{old_nl}"
                fixed_new = f"+++ {fixed_path}{new_nl}"

                if lines[i] != fixed_old or lines[i + 1] != fixed_new:
                    lines[i] = fixed_old
                    lines[i + 1] = fixed_new
                    changed = True

                i += 2
                continue

        i += 1

    if changed:
        print(f"[fix] {diff_path}")
        if not dry_run:
            diff_path.write_text("".join(lines), encoding="utf-8")
    else:
        print(f"[skip] {diff_path}")

    return changed


def main():
    parser = argparse.ArgumentParser(
        description="Recursively fix .sv.diff headers to --- a/rtl/xxx.sv and +++ a/rtl/xxx.sv"
    )
    parser.add_argument("dir", help="directory to search recursively")
    parser.add_argument("--dry-run", action="store_true", help="only show files that would be processed")
    args = parser.parse_args()

    root = Path(args.dir)

    if not root.is_dir():
        raise SystemExit(f"Not a directory: {root}")

    diff_files = sorted(root.rglob("*.sv.diff"))

    if not diff_files:
        print("No .sv.diff files found.")
        return

    fixed = 0
    for diff_file in diff_files:
        if fix_one_file(diff_file, dry_run=args.dry_run):
            fixed += 1

    print(f"\nDone. Fixed {fixed}/{len(diff_files)} files.")


if __name__ == "__main__":
    main()
