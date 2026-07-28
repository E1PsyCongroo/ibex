import json
from types import SimpleNamespace

from ibex_sbfl_llm.models import Candidate
from ibex_sbfl_llm.openai_client import call_model


class UnsupportedSchemaError(Exception):
    status_code = 400


class FakeResponses:
    def __init__(self):
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        if len(self.calls) == 1:
            raise UnsupportedSchemaError("text.format json_schema is unsupported")
        content = json.dumps(
            {
                "assessments": [
                    {
                        "candidate_id": "B001",
                        "score": 0.9,
                    }
                ]
            }
        )
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)
        return SimpleNamespace(
            id="response-1",
            output_text=content,
            usage=usage,
            status="completed",
        )


class FakeOpenAI:
    instance = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.responses = FakeResponses()
        FakeOpenAI.instance = self


def test_structured_output_falls_back_to_json_object(monkeypatch):
    monkeypatch.setattr("ibex_sbfl_llm.openai_client.OpenAI", FakeOpenAI)
    candidates = [Candidate("B001", 1, "1", "m", "s", 1, (10,), "Assign")]
    result = call_model(
        api_base="http://localhost:8000/v1/responses",
        api_key=None,
        model="test",
        system_prompt="system",
        user_prompt="user JSON",
        candidates=candidates,
        timeout=1,
        temperature=None,
        retries=0,
        retry_delay=0,
        structured_output="auto",
    )
    assert result.structured_output == "json_object"
    assert result.attempts == 2
    assert result.usage["input_tokens"] == 10
    assert result.usage["output_tokens"] == 5
    assert result.usage["total_tokens"] == 15
    calls = FakeOpenAI.instance.responses.calls
    assert calls[0]["text"]["format"]["type"] == "json_schema"
    assert calls[0]["text"]["format"]["name"] == "candidate_assessments"
    assert "reason" not in json.dumps(calls[0]["text"]["format"]["schema"])
    assert calls[1]["text"]["format"]["type"] == "json_object"
    assert calls[0]["instructions"] == "system"
    assert calls[0]["input"] == [{"role": "user", "content": "user JSON"}]
    assert calls[0]["store"] is False
    assert str(FakeOpenAI.instance.kwargs["base_url"]) == "http://localhost:8000/v1"


class ReasonResponses:
    def __init__(self):
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        output = {
            "assessments": [
                {
                    "candidate_id": "B001",
                    "score": 0.8,
                    "reason": "The predicate can select the wrong transition.",
                }
            ]
        }
        usage = SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)
        return SimpleNamespace(
            id="response-reason",
            output_text=json.dumps(output),
            usage=usage,
            status="completed",
        )


class ReasonOpenAI:
    instance = None

    def __init__(self, **kwargs):
        self.responses = ReasonResponses()
        ReasonOpenAI.instance = self


def test_include_reason_uses_reasoned_schema(monkeypatch):
    monkeypatch.setattr("ibex_sbfl_llm.openai_client.OpenAI", ReasonOpenAI)
    candidates = [Candidate("B001", 1, "1", "m", "s", 1, (10,), "Assign")]
    result = call_model(
        api_base="http://localhost:8000/v1",
        api_key=None,
        model="test",
        system_prompt="system",
        user_prompt="user JSON",
        candidates=candidates,
        timeout=1,
        temperature=None,
        retries=0,
        retry_delay=0,
        structured_output="strict",
        include_reason=True,
    )

    assessment = result.response.assessments[0]
    assert assessment.score == 0.8
    assert assessment.reason == "The predicate can select the wrong transition."
    calls = ReasonOpenAI.instance.responses.calls
    assert "reason" in json.dumps(calls[0]["text"]["format"]["schema"])
