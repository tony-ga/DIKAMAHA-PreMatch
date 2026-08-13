"""Pruebas de Fase 123: congelación y liquidación prospectiva del menú de
mayor probabilidad."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.high_probability_settlement import (
    PickSettlementBase,
    SqlAlchemyHighProbabilityPickRepository,
    fixture_key,
    freeze_from_pick,
    pick_key,
    prospective_reliability,
    resolve_goal_market,
    resolve_team_market,
)
from src.settlement_store import SettlementRecord

KICKOFF = datetime(2026, 8, 20, 20, 0, tzinfo=timezone.utc)
FROZEN_AT = KICKOFF - timedelta(hours=6)


def _repository() -> SqlAlchemyHighProbabilityPickRepository:
    engine = create_engine(
        "sqlite+pysqlite://", future=True,
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    PickSettlementBase.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, class_=Session)
    return SqlAlchemyHighProbabilityPickRepository(factory)


def _fixture(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "match_id": 900001, "league_slug": "esp.1",
        "kickoff_ts": KICKOFF.isoformat(),
        "home_team_id": 1, "away_team_id": 2,
        "home_team_name": "Local", "away_team_name": "Visitante",
    }
    values.update(changes)
    return values


def _pick(**changes: object) -> dict[str, object]:
    values: dict[str, object] = {
        "market": "1x2", "direction": "home", "confidence": 0.72,
        "metric": "result", "team_side": "match", "period": "full_match",
        "line": None, "model_probability": 0.72, "observed_rate": 0.80,
        "observed_ci95": [0.70, 0.88], "sample_size": 40,
        "edge_source": "model_edge", "bucket": [0.65, 0.75],
        "league_stability": 0.9, "status": "experimental_shadow_not_promoted",
    }
    values.update(changes)
    return values


def _settlement(**changes: object) -> SettlementRecord:
    values: dict[str, object] = {
        "fixture_key": "esp.1:900001", "league_slug": "esp.1",
        "match_id": 900001, "competition_id": "900001",
        "kickoff_ts": KICKOFF, "settled_at": KICKOFF + timedelta(hours=3),
        "home_team_name": "Local", "away_team_name": "Visitante",
        "score_home": 2, "score_away": 0, "prediction_hash": "a" * 64,
        "official_verdicts": {
            "one_x_two": {"predicted": "Local", "actual": "Local", "hit": True},
            "over_2_5": {"predicted": "No", "actual": "No", "hit": True},
            "btts": {"predicted": "No", "actual": "No", "hit": True},
        },
    }
    values.update(changes)
    return SettlementRecord(**values)  # type: ignore[arg-type]


def test_fixture_key_matches_channel_publisher_convention() -> None:
    assert fixture_key("esp.1", 900001) == "esp.1:900001"


def test_pick_key_is_stable_and_distinguishes_lines() -> None:
    fx = fixture_key("esp.1", 900001)
    over = _pick(market="home_corners_over_4_5", direction="over",
                 team_side="home", period="full_match", line=4.5)
    under = _pick(market="home_corners_over_4_5", direction="under",
                  team_side="home", period="full_match", line=4.5)

    assert pick_key(fx, over) == pick_key(fx, over)
    assert pick_key(fx, over) != pick_key(fx, under)


def test_freeze_from_pick_rejects_naive_kickoff() -> None:
    fixture = _fixture(kickoff_ts="2026-08-20T20:00:00")
    try:
        freeze_from_pick(_pick(), fixture, "sha", FROZEN_AT)
    except ValueError as error:
        assert "naive" in str(error)
    else:  # pragma: no cover - debe fallar siempre
        raise AssertionError("naive kickoff should be rejected")


def test_freeze_and_settle_round_trip_is_idempotent() -> None:
    repository = _repository()
    fixture = _fixture()
    record = freeze_from_pick(_pick(), fixture, "sha256value", FROZEN_AT)

    assert repository.freeze_if_absent(record) is True
    assert repository.freeze_if_absent(record) is False

    settlement = _settlement()
    verdict = resolve_goal_market(record, settlement)
    assert verdict is not None
    assert repository.settle_if_absent(verdict) is True
    assert repository.settle_if_absent(verdict) is False

    settled = repository.settled_recent(10)
    assert len(settled) == 1
    assert settled[0].hit is True


def test_unsettled_excludes_picks_with_future_kickoff() -> None:
    repository = _repository()
    past = freeze_from_pick(
        _pick(), _fixture(match_id=1, kickoff_ts=KICKOFF.isoformat()),
        "sha", FROZEN_AT)
    future = freeze_from_pick(
        _pick(), _fixture(match_id=2, kickoff_ts=(KICKOFF + timedelta(days=3)).isoformat()),
        "sha", FROZEN_AT)
    repository.freeze_if_absent(past)
    repository.freeze_if_absent(future)

    pending = repository.unsettled(KICKOFF + timedelta(hours=1))

    assert [row.match_id for row in pending] == [1]


def test_resolve_goal_market_maps_1x2_over_and_btts() -> None:
    fixture = _fixture()
    settlement = _settlement()

    home = resolve_goal_market(
        freeze_from_pick(_pick(market="1x2"), fixture, "sha", FROZEN_AT), settlement)
    over = resolve_goal_market(
        freeze_from_pick(_pick(market="over_2_5"), fixture, "sha", FROZEN_AT), settlement)
    btts = resolve_goal_market(
        freeze_from_pick(_pick(market="btts"), fixture, "sha", FROZEN_AT), settlement)

    assert home is not None and home.hit is True
    assert over is not None and over.hit is True
    assert btts is not None and btts.hit is True


def test_resolve_goal_market_returns_none_for_team_markets() -> None:
    fixture = _fixture()
    pick = freeze_from_pick(
        _pick(market="home_corners_over_4_5", team_side="home",
              period="full_match", line=4.5, direction="over"),
        fixture, "sha", FROZEN_AT)

    assert resolve_goal_market(pick, _settlement()) is None


def test_resolve_team_market_uses_the_pick_own_fixed_line() -> None:
    fixture = _fixture()
    pick = freeze_from_pick(
        _pick(market="home_corners_over_4_5", metric="corners",
              team_side="home", period="full_match", line=4.5, direction="over"),
        fixture, "sha", FROZEN_AT)
    statistics = {"periods": {"home": {"full_match": {"corners": 6}}}}

    verdict = resolve_team_market(pick, statistics, KICKOFF + timedelta(hours=3))

    assert verdict is not None
    assert verdict.hit is True
    assert verdict.settlement_source == "team_market_statistics"


def test_resolve_team_market_misses_when_line_not_covered() -> None:
    fixture = _fixture()
    pick = freeze_from_pick(
        _pick(market="home_corners_over_4_5", metric="corners",
              team_side="home", period="full_match", line=4.5, direction="over"),
        fixture, "sha", FROZEN_AT)
    statistics = {"periods": {"away": {"full_match": {"corners": 6}}}}

    assert resolve_team_market(pick, statistics, KICKOFF + timedelta(hours=3)) is None


def test_resolve_team_market_accepts_a_ladder_sourced_market_key() -> None:
    """La clave de grupo de la escalera auditada (Etapa 4) también liquida.

    `"home_corners"` nunca estuvo en `MARKET_METADATA` -es la clave de
    `src/ladder_pick_selection.py`, no una línea fija-, pero su
    metric/team_side son estructuralmente válidos, así que debe liquidar
    igual que una línea fija heredada.
    """

    fixture = _fixture()
    pick = freeze_from_pick(
        _pick(market="home_corners", metric="corners",
              team_side="home", period="full_match", line=4.5, direction="over"),
        fixture, "sha", FROZEN_AT)
    statistics = {"periods": {"home": {"full_match": {"corners": 6}}}}

    verdict = resolve_team_market(pick, statistics, KICKOFF + timedelta(hours=3))

    assert verdict is not None
    assert verdict.hit is True
    assert verdict.settlement_source == "team_market_statistics"


def test_resolve_team_market_rejects_an_unrecognized_metric_or_side() -> None:
    """Un `market` que no es ni una línea fija heredada ni una métrica/lado
    conocidos de la escalera se rechaza -nunca liquida un string inventado."""

    fixture = _fixture()
    bad_metric = freeze_from_pick(
        _pick(market="unknown_market_key", metric="possession",
              team_side="home", period="full_match", line=4.5, direction="over"),
        fixture, "sha", FROZEN_AT)
    bad_side = freeze_from_pick(
        _pick(market="another_unknown_key", metric="corners",
              team_side="referee", period="full_match", line=4.5, direction="over"),
        fixture, "sha", FROZEN_AT)
    statistics = {"periods": {"home": {"full_match": {"corners": 6}}}}

    assert resolve_team_market(bad_metric, statistics, KICKOFF + timedelta(hours=3)) is None
    assert resolve_team_market(bad_side, statistics, KICKOFF + timedelta(hours=3)) is None


def test_prospective_reliability_hides_rate_below_minimum_sample() -> None:
    fixture = _fixture()
    frozen = [freeze_from_pick(
        _pick(bucket=[0.65, 0.75]), fixture, "sha", FROZEN_AT)]
    settled = [resolve_goal_market(frozen[0], _settlement())]

    summary = prospective_reliability(frozen, [row for row in settled if row])

    cell = summary["cells"][0]
    assert cell["sufficient_sample"] is False
    assert "observed_rate_prospective" not in cell
    assert cell["missing_for_rate"] == 19


def test_prospective_reliability_reports_rate_once_enough_sample() -> None:
    fixture = _fixture()
    frozen = []
    settled = []
    for index in range(20):
        record = freeze_from_pick(
            _pick(bucket=[0.65, 0.75]),
            _fixture(match_id=1000 + index), "sha", FROZEN_AT)
        frozen.append(record)
        hit = index % 5 != 0
        settlement = _settlement(
            fixture_key=record.fixture_key,
            official_verdicts={
                "one_x_two": {
                    "predicted": "Local",
                    "actual": "Local" if hit else "Visitante", "hit": hit,
                },
                "over_2_5": {"predicted": "No", "actual": "No", "hit": True},
                "btts": {"predicted": "No", "actual": "No", "hit": True},
            })
        settled.append(resolve_goal_market(record, settlement))

    summary = prospective_reliability(frozen, [row for row in settled if row])

    cell = summary["cells"][0]
    assert cell["sufficient_sample"] is True
    assert cell["total"] == 20
    assert cell["observed_rate_prospective"] == 16 / 20
    assert summary["total_frozen"] == 20
    assert summary["total_settled"] == 20


# Version: 1.0.0
# Created: 2026-08-12
