"""Pruebas del gate de reparación por shrinkage bayesiano de Fase 119."""

from __future__ import annotations

from scripts.run_phase_106_probability_repair import _metrics


def _row(match_id: int, league: str, raw: float, calibrated: float, actual: bool) -> dict:
    return {
        "match_id": match_id, "league_slug": league,
        "raw_probability": raw, "calibrated_probability": calibrated,
        "actual": actual,
        "raw_log_loss": _loss(raw, actual),
        "calibrated_log_loss": _loss(calibrated, actual),
        "raw_brier": (raw - float(actual)) ** 2,
        "calibrated_brier": (calibrated - float(actual)) ** 2,
    }


def _loss(probability: float, actual: bool) -> float:
    import math
    value = min(max(probability, 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value)


def test_gate_passes_when_calibration_clearly_improves() -> None:
    """Una calibración que corrige subconfianza sistemática pasa el gate."""

    rows = []
    leagues = ["esp.1", "mex.1", "bra.1"]
    for index in range(300):
        league = leagues[index % 3]
        actual = index % 10 < 7  # tasa real ~70%
        raw = 0.5  # crudo subconfiado y constante
        calibrated = 0.7  # calibrado cerca de la tasa real
        rows.append(_row(index, league, raw, calibrated, actual))

    metrics = _metrics(rows)

    assert metrics["passed"] is True
    assert metrics["calibrated_log_loss"] < metrics["raw_log_loss"]


def test_gate_fails_closed_when_calibration_does_not_help() -> None:
    """Una calibración que no mejora nada no debe pasar el gate."""

    rows = []
    leagues = ["esp.1", "mex.1", "bra.1"]
    for index in range(300):
        league = leagues[index % 3]
        actual = index % 2 == 0  # tasa real ~50%
        raw = 0.5  # crudo ya bien calibrado
        calibrated = 0.5  # calibrado idéntico: no hay mejora
        rows.append(_row(index, league, raw, calibrated, actual))

    metrics = _metrics(rows)

    assert metrics["passed"] is False


def test_gate_fails_when_calibration_degrades_most_leagues() -> None:
    """Una calibración que empeora la mayoría de ligas no debe pasar."""

    rows = []
    leagues = ["esp.1", "mex.1", "bra.1", "usa.1", "col.1"]
    for index in range(500):
        league = leagues[index % 5]
        actual = index % 2 == 0
        raw = 0.5
        # empeora deliberadamente en 4 de 5 ligas, mejora sólo en una
        calibrated = 0.9 if league != "esp.1" else 0.5
        rows.append(_row(index, league, raw, calibrated, actual))

    metrics = _metrics(rows)

    assert metrics["stability"]["non_degradation_rate"] < 0.70
    assert metrics["passed"] is False


# Version: 1.0.0
# Created: 2026-08-10
