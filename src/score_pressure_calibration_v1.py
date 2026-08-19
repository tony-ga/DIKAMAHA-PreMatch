"""Estima la forma temporal de la presión de marcador (`DEC-216`).

El motor live reparte el efecto "ir perdiendo ataca más" de forma lineal
desde el saque inicial. El corpus causal de Fase 74 dice otra cosa: en las
ventanas de primera mitad la diferencia de presión entre ir ganando e ir
perdiendo cruza cero, y sólo se confirma a partir del minuto 45.

Este módulo mide el ratio empírico por ventana y ajusta sobre él la rampa
con umbral que `live_probability_engine_v1` expone como `ramp_v2`:

    late = 0                                        si t <= onset
           ((t - onset) / (1 - onset)) ** curvature  si t >  onset
    ratio(t) = (1 + gain*late) / max(floor, 1 - drop*late)

`ratio` es lo observable: cuánto más intensidad tiene quien persigue frente
a quien protege. Los dos multiplicadores no son identificables por separado
a partir del ratio -sólo su cociente lo es-, así que `drop` se fija a su
valor histórico y se estiman `onset`, `gain` y `curvature`. Fijar un
parámetro no observable en su valor vigente es más honesto que dejar que el
optimizador lo elija sin información para hacerlo.

La unidad de remuestreo es el partido completo, nunca la ventana.

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from scipy.optimize import minimize

# Splits del corpus de Fase 74 que este módulo puede leer. `confirmation`
# queda deliberadamente fuera: se reserva para el gate y ningún parámetro
# puede elegirse mirándolo.
ALLOWED_SPLITS = frozenset({"fit", "selection"})
FORBIDDEN_SPLIT = "confirmation"

# Minuto central de cada ventana de 15 minutos, que es el `midpoint` con el
# que el motor evalúa esa franja.
WINDOW_MIDPOINTS = (7.5, 22.5, 37.5, 52.5, 67.5, 82.5)
REGULATION_MINUTES = 90.0

BOOTSTRAP_REPLICATES = 2_000
BOOTSTRAP_SEED = 42

# Valor histórico de `protecting_drop`. Ver el docstring: el ratio observado
# no identifica `gain` y `drop` por separado.
HISTORICAL_PROTECTING_DROP = 0.10
HISTORICAL_PROTECTING_FLOOR = 0.78
HISTORICAL_CHASING_GAIN = 0.18


@dataclass(frozen=True, slots=True)
class RampParameters:
    """Parámetros de la rampa con umbral."""

    onset_fraction: float
    curvature: float
    chasing_gain: float
    protecting_drop: float = HISTORICAL_PROTECTING_DROP
    protecting_floor: float = HISTORICAL_PROTECTING_FLOOR


def ramp_ratio(parameters: RampParameters, midpoint: float,
               duration: float = REGULATION_MINUTES) -> float:
    """Ratio perseguir/proteger que implica una parametrización."""

    raw = min(1.0, midpoint / max(duration, 1.0))
    if raw <= parameters.onset_fraction:
        late = 0.0
    else:
        late = ((raw - parameters.onset_fraction)
                / (1.0 - parameters.onset_fraction)) ** parameters.curvature
    chasing = 1.0 + parameters.chasing_gain * late
    protecting = max(
        parameters.protecting_floor, 1.0 - parameters.protecting_drop * late)
    return chasing / protecting


def linear_ratio(midpoint: float,
                 duration: float = REGULATION_MINUTES) -> float:
    """Ratio que implica la forma lineal vigente, como comparador."""

    late = min(1.0, midpoint / max(duration, 1.0))
    chasing = 1.0 + HISTORICAL_CHASING_GAIN * late
    protecting = max(
        HISTORICAL_PROTECTING_FLOOR, 1.0 - HISTORICAL_PROTECTING_DROP * late)
    return chasing / protecting


def load_windows(path: Path, splits: Iterable[str]) -> list[dict[str, Any]]:
    """Carga las ventanas del corpus restringiendo los splits permitidos.

    Falla cerrado si se pide `confirmation`: es la única garantía real de que
    el bloque de confirmación no se toca, porque un filtro silencioso no
    dejaría rastro de haberlo leído.
    """

    requested = frozenset(splits)
    if FORBIDDEN_SPLIT in requested:
        raise ValueError("score_pressure_calibration_confirmation_forbidden")
    if not requested <= ALLOWED_SPLITS:
        raise ValueError("score_pressure_calibration_unknown_split")

    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("split") not in requested:
                continue
            rows.append({
                "match_id": int(record["match_id"]),
                "league_slug": str(record["league_slug"]),
                "window_index": int(record["window_index"]),
                "pressure": float(record["pressure"]),
                "score_for_start": int(record["score_for_start"]),
                "score_against_start": int(record["score_against_start"]),
            })
    if not rows:
        raise ValueError("score_pressure_calibration_no_rows")
    return rows


def empirical_ratios(rows: Sequence[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Ratio presión(perdiendo)/presión(ganando) por ventana, con IC95%.

    El bootstrap remuestrea partidos completos: las seis ventanas de un mismo
    partido comparten estado y no son independientes entre sí.
    """

    by_window: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        difference = row["score_for_start"] - row["score_against_start"]
        if difference == 0:
            continue
        by_window[row["window_index"]].append({
            "match_id": row["match_id"],
            "pressure": row["pressure"],
            "trailing": difference < 0,
        })

    generator = np.random.default_rng(BOOTSTRAP_SEED)
    output: dict[int, dict[str, Any]] = {}
    for window, entries in sorted(by_window.items()):
        by_match: dict[int, list[dict[str, Any]]] = defaultdict(list)
        for entry in entries:
            by_match[entry["match_id"]].append(entry)
        matches = sorted(by_match)

        point = _ratio(entries)
        estimates = np.empty(BOOTSTRAP_REPLICATES, dtype=float)
        indices = np.arange(len(matches))
        for replicate in range(BOOTSTRAP_REPLICATES):
            drawn = generator.choice(indices, size=len(matches), replace=True)
            sample: list[dict[str, Any]] = []
            for position in drawn:
                sample.extend(by_match[matches[position]])
            estimates[replicate] = _ratio(sample)
        valid = estimates[np.isfinite(estimates)]
        variance = float(np.var(valid, ddof=1)) if valid.size > 1 else float("nan")
        output[window] = {
            "ratio": point,
            "ci_low": float(np.percentile(valid, 2.5)) if valid.size else float("nan"),
            "ci_high": float(np.percentile(valid, 97.5)) if valid.size else float("nan"),
            "variance": variance,
            "matches": len(matches),
            "observations": len(entries),
            "midpoint": WINDOW_MIDPOINTS[window] if window < len(WINDOW_MIDPOINTS) else float("nan"),
        }
    return output


