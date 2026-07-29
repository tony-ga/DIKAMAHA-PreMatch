"""Pruebas unitarias del gate PostgreSQL read-only."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from src.postgres_readonly_staging import (
    ReadonlyDatabase,
    counts_identical,
    detect_capabilities,
    ensure_select,
    sanitize_error,
)

ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "scripts/run_phase_7_5_postgres_readonly.py"


def _finder(available: set[str]) -> Any:
    """Construye un finder determinista de dependencias."""

    return lambda name: object() if name in available else None


def test_capabilities_report_missing_url_and_dependencies() -> None:
    """Expone explícitamente cada bloqueo sin abrir conexión."""

    missing = detect_capabilities("", finder=_finder(set()))
    assert missing.ready is False
    assert set(missing.missing()) == {
        "database_url_present", "sqlalchemy_available", "psycopg2_available"
    }
    no_sqlalchemy = detect_capabilities(
        "postgresql://redacted", finder=_finder({"psycopg2"})
    )
    assert no_sqlalchemy.missing() == ["sqlalchemy_available"]
    no_psycopg2 = detect_capabilities(
        "postgresql://redacted", finder=_finder({"sqlalchemy"})
    )
    assert no_psycopg2.missing() == ["psycopg2_available"]


def test_select_guard_rejects_writes_and_ddl() -> None:
    """Impide INSERT, UPDATE, DELETE y DDL."""

    assert ensure_select(" SELECT COUNT(*) FROM matches ") == "SELECT COUNT(*) FROM matches"
    for statement in (
        "INSERT INTO matches VALUES (1)",
        "UPDATE matches SET status='x'",
        "DELETE FROM matches",
        "CREATE TABLE unsafe(id int)",
        "ALTER TABLE matches ADD COLUMN unsafe int",
        "DROP TABLE matches",
        "TRUNCATE matches",
    ):
        with pytest.raises(ValueError):
            ensure_select(statement)


def test_database_url_is_sanitized() -> None:
    """No expone usuario ni password en excepciones."""

    url = "postgresql://admin:secret@127.0.0.1:5432/db"
    sanitized = sanitize_error(f"falló {url}", url)
    assert "admin" not in sanitized
    assert "secret" not in sanitized
    assert "DATABASE_URL" in sanitized


def test_counts_require_exact_nonempty_equality() -> None:
    """Rechaza conteos ausentes o diferentes."""

    assert counts_identical({"matches": 381}, {"matches": 381})
    assert not counts_identical({}, {})
    assert not counts_identical({"matches": 381}, {"matches": 382})


class _FakeResult:
    """Resultado mínimo compatible con SQLAlchemy."""

    def mappings(self) -> "_FakeResult":
        """Devuelve la interfaz de mappings."""

        return self

    def all(self) -> list[dict[str, int]]:
        """Devuelve una fila sintética."""

        return [{"count": 1}]

    def scalar_one(self) -> int:
        """Devuelve un escalar sintético."""

        return 1


class _FakeConnection:
    """Conexión fake con cierre y rollback observables."""

    def __init__(self) -> None:
        self.exited = False
        self.rolled_back = False

    def __enter__(self) -> "_FakeConnection":
        return self

    def __exit__(self, *_: object) -> None:
        self.exited = True

    def execute(self, _: object, __: dict[str, Any]) -> _FakeResult:
        return _FakeResult()

    def in_transaction(self) -> bool:
        return True

    def rollback(self) -> None:
        self.rolled_back = True


class _FakeEngine:
    """Motor fake para validar cleanup sin PostgreSQL."""

    def __init__(self) -> None:
        self.connection = _FakeConnection()
        self.disposed = False

    def connect(self) -> _FakeConnection:
        return self.connection

    def dispose(self) -> None:
        self.disposed = True


def test_connection_is_closed_and_disposed() -> None:
    """Garantiza rollback, cierre y dispose mediante context manager."""

    engine = _FakeEngine()
    database = ReadonlyDatabase(
        "postgresql://redacted",
        engine_factory=lambda *_args, **_kwargs: engine,
        text_factory=lambda value: value,
        pool_class=object,
    )
    with database.session() as session:
        assert session.scalar("SELECT COUNT(*) FROM matches") == 1
    assert database.closed
    assert engine.connection.exited
    assert engine.connection.rolled_back
    assert engine.disposed
    assert database.statements == ["SELECT COUNT(*) FROM matches"]


def test_runner_without_database_url_is_explicit_and_secret_free() -> None:
    """Genera `database_verification_incomplete` sin inventar conteos."""

    environment = dict(os.environ)
    environment.pop("DATABASE_URL", None)
    with tempfile.TemporaryDirectory() as directory:
        result = subprocess.run(
            [sys.executable, str(RUNNER), "--out-dir", directory],
            cwd=ROOT, env=environment, text=True, capture_output=True, check=True,
        )
        manifest = json.loads(Path(directory, "manifest.json").read_text())
        counts = json.loads(Path(directory, "counts_before_after.json").read_text())
        combined = "".join(path.read_text() for path in Path(directory).iterdir())
    assert json.loads(result.stdout)["decision"] == "database_verification_incomplete"
    assert manifest["decision"] == "database_verification_incomplete"
    assert counts["before"] is None and counts["after"] is None
    assert "postgresql://" not in combined


# Version: 1.0.0
# Created: 2026-07-16
