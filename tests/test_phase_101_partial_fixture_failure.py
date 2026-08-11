"""El ciclo diario tolera fixtures que el modelo no puede predecir.

Existe por un fallo real de producción. Desde que Fase 120 amplió el catálogo a
63 ligas, la lista de mañana incluye con frecuencia alguna competición cuyo
historial causal no alcanza el mínimo del snapshot. `/v1/predict/upcoming`
devuelve un 422 legítimo (`league_history_below_minimum`), el gateway lo traduce
a `PredictionGatewayError`, y la comprensión de lista de `_daily` propagaba esa
excepción hasta `run_cycle`, abortando el ciclo completo: un solo partido no
predecible impedía publicar todos los demás.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.telegram_bot import PredictionGateway, PredictionGatewayError
from src.telegram_channel_publisher import (
    ChannelBroadcastBase,
    ChannelTransport,
    SqlAlchemyChannelRepository,
    TelegramChannelPublisher,
)

NOW = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)


def _fixture(match_id: int, league: str, home: int, away: int) -> dict[str, Any]:
    """Construye un fixture futuro con identidad completa."""

    return {
        "league_slug": league, "match_id": match_id,
        "competition_id": str(match_id),
        "home_team_id": home, "away_team_id": away,
        "home_team_name": f"Local {home}", "away_team_name": f"Visita {away}",
        "kickoff_ts": "2026-07-30T16:00:00+00:00",
    }


PREDICTABLE = _fixture(10, "mex.1", 1, 2)
UNPREDICTABLE = _fixture(11, "kor.1", 3, 4)


class _Gateway(PredictionGateway):
    """Gateway que rechaza exactamente una liga, como hace producción."""

    def __init__(self, rejected_league: str = "kor.1") -> None:
        """Fija la liga sin historial causal suficiente."""

        self.rejected_league = rejected_league
        self.prediction_calls = 0

    def predict_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """No se usa en la difusión programada."""

        return self.predict_upcoming(payload)

    def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Rechaza la liga sin historial, igual que un 422 del servicio."""

        self.prediction_calls += 1
        if str(payload.get("league_slug")) == self.rejected_league:
            raise PredictionGatewayError("dikamaha_prediction_rejected")
        return {
            **payload, "probability_home": 0.60, "probability_draw": 0.25,
            "probability_away": 0.15, "probability_over_2_5": 0.55,
            "probability_btts": 0.45,
        }

    def readiness(self) -> dict[str, Any]:
        """Simula servicio listo."""

        return {"ready": True}

    def explorer_leagues(self) -> dict[str, Any]:
        """Expone las dos ligas del escenario."""

        return {"leagues": [
            {"slug": "mex.1", "name": "Liga MX"},
            {"slug": "kor.1", "name": "K League"},
        ]}

    def explorer_teams(self, league: str) -> dict[str, Any]:
        """Sin escudos; la presentación cae a texto."""

        return {"teams": []}

    def list_upcoming(
        self, limit: int = 8, leagues: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Publica un fixture por liga sólo para el 30 de julio."""

        if date != "20260730":
            return {"fixtures": []}
        row = PREDICTABLE if leagues == "mex.1" else UNPREDICTABLE
        return {"fixtures": [row]}

    def explorer_fixtures(self, league: str, date: str) -> dict[str, Any]:
        """No participa en este escenario."""

        return {"fixtures": []}

    def explorer_statistics(
        self, league: str, match_id: str, competition_id: str,
    ) -> dict[str, Any]:
        """No participa en este escenario."""

        return {"reconciled": False, "score_reconciled": False}


class _Transport(ChannelTransport):
    """Transporte que sólo cuenta mensajes."""

    def __init__(self) -> None:
        """Inicializa el registro de mensajes."""

        self.messages: list[str] = []

    def send_message(self, text: str) -> str:
        """Registra el envío y devuelve un identificador estable."""

        self.messages.append(text)
        return f"msg-{len(self.messages)}"

    def send_photo_group(
        self, urls: list[str], caption: str,
    ) -> str:
        """Registra la tarjeta con escudos."""

        self.messages.append(caption)
        return f"msg-{len(self.messages)}"


def _repository() -> SqlAlchemyChannelRepository:
    """Crea un ledger SQLite en memoria."""

    engine = create_engine(
        "sqlite+pysqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    ChannelBroadcastBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemyChannelRepository(factory)


def test_one_unpredictable_fixture_does_not_abort_the_cycle() -> None:
    """El resumen se publica con los fixtures que sí congelaron."""

    gateway, transport, repository = _Gateway(), _Transport(), _repository()
    publisher = TelegramChannelPublisher(gateway, repository, transport)

    counts = publisher.run_cycle(NOW)

    assert counts["summaries"] == 1
    assert counts["frozen"] == 1
    frozen = repository.predictions()
    assert len(frozen) == 1
    assert frozen[0].prediction["league_slug"] == "mex.1"


def test_the_rejected_fixture_is_never_frozen() -> None:
    """Un fixture rechazado no deja predicción congelada ni se publica."""

    gateway, transport, repository = _Gateway(), _Transport(), _repository()
    TelegramChannelPublisher(gateway, repository, transport).run_cycle(NOW)

    assert all(
        row.prediction["league_slug"] != "kor.1"
        for row in repository.predictions())
    assert not any("Visita 4" in message for message in transport.messages)


def test_partial_failure_is_logged_as_auditable(
    caplog: Any,
) -> None:
    """El fixture omitido queda registrado, no desaparece en silencio."""

    gateway, transport, repository = _Gateway(), _Transport(), _repository()
    publisher = TelegramChannelPublisher(gateway, repository, transport)

    with caplog.at_level(logging.WARNING):
        publisher.run_cycle(NOW)

    records = [
        record.getMessage() for record in caplog.records
        if "daily_partial_failure" in record.getMessage()]
    assert records, "el fallo parcial debe quedar en el log"
    assert "skipped=1" in records[0]
    assert "frozen=1" in records[0]


def test_nothing_is_published_when_no_fixture_can_be_predicted(
    caplog: Any,
) -> None:
    """Sin ninguna predicción no se publica un resumen vacío."""

    class _AllRejected(_Gateway):
        """Rechaza cualquier liga."""

        def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
            """Simula que ninguna liga alcanza el mínimo causal."""

            self.prediction_calls += 1
            raise PredictionGatewayError("dikamaha_prediction_rejected")

    transport, repository = _Transport(), _repository()
    publisher = TelegramChannelPublisher(
        _AllRejected(), repository, transport)

    with caplog.at_level(logging.WARNING):
        counts = publisher.run_cycle(NOW)

    assert counts["summaries"] == 0
    assert counts["frozen"] == 0
    assert transport.messages == []
    assert repository.predictions() == []
    assert any(
        "daily_summary_skipped_no_predictable_fixture" in record.getMessage()
        for record in caplog.records)


def test_replay_after_partial_failure_stays_idempotent() -> None:
    """Un segundo ciclo no republica ni vuelve a llamar al modelo."""

    gateway, transport, repository = _Gateway(), _Transport(), _repository()
    publisher = TelegramChannelPublisher(gateway, repository, transport)

    publisher.run_cycle(NOW)
    calls_after_first = gateway.prediction_calls
    published_after_first = len(transport.messages)

    assert publisher.run_cycle(NOW)["summaries"] == 0
    assert len(transport.messages) == published_after_first
    assert gateway.prediction_calls == calls_after_first
