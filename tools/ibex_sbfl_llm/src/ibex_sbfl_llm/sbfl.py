"""LLM candidate construction backed by generic SBFL artifact parsing."""

from __future__ import annotations

import sys
from collections.abc import Mapping, Sequence
from typing import Any

from ibex_sbfl_common.artifacts import load_blocks, parse_block_suspiciousness, resolve_input_files

from .errors import SbflLlmError
from .models import Candidate

__all__ = [
    "build_candidates",
    "load_blocks",
    "parse_block_suspiciousness",
    "resolve_input_files",
]


def build_candidates(
    ranked_blocks: Sequence[Mapping[str, Any]],
    block_map: Mapping[tuple[str, int], Mapping[str, Any]],
    limit: int,
) -> list[Candidate]:
    candidates: list[Candidate] = []
    missing: list[str] = []
    seen: set[tuple[str, int]] = set()
    for raw in ranked_blocks[:limit]:
        scope = str(raw["scope"])
        bid = int(raw["bid"])
        key = (scope, bid)
        if key in seen:
            continue
        seen.add(key)
        block = block_map.get(key)
        if block is None:
            missing.append(f"scope={scope!r}, bid={bid}")
            continue
        try:
            lines = tuple(sorted({int(line) for line in block.get("lines", [])}))
        except (TypeError, ValueError) as exc:
            raise SbflLlmError(f"invalid lines for block {key}: {exc}") from exc
        module = str(block.get("module", ""))
        if not module:
            raise SbflLlmError(f"block {key} has no module in blocks.json")
        candidates.append(
            Candidate(
                candidate_id=f"B{int(raw['rank']):03d}",
                original_rank=int(raw["rank"]),
                suspiciousness=str(raw["sus"]),
                module=module,
                scope=scope,
                bid=bid,
                lines=lines,
                block_type=str(block.get("type", "")),
            )
        )
    if missing:
        preview = "; ".join(missing[:3])
        suffix = " ..." if len(missing) > 3 else ""
        print(
            f"[WARN] skipped {len(missing)} ranked blocks absent from blocks.json: "
            f"{preview}{suffix}",
            file=sys.stderr,
        )
    if not candidates:
        raise SbflLlmError("none of the ranked blocks exists in blocks.json")
    return candidates
