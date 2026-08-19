"""Recalibra la forma temporal contra la cantidad correcta (`DEC-216`, 116D).

Fase 116A ajustó la rampa contra el ratio de **eventos de presión** y el gate
116C la rechazó. El diagnóstico de ese rechazo identificó dos defectos de
especificación, y esta fase los corrige:

1. **Cantidad equivocada.** `_score_factors` multiplica la intensidad de gol,
   no la de presión. El objetivo correcto es el ratio de **tasa de gol** entre
   quien pierde y quien gana.
2. **Confusión por fuerza.** El ratio crudo de tasa de gol da `0.913` -quien
   pierde marca menos-, pero eso es selección: quien va ganando suele ser el
   equipo mejor. Controlando por fuerza propia y del rival el ratio pasa a
   `1.097`, **invirtiendo el signo**. Es el caso que `DEC-218` existe para
   detectar, aplicado a la propia calibración que lo motivó.

Controlado, el patrón confirma la hipótesis original de `DEC-216` con la
cantidad buena: indistinguible de 1.0 hasta el minuto ~52 y confirmado
-IC95% que excluye 1.0- sólo desde el minuto 60.

Los parámetros se ajustan **exclusivamente sobre `split=fit`**; `selection`
queda para elegir entre formas y `confirmation` no se lee nunca aquí.

Uso:
    python -m scripts.run_phase_116d_goal_rate_calibration

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from scipy.optimize import minimize

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.score_pressure_calibration_v1 import (  # noqa: E402
    FORBIDDEN_SPLIT,
    RampParameters,
    WINDOW_MIDPOINTS,
    linear_ratio,
    parameters_as_dict,
    ramp_ratio,
)

SOURCE = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
OUTPUT = ROOT / "artifacts/phase_116d_goal_rate_calibration"

MINIMUM_TEAM_WINDOWS = 30
STRENGTH_QUANTILES = (0.25, 0.5, 0.75)
MINIMUM_CELL = 25
BOOTSTRAP_REPLICATES = 400
SEED = 42


def _load(splits: frozenset[str]) -> list[dict[str, Any]]:
    """Carga ventanas de los splits pedidos, prohibiendo `confirmation`."""

    if FORBIDDEN_SPLIT in splits:
        raise ValueError("goal_rate_calibration_confirmation_forbidden")
    rows = []
    with SOURCE.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record.get("split") not in splits:
                continue
            rows.append(record)
    return rows


def _team_strength(rows: Sequence[dict[str, Any]]) -> dict[int, float]:
    """Tasa de gol por ventana de cada equipo, como proxy de calidad."""

    goals: dict[int, float] = defaultdict(float)
    counts: dict[int, int] = defaultdict(int)
    for row in rows:
        goals[row["team_id"]] += float(row["goals"])
        counts[row["team_id"]] += 1
    return {
        team: goals[team] / counts[team]
        for team in counts if counts[team] >= MINIMUM_TEAM_WINDOWS
    }


def _controlled_ratio(block: np.ndarray, own: np.ndarray, rival: np.ndarray) -> float:
    """Ratio gol(perdiendo)/gol(ganando) dentro de estratos de fuerza."""

    numerator, denominator = 0.0, 0
    for own_bin in range(len(STRENGTH_QUANTILES) + 1):
        for rival_bin in range(len(STRENGTH_QUANTILES) + 1):
            mask = (own == own_bin) & (rival == rival_bin)
            trailing = block[mask & (block[:, 0] == 1)]
            leading = block[mask & (block[:, 0] == 0)]
            if (len(trailing) < MINIMUM_CELL or len(leading) < MINIMUM_CELL
                    or leading[:, 1].mean() <= 0.0):
                continue
            weight = len(trailing) + len(leading)
            numerator += (trailing[:, 1].mean() / leading[:, 1].mean()) * weight
            denominator += weight
    return numerator / denominator if denominator else float("nan")


def controlled_ratios(rows: Sequence[dict[str, Any]]) -> dict[int, dict[str, Any]]:
    """Ratio controlado por ventana, con IC95% remuestreando partidos."""

    strength = _team_strength(rows)
    generator = np.random.default_rng(SEED)
    output: dict[int, dict[str, Any]] = {}

    for window in range(1, len(WINDOW_MIDPOINTS)):
        entries, match_ids = [], []
        for row in rows:
            if row["window_index"] != window:
                continue
            difference = row["score_for_start"] - row["score_against_start"]
            if difference == 0:
                continue
            own = strength.get(row["team_id"])
            rival = strength.get(row["opponent_team_id"])
            if own is None or rival is None:
                continue
            entries.append((1.0 if difference < 0 else 0.0,
                            float(row["goals"]), own, rival))
            match_ids.append(row["match_id"])
        if not entries:
            continue

        block = np.array(entries, dtype=float)
        ids = np.array(match_ids)
        own_edges = np.quantile(block[:, 2], STRENGTH_QUANTILES)
        rival_edges = np.quantile(block[:, 3], STRENGTH_QUANTILES)
        own_bin = np.digitize(block[:, 2], own_edges)
        rival_bin = np.digitize(block[:, 3], rival_edges)

        point = _controlled_ratio(block, own_bin, rival_bin)
        raw = (block[block[:, 0] == 1][:, 1].mean()
               / block[block[:, 0] == 0][:, 1].mean())

        unique = np.unique(ids)
        samples = []
        for _ in range(BOOTSTRAP_REPLICATES):
            drawn = generator.choice(unique, size=len(unique), replace=True)
            index = np.concatenate([np.where(ids == m)[0] for m in drawn])
            value = _controlled_ratio(
                block[index], own_bin[index], rival_bin[index])
            if np.isfinite(value):
                samples.append(value)
        samples = np.array(samples)
        low, high = np.percentile(samples, [2.5, 97.5])
        variance = float(np.var(samples, ddof=1)) if samples.size > 1 else float("nan")

        output[window] = {
            "midpoint": WINDOW_MIDPOINTS[window],
            "ratio": float(point),
            "raw_ratio": float(raw),
            "ci_low": float(low), "ci_high": float(high),
            "variance": variance,
            "excludes_one": bool(not (low <= 1.0 <= high)),
            "observations": int(len(block)),
            "matches": int(len(unique)),
        }
    return output


def fit_ramp_to(ratios: dict[int, dict[str, Any]]) -> RampParameters:
    """Ajusta la rampa por mínimos cuadrados ponderados por 1/varianza."""

    midpoints = np.array([e["midpoint"] for e in ratios.values()], dtype=float)
    observed = np.array([e["ratio"] for e in ratios.values()], dtype=float)
    variances = np.array([e["variance"] for e in ratios.values()], dtype=float)
    weights = np.where(
        np.isfinite(variances) & (variances > 0.0), 1.0 / variances, 1.0)

    def objective(vector: np.ndarray) -> float:
        onset, curvature, gain = vector
        if not (0.0 <= onset < 0.95) or curvature <= 0.0 or gain < 0.0:
            return 1e9
        candidate = RampParameters(
            onset_fraction=float(onset), curvature=float(curvature),
            chasing_gain=float(gain))
        predicted = np.array([ramp_ratio(candidate, m) for m in midpoints])
        return float(np.sum(weights * (observed - predicted) ** 2))

    best, best_score = None, float("inf")
    for onset in (0.30, 0.45, 0.55, 0.65):
        for curvature in (0.8, 1.0, 1.5, 2.0):
            for gain in (0.05, 0.12, 0.20):
                result = minimize(
                    objective, x0=np.array([onset, curvature, gain]),
                    method="Nelder-Mead",
                    options={"xatol": 1e-9, "fatol": 1e-13, "maxiter": 6000})
                if result.fun < best_score:
                    best_score, best = float(result.fun), result.x
    if best is None:
        raise RuntimeError("goal_rate_calibration_did_not_converge")
    return RampParameters(
        onset_fraction=float(np.clip(best[0], 0.0, 0.95)),
        curvature=float(max(best[1], 1e-6)),
        chasing_gain=float(max(best[2], 0.0)))


def _weighted_error(ratios: dict[int, dict[str, Any]], predictor: Any) -> float:
    total = 0.0
    for entry in ratios.values():
        variance = entry["variance"]
        weight = 1.0 / variance if (
            np.isfinite(variance) and variance > 0.0) else 1.0
        total += weight * (entry["ratio"] - predictor(entry["midpoint"])) ** 2
    return float(total)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    print("cargando split fit (confirmation prohibido)...", flush=True)
    fit_rows = _load(frozenset({"fit"}))
    selection_rows = _load(frozenset({"selection"}))
    print(f"  fit={len(fit_rows)} selection={len(selection_rows)}", flush=True)

    fit_ratios = controlled_ratios(fit_rows)
    print("\nratio de tasa de gol controlado por fuerza (fit):", flush=True)
    for window, entry in sorted(fit_ratios.items()):
        mark = "  <-- excluye 1.0" if entry["excludes_one"] else ""
        print(f"  min {entry['midpoint']:>5.1f}: crudo={entry['raw_ratio']:.4f} "
              f"controlado={entry['ratio']:.4f} "
              f"IC95%[{entry['ci_low']:.3f},{entry['ci_high']:.3f}] "
              f"lineal={linear_ratio(entry['midpoint']):.4f}{mark}", flush=True)

    parameters = fit_ramp_to(fit_ratios)
    print(f"\nrampa ajustada: onset={parameters.onset_fraction:.4f} "
          f"(min {parameters.onset_fraction * 90:.1f}) "
          f"curvature={parameters.curvature:.4f} "
          f"gain={parameters.chasing_gain:.4f}", flush=True)

    selection_ratios = controlled_ratios(selection_rows)
    ramp_error = _weighted_error(
        selection_ratios, lambda m: ramp_ratio(parameters, m))
    linear_error = _weighted_error(selection_ratios, linear_ratio)
    ramp_wins = ramp_error < linear_error
    print(f"\nerror ponderado en selection: rampa={ramp_error:.2f} "
          f"lineal={linear_error:.2f} -> "
          f"{'RAMPA' if ramp_wins else 'LINEAL'} gana", flush=True)

    payload = {
        "classification": "ready_for_gate" if ramp_wins else "rejected_for_revision",
        "target_quantity": "goal_rate_ratio_controlled_for_own_and_rival_strength",
        "why_this_target": (
            "_score_factors multiplica intensidad de gol, no de presión; y el "
            "ratio crudo de gol está confundido por fuerza -quien gana suele "
            "ser el equipo mejor-, de modo que controlarlo invierte el signo"
        ),
        "protocol": "fit_estimates_selection_chooses_confirmation_untouched",
        "coverage": {
            "fit_rows": len(fit_rows), "selection_rows": len(selection_rows),
            "confirmation_rows_read": 0,
        },
        "controlled_ratios": {
            "fit": {str(k): v for k, v in fit_ratios.items()},
            "selection": {str(k): v for k, v in selection_ratios.items()},
        },
        "fitted_parameters": parameters_as_dict(parameters),
        "model_comparison": {
            "ramp_weighted_error_selection": ramp_error,
            "linear_weighted_error_selection": linear_error,
            "ramp_wins": ramp_wins,
        },
        "source_sha256": hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "calibration.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8")
    print(f"\nclasificación: {payload['classification']}", flush=True)
    print(f"artefacto: {args.output / 'calibration.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
