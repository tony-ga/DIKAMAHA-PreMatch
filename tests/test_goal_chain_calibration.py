"""Pruebas de la cadena oficial con peso reestimado y 1X2 recalibrado.

Cubre `DEC-199` (escalado de temperatura conectado) y `DEC-200` (peso de mezcla
reestimado). Ambos cambian probabilidades servidas a usuarios reales, así que lo
que se fija aquí no es que existan, sino que **no puedan romper lo que ya
funcionaba**: normalización, orden de los resultados, y degradación visible.
"""

from __future__ import annotations

import hashlib
import json
import math

import pandas as pd
import pytest

from src.official_goal_chain import (
    BLEND_WEIGHT_DIXON_COLES,
    CALIBRATED_MARKET,
    DixonColesKalmanGoalModel,
    _blend_lambda,
)
from src.temperature_calibration import (
    ArtifactTemperatureCalibrationProvider,
    CONTRACT_VERSION,
)

CORPUS = "artifacts/match_level_corpus/matches.csv"


def _history_and_target() -> tuple[list[dict], dict]:
    """Toma una historia causal real y su siguiente partido."""

    frame = pd.read_csv(CORPUS)
    frame = frame[frame["league_slug"] == "esp.1"].sort_values(
        ["match_date", "match_id"])
    rows = [
        {
            "match_id": int(record["match_id"]),
            "match_date": pd.Timestamp(record["match_date"]).isoformat(),
            "home_team_id": int(record["home_team_id"]),
            "away_team_id": int(record["away_team_id"]),
            "home_goals": int(record["home_goals"]),
            "away_goals": int(record["away_goals"]),
        }
        for record in frame.to_dict("records")
    ]
    target = rows[300]
    history = [row for row in rows[:300]
               if row["match_date"] < target["match_date"]]
    return history, target


def _sealed_provider(tmp_path, temperature: float):
    """Sella un calibrador de prueba con su hash."""

    tmp_path.mkdir(parents=True, exist_ok=True)
    path = tmp_path / f"{CALIBRATED_MARKET}.json"
    path.write_text(json.dumps({
        "version": CONTRACT_VERSION,
        "market": CALIBRATED_MARKET,
        "temperature": temperature,
    }), encoding="utf-8")
    (tmp_path / "hashes.json").write_text(
        json.dumps({path.name: hashlib.sha256(path.read_bytes()).hexdigest()}),
        encoding="utf-8")
    return ArtifactTemperatureCalibrationProvider(tmp_path)


def test_blend_weight_matches_the_decision_and_is_log_linear() -> None:
    """El peso servido es el de `DEC-200` y la mezcla sigue siendo log-lineal."""

    assert BLEND_WEIGHT_DIXON_COLES == pytest.approx(0.642848)

    blended = _blend_lambda(2.0, 0.5)
    expected = math.exp(
        BLEND_WEIGHT_DIXON_COLES * math.log(2.0)
        + (1 - BLEND_WEIGHT_DIXON_COLES) * math.log(0.5))

    assert blended == pytest.approx(expected)
    # Un peso menor que 1 deja el blend estrictamente entre los dos componentes.
    assert 0.5 < blended < 2.0


@pytest.mark.historical
def test_served_1x2_is_calibrated_and_normalized() -> None:
    """La salida oficial llega recalibrada y suma exactamente uno."""

    history, target = _history_and_target()
    prediction = DixonColesKalmanGoalModel().predict(
        history, target["home_team_id"], target["away_team_id"],
        target["match_date"])

    total = (prediction.probability_home + prediction.probability_draw
             + prediction.probability_away)

    assert prediction.provenance["temperature_calibrated"] is True
    assert math.isclose(total, 1.0, abs_tol=1e-9)
    assert prediction.provenance["dixon_coles_weight"] == pytest.approx(
        BLEND_WEIGHT_DIXON_COLES)


@pytest.mark.historical
def test_calibration_never_changes_the_most_probable_result(tmp_path) -> None:
    """Recalibrar mueve la confianza, nunca el resultado anunciado.

    Es la propiedad que hace esto adoptable en un producto ya desplegado: si
    cambiara el argmax, cada despliegue del calibrador reescribiría qué se
    afirma sobre partidos ya publicados.
    """

    history, target = _history_and_target()

    outcomes = []
    for temperature in (0.6, 1.0, 1.1989, 3.0):
        prediction = DixonColesKalmanGoalModel(
            calibrator=_sealed_provider(tmp_path / str(temperature), temperature),
        ).predict(history, target["home_team_id"], target["away_team_id"],
                  target["match_date"])
        probabilities = {
            "1": prediction.probability_home,
            "X": prediction.probability_draw,
            "2": prediction.probability_away,
        }
        outcomes.append(max(probabilities, key=probabilities.get))

    assert len(set(outcomes)) == 1


@pytest.mark.historical
def test_missing_artifact_degrades_visibly_instead_of_failing(tmp_path) -> None:
    """Sin artefacto se sirve sin calibrar, y la procedencia lo declara.

    No predecir sería peor para un servicio desplegado, pero una degradación
    silenciosa es lo que hizo que `eligibility.json` se perdiera dos veces sin
    que nadie lo notara. Aquí queda escrita en la procedencia.
    """

    history, target = _history_and_target()
    empty = tmp_path / "sin_artefacto"
    empty.mkdir()

    prediction = DixonColesKalmanGoalModel(
        calibrator=ArtifactTemperatureCalibrationProvider(empty),
    ).predict(history, target["home_team_id"], target["away_team_id"],
              target["match_date"])

    total = (prediction.probability_home + prediction.probability_draw
             + prediction.probability_away)

    assert prediction.provenance["temperature_calibrated"] is False
    assert prediction.provenance["temperature_status"] == "artifact_unavailable"
    assert math.isclose(total, 1.0, abs_tol=1e-9)


@pytest.mark.historical
def test_over_2_5_and_btts_are_not_touched_by_the_1x2_calibrator(
    tmp_path,
) -> None:
    """La temperatura de 1X2 no altera los mercados binarios.

    Salen de la matriz conjunta y tienen su propia vía de calibración; pasarlos
    por un parámetro ajustado para tres clases sería aplicar una corrección que
    no se midió sobre ellos.
    """

    history, target = _history_and_target()

    flat = DixonColesKalmanGoalModel(
        calibrator=_sealed_provider(tmp_path / "flat", 3.0),
    ).predict(history, target["home_team_id"], target["away_team_id"],
              target["match_date"])
    sharp = DixonColesKalmanGoalModel(
        calibrator=_sealed_provider(tmp_path / "sharp", 0.6),
    ).predict(history, target["home_team_id"], target["away_team_id"],
              target["match_date"])

    assert flat.probability_over_2_5 == pytest.approx(sharp.probability_over_2_5)
    assert flat.probability_btts == pytest.approx(sharp.probability_btts)


def test_sealed_artifact_is_the_one_the_decision_measured() -> None:
    """El artefacto servido lleva la temperatura y el peso que se midieron."""

    provider = ArtifactTemperatureCalibrationProvider()
    calibrated, provenance = provider.predict(
        CALIBRATED_MARKET, {"1": 0.5, "X": 0.3, "2": 0.2})

    assert provenance["temperature"] == pytest.approx(1.1989353, abs=1e-6)
    assert math.isclose(sum(calibrated.values()), 1.0, abs_tol=1e-9)
