"""Integración PostgreSQL read-only de Fase 7.7."""

from __future__ import annotations

import os

import pytest

from src.audit_event_label_coverage import _read_database

pytestmark = pytest.mark.postgres


def test_phase_7_7_postgres_counts_and_cleanup() -> None:
    """Confirma SELECT-only, conteos idénticos y cierre de conexión."""

    database_url = os.environ["DATABASE_URL"]
    matches, events, audit = _read_database(database_url)
    assert matches
    assert events
    assert audit["before"] == audit["after"]
    assert audit["identical"] is True
    assert audit["write_statements"] == 0
    assert audit["connection_closed"] is True
    assert all(statement.startswith("SELECT ") for statement in audit["statements"])


# Version: 1.0.0
# Created: 2026-07-16
