"""Pruebas idempotentes del publicador de canal Telegram."""

from __future__ import annotations

import logging
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
from src.telegram_bot import PredictionGateway, PredictionGatewayError
from src.telegram_channel_publisher import (
    ChannelBroadcastBase,
    ChannelTransport,
    FrozenPrediction,
    SqlAlchemyChannelRepository,
    TelegramChannelPublisher,
    _daily_track_record_chunks,
    _shadow_verdicts,
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
        # DEC-189: controles para reproducir el respaldo de liquidación y el
        # aislamiento por fila sin depender de un aplazamiento real de ESPN.
        self.date_lookup_finds_fixture = True
        self.statistics_is_final = True
        self.statistics_status_detail = "Final"
        self.failing_match_ids: set[int] = set()

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
        """Devuelve marcador final explícito, salvo que se simule un
        aplazamiento -`date_lookup_finds_fixture = False`- que reproduce el
        caso en que ESPN archiva el partido bajo una fecha distinta a la del
        kickoff original y `_final_fixture` nunca lo encuentra."""

        if not self.date_lookup_finds_fixture:
            return {"fixtures": []}
        return {"fixtures": [{
            **_fixture(), "home_score": "2", "away_score": "1",
            "status_detail": "Final",
        }]}

    def explorer_statistics(
        self, league: str, match_id: str, competition_id: str,
    ) -> dict[str, Any]:
        """Entrega reconciliación configurable."""

        if int(match_id) in self.failing_match_ids:
            raise PredictionGatewayError("dikamaha_service_unavailable")
        return {
            "reconciled": self.reconciled,
            "score_reconciled": self.reconciled,
            "score": {"home": 2, "away": 1},
            "is_final": self.statistics_is_final,
            "status_detail": self.statistics_status_detail,
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


def _settlement(
    index: int, *, hit: bool, kickoff_local: datetime,
    settled_at: datetime | None = None,
) -> SettlementRecord:
    """Construye un veredicto liquidado con kickoff normalizado a UTC.

    `fixture_key` usa el mismo esquema `liga:match_id` que
    `_freeze_prediction_for`, no un identificador arbitrario: el nuevo
    disparador de `_daily_track_record` (DEC-168) exige que la clave de cada
    settlement coincida exactamente con la de su predicción congelada para
    considerar el día completo.
    """

    actual = "Local" if hit else "Visitante"
    kickoff_utc = kickoff_local.astimezone(timezone.utc)
    return SettlementRecord(
        fixture_key=f"mex.1:{2000 + index}", league_slug="mex.1",
        match_id=2000 + index, competition_id="c",
        kickoff_ts=kickoff_utc,
        settled_at=settled_at or (kickoff_utc + timedelta(hours=3)),
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


def test_stale_fixture_falls_back_to_match_id_indexed_statistics(caplog) -> None:
    """DEC-189: un partido que ESPN reindexa bajo otra fecha no se atasca.

    `_final_fixture` ubica el partido por fecha de calendario -México y UTC
    del kickoff original- y nunca lo encuentra si el proveedor lo archiva
    bajo otra fecha (aplazamiento, reindexado). Antes eso dejaba la fila en
    `still_pending` para siempre, sin ninguna línea en el log que lo
    explicara -el caso exacto de los 43 picks de DEC-184-. El respaldo
    consulta `explorer_statistics`, indexado por `match_id` y por lo tanto
    inmune a esa fragilidad, después de `STALE_FIXTURE_LOOKUP_GRACE`.
    """

    gateway, transport = _Gateway(), _Transport()
    gateway.date_lookup_finds_fixture = False
    publisher = TelegramChannelPublisher(gateway, _repository(), transport)
    publisher.run_cycle(datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc))

    # kickoff + 3h: elegible para SETTLEMENT_DELAY, pero todavía no para el
    # respaldo -no debe intentar `explorer_statistics` ni publicar nada-.
    just_after_kickoff = datetime(2026, 7, 30, 19, 0, tzinfo=timezone.utc)
    with caplog.at_level(logging.WARNING):
        result = publisher.run_cycle(just_after_kickoff)
    assert result["results"] == 0
    assert "channel_final_fixture_lookup_stale" not in caplog.text

    # kickoff + 13h: ya pasó STALE_FIXTURE_LOOKUP_GRACE (12h). El respaldo se
    # activa, deja constancia en el log y liquida usando el estado indexado
    # por match_id en vez del scoreboard por fecha.
    stale_time = datetime(2026, 7, 31, 5, 0, tzinfo=timezone.utc)
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        result = publisher.run_cycle(stale_time)
    assert result["results"] == 1
    assert "channel_final_fixture_lookup_stale" in caplog.text
    assert "mex.1:10" in caplog.text
    assert "RESULTADO FINAL VERIFICADO" in transport.messages[-1]


def test_stale_fixture_without_final_status_stays_pending_and_logs() -> None:
    """Si tampoco el respaldo confirma un final, no se inventa un resultado.

    Antes de esta corrección no había ningún registro de este caso: la fila
    simplemente no aparecía nunca en `results`. Ahora al menos queda un log
    con el motivo, para que una liga genuinamente en curso (o cualquier otro
    estado no final) sea diagnosticable sin acceso directo a la base.
    """

    gateway, transport = _Gateway(), _Transport()
    gateway.date_lookup_finds_fixture = False
    gateway.statistics_is_final = False
    gateway.statistics_status_detail = "En curso"
    publisher = TelegramChannelPublisher(gateway, _repository(), transport)
    publisher.run_cycle(datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc))

    stale_time = datetime(2026, 7, 31, 5, 0, tzinfo=timezone.utc)
    result = publisher.run_cycle(stale_time)
    assert result["results"] == 0


def test_one_broken_fixture_does_not_block_settlement_of_the_rest(caplog) -> None:
    """DEC-189: aislamiento por fila en `_results`.

    Antes, una excepción sin capturar al liquidar la fila más antigua
    -`predictions()` ordena por kickoff- abortaba el bucle completo, así que
    ninguna fila más nueva se liquidaba nunca en ningún ciclo posterior: el
    fixture roto bloqueaba a todos los que llegaron después de él,
    indefinidamente, sin que ningún log lo señalara. Este es el mecanismo más
    verosímil detrás de "43 picks estancados, sin variar entre ciclos ni
    entre ocho redeploys" que DEC-184 no pudo explicar.
    """

    gateway, transport = _Gateway(), _Transport()

    def two_fixtures(
        limit: int = 8, leagues: str | None = None, date: str | None = None,
    ) -> dict[str, Any]:
        if date != "20260730":
            return {"fixtures": []}
        older = {**_fixture(), "match_id": 10, "competition_id": "10",
                 "kickoff_ts": "2026-07-30T15:00:00+00:00"}
        newer = {**_fixture(), "match_id": 11, "competition_id": "11",
                 "kickoff_ts": "2026-07-30T17:00:00+00:00"}
        return {"fixtures": [older, newer]}

    def both_final(league: str, date: str) -> dict[str, Any]:
        return {"fixtures": [
            {**_fixture(), "match_id": 10, "status_detail": "Final",
             "home_score": "2", "away_score": "1"},
            {**_fixture(), "match_id": 11, "status_detail": "Final",
             "home_score": "2", "away_score": "1"},
        ]}

    gateway.list_upcoming = two_fixtures  # type: ignore[method-assign]
    gateway.explorer_fixtures = both_final  # type: ignore[method-assign]
    gateway.failing_match_ids = {10}
    publisher = TelegramChannelPublisher(gateway, _repository(), transport)
    frozen = publisher.run_cycle(datetime(2026, 7, 29, 15, 0, tzinfo=timezone.utc))
    assert frozen["frozen"] == 2

    settle_time = datetime(2026, 7, 30, 21, 0, tzinfo=timezone.utc)
    with caplog.at_level(logging.WARNING):
        result = publisher.run_cycle(settle_time)

    # `results == 1`: la fila 10 (más antigua, la primera en la cola por
    # kickoff) falló y se registró, pero la 11 -detrás de ella- se liquidó
    # igual en el mismo ciclo. Antes de aislar la excepción, `results`
    # habría sido 0 -la fila 11 nunca se habría llegado a evaluar-.
    assert result["results"] == 1
    assert "channel_settlement_row_failed" in caplog.text
    assert "mex.1:10" in caplog.text
    assert "RESULTADO FINAL VERIFICADO" in transport.messages[-1]

    # El fixture roto se reintenta cada ciclo -no se descarta-, pero ya no
    # bloquea al que sí puede liquidarse.
    gateway.failing_match_ids = set()
    caplog.clear()
    with caplog.at_level(logging.WARNING):
        result = publisher.run_cycle(settle_time)
    assert result["results"] == 1
    assert "channel_settlement_row_failed" not in caplog.text


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


def test_with_logos_skips_a_broken_league_and_keeps_the_rest() -> None:
    """DEC-191: una liga con `explorer_teams` roto no debe tumbar el día entero.

    `_with_logos` corre antes de `_freeze_all` en `_daily()`: sin aislar por
    liga, una excepción aquí -para una sola de las varias ligas presentes en
    los fixtures de mañana- abortaba la congelación del día **completo**, sin
    ningún fixture de ninguna liga, en cada ciclo mientras esa liga siguiera
    fallando. `_attach_logos` ya degrada limpio ante un catálogo ausente
    (escudo vacío), así que saltarse la liga rota no debe perder nada más.
    """

    gateway, transport = _Gateway(), _Transport()
    original = gateway.explorer_teams

    def flaky_teams(league: str) -> dict[str, Any]:
        if league == "eng.1":
            raise RuntimeError("espn_unavailable")
        return original(league)

    gateway.explorer_teams = flaky_teams  # type: ignore[method-assign]
    publisher = TelegramChannelPublisher(gateway, _repository(), transport)
    fixtures = [
        {**_fixture(), "league_slug": "eng.1", "match_id": 1,
         "home_team_id": 1, "away_team_id": 2},
        {**_fixture(), "league_slug": "mex.1", "match_id": 2,
         "home_team_id": 1, "away_team_id": 2},
    ]

    result = publisher._with_logos(fixtures)

    broken_league, healthy_league = result
    assert broken_league["home_team_logo"] == ""
    assert healthy_league["home_team_logo"] == "https://img.test/puebla.png"


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


def _frozen(fixture_key: str, match_id: int, kickoff: datetime) -> FrozenPrediction:
    """Construye una predicción congelada mínima, sin pasar por el repositorio."""

    fixture = {
        **_fixture(), "match_id": match_id, "home_team_name": "Puebla",
        "away_team_name": "Guadalajara", "kickoff_ts": kickoff.isoformat(),
    }
    prediction = {
        "probability_home": 0.6, "probability_draw": 0.25, "probability_away": 0.15,
        "probability_over_2_5": 0.55, "probability_btts": 0.45,
        "experimental_team_markets": {
            "status": "experimental_shadow_not_promoted",
            "user_market_view": _market_rows(),
        },
    }
    return FrozenPrediction(
        fixture_key=fixture_key, target_date=kickoff.date().isoformat(),
        league_slug="mex.1", match_id=match_id, competition_id=str(match_id),
        kickoff_ts=kickoff, fixture=fixture, prediction=prediction,
        prediction_hash="a" * 64, frozen_at=kickoff - timedelta(hours=12))


def test_publish_predictions_isolates_a_broken_fixture_from_the_rest() -> None:
    """DEC-191: un fixture roto al publicar no debe bloquear a los siguientes.

    `_publish_predictions` recorre los partidos del día en orden de kickoff.
    `_publish_markets` puede pedir una predicción fresca a `predict_upcoming`
    si el mercado variable todavía no está congelado; sin aislar por
    fixture, una excepción ahí -para el partido con kickoff más próximo-
    cortaba el `for` y ningún partido posterior del día recibía tarjeta ni
    mercados en ese ciclo (mismo patrón que DEC-189/191 en `_results`/
    `run_settle_cycle`).
    """

    gateway, transport = _Gateway(), _Transport()
    original_predict = gateway.predict_upcoming

    def flaky_predict(payload: dict[str, Any]) -> dict[str, Any]:
        if int(payload.get("match_id", 0)) == 10:
            raise Exception("espn_unavailable")  # noqa: TRY002 - error genérico simulado
        return original_predict(payload)

    gateway.predict_upcoming = flaky_predict  # type: ignore[method-assign]
    publisher = TelegramChannelPublisher(gateway, _repository(), transport)
    now = datetime(2026, 7, 30, 10, 0, tzinfo=timezone.utc)
    broken = _frozen("mex.1:10", 10, now + timedelta(hours=2))
    healthy = _frozen("mex.1:11", 11, now + timedelta(hours=3))

    cards, markets = publisher._publish_predictions([broken, healthy], now)

    # Ambas tarjetas se publican (no dependen de `predict_upcoming`), pero
    # sólo el partido sano consigue mercados: el roto falla y se registra.
    assert cards == 2
    assert markets == 1
    assert any("Puebla" in message for message in transport.messages)


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


def _freeze_prediction_for(
    repository: SqlAlchemyChannelRepository, index: int,
    kickoff_local: datetime, target_date_text: str,
) -> None:
    """Siembra en `ChannelRepository` el fixture que respalda un settlement.

    El nuevo disparador de `_daily_track_record` exige que cada predicción
    congelada de un día tenga un settlement con la MISMA `fixture_key` antes
    de considerar ese día completo; en producción ambos siempre coinciden
    porque `_results` sólo liquida fixtures que ya pasaron por `freeze`, pero
    estas pruebas siembran el settlement por separado, así que necesitan
    sembrar también la predicción congelada correspondiente.
    """

    fixture = {
        "league_slug": "mex.1", "match_id": 2000 + index,
        "competition_id": str(2000 + index),
        "home_team_id": 1, "away_team_id": 2,
        "home_team_name": "Puebla", "away_team_name": "Guadalajara",
        "kickoff_ts": kickoff_local.astimezone(timezone.utc).isoformat(),
    }
    repository.freeze(
        fixture, {"probability_home": 0.6}, target_date_text,
        kickoff_local.astimezone(timezone.utc) - timedelta(hours=6))


def test_daily_track_record_publishes_the_same_day_30_minutes_after_the_last_match() -> None:
    """DEC-168: el aviso sale 30 minutos después de que el sistema confirma
    el último partido del día -no una estimación desde el kickoff, el
    `settled_at` real que `_seal_settlement` ya escribió-, y hereda de
    DEC-161 que nunca oculta un fallo.
    """

    gateway, transport = _Gateway(), _Transport()
    repository = _repository()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ)))
    settlements.add_if_absent(_settlement(1, hit=False, kickoff_local=datetime(
        2026, 7, 29, 20, 0, tzinfo=MEXICO_TZ)))
    _freeze_prediction_for(
        repository, 0, datetime(2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ),
        "2026-07-29")
    _freeze_prediction_for(
        repository, 1, datetime(2026, 7, 29, 20, 0, tzinfo=MEXICO_TZ),
        "2026-07-29")
    publisher = TelegramChannelPublisher(
        gateway, repository, transport, settlements=settlements)
    # El segundo partido (kickoff 20:00 CDMX) liquida a las 3h por defecto de
    # _settlement: 05:00 UTC del 30. +30 min = 05:30 UTC. El aviso sale esa
    # misma noche local (29 de julio), no a las 09:00 del día siguiente.
    now = datetime(2026, 7, 30, 5, 35, tzinfo=timezone.utc)

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
    repository = _repository()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ)))
    _freeze_prediction_for(
        repository, 0, datetime(2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ),
        "2026-07-29")
    publisher = TelegramChannelPublisher(
        gateway, repository, transport, settlements=settlements)
    # settled_at por defecto = kickoff (16:00 UTC) + 3h = 19:00 UTC; +30 min.
    now = datetime(2026, 7, 29, 19, 35, tzinfo=timezone.utc)

    first = publisher.run_cycle(now)
    second = publisher.run_cycle(now)

    assert first["track_record_daily"] == 1
    assert second["track_record_daily"] == 0
    assert sum(1 for m in transport.messages if "RESULTADOS DEL DÍA" in m) == 1


