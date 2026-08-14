"""Pruebas del menú de mayor probabilidad (Fase 122 + Etapa 4).

Dos fuentes independientes, cada una con su propia degradación segura:

- Mercados de gol (1X2, Over 2.5, Ambos marcan): sólo se exponen si el par
  (mercado, tramo de confianza) supera el gate sellado de
  `artifacts/phase_122_confidence_reliability`.
- Mercados de equipo (córners, tiros, tiros a puerta, tarjetas): vienen de
  `audited_market_ladder_view` vía `src/ladder_pick_selection.py`, ya
  auditados en origen -no pasan por ningún gate de este archivo-. Siempre se
  expone al menos una línea por cada mercado que la escalera cubra.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from src.dikamaha_service import ServiceConfig, create_app
from src.high_probability_view import (
    ELIGIBILITY, EXPECTED_VERSION, HighProbabilityView,
)

ROOT = Path(__file__).resolve().parents[1]


def _cell(market: str, low: float, high: float, rate: float, **changes: Any) -> dict[str, Any]:
    """Construye una celda apta sintética del gate de gol."""

    payload = {
        "market": market, "bucket_low": low, "bucket_high": high,
        "observed_rate": rate, "observed_ci95": [rate - 0.05, rate + 0.05],
        "picks": 150, "mean_predicted": low + 0.02,
        "calibration_gap": 0.01, "skill_vs_naive": 0.03,
        "edge_source": "model_edge", "non_degraded_rate": 1.0,
        "holdout_picks": 30, "holdout_observed_rate": rate,
        "holdout_consistent": True,
    }
    payload.update(changes)
    return payload


def _artifact(tmp_path: Path, cells: list[dict[str, Any]], **changes: Any) -> Path:
    """Sella un artefacto de elegibilidad de gol sintético con su manifiesto."""

    payload: dict[str, Any] = {
        "version": EXPECTED_VERSION,
        "status": "experimental_shadow_not_promoted",
        "buckets": [[0.55, 0.65], [0.65, 0.75], [0.75, 1.0001]],
        "eligible_cells": cells,
    }
    payload.update(changes)
    path = tmp_path / "eligibility.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    _reseal(path)
    return path


def _reseal(path: Path) -> None:
    """Regenera `hashes.json` para el artefacto sintético."""

    digest = hashlib.sha256(
        path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()
    (path.parent / "hashes.json").write_text(
        json.dumps({path.name: digest}), encoding="utf-8")


def _ladder_group(
    key: str, metric: str, side: str, period: str, line: float,
    probability: float, reliability: str = "model_edge",
    observed: float | None = None, sample: int = 500,
) -> dict[str, Any]:
    """Construye un grupo sintético de la escalera auditada, una sola línea."""

    return {
        "key": key, "metric": metric, "team_side": side, "period": period,
        "expected_count": 5.0,
        "lines": [{
            "line": line, "over_probability": probability,
            "under_probability": 1.0 - probability, "reliability": reliability,
            "observed_rate_historical": observed if observed is not None else probability,
            "sample_size": sample,
        }],
    }


def _prediction(
    ladder_groups: list[dict[str, Any]] | None = None, **goals: float,
) -> dict[str, Any]:
    """Construye una predicción pre-match con mercados de gol y/o escalera."""

    return {
        "probability_home": goals.get("home", 0.34),
        "probability_draw": goals.get("draw", 0.33),
        "probability_away": goals.get("away", 0.33),
        "probability_over_2_5": goals.get("over_2_5", 0.50),
        "probability_btts": goals.get("btts", 0.50),
        "experimental_team_markets": {
            "audited_market_ladder_view": ladder_groups or [],
        },
    }


def _one_pick_prediction() -> dict[str, Any]:
    """Predicción mínima con exactamente un pick de equipo apto.

    Conveniencia para las pruebas de infraestructura del endpoint
    (concurrencia, caché, presupuesto de tiempo, `limit`), a las que no les
    importa el contenido del pick, sólo que exista exactamente uno.
    """

    return _prediction(ladder_groups=[
        _ladder_group("home_corners", "corners", "home", "full_match", 4.5, 0.70)])


# --------------------------------------------------------------------------
# Mercados de equipo — escalera auditada (Etapa 4)
# --------------------------------------------------------------------------

def test_team_market_picks_do_not_depend_on_the_goal_eligibility_artifact(
    tmp_path: Path,
) -> None:
    """Los mercados de equipo nunca pasan por `eligibility.json`.

    Se construye la vista apuntando a un artefacto de gol inexistente -el
    gate de gol queda indisponible- y aun así el pick de equipo aparece.
    """

    view = HighProbabilityView(tmp_path / "no_existe.json")
    assert view.available() is False
    picks = view.picks(_one_pick_prediction())
    assert len(picks) == 1
    assert picks[0]["market"] == "home_corners"


def test_team_markets_are_not_capped_at_three() -> None:
    """A diferencia de los mercados de gol, la escalera no tiene tope de 3.

    El pedido es "siempre al menos una estadística por cada mercado", así
    que un partido con cobertura completa puede exponer todos sus grupos.
    """

    groups = [
        _ladder_group(f"home_corners_{index}", "corners", "home", "full_match",
                      4.5 + index, 0.70)
        for index in range(6)
    ]
    view = HighProbabilityView()
    picks = view.picks(_prediction(ladder_groups=groups))
    assert len(picks) == 6


def test_obvious_team_market_lines_are_dropped_not_published() -> None:
    """Una línea con probabilidad cercana a la certeza no llega al menú.

    Regla principal declarada por el usuario y congelada en DEC-182: antes
    vacío que obvio. `away_corners` sólo ofrece "más de 0.5" al 98%, así que
    ese mercado desaparece del menú en vez de ocupar espacio con una cifra
    que acierta casi siempre sin informar nada.
    """

    groups = [
        _ladder_group("home_corners", "corners", "home", "full_match", 4.5, 0.70),
        _ladder_group("away_corners", "corners", "away", "full_match", 0.5, 0.98),
    ]
    view = HighProbabilityView()
    picks = {pick["market"]: pick for pick in view.picks(_prediction(ladder_groups=groups))}
    assert set(picks) == {"home_corners"}
    assert picks["home_corners"]["selection"] == "target_band"


def test_team_market_edge_source_travels_end_to_end() -> None:
    """El origen de la ventaja llega sin alterarse desde la escalera al pick."""

    groups = [_ladder_group(
        "home_shots_on_target", "shots_on_target", "home", "full_match", 7.5,
        0.68, reliability="base_rate_driven", observed=0.71, sample=1204)]
    picks = HighProbabilityView().picks(_prediction(ladder_groups=groups))
    assert picks[0]["edge_source"] == "base_rate_driven"
    assert picks[0]["sample_size"] == 1204
    assert picks[0]["observed_rate"] == pytest.approx(0.71)


def test_goal_market_malformed_probability_does_not_affect_team_picks() -> None:
    """Un mercado de gol corrupto vacía sólo los picks de gol -no los de equipo."""

    groups = [_ladder_group("home_corners", "corners", "home", "full_match", 4.5, 0.70)]
    prediction = _prediction(ladder_groups=groups, over_2_5=0.50)
    prediction["probability_over_2_5"] = 1.4

    picks = HighProbabilityView().picks(prediction)
    assert len(picks) == 1
    assert picks[0]["market"] == "home_corners"


def test_prediction_without_ladder_block_yields_no_team_picks() -> None:
    """Sin `audited_market_ladder_view`, no hay picks de equipo -sin excepción."""

    prediction = _prediction()
    del prediction["experimental_team_markets"]["audited_market_ladder_view"]
    assert HighProbabilityView().picks(prediction) == []


# --------------------------------------------------------------------------
# Artefacto real sellado (mercados de gol)
# --------------------------------------------------------------------------

def test_sealed_artifact_loads_and_matches_backtest() -> None:
    """El artefacto real de Fase 122 carga y conserva sus nueve celdas."""

    view = HighProbabilityView()
    assert view.available() is True
    provenance = view.provenance()
    assert provenance["version"] == EXPECTED_VERSION
    assert provenance["eligible_cells"] == 9
    assert len(provenance["eligibility_sha256"]) == 64
    assert provenance["status"] == "experimental_shadow_not_promoted"
    assert provenance["goal_markets_gate_available"] is True
    assert len(provenance["team_markets_sha256"]) == 64


def test_official_goal_markets_never_surface() -> None:
    """1X2, Más de 2.5 y Ambos marcan no aparecen ni con confianza extrema.

    Es el hallazgo central del backtest: ninguno superó el gate en ningún
    tramo, de modo que su confianza alta no debe llegar nunca al usuario.
    Fuera de alcance de la Etapa 4 -sigue gobernado por el mismo gate-.
    """

    view = HighProbabilityView()
    prediction = _prediction(home=0.97, draw=0.02, away=0.01,
                             over_2_5=0.96, btts=0.94)
    assert view.picks(prediction) == []


# --------------------------------------------------------------------------
# Selección de tramo y dirección (mercados de gol)
# --------------------------------------------------------------------------

def test_under_direction_uses_complementary_confidence(tmp_path: Path) -> None:
    """Una probabilidad de 0.28 es un pick `under` con confianza 0.72."""

    path = _artifact(tmp_path, [_cell("over_2_5", 0.65, 0.75, 0.768)])
    picks = HighProbabilityView(path).picks(_prediction(over_2_5=0.28))
    assert len(picks) == 1
    assert picks[0]["direction"] == "under"
    assert picks[0]["model_probability"] == pytest.approx(0.72)
    assert picks[0]["observed_rate"] == pytest.approx(0.768)


def test_confidence_outside_every_bucket_is_dropped(tmp_path: Path) -> None:
    """Una confianza fuera de los tramos aptos no produce pick."""

    path = _artifact(tmp_path, [_cell("over_2_5", 0.65, 0.75, 0.893)])
    view = HighProbabilityView(path)
    assert view.picks(_prediction(over_2_5=0.60)) == []
    assert view.picks(_prediction(over_2_5=0.80)) == []
    assert len(view.picks(_prediction(over_2_5=0.70))) == 1


def test_bucket_upper_bound_is_exclusive(tmp_path: Path) -> None:
    """El límite superior del tramo no pertenece al tramo."""

    path = _artifact(tmp_path, [_cell("over_2_5", 0.65, 0.75, 0.893)])
    assert HighProbabilityView(path).picks(_prediction(over_2_5=0.75)) == []


# --------------------------------------------------------------------------
# Política de exposición (mercados de gol)
# --------------------------------------------------------------------------

def test_correlated_goal_markets_collapse_to_the_strongest(tmp_path: Path) -> None:
    """Los tres mercados de gol son un solo componente correlacionado.

    Si hipotéticamente dos superaran el gate a la vez, sólo sobrevive el más
    fuerte -mismo mecanismo que antes protegía las líneas por periodo de un
    mismo mercado de equipo, ahora exclusivo de los mercados de gol-.
    """

    path = _artifact(tmp_path, [
        _cell("over_2_5", 0.65, 0.75, 0.893), _cell("btts", 0.65, 0.75, 0.688)])
    picks = HighProbabilityView(path).picks(
        _prediction(over_2_5=0.70, btts=0.70))
    assert [pick["market"] for pick in picks] == ["over_2_5"]


# --------------------------------------------------------------------------
# Degradación segura
# --------------------------------------------------------------------------

def test_missing_goal_artifact_fails_open_without_affecting_team_markets(
    tmp_path: Path,
) -> None:
    """Sin artefacto de gol no hay picks de gol, pero los de equipo siguen.

    Es la propiedad central de la Etapa 4: las dos fuentes degradan por
    separado.
    """

    view = HighProbabilityView(tmp_path / "ausente.json")
    assert view.available() is False
    assert view.provenance()["status"] == "experimental_shadow_not_promoted"
    assert view.provenance()["goal_markets_gate_available"] is False

    goal_only = _prediction(over_2_5=0.90)
    assert view.picks(goal_only) == []

    with_ladder = _one_pick_prediction()
    picks = view.picks(with_ladder)
    assert len(picks) == 1
    assert picks[0]["market"] == "home_corners"


@pytest.mark.parametrize("changes,cells", [
    ({"version": "otra_version"}, [_cell("over_2_5", 0.65, 0.75, 0.89)]),
    ({}, [_cell("over_2_5", 0.65, 0.75, 1.4)]),
    ({}, [_cell("over_2_5", 0.75, 0.65, 0.89)]),
    ({}, [_cell("over_2_5", 0.65, 0.75, 0.89, picks=0)]),
])
def test_corrupt_goal_artifact_fails_open(
    tmp_path: Path, changes: dict[str, Any], cells: list[dict[str, Any]],
) -> None:
    """Versión distinta o cifras imposibles vacían el gate de gol, no lo falsean."""

    path = _artifact(tmp_path, cells, **changes)
    view = HighProbabilityView(path)
    assert view.available() is False
    assert view.picks(_prediction(over_2_5=0.70)) == []


def test_tampered_goal_artifact_fails_open(tmp_path: Path) -> None:
    """Editar el artefacto de gol sin resellar vacía el gate, no lo altera."""

    path = _artifact(tmp_path, [_cell("over_2_5", 0.65, 0.75, 0.893)])
    assert len(HighProbabilityView(path).picks(_prediction(over_2_5=0.70))) == 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["eligible_cells"].append(_cell("btts", 0.65, 0.75, 0.95))
    path.write_text(json.dumps(payload), encoding="utf-8")

    view = HighProbabilityView(path)
    assert view.available() is False
    assert view.picks(_prediction(over_2_5=0.70, btts=0.70)) == []


def test_missing_hash_manifest_fails_open(tmp_path: Path) -> None:
    """Sin manifiesto de hashes no se sirve ningún pick de gol."""

    path = _artifact(tmp_path, [_cell("over_2_5", 0.65, 0.75, 0.893)])
    (path.parent / "hashes.json").unlink()
    assert HighProbabilityView(path).available() is False


def test_provenance_combines_both_source_hashes() -> None:
    """El hash publicado combina el de gol (sellado) y el de la escalera.

    No es una comparación contra un manifiesto sellado de la escalera -no
    existe uno-: es una cifra de trazabilidad que cambia si cualquiera de
    las dos fuentes cambia.
    """

    sealed_goal = json.loads(
        (ELIGIBILITY.parent / "hashes.json").read_text(encoding="utf-8")
    )["eligibility.json"]
    provenance = HighProbabilityView().provenance()
    expected = hashlib.sha256(
        f"{sealed_goal}|{provenance['team_markets_sha256']}".encode()
    ).hexdigest()
    assert provenance["eligibility_sha256"] == expected


def test_prediction_without_shadow_block_is_safe() -> None:
    """Una predicción sin bloque shadow no rompe la vista."""

    view = HighProbabilityView()
    assert view.picks({"probability_home": 0.5, "probability_draw": 0.3,
                       "probability_away": 0.2}) == []
    assert view.picks({}) == []


def test_goal_probability_out_of_range_fails_open(tmp_path: Path) -> None:
    """Una probabilidad de gol corrupta vacía el menú de gol, no se propaga."""

    path = _artifact(tmp_path, [_cell("over_2_5", 0.65, 0.75, 0.893)])
    prediction = _prediction(over_2_5=0.70)
    prediction["probability_over_2_5"] = 1.4
    assert HighProbabilityView(path).picks(prediction) == []


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def test_endpoint_requires_external_calls() -> None:
    """Sin llamadas externas el catálogo del día no se puede construir."""

    response = TestClient(create_app()).get("/v1/high-probability")
    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "external_calls_disabled"


def test_endpoint_reports_unavailable_only_when_both_sources_are_down(
    monkeypatch: Any,
) -> None:
    """El endpoint sólo corta el barrido si gol Y equipo están indisponibles.

    Antes de la Etapa 4 un solo artefacto gobernaba todo el menú; ahora un
    gate de gol caído no debe vaciar los mercados de equipo -el artefacto de
    la escalera sigue siendo el real del repositorio-, así que el barrido
    real sigue adelante y el estado es `ok` (aquí sin picks porque el
    catálogo del día está vacío), no `unavailable`.
    """

    import src.dikamaha_service as service

    app = create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    app.state.high_probability_view = HighProbabilityView(
        Path("/no/existe/eligibility.json"))
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: ([], []))

    payload = TestClient(app).get("/v1/high-probability").json()
    assert payload["status"] == "ok"
    assert payload["picks"] == []
    assert payload["provenance"]["goal_markets_gate_available"] is False
    assert len(payload["provenance"]["team_markets_sha256"]) == 64


def test_endpoint_reports_unavailable_when_both_sources_are_down(
    monkeypatch: Any,
) -> None:
    """Con las dos fuentes caídas, el endpoint corta antes del barrido real."""

    import src.dikamaha_service as service

    app = create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    app.state.high_probability_view = HighProbabilityView(
        Path("/no/existe/eligibility.json"))
    monkeypatch.setattr(
        "src.high_probability_view.LADDER_RELIABILITY_ARTIFACT",
        Path("/no/existe/ladder_reliability.json"))
    catalog_called = {"count": 0}

    def fail_if_called(payload: Any) -> tuple[list[Any], list[Any]]:
        catalog_called["count"] += 1
        return [], []

    monkeypatch.setattr(service, "_upcoming_catalog", fail_if_called)

    payload = TestClient(app).get("/v1/high-probability").json()
    assert payload["status"] == "unavailable"
    assert payload["reason"] == "high_probability_sources_unavailable"
    assert payload["picks"] == []
    assert catalog_called["count"] == 0


def test_endpoint_groups_by_fixture_instead_of_flattening_globally(
    monkeypatch: Any,
) -> None:
    """Cada partido aporta todos sus mercados, ordenados cronológicamente.

    Regresión real: antes el límite ordenaba todos los picks de todos los
    partidos por tasa observada y cortaba los primeros N, así que un
    partido con un pick muy fuerte podía desplazar los demás mercados de
    otros partidos -el usuario veía "un solo mercado" en vez de la escalera
    completa por partido-.
    """

    import src.dikamaha_service as service

    fixtures = [
        {"match_id": 1, "league_slug": "esp.1", "home_team_id": 10,
         "away_team_id": 11, "kickoff_ts": "2026-08-11T18:00:00+00:00",
         "home_team_name": "A", "away_team_name": "B",
         "home_team_logo": None, "away_team_logo": None},
        {"match_id": 2, "league_slug": "eng.1", "home_team_id": 20,
         "away_team_id": 21, "kickoff_ts": "2026-08-11T20:00:00+00:00",
         "home_team_name": "C", "away_team_name": "D",
         "home_team_logo": None, "away_team_logo": None},
    ]
    predictions = {
        1: _prediction(ladder_groups=[
            _ladder_group("away_shots", "shots", "away", "full_match", 10.5, 0.68,
                          observed=0.70),
            _ladder_group("away_corners", "corners", "away", "full_match", 4.5, 0.62,
                          observed=0.61),
            _ladder_group("home_yellow_cards", "yellow_cards", "home", "full_match", 1.5,
                          0.65, observed=0.66),
        ]),
        2: _prediction(ladder_groups=[_ladder_group(
            "home_corners", "corners", "home", "full_match", 4.5, 0.70,
            observed=0.84)]),
    }

    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: (fixtures, []))

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Devuelve la predicción sintética del fixture."""

        return predictions[int(fixture["match_id"])]

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    payload = TestClient(
        create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    ).get("/v1/high-probability").json()

    assert payload["status"] == "ok"
    assert payload["classification"] == "experimental_shadow_not_promoted"
    assert payload["fixtures_scanned"] == 2
    assert payload["fixtures_with_picks"] == 2
    # Los tres mercados del partido 1 sobreviven -no sólo el más fuerte-,
    # aunque el partido 2 tenga una tasa observada más alta en su único pick.
    match_1_markets = {
        pick["market"] for pick in payload["picks"] if pick["fixture"]["match_id"] == 1}
    assert match_1_markets == {"away_shots", "away_corners", "home_yellow_cards"}
    # Los partidos se ordenan cronológicamente por kickoff, no por tasa
    # observada -partido 1 (18:00) antes que partido 2 (20:00)-.
    assert [pick["fixture"]["match_id"] for pick in payload["picks"][:3]] == [1, 1, 1]
    assert payload["picks"][-1]["fixture"]["match_id"] == 2
    # Dentro de un mismo partido, sí ordena por tasa observada (0.70 > 0.66 > 0.61).
    match_1_picks = [pick for pick in payload["picks"] if pick["fixture"]["match_id"] == 1]
    assert [pick["market"] for pick in match_1_picks] == [
        "away_shots", "home_yellow_cards", "away_corners"]
    assert match_1_picks[0]["observed_rate"] > match_1_picks[-1]["observed_rate"]


