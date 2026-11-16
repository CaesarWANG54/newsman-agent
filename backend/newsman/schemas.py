from datetime import date, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, HttpUrl, Field, field_validator

from newsman.models import FeedbackVote, SourceType
from newsman.insight_contract import InsightRead


class SourceCreate(BaseModel):
    name: str
    url: HttpUrl
    feed_url: HttpUrl | None = None
    source_type: SourceType = SourceType.rss
    category_hint: str = "general"
    credibility: float = 0.8
    enabled: bool = False


class SourceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    url: str
    feed_url: str | None
    source_type: SourceType
    category_hint: str
    credibility: float
    enabled: bool


class SourceHealthRead(SourceRead):
    article_count: int
    latest_article_at: datetime | None
    latest_article_title: str | None
    health_status: str
    observed_status: str = "unverified"
    freshness_status: str = "unknown"
    last_attempt_at: datetime | None = None
    last_success_at: datetime | None = None
    last_body_success_at: datetime | None = None
    next_retry_at: datetime | None = None
    failure_count: int = 0
    http_status: int | None = None
    latency_ms: float | None = None
    body_status: str = "unknown"
    body_results: dict[str, int] = Field(default_factory=dict)
    body_http_statuses: list[int | None] = Field(default_factory=list)
    origin_kind: str = "unknown"
    url_verification: str = "not_verified"
    activation_allowed: bool = False
    fact_verification_status: str = "not_verified"


class SourceEnabledUpdate(BaseModel):
    enabled: bool


class ArticleRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    source_id: UUID | None
    url: str
    canonical_url: str
    title: str
    published_at: datetime | None
    fetched_at: datetime
    category: str
    tags: list[str]
    cluster_key: str | None
    importance_score: float
    summary: str | None
    why_it_matters: str | None


class DigestItemRead(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    title: str
    url: str
    score: float
    tags: list[str] = Field(default_factory=list)
    summary_en: str = ""
    summary_zh: str = ""
    why_it_matters_en: str = ""
    why_it_matters_zh: str = ""
    summary_mode: str = "legacy_unknown"
    quality_status: str = "legacy_unknown"
    translation_status: str = "legacy_unknown"
    body_status: str = "unknown"
    verification_status: str = "not_verified"
    consistency_status: str = "not_checked"
    quality_issues: list[str] = Field(default_factory=list)
    published_at: datetime | None = None
    fetched_at: datetime | None = None
    event_at: datetime | None = None
    time_status: str = "legacy_unknown"
    cluster_id: str | None = None
    clustering_version: str | None = None
    cluster_method: str | None = None
    event_sources: list[dict] = Field(default_factory=list)
    source_count: int | None = None
    document_count: int | None = None
    coverage_status: str = "legacy_unknown"
    evidence_review_required: bool = False
    insight: InsightRead | None = None

    @field_validator(
        "summary_en", "summary_zh", "why_it_matters_en", "why_it_matters_zh", mode="before"
    )
    @classmethod
    def legacy_text(cls, value):
        return value if isinstance(value, str) else ""


class DigestSectionRead(BaseModel):
    model_config = ConfigDict(extra="allow")

    key: str
    title: str
    items: list[DigestItemRead]


class DigestRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    digest_date: date
    title: str
    brief: str
    sections: list[DigestSectionRead]
    markdown: str
    created_at: datetime
    revision: int = 0
    run_id: UUID | None = None
    attempt: int | None = None
    pipeline_version: str = "legacy_unknown"
    source_version: str = "legacy_unknown"
    summary_version: str = "legacy_unknown"
    status: str = "legacy_unknown"
    content_hash: str | None = None
    read_only: bool = True
    publication_status: str = "legacy_read_only"


class FeedbackCreate(BaseModel):
    article_id: UUID
    vote: FeedbackVote
    note: str | None = None


class DailyRunResponse(BaseModel):
    mode: str
    task_id: str | None = None
    digest_id: UUID | None = None
    collected: int | None = None
    summarized: int | None = None
    window: dict | None = None
    status: str | None = None
    run_id: UUID | None = None
    attempt: int | None = None
    revision: int | None = None
    stage: str | None = None
    diagnostics: dict = Field(default_factory=dict)
    retry_at: datetime | None = None
    retry_deferred: bool = False
    error_type: str | None = None


class SchedulerStatus(BaseModel):
    enabled: bool
    task_name: str
    timezone: str
    daily_time: str
    daily_update_hour: int
    daily_update_minute: int
    latest_digest_date: date | None = None
    latest_digest_created_at: datetime | None = None
    backend_scheduler: str
    target_date: date | None = None
    run_status: str = "not_started"
    pipeline_version: str | None = None


class DigestRollback(BaseModel):
    revision: int = Field(ge=0)
    expected_revision: int = Field(ge=0)
