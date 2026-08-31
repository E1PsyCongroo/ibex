"""GPT OpenAI Responses and Chat Completions API adapter."""

from __future__ import annotations

from typing import Any, cast

from openai import OpenAI

from .models import (
    ProviderRequest,
    ResolvedApiProtocol,
    StructuredMode,
    assessment_response_model,
)


def normalize_base_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


def _text_format(mode: StructuredMode, *, include_reason: bool) -> dict[str, Any] | None:
    if mode == "json_schema":
        response_type = assessment_response_model(include_reason)
        return {
            "type": "json_schema",
            "name": "candidate_assessments",
            "strict": True,
            "schema": response_type.model_json_schema(),
        }
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def _chat_response_format(
    mode: StructuredMode,
    *,
    include_reason: bool,
) -> dict[str, Any] | None:
    text_format = _text_format(mode, include_reason=include_reason)
    if text_format is None or text_format["type"] == "json_object":
        return text_format
    return {
        "type": "json_schema",
        "json_schema": {
            "name": text_format["name"],
            "strict": text_format["strict"],
            "schema": text_format["schema"],
        },
    }


class GptClient:
    def __init__(
        self,
        *,
        protocol: str,
        api_base: str,
        api_key: str | None,
        timeout: float,
    ) -> None:
        if protocol not in {"openai-responses", "openai-chat-completions"}:
            raise ValueError(f"unsupported GPT API protocol: {protocol}")
        self.protocol = cast(ResolvedApiProtocol, protocol)
        credential = api_key if api_key else (lambda: "")
        self.client = OpenAI(
            api_key=credential,
            base_url=normalize_base_url(api_base),
            timeout=timeout,
            max_retries=0,
        )

    def structured_modes(self, structured_output: str) -> tuple[StructuredMode, ...]:
        return {
            "strict": ("json_schema",),
            "auto": ("json_schema", "json_object", "prompt_only"),
            "off": ("prompt_only",),
        }[structured_output]

    def create(self, request: ProviderRequest, mode: StructuredMode) -> Any:
        if self.protocol == "openai-chat-completions":
            return self._create_chat_completion(request, mode)
        return self._create_response(request, mode)

    def _create_response(self, request: ProviderRequest, mode: StructuredMode) -> Any:
        payload: dict[str, Any] = {
            "model": request.model,
            "reasoning": {"effort": request.reasoning_effort},
            "instructions": request.system_prompt,
            "input": list(request.input_items),
            "store": False,
        }
        text_format = _text_format(mode, include_reason=request.include_reason)
        if text_format is not None:
            payload["text"] = {"format": text_format}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return self.client.responses.create(**payload)

    def _create_chat_completion(
        self,
        request: ProviderRequest,
        mode: StructuredMode,
    ) -> Any:
        payload: dict[str, Any] = {
            "model": request.model,
            "reasoning_effort": request.reasoning_effort,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                *request.input_items,
            ],
        }
        response_format = _chat_response_format(
            mode,
            include_reason=request.include_reason,
        )
        if response_format is not None:
            payload["response_format"] = response_format
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return self.client.chat.completions.create(**payload)

    def output_text(self, response: Any) -> str:
        if self.protocol == "openai-responses":
            value = getattr(response, "output_text", "")
            return value if isinstance(value, str) else ""
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "")
        return content if isinstance(content, str) else ""
