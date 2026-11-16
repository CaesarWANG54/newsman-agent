"""Read-only HTTP/body observations of the public default registry; never opens a DB."""

from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timezone


async def observe_registry():
    from newsman.models import Source
    from newsman.services.collector import inspect_source
    from newsman.services.sources import DEFAULT_SOURCES

    gate = asyncio.Semaphore(3)

    async def one(payload):
        source = Source(**payload)
        async with gate:
            checked_at = datetime.now(timezone.utc)
            try:
                result = await asyncio.wait_for(
                    inspect_source(source, limit=2, now=checked_at, allow_dynamic=False),
                    timeout=35,
                )
                return {
                    "name": source.name,
                    "url": source.url,
                    "feed_url": source.feed_url,
                    "checked_at": checked_at.isoformat(),
                    "status": result.status,
                    "http_status": result.http_status,
                    "latency_ms": result.latency_ms,
                    "details": result.details,
                }
            except TimeoutError:
                return {
                    "name": source.name,
                    "url": source.url,
                    "feed_url": source.feed_url,
                    "checked_at": checked_at.isoformat(),
                    "status": "probe_timeout",
                    "fact_verification_status": "not_verified",
                }

    return await asyncio.gather(*(one(payload) for payload in DEFAULT_SOURCES))


def main():
    # Disable local config loading before model imports. No engine connections or writes occur.
    from newsman.config import Settings, get_settings

    Settings.model_config["env_file"] = None
    get_settings.cache_clear()
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    results = asyncio.run(observe_registry())
    print(
        json.dumps(
            {
                "mode": "read_only_public_http",
                "database_written": False,
                "sources_enabled": False,
                "observations": results,
            },
            ensure_ascii=True,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
