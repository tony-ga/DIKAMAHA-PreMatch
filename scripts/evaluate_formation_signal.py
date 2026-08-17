"""¿Aporta la formación información sobre el resultado? (D.9 de la auditoría).

El encuadre original era el teorema minimax: dos entrenadores eligiendo formación
en un juego de suma cero. Ese marco describe cómo *deberían* elegir, no si su
elección **predice** algo. La pregunta medible, y la única que puede promover un
candidato, es otra: conocidas las dos formaciones antes del kickoff, ¿mejora la
probabilidad que ya emite la cadena Dixon-Coles/Kalman?

Método. Sobre `selection` se mide, para cada formación, cuánto se desvía el
resultado observado de lo que la cadena predijo -un residuo por clase 1/X/2-.
Ese residuo se contrae hacia cero según la muestra de la formación, porque una
formación vista ocho veces no puede sostener su propio ajuste. En `confirmation`
se aplica el ajuste ya congelado y se compara contra la cadena sin tocar.

Si las formaciones no aportan nada, los residuos son ruido, la contracción los
anula y el resultado será indistinguible. Si aportan, el ajuste mejora.

Uso:
    python -m scripts.evaluate_formation_signal

# Requirements:
#   numpy>=1.24

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from src.kalman_v2 import poisson_matrix
from src.official_goal_chain import BLEND_WEIGHT_DIXON_COLES

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PREDICTIONS = ROOT / "artifacts/walkforward_predictions/baseline.jsonl"
DEFAULT_FORMATIONS = ROOT / "artifacts/match_formations/formations.jsonl"
DEFAULT_OUTPUT = ROOT / "artifacts/candidate_evaluation"

LABELS = ("1", "X", "2")
SHRINKAGE_GRID = (5.0, 15.0, 40.0, 100.0, 300.0)
MATERIALITY = 1e-9


def _bootstrap(deltas: np.ndarray, replicates: int = 10000, seed: int = 42):
    """IC95% percentil del delta medio remuestreando partidos."""

    rng = np.random.default_rng(seed)
    means = np.array([
        float(np.mean(rng.choice(deltas, size=len(deltas), replace=True)))
        for _ in range(replicates)])
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    mean = float(np.mean(deltas))
    if abs(mean) < MATERIALITY and max(abs(low), abs(high)) < MATERIALITY:
        verdict = "sin efecto medible"
    elif low <= 0.0 <= high:
        verdict = "indistinguible"
    else:
        verdict = "mejora confirmada" if mean > 0 else "degradación confirmada"
    return {
        "mean": mean, "ci_low": low, "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high), "verdict": verdict,
    }


def _markets(row: dict[str, Any]) -> dict[str, float]:
    """Recompone 1X2 al peso de producción, sin calibrar."""

    dc_home, dc_away = row["lambda_dixon_coles"]
    k_home, k_away = row["lambda_kalman"]
    weight = BLEND_WEIGHT_DIXON_COLES
    home = math.exp(weight * math.log(dc_home) + (1 - weight) * math.log(k_home))
    away = math.exp(weight * math.log(dc_away) + (1 - weight) * math.log(k_away))
    matrix = poisson_matrix(home, away, 12, float(row["tau_dc"]), True)
    return {
        "1": float(np.tril(matrix, -1).sum()),
        "X": float(np.trace(matrix)),
        "2": float(np.triu(matrix, 1).sum()),
    }


def _apply(base: dict[str, float], adjust: dict[str, float]) -> dict[str, float]:
    """Aplica un ajuste aditivo y renormaliza sin salirse de [0,1]."""

    raw = {
        label: max(base[label] + adjust.get(label, 0.0), 1e-6)
        for label in LABELS
    }
    total = sum(raw.values())
    return {label: value / total for label, value in raw.items()}


def _fit_adjustments(
    rows: Sequence[dict[str, Any]], key: str, shrinkage: float,
) -> dict[str, dict[str, float]]:
    """Residuo medio por formación, contraído hacia cero según la muestra."""

    sums: dict[str, dict[str, float]] = defaultdict(
        lambda: {label: 0.0 for label in LABELS})
    counts: dict[str, int] = defaultdict(int)

    for row in rows:
        formation = row[key]
        if not formation:
            continue
        base = row["_markets"]
        for label in LABELS:
            observed = float(row["outcome"] == label)
            sums[formation][label] += observed - base[label]
        counts[formation] += 1

    return {
        formation: {
            # La contracción es `n/(n+k)`: una formación con pocas apariciones
            # conserva casi nada de su residuo, que a esa escala es ruido.
            label: value / (counts[formation] + shrinkage)
            for label, value in totals.items()
        }
        for formation, totals in sums.items()
    }


def _score(
    rows: Sequence[dict[str, Any]],
    home_adjust: dict[str, dict[str, float]],
    away_adjust: dict[str, dict[str, float]],
) -> np.ndarray:
    """Log-loss por partido con los ajustes de formación aplicados."""

    losses = []
    for row in rows:
        adjust = {label: 0.0 for label in LABELS}
        for source, key in ((home_adjust, "home"), (away_adjust, "away")):
            values = source.get(row[key] or "")
            if values:
                for label in LABELS:
                    adjust[label] += values[label]
        probabilities = _apply(row["_markets"], adjust)
        losses.append(-math.log(max(probabilities[row["outcome"]], 1e-15)))
    return np.array(losses)


def main() -> None:
    """Elige la contracción en selección y reporta en confirmación."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--predictions", type=Path, default=DEFAULT_PREDICTIONS)
    parser.add_argument("--formations", type=Path, default=DEFAULT_FORMATIONS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    formations: dict[int, dict[str, Any]] = {}
    with args.formations.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                formations[int(row["match_id"])] = row

    rows: list[dict[str, Any]] = []
    with args.predictions.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            found = formations.get(int(row["match_id"]))
            if not found or not found.get("home") or not found.get("away"):
                continue
            row["home"], row["away"] = found["home"], found["away"]
            row["_markets"] = _markets(row)
            rows.append(row)

    selection = [row for row in rows if row["split"] == "selection"]
    confirmation = [row for row in rows if row["split"] == "confirmation"]
    distinct = len({row["home"] for row in rows} | {row["away"] for row in rows})
    print(f"con formación: selección={len(selection)} "
          f"confirmación={len(confirmation)}", flush=True)
    print(f"formaciones distintas: {distinct}\n", flush=True)

    scores: dict[float, float] = {}
    for shrinkage in SHRINKAGE_GRID:
        home_adjust = _fit_adjustments(selection, "home", shrinkage)
        away_adjust = _fit_adjustments(selection, "away", shrinkage)
        scores[shrinkage] = float(np.mean(
            _score(selection, home_adjust, away_adjust)))
        print(f"  selección k={shrinkage:<6} log_loss={scores[shrinkage]:.6f}",
              flush=True)

    best = min(scores, key=scores.get)
    print(f"\ncontracción elegida: k={best}\n", flush=True)

    home_adjust = _fit_adjustments(selection, "home", best)
    away_adjust = _fit_adjustments(selection, "away", best)
    baseline = _score(confirmation, {}, {})
    candidate = _score(confirmation, home_adjust, away_adjust)
    stats = _bootstrap(baseline - candidate)

    report = {
        "target": "match_result_1x2",
        "distinct_formations": distinct,
        "selection_matches": len(selection),
        "confirmation_matches": len(confirmation),
        "selection_log_loss_by_shrinkage": scores,
        "selected_shrinkage": best,
        "baseline_log_loss": float(np.mean(baseline)),
        "candidate_log_loss": float(np.mean(candidate)),
        "formation_vs_baseline": stats,
    }

    print(f"confirmación ({len(confirmation)} partidos)", flush=True)
    print(f"  log-loss {report['baseline_log_loss']:.6f} → "
          f"{report['candidate_log_loss']:.6f}", flush=True)
    print(f"  delta {stats['mean']:+.6f} "
          f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
          f"→ {stats['verdict']}", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "formation_signal.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nartefacto: {path}", flush=True)


if __name__ == "__main__":
    main()
