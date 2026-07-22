import json

import pytest

from ibex_sbfl_llm.errors import ModelResponseError
from ibex_sbfl_llm.models import Candidate
from ibex_sbfl_llm.openai_client import validate_assessments


def candidate():
    return Candidate("B001", 1, "1.0", "m", "s", 1, (10, 11), "Assign")


def response(candidate_id="B001", key_lines=None):
    return json.dumps(
        {
            "assessments": [
                {
                    "candidate_id": candidate_id,
                    "score": 0.8,
                    "causal_role": "probable_root_cause",
                    "key_lines": [10] if key_lines is None else key_lines,
                    "reason": "wrong predicate",
                }
            ]
        }
    )


def test_validates_exact_candidate_set():
    parsed = validate_assessments(response(), [candidate()])
    assert parsed.assessments[0].score == 0.8
    with pytest.raises(ModelResponseError, match="candidate set mismatch"):
        validate_assessments(response("B999"), [candidate()])


def test_rejects_key_lines_outside_block():
    with pytest.raises(ModelResponseError, match="outside its block"):
        validate_assessments(response(key_lines=[99]), [candidate()])
