"""Pruebas del historial de aciertos verificable de Fase 118 y su resumen
diario de Fase 121 (DEC-161)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

MEXICO_TZ = ZoneInfo("America/Mexico_City")

from src.high_probability_settlement import (
    PickSettlementBase,
    SqlAlchemyHighProbabilityPickRepository,
    freeze_from_pick,
    resolve_goal_market,
)
from src.settlement_store import (
    MINIMUM_SAMPLE,
    SettlementBase,
    SettlementRecord,
    SqlAlchemySettlementRepository,
    track_record,
    wilson_interval,
)
from src.telegram_channel_publisher import (
    FrozenPrediction,
    _shadow_verdicts,
    _track_record_text,
    official_verdicts,
)


def _high_probability_repository() -> SqlAlchemyHighProbabilityPickRepository:
    engine = create_engine(
        "sqlite+pysqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    PickSettlementBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemyHighProbabilityPickRepository(factory)

KICKOFF = datetime(2026, 8, 1, 20, 0, tzinfo=timezone.utc)


def _repository() -> SqlAlchemySettlementRepository:
    engine = create_engine(
        "sqlite+pysqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SettlementBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemySettlementRepository(factory)


def _record(
    index: int, *, hit: bool = True, kickoff: datetime | None = None,
) -> SettlementRecord:
    actual = "Local" if hit else "Visitante"
    kickoff_ts = kickoff if kickoff is not None else KICKOFF + timedelta(days=index)
    return SettlementRecord(
        fixture_key=f"fixture-{index}",
        league_slug="esp.1", match_id=1000 + index, competition_id="c",
        kickoff_ts=kickoff_ts,
        settled_at=kickoff_ts + timedelta(hours=3),
        home_team_name="Local", away_team_name="Visitante",
        score_home=2 if hit else 0, score_away=0 if hit else 2,
        prediction_hash=f"{index:064d}",
        official_verdicts={
            "one_x_two": {"predicted": "Local", "actual": actual, "hit": hit},
            "over_2_5": {"predicted": "No", "actual": "No", "hit": True},
            "btts": {"predicted": "No", "actual": "No", "hit": True},
        },
    )


def _prediction(**changes: object) -> FrozenPrediction:
    values: dict[str, object] = {
        "fixture_key": "esp.1:900001",
        "target_date": "2026-08-01",
        "league_slug": "esp.1",
        "match_id": 900001,
        "competition_id": "900001",
        "kickoff_ts": KICKOFF,
        "fixture": {"home_team_name": "Local", "away_team_name": "Visitante"},
        "prediction": {
            "probability_home": 0.55, "probability_draw": 0.25,
            "probability_away": 0.20, "probability_over_2_5": 0.61,
            "probability_btts": 0.44,
        },
        "prediction_hash": "a" * 64,
        "frozen_at": KICKOFF - timedelta(days=1),
    }
    values.update(changes)
    return FrozenPrediction(**values)  # type: ignore[arg-type]


def test_official_verdicts_cover_win_draw_and_binary_markets() -> None:
    row = _prediction()
    verdicts = official_verdicts(
        row, {"home": 3, "away": 1}, "Local", "Visitante")

    assert verdicts["one_x_two"] == {
        "predicted": "Local", "actual": "Local", "hit": True}
    assert verdicts["over_2_5"]["predicted"] == "Sí"
    assert verdicts["over_2_5"]["actual"] == "Sí"
    assert verdicts["over_2_5"]["hit"] is True
    assert verdicts["btts"]["predicted"] == "No"
    assert verdicts["btts"]["actual"] == "Sí"
    assert verdicts["btts"]["hit"] is False


def test_official_verdicts_detect_a_draw_as_a_miss() -> None:
    verdicts = official_verdicts(
        _prediction(), {"home": 1, "away": 1}, "Local", "Visitante")

    assert verdicts["one_x_two"]["actual"] == "Empate"
    assert verdicts["one_x_two"]["hit"] is False


def test_shadow_verdicts_settle_against_period_counts() -> None:
    snapshot = {"prediction": {"bounded_market_grid_view": [{
        "key": "home_corners_remaining", "metric": "corners",
        "team_side": "home", "period": "total",
        "lines": [
            {"line": 4.5, "over_probability": 0.72},
            {"line": 6.5, "over_probability": 0.31},
        ],
    }]}}
    statistics = {"periods": {"home": {"total": {"corners": 6}}}}

    verdicts = _shadow_verdicts(snapshot, statistics)

    assert verdicts["home_corners_remaining_over_4_5"]["hit"] is True
    assert verdicts["home_corners_remaining_over_6_5"]["hit"] is True


def test_shadow_verdicts_are_empty_without_statistics() -> None:
    snapshot = {"prediction": {"bounded_market_grid_view": []}}

    assert _shadow_verdicts(snapshot, None) == {}
    assert _shadow_verdicts(None, {"periods": {}}) == {}


def test_settlement_is_append_only() -> None:
    repository = _repository()

    assert repository.add_if_absent(_record(1)) is True
    assert repository.add_if_absent(_record(1, hit=False)) is False
    stored = repository.recent(10)

    assert len(stored) == 1
    assert stored[0].official_verdicts["one_x_two"]["hit"] is True


def test_recent_returns_a_chronological_queue_including_misses() -> None:
    repository = _repository()
    for index in range(5):
        repository.add_if_absent(_record(index, hit=index % 2 == 0))

    rows = repository.recent(3)

    assert [row.fixture_key for row in rows] == [
        "fixture-4", "fixture-3", "fixture-2"]
    assert any(
        row.official_verdicts["one_x_two"]["hit"] is False for row in rows)


def test_on_date_filters_by_local_kickoff_day_not_utc_day() -> None:
    """El día lo define la fecha local del kickoff, no la fecha UTC.

    22:30 hora de Ciudad de México del 10 de agosto ya es 11 de agosto en
    UTC; un filtro ingenuo por fecha UTC lo colaría en el día equivocado.
    """

    repository = _repository()
    late_previous_day = datetime(
        2026, 8, 10, 22, 30, tzinfo=MEXICO_TZ).astimezone(timezone.utc)
    target_morning = datetime(
        2026, 8, 11, 10, 0, tzinfo=MEXICO_TZ).astimezone(timezone.utc)
    early_next_day = datetime(
        2026, 8, 12, 0, 30, tzinfo=MEXICO_TZ).astimezone(timezone.utc)
    repository.add_if_absent(_record(0, kickoff=late_previous_day, hit=True))
    repository.add_if_absent(_record(1, kickoff=target_morning, hit=False))
    repository.add_if_absent(_record(2, kickoff=early_next_day, hit=True))

    rows = repository.on_date(date(2026, 8, 11), MEXICO_TZ)

    assert [row.fixture_key for row in rows] == ["fixture-1"]
    assert rows[0].official_verdicts["one_x_two"]["hit"] is False


def test_on_date_orders_chronologically_and_includes_every_verdict() -> None:
    repository = _repository()
    early = datetime(2026, 8, 11, 9, 0, tzinfo=MEXICO_TZ).astimezone(timezone.utc)
    late = datetime(2026, 8, 11, 21, 0, tzinfo=MEXICO_TZ).astimezone(timezone.utc)
    repository.add_if_absent(_record(0, kickoff=late, hit=True))
    repository.add_if_absent(_record(1, kickoff=early, hit=False))

    rows = repository.on_date(date(2026, 8, 11), MEXICO_TZ)

    assert [row.fixture_key for row in rows] == ["fixture-1", "fixture-0"]


def test_on_date_returns_nothing_outside_the_requested_day() -> None:
    repository = _repository()
    repository.add_if_absent(_record(0, kickoff=datetime(
        2026, 8, 11, 12, 0, tzinfo=MEXICO_TZ).astimezone(timezone.utc)))

    assert repository.on_date(date(2026, 8, 12), MEXICO_TZ) == []


def test_track_record_hides_the_rate_below_the_minimum_sample() -> None:
    records = [_record(index) for index in range(MINIMUM_SAMPLE - 1)]

    summary = track_record(records)
    market = summary["official"]["one_x_two"]

    assert market["sufficient_sample"] is False
    assert "rate" not in market
    assert market["missing_for_rate"] == 1


def test_track_record_reports_rate_interval_and_baseline() -> None:
    records = [
        _record(index, hit=index % 4 != 0) for index in range(MINIMUM_SAMPLE)
    ]

    summary = track_record(records)
    market = summary["official"]["one_x_two"]

    assert market["sufficient_sample"] is True
    assert market["total"] == MINIMUM_SAMPLE
    assert 0.0 <= market["interval_95"][0] <= market["rate"]
    assert market["rate"] <= market["interval_95"][1] <= 1.0
    assert 0.0 <= market["baseline_rate"] <= 1.0


def test_track_record_exposes_every_settled_match_not_only_hits() -> None:
    records = [_record(index, hit=index == 0) for index in range(4)]

    summary = track_record(records)
    hits = [
        row["official_verdicts"]["one_x_two"]["hit"]
        for row in summary["matches"]
    ]

    assert len(summary["matches"]) == 4
    assert hits.count(False) == 3
    assert summary["shadow"]["status"] == "experimental_not_promoted"
    assert "acertados y no acertados" in summary["disclosure"]


def test_wilson_interval_is_wide_for_small_samples() -> None:
    narrow = wilson_interval(60, 100)
    wide = wilson_interval(6, 10)

    assert wide[1] - wide[0] > narrow[1] - narrow[0]
    assert wilson_interval(0, 0) == (0.0, 0.0)


def test_weekly_text_never_uses_profit_language() -> None:
    records = [_record(index) for index in range(MINIMUM_SAMPLE)]

    text = _track_record_text(track_record(records))
    lowered = text.casefold()

    assert "roi" not in lowered
    assert "cuota" not in lowered
    assert "stake" not in lowered
    assert "kelly" not in lowered
    assert "apuesta" not in lowered
    assert "referencia" in lowered


def test_weekly_text_states_insufficient_sample_without_a_percentage() -> None:
    text = _track_record_text(track_record([_record(0)]))
    market_lines = [line for line in text.splitlines() if line.startswith("1X2")]

    assert len(market_lines) == 1
    assert "muestra insuficiente" in market_lines[0]
    assert "%" not in market_lines[0]


def test_endpoint_degrades_without_a_configured_store() -> None:
    """La API arrancó sin base de datos hasta Fase 118; no debe romperse."""

    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    client = TestClient(create_app())
    payload = client.get("/v1/track-record").json()

    assert payload["status"] == "unavailable"
    assert payload["matches"] == []


def test_endpoint_publishes_the_chronological_queue() -> None:
    """Expone la cola completa y no admite filtrar por acierto."""

    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    repository = _repository()
    for index in range(4):
        repository.add_if_absent(_record(index, hit=index == 0))
    client = TestClient(create_app(settlement_store=repository))

    payload = client.get("/v1/track-record?window=3").json()

    assert payload["status"] == "available"
    assert payload["window"] == {
        **payload["window"], "requested": 3, "available": 3}
    assert [row["fixture_key"] for row in payload["matches"]] == [
        "fixture-3", "fixture-2", "fixture-1"]
    assert all(
        row["official_verdicts"]["one_x_two"]["hit"] is False
        for row in payload["matches"]
    )
    assert "hit" not in client.get(
        "/v1/track-record?hit=true").json()["window"]


def test_daily_endpoint_requires_a_date() -> None:
    """`date` es obligatorio: no hay valor por defecto de reloj de pared."""

    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    client = TestClient(create_app())

    assert client.get("/v1/track-record/daily").status_code == 422


def test_daily_endpoint_rejects_an_invalid_date_format() -> None:
    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    client = TestClient(create_app())

    response = client.get("/v1/track-record/daily?date=2026-08-11")

    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "contract_validation_error"


def test_daily_endpoint_degrades_without_a_configured_store() -> None:
    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    client = TestClient(create_app())

    payload = client.get("/v1/track-record/daily?date=20260811").json()

    assert payload["status"] == "unavailable"
    assert payload["matches"] == []
    assert payload["date"] == "2026-08-11"


def test_daily_endpoint_scopes_to_the_local_day_and_never_hides_misses() -> None:
    """Fase 121 (DEC-161): el resumen diario nunca filtra por acierto."""

    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    repository = _repository()
    repository.add_if_absent(_record(0, hit=True, kickoff=datetime(
        2026, 8, 11, 10, 0, tzinfo=MEXICO_TZ).astimezone(timezone.utc)))
    repository.add_if_absent(_record(1, hit=False, kickoff=datetime(
        2026, 8, 11, 20, 0, tzinfo=MEXICO_TZ).astimezone(timezone.utc)))
    repository.add_if_absent(_record(2, hit=True, kickoff=datetime(
        2026, 8, 12, 9, 0, tzinfo=MEXICO_TZ).astimezone(timezone.utc)))
    client = TestClient(create_app(settlement_store=repository))

    payload = client.get("/v1/track-record/daily?date=20260811").json()

    assert payload["status"] == "available"
    assert payload["date"] == "2026-08-11"
    # track_record() ordena sus "matches" del más reciente al más antiguo,
    # igual que /v1/track-record; fixture-1 (20:00) llegó después que
    # fixture-0 (10:00) ese mismo día local.
    fixture_keys = [row["fixture_key"] for row in payload["matches"]]
    assert fixture_keys == ["fixture-1", "fixture-0"]
    hits = [
        row["official_verdicts"]["one_x_two"]["hit"]
        for row in payload["matches"]
    ]
    assert hits == [False, True]
    filtered_attempt = client.get(
        "/v1/track-record/daily?date=20260811&hit=true").json()
    assert [row["fixture_key"] for row in filtered_attempt["matches"]] == (
        fixture_keys)


def _hp_fixture(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "match_id": 1000, "league_slug": "esp.1",
        "kickoff_ts": KICKOFF.isoformat(),
        "home_team_name": "Local", "away_team_name": "Visitante",
    }
    values.update(changes)
    return values


def _hp_pick(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "market": "1x2", "direction": "home", "metric": "result",
        "team_side": "match", "period": "full_match", "line": None,
        "model_probability": 0.72, "observed_rate": 0.80, "sample_size": 40,
        "edge_source": "model_edge", "bucket": [0.65, 0.75],
    }
    values.update(changes)
    return values


def test_track_record_high_probability_block_defaults_to_unavailable() -> None:
    """Sin `DATABASE_URL` el bloque nuevo degrada igual que el resto de la
    ventana de Aciertos, en vez de romper la respuesta (DEC-184)."""

    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    client = TestClient(create_app())

    payload = client.get("/v1/track-record").json()

    assert payload["high_probability"] == {
        "status": "unavailable", "picks": [],
        "summary": {"hits": 0, "settled": 0, "pending": 0, "total": 0},
    }


def test_daily_endpoint_high_probability_block_defaults_to_unavailable() -> None:
    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    client = TestClient(create_app())

    payload = client.get("/v1/track-record/daily?date=20260811").json()

    assert payload["high_probability"]["status"] == "unavailable"


def test_track_record_endpoint_includes_a_settled_high_probability_pick() -> None:
    """Fase 123 alimenta la ventana de Aciertos (DEC-184): un pick del menú
    de "Mayor probabilidad" ya liquidado aparece con su mismo `market`."""

    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    settlement_repository = _repository()
    settlement_repository.add_if_absent(_record(0, hit=True))
    hp_repository = _high_probability_repository()
    frozen = freeze_from_pick(
        _hp_pick(), _hp_fixture(), "sha256value", KICKOFF - timedelta(hours=6))
    hp_repository.freeze_if_absent(frozen)
    outcome = resolve_goal_market(frozen, _record(0, hit=True))
    assert outcome is not None
    hp_repository.settle_if_absent(outcome)
    client = TestClient(create_app(
        settlement_store=settlement_repository,
        high_probability_pick_store=hp_repository))

    payload = client.get("/v1/track-record?window=5").json()

    entries = payload["high_probability"]["picks"]
    assert len(entries) == 1
    assert entries[0]["market"] == "1x2"
    assert entries[0]["status"] == "hit"
    assert payload["high_probability"]["summary"] == {
        "hits": 1, "settled": 1, "pending": 0, "total": 1}


def test_daily_endpoint_lists_a_pending_high_probability_pick_for_that_day() -> None:
    """Un pick congelado sin liquidar todavía se publica, nunca se omite
    (DEC-158/161): la ventana diaria debe quedar íntegra."""

    from fastapi.testclient import TestClient

    from src.dikamaha_service import create_app

    settlement_repository = _repository()
    hp_repository = _high_probability_repository()
    kickoff = datetime(
        2026, 8, 11, 20, 0, tzinfo=MEXICO_TZ).astimezone(timezone.utc)
    frozen = freeze_from_pick(
        _hp_pick(market="over_2_5", direction="over"),
        _hp_fixture(match_id=2000, kickoff_ts=kickoff.isoformat()),
        "sha256value", kickoff - timedelta(hours=6))
    hp_repository.freeze_if_absent(frozen)
    client = TestClient(create_app(
        settlement_store=settlement_repository,
        high_probability_pick_store=hp_repository))

    payload = client.get("/v1/track-record/daily?date=20260811").json()

    entries = payload["high_probability"]["picks"]
    assert len(entries) == 1
    assert entries[0]["status"] == "pending"
    assert entries[0]["market"] == "over_2_5"
    assert payload["high_probability"]["summary"] == {
        "hits": 0, "settled": 0, "pending": 1, "total": 1}


# Version: 1.0.0
# Created: 2026-08-10
