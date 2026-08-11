"""Pruebas idempotentes del publicador de canal Telegram."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.settlement_store import (
    SettlementBase,
    SettlementRecord,
    SqlAlchemySettlementRepository,
)
from src.telegram_bot import PredictionGateway
from src.telegram_channel_publisher import (
    ChannelBroadcastBase,
    ChannelTransport,
    SqlAlchemyChannelRepository,
    TelegramChannelPublisher,
    _daily_track_record_chunks,
    channel_prediction_messages,
)
from src.telegram_mobile_layout import mobile_layout_issues

MEXICO_TZ = ZoneInfo("America/Mexico_City")


class _Gateway(PredictionGateway):
    """Gateway determinista sin red externa."""

    def __init__(self) -> None:
        """Inicializa una predicción mutable y settlement reconciliado."""

        self.probability_home = 0.60
        self.reconciled = True
        self.prediction_calls = 0
        self.include_recommended = False
        self.include_grid = False

    def predict_fixture(self, payload: dict[str, Any]) -> dict[str, Any]:
        """No se usa en la difusión programada."""

        return self.predict_upcoming(payload)

    def predict_upcoming(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Devuelve probabilidades principales representativas."""

        self.prediction_calls += 1
        markets = {
            "status": "experimental_shadow_not_promoted",
            "user_market_view": _market_rows(),
        }
        if self.include_recommended:
            markets["recommended_market_view"] = _recommended_rows()
        if self.include_grid:
            markets["bounded_market_grid_view"] = _grid_rows()
        return {
            **payload, "probability_home": self.probability_home,
            "probability_draw": 0.25, "probability_away": 0.15,
            "probability_over_2_5": 0.55, "probability_btts": 0.45,
            "experimental_team_markets": markets,
        }

    def readiness(self) -> dict[str, Any]:
        """Simula servicio listo."""

        return {"ready": True}

    def explorer_leagues(self) -> dict[str, Any]:
        """Expone una liga controlada."""

        return {"leagues": [{"slug": "mex.1", "name": "Liga MX"}]}

    def explorer_teams(self, league: str) -> dict[str, Any]:
        """Entrega escudos oficiales para presentación."""

        return {"teams": [
            {"id": "1", "name": "Puebla", "logo": "https://img.test/puebla.png"},
            {"id": "2", "name": "Guadalajara", "logo": "https://img.test/chivas.png"},
        ]}

    def list_upcoming(
        self, limit: int = 8, leagues: str | None = None,
        date: str | None = None,
    ) -> dict[str, Any]:
        """Publica el fixture sólo para el 30 de julio."""

        fixtures = [_fixture()] if date == "20260730" else []
        return {"fixtures": fixtures}

    def explorer_fixtures(self, league: str, date: str) -> dict[str, Any]:
        """Devuelve marcador final explícito."""

        return {"fixtures": [{
            **_fixture(), "home_score": "2", "away_score": "1",
            "status_detail": "Final",
        }]}

    def explorer_statistics(
        self, league: str, match_id: str, competition_id: str,
    ) -> dict[str, Any]:
        """Entrega reconciliación configurable."""

        return {
            "reconciled": self.reconciled,
            "score_reconciled": self.reconciled,
            "score": {"home": 2, "away": 1},
        }


class _Transport(ChannelTransport):
    """Captura mensajes y genera IDs deterministas."""

    def __init__(self) -> None:
        """Inicializa una bandeja vacía."""

        self.messages: list[str] = []
        self.card_logos: list[list[str]] = []

    def send_message(self, text: str) -> str:
        """Guarda el texto sin contactar Telegram."""

        self.messages.append(text)
        return str(len(self.messages))

    def send_prediction_card(self, text: str, logos: list[str]) -> str:
        """Captura texto y escudos de la tarjeta."""

        self.card_logos.append(logos)
        return self.send_message(text)


def _fixture() -> dict[str, Any]:
    """Construye un fixture futuro con identidad completa."""

    return {
        "league_slug": "mex.1", "match_id": 10, "competition_id": "10",
        "home_team_id": 1, "away_team_id": 2,
        "home_team_name": "Puebla", "away_team_name": "Guadalajara",
        "kickoff_ts": "2026-07-30T16:00:00+00:00",
    }


