"""OpenAI SDK adapter with structured-output fallback and strict validation."""

from __future__ import annotations

import json
import random
import re
import time
from collections.abc import Sequence
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from .errors import ModelResponseError, SbflLlmError
from .models import (
    ApiResult,
    AssessmentResponse,
    AssessmentResponseType,
    Candidate,
    ReasonedAssessmentResponse,
)
from .prompting import build_repair_prompt


def normalize_base_url(base_url: str) -> str:
    value = base_url.rstrip("/")
    if value.endswith("/responses"):
        return value[: -len("/responses")]
    return value


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
    return _parse_assessments(text, candidates, include_reason=include_reason)


def _parse_assessments(
    text: str,
    candidates: Sequence[Candidate],
    *,
    include_reason: bool,
) -> AssessmentResponseType:
    response_type = ReasonedAssessmentResponse if include_reason else AssessmentResponse
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


def _text_format(mode: str, *, include_reason: bool) -> dict[str, Any] | None:
    if mode == "json_schema":
        response_type = ReasonedAssessmentResponse if include_reason else AssessmentResponse
        return {
            "type": "json_schema",
            "name": "candidate_assessments",
            "strict": True,
            "schema": response_type.model_json_schema(),
        }
    if mode == "json_object":
        return {"type": "json_object"}
    return None


def _status_code(exc: Exception) -> int | None:
    value = getattr(exc, "status_code", None)
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
            "structured output",
        )
    )


def _transient(exc: Exception) -> bool:
    status = _status_code(exc)
    if status is not None:
        return status == 429 or status >= 500
    name = type(exc).__name__.lower()
    return any(marker in name for marker in ("timeout", "connection", "ratelimit"))


def _usage_dict(response: Any) -> dict[str, int]:
    usage = getattr(response, "usage", None)
    if usage is None:
        return {}
    result: dict[str, int] = {}
    for field in ("input_tokens", "output_tokens", "total_tokens"):
        value = getattr(usage, field, None)
        if isinstance(value, int):
            result[field] = value
    return result


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
    include_reason: bool = False,
) -> ApiResult:
    # The SDK requires credential configuration at construction time. A callable
    # returning an empty string satisfies that configuration without emitting an
    # Authorization header for explicitly no-auth compatible endpoints.
    credential = api_key if api_key else (lambda: "")
    client = OpenAI(
        api_key=credential,
        base_url=normalize_base_url(api_base),
        timeout=timeout,
        max_retries=0,
    )
    modes = {
        "strict": ["json_schema"],
        "auto": ["json_schema", "json_object", "prompt_only"],
        "off": ["prompt_only"],
    }[structured_output]
    mode_index = 0
    input_items: list[dict[str, str]] = [{"role": "user", "content": user_prompt}]
    attempts = 0
    transient_failures = 0
    validation_failures = 0
    started = time.monotonic()

    while True:
        mode = modes[mode_index]
        payload: dict[str, Any] = {
            "model": model,
            "instructions": system_prompt,
            "input": input_items,
            "store": False,
        }
        text_format = _text_format(mode, include_reason=include_reason)
        if text_format is not None:
            payload["text"] = {"format": text_format}
        if temperature is not None:
            payload["temperature"] = temperature
        attempts += 1
        try:
            response = client.responses.create(**payload)
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

        raw_text = getattr(response, "output_text", "")
        if not isinstance(raw_text, str) or not raw_text.strip():
            status = getattr(response, "status", None)
            error = getattr(response, "error", None)
            detail = error or getattr(response, "incomplete_details", None) or status
            raise SbflLlmError(
                "LLM Responses API returned no output_text" + (f": {detail}" if detail else "")
            )

        try:
            validated = _parse_assessments(
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
        )
