"""Parse candidates and model assessments from saved prompt/result pairs."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from .ranking import Candidate, RerankError

_CANDIDATE_HEADING = "# SBFL candidates"


def _json_object_from_text(text: str, label: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise RerankError(f"{label} does not contain a JSON object") from None
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise RerankError(f"invalid JSON in {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise RerankError(f"{label} JSON must be an object")
    return value


def _expand_line_ranges(value: object, candidate_id: str) -> tuple[int, ...]:
    if not isinstance(value, str):
        raise RerankError(f"candidate {candidate_id} line_ranges must be a string")
    lines: set[int] = set()
    for raw_part in value.split(","):
        part = raw_part.strip()
        if not part:
            continue
        try:
            if "-" in part:
                first_text, last_text = part.split("-", 1)
                first, last = int(first_text), int(last_text)
                if first > last:
                    raise ValueError
                lines.update(range(first, last + 1))
            else:
                lines.add(int(part))
        except ValueError as exc:
            raise RerankError(
                f"candidate {candidate_id} has invalid line range {part!r}"
            ) from exc
    if not lines:
        raise RerankError(f"candidate {candidate_id} has no source lines")
    return tuple(sorted(lines))


def parse_prompt_candidates(prompt_path: Path) -> list[Candidate]:
    try:
        prompt = prompt_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RerankError(f"cannot read {prompt_path}: {exc}") from exc
    heading_index = prompt.find(_CANDIDATE_HEADING)
    if heading_index < 0:
        raise RerankError(f"{prompt_path} does not contain {_CANDIDATE_HEADING!r}")
    candidate_section = prompt[heading_index + len(_CANDIDATE_HEADING) :]
    fenced = re.search(r"```json\s*(\[.*?\])\s*```", candidate_section, re.DOTALL)
    if not fenced:
        raise RerankError(f"{prompt_path} does not contain the candidate JSON array")
    try:
        raw_candidates = json.loads(fenced.group(1))
    except json.JSONDecodeError as exc:
        raise RerankError(f"invalid candidate JSON in {prompt_path}: {exc}") from exc
    if not isinstance(raw_candidates, list) or not raw_candidates:
        raise RerankError(f"candidate JSON in {prompt_path} must be a non-empty array")

    candidates: list[Candidate] = []
    seen_ids: set[str] = set()
    seen_ranks: set[int] = set()
    required = (
        "candidate_id",
        "original_rank",
        "suspiciousness",
        "module",
        "scope",
        "bid",
        "block_type",
        "line_ranges",
    )
    for index, raw in enumerate(raw_candidates, 1):
        if not isinstance(raw, Mapping):
            raise RerankError(f"candidate {index} in {prompt_path} is not an object")
        missing = [field for field in required if field not in raw]
        if missing:
            raise RerankError(f"candidate {index} is missing: {', '.join(missing)}")
        candidate_id = str(raw["candidate_id"])
        try:
            original_rank = int(raw["original_rank"])
            bid = int(raw["bid"])
        except (TypeError, ValueError) as exc:
            raise RerankError(f"candidate {candidate_id} has invalid rank or bid") from exc
        if not candidate_id or candidate_id in seen_ids:
            raise RerankError(f"duplicate or empty candidate_id at candidate {index}")
        if original_rank <= 0 or original_rank in seen_ranks:
            raise RerankError(f"duplicate or invalid original_rank for candidate {candidate_id}")
        seen_ids.add(candidate_id)
        seen_ranks.add(original_rank)
        candidates.append(
            Candidate(
                candidate_id=candidate_id,
                original_rank=original_rank,
                suspiciousness=str(raw["suspiciousness"]),
                module=str(raw["module"]),
                scope=str(raw["scope"]),
                bid=bid,
                lines=_expand_line_ranges(raw["line_ranges"], candidate_id),
                block_type=str(raw["block_type"]),
            )
        )
    return candidates


def load_saved_result(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise RerankError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise RerankError(f"invalid JSON in {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise RerankError(f"{path} must contain a JSON object")
    return value


def parse_raw_assessments(result: Mapping[str, Any], result_path: Path) -> list[dict[str, Any]]:
    raw_response = result.get("raw_model_response")
    if isinstance(raw_response, str):
        response = _json_object_from_text(raw_response, f"raw_model_response in {result_path}")
    elif isinstance(raw_response, Mapping):
        response = dict(raw_response)
    else:
        raise RerankError(f"raw_model_response in {result_path} must be text or an object")
    assessments = response.get("assessments")
    if not isinstance(assessments, list) or not assessments:
        raise RerankError(f"raw_model_response in {result_path} has no assessments array")
    if not all(isinstance(item, dict) for item in assessments):
        raise RerankError(f"raw_model_response in {result_path} has a non-object assessment")
    return assessments
