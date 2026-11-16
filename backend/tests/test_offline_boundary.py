import socket
from datetime import date

import pytest

from newsman import db
from newsman.services import newsletter


def test_tcp_dns_and_database_connections_are_blocked():
    with pytest.raises(AssertionError, match="Network access is forbidden"):
        socket.create_connection(("example.com", 443))
    with pytest.raises(AssertionError, match="Network access is forbidden"):
        socket.getaddrinfo("example.com", 443)
    with pytest.raises(AssertionError, match="Network access is forbidden"):
        db.engine.connect()


def test_export_cannot_use_production_default_in_tests():
    with pytest.raises(AssertionError):
        newsletter.write_markdown_digest(date(2026, 10, 25), "offline fixture")
