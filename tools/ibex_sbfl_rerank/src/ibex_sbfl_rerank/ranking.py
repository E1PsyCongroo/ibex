"""Combine raw model scores and unnormalized SBFL suspiciousness."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any


class RerankError(ValueError):
    """Raised when a saved reranking artifact cannot be processed."""


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    original_rank: int
    suspiciousness: str
    module: str
    scope: str
    bid: int
    lines: tuple[int, ...]
    block_type: str

    def to_dict(self) -> dict[str, object]:
        result = asdict(self)
        result["lines"] = list(self.lines)
        return result


def _finite_number(value: object, label: str) -> float:
    if isinstance(value, bool):
        raise RerankError(f"{label} must be a number, got {value!r}")
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise RerankError(f"{label} must be a number, got {value!r}") from exc
    if not math.isfinite(number):
        raise RerankError(f"{label} must be finite, got {value!r}")
    return number


def parse_suspiciousness(candidate: Candidate) -> float:
    value = _finite_number(
        candidate.suspiciousness,
        f"candidate {candidate.candidate_id} suspiciousness",
    )
    if value < 0:
        raise RerankError(
            f"candidate {candidate.candidate_id} suspiciousness must be non-negative"
        )
    return value


def validate_scores(
    candidates: Sequence[Candidate], assessments: Sequence[Mapping[str, Any]]
) -> dict[str, Mapping[str, Any]]:
    candidate_ids = {candidate.candidate_id for candidate in candidates}
    result: dict[str, Mapping[str, Any]] = {}
    for index, assessment in enumerate(assessments, 1):
        candidate_id = str(assessment.get("candidate_id", ""))
        if not candidate_id:
            raise RerankError(f"assessment {index} has an empty candidate_id")
        if candidate_id in result:
            raise RerankError(f"duplicate assessment for candidate {candidate_id}")
        score = _finite_number(assessment.get("score"), f"candidate {candidate_id} LLM score")
        if not 0.0 <= score <= 1.0:
            raise RerankError(f"candidate {candidate_id} LLM score is outside [0, 1]: {score}")
        result[candidate_id] = {**assessment, "candidate_id": candidate_id, "score": score}

    unknown = sorted(result.keys() - candidate_ids)
    missing = sorted(candidate_ids - result.keys())
    if unknown or missing:
        raise RerankError(f"candidate set mismatch; unknown={unknown}, missing={missing}")
    return result


def rank_candidates(
    candidates: Sequence[Candidate],
    assessments: Sequence[Mapping[str, Any]],
    llm_weight: float,
) -> list[dict[str, object]]:
    """Rank candidates without scaling or normalizing suspiciousness."""
    if not 0.0 <= llm_weight <= 1.0:
        raise RerankError(f"llm_weight must be in [0, 1], got {llm_weight}")

    assessment_map = validate_scores(candidates, assessments)
    suspiciousness = {
        candidate.candidate_id: parse_suspiciousness(candidate) for candidate in candidates
    }
    scored: list[dict[str, object]] = []
    for candidate in candidates:
        assessment = assessment_map[candidate.candidate_id]
        llm_score = float(assessment["score"])
        sbfl_score = suspiciousness[candidate.candidate_id]
        final_score = llm_weight * llm_score + (1.0 - llm_weight) * sbfl_score

        value = candidate.to_dict()
        value.update(
            {
                "llm_score": round(llm_score, 12),
                "sbfl_score": round(sbfl_score, 12),
                "final_score": round(final_score, 12),
            }
        )
        reason = assessment.get("reason")
        if reason is not None:
            value["reason"] = str(reason).strip()
        scored.append(value)

    scored.sort(
        key=lambda item: (
            -float(item["final_score"]),
            -float(item["llm_score"]),
            -float(item["sbfl_score"]),
            int(item["original_rank"]),
            str(item["candidate_id"]),
        )
    )
    for rank, item in enumerate(scored, 1):
        item["reranked_rank"] = rank
    return scored
