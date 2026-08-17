"""Pruebas del escalado de temperatura para mercados multiclase (`DEC-199`)."""

from __future__ import annotations

import json
import math
import random

import pytest

from src.temperature_calibration import (
    ArtifactTemperatureCalibrationProvider,
    CONTRACT_VERSION,
    apply_temperature,
    fit_temperature,
    negative_log_likelihood,
    records_from_rows,
)


def _entropy(probabilities: dict[str, float]) -> float:
    """Entropía en nats; mide cuán concentrada está la distribución."""

    return -sum(
        value * math.log(value) for value in probabilities.values()
        if value > 0.0)


def _overconfident_records(
    count: int = 400, seed: int = 7,
) -> list[tuple[dict[str, float], str]]:
    """Genera predicciones sistemáticamente sobreconfiadas.

    El resultado se sortea con la probabilidad *verdadera*, mientras que la
    predicción declarada es una versión concentrada de esa verdad. Es la firma
    exacta que `DEC-162` midió en 1X2: el tramo declarado supera al observado.
    """

    rng = random.Random(seed)
    records = []
    for _ in range(count):
        true_home = rng.uniform(0.25, 0.55)
        true_draw = rng.uniform(0.20, 0.30)
        true_away = 1.0 - true_home - true_draw
        truth = {"1": true_home, "X": true_draw, "2": true_away}
        declared = apply_temperature(truth, 0.55)
        draw = rng.random()
        cumulative = 0.0
        for label, value in truth.items():
            cumulative += value
            if draw <= cumulative:
                records.append((declared, label))
                break
    return records


def test_temperature_one_is_the_identity() -> None:
    """`T = 1` no cambia nada: la recalibración es opcional por construcción."""

    probabilities = {"1": 0.55, "X": 0.25, "2": 0.20}
    calibrated = apply_temperature(probabilities, 1.0)

    for label, value in probabilities.items():
        assert calibrated[label] == pytest.approx(value)


def test_temperature_preserves_the_most_probable_outcome() -> None:
    """El argmax se conserva para cualquier `T > 0`.

    Es la propiedad que hace segura esta pieza en un producto ya desplegado:
    recalibrar cambia la confianza declarada, nunca qué resultado se anuncia
    como más probable (R6 de `model_composition v1`).
    """

    probabilities = {"1": 0.46, "X": 0.31, "2": 0.23}
    expected = max(probabilities, key=probabilities.get)

    for temperature in (0.1, 0.5, 0.9, 1.0, 1.5, 3.0, 12.0):
        calibrated = apply_temperature(probabilities, temperature)
        assert max(calibrated, key=calibrated.get) == expected
        assert sum(calibrated.values()) == pytest.approx(1.0)
        assert all(0.0 <= value <= 1.0 for value in calibrated.values())


def test_high_temperature_flattens_and_low_temperature_sharpens() -> None:
    """`T` alto acerca a la uniforme; `T` bajo concentra en el argmax."""

    probabilities = {"1": 0.60, "X": 0.25, "2": 0.15}
    base = _entropy(probabilities)

    assert _entropy(apply_temperature(probabilities, 4.0)) > base
    assert _entropy(apply_temperature(probabilities, 0.4)) < base


def test_fit_recovers_a_temperature_above_one_for_overconfident_input() -> None:
    """Ante sobreconfianza sistemática, el ajuste aplana.

    Un modelo que declara más certeza de la que entrega necesita `T > 1`. Si
    esta prueba fallara con `T < 1`, el signo del ajuste estaría invertido y la
    recalibración empeoraría exactamente el defecto que debe corregir.
    """

    result = fit_temperature(_overconfident_records())

    assert result["temperature"] > 1.0
    assert result["nll_improvement"] > 0.0
    assert result["records"] == len(_overconfident_records())