def _repository() -> SqlAlchemyChannelRepository:
    """Crea un ledger SQLite compartido en memoria."""

    engine = create_engine(
        "sqlite+pysqlite://", future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    ChannelBroadcastBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemyChannelRepository(factory)


def _settlement_repository() -> SqlAlchemySettlementRepository:
    """Crea el almacén de veredictos liquidados en memoria."""

    engine = create_engine(
        "sqlite+pysqlite://", future=True,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    SettlementBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemySettlementRepository(factory)


def _settlement(index: int, *, hit: bool, kickoff_local: datetime) -> SettlementRecord:
    """Construye un veredicto liquidado con kickoff normalizado a UTC."""

    actual = "Local" if hit else "Visitante"
    kickoff_utc = kickoff_local.astimezone(timezone.utc)
    return SettlementRecord(
        fixture_key=f"daily-fixture-{index}", league_slug="mex.1",
        match_id=2000 + index, competition_id="c",
        kickoff_ts=kickoff_utc, settled_at=kickoff_utc + timedelta(hours=3),
        home_team_name="Puebla", away_team_name="Guadalajara",
        score_home=2 if hit else 0, score_away=0 if hit else 2,
        prediction_hash=f"{index:064d}",
        official_verdicts={
            "one_x_two": {"predicted": "Local", "actual": actual, "hit": hit},
            "over_2_5": {"predicted": "No", "actual": "No", "hit": True},
            "btts": {"predicted": "No", "actual": "No", "hit": True},
        })


def test_daily_prediction_is_frozen_and_replay_is_idempotent() -> None:
    """Congela la primera probabilidad y no repite el resumen."""

    gateway, transport = _Gateway(), _Transport()
    repository = _repository()
    publisher = TelegramChannelPublisher(gateway, repository, transport)
    now = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)

    assert publisher.run_cycle(now)["summaries"] == 1
    gateway.probability_home = 0.10
    assert publisher.run_cycle(now)["summaries"] == 0
    frozen = repository.predictions()[0]
    assert frozen.prediction["probability_home"] == 0.60
    assert gateway.prediction_calls == 1
    assert len(transport.messages) == 3
    assert transport.card_logos == [[
        "https://img.test/puebla.png", "https://img.test/chivas.png"]]


def test_card_is_immediate_and_result_keeps_reconciliation_gates() -> None:
    """Publica la tarjeta a las 09:00 y rechaza settlement inconsistente."""

    gateway, transport = _Gateway(), _Transport()
    publisher = TelegramChannelPublisher(gateway, _repository(), transport)
    first = publisher.run_cycle(datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc))
    assert first["cards"] == 1
    assert first["markets"] == 1
    assert "PRIMER TIEMPO" in transport.messages[2]
    assert "Puebla" in transport.messages[2]
    assert "antes del inicio" in transport.messages[2]
    assert "experimental" not in transport.messages[2].lower()
    assert "baseline" not in transport.messages[2].lower()
    card_time = datetime(2026, 7, 30, 14, 30, tzinfo=timezone.utc)
    assert publisher.run_cycle(card_time)["cards"] == 0
    gateway.reconciled = False
    result_time = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)
    assert publisher.run_cycle(result_time)["results"] == 0
    gateway.reconciled = True
    assert publisher.run_cycle(result_time)["results"] == 1
    assert publisher.run_cycle(result_time)["results"] == 0
    assert "RESULTADO FINAL VERIFICADO" in transport.messages[-1]


def test_all_messages_stay_below_telegram_limit() -> None:
    """Verifica el límite conservador en resumen, tarjeta y resultado."""

    gateway, transport = _Gateway(), _Transport()
    publisher = TelegramChannelPublisher(gateway, _repository(), transport)
    publisher.run_cycle(datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc))
    publisher.run_cycle(datetime(2026, 7, 30, 14, 30, tzinfo=timezone.utc))
    publisher.run_cycle(datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc))
    assert transport.messages
    assert all(len(message) <= 3900 for message in transport.messages)
    assert all(not mobile_layout_issues(message) for message in transport.messages)


