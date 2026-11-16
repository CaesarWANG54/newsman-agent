"""Versioned evidence and proposal types; models cannot approve sources."""

from datetime import datetime
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, field_validator

VERSION = "insight-evidence-v1"
Role = Literal[
    "demand",
    "customer",
    "workflow",
    "solution",
    "alternative",
    "risk",
    "validation",
    "event",
    "transmission",
    "sector",
    "counterevidence",
    "scenario",
    "indicator",
    "company_exposure",
]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceFact(StrictModel):
    id: str = Field(min_length=1, max_length=120)
    role: Role
    text_en: str = Field(min_length=1, max_length=2000)
    text_zh: str = Field(min_length=1, max_length=2000)
    quote: str = Field(min_length=1, max_length=4000)
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    valid_from: datetime
    valid_until: datetime
    entity: str | None = None
    symbol: str | None = None
    contradicts: list[str] = Field(default_factory=list)
    material_adverse: bool = False
    already_priced: bool = False

    @field_validator("valid_from", "valid_until")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Explicit timezone required")
        return value


class EvidenceReview(StrictModel):
    version: Literal["insight-evidence-v1"]
    source_id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    body_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    reviewed_at: datetime
    reviewer: str = Field(min_length=1)
    method: Literal["human_source_review"]
    event_id: str = Field(min_length=1)
    topic: str = Field(min_length=1)
    independence_group: str = Field(min_length=1)
    conflict_status: Literal["clear", "unresolved", "not_reviewed"]
    facts: list[EvidenceFact] = Field(min_length=1, max_length=30)

    @field_validator("reviewed_at")
    @classmethod
    def aware(cls, value):
        return EvidenceFact.aware(value)


class InsightClaim(StrictModel):
    id: str = Field(min_length=1)
    role: Role
    kind: Literal["fact", "source_report", "inference", "hypothesis", "validation"]
    text_en: str = ""
    text_zh: str = ""
    fact_ids: list[str] = Field(min_length=1)
    counter_fact_ids: list[str] = Field(default_factory=list)


class SourceExtraction(StrictModel):
    """Model output cannot claim a reviewer, approval, source identity or completeness."""

    facts: list[EvidenceFact] = Field(default_factory=list, max_length=30)
    conflict_status: Literal["not_detected", "unresolved"]


class MachineEvidence(StrictModel):
    version: Literal["machine-source-v1"]
    method: Literal["machine_source_extraction"]
    verification_status: Literal["not_verified"]
    source_id: str
    article_id: str
    url: str
    body_sha256: str
    normalized_body_sha256: str
    body_scope: Literal["observed_extracted_text_completeness_unknown", "complete_extracted_text"]
    published_at: datetime
    fetched_at: datetime
    extracted_at: datetime
    event_id: str
    topic: str
    independence_group: str
    conflict_status: Literal["not_detected", "unresolved"]
    window: dict
    provenance: dict
    facts: list[EvidenceFact] = Field(default_factory=list, max_length=30)

    @field_validator("published_at", "fetched_at", "extracted_at")
    @classmethod
    def aware(cls, value):
        return EvidenceFact.aware(value)


class CompanyExposure(StrictModel):
    entity: str = Field(min_length=1)
    symbol: str | None = None
    fact_ids: list[str] = Field(min_length=1)


class InsightProposal(StrictModel):
    decision: Literal["analyze", "no_conclusion"]
    reason: Literal[
        "evidence_supported",
        "insufficient_evidence",
        "conflicting_evidence",
        "risk_dominates",
        "already_priced",
    ]
    claims: list[InsightClaim] = Field(default_factory=list, max_length=20)
    companies: list[CompanyExposure] = Field(default_factory=list, max_length=10)


class InsightRead(StrictModel):
    version: Literal["insight-evidence-v1"]
    kind: Literal["startup", "investment"]
    status: Literal["no_conclusion", "review_required"]
    reason: str
    publishable: Literal[False]
    as_of: datetime | None
    valid_until: datetime | None
    confidence: Literal["not_calibrated"]
    confidence_basis: dict
    generation: str
    generation_details: dict = Field(default_factory=dict)
    evidence_context: dict | None
    input_hash: str
    output_hash: str
    proposal: dict | None
    rejected_inputs: list[dict]
    claims: list[InsightClaim]
    companies: list[CompanyExposure]