def test_daily_track_record_waits_for_every_match_of_the_day_to_settle() -> None:
    """"Íntegro" exige a todos los partidos del día, no sólo a los liquidados.

    Un partido congelado y liquidado no basta si otro partido del mismo día
    sigue pendiente: publicar de todos modos dejaría el resumen incompleto de
    forma permanente, porque la clave de idempotencia no vuelve a intentarlo.
    """

    gateway, transport = _Gateway(), _Transport()
    repository = _repository()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ)))
    _freeze_prediction_for(
        repository, 0, datetime(2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ),
        "2026-07-29")
    # Un segundo partido del mismo día está congelado pero aún sin liquidar.
    _freeze_prediction_for(
        repository, 1, datetime(2026, 7, 29, 20, 0, tzinfo=MEXICO_TZ),
        "2026-07-29")
    publisher = TelegramChannelPublisher(
        gateway, repository, transport, settlements=settlements)
    # Mucho después de que el primer partido liquidó; el segundo sigue sin
    # settlement, así que el día no se considera completo pase lo que pase.
    now = datetime(2026, 7, 31, 12, 0, tzinfo=timezone.utc)

    counts = publisher.run_cycle(now)

    assert counts["track_record_daily"] == 0
    assert not any("RESULTADOS DEL DÍA" in m for m in transport.messages)


