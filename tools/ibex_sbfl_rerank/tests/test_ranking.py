from ibex_sbfl_rerank.ranking import Candidate, rank_candidates


def _candidates() -> list[Candidate]:
    return [
        Candidate("B001", 1, "0.2", "m", "s", 1, (10,), "Assign"),
        Candidate("B002", 2, "0.1", "m", "s", 2, (20,), "Assign"),
    ]


def _assessments() -> list[dict[str, object]]:
    return [
        {"candidate_id": "B001", "score": 0.0},
        {"candidate_id": "B002", "score": 0.15},
    ]


def test_combination_uses_raw_suspiciousness_without_normalization() -> None:
    ranked = rank_candidates(_candidates(), _assessments(), 0.5)

    # Raw suspiciousness puts B002 first (0.125 vs 0.1). Dividing SBFL by
    # max(suspiciousness) would instead put B001 first (0.5 vs 0.325).
    assert [item["candidate_id"] for item in ranked] == ["B002", "B001"]
    assert ranked[0]["sbfl_score"] == 0.1
    assert ranked[0]["final_score"] == 0.125
    assert "normalized_sbfl_score" not in ranked[0]


def test_weight_one_uses_only_llm_score() -> None:
    ranked = rank_candidates(_candidates(), _assessments(), 1.0)

    assert [item["candidate_id"] for item in ranked] == ["B002", "B001"]
