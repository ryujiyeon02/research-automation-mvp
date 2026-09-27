from __future__ import annotations

from dataclasses import dataclass
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


@dataclass(frozen=True)
class FilingRef:
    cik: str
    company_name: str
    accession_number: str
    form: str
    filing_date: str
    report_date: Optional[str]
    primary_document: str
    url: str


class CausalClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cause_variable: str = Field(min_length=1, max_length=200)
    effect_variable: str = Field(min_length=1, max_length=200)
    direction: Literal["increase", "decrease", "mixed", "unknown"]
    transmission_channel: str = Field(min_length=1, max_length=300)
    horizon: Literal["immediate", "short", "medium", "long", "unknown"]
    modality: Literal["observed", "forecast", "possible", "conditional", "uncertain"]
    confidence: float = Field(ge=0.0, le=1.0)
    evidence_text: str = Field(min_length=5)

    @field_validator("evidence_text")
    @classmethod
    def evidence_must_be_compact(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if len(normalized) > 2_000:
            raise ValueError("evidence_text is too long")
        return normalized


class ClaimsEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claims: List[CausalClaim]
