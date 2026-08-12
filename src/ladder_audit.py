"""Audita la fiabilidad de cada línea de la escalera over/under.

El objetivo (`docs/objetivo_auditoria_modelos_v1.md`) fija un criterio de éxito
que no es el porcentaje de aciertos: una línea es apta cuando (1) está
calibrada -si declara 78%, acierta 78%- y (2) supera a la estrategia ingenua
de predecir siempre el lado mayoritario de esa línea, con intervalo de
confianza que no cruce cero.

La distinción importa porque el acierto puro se auto-infla: con una media de
~5.4 córners, predecir "over 0.5" acierta ~98% sin que el modelo aporte nada.
Publicar la escalera completa 0.5–12.5 medida por acierto agregado produciría
un sistema que reporta ~85% siendo inútil. Este módulo separa las dos cosas y
etiqueta cada línea con el origen real de su fiabilidad.

Version: 1.0.0
Created: 2026-08-12
"""

from __future__ import annotations

import math
from collections import defaultdict
from typing import Any, Iterable, Sequence

import numpy as np

try:
    from src.team_count_markets import (
        combined_dispersion, negative_binomial_distribution)
except ModuleNotFoundError:  # pragma: no cover - ejecución directa desde src
    from team_count_markets import (
        combined_dispersion, negative_binomial_distribution)

# Muestra mínima por celda. Por debajo no se emite veredicto: el intervalo
# bootstrap sería tan ancho que cualquier conclusión resultaría vacía.
MINIMUM_SAMPLE = 150

# Tolerancia de calibración. Una línea que declara 78% y entrega 71% no es
# publicable como cifra de confianza aunque su acierto agregado sea alto.
CALIBRATION_TOLERANCE = 0.05

# Remuestreos bootstrap. La unidad es el partido completo, nunca la línea:
# las líneas de un mismo partido están fuertemente correlacionadas.
BOOTSTRAP_RESAMPLES = 1000

# Cuantos tramos de probabilidad se usan para el error de calibración (ECE).
CALIBRATION_BINS = 10

VERDICT_EDGE = "model_edge"
VERDICT_BASE_RATE = "base_rate_driven"
VERDICT_MISCALIBRATED = "miscalibrated"
VERDICT_INSUFFICIENT = "insufficient_sample"

# Sólo estos dos veredictos son publicables: la línea está calibrada y su
# fiabilidad -por ventaja del modelo o por tasa base- está medida.
PUBLISHABLE_VERDICTS = frozenset({VERDICT_EDGE, VERDICT_BASE_RATE})

# Qué escalera corresponde a cada métrica del artefacto de Fase 84A. El
# nombre ya codifica el periodo -"corners_first_half" es la primera mitad-,
# así que el máximo se elige por métrica base y mitad. Compartido entre el
# script de auditoría (`scripts/run_ladder_audit.py`) y el runtime que expone
# la escalera auditada, para que nunca puedan divergir sobre qué rango audita
# cada métrica.
METRIC_LADDERS: dict[str, tuple[str, str]] = {
    "corners": ("corners", "full_match"),
    "corners_first_half": ("corners", "half"),
    "shots": ("shots", "full_match"),
    "shots_on_target": ("shots_on_target", "full_match"),
    "yellow_cards": ("yellow_cards", "full_match"),
    "yellow_cards_first_half": ("yellow_cards", "half"),
}


def maximum_line(metric: str, side: str, ladder_maximums: dict[str, dict[str, int]]) -> int:
    """Umbral entero máximo a auditar o exponer para esa métrica y lado.

    Una línea `total` suma dos equipos, así que su soporte útil es mayor.
    """

    base, period = METRIC_LADDERS[metric]
    maximum = ladder_maximums[base][period]
    return maximum * 2 if side == "total" else maximum


def over_probability(mean: float, phi: float, threshold: int) -> float:
    """Probabilidad de superar un umbral entero bajo la NB del runtime."""

    distribution = negative_binomial_distribution(mean, phi)
    return float(sum(
        value for count, value in distribution.items() if count > threshold))


def combined_phi(
    phi: float, home_mean: float, away_mean: float,
    correlation: float = 0.0,
) -> float:
    """Delega en la misma fórmula que usa el runtime para las líneas `total`.

    Se reutiliza en vez de replicarse para que la auditoría no pueda
    divergir de lo que realmente se sirve.
    """

    return combined_dispersion(phi, home_mean, away_mean, correlation)


def _expected_calibration_error(
    predicted: np.ndarray, observed: np.ndarray,
) -> float:
    """Calcula el ECE por tramos de probabilidad declarada.

    Promediar la probabilidad de toda la celda ocultaría el caso que importa:
    un modelo que compensa exceso de confianza en unos partidos con defecto en
    otros parecería perfecto en el agregado.
    """

    edges = np.linspace(0.0, 1.0, CALIBRATION_BINS + 1)
    total = len(predicted)
    error = 0.0
    for index in range(CALIBRATION_BINS):
        low, high = edges[index], edges[index + 1]
        mask = (predicted >= low) & (
            predicted < high if index < CALIBRATION_BINS - 1
            else predicted <= high)
        if not mask.any():
            continue
        error += (mask.sum() / total) * abs(
            float(predicted[mask].mean()) - float(observed[mask].mean()))
    return error