def test_fit_never_increases_negative_log_likelihood() -> None:
    """El ajuste no puede quedar peor que no recalibrar.

    `T = 1` está dentro del rango de búsqueda, así que el óptimo es a lo sumo
    tan malo como la identidad. Si esto falla, la optimización no convergió.
    """

    records = _overconfident_records(count=250, seed=11)
    result = fit_temperature(records)

    assert result["nll_calibrated"] <= result["nll_uncalibrated"] + 1e-9
    assert negative_log_likelihood(
        records, result["temperature"]) == pytest.approx(
            result["nll_calibrated"])


def test_well_calibrated_input_leaves_temperature_near_one() -> None:
    """Sobre datos ya calibrados el ajuste casi no interviene.

    Importa tanto como el caso anterior: una recalibración que siempre mueve el
    número aunque no haga falta introduce ruido en vez de corregir sesgo.
    """

    rng = random.Random(3)
    records = []
    for _ in range(600):
        home = rng.uniform(0.30, 0.50)
        draw = rng.uniform(0.22, 0.30)
        truth = {"1": home, "X": draw, "2": 1.0 - home - draw}
        pick = rng.random()
        cumulative = 0.0
        for label, value in truth.items():
            cumulative += value
            if pick <= cumulative:
                records.append((truth, label))
                break

    result = fit_temperature(records)
    assert result["temperature"] == pytest.approx(1.0, abs=0.25)


def test_invalid_inputs_fail_closed() -> None:
    """Entradas inválidas levantan en vez de devolver un número inventado."""

    with pytest.raises(ValueError):
        apply_temperature({"1": 0.5, "X": 0.5}, 0.0)
    with pytest.raises(ValueError):
        apply_temperature({"1": 0.5, "X": 0.5}, float("nan"))
    with pytest.raises(ValueError):
        apply_temperature({"1": 0.6, "X": 0.6}, 1.0)
    with pytest.raises(ValueError):
        apply_temperature({"1": 1.0}, 1.0)
    with pytest.raises(ValueError):
        apply_temperature({"1": -0.2, "X": 1.2}, 1.0)
    with pytest.raises(ValueError):
        fit_temperature([({"1": 0.5, "X": 0.5}, "1")])


def test_records_from_rows_rejects_an_outcome_outside_the_label_set() -> None:
    """Un outcome fuera del conjunto declarado no se silencia."""

    rows = [{"probabilities": {"1": 0.5, "X": 0.3, "2": 0.2}, "outcome": "Z"}]

    with pytest.raises(KeyError):
        records_from_rows(rows, ("1", "X", "2"))


def test_artifact_provider_validates_hash_and_version(tmp_path) -> None:
    """El proveedor falla cerrado ante artefacto alterado o de otra versión."""

    import hashlib

    calibrator = tmp_path / "match_result_1x2.json"
    payload = {
        "version": CONTRACT_VERSION,
        "market": "match_result_1x2",
        "temperature": 1.6,
    }
    calibrator.write_text(json.dumps(payload), encoding="utf-8")
    digest = hashlib.sha256(calibrator.read_bytes()).hexdigest()
    (tmp_path / "hashes.json").write_text(
        json.dumps({"match_result_1x2.json": digest}), encoding="utf-8")

    provider = ArtifactTemperatureCalibrationProvider(tmp_path)
    calibrated, provenance = provider.predict(
        "match_result_1x2", {"1": 0.55, "X": 0.25, "2": 0.20})

    assert provenance["temperature"] == pytest.approx(1.6)
    assert provenance["argmax_preserved"] is True
    assert max(calibrated, key=calibrated.get) == "1"

    calibrator.write_text(
        json.dumps({**payload, "temperature": 9.0}), encoding="utf-8")
    with pytest.raises(ValueError, match="hash_mismatch"):
        ArtifactTemperatureCalibrationProvider(tmp_path).predict(
            "match_result_1x2", {"1": 0.55, "X": 0.25, "2": 0.20})