def test_daily_track_record_waits_30_minutes_after_the_day_becomes_complete() -> None:
    """El día ya está completo, pero aún no pasan los 30 min de DEC-168."""

    gateway, transport = _Gateway(), _Transport()
    repository = _repository()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ)))
    _freeze_prediction_for(
        repository, 0, datetime(2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ),
        "2026-07-29")
    publisher = TelegramChannelPublisher(
        gateway, repository, transport, settlements=settlements)
    # settled_at = 19:00 UTC; sólo 10 minutos después, no los 30 exigidos.
    now = datetime(2026, 7, 29, 19, 10, tzinfo=timezone.utc)

    counts = publisher.run_cycle(now)

    assert counts["track_record_daily"] == 0
    assert not any("RESULTADOS DEL DÍA" in m for m in transport.messages)


def test_daily_track_record_is_silent_without_settled_matches() -> None:
    gateway, transport = _Gateway(), _Transport()
    repository = _repository()
    settlements = _settlement_repository()
    _freeze_prediction_for(
        repository, 0, datetime(2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ),
        "2026-07-29")
    publisher = TelegramChannelPublisher(
        gateway, repository, transport, settlements=settlements)
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)

    counts = publisher.run_cycle(now)

    assert counts["track_record_daily"] == 0


def test_daily_track_record_ignores_a_day_without_frozen_predictions() -> None:
    """Un settlement sin predicción congelada respaldándolo no dispara nada.

    En producción esto no ocurre (`_results` sólo liquida lo que `freeze`
    congeló primero), pero el disparador depende de `ChannelRepository`, no
    del almacén de settlements, así que debe degradar sin publicar si esa
    fuente no tiene nada para ese fixture.
    """

    gateway, transport = _Gateway(), _Transport()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ)))
    publisher = TelegramChannelPublisher(
        gateway, _repository(), transport, settlements=settlements)
    now = datetime(2026, 7, 30, 15, 0, tzinfo=timezone.utc)

    counts = publisher.run_cycle(now)

    assert counts["track_record_daily"] == 0


