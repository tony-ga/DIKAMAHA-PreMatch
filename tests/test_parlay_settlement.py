"""Pruebas del store propio de parlays (Fase 136)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from src.parlay_settlement import (
    LegSettlementRecord,
    ParlayBase,
    SqlAlchemyParlayRepository,
    freeze_from_leg,
    leg_key,
    parlay_key,
    prospective_delivery,
    reference_parlays,
    resolve_leg,
    settle_ready_parlays,
)

NOW = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
KICKOFF = NOW + timedelta(hours=6)


@pytest.fixture()
def repo():
    """Repositorio SQLite en memoria."""

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool, future=True)
    ParlayBase.metadata.create_all(engine)
    return SqlAlchemyParlayRepository(
        sessionmaker(bind=engine, expire_on_commit=False, class_=Session))


def _leg_payload(key: str = "home_corners_over_4_5", probability: float = 0.72):
    """Pierna tal como la emite el gate de Fase 135."""

    return {"key": key, "metric": "corners", "team_side": "home",
            "period": "full_match", "line": 4.5, "direction": "over",
            "probability": probability, "threshold": 0.6}


def _fixture(name: str, kickoff: datetime = KICKOFF):
    """Partido tal como lo agrupa `ParlayEligibilityView.menu`."""

    return {"fixture_key": name, "league_slug": "esp.1", "match_id": 991,
            "kickoff_ts": kickoff.isoformat()}


def _freeze(repo, name: str, probability: float = 0.72,
            kickoff: datetime = KICKOFF):
    """Congela una pierna y devuelve su registro."""

    record = freeze_from_leg(
        _leg_payload(probability=probability), _fixture(name, kickoff),
        "a" * 64, NOW)
    repo.freeze_leg_if_absent(record)
    return record


# --- identidad ------------------------------------------------------------

def test_leg_key_is_stable_and_distinguishes_line():
    """La clave de pierna es determinista y separa líneas distintas."""

    assert leg_key("f1", "m", 4.5) == leg_key("f1", "m", 4.5)
    assert leg_key("f1", "m", 4.5) != leg_key("f1", "m", 5.5)
    assert leg_key("f1", "m", 4.5) != leg_key("f2", "m", 4.5)


def test_parlay_key_ignores_leg_order():
    """La misma combinación no se congela dos veces por llegar desordenada."""

    assert parlay_key(["a", "b", "c"]) == parlay_key(["c", "a", "b"])
    assert parlay_key(["a", "b"]) != parlay_key(["a", "c"])


# --- append-only ----------------------------------------------------------

def test_freeze_is_append_only(repo):
    """Congelar dos veces conserva la primera versión."""

    record = _freeze(repo, "f1", probability=0.72)
    assert repo.freeze_leg_if_absent(record) is False
    stored = repo.legs_frozen_today(NOW.date().isoformat())
    assert len(stored) == 1
    assert stored[0].model_probability == pytest.approx(0.72)


def test_settlement_is_append_only(repo):
    """Un veredicto sellado no se sobrescribe."""

    record = _freeze(repo, "f1")
    verdict = LegSettlementRecord(
        leg_key=record.leg_key, fixture_key="f1", hit=True, settled_at=NOW)
    assert repo.settle_leg_if_absent(verdict) is True
    assert repo.settle_leg_if_absent(verdict) is False


# --- combinaciones de referencia -----------------------------------------

def test_reference_parlays_respect_one_leg_per_match(repo):
    """Ninguna combinación puede tomar dos piernas del mismo partido."""

    legs = [_freeze(repo, f"f{i}", kickoff=KICKOFF + timedelta(minutes=i))
            for i in range(4)]
    legs.append(freeze_from_leg(
        _leg_payload(key="away_shots_over_10_5"), _fixture("f0"), "a" * 64, NOW))
    parlays = reference_parlays(legs, {"2": 0.97}, "a" * 64, NOW,
                                leg_counts=(2,))
    by_key = {row.leg_key: row for row in legs}
    for parlay in parlays:
        fixtures = [by_key[key].fixture_key for key in parlay.leg_keys]
        assert len(fixtures) == len(set(fixtures))


def test_reference_parlays_are_deterministic_and_disjoint(repo):
    """El mismo conjunto congelado produce siempre las mismas combinaciones."""

    legs = [_freeze(repo, f"f{i}", probability=0.6 + i / 100,
                    kickoff=KICKOFF + timedelta(minutes=i)) for i in range(6)]
    first = reference_parlays(legs, {}, "a" * 64, NOW, leg_counts=(3,))
    second = reference_parlays(list(reversed(legs)), {}, "a" * 64, NOW,
                               leg_counts=(3,))
    assert [p.parlay_key for p in first] == [p.parlay_key for p in second]
    used = [key for parlay in first for key in parlay.leg_keys]
    assert len(used) == len(set(used)), "los bloques deben ser disjuntos"


def test_declared_probability_is_the_product_of_its_legs(repo):
    """La probabilidad declarada del parlay es el producto de sus piernas."""

    legs = [_freeze(repo, "f1", 0.8, KICKOFF),
            _freeze(repo, "f2", 0.7, KICKOFF + timedelta(minutes=1))]
    parlay = reference_parlays(legs, {"2": 0.97}, "a" * 64, NOW,
                               leg_counts=(2,))[0]
    assert parlay.declared_probability == pytest.approx(0.56)
    assert parlay.declared_delivery_ratio == pytest.approx(0.97)
    assert parlay.earliest_kickoff_ts == KICKOFF


def test_incomplete_block_is_not_frozen(repo):
    """Con menos piernas de las que pide el tamaño no se congela nada."""

    legs = [_freeze(repo, "f1")]
    assert reference_parlays(legs, {}, "a" * 64, NOW, leg_counts=(3,)) == []


# --- liquidación ----------------------------------------------------------

def test_resolve_leg_uses_observed_count(repo):
    """La pierna se liquida contra el conteo observado del periodo."""

    record = _freeze(repo, "f1")
    statistics = {"periods": {"home": {"total": {"corners": 6}}}}
    verdict = resolve_leg(record, statistics, NOW)
    assert verdict is not None and verdict.hit is True
    assert verdict.observed_value == {"observed": 6.0, "line": 4.5}

    statistics = {"periods": {"home": {"total": {"corners": 3}}}}
    assert resolve_leg(record, statistics, NOW).hit is False


def test_resolve_leg_returns_none_without_statistics(repo):
    """Sin estadística utilizable no se inventa un veredicto."""

    record = _freeze(repo, "f1")
    assert resolve_leg(record, {}, NOW) is None
    assert resolve_leg(record, {"periods": {}}, NOW) is None
    assert resolve_leg(record, {"periods": {"home": {"total": {}}}}, NOW) is None


def test_parlay_stays_pending_until_every_leg_resolves(repo):
    """Un parlay con una pierna pendiente no se cierra ni por mayoría."""

    legs = [_freeze(repo, "f1", 0.8, KICKOFF),
            _freeze(repo, "f2", 0.7, KICKOFF + timedelta(minutes=1))]
    parlay = reference_parlays(legs, {}, "a" * 64, NOW, leg_counts=(2,))[0]
    repo.freeze_parlay_if_absent(parlay)

    repo.settle_leg_if_absent(LegSettlementRecord(
        leg_key=legs[0].leg_key, fixture_key="f1", hit=False, settled_at=NOW))
    assert settle_ready_parlays(repo, NOW) == {"settled": 0, "still_pending": 1}

    repo.settle_leg_if_absent(LegSettlementRecord(
        leg_key=legs[1].leg_key, fixture_key="f2", hit=True, settled_at=NOW))
    assert settle_ready_parlays(repo, NOW) == {"settled": 1, "still_pending": 0}
    verdict = repo.parlay_settlements()[0]
    assert verdict.hit is False and verdict.legs_hit == 1


def test_parlay_hits_only_when_all_legs_hit(repo):
    """El parlay se cumple sólo si todas sus piernas se cumplen."""

    legs = [_freeze(repo, "f1", 0.8, KICKOFF),
            _freeze(repo, "f2", 0.7, KICKOFF + timedelta(minutes=1))]
    parlay = reference_parlays(legs, {}, "a" * 64, NOW, leg_counts=(2,))[0]
    repo.freeze_parlay_if_absent(parlay)
    for leg in legs:
        repo.settle_leg_if_absent(LegSettlementRecord(
            leg_key=leg.leg_key, fixture_key=leg.fixture_key, hit=True,
            settled_at=NOW))
    settle_ready_parlays(repo, NOW)
    verdict = repo.parlay_settlements()[0]
    assert verdict.hit is True and verdict.legs_hit == 2


def test_unsettled_legs_only_returns_started_matches(repo):
    """Una pierna cuyo partido no arrancó no entra a liquidación."""

    _freeze(repo, "f1", kickoff=NOW + timedelta(hours=3))
    assert repo.unsettled_legs(NOW) == []
    assert len(repo.unsettled_legs(NOW + timedelta(hours=4))) == 1


# --- reporte prospectivo --------------------------------------------------

def test_delivery_hides_ratio_below_minimum_sample(repo):
    """Con muestra chica no se publica un ratio que sería ruido."""

    legs = [_freeze(repo, f"f{i}", 0.8, KICKOFF + timedelta(minutes=i))
            for i in range(2)]
    parlay = reference_parlays(legs, {}, "a" * 64, NOW, leg_counts=(2,))[0]
    repo.freeze_parlay_if_absent(parlay)
    for leg in legs:
        repo.settle_leg_if_absent(LegSettlementRecord(
            leg_key=leg.leg_key, fixture_key=leg.fixture_key, hit=True,
            settled_at=NOW))
    settle_ready_parlays(repo, NOW)
    report = prospective_delivery(
        repo.frozen_parlays(), repo.parlay_settlements())
    block = report["by_legs"]["2"]
    assert block["sufficient_sample"] is False
    assert "delivery_ratio" not in block
    assert block["missing_for_ratio"] == 29
    assert report["status"] == "experimental_shadow_not_promoted"


def test_delivery_reports_ratio_with_enough_sample():
    """Con muestra suficiente se publica entrega dividida entre promesa."""

    from src.parlay_settlement import ParlayFreezeRecord, ParlaySettlementRecord
    frozen, settled = [], []
    for index in range(40):
        key = f"p{index}"
        frozen.append(ParlayFreezeRecord(
            parlay_key=key, leg_count=2, leg_keys=["a", "b"],
            declared_probability=0.5, declared_delivery_ratio=0.97,
            earliest_kickoff_ts=KICKOFF, criteria_sha256="a" * 64,
            frozen_at=NOW))
        settled.append(ParlaySettlementRecord(
            parlay_key=key, hit=index < 18, legs_hit=2 if index < 18 else 1,
            leg_count=2, settled_at=NOW))
    report = prospective_delivery(frozen, settled)
    block = report["by_legs"]["2"]
    assert block["sufficient_sample"] is True
    assert block["observed"] == pytest.approx(0.45)
    assert block["delivery_ratio"] == pytest.approx(0.9)


def test_delivery_ignores_parlays_without_verdict(repo):
    """Un parlay sin liquidar no entra al cómputo del ratio."""

    legs = [_freeze(repo, f"f{i}", 0.8, KICKOFF + timedelta(minutes=i))
            for i in range(2)]
    parlay = reference_parlays(legs, {}, "a" * 64, NOW, leg_counts=(2,))[0]
    repo.freeze_parlay_if_absent(parlay)
    report = prospective_delivery(
        repo.frozen_parlays(), repo.parlay_settlements())
    assert report["by_legs"] == {}
    assert report["frozen_parlays"] == 1
    assert report["settled_parlays"] == 0


# --- congelación desde el gate -------------------------------------------

def test_freeze_from_leg_normalizes_naive_kickoff():
    """Un kickoff sin zona se interpreta como UTC, no como local."""

    fixture = _fixture("f1")
    fixture["kickoff_ts"] = "2026-08-21T18:00:00"
    record = freeze_from_leg(_leg_payload(), fixture, "a" * 64, NOW)
    assert record.kickoff_ts.tzinfo is not None
    assert record.kickoff_ts == datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)


def test_freeze_from_leg_accepts_zulu_suffix():
    """El sufijo `Z` se acepta igual que un offset explícito."""

    fixture = _fixture("f1")
    fixture["kickoff_ts"] = "2026-08-21T18:00:00Z"
    record = freeze_from_leg(_leg_payload(), fixture, "a" * 64, NOW)
    assert record.kickoff_ts == datetime(2026, 8, 21, 18, 0, tzinfo=timezone.utc)


# --- lecciones heredadas de Fase 123 --------------------------------------

def test_every_eligible_market_of_the_gate_can_be_settled():
    """Ninguna pierna elegible puede ser imposible de liquidar.

    `shots_on_target_total_over_7_5` está en `APPROVED_MARKETS` con lado
    `total`, y durante meses ese lado fue irresoluble: 1,645 picks de Fase 123
    congelados y cero liquidados. Hoy falla el gate por otras razones, pero si
    algún día lo pasara, sus piernas tienen que poder liquidarse. Esta prueba
    ata el gate al resolutor para que no puedan divergir.
    """

    import json
    from pathlib import Path

    from src.settlement_store import observed_team_count
    from src.team_count_market_runtime import MARKET_METADATA

    root = Path(__file__).resolve().parents[1]
    criteria = root / "artifacts/phase_135_parlay_eligibility/criteria.json"
    if not criteria.exists():
        pytest.skip("artefacto de Fase 135 ausente")
    payload = json.loads(criteria.read_text(encoding="utf-8"))

    def side(value: int) -> dict:
        metrics = ("corners", "shots", "shots_on_target", "yellow_cards")
        return {
            "first_half": {m: value for m in metrics},
            "second_half": {m: value for m in metrics},
            "total": {m: value * 2 for m in metrics},
        }

    statistics = {"home": side(6), "away": side(7)}
    unresolved = []
    for key in payload["eligible_markets"]:
        metric, team_side, period, _line, _source = MARKET_METADATA[key]
        if observed_team_count(statistics, team_side, period, metric) is None:
            unresolved.append(key)
    assert unresolved == [], (
        f"mercados elegibles que nunca liquidarían: {unresolved}")


def test_settle_window_prioritises_recent_legs_over_dead_ones(repo):
    """Una pierna muerta no puede bloquear la cola, como pasó en Fase 123."""

    from src.parlay_settlement import SETTLE_WINDOW

    for index in range(SETTLE_WINDOW):
        _freeze(repo, f"dead{index}", kickoff=KICKOFF)
    reachable = _freeze(repo, "fresh", kickoff=KICKOFF + timedelta(hours=6))

    window = repo.unsettled_legs(KICKOFF + timedelta(days=1))

    assert len(window) == SETTLE_WINDOW, "la ventana debe estar acotada"
    assert reachable.leg_key in {row.leg_key for row in window}, (
        "la pierna más reciente quedó fuera: las muertas bloquean la cola")


# --- ciclo prospectivo compartido -----------------------------------------

class _Gateway:
    """Gateway falso con la forma exacta que devuelve `/v1/parlay/menu`."""

    def __init__(self, matches, statistics=None):
        self._matches = matches
        self._statistics = statistics or {}

    def parlay_menu(self, date=None, limit=30):
        return {"status": "ok", "matches": self._matches, "legs": 99}

    def explorer_statistics(self, league_slug, match_id, competition_id):
        return self._statistics


class _View:
    """Vista de elegibilidad falsa, ya validada."""

    def __init__(self, matches):
        self._matches = matches

    def available(self):
        return True

    def menu(self, predictions):
        return {"status": "available", "criteria_sha256": "a" * 64,
                "matches": predictions, "legs": 99}

    def _load(self):
        return {"delivery": {"2": {"ratio": 0.97}}}


def _menu_match(name, kickoff):
    return {**_fixture(name, kickoff), "legs": [_leg_payload()]}


def test_cycle_freezes_only_matches_that_have_not_started(repo):
    """Congelar después del kickoff destruiría la causalidad de la medición."""

    from src.parlay_settlement import run_prospective_cycle

    future = datetime.now(timezone.utc) + timedelta(hours=5)
    past = datetime.now(timezone.utc) - timedelta(hours=5)
    matches = [_menu_match("f1", future), _menu_match("f2", past)]
    view = _View(matches)

    counts = run_prospective_cycle(_Gateway(matches), view, repo, None)

    assert counts["freeze"]["frozen_legs"] == 1
    assert counts["freeze"]["skipped_started"] == 1


def test_cycle_is_idempotent_within_the_same_day(repo):
    """Repetir el ciclo no duplica piernas ni parlays."""

    from src.parlay_settlement import run_prospective_cycle

    future = datetime.now(timezone.utc) + timedelta(hours=5)
    matches = [_menu_match(f"f{i}", future + timedelta(minutes=i))
               for i in range(2)]
    view = _View(matches)
    gateway = _Gateway(matches)

    first = run_prospective_cycle(gateway, view, repo, None)
    second = run_prospective_cycle(gateway, view, repo, None)

    assert first["freeze"]["frozen_legs"] == 2
    assert first["freeze"]["frozen_parlays"] == 1
    assert second["freeze"]["frozen_legs"] == 0
    assert second["freeze"]["frozen_parlays"] == 0


def test_cycle_without_settlement_store_still_freezes(repo):
    """Sin `DATABASE_URL` se congela igual y no se liquida: degradación segura."""

    from src.parlay_settlement import run_prospective_cycle

    future = datetime.now(timezone.utc) + timedelta(hours=5)
    matches = [_menu_match("f1", future)]
    counts = run_prospective_cycle(_Gateway(matches), _View(matches), repo, None)

    assert counts["freeze"]["frozen_legs"] == 1
    assert counts["settle"]["settled_legs"] == 0
