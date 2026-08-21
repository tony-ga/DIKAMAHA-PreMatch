"""Pruebas del gate de elegibilidad de parlays (Fase 135)."""
from __future__ import annotations

import json
import hashlib
from pathlib import Path

import pytest

from src.parlay_eligibility_v1 import (
    EXPECTED_VERSION,
    ParlayEligibilityError,
    ParlayEligibilityView,
)

ROOT = Path(__file__).resolve().parents[1]
CRITERIA = ROOT / "artifacts/phase_135_parlay_eligibility/criteria.json"


def _seal(directory: Path, payload: dict) -> Path:
    """Escribe un artefacto y su manifiesto de hash coherente."""

    path = directory / "criteria.json"
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    (directory / "hashes.json").write_text(json.dumps({
        "criteria.json": hashlib.sha256(path.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return path


def _payload(**overrides) -> dict:
    """Artefacto mínimo válido del gate."""

    base = {
        "version": EXPECTED_VERSION,
        "status": "experimental_shadow_not_promoted",
        "eligible_markets": {"home_corners_over_4_5": {"threshold": 0.6}},
        "structural_rules": {
            "min_legs": 2, "max_legs": 5, "max_legs_per_match": 1},
        "out_of_sample_simulation": {"by_legs": {
            "2": {"declared": 0.5, "observed": 0.475,
                  "delivery_ratio": 0.95, "parlays": 6000}}},
    }
    base.update(overrides)
    return base


def _prediction(fixture: str, key: str, probability: float) -> dict:
    """Predicción pre-match con un mercado de equipo."""

    return {
        "fixture_key": fixture, "league_slug": "esp.1",
        "experimental_team_markets": {"user_market_view": [{
            "key": key, "metric": "corners", "team_side": "home",
            "period": "full_match", "line": 4.5,
            "probability": probability, "baseline_probability": 0.55,
            "source_model": "phase84a_team_count",
        }]},
    }


def _leg(fixture: str, key: str = "home_corners_over_4_5",
         probability: float = 0.72) -> dict:
    """Pierna ya elegible, tal como la devuelve `legs()` más su fixture."""

    return {"key": key, "probability": probability, "fixture_key": fixture}


# --- artefacto real -------------------------------------------------------

@pytest.mark.skipif(not CRITERIA.exists(), reason="artefacto de Fase 135 ausente")
def test_sealed_artifact_loads_and_declares_shadow():
    """El artefacto sellado real carga y no se presenta como promovido."""

    view = ParlayEligibilityView()
    assert view.available()
    payload = json.loads(CRITERIA.read_text(encoding="utf-8"))
    assert payload["status"] == "experimental_shadow_not_promoted"
    assert payload["gate"]["frozen"] is True
    assert payload["eligible_markets"], "el gate no puede quedar vacío"


@pytest.mark.skipif(not CRITERIA.exists(), reason="artefacto de Fase 135 ausente")
def test_excluded_markets_never_become_legs():
    """Los mercados que el análisis descartó no pueden entrar al menú.

    Protege el hallazgo central: «Ambos marcan» declara 0.88 y entrega 0.51,
    así que ordenarlo por probabilidad lo pondría primero.
    """

    view = ParlayEligibilityView()
    for excluded in ("btts", "over_2_5", "1x2",
                     "home_corners_second_half_over_2_5"):
        legs = view.legs(_prediction("f1", excluded, 0.95))
        assert legs == [], f"{excluded} no debe ser elegible"


# --- selección ------------------------------------------------------------

def test_leg_below_threshold_is_rejected(tmp_path):
    """Un mercado elegible por debajo de su umbral no entra."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    assert view.legs(_prediction("f1", "home_corners_over_4_5", 0.59)) == []
    assert len(view.legs(_prediction("f1", "home_corners_over_4_5", 0.61))) == 1


def test_missing_team_market_block_degrades_to_empty(tmp_path):
    """Una predicción sin cobertura de mercados de equipo no revienta."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    assert view.legs({"fixture_key": "f1"}) == []
    assert view.legs({"experimental_team_markets": {}}) == []
    assert view.legs({"experimental_team_markets": {"user_market_view": None}}) == []


def test_malformed_row_is_skipped_not_fatal(tmp_path):
    """Una fila corrupta se salta sin tumbar el resto del menú."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    prediction = _prediction("f1", "home_corners_over_4_5", 0.8)
    prediction["experimental_team_markets"]["user_market_view"].extend([
        {"key": "home_corners_over_4_5"},                     # sin probabilidad
        {"key": "home_corners_over_4_5", "probability": "x"},  # no numérica
        {"key": "home_corners_over_4_5", "probability": 1.4},  # fuera de rango
        "no soy un dict",
    ])
    assert len(view.legs(prediction)) == 1


# --- reglas estructurales -------------------------------------------------

def test_two_legs_from_same_match_are_refused(tmp_path):
    """La regla que impide multiplicar bajo independencia falsa."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    with pytest.raises(ParlayEligibilityError, match="same_match"):
        view.build([_leg("same"), _leg("same")])


def test_leg_without_fixture_fails_closed(tmp_path):
    """Sin `fixture_key` no se puede comprobar la regla: falla cerrado."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    leg = _leg("f1")
    leg.pop("fixture_key")
    with pytest.raises(ParlayEligibilityError, match="missing_fixture"):
        view.build([leg, _leg("f2")])


def test_leg_count_bounds_are_enforced(tmp_path):
    """El techo de piernas es regla, no sugerencia."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    with pytest.raises(ParlayEligibilityError, match="below_minimum"):
        view.build([_leg("f1")])
    with pytest.raises(ParlayEligibilityError, match="above_maximum"):
        view.build([_leg(f"f{i}") for i in range(6)])


def test_non_eligible_market_cannot_be_combined(tmp_path):
    """No basta con pasar una pierna a mano: `build` revalida el gate."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    with pytest.raises(ParlayEligibilityError, match="not_eligible"):
        view.build([_leg("f1", key="btts", probability=0.99), _leg("f2")])


def test_build_revalidates_threshold(tmp_path):
    """Una pierna manipulada por debajo del umbral se rechaza en `build`."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    with pytest.raises(ParlayEligibilityError, match="below_threshold"):
        view.build([_leg("f1", probability=0.4), _leg("f2")])


# --- combinación ----------------------------------------------------------

def test_joint_probability_and_delivery_ratio(tmp_path):
    """Multiplica y publica el ratio de entrega junto a la probabilidad."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    result = view.build([_leg("f1", probability=0.8),
                         _leg("f2", probability=0.7)])
    assert result["joint_probability"] == pytest.approx(0.56)
    assert result["delivery_ratio"] == pytest.approx(0.95)
    assert result["expected_delivery"] == pytest.approx(0.56 * 0.95)
    assert result["status"] == "experimental_shadow_not_promoted"
    assert result["evidence"]["source"] == "out_of_sample"


def test_delivery_ratio_absent_when_no_evidence(tmp_path):
    """Sin evidencia para ese número de piernas no se inventa un ratio."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    result = view.build([_leg(f"f{i}") for i in range(3)])
    assert result["delivery_ratio"] is None
    assert result["expected_delivery"] is None
    assert result["evidence"] is None


# --- degradación fail-closed ---------------------------------------------

def test_missing_artifact_reports_unavailable(tmp_path):
    """Sin artefacto el menú queda vacío y lo declara."""

    view = ParlayEligibilityView(tmp_path / "ausente.json")
    assert view.available() is False
    assert view.legs(_prediction("f1", "home_corners_over_4_5", 0.9)) == []
    assert view.menu([])["status"] == "unavailable"


def test_tampered_artifact_is_refused(tmp_path):
    """Un artefacto alterado tras sellarse no se usa."""

    path = _seal(tmp_path, _payload())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["eligible_markets"]["btts"] = {"threshold": 0.5}
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    assert ParlayEligibilityView(path).available() is False


def test_version_mismatch_is_refused(tmp_path):
    """Un artefacto de otra versión falla cerrado."""

    view = ParlayEligibilityView(
        _seal(tmp_path, _payload(version="phase135_parlay_eligibility_v2")))
    assert view.available() is False


def test_out_of_range_threshold_is_refused(tmp_path):
    """Un umbral imposible invalida el gate entero."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload(
        eligible_markets={"home_corners_over_4_5": {"threshold": 1.4}})))
    assert view.available() is False


def test_inverted_leg_bounds_are_refused(tmp_path):
    """Reglas estructurales incoherentes fallan cerrado."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload(
        structural_rules={"min_legs": 5, "max_legs": 2,
                          "max_legs_per_match": 1})))
    assert view.available() is False


# --- menú -----------------------------------------------------------------

def test_menu_groups_by_match_and_counts_legs(tmp_path):
    """El menú agrupa por partido y omite los que no aportan piernas."""

    view = ParlayEligibilityView(_seal(tmp_path, _payload()))
    menu = view.menu([
        _prediction("f1", "home_corners_over_4_5", 0.8),
        _prediction("f2", "home_corners_over_4_5", 0.5),   # bajo umbral
        _prediction("f3", "btts", 0.99),                    # no elegible
        _prediction("f4", "home_corners_over_4_5", 0.7),
    ])
    assert menu["status"] == "available"
    assert menu["label"] == "experimental_shadow_not_promoted"
    assert [row["fixture_key"] for row in menu["matches"]] == ["f1", "f4"]
    assert menu["legs"] == 2
    assert menu["max_legs_per_match"] == 1