def test_endpoint_skips_fixtures_without_prediction(monkeypatch: Any) -> None:
    """Un fixture sin historial causal se cuenta y no aborta la respuesta."""

    import src.dikamaha_service as service
    from src.universal_prematch import PrematchUnavailableError

    fixtures = [
        {"match_id": 1, "league_slug": "esp.1", "home_team_id": 10,
         "away_team_id": 11, "kickoff_ts": "2026-08-11T18:00:00+00:00",
         "home_team_name": "A", "away_team_name": "B",
         "home_team_logo": None, "away_team_logo": None},
        {"match_id": 2, "league_slug": "eng.1", "home_team_id": 20,
         "away_team_id": 21, "kickoff_ts": "2026-08-11T20:00:00+00:00",
         "home_team_name": "C", "away_team_name": "D",
         "home_team_logo": None, "away_team_logo": None},
    ]
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: (fixtures, []))

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Falla el primero y resuelve el segundo."""

        if int(fixture["match_id"]) == 1:
            raise PrematchUnavailableError("history_insufficient")
        return _one_pick_prediction()

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    payload = TestClient(
        create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    ).get("/v1/high-probability").json()

    assert payload["fixtures_without_prediction"] == 1
    assert payload["count"] == 1
    assert payload["picks"][0]["fixture"]["match_id"] == 2


def test_endpoint_limit_is_bounded(monkeypatch: Any) -> None:
    """El parámetro `limit` acota la salida sin perder el total real."""

    import src.dikamaha_service as service

    fixtures = [
        {"match_id": index, "league_slug": "esp.1", "home_team_id": index,
         "away_team_id": index + 100,
         "kickoff_ts": f"2026-08-11T{18 + index:02d}:00:00+00:00",
         "home_team_name": "A", "away_team_name": "B",
         "home_team_logo": None, "away_team_logo": None}
        for index in range(3)
    ]
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: (fixtures, []))

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Devuelve siempre un pick apto."""

        return _one_pick_prediction()

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    payload = TestClient(
        create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    ).get("/v1/high-probability?limit=2").json()

    assert payload["count"] == 2
    assert len(payload["picks"]) == 2
    assert payload["total_candidates"] == 3


