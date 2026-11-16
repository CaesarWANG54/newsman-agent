"""Single physical connection owns both the lock and all pipeline writes."""

from contextlib import contextmanager

from sqlalchemy import event, text
from sqlalchemy.orm import Session

LOCK_KEY = 78261490051


class RunBusy(Exception):
    pass


class RunConflict(ValueError):
    pass


@contextmanager
def locked_session(engine):
    if engine.dialect.name != "postgresql":
        raise RuntimeError("Daily execution requires PostgreSQL advisory locks")
    with engine.connect() as connection:
        acquired = connection.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": LOCK_KEY})
        connection.commit()
        if not acquired:
            raise RunBusy("Another daily operation owns the pipeline")
        physical = connection.connection.driver_connection

        def guard(conn):
            # Never let SQLAlchemy transparently reconnect an owner after connection loss.
            if conn.invalidated or conn.connection.driver_connection is not physical:
                raise RunBusy("Run connection lost; resume through the locked entry")
            conn.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": LOCK_KEY})

        event.listen(connection, "begin", guard)
        try:
            with Session(bind=connection, expire_on_commit=False) as session:
                yield session
        finally:
            event.remove(connection, "begin", guard)
            connection.rollback()
            # Never return a session-locked connection to the pool, even on errors.
            connection.invalidate()
