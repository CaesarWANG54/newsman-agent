import enum
import uuid
from datetime import date, datetime

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    JSON,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func

from newsman.db import Base


class SourceType(str, enum.Enum):
    rss = "rss"
    api = "api"
    web = "web"


class ArticleStatus(str, enum.Enum):
    collected = "collected"
    cleaned = "cleaned"
    summarized = "summarized"
    skipped = "skipped"


class FeedbackVote(str, enum.Enum):
    useful = "useful"
    noisy = "noisy"
    follow = "follow"


class Source(Base):
    __tablename__ = "sources"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(160), unique=True, index=True)
    url: Mapped[str] = mapped_column(Text)
    feed_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_type: Mapped[SourceType] = mapped_column(Enum(SourceType), default=SourceType.rss)
    category_hint: Mapped[str] = mapped_column(String(80), default="general")
    credibility: Mapped[float] = mapped_column(Float, default=0.8)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    articles: Mapped[list["Article"]] = relationship(back_populates="source")


class SourceHealthState(Base):
    __tablename__ = "source_health_states"

    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    checked_url: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(64), default="unverified")
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_body_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    next_retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    failure_count: Mapped[int] = mapped_column(default=0)
    http_status: Mapped[int | None] = mapped_column(nullable=True)
    latency_ms: Mapped[float | None] = mapped_column(Float, nullable=True)
    details: Mapped[dict] = mapped_column(JSON().with_variant(JSONB, "postgresql"), default=dict)


class Article(Base):
    __tablename__ = "articles"
    __table_args__ = (
        UniqueConstraint("url", name="uq_articles_url"),
        UniqueConstraint("canonical_url", name="uq_articles_canonical_url"),
        Index("ix_articles_published_score", "published_at", "importance_score"),
        Index(
            "ix_articles_embedding",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="SET NULL"),
        nullable=True,
    )
    url: Mapped[str] = mapped_column(Text)
    canonical_url: Mapped[str] = mapped_column(Text, index=True)
    title: Mapped[str] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(240), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    raw_excerpt: Mapped[str | None] = mapped_column(Text, nullable=True)
    text_content: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(20), default="unknown")
    category: Mapped[str] = mapped_column(String(80), default="general")
    tags: Mapped[list[str]] = mapped_column(ARRAY(String), default=list)
    status: Mapped[ArticleStatus] = mapped_column(
        Enum(ArticleStatus), default=ArticleStatus.collected
    )
    cluster_key: Mapped[str | None] = mapped_column(String(128), index=True, nullable=True)
    importance_score: Mapped[float] = mapped_column(Float, default=0.0)
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    why_it_matters: Mapped[str | None] = mapped_column(Text, nullable=True)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(1536), nullable=True)
    metadata_json: Mapped[dict] = mapped_column(JSONB, default=dict)

    source: Mapped[Source | None] = relationship(back_populates="articles")
    feedback: Mapped[list["Feedback"]] = relationship(back_populates="article")


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    digest_date: Mapped[date] = mapped_column(Date, unique=True, index=True)
    title: Mapped[str] = mapped_column(String(240))
    brief: Mapped[str] = mapped_column(Text, default="")
    sections: Mapped[list[dict]] = mapped_column(JSONB, default=list)
    markdown: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DailyRun(Base):
    __tablename__ = "daily_runs"
    __table_args__ = (UniqueConstraint("target_date", "pipeline_version"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    target_date: Mapped[date] = mapped_column(Date)
    pipeline_version: Mapped[str] = mapped_column(String(64))
    attempt: Mapped[int] = mapped_column(default=1)
    status: Mapped[str] = mapped_column(String(32), default="running")
    stage: Mapped[str] = mapped_column(String(32), default="ensure_sources")
    window: Mapped[dict] = mapped_column(JSONB, default=dict)
    checkpoints: Mapped[dict] = mapped_column(JSONB, default=dict)
    commands: Mapped[dict] = mapped_column(JSONB, default=dict)
    diagnostics: Mapped[dict] = mapped_column(JSONB, default=dict)
    retry_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    error_type: Mapped[str | None] = mapped_column(String(80), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DigestRevision(Base):
    __tablename__ = "digest_revisions"
    __table_args__ = (
        UniqueConstraint("digest_date", "revision"),
        UniqueConstraint("run_id", "attempt"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    digest_date: Mapped[date] = mapped_column(Date, index=True)
    revision: Mapped[int] = mapped_column()
    run_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("daily_runs.id"))
    attempt: Mapped[int] = mapped_column()
    pipeline_version: Mapped[str] = mapped_column(String(64))
    source_version: Mapped[str] = mapped_column(String(64))
    summary_version: Mapped[str] = mapped_column(String(64))
    exported_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32))
    title: Mapped[str] = mapped_column(String(240))
    brief: Mapped[str] = mapped_column(Text)
    sections: Mapped[list[dict]] = mapped_column(JSONB)
    markdown: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class DigestHead(Base):
    __tablename__ = "digest_heads"

    digest_date: Mapped[date] = mapped_column(Date, primary_key=True)
    revision: Mapped[int] = mapped_column()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class ReadingPreference(Base):
    __tablename__ = "reading_preferences"

    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("articles.id", ondelete="CASCADE"), primary_key=True
    )
    vote: Mapped[str] = mapped_column(String(24))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Feedback(Base):
    __tablename__ = "feedback"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    article_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("articles.id", ondelete="CASCADE"),
    )
    vote: Mapped[FeedbackVote] = mapped_column(Enum(FeedbackVote))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    article: Mapped[Article] = relationship(back_populates="feedback")
