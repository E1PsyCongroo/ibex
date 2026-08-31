"""Shared models and provider interfaces for LLM requests."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

ResolvedApiProtocol = Literal[
    "anthropic",
    "zai",
    "openai-responses",
    "openai-chat-completions",
]
StructuredMode = Literal["json_schema", "json_object", "prompt_only"]


@dataclass(frozen=True)
class Candidate:
    candidate_id: str
    original_rank: int
    suspiciousness: str
    module: str
    scope: str
    bid: int
    lines: tuple[int, ...]
    block_type: str

    def to_dict(self) -> dict[str, object]:
        value = asdict(self)
        value["lines"] = list(self.lines)
        return value


class CandidateAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(description="An ID from the supplied candidate list")
    score: float = Field(ge=0.0, le=1.0)


class ReasonedCandidateAssessment(CandidateAssessment):
    reason: str = Field(min_length=1, max_length=1000)


class AssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: list[CandidateAssessment]


class ReasonedAssessmentResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: list[ReasonedCandidateAssessment]


AssessmentResponseType = AssessmentResponse | ReasonedAssessmentResponse


def assessment_response_model(
    include_reason: bool,
) -> type[AssessmentResponse] | type[ReasonedAssessmentResponse]:
    return ReasonedAssessmentResponse if include_reason else AssessmentResponse


@dataclass(frozen=True)
class ApiResult:
    response: AssessmentResponseType
    raw_text: str
    response_id: str | None
    usage: dict[str, int]
    structured_output: str
    attempts: int
    elapsed_seconds: float
    api: str = "openai-responses"


@dataclass(frozen=True)
class ProviderRequest:
    model: str
    system_prompt: str
    input_items: Sequence[dict[str, str]]
    temperature: float | None
    max_output_tokens: int
    reasoning_effort: str
    include_reason: bool


class ModelProvider(Protocol):
    protocol: ResolvedApiProtocol

    def structured_modes(self, structured_output: str) -> tuple[StructuredMode, ...]: ...

    def create(self, request: ProviderRequest, mode: StructuredMode) -> Any: ...

    def output_text(self, response: Any) -> str: ...
