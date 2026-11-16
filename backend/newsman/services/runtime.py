from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import NullPool
from urllib.parse import urlsplit, parse_qsl, urlencode


def runtime_status(engine, settings, probe_worker=True):
    """Read-only, individually bounded dependency probes. Configuration is not uptime."""
    result = {
        "database": "unavailable",
        "redis": "unavailable",
        "worker": "unknown" if probe_worker else "not_probed",
        "scheduler": "not_observed",
        "daily_configured": settings.daily_update_enabled,
        "issues": [],
    }
    import os

    if os.name == "posix":
        try:
            from newsman.cli.manage_runtime import component_status

            result["components"] = {
                role: component_status(role) for role in ("api", "worker", "beat")
            }

            if result["components"]["beat"]["owned_running"]:
                result["scheduler"] = "process_observed"
        except (OSError, ValueError):
            result["issues"].append(
                "Managed scheduler identity unavailable; execution remains unverified."
            )
    probe_engine = engine
    try:
        if engine.dialect.name == "postgresql":
            probe_engine = create_engine(
                engine.url, connect_args={"connect_timeout": 2}, poolclass=NullPool
            )
        with probe_engine.connect() as connection:
            if engine.dialect.name == "postgresql":
                connection.execute(text("SET LOCAL statement_timeout = 2000"))
            connection.execute(text("SELECT 1"))
            expected = {
                "articles",
                "digests",
                "daily_runs",
                "digest_revisions",
                "digest_heads",
                "reading_preferences",
                "source_health_states",
            }
            missing = sorted(expected - set(inspect(connection).get_table_names()))
            result["database"] = "migration_required" if missing else "ready"
            result["missing_tables"] = missing
    except Exception:
        result["issues"].append(
            "Database unavailable; check configured instance without printing credentials."
        )
    finally:
        if probe_engine is not engine:
            probe_engine.dispose()
    try:
        from redis import Redis

        url = settings.redis_url
        parsed = urlsplit(url)
        if parsed.scheme == "redis+socket":
            query = [
                ("db" if key == "virtual_host" else key, value)
                for key, value in parse_qsl(parsed.query)
            ]
            url = f"unix://{parsed.netloc}{parsed.path}?{urlencode(query)}"
        with Redis.from_url(url, socket_connect_timeout=1, socket_timeout=1) as client:
            client.ping()
        result["redis"] = "ready"
    except Exception as exc:
        result["issues"].append("Redis unavailable; queue submission may fail.")
        result["redis_error_type"] = type(exc).__name__
    if probe_worker and result["redis"] == "ready":
        try:
            from newsman.celery_app import celery_app

            with celery_app.connection_for_write(connect_timeout=1) as connection:
                connection.ensure_connection(max_retries=0)
                inspector = celery_app.control.inspect(timeout=1, connection=connection)
                reply = inspector.ping() or {}
                registered = inspector.registered() or {} if reply else {}
                queues = inspector.active_queues() or {} if reply else {}
                stats = getattr(inspector, "stats", lambda: {})() or {} if reply else {}
            expected_queue = celery_app.conf.task_default_queue
            candidates = [
                name
                for name in reply
                if any(
                    task.partition(" ")[0] == "newsman.tasks.run_daily_digest"
                    for task in registered.get(name, [])
                )
                and any(queue.get("name") == expected_queue for queue in queues.get(name, []))
            ]
            result["worker"] = "ready" if candidates else "not_observed"
            result["worker_pids"] = {
                name: stats[name]["pid"]
                for name in candidates
                if isinstance(stats.get(name, {}).get("pid"), int)
            }
        except Exception:
            result["worker"] = "unavailable"
    return result