def _bootstrap_skills(
    correct_model: np.ndarray, correct_base: np.ndarray,
    squared_model: np.ndarray, squared_base: np.ndarray,
    match_ids: np.ndarray, rng: np.random.Generator,
) -> tuple[tuple[float, float], tuple[float, float]]:
    """Intervalos del 95% para las dos ventajas, por partido completo.

    Devuelve `(acierto, brier)`. Se miden las dos porque responden preguntas
    distintas y una sola engañaría:

    - **Acierto contra el lado mayoritario** dice si el modelo sirve para
      elegir un lado. En líneas extremas es casi imposible de batir -si el
      98% de los partidos supera la línea 0.5, acertar siempre "over" ya da
      98%- así que su ausencia no implica que el modelo no informe.
    - **Brier contra el baseline de liga** dice si conocer los equipos
      concretos mejora la probabilidad publicada frente a usar la media de
      la liga. Es la pregunta que de verdad importa para una salida
      adaptativa por partido, que es lo que se publica.
    """

    unique = np.unique(match_ids)
    index_by_match = {
        value: np.flatnonzero(match_ids == value) for value in unique}
    accuracy = np.empty(BOOTSTRAP_RESAMPLES, dtype=float)
    brier = np.empty(BOOTSTRAP_RESAMPLES, dtype=float)
    for resample in range(BOOTSTRAP_RESAMPLES):
        chosen = rng.choice(unique, size=len(unique), replace=True)
        rows = np.concatenate([index_by_match[value] for value in chosen])
        accuracy[resample] = float(
            correct_model[rows].mean() - correct_base[rows].mean())
        # Positivo = el modelo mejora (menor Brier es mejor).
        brier[resample] = float(
            squared_base[rows].mean() - squared_model[rows].mean())
    return (
        (float(np.percentile(accuracy, 2.5)),
         float(np.percentile(accuracy, 97.5))),
        (float(np.percentile(brier, 2.5)),
         float(np.percentile(brier, 97.5))),
    )


def audit_cell(
    predicted: Sequence[float], baseline: Sequence[float],
    actual_over: Sequence[bool], leagues: Sequence[str],
    match_ids: Sequence[int], rng: np.random.Generator,
) -> dict[str, Any]:
    """Audita una línea concreta y emite su veredicto de fiabilidad."""

    model = np.asarray(predicted, dtype=float)
    base = np.asarray(baseline, dtype=float)
    observed = np.asarray(actual_over, dtype=bool)
    total = len(observed)
    if total < MINIMUM_SAMPLE:
        return {"sample": total, "verdict": VERDICT_INSUFFICIENT}

    observed_rate = float(observed.mean())
    mean_predicted = float(model.mean())
    ece = _expected_calibration_error(model, observed.astype(float))

    # La estrategia ingenua no mira el modelo: apuesta siempre al lado
    # mayoritario observado de esa línea. Es el comparador honesto.
    majority_side = observed_rate >= 0.5
    correct_base = (observed == majority_side)
    correct_model = ((model > 0.5) == observed)
    model_accuracy = float(correct_model.mean())
    base_accuracy = float(correct_base.mean())

    truth = observed.astype(float)
    squared_model = (model - truth) ** 2
    squared_base = (base - truth) ** 2
    # Cuando el peso de mezcla seleccionado es 0, la salida servida ES el
    # baseline de liga. Comparar el modelo contra sí mismo daría ventaja
    # exactamente cero y parecería un empate; conviene declararlo.
    model_is_baseline = bool(np.allclose(model, base))
    (accuracy_low, accuracy_high), (brier_low, brier_high) = _bootstrap_skills(
        correct_model, correct_base, squared_model, squared_base,
        np.asarray(match_ids), rng)

    calibrated = (
        ece <= CALIBRATION_TOLERANCE
        and abs(mean_predicted - observed_rate) <= CALIBRATION_TOLERANCE)
    if not calibrated:
        verdict = VERDICT_MISCALIBRATED
    elif brier_low > 0.0:
        # Conocer los equipos concretos mejora la probabilidad frente a la
        # media de la liga, que es lo que hace útil una salida adaptativa.
        verdict = VERDICT_EDGE
    else:
        verdict = VERDICT_BASE_RATE

    return {
        "sample": total,
        "model_is_league_baseline": model_is_baseline,
        "observed_rate": observed_rate,
        "mean_predicted": mean_predicted,
        "calibration_gap": mean_predicted - observed_rate,
        "ece": ece,
        "model_accuracy": model_accuracy,
        "base_rate_accuracy": base_accuracy,
        "skill_accuracy_vs_majority": model_accuracy - base_accuracy,
        "skill_accuracy_ci95": [accuracy_low, accuracy_high],
        "brier_model": float(squared_model.mean()),
        "brier_baseline": float(squared_base.mean()),
        "skill_brier_vs_league_baseline": float(
            squared_base.mean() - squared_model.mean()),
        "skill_brier_ci95": [brier_low, brier_high],
        "league_nondegraded_rate": _league_rate(
            model, base, observed, leagues),
        "verdict": verdict,
    }