def test_endpoint_limit_bounds_fixtures_not_individual_picks(monkeypatch: Any) -> None:
    """Un partido con muchos mercados no se recorta por `limit`.

    Con `limit=1` y un único partido con cinco picks, deben aparecer los
    cinco -el límite decide cuántos partidos entran, no cuántas líneas de
    un mismo partido sobreviven-.
    """

    import src.dikamaha_service as service

    fixtures = [
        {"match_id": 1, "league_slug": "esp.1", "home_team_id": 1,
         "away_team_id": 2, "kickoff_ts": "2026-08-11T18:00:00+00:00",
         "home_team_name": "A", "away_team_name": "B",
         "home_team_logo": None, "away_team_logo": None},
    ]
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: (fixtures, []))

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Un solo partido con cinco mercados de equipo distintos."""

        return _prediction(ladder_groups=[
            _ladder_group(f"home_corners_{index}", "corners", "home", "full_match",
                          4.5 + index, 0.70)
            for index in range(5)
        ])

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    payload = TestClient(
        create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    ).get("/v1/high-probability?limit=1").json()

    assert payload["fixtures_with_picks"] == 1
    assert payload["count"] == 5
    assert len(payload["picks"]) == 5


def test_eligibility_artifact_is_versioned_in_repository() -> None:
    """El artefacto sellado vive en el repositorio y es el que usa el runtime."""

    assert ELIGIBILITY.exists()
    payload = json.loads(ELIGIBILITY.read_text(encoding="utf-8"))
    assert payload["version"] == EXPECTED_VERSION
    assert payload["status"] == "experimental_shadow_not_promoted"
    assert payload["primary_result_frozen_gate_v1_eligible_cells"] == 0
    for cell in payload["eligible_cells"]:
        assert cell["holdout_consistent"] is True
        assert cell["picks"] >= 100
        assert cell["observed_ci95"][0] >= 0.60


# --------------------------------------------------------------------------
# Concurrencia acotada y presupuesto de tiempo
#
# Antes el barrido era un bucle secuencial sin límites: con caché fría (los
# partidos de mañana, que nadie vio todavía) podía encadenar hasta 30
# inferencias completas una tras otra, monopolizando el pool de hilos
# compartido con el resto del servicio. En producción esto se midió tumbando
# hasta /v1/models (un diccionario en memoria, sin E/S) a 10+ segundos.
# --------------------------------------------------------------------------

def _slow_fixtures(count: int) -> list[dict[str, Any]]:
    """Construye un catálogo sintético de `count` fixtures."""

    return [
        {"match_id": index, "league_slug": "esp.1", "home_team_id": index,
         "away_team_id": index + 1000,
         "kickoff_ts": f"2026-08-13T{10 + index % 12:02d}:00:00+00:00",
         "home_team_name": f"Local {index}", "away_team_name": f"Visita {index}",
         "home_team_logo": None, "away_team_logo": None}
        for index in range(count)
    ]


def test_predictions_run_with_bounded_concurrency_not_unbounded(
    monkeypatch: Any,
) -> None:
    """Nunca hay más de HIGH_PROBABILITY_CONCURRENCY inferencias a la vez.

    Sin este límite, 12 fixtures fríos dispararían 12 inferencias
    simultáneas y saturarían el mismo pool de hilos que usa todo lo demás.
    """

    import src.dikamaha_service as service

    fixtures = _slow_fixtures(12)
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: (fixtures, []))

    in_flight = {"current": 0, "max_seen": 0}

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Simula una inferencia real con latencia, contando concurrencia."""

        in_flight["current"] += 1
        in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        await asyncio.sleep(0.15)
        in_flight["current"] -= 1
        return _one_pick_prediction()

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    payload = TestClient(
        create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    ).get("/v1/high-probability").json()

    assert in_flight["max_seen"] <= service.HIGH_PROBABILITY_CONCURRENCY
    assert in_flight["max_seen"] > 1, "debe paralelizar, no ser secuencial"
    assert payload["fixtures_scanned"] == 12


