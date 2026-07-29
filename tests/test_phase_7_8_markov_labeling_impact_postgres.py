"""Integración PostgreSQL read-only de Fase 7.8."""

from __future__ import annotations

import os

import pytest

from src.audit_event_label_coverage import _read_database

pytestmark = pytest.mark.postgres


def test_phase_7_8_postgres_is_select_only() -> None:
    """Confirma conteos estables, SELECT-only y cierre de conexión."""

    matches, events, audit = _read_database(os.environ["DATABASE_URL"])
    assert matches and events
    assert audit["before"] == audit["after"]
    assert audit["identical"] is True
    assert audit["write_statements"] == 0
    assert audit["connection_closed"] is True
    assert all(statement.lstrip().upper().startswith("SELECT ") for statement in audit["statements"])


# Version: 1.0.0
# Created: 2026-07-16
