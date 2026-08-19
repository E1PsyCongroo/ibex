import json
from types import SimpleNamespace

from ibex_sbfl_llm.llm_client import call_model
from ibex_sbfl_llm.models import Candidate


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
    monkeypatch.setattr("ibex_sbfl_llm.llm_client.OpenAI", FakeOpenAI)
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
    assert calls[0]["reasoning"] == {"effort": "high"}
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
    monkeypatch.setattr("ibex_sbfl_llm.llm_client.OpenAI", ReasonOpenAI)
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


class FakeChatCompletions:
    def __init__(self):
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        content = json.dumps(
            {
                "assessments": [
                    {
                        "candidate_id": "B001",
                        "score": 0.7,
                    }
                ]
            }
        )
        usage = SimpleNamespace(prompt_tokens=12, completion_tokens=6, total_tokens=18)
        message = SimpleNamespace(content=content)
        return SimpleNamespace(
            id="chat-1",
            choices=[SimpleNamespace(message=message)],
            usage=usage,
        )


class FakeChatOpenAI:
    instance = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.chat = SimpleNamespace(completions=FakeChatCompletions())
        FakeChatOpenAI.instance = self


def test_chat_completions_protocol(monkeypatch):
    monkeypatch.setattr("ibex_sbfl_llm.llm_client.OpenAI", FakeChatOpenAI)
    candidates = [Candidate("B001", 1, "1", "m", "s", 1, (10,), "Assign")]
    result = call_model(
        api_base="http://localhost:8000/v1",
        api_key=None,
        model="openai-compatible-model",
        system_prompt="system",
        user_prompt="user JSON",
        candidates=candidates,
        timeout=1,
        temperature=None,
        retries=0,
        retry_delay=0,
        structured_output="auto",
        api_protocol="chat-completions",
    )

    assert result.api == "chat-completions"
    assert result.structured_output == "json_schema"
    assert result.usage == {
        "input_tokens": 12,
        "output_tokens": 6,
        "total_tokens": 18,
    }
    calls = FakeChatOpenAI.instance.chat.completions.calls
    assert calls[0]["model"] == "openai-compatible-model"
    assert calls[0]["messages"] == [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "user JSON"},
    ]
    assert calls[0]["response_format"]["type"] == "json_schema"
    assert calls[0]["reasoning_effort"] == "high"


class FakeAnthropicMessages:
    def __init__(self):
        self.calls = []

    def create(self, **payload):
        self.calls.append(payload)
        content = json.dumps(
            {
                "assessments": [
                    {
                        "candidate_id": "B001",
                        "score": 0.95,
                    }
                ]
            }
        )
        return SimpleNamespace(
            id="msg-1",
            content=[SimpleNamespace(type="text", text=content)],
            usage=SimpleNamespace(input_tokens=14, output_tokens=7),
            stop_reason="end_turn",
        )


class FakeAnthropic:
    instance = None

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.messages = FakeAnthropicMessages()
        FakeAnthropic.instance = self


def test_anthropic_messages_protocol_uses_anthropic_sdk(monkeypatch):
    monkeypatch.setattr("ibex_sbfl_llm.llm_client.Anthropic", FakeAnthropic)
    candidates = [Candidate("B001", 1, "1", "m", "s", 1, (10,), "Assign")]
    result = call_model(
        api_base="http://localhost:8000/v1/messages",
        api_key="test-key",
        model="claude-sonnet-4-6",
        system_prompt="system",
        user_prompt="user JSON",
        candidates=candidates,
        timeout=2,
        temperature=0.2,
        retries=0,
        retry_delay=0,
        structured_output="auto",
        api_protocol="anthropic",
        max_output_tokens=4096,
    )

    assert result.api == "anthropic"
    assert result.structured_output == "json_schema"
    assert result.usage == {
        "input_tokens": 14,
        "output_tokens": 7,
        "total_tokens": 21,
    }
    assert FakeAnthropic.instance.kwargs == {
        "api_key": "test-key",
        "base_url": "http://localhost:8000",
        "timeout": 2,
        "max_retries": 0,
    }
    call = FakeAnthropic.instance.messages.calls[0]
    assert call["model"] == "claude-sonnet-4-6"
    assert call["max_tokens"] == 4096
    assert call["system"] == "system"
    assert call["messages"] == [{"role": "user", "content": "user JSON"}]
    assert call["temperature"] == 0.2
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "schema" in call["output_config"]["format"]
    assert call["output_config"]["effort"] == "high"
