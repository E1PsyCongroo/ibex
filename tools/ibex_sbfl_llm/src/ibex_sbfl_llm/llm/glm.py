"""GLM Z.AI Chat Completions API adapter."""

from __future__ import annotations

from typing import Any

from zai import ZaiClient

from .models import ProviderRequest, StructuredMode


def normalize_base_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    for suffix in ("/chat/completions", "/responses"):
        if value.endswith(suffix):
            return value[: -len(suffix)]
    return value


class GlmClient:
    protocol = "zai"

    def __init__(self, *, api_base: str, api_key: str | None, timeout: float) -> None:
        self.client = ZaiClient(
            api_key=api_key or "no-auth",
            base_url=normalize_base_url(api_base),
            timeout=timeout,
            max_retries=0,
        )

    def structured_modes(self, structured_output: str) -> tuple[StructuredMode, ...]:
        return {
            "strict": ("json_object",),
            "auto": ("json_object", "prompt_only"),
            "off": ("prompt_only",),
        }[structured_output]

    def create(self, request: ProviderRequest, mode: StructuredMode) -> Any:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                *request.input_items,
            ],
            "max_tokens": request.max_output_tokens,
            "thinking": {"type": "enabled"},
        }
        if mode == "json_object":
            payload["response_format"] = {"type": "json_object"}
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        return self.client.chat.completions.create(**payload)

    def output_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None)
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        content = getattr(message, "content", "")
        return content if isinstance(content, str) else ""
