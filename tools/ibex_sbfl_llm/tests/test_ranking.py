from ibex_sbfl_llm.models import (
    AssessmentResponse,
    Candidate,
    CandidateAssessment,
    ReasonedAssessmentResponse,
    ReasonedCandidateAssessment,
)
from ibex_sbfl_llm.ranking import rank_candidates


def test_weighted_ranking_and_stable_tie_break():
    candidates = [
        Candidate("B001", 1, "1.0", "m", "s", 1, (1,), "Assign"),
        Candidate("B002", 2, "0.5", "m", "s", 2, (2,), "Assign"),
    ]
    response = AssessmentResponse(
        assessments=[
            CandidateAssessment(
                candidate_id="B001",
                score=0.4,
            ),
            CandidateAssessment(
                candidate_id="B002",
                score=0.9,
            ),
        ]
    )
    ranked = rank_candidates(candidates, response, "weighted", 0.75)
    assert [item["candidate_id"] for item in ranked] == ["B002", "B001"]
    assert ranked[0]["llm_score"] == 0.9
    assert ranked[0]["normalized_sbfl_score"] == 0.5
    assert ranked[0]["reranked_rank"] == 1
    assert "reason" not in ranked[0]
    assert "key_lines" not in ranked[0]
    assert "causal_role" not in ranked[0]


def test_reason_is_copied_only_for_reasoned_response():
    candidate = Candidate("B001", 1, "1.0", "m", "s", 1, (1,), "Assign")
    response = ReasonedAssessmentResponse(
        assessments=[
            ReasonedCandidateAssessment(
                candidate_id="B001",
                score=0.8,
                reason="wrong predicate",
            )
        ]
    )
    ranked = rank_candidates([candidate], response, "llm-only", 1.0)
    assert ranked[0]["reason"] == "wrong predicate"
