"""Claude Messages API adapter."""

from __future__ import annotations

from typing import Any

from anthropic import Anthropic, transform_schema

from .models import ProviderRequest, StructuredMode, assessment_response_model


def normalize_base_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/messages"):
        value = value[: -len("/messages")]
    if value.endswith("/v1"):
        value = value[: -len("/v1")]
    return value


class ClaudeClient:
    protocol = "anthropic"

    def __init__(self, *, api_base: str, api_key: str | None, timeout: float) -> None:
        self.client = Anthropic(
            api_key=api_key or "no-auth",
            base_url=normalize_base_url(api_base),
            timeout=timeout,
            max_retries=0,
        )

    def structured_modes(self, structured_output: str) -> tuple[StructuredMode, ...]:
        return {
            "strict": ("json_schema",),
            "auto": ("json_schema", "prompt_only"),
            "off": ("prompt_only",),
        }[structured_output]

    def create(self, request: ProviderRequest, mode: StructuredMode) -> Any:
        payload: dict[str, Any] = {
            "model": request.model,
            "max_tokens": request.max_output_tokens,
            "system": request.system_prompt,
            "messages": list(request.input_items),
            "output_config": {"effort": request.reasoning_effort},
        }
        if mode == "json_schema":
            response_type = assessment_response_model(request.include_reason)
            payload["output_config"]["format"] = {
                "type": "json_schema",
                "schema": transform_schema(response_type.model_json_schema()),
            }
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return self.client.messages.create(**payload)

    def output_text(self, response: Any) -> str:
        content = (
            response.get("content")
            if isinstance(response, dict)
            else getattr(response, "content", None)
        )
        if not isinstance(content, list):
            return ""
        texts: list[str] = []
        for block in content:
            block_type = (
                block.get("type") if isinstance(block, dict) else getattr(block, "type", None)
            )
            block_text = (
                block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
            )
            if block_type == "text" and isinstance(block_text, str):
                texts.append(block_text)
        return "".join(texts)
