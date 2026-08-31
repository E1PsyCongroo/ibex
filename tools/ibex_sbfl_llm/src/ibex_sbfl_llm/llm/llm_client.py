"""Provider-independent retry and model-response validation."""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Sequence
from typing import Any, cast

from pydantic import ValidationError

from ..errors import ModelResponseError, SbflLlmError
from ..prompting import build_repair_prompt
from .claude import ClaudeClient
from .glm import GlmClient
from .gpt import GptClient
from .models import (
    ApiResult,
    AssessmentResponseType,
    Candidate,
    ModelProvider,
    ProviderRequest,
    ResolvedApiProtocol,
    assessment_response_model,
)


def resolve_api_protocol(model: str, requested: str) -> ResolvedApiProtocol:
    if requested != "auto":
        return cast(ResolvedApiProtocol, requested)
    model_name = model.lower().rsplit("/", 1)[-1]
    if model_name.startswith("claude-"):
        return "anthropic"
    if model_name.startswith("glm-"):
        return "zai"
    if model_name.startswith("gpt-"):
        return "openai-responses"
    return "openai-chat-completions"


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", stripped, re.DOTALL)
    if fenced:
        stripped = fenced.group(1)
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start < 0 or end <= start:
            raise ModelResponseError("model response does not contain a JSON object") from None
        try:
            value = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError as exc:
            raise ModelResponseError(f"model returned invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ModelResponseError("model response JSON must be an object")
    return value


def validate_assessments(
    text: str,
    candidates: Sequence[Candidate],
    *,
    include_reason: bool = False,
) -> AssessmentResponseType:
    response_type = assessment_response_model(include_reason)
    try:
        response = response_type.model_validate(extract_json_object(text))
    except ValidationError as exc:
        raise ModelResponseError(f"response schema validation failed: {exc}") from exc

    candidate_map = {candidate.candidate_id: candidate for candidate in candidates}
    returned_ids = [assessment.candidate_id for assessment in response.assessments]
    duplicates = sorted({value for value in returned_ids if returned_ids.count(value) > 1})
    if duplicates:
        raise ModelResponseError(f"duplicate candidate IDs: {duplicates}")
    unknown = sorted(set(returned_ids) - set(candidate_map))
    missing = sorted(set(candidate_map) - set(returned_ids))
    if unknown or missing:
        raise ModelResponseError(f"candidate set mismatch; unknown={unknown}, missing={missing}")

    if include_reason:
        for assessment in response.assessments:
            assessment.reason = assessment.reason.strip()
    return response


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
    if not isinstance(value, int):
        value = getattr(getattr(exc, "response", None), "status_code", None)
    return int(value) if isinstance(value, int) else None


def _structured_output_unsupported(exc: Exception) -> bool:
    if _status_code(exc) not in {400, 404, 422}:
        return False
    text = str(exc).lower()
    return any(
        marker in text
        for marker in (
            "text.format",
            "json_schema",
            "json schema",
            "json mode",
            "json_object",
            "response_format",
            "structured output",
            "output_config",
        )
    )


def _transient(exc: Exception) -> bool:
    status = _status_code(exc)
    if status is not None:
        return status == 429 or status >= 500
    name = type(exc).__name__.lower()
    return any(marker in name for marker in ("timeout", "connection", "ratelimit"))


def _usage_dict(response: Any) -> dict[str, int]:
    usage = (
        response.get("usage") if isinstance(response, dict) else getattr(response, "usage", None)
    )
    if usage is None:
        return {}

    def usage_value(field: str) -> Any:
        return usage.get(field) if isinstance(usage, dict) else getattr(usage, field, None)

    result: dict[str, int] = {}
    fields = {
        "input_tokens": ("input_tokens", "prompt_tokens"),
        "output_tokens": ("output_tokens", "completion_tokens"),
        "total_tokens": ("total_tokens",),
    }
    for output_field, source_fields in fields.items():
        value = next(
            (
                candidate
                for field in source_fields
                if isinstance((candidate := usage_value(field)), int)
            ),
            None,
        )
        if isinstance(value, int):
            result[output_field] = value
    if "total_tokens" not in result and {"input_tokens", "output_tokens"} <= result.keys():
        result["total_tokens"] = result["input_tokens"] + result["output_tokens"]
    return result


def _build_provider(
    *,
    api_protocol: ResolvedApiProtocol,
    api_base: str,
    api_key: str | None,
    timeout: float,
) -> ModelProvider:
    options = {"api_base": api_base, "api_key": api_key, "timeout": timeout}
    if api_protocol == "anthropic":
        return ClaudeClient(**options)
    if api_protocol == "zai":
        return GlmClient(**options)
    return GptClient(protocol=api_protocol, **options)


def call_model(
    *,
    api_base: str,
    api_key: str | None,
    model: str,
    system_prompt: str,
    user_prompt: str,
    candidates: Sequence[Candidate],
    timeout: float,
    temperature: float | None,
    retries: int,
    retry_delay: float,
    structured_output: str,
    api_protocol: ResolvedApiProtocol = "openai-responses",
    max_output_tokens: int = 49152,
    reasoning_effort: str = "high",
    include_reason: bool = False,
) -> ApiResult:
    provider = _build_provider(
        api_protocol=api_protocol,
        api_base=api_base,
        api_key=api_key,
        timeout=timeout,
    )
    modes = provider.structured_modes(structured_output)
    mode_index = 0
    input_items: list[dict[str, str]] = [{"role": "user", "content": user_prompt}]
    attempts = 0
    transient_failures = 0
    validation_failures = 0
    started = time.monotonic()

    while True:
        mode = modes[mode_index]
        request = ProviderRequest(
            model=model,
            system_prompt=system_prompt,
            input_items=input_items,
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            reasoning_effort=reasoning_effort,
            include_reason=include_reason,
        )
        attempts += 1
        try:
            response = provider.create(request, mode)
        except Exception as exc:
            if mode_index + 1 < len(modes) and _structured_output_unsupported(exc):
                mode_index += 1
                continue
            if _transient(exc) and transient_failures < retries:
                transient_failures += 1
                delay = retry_delay * (2 ** (transient_failures - 1))
                time.sleep(delay + random.uniform(0.0, delay * 0.2))
                continue
            raise SbflLlmError(f"LLM API request failed: {exc}") from exc

        raw_text = provider.output_text(response)
        if not raw_text.strip():
            status = getattr(response, "status", None)
            error = getattr(response, "error", None)
            detail = (
                error
                or getattr(response, "incomplete_details", None)
                or getattr(response, "stop_reason", None)
                or status
            )
            raise SbflLlmError(
                f"LLM {provider.protocol} API returned no text" + (f": {detail}" if detail else "")
            )

        try:
            validated = validate_assessments(
                raw_text,
                candidates,
                include_reason=include_reason,
            )
        except ModelResponseError as exc:
            if validation_failures >= retries:
                raise
            validation_failures += 1
            input_items.extend(
                [
                    {"role": "assistant", "content": raw_text},
                    {
                        "role": "user",
                        "content": build_repair_prompt(
                            str(exc),
                            candidates,
                            include_reason=include_reason,
                        ),
                    },
                ]
            )
            time.sleep(retry_delay * validation_failures)
            continue

        return ApiResult(
            response=validated,
            raw_text=raw_text,
            response_id=str(getattr(response, "id", "")) or None,
            usage=_usage_dict(response),
            structured_output=mode,
            attempts=attempts,
            elapsed_seconds=time.monotonic() - started,
            api=provider.protocol,
        )