def test_daily_track_record_catches_up_a_day_missed_by_a_service_restart() -> None:
    """Recorrer todos los días pendientes recupera uno que quedó sin revisar.

    Antes el día objetivo se derivaba de `local.date()` en el momento del
    ciclo: si el servicio no corrió ningún ciclo durante varios días (por
    ejemplo, tras una caída) y "hoy" ya avanzó, ese día antiguo nunca volvía
    a coincidir con `local.date()` y su aviso se perdía para siempre. Iterar
    por día en vez de fijarlo a partir de `now` lo recupera igual.
    """

    gateway, transport = _Gateway(), _Transport()
    repository = _repository()
    settlements = _settlement_repository()
    settlements.add_if_absent(_settlement(0, hit=True, kickoff_local=datetime(
        2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ)))
    _freeze_prediction_for(
        repository, 0, datetime(2026, 7, 29, 10, 0, tzinfo=MEXICO_TZ),
        "2026-07-29")
    publisher = TelegramChannelPublisher(
        gateway, repository, transport, settlements=settlements)
    # El primer ciclo tras la caída corre tres días después.
    now = datetime(2026, 8, 1, 15, 0, tzinfo=timezone.utc)

    counts = publisher.run_cycle(now)

    assert counts["track_record_daily"] == 1
    assert any("RESULTADOS DEL DÍA" in m for m in transport.messages)


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


