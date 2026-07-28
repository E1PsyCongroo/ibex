"""Combine model and SBFL evidence into a deterministic ranking."""

from __future__ import annotations

import math
from collections.abc import Sequence

from .errors import SbflLlmError
from .models import AssessmentResponseType, Candidate


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
    strategy: str,
    llm_weight: float,
) -> list[dict[str, object]]:
    assessments = {item.candidate_id: item for item in response.assessments}
    suspiciousness = {item.candidate_id: _parse_suspiciousness(item) for item in candidates}
    max_suspiciousness = max(suspiciousness.values(), default=0.0)
    normalized_sbfl = {
        candidate_id: (value / max_suspiciousness if max_suspiciousness > 0 else 0.0)
        for candidate_id, value in suspiciousness.items()
    }

    llm_order = sorted(
        candidates,
        key=lambda item: (
            -assessments[item.candidate_id].score,
            -suspiciousness[item.candidate_id],
            item.original_rank,
            item.candidate_id,
        ),
    )
    llm_rank = {candidate.candidate_id: rank for rank, candidate in enumerate(llm_order, 1)}

    scored: list[dict[str, object]] = []
    for candidate in candidates:
        assessment = assessments[candidate.candidate_id]
        if strategy == "llm-only":
            final_score = assessment.score
        elif strategy == "weighted":
            final_score = (
                llm_weight * assessment.score
                + (1.0 - llm_weight) * normalized_sbfl[candidate.candidate_id]
            )
        elif strategy == "rrf":
            rrf_k = 60.0
            final_score = llm_weight / (rrf_k + llm_rank[candidate.candidate_id]) + (
                1.0 - llm_weight
            ) / (rrf_k + candidate.original_rank)
        else:
            raise SbflLlmError(f"unknown ranking strategy: {strategy}")

        value = candidate.to_dict()
        value.update(
            {
                "llm_score": round(assessment.score, 12),
                "normalized_sbfl_score": round(normalized_sbfl[candidate.candidate_id], 12),
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
            -float(item["normalized_sbfl_score"]),
            int(item["original_rank"]),
            str(item["candidate_id"]),
        )
    )
    for rank, item in enumerate(scored, 1):
        item["reranked_rank"] = rank
    return scored
