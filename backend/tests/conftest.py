"""Offline tests must never open the installed database, feeds, or model services."""

import os
import socket
from types import SimpleNamespace

import pytest

os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["NEWSMAN_LLM_PROVIDER"] = "none"
os.environ["OPENAI_API_KEY"] = "offline-test-key"

from newsman.config import Settings  # noqa: E402

Settings.model_config["env_file"] = None


def pytest_configure(config):
    from pathlib import Path

    local_temp = Path(__file__).resolve().parents[2] / ".qa"
    local_temp.mkdir(exist_ok=True)
    if config.option.basetemp is None:
        config.option.basetemp = str(local_temp / "offline-tmp")


@pytest.fixture(autouse=True)
def block_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network access is forbidden in offline tests")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "sendto", blocked)
    from newsman import db
    from newsman.services import collector, summarizer, newsletter
    from newsman.agents import graph

    monkeypatch.setattr(db.engine, "connect", blocked)
    monkeypatch.setattr(db, "init_db", blocked)

    async def fake_collector(*args, **kwargs):
        return collector.CollectionResult(collected=0, skipped=0)

    monkeypatch.setattr(collector, "collect_enabled_sources", fake_collector)
    monkeypatch.setattr(graph, "collect_enabled_sources", fake_collector)
    unexpected = []

    def unexpected_model(*args, **kwargs):
        unexpected.append(True)
        raise AssertionError("Install the Responses and Embeddings mock for this test")

    monkeypatch.setattr(
        summarizer,
        "OpenAI",
        lambda **kw: SimpleNamespace(
            responses=SimpleNamespace(create=unexpected_model),
            embeddings=SimpleNamespace(create=unexpected_model),
        ),
    )
    real_write = newsletter.write_markdown_digest

    def guarded_write(day, markdown, output_dir=None):
        from pathlib import Path

        allowed = Path(__file__).resolve().parents[2] / ".qa"
        assert output_dir is not None and Path(output_dir).resolve().is_relative_to(allowed)
        return real_write(day, markdown, output_dir)

    monkeypatch.setattr(newsletter, "write_markdown_digest", guarded_write)
    yield
    assert not unexpected, "Unexpected model invocation was attempted"


@pytest.fixture
def model_client(monkeypatch):
    from newsman.services import summarizer
    from unittest.mock import Mock

    client = SimpleNamespace(
        responses=SimpleNamespace(create=Mock()),
        embeddings=SimpleNamespace(
            create=Mock(
                return_value=SimpleNamespace(
                    data=[SimpleNamespace(embedding=[0.0] * 1536)],
                )
            )
        ),
    )
    monkeypatch.setattr(summarizer, "OpenAI", lambda **kw: client)
    return client


@pytest.fixture
def settings():
    return Settings(_env_file=None, database_url="sqlite:///:memory:", openai_api_key=None)