def test_wall_clock_budget_returns_partial_results_instead_of_blocking(
    monkeypatch: Any,
) -> None:
    """Un catálogo grande y lento devuelve lo que alcanzó, no bloquea todo.

    Antes esto no tenía presupuesto de tiempo: fixtures fríos podían sumar su
    latencia completa de forma secuencial. Aquí, completar los 40 fixtures
    sin presupuesto tomaría al menos ~1s (40 fixtures / concurrencia 4 ×
    0.1s); el presupuesto de 0.2s debe cortarlo bastante antes. Los márgenes
    son deliberadamente amplios (5x+) para no ser un test frágil al correr
    junto al resto de la suite bajo carga de CPU compartida.
    """

    import src.dikamaha_service as service

    fixtures = _slow_fixtures(40)
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: (fixtures, []))
    monkeypatch.setattr(
        service, "HIGH_PROBABILITY_WALL_CLOCK_BUDGET_SECONDS", 0.2)

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Simula una inferencia real con latencia constante."""

        await asyncio.sleep(0.1)
        return _one_pick_prediction()

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    # Construir la app queda fuera del cronómetro. Cargar el snapshot y los
    # artefactos cuesta ~1.5s por sí solo, así que medirlo aquí gastaba casi un
    # tercio del presupuesto en algo que este test no está evaluando: el
    # presupuesto acota el barrido de fixtures, no el arranque del servicio.
    # Con la suite completa compitiendo por CPU eso bastaba para hacerlo fallar
    # sin que el mecanismo bajo prueba tuviera nada que ver.
    client = TestClient(
        create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True)))

    started = time.monotonic()
    payload = client.get("/v1/high-probability?limit=50").json()
    elapsed = time.monotonic() - started

    assert payload["status"] == "ok"
    assert payload["fixtures_catalog_size"] == 40
    assert payload["fixtures_scanned"] < 40, (
        "el presupuesto debe cortar el barrido antes de agotar el catálogo")
    assert elapsed < 5.0, "no debe bloquear por la suma de las 40 latencias"


def test_high_probability_catalog_fetch_is_cached(monkeypatch: Any) -> None:
    """Dos llamadas seguidas comparten un único barrido ESPN real."""

    import src.dikamaha_service as service

    calls = {"count": 0}
    fixtures = _slow_fixtures(2)

    def fetch(
        payload: tuple[str, int, str | None],
    ) -> tuple[list[dict[str, Any]], list[str]]:
        """Cuenta cuántas veces se ejecuta el barrido real."""

        calls["count"] += 1
        return fixtures, []

    monkeypatch.setattr(service, "_upcoming_catalog", fetch)

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Predicción sintética instantánea."""

        return _one_pick_prediction()

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    client = TestClient(create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True)))
    first = client.get("/v1/high-probability")
    second = client.get("/v1/high-probability")

    assert first.status_code == second.status_code == 200
    assert calls["count"] == 1
