"""Combine model and SBFL evidence into a deterministic ranking."""

from __future__ import annotations

import math
from collections.abc import Sequence

from .errors import SbflLlmError
from .llm.models import AssessmentResponseType, Candidate


def _parse_suspiciousness(candidate: Candidate) -> float:
    try:
        value = float(candidate.suspiciousness)
    except ValueError as exc:
        raise SbflLlmError(
            f"candidate {candidate.candidate_id} has non-numeric suspiciousness "
            f"{candidate.suspiciousness!r}"
        ) from exc
    if not math.isfinite(value) or value < 0:
        raise SbflLlmError(f"candidate {candidate.candidate_id} has invalid suspiciousness {value}")
    return value


def rank_candidates(
    candidates: Sequence[Candidate],
    response: AssessmentResponseType,
    llm_weight: float,
) -> list[dict[str, object]]:
    assessments = {item.candidate_id: item for item in response.assessments}
    suspiciousness = {item.candidate_id: _parse_suspiciousness(item) for item in candidates}

    scored: list[dict[str, object]] = []
    for candidate in candidates:
        assessment = assessments[candidate.candidate_id]
        final_score = (
            llm_weight * assessment.score
            + (1.0 - llm_weight) * suspiciousness[candidate.candidate_id]
        )

        value = candidate.to_dict()
        value.update(
            {
                "llm_score": round(assessment.score, 12),
                "sbfl_score": round(suspiciousness[candidate.candidate_id], 12),
                "final_score": round(final_score, 12),
            }
        )
        reason = getattr(assessment, "reason", None)
        if reason is not None:
            value["reason"] = reason
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
