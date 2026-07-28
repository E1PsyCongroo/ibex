import json

import pytest

from ibex_sbfl_llm.errors import ModelResponseError
from ibex_sbfl_llm.models import Candidate
from ibex_sbfl_llm.openai_client import validate_assessments


def candidate():
    return Candidate("B001", 1, "1.0", "m", "s", 1, (10, 11), "Assign")


def response(candidate_id="B001", reason=None):
    assessment = {
        "candidate_id": candidate_id,
        "score": 0.8,
    }
    if reason is not None:
        assessment["reason"] = reason
    return json.dumps(
        {
            "assessments": [assessment]
        }
    )


def test_validates_exact_candidate_set():
    parsed = validate_assessments(response(), [candidate()])
    assert parsed.assessments[0].score == 0.8
    with pytest.raises(ModelResponseError, match="candidate set mismatch"):
        validate_assessments(response("B999"), [candidate()])


def test_reason_is_forbidden_in_ranking_only_mode():
    with pytest.raises(ModelResponseError, match="response schema validation failed"):
        validate_assessments(response(reason="wrong predicate"), [candidate()])


def test_reason_is_required_and_trimmed_when_enabled():
    with pytest.raises(ModelResponseError, match="response schema validation failed"):
        validate_assessments(response(), [candidate()], include_reason=True)
    parsed = validate_assessments(
        response(reason="  wrong predicate  "),
        [candidate()],
        include_reason=True,
    )
    assert parsed.assessments[0].reason == "wrong predicate"
