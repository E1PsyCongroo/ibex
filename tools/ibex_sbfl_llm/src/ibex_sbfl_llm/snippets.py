"""Build compact, line-numbered RTL context for candidate blocks."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

from .errors import SbflLlmError
from .io_utils import read_text
from .models import Candidate


@dataclass(frozen=True)
class SourceBundle:
    regions: tuple[dict[str, object], ...]
    source_mode: str
    effective_radius: int | None
    source_chars: int


def compress_line_ranges(lines: Iterable[int]) -> str:
    values = sorted({int(line) for line in lines})
    if not values:
        return ""
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(str(start) if start == previous else f"{start}-{previous}")
        start = previous = value
    ranges.append(str(start) if start == previous else f"{start}-{previous}")
    return ",".join(ranges)


def find_module_source(rtl_root: Path, module: str) -> Path:
    direct = rtl_root / f"{module}.sv"
    if direct.is_file():
        return direct
    matches = sorted(rtl_root.rglob(f"{module}.sv"))
    if not matches:
        raise SbflLlmError(f"cannot find source for module {module!r} under {rtl_root}")
    if len(matches) > 1:
        preview = ", ".join(str(path) for path in matches[:3])
        raise SbflLlmError(f"multiple source files found for module {module!r}: {preview}")
    return matches[0]


def _expanded_lines(core_lines: set[int], line_count: int, radius: int) -> set[int]:
    selected: set[int] = set()
    for line in core_lines:
        if line < 1 or line > line_count:
            raise SbflLlmError(f"candidate line {line} is outside source range 1..{line_count}")
        selected.update(range(max(1, line - radius), min(line_count, line + radius) + 1))
    return selected


def _render_selected(lines: list[str], selected: set[int]) -> str:
    width = max(4, len(str(len(lines))))
    output: list[str] = []
    previous_rendered: int | None = None
    for number in sorted(selected):
        text = lines[number - 1]
        if not text.strip():
            continue
        if previous_rendered is not None and number > previous_rendered + 1:
            omitted = lines[previous_rendered : number - 1]
            if any(value.strip() for value in omitted):
                output.append(f"... lines {previous_rendered + 1}-{number - 1} omitted ...")
        output.append(f"{number:{width}d}: {text.rstrip()}")
        previous_rendered = number
    return "\n".join(output)


def _build_regions(
    rtl_root: Path,
    candidates: Sequence[Candidate],
    mode: str,
    radius: int,
) -> tuple[dict[str, object], ...]:
    by_module: dict[str, list[Candidate]] = defaultdict(list)
    for candidate in candidates:
        by_module[candidate.module].append(candidate)

    regions: list[dict[str, object]] = []
    for module in sorted(by_module):
        module_candidates = by_module[module]
        path = find_module_source(rtl_root, module)
        source_lines = read_text(path).splitlines()
        core_lines = {line for candidate in module_candidates for line in candidate.lines}
        if not core_lines:
            raise SbflLlmError(f"candidates for module {module!r} have no source lines")
        if mode == "full":
            selected = set(range(1, len(source_lines) + 1))
        else:
            selected = _expanded_lines(core_lines, len(source_lines), radius)

        regions.append(
            {
                "module": module,
                "path": str(path),
                "candidate_ranges": {
                    candidate.candidate_id: compress_line_ranges(candidate.lines)
                    for candidate in module_candidates
                },
                "source": _render_selected(source_lines, selected),
            }
        )
    return tuple(regions)


def _source_chars(regions: Sequence[dict[str, object]]) -> int:
    return sum(len(str(region["source"])) for region in regions)


def collect_sources(
    rtl_root: Path,
    candidates: Sequence[Candidate],
    source_mode: str,
    max_chars: int,
    snippet_radius: int,
) -> SourceBundle:
    if source_mode not in {"snippets", "full", "auto"}:
        raise SbflLlmError(f"unknown source mode: {source_mode}")

    if source_mode in {"full", "auto"}:
        full = _build_regions(rtl_root, candidates, "full", snippet_radius)
        full_chars = _source_chars(full)
        if source_mode == "full":
            if full_chars > max_chars:
                raise SbflLlmError(
                    f"full RTL context needs {full_chars} characters, exceeding {max_chars}"
                )
            return SourceBundle(full, "full", None, full_chars)
        if full_chars <= max_chars:
            return SourceBundle(full, "full", None, full_chars)

    # Adaptively shrink context but never omit a candidate's own block lines.
    for radius in range(snippet_radius, -1, -1):
        regions = _build_regions(rtl_root, candidates, "snippets", radius)
        chars = _source_chars(regions)
        if chars <= max_chars:
            return SourceBundle(regions, "snippets", radius, chars)

    core = _build_regions(rtl_root, candidates, "snippets", 0)
    core_chars = _source_chars(core)
    raise SbflLlmError(
        f"candidate block code needs {core_chars} characters, exceeding "
        f"--max-source-chars={max_chars}; reduce --candidate-count or increase the limit"
    )