def _league_rate(
    model: np.ndarray, base: np.ndarray, observed: np.ndarray,
    leagues: Sequence[str],
) -> float:
    """Fracción de ligas donde el modelo no empeora el Brier del baseline."""

    grouped: dict[str, list[int]] = defaultdict(list)
    for index, league in enumerate(leagues):
        grouped[str(league)].append(index)
    eligible = [rows for rows in grouped.values() if len(rows) >= 30]
    if not eligible:
        return 0.0
    truth = observed.astype(float)
    passed = sum(
        float(np.mean((model[rows] - truth[rows]) ** 2)
              <= np.mean((base[rows] - truth[rows]) ** 2))
        for rows in eligible)
    return passed / len(eligible)


def _side_rows(
    rows: Iterable[dict[str, Any]], metric: str, side: str,
) -> list[dict[str, Any]]:
    """Materializa observaciones de un lado, o la suma de ambos para `total`."""

    if side in ("home", "away"):
        want_home = side == "home"
        return [
            {
                "model": float(row["expected"][metric]),
                "baseline": float(row["baseline"][metric]),
                "actual": float(row["actual"][metric]),
                "league": str(row["league_slug"]),
                "match_id": int(row["match_id"]),
                "phi_scale": None,
            }
            for row in rows
            if bool(row["is_home"]) is want_home and metric in row["expected"]
        ]
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if metric in row["expected"]:
            grouped[int(row["match_id"])].append(row)
    output = []
    for match_id, values in grouped.items():
        if len(values) != 2:
            continue
        home = next((r for r in values if bool(r["is_home"])), None)
        away = next((r for r in values if not bool(r["is_home"])), None)
        if home is None or away is None:
            continue
        output.append({
            "model": float(home["expected"][metric]) + float(
                away["expected"][metric]),
            "baseline": float(home["baseline"][metric]) + float(
                away["baseline"][metric]),
            "actual": float(home["actual"][metric]) + float(
                away["actual"][metric]),
            "league": str(home["league_slug"]),
            "match_id": match_id,
            "phi_scale": (
                float(home["expected"][metric]),
                float(away["expected"][metric])),
        })
    return output


def audit_ladder(
    rows: list[dict[str, Any]], metric: str, side: str, phi: float,
    maximum_line: int, seed: int = 20260812, correlation: float = 0.0,
) -> list[dict[str, Any]]:
    """Audita cada línea entera de una escalera (0.5 hasta `maximum_line`+0.5)."""

    observations = _side_rows(rows, metric, side)
    if not observations:
        return []
    rng = np.random.default_rng(seed)
    leagues = [row["league"] for row in observations]
    match_ids = [row["match_id"] for row in observations]
    output = []
    for threshold in range(maximum_line + 1):
        predicted, baseline, actual_over = [], [], []
        for row in observations:
            scale = row["phi_scale"]
            row_phi = (
                phi if scale is None
                else combined_phi(phi, *scale, correlation))
            predicted.append(over_probability(row["model"], row_phi, threshold))
            baseline.append(
                over_probability(row["baseline"], row_phi, threshold))
            actual_over.append(row["actual"] > threshold)
        result = audit_cell(
            predicted, baseline, actual_over, leagues, match_ids, rng)
        output.append({
            "metric": metric, "team_side": side,
            "line": float(threshold) + 0.5, **result})
    return output


def summarize(cells: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega el resultado de la auditoría por veredicto."""

    counts: dict[str, int] = defaultdict(int)
    for cell in cells:
        counts[str(cell["verdict"])] += 1
    publishable = [
        cell for cell in cells
        if cell["verdict"] in (VERDICT_EDGE, VERDICT_BASE_RATE)]
    baseline_only = sorted({
        str(cell["metric"]) for cell in cells
        if cell.get("model_is_league_baseline")})
    return {
        "cells": len(cells),
        "by_verdict": dict(sorted(counts.items())),
        "publishable": len(publishable),
        "with_model_edge": counts[VERDICT_EDGE],
        "metrics_served_as_league_baseline": baseline_only,
        "disclosure": (
            "Una línea apta puede serlo por ventaja real del modelo "
            "(`model_edge`: conocer los equipos concretos mejora el Brier "
            "frente a la media de la liga, con IC que no cruza cero) o "
            "porque la distribución de esa línea ya es predecible sin el "
            "modelo (`base_rate_driven`). Ambas son fiables como "
            "probabilidad publicada; sólo la primera refleja capacidad "
            "predictiva específica del partido."
        ),
    }


# Version: 1.0.0
# Created: 2026-08-12
