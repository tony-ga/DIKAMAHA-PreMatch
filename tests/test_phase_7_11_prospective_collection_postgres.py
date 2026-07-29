"""Integración PostgreSQL read-only de Fase 7.11."""

from __future__ import annotations

import os

import pytest

from src.collect_prospective_signals import _read_database

pytestmark = pytest.mark.postgres


def test_phase_7_11_postgres_is_select_only() -> None:
    """Comprueba conteos, ausencia de escrituras y cierre de conexión."""

    matches, events, audit = _read_database(os.environ["DATABASE_URL"])
    assert matches and events
    assert audit["before"] == audit["after"]
    assert audit["write_statements"] == 0
    assert audit["connection_closed"] is True
    assert all(statement.lstrip().upper().startswith("SELECT ") for statement in audit["statements"])


# Version: 1.0.0
# Created: 2026-07-16
