"""Ancla que el worker real del canal también conecta el ciclo de Fase 123.

Mismo motivo que `test_phase_101_telegram_channel_publisher_wiring.py`: la
composición dentro de `scripts/run_phase_101_telegram_channel_publisher.py`
-el proceso que Railway arranca de verdad- es lo único que puede detectar un
cableado roto (por ejemplo, `_pick_repository` devolviendo un almacén que no
persiste entre llamadas, o `_high_probability_cycle` sin conectar la config
real). Fase 123 corre dentro de este mismo proceso por decisión del cierre
del proyecto, en vez de un servicio Railway nuevo.
"""

from __future__ import annotations

import pytest

from scripts.run_phase_101_telegram_channel_publisher import (
    _high_probability_cycle,
    _pick_repository,
)
from src.high_probability_settlement import (
    PickFreezeRecord,
    SqlAlchemyHighProbabilityPickRepository,
)
from src.telegram_bot import DikamahaHttpGateway

from datetime import datetime, timedelta, timezone

KICKOFF = datetime(2026, 9, 1, 20, 0, tzinfo=timezone.utc)


def _freeze(match_id: int) -> PickFreezeRecord:
    return PickFreezeRecord(
        pick_key=f"esp.1:{match_id}:1x2:match:full_match:na:home",
        fixture_key=f"esp.1:{match_id}", league_slug="esp.1", match_id=match_id,
        kickoff_ts=KICKOFF, market="1x2", direction="home", metric="result",
        team_side="match", period="full_match", line=None,
        model_probability=0.7, observed_rate_declared=0.8,
        sample_size_declared=40, edge_source="model_edge",
        bucket_low=0.65, bucket_high=0.75, eligibility_sha256="sha",
        prediction_hash="a" * 64, frozen_at=KICKOFF - timedelta(hours=6))


def test_dry_run_pick_repository_shares_state_across_calls_within_process() -> None:
    """El respaldo en memoria debe usar `StaticPool`, no una base nueva por conexión."""

    repository = _pick_repository(dry_run=True)

    assert repository.freeze_if_absent(_freeze(1)) is True
    assert repository.freeze_if_absent(_freeze(1)) is False
    assert len(repository.frozen_on_date(KICKOFF.date(), timezone.utc)) == 1


def test_configured_database_url_builds_a_real_pick_repository(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Con `DATABASE_URL` configurada, el worker construye el repositorio real."""

    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite://")

    repository = _pick_repository(dry_run=False)

    assert isinstance(repository, SqlAlchemyHighProbabilityPickRepository)


def test_missing_database_url_falls_back_to_local_sqlite(
    monkeypatch: pytest.MonkeyPatch, tmp_path: object,
) -> None:
    """Sin `DATABASE_URL` el worker sigue arrancando con un respaldo local."""

    monkeypatch.delenv("DATABASE_URL", raising=False)

    repository = _pick_repository(dry_run=False)

    assert isinstance(repository, SqlAlchemyHighProbabilityPickRepository)


def test_high_probability_cycle_wires_gateway_repository_and_settlements(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """El ciclo real debe llamar al gateway configurado, no un doble silencioso.

    Es la prueba que habría fallado si `_high_probability_cycle` hubiera
    quedado sin pasar `settlements` o hubiera apuntado a un repositorio en
    memoria por error, igual que el defecto real que motivó el archivo
    hermano de Fase 118.
    """

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "test-token")
    monkeypatch.setenv("DIKAMAHA_API_KEY", "test-key")
    monkeypatch.setenv("DATABASE_URL", "sqlite+pysqlite://")
    calls: list[dict[str, object]] = []

    def _fake_high_probability(self, date=None, limit=30, leagues=None):
        calls.append({"date": date, "limit": limit})
        return {"picks": [], "provenance": {"eligibility_sha256": "sha"}}

    monkeypatch.setattr(
        DikamahaHttpGateway, "high_probability", _fake_high_probability)

    result = _high_probability_cycle(dry_run=False)

    assert len(calls) == 1
    assert result == {
        "freeze": {
            "frozen": 0, "skipped_started": 0, "skipped_invalid": 0,
            "candidates": 0,
        },
        "settle": {"settled": 0, "still_pending": 0, "failed": 0},
    }


def test_high_probability_cycle_never_raises_without_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Un fallo de configuración degrada; el llamador lo aísla con `except Exception`.

    `_run()` envuelve esta llamada en un `try/except Exception` amplio a
    propósito -Fase 123 nunca debe tumbar el canal-, así que esta prueba sólo
    confirma que el fallo es del tipo esperado (`ValueError` por token
    ausente), no que la función lo trague ella misma.
    """

    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)

    with pytest.raises(ValueError):
        _high_probability_cycle(dry_run=False)


# Version: 1.0.0
# Created: 2026-08-12
