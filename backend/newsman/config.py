from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = Field(default="NewsMan Agent", alias="NEWSMAN_APP_NAME")
    env: str = Field(default="local", alias="NEWSMAN_ENV")
    api_base_url: str = Field(default="http://localhost:8000", alias="NEWSMAN_API_BASE_URL")
    frontend_origin: str = Field(default="http://localhost:3000", alias="NEWSMAN_FRONTEND_ORIGIN")

    database_url: str = Field(
        default="postgresql+psycopg://newsman:newsman@localhost:5432/newsman",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")

    llm_provider: str = Field(default="none", alias="NEWSMAN_LLM_PROVIDER")
    openai_api_key: str | None = Field(default=None, alias="OPENAI_API_KEY")
    openai_model: str = Field(default="gpt-4.1-mini", alias="OPENAI_MODEL")
    openai_embedding_model: str = Field(
        default="text-embedding-3-small",
        alias="OPENAI_EMBEDDING_MODEL",
    )

    daily_timezone: str = Field(default="Asia/Shanghai", alias="NEWSMAN_DAILY_TIMEZONE")
    daily_update_enabled: bool = Field(default=True, alias="NEWSMAN_DAILY_UPDATE_ENABLED")
    daily_update_hour: int = Field(default=8, ge=0, le=23, alias="NEWSMAN_DAILY_UPDATE_HOUR")
    daily_update_minute: int = Field(default=0, ge=0, le=59, alias="NEWSMAN_DAILY_UPDATE_MINUTE")
    max_articles_per_run: int = Field(default=80, alias="NEWSMAN_MAX_ARTICLES_PER_RUN")
    digest_article_limit: int = Field(default=15, alias="NEWSMAN_DIGEST_ARTICLE_LIMIT")

    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"),
        env_file_encoding="utf-8",
        populate_by_name=True,
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    import json
    import os
    from pathlib import Path

    explicit = os.environ.get("NEWSMAN_SETTINGS_FILE")
    if explicit:
        try:
            data = json.loads(Path(explicit).read_text(encoding="utf-8-sig"))
            if not isinstance(data, dict):
                raise ValueError("settings object required")
            return Settings(_env_file=None, **data)
        except Exception:
            raise RuntimeError(
                "Explicit NewsMan settings file invalid; input values withheld"
            ) from None
    return Settings()
