"""Pruebas del menú de mayor probabilidad de Fase 122.

Cubren la regla que gobierna la fase: una probabilidad alta sólo se expone si
el par (mercado, tramo de confianza) superó el gate, y lo que se publica es la
tasa observada histórica, no la probabilidad del modelo.
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
    """Construye una celda apta sintética."""

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
    """Sella un artefacto de elegibilidad sintético con su manifiesto."""

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


def _prediction(**markets: float) -> dict[str, Any]:
    """Construye una predicción pre-match con los mercados indicados."""

    metadata = {
        "home_corners_over_4_5": ("corners", "home", "full_match", 4.5),
        "away_corners_over_4_5": ("corners", "away", "full_match", 4.5),
        "home_shots_over_10_5": ("shots", "home", "full_match", 10.5),
        "away_shots_over_10_5": ("shots", "away", "full_match", 10.5),
        "home_corners_second_half_over_2_5": ("corners", "home", "second_half", 2.5),
        "home_shots_second_half_over_5_5": ("shots", "home", "second_half", 5.5),
        "away_shots_second_half_over_5_5": ("shots", "away", "second_half", 5.5),
        "shots_on_target_total_over_7_5": ("shots_on_target", "total", "full_match", 7.5),
    }
    view = [
        {"key": key, "metric": metadata[key][0], "team_side": metadata[key][1],
         "period": metadata[key][2], "line": metadata[key][3],
         "probability": value}
        for key, value in markets.items() if key in metadata
    ]
    return {
        "probability_home": markets.get("home", 0.34),
        "probability_draw": markets.get("draw", 0.33),
        "probability_away": markets.get("away", 0.33),
        "probability_over_2_5": markets.get("over_2_5", 0.50),
        "probability_btts": markets.get("btts", 0.50),
        "experimental_team_markets": {"user_market_view": view},
    }


# --------------------------------------------------------------------------
# Artefacto real sellado
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


def test_official_goal_markets_never_surface() -> None:
    """1X2, Más de 2.5 y Ambos marcan no aparecen ni con confianza extrema.

    Es el hallazgo central del backtest: ninguno superó el gate en ningún
    tramo, de modo que su confianza alta no debe llegar nunca al usuario.
    """

    view = HighProbabilityView()
    prediction = _prediction(home=0.97, draw=0.02, away=0.01,
                             over_2_5=0.96, btts=0.94)
    assert view.picks(prediction) == []


def test_sealed_artifact_ranks_by_observed_rate() -> None:
    """Con el artefacto real, el orden lo fija la tasa observada.

    `away_shots_over_10_5` y `away_shots_second_half_over_5_5` miden tiros del
    visitante y son el mismo componente, así que sólo sobrevive el más fuerte:
    el menú no repite la misma señal partida por periodo.
    """

    view = HighProbabilityView()
    picks = view.picks(_prediction(
        home_corners_over_4_5=0.70, away_shots_over_10_5=0.28,
        away_shots_second_half_over_5_5=0.70))
    assert [pick["market"] for pick in picks] == [
        "home_corners_over_4_5", "away_shots_over_10_5"]
    assert picks[0]["observed_rate"] > picks[1]["observed_rate"]
    assert picks[0]["model_probability"] == pytest.approx(0.70)


# --------------------------------------------------------------------------
# Selección de tramo y dirección
# --------------------------------------------------------------------------

def test_under_direction_uses_complementary_confidence(tmp_path: Path) -> None:
    """Una probabilidad de 0.28 es un pick `under` con confianza 0.72."""

    path = _artifact(tmp_path, [
        _cell("away_shots_over_10_5", 0.65, 0.75, 0.768)])
    picks = HighProbabilityView(path).picks(
        _prediction(away_shots_over_10_5=0.28))
    assert len(picks) == 1
    assert picks[0]["direction"] == "under"
    assert picks[0]["model_probability"] == pytest.approx(0.72)
    assert picks[0]["observed_rate"] == pytest.approx(0.768)


def test_confidence_outside_every_bucket_is_dropped(tmp_path: Path) -> None:
    """Una confianza fuera de los tramos aptos no produce pick."""

    path = _artifact(tmp_path, [
        _cell("home_corners_over_4_5", 0.65, 0.75, 0.893)])
    view = HighProbabilityView(path)
    assert view.picks(_prediction(home_corners_over_4_5=0.60)) == []
    assert view.picks(_prediction(home_corners_over_4_5=0.80)) == []
    assert len(view.picks(_prediction(home_corners_over_4_5=0.70))) == 1


def test_bucket_upper_bound_is_exclusive(tmp_path: Path) -> None:
    """El límite superior del tramo no pertenece al tramo."""

    path = _artifact(tmp_path, [
        _cell("home_corners_over_4_5", 0.65, 0.75, 0.893)])
    assert HighProbabilityView(path).picks(
        _prediction(home_corners_over_4_5=0.75)) == []


# --------------------------------------------------------------------------
# Política de exposición
# --------------------------------------------------------------------------

def test_correlated_markets_collapse_to_the_strongest(tmp_path: Path) -> None:
    """Dos líneas de córners del local son una señal, no dos."""

    path = _artifact(tmp_path, [
        _cell("home_corners_over_4_5", 0.65, 0.75, 0.893),
        _cell("home_corners_second_half_over_2_5", 0.65, 0.75, 0.688),
    ])
    picks = HighProbabilityView(path).picks(_prediction(
        home_corners_over_4_5=0.70, home_corners_second_half_over_2_5=0.70))
    assert [pick["market"] for pick in picks] == ["home_corners_over_4_5"]


def test_match_is_capped_at_three_picks(tmp_path: Path) -> None:
    """Ningún partido aporta más de tres picks al menú."""

    path = _artifact(tmp_path, [
        _cell("home_corners_over_4_5", 0.65, 0.75, 0.90),
        _cell("away_corners_over_4_5", 0.65, 0.75, 0.88),
        _cell("home_shots_over_10_5", 0.65, 0.75, 0.86),
        _cell("away_shots_over_10_5", 0.65, 0.75, 0.84),
        _cell("shots_on_target_total_over_7_5", 0.65, 0.75, 0.82),
    ])
    picks = HighProbabilityView(path).picks(_prediction(
        home_corners_over_4_5=0.70, away_corners_over_4_5=0.70,
        home_shots_over_10_5=0.70, away_shots_over_10_5=0.70,
        shots_on_target_total_over_7_5=0.70))
    assert len(picks) == 3
    assert [pick["market"] for pick in picks] == [
        "home_corners_over_4_5", "away_corners_over_4_5",
        "home_shots_over_10_5"]


def test_edge_source_is_preserved(tmp_path: Path) -> None:
    """El origen de la ventaja viaja hasta la interfaz sin alterarse."""

    path = _artifact(tmp_path, [
        _cell("home_shots_second_half_over_5_5", 0.65, 0.75, 0.68,
              edge_source="base_rate_driven", skill_vs_naive=0.0)])
    picks = HighProbabilityView(path).picks(
        _prediction(home_shots_second_half_over_5_5=0.70))
    assert picks[0]["edge_source"] == "base_rate_driven"
    assert picks[0]["skill_vs_naive"] == pytest.approx(0.0)


# --------------------------------------------------------------------------
# Degradación segura
# --------------------------------------------------------------------------

def test_missing_artifact_fails_open(tmp_path: Path) -> None:
    """Sin artefacto no hay picks, y tampoco hay excepción."""

    view = HighProbabilityView(tmp_path / "ausente.json")
    assert view.available() is False
    assert view.picks(_prediction(home_corners_over_4_5=0.70)) == []
    assert view.provenance()["status"] == "unavailable"


@pytest.mark.parametrize("changes,cells", [
    ({"version": "otra_version"}, [_cell("home_corners_over_4_5", 0.65, 0.75, 0.89)]),
    ({}, [_cell("home_corners_over_4_5", 0.65, 0.75, 1.4)]),
    ({}, [_cell("home_corners_over_4_5", 0.75, 0.65, 0.89)]),
    ({}, [_cell("home_corners_over_4_5", 0.65, 0.75, 0.89, picks=0)]),
])
def test_corrupt_artifact_fails_open(
    tmp_path: Path, changes: dict[str, Any], cells: list[dict[str, Any]],
) -> None:
    """Versión distinta o cifras imposibles vacían el menú, no lo falsean."""

    path = _artifact(tmp_path, cells, **changes)
    view = HighProbabilityView(path)
    assert view.available() is False
    assert view.picks(_prediction(home_corners_over_4_5=0.70)) == []


def test_tampered_artifact_fails_open(tmp_path: Path) -> None:
    """Editar el artefacto sin resellar vacía el menú, no lo altera.

    Es el control que impide que una edición manual del archivo cambie qué
    picks ve el usuario sin dejar rastro.
    """

    path = _artifact(tmp_path, [
        _cell("home_corners_over_4_5", 0.65, 0.75, 0.893)])
    assert len(HighProbabilityView(path).picks(
        _prediction(home_corners_over_4_5=0.70))) == 1

    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["eligible_cells"].append(
        _cell("over_2_5", 0.65, 0.75, 0.95))
    path.write_text(json.dumps(payload), encoding="utf-8")

    view = HighProbabilityView(path)
    assert view.available() is False
    assert view.picks(_prediction(
        home_corners_over_4_5=0.70, over_2_5=0.70)) == []


def test_missing_hash_manifest_fails_open(tmp_path: Path) -> None:
    """Sin manifiesto de hashes no se sirve ningún pick."""

    path = _artifact(tmp_path, [
        _cell("home_corners_over_4_5", 0.65, 0.75, 0.893)])
    (path.parent / "hashes.json").unlink()
    assert HighProbabilityView(path).available() is False


def test_provenance_reports_the_sealed_hash() -> None:
    """El hash publicado es el sellado, no uno recalculado en caliente."""

    sealed = json.loads(
        (ELIGIBILITY.parent / "hashes.json").read_text(encoding="utf-8"))
    assert HighProbabilityView().provenance()["eligibility_sha256"] == (
        sealed["eligibility.json"])


def test_prediction_without_shadow_block_is_safe() -> None:
    """Una predicción sin bloque shadow no rompe la vista."""

    view = HighProbabilityView()
    assert view.picks({"probability_home": 0.5, "probability_draw": 0.3,
                       "probability_away": 0.2}) == []
    assert view.picks({}) == []


def test_probability_out_of_range_fails_open(tmp_path: Path) -> None:
    """Una probabilidad corrupta vacía el menú en vez de propagarse."""

    path = _artifact(tmp_path, [
        _cell("home_corners_over_4_5", 0.65, 0.75, 0.893)])
    prediction = _prediction(home_corners_over_4_5=0.70)
    prediction["experimental_team_markets"]["user_market_view"][0][
        "probability"] = 1.4
    assert HighProbabilityView(path).picks(prediction) == []


# --------------------------------------------------------------------------
# API
# --------------------------------------------------------------------------

def test_endpoint_requires_external_calls() -> None:
    """Sin llamadas externas el catálogo del día no se puede construir."""

    response = TestClient(create_app()).get("/v1/high-probability")
    assert response.status_code == 422
    assert response.json()["detail"]["message"] == "external_calls_disabled"


def test_endpoint_reports_unavailable_gate(monkeypatch: Any, tmp_path: Path) -> None:
    """Sin artefacto el endpoint responde vacío y explícito, no 500."""

    app = create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    app.state.high_probability_view = HighProbabilityView(
        tmp_path / "ausente.json")
    payload = TestClient(app).get("/v1/high-probability").json()
    assert payload["status"] == "unavailable"
    assert payload["picks"] == []
    assert payload["reason"] == "phase122_eligibility_unavailable"


def test_endpoint_ranks_across_matches(monkeypatch: Any) -> None:
    """El menú ordena por tasa observada entre partidos distintos."""

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
        1: _prediction(away_shots_second_half_over_5_5=0.70),
        2: _prediction(home_corners_over_4_5=0.70),
    }

    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: fixtures)

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
    markets = [pick["market"] for pick in payload["picks"]]
    assert markets == [
        "home_corners_over_4_5", "away_shots_second_half_over_5_5"]
    assert payload["picks"][0]["fixture"]["match_id"] == 2
    assert payload["picks"][0]["observed_rate"] > payload["picks"][1][
        "observed_rate"]


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
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: fixtures)

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Falla el primero y resuelve el segundo."""

        if int(fixture["match_id"]) == 1:
            raise PrematchUnavailableError("history_insufficient")
        return _prediction(home_corners_over_4_5=0.70)

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
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: fixtures)

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Devuelve siempre un pick apto."""

        return _prediction(home_corners_over_4_5=0.70)

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    payload = TestClient(
        create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    ).get("/v1/high-probability?limit=2").json()

    assert payload["count"] == 2
    assert len(payload["picks"]) == 2
    assert payload["total_candidates"] == 3


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
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: fixtures)

    in_flight = {"current": 0, "max_seen": 0}

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Simula una inferencia real con latencia, contando concurrencia."""

        in_flight["current"] += 1
        in_flight["max_seen"] = max(in_flight["max_seen"], in_flight["current"])
        await asyncio.sleep(0.15)
        in_flight["current"] -= 1
        return _prediction(home_corners_over_4_5=0.70)

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
    monkeypatch.setattr(service, "_upcoming_catalog", lambda payload: fixtures)
    monkeypatch.setattr(
        service, "HIGH_PROBABILITY_WALL_CLOCK_BUDGET_SECONDS", 0.2)

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Simula una inferencia real con latencia constante."""

        await asyncio.sleep(0.1)
        return _prediction(home_corners_over_4_5=0.70)

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    started = time.monotonic()
    payload = TestClient(
        create_app(ServiceConfig(mode="operational_readonly", external_calls_enabled=True))
    ).get("/v1/high-probability?limit=50").json()
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

    def fetch(payload: tuple[str, int, str | None]) -> list[dict[str, Any]]:
        """Cuenta cuántas veces se ejecuta el barrido real."""

        calls["count"] += 1
        return fixtures

    monkeypatch.setattr(service, "_upcoming_catalog", fetch)

    async def prediction(app: Any, engine: Any, config: Any,
                         fixture: dict[str, Any]) -> dict[str, Any]:
        """Predicción sintética instantánea."""

        return _prediction(home_corners_over_4_5=0.70)

    monkeypatch.setattr(service, "_high_probability_prediction", prediction)

    client = TestClient(create_app(
        ServiceConfig(mode="operational_readonly", external_calls_enabled=True)))
    first = client.get("/v1/high-probability")
    second = client.get("/v1/high-probability")

    assert first.status_code == second.status_code == 200
    assert calls["count"] == 1


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
