"""Ancla que el worker real conecta el historial verificado de Fase 118.

`scripts/run_phase_101_telegram_channel_publisher.py` es el proceso que
Railway arranca de verdad (vía `src/telegram_channel_service.py`), distinto
del `TelegramChannelPublisher` en sí. Fase 118 construyó `_seal_settlement` y
lo probó en aislamiento, pero el primer despliegue no conectó
`SettlementRepository` a este script — el worker real nunca llegaba a
sellar un veredicto porque `self._settlements` quedaba en `None` sin que
ningún test lo detectara. Estas pruebas anclan la composición real.
"""

from __future__ import annotations

import pytest

from scripts.run_phase_101_telegram_channel_publisher import _publisher, _settlements
from src.settlement_store import SqlAlchemySettlementRepository


def test_dry_run_never_touches_a_real_settlement_store() -> None:
    """El modo de auditoría no debe intentar conectar Postgres."""

    assert _settlements(dry_run=True) is None


def test_missing_database_url_degrades_without_raising(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Sin `DATABASE_URL` el worker sigue arrancando, sólo sin historial."""

    monkeypatch.delenv("DATABASE_URL", raising=False)

    assert _settlements(dry_run=False) is None


def test_configured_database_url_builds_a_real_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con `DATABASE_URL` configurada, el worker sí construye el repositorio."""

    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite://")

    repository = _settlements(dry_run=False)

    assert isinstance(repository, SqlAlchemySettlementRepository)


def test_publisher_factory_wires_the_settlement_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`_publisher()` debe pasar el historial al publicador, no dejarlo fuera.

    Esta es la prueba que habría fallado antes de la corrección: construía
    `TelegramChannelPublisher` sin el parámetro `settlements`, de modo que
    `_seal_settlement` se convertía en un no-op silencioso en producción.
    """

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DIKAMAHA_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite://")

    publisher = _publisher(dry_run=False, mode="lite", ledger_path=None)

    assert publisher._settlements is not None
    assert isinstance(publisher._settlements, SqlAlchemySettlementRepository)


# Version: 1.0.0
# Created: 2026-08-11
