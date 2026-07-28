"""Shared internal and structured-response models."""

from __future__ import annotations

from dataclasses import asdict, dataclass

from pydantic import BaseModel, ConfigDict, Field


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


@dataclass(frozen=True)
class ApiResult:
    response: AssessmentResponseType
    raw_text: str
    response_id: str | None
    usage: dict[str, int]
    structured_output: str
    attempts: int
    elapsed_seconds: float
