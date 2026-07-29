"""Pruebas de calibración causal de mercados."""
from __future__ import annotations

from src.market_probability_calibration import PlattCalibrator


def test_platt_calibrator_is_monotonic() -> None:
    """Conserva el orden de riesgo del modelo base."""

    calibrator = PlattCalibrator().fit(
        [0.1, 0.2, 0.3, 0.7, 0.8, 0.9],
        [False, False, False, True, True, True],
    )

    assert calibrator.predict(0.8) > calibrator.predict(0.2)
    assert 0.0 < calibrator.predict(0.5) < 1.0


def test_platt_calibrator_rejects_one_class() -> None:
    """Evita ajustar una regresión sin soporte de ambas clases."""

    try:
        PlattCalibrator().fit([0.1, 0.2], [False, False])
    except ValueError as error:
        assert str(error) == "platt_requires_two_classes"
    else:
        raise AssertionError("one_class_calibration_was_accepted")


# Version: 1.0.0
# Created: 2026-07-29