def _ratio(entries: Sequence[dict[str, Any]]) -> float:
    """Presión media de quien pierde dividida por la de quien gana."""

    trailing = [entry["pressure"] for entry in entries if entry["trailing"]]
    leading = [entry["pressure"] for entry in entries if not entry["trailing"]]
    if not trailing or not leading:
        return float("nan")
    denominator = float(np.mean(leading))
    if denominator <= 1e-12:
        return float("nan")
    return float(np.mean(trailing)) / denominator


def fit_ramp(ratios: dict[int, dict[str, Any]]) -> RampParameters:
    """Ajusta la rampa por mínimos cuadrados ponderados por 1/varianza.

    Ponderar por la varianza bootstrap evita que las ventanas con menos
    partidos -y por tanto ratio más ruidoso- arrastren el ajuste.
    """

    usable = [
        (entry["midpoint"], entry["ratio"], entry["variance"])
        for entry in ratios.values()
        if math.isfinite(entry["ratio"]) and math.isfinite(entry["midpoint"])
    ]
    if len(usable) < 3:
        raise ValueError("score_pressure_calibration_insufficient_windows")

    midpoints = np.array([item[0] for item in usable], dtype=float)
    observed = np.array([item[1] for item in usable], dtype=float)
    variances = np.array([item[2] for item in usable], dtype=float)
    weights = np.where(
        np.isfinite(variances) & (variances > 0.0), 1.0 / variances, 1.0)

    def objective(vector: np.ndarray) -> float:
        onset, curvature, gain = vector
        if not (0.0 <= onset < 1.0) or curvature <= 0.0 or gain < 0.0:
            return 1e9
        parameters = RampParameters(
            onset_fraction=float(onset), curvature=float(curvature),
            chasing_gain=float(gain))
        predicted = np.array(
            [ramp_ratio(parameters, midpoint) for midpoint in midpoints])
        return float(np.sum(weights * (observed - predicted) ** 2))

    best_vector, best_score = None, float("inf")
    # Arranques múltiples: la superficie tiene una meseta plana bajo el umbral
    # y un único punto de partida puede quedarse atrapado en ella.
    for onset_start in (0.30, 0.45, 0.55):
        for curvature_start in (0.8, 1.0, 1.5):
            for gain_start in (0.10, 0.20, 0.30):
                result = minimize(
                    objective,
                    x0=np.array([onset_start, curvature_start, gain_start]),
                    method="Nelder-Mead",
                    options={"xatol": 1e-8, "fatol": 1e-12, "maxiter": 4000},
                )
                if result.fun < best_score:
                    best_score, best_vector = float(result.fun), result.x

    if best_vector is None:
        raise RuntimeError("score_pressure_calibration_did_not_converge")
    return RampParameters(
        onset_fraction=float(np.clip(best_vector[0], 0.0, 0.99)),
        curvature=float(max(best_vector[1], 1e-6)),
        chasing_gain=float(max(best_vector[2], 0.0)),
    )


def weighted_error(
    ratios: dict[int, dict[str, Any]], predictor: Any,
) -> float:
    """Error cuadrático ponderado de un predictor de ratio sobre unas ventanas."""

    total = 0.0
    for entry in ratios.values():
        if not math.isfinite(entry["ratio"]) or not math.isfinite(entry["midpoint"]):
            continue
        variance = entry["variance"]
        weight = 1.0 / variance if (
            math.isfinite(variance) and variance > 0.0) else 1.0
        total += weight * (entry["ratio"] - predictor(entry["midpoint"])) ** 2
    return total


def parameters_as_dict(parameters: RampParameters) -> dict[str, float]:
    """Serializa los parámetros con las claves de la configuración del motor."""

    values = asdict(parameters)
    return {
        "score_pressure_profile": "ramp_v2",
        "score_pressure_onset_fraction": values["onset_fraction"],
        "score_pressure_curvature": values["curvature"],
        "score_pressure_chasing_gain": values["chasing_gain"],
        "score_pressure_protecting_drop": values["protecting_drop"],
        "score_pressure_protecting_floor": values["protecting_floor"],
    }


__all__ = [
    "ALLOWED_SPLITS",
    "RampParameters",
    "empirical_ratios",
    "fit_ramp",
    "linear_ratio",
    "load_windows",
    "parameters_as_dict",
    "ramp_ratio",
    "weighted_error",
]

# Version: 1.0.0
# Created: 2026-08-18