def test_lite_mode_freezes_only_three_nearest_fixtures() -> None:
    """Comprueba que el interruptor lite limita la entrega a tres partidos."""

    gateway, transport = _Gateway(), _Transport()
    original = gateway.list_upcoming

    def five_fixtures(
        limit: int = 8, leagues: str | None = None, date: str | None = None,
    ) -> dict[str, Any]:
        """Replica cinco fixtures ordenables sin red."""

        payload = original(limit, leagues, date)
        base = payload.get("fixtures", [])
        return {"fixtures": [
            {**base[0], "match_id": index, "competition_id": str(index)}
            for index in range(10, 15)]} if base else payload

    gateway.list_upcoming = five_fixtures  # type: ignore[method-assign]
    publisher = TelegramChannelPublisher(
        gateway, _repository(), transport, mode="lite")
    result = publisher.run_cycle(
        datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc))
    assert result["frozen"] == 3
    assert result["cards"] == 3
    assert result["markets"] == 3
    assert len(transport.card_logos) == 3


def test_visual_grid_sends_one_dashboard_per_fixture() -> None:
    """Reúne 1T, 2T y total sin perder sus tablas compactas."""

    gateway, transport = _Gateway(), _Transport()
    gateway.include_grid = True
    publisher = TelegramChannelPublisher(gateway, _repository(), transport)
    result = publisher.run_cycle(datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc))

    assert result["markets"] == 1
    dashboard = transport.messages[-1]
    assert "<pre>" in dashboard
    assert "PRIMER TIEMPO" in dashboard
    assert "SEGUNDO TIEMPO" in dashboard
    assert "PARTIDO COMPLETO" in dashboard
    assert len(dashboard) <= 3900


def test_channel_cards_with_long_names_remain_mobile_safe() -> None:
    """Presiona tarjeta y dashboard con nombres largos sin filas ambiguas."""

    gateway = _Gateway()
    gateway.include_grid = True
    fixture = {
        **_fixture(),
        "home_team_name": "Club Deportivo Independiente de la Montaña",
        "away_team_name": "Asociación Deportiva Internacional del Valle",
    }
    prediction = gateway.predict_upcoming(fixture)
    messages = channel_prediction_messages(fixture, prediction)

    assert messages
    assert all(not mobile_layout_issues(message) for message in messages)


def test_legacy_freeze_gets_append_only_distributional_market_snapshot() -> None:
    """Completa mercados variables sin sobrescribir la predicción original."""

    gateway, transport = _Gateway(), _Transport()
    repository = _repository()
    publisher = TelegramChannelPublisher(gateway, repository, transport)
    now = datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc)
    legacy = gateway.predict_upcoming(_fixture())
    repository.freeze(_fixture(), legacy, "2026-07-30", now)
    original = repository.predictions()[0].prediction

    gateway.include_recommended = True
    replay = publisher.run_cycle(now)
    snapshot = repository.market_snapshot(
        "mex.1:10", "phase102_v4_direct_totals")

    assert replay["markets"] == 1
    assert snapshot is not None
    assert repository.predictions()[0].prediction == original
    assert "ESCENARIOS MÁS PROBABLES" in transport.messages[-1]
    assert publisher.run_cycle(now)["markets"] == 0


def test_daily_track_record_publishes_the_previous_local_day_with_misses() -> None:
    """Fase 121 (DEC-161): el aviso diario nunca oculta un fallo."""

    gateway, transport = _Gateway(), _Transport()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ)))
    settlements.add_if_absent(_settlement(1, hit=False, kickoff_local=datetime(
        2026, 7, 29, 20, 0, tzinfo=MEXICO_TZ)))
    publisher = TelegramChannelPublisher(
        gateway, _repository(), transport, settlements=settlements)
    # 09:00 hora de Ciudad de México del 30 de julio = 15:00 UTC; el aviso
    # cubre el día calendario local anterior, el 29 de julio.
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)

    counts = publisher.run_cycle(now)

    assert counts["track_record_daily"] == 1
    daily = next(
        message for message in transport.messages
        if "RESULTADOS DEL DÍA" in message)
    assert "✅" in daily
    assert "❌" in daily
    assert "1/2" in daily
    assert "29/07/2026" in daily


def test_daily_track_record_replay_is_idempotent() -> None:
    gateway, transport = _Gateway(), _Transport()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ)))
    publisher = TelegramChannelPublisher(
        gateway, _repository(), transport, settlements=settlements)
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)

    first = publisher.run_cycle(now)
    second = publisher.run_cycle(now)

    assert first["track_record_daily"] == 1
    assert second["track_record_daily"] == 0
    assert sum(1 for m in transport.messages if "RESULTADOS DEL DÍA" in m) == 1