def _grid_snapshot(period: str) -> dict[str, Any]:
    """Rejilla congelada mínima con una sola línea, para probar `_shadow_verdicts`."""

    return {"bounded_market_grid_view": [{
        "key": "home_corners", "team_side": "home", "metric": "corners",
        "period": period,
        "lines": [{"line": 4.5, "over_probability": 0.7}],
    }]}


def test_shadow_verdicts_translate_full_match_to_the_total_period_key() -> None:
    """DEC-190: `explorer_statistics` nunca trae la clave `"full_match"`.

    `bounded_market_grid_view` declara sus líneas de partido completo como
    `period: "full_match"` -mismo vocabulario público que el resto del
    sistema-, pero `_period_statistics` (`src/espn_user_explorer.py`) sólo
    expone `first_half`/`second_half`/`total`. Antes de esta corrección,
    `_shadow_verdicts` buscaba la clave inexistente y omitía la línea en
    silencio siempre: ninguna línea de partido completo -la mayoría del
    universo, medido en producción- llegaba nunca a "Resultados de hoy".
    """

    statistics = {"periods": {"home": {"total": {"corners": 6}}}}

    verdicts = _shadow_verdicts(_grid_snapshot("full_match"), statistics)

    assert verdicts
    entry = next(iter(verdicts.values()))
    assert entry["hit"] is True
    assert entry["actual"] == "6 observados"


def test_shadow_verdicts_read_half_periods_without_translation() -> None:
    """Las mitades no necesitan traducción: son la misma clave en ambos lados."""

    statistics = {"periods": {"home": {"first_half": {"corners": 6}}}}

    verdicts = _shadow_verdicts(_grid_snapshot("first_half"), statistics)

    assert verdicts
    assert next(iter(verdicts.values()))["hit"] is True


def test_shadow_verdicts_omit_a_line_with_no_matching_observation() -> None:
    """Sin conteo real para ese lado/periodo, la línea se omite, no se inventa."""

    statistics = {"periods": {"away": {"total": {"corners": 6}}}}

    assert _shadow_verdicts(_grid_snapshot("full_match"), statistics) == {}


# Version: 1.3.0
# Created: 2026-07-29
