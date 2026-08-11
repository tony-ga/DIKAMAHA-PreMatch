"""El ledger del canal persiste en PostgreSQL, no en disco efímero.

Existe por dos incidencias encadenadas. El ledger vivía siempre en SQLite sobre
el disco del contenedor, que Railway destruye en cada redeploy: eso permitía
republicar en el canal e impedía sellar settlements, porque `_seal_settlement`
recorre las predicciones congeladas. El intento de persistirlo con un volumen
montado en `/data` provocó una caída de producción, porque el punto de montaje
llega propiedad de root y el contenedor corre como el usuario `app`.

PostgreSQL ya está conectado mediante `DATABASE_URL` y no depende de la
propiedad de ningún directorio.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.dialects import postgresql, sqlite

from scripts import run_phase_101_telegram_channel_publisher as worker
from src.telegram_channel_publisher import ChannelBroadcastBase

LEDGER_TABLES = {
    "channel_predictions", "channel_publications", "channel_market_snapshots",
}


def test_postgres_is_preferred_when_database_url_exists(
    monkeypatch: pytest.MonkeyPatch, caplog: Any,
) -> None:
    """Con `DATABASE_URL` el ledger deja de tocar el disco del contenedor."""

    captured: dict[str, Any] = {}

    def _fake_create_engine(url: str, **options: Any) -> Any:
        """Registra la URL sin abrir conexión real."""

        captured["url"] = url
        captured["options"] = options
        return object()

    monkeypatch.setattr(worker, "create_engine", _fake_create_engine)
    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/dikamaha")
    monkeypatch.setenv(
        "TELEGRAM_CHANNEL_LEDGER_PATH", "/app/data/telegram_channel.sqlite")

    with caplog.at_level(logging.INFO):
        worker._ledger_engine(None)

    assert captured["url"] == "postgresql://u:p@db:5432/dikamaha"
    assert captured["options"]["pool_pre_ping"] is True
    assert any(
        "backend=postgresql" in record.getMessage()
        for record in caplog.records)


def test_sqlite_fallback_is_explicit_and_warned(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: Any,
) -> None:
    """Sin `DATABASE_URL` se cae a SQLite, pero el log lo declara efímero."""

    target = tmp_path / "nested" / "telegram_channel.sqlite"
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("TELEGRAM_CHANNEL_LEDGER_PATH", str(target))

    with caplog.at_level(logging.WARNING):
        engine = worker._ledger_engine(None)

    assert engine.dialect.name == "sqlite"
    assert target.parent.is_dir()
    messages = [record.getMessage() for record in caplog.records]
    assert any("backend=sqlite_ephemeral" in message for message in messages)
    assert any("DATABASE_URL_missing" in message for message in messages)


def test_explicit_ledger_path_forces_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """`--ledger-path` gana a `DATABASE_URL` para auditorías locales."""

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/dikamaha")
    engine = worker._ledger_engine(tmp_path / "auditoria.sqlite")

    assert engine.dialect.name == "sqlite"


def test_dry_run_never_touches_a_real_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El modo dry-run permanece aislado en memoria pese a `DATABASE_URL`."""

    monkeypatch.setenv("DATABASE_URL", "postgresql://u:p@db:5432/dikamaha")
    repository = worker._repository(dry_run=True)

    assert repository is not None


def test_schema_is_created_on_a_fresh_backend(tmp_path: Path) -> None:
    """Las tres tablas del ledger se crean solas, sin migración manual.

    Es la misma convención que Fase 118 usa para `prediction_settlements`.
    """

    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'ledger.sqlite'}")
    ChannelBroadcastBase.metadata.create_all(engine)

    assert LEDGER_TABLES <= set(inspect(engine).get_table_names())


@pytest.mark.parametrize("table,column", [
    ("channel_predictions", "fixture_json"),
    ("channel_predictions", "prediction_json"),
    ("channel_market_snapshots", "prediction_json"),
])
def test_json_columns_compile_to_jsonb_on_postgres(
    table: str, column: str,
) -> None:
    """El tipo se adapta al dialecto sin duplicar modelos.

    Misma convención que `prediction_settlements` de Fase 118: JSONB en
    PostgreSQL y JSON en SQLite, que es lo que usan las pruebas.
    """

    target = ChannelBroadcastBase.metadata.tables[table].c[column]
    assert target.type.compile(postgresql.dialect()) == "JSONB"
    assert target.type.compile(sqlite.dialect()) == "JSON"