def test_daily_track_record_is_silent_before_the_summary_time() -> None:
    """No publica antes de las 09:00 locales, igual que el resumen semanal."""

    gateway, transport = _Gateway(), _Transport()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ)))
    publisher = TelegramChannelPublisher(
        gateway, _repository(), transport, settlements=settlements)
    before_summary = datetime(2026, 7, 30, 12, 0, tzinfo=timezone.utc)

    counts = publisher.run_cycle(before_summary)

    assert counts["track_record_daily"] == 0
    assert not any("RESULTADOS DEL DÍA" in m for m in transport.messages)


def test_daily_track_record_is_silent_without_settled_matches() -> None:
    gateway, transport = _Gateway(), _Transport()
    settlements = _settlement_repository()
    publisher = TelegramChannelPublisher(
        gateway, _repository(), transport, settlements=settlements)
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)

    counts = publisher.run_cycle(now)

    assert counts["track_record_daily"] == 0


def test_daily_track_record_ignores_the_current_day_and_other_leagues_day() -> None:
    """Sólo cuenta el día calendario local anterior completo, no el actual."""

    gateway, transport = _Gateway(), _Transport()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 30, 8, 0, tzinfo=MEXICO_TZ)))
    publisher = TelegramChannelPublisher(
        gateway, _repository(), transport, settlements=settlements)
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)

    counts = publisher.run_cycle(now)

    assert counts["track_record_daily"] == 0


def test_daily_track_record_text_stays_mobile_safe_with_long_names() -> None:
    from dataclasses import replace as dataclass_replace
    from datetime import date as date_cls

    base = _settlement(
        0, hit=False, kickoff_local=datetime(2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ))
    long = dataclass_replace(
        base,
        home_team_name="Club Deportivo Independiente de la Montaña",
        away_team_name="Asociación Deportiva Internacional del Valle")

    chunks = _daily_track_record_chunks(date_cls(2026, 7, 29), [long])

    assert chunks
    assert all(not mobile_layout_issues(chunk) for chunk in chunks)


def _market_rows() -> list[dict[str, Any]]:
    """Construye mercados shadow representativos por periodo."""

    return [
        _market("corners", "home", "first_half", 2.5, 0.58, 0.52),
        _market("shots", "away", "second_half", 5.5, 0.61, 0.55),
        _market("shots_on_target", "total", "full_match", 7.5, 0.47, 0.49),
    ]


def _recommended_rows() -> list[dict[str, Any]]:
    """Construye un escenario variable derivado de una distribución."""

    return [{
        "metric": "corners", "team_side": "home",
        "period": "first_half", "line": 1.5, "direction": "under",
        "probability": 0.63, "baseline_probability": 0.57,
        "incremental_probability": 0.06, "expected_count": 1.2,
        "status": "experimental_shadow_not_promoted",
    }]


def _grid_rows() -> list[dict[str, Any]]:
    """Construye tres grupos visuales, uno por periodo."""

    return [_grid(period) for period in (
        "first_half", "second_half", "full_match")]


def _grid(period: str) -> dict[str, Any]:
    """Crea tres líneas complementarias para una tarjeta de prueba."""

    return {
        "metric": "corners", "team_side": "home", "period": period,
        "lines": [_grid_line(1.5, 0.68), _grid_line(2.5, 0.52),
                  _grid_line(3.5, 0.38)],
    }


def _grid_line(line: float, over: float) -> dict[str, float]:
    """Crea una fila de escalera con baseline independiente."""

    baseline = max(over - 0.03, 0.0)
    return {
        "line": line, "over_probability": over,
        "under_probability": 1.0 - over,
        "baseline_over_probability": baseline,
        "baseline_under_probability": 1.0 - baseline,
    }


def _market(
    metric: str, side: str, period: str, line: float,
    probability: float, baseline: float,
) -> dict[str, Any]:
    """Compone una fila compatible con el contrato de Fase 93."""

    return {
        "metric": metric, "team_side": side, "period": period,
        "line": line, "probability": probability,
        "baseline_probability": baseline, "source_model": "markov",
        "status": "experimental_shadow_not_promoted",
    }


# Version: 1.3.0
# Created: 2026-07-29
