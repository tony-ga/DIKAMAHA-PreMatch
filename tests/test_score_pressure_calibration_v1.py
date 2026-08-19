"""Pruebas de la calibración de la forma temporal de presión (`DEC-216`).

El criterio que verifica este módulo es de separación de bloques: los
parámetros se estiman sobre `fit`, la elección de forma se decide sobre
`selection`, y `confirmation` no puede leerse por accidente -pedirlo es un
error, no un filtro silencioso-. Además fija que la parametrización nueva
contiene exactamente a la vigente como caso particular, que es lo que
permite activarla sin cambiar nada mientras no se decida hacerlo.

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.score_pressure_calibration_v1 import (
    HISTORICAL_CHASING_GAIN,
    HISTORICAL_PROTECTING_DROP,
    RampParameters,
    empirical_ratios,
    fit_ramp,
    linear_ratio,
    load_windows,
    parameters_as_dict,
    ramp_ratio,
    weighted_error,
)


def _window(**changes: object) -> dict[str, object]:
    row = {
        "match_id": 1,
        "league_slug": "esp.1",
        "window_index": 3,
        "pressure": 2.0,
        "score_for_start": 0,
        "score_against_start": 0,
        "split": "fit",
        "team_id": 10,
        "is_home": True,
    }
    row.update(changes)
    return row


def _corpus(tmp_path: Path, rows: list[dict[str, object]]) -> Path:
    path = tmp_path / "micro_windows.jsonl"
    path.write_text(
        "\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
    return path


def test_requesting_the_confirmation_split_fails_closed(tmp_path: Path) -> None:
    path = _corpus(tmp_path, [_window(split="confirmation")])

    with pytest.raises(ValueError, match="confirmation_forbidden"):
        load_windows(path, {"confirmation"})


def test_an_unknown_split_is_rejected(tmp_path: Path) -> None:
    path = _corpus(tmp_path, [_window()])

    with pytest.raises(ValueError, match="unknown_split"):
        load_windows(path, {"holdout"})


def test_only_the_requested_split_is_loaded(tmp_path: Path) -> None:
    path = _corpus(tmp_path, [
        _window(match_id=1, split="fit"),
        _window(match_id=2, split="selection"),
        _window(match_id=3, split="confirmation"),
    ])

    rows = load_windows(path, {"fit"})

    assert [row["match_id"] for row in rows] == [1]


def test_the_ramp_contains_the_legacy_linear_form_as_a_special_case() -> None:
    """Con umbral cero y curvatura uno la rampa ES la forma vigente."""

    legacy = RampParameters(
        onset_fraction=0.0, curvature=1.0,
        chasing_gain=HISTORICAL_CHASING_GAIN,
        protecting_drop=HISTORICAL_PROTECTING_DROP)

    for midpoint in (0.0, 22.5, 45.0, 67.5, 90.0):
        assert ramp_ratio(legacy, midpoint) == pytest.approx(
            linear_ratio(midpoint), abs=1e-12)


def test_a_curvature_above_one_suppresses_the_first_half() -> None:
    """Es la propiedad que motiva `DEC-216`: efecto tardío, no proporcional."""

    quadratic = RampParameters(
        onset_fraction=0.0, curvature=2.0, chasing_gain=0.13)

    early = ramp_ratio(quadratic, 22.5)
    late = ramp_ratio(quadratic, 82.5)

    assert early < linear_ratio(22.5)
    assert late > early


def test_the_ratio_is_monotone_in_the_clock() -> None:
    parameters = RampParameters(
        onset_fraction=0.4, curvature=1.5, chasing_gain=0.2)

    ratios = [ramp_ratio(parameters, float(minute))
              for minute in range(0, 91, 5)]

    assert ratios == sorted(ratios)


def test_empirical_ratios_ignore_windows_without_both_sides(
    tmp_path: Path,
) -> None:
    """Una ventana con todos empatados no produce contraste, y se omite."""

    path = _corpus(tmp_path, [
        _window(match_id=index, window_index=0,
                score_for_start=0, score_against_start=0)
        for index in range(5)
    ])
    rows = load_windows(path, {"fit"})

    assert empirical_ratios(rows) == {}


def test_the_fit_recovers_a_known_shape() -> None:
    """Ratios generados por una parametrización se vuelven a encontrar."""

    truth = RampParameters(
        onset_fraction=0.0, curvature=2.0, chasing_gain=0.15)
    ratios = {
        window: {
            "ratio": ramp_ratio(truth, midpoint),
            "midpoint": midpoint,
            "variance": 1e-4,
        }
        for window, midpoint in enumerate((22.5, 37.5, 52.5, 67.5, 82.5), start=1)
    }

    recovered = fit_ramp(ratios)

    for midpoint in (22.5, 52.5, 82.5):
        assert ramp_ratio(recovered, midpoint) == pytest.approx(
            ramp_ratio(truth, midpoint), abs=1e-3)


def test_weighted_error_penalises_the_wrong_shape() -> None:
    truth = RampParameters(
        onset_fraction=0.0, curvature=2.0, chasing_gain=0.15)
    ratios = {
        window: {
            "ratio": ramp_ratio(truth, midpoint),
            "midpoint": midpoint,
            "variance": 1e-4,
        }
        for window, midpoint in enumerate((22.5, 52.5, 82.5), start=1)
    }

    good = weighted_error(ratios, lambda m: ramp_ratio(truth, m))
    bad = weighted_error(ratios, linear_ratio)

    assert good < bad


def test_the_calibrated_ratio_matches_what_the_engine_actually_applies() -> None:
    """El calibrador y el motor deben implementar la misma fórmula.

    Están en módulos distintos por una razón -uno estima, el otro sirve- pero
    si sus fórmulas se separan, la calibración estaría ajustando una forma que
    producción no usa. Esta prueba ata las dos implementaciones.
    """

    from src.live_probability_engine_v1 import (
        LiveProbabilityEngineConfig, LiveProbabilityEngineV1)
    from src.markov_live_v1 import MarkovLiveInput

    parameters = RampParameters(
        onset_fraction=0.35, curvature=1.7, chasing_gain=0.14)
    engine = LiveProbabilityEngineV1(
        LiveProbabilityEngineConfig(**parameters_as_dict(parameters)))
    request = MarkovLiveInput(
        match_id=1, home_team_id=10, away_team_id=20,
        kickoff_ts="2026-08-09T20:00:00+00:00",
        snapshot_ts="2026-08-09T20:00:00+00:00",
        match_clock_seconds=0.0, period=1,
        score_home=0, score_away=1,
        lambda_base_home=1.5, lambda_base_away=1.1,
        events=(), league_slug="esp.1", source_hash="h")

    for midpoint in (0.0, 22.5, 31.5, 45.0, 67.5, 82.5, 90.0):
        chasing, protecting = engine._score_factors(request, midpoint, 90.0)
        assert chasing / protecting == pytest.approx(
            ramp_ratio(parameters, midpoint), abs=1e-12)


def test_serialised_parameters_use_the_engine_configuration_keys() -> None:
    payload = parameters_as_dict(RampParameters(
        onset_fraction=0.1, curvature=2.0, chasing_gain=0.13))

    assert payload["score_pressure_profile"] == "ramp_v2"
    assert set(payload) == {
        "score_pressure_profile", "score_pressure_onset_fraction",
        "score_pressure_curvature", "score_pressure_chasing_gain",
        "score_pressure_protecting_drop", "score_pressure_protecting_floor",
    }
