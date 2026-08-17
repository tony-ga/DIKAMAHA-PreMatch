"""Predicción de conteos con estructura de factores (continuación de `DEC-203`).

El filtro de Marchenko-Pastur encontró que tres de diez componentes de la
correlación entre conteos superan el ruido de muestreo y concentran el 72.5% de
la varianza. Si esa estructura es real, el perfil de un equipo -sus medias
históricas de córners, tiros, tiros a puerta, tarjetas y faltas- vive
aproximadamente en un subespacio de dimensión baja, y proyectarlo sobre ese
subespacio debería **quitarle ruido de muestreo sin quitarle señal**.

La consecuencia práctica es concreta: hoy cada métrica se predice desde su propia
historia. Si el ritmo de un partido mueve tiros, córners y faltas a la vez,
entonces el historial de tiros de un equipo informa sobre sus córners, y
predecirlas por separado desperdicia información.

Comparación, todo causal y con la partición congelada de Fase 74:

- **baseline**: media histórica del propio equipo en esa métrica, contraída hacia
  la liga con el mismo `shrinkage` fijo que usa el proyecto;
- **candidato**: ese mismo perfil, proyectado sobre los `k` componentes
  principales estimados **sólo en el bloque `fit`**, y reconstruido.

Los componentes se estiman en `fit`, el número `k` se elige en `selection`, y el
resultado se reporta en `confirmation`: tres bloques, tres funciones distintas
(R2).

Uso:
    python -m scripts.evaluate_factor_counts

# Requirements:
#   numpy>=1.24
#   pandas>=2.0

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "artifacts/match_level_corpus/matches.csv"
DEFAULT_OUTPUT = ROOT / "artifacts/candidate_evaluation"

METRICS = ("corners", "shots", "shots_on_target", "yellow_cards", "fouls")
FIXED_SHRINKAGE = 5.0
MIN_HISTORY = 4


# Cota de materialidad. Un intervalo puede no contener el cero y aun así
# describir una diferencia numéricamente irrelevante: si el candidato es la
# identidad salvo error de redondeo, los deltas son del orden de 1e-16 y el
# bootstrap devuelve un intervalo estrictamente positivo por pura aritmética de
# punto flotante. Sin esta cota, un cambio que no hace nada se etiquetaría
# "mejora confirmada", que es precisamente el mecanismo de una promoción falsa.
MATERIALITY = 1e-9


def _bootstrap(deltas: np.ndarray, replicates: int = 10000, seed: int = 42):
    """IC95% percentil del delta medio remuestreando observaciones."""

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
    elif mean > 0:
        verdict = "mejora confirmada"
    else:
        verdict = "degradación confirmada"

    return {
        "mean": mean, "ci_low": low, "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high),
        "materially_zero": bool(abs(mean) < MATERIALITY),
        "verdict": verdict,
    }


def _observations(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Aplana a observaciones equipo-partido con las cinco métricas."""

    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        for side in ("home", "away"):
            rows.append({
                "match_id": record["match_id"],
                "match_date": record["match_date"],
                "league": record["league_slug"],
                "split": record["split"],
                "team_id": record[f"{side}_team_id"],
                "values": np.array(
                    [float(record[f"{side}_{metric}"]) for metric in METRICS]),
            })
    rows.sort(key=lambda item: (item["match_date"], item["match_id"]))
    return rows


def _components(rows: list[dict[str, Any]]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Estima ejes principales de los perfiles de equipo en el bloque `fit`."""

    totals: dict[tuple[str, int], list[np.ndarray]] = defaultdict(list)
    for row in rows:
        if row["split"] == "fit":
            totals[(row["league"], row["team_id"])].append(row["values"])

    profiles = np.array([
        np.mean(values, axis=0) for values in totals.values()
        if len(values) >= MIN_HISTORY
    ])
    center = profiles.mean(axis=0)
    scale = profiles.std(axis=0, ddof=1) + 1e-12
    standardized = (profiles - center) / scale
    _, _, vectors = np.linalg.svd(standardized, full_matrices=False)
    return center, scale, vectors


def _run(rows: list[dict[str, Any]], center, scale, vectors, rank: int):
    """Recorre el corpus emitiendo error del baseline y del candidato."""

    league_sum: dict[str, np.ndarray] = defaultdict(
        lambda: np.zeros(len(METRICS)))
    league_count: dict[str, int] = defaultdict(int)
    team_sum: dict[tuple[str, int], np.ndarray] = defaultdict(
        lambda: np.zeros(len(METRICS)))
    team_count: dict[tuple[str, int], int] = defaultdict(int)

    basis = vectors[:rank]
    baseline_errors, factor_errors, splits = [], [], []

    for row in rows:
        league, team = row["league"], (row["league"], row["team_id"])
        observed = row["values"]

        if league_count[league] >= 30 and team_count[team] >= MIN_HISTORY:
            league_mean = league_sum[league] / league_count[league]
            n = team_count[team]
            shrunk = ((team_sum[team] + FIXED_SHRINKAGE * league_mean)
                      / (n + FIXED_SHRINKAGE))

            # Proyectar el perfil ya contraído sobre el subespacio de señal:
            # lo que quede fuera de esos ejes es, según Marchenko-Pastur,
            # indistinguible del ruido de muestreo.
            standardized = (shrunk - center) / scale
            reconstructed = basis.T @ (basis @ standardized)
            factored = reconstructed * scale + center
            factored = np.maximum(factored, 0.0)

            baseline_errors.append(float(np.sum((shrunk - observed) ** 2)))
            factor_errors.append(float(np.sum((factored - observed) ** 2)))
            splits.append(row["split"])

        league_sum[league] = league_sum[league] + observed
        league_count[league] += 1
        team_sum[team] = team_sum[team] + observed
        team_count[team] += 1

    return (np.array(baseline_errors), np.array(factor_errors),
            np.array(splits))


def main() -> None:
    """Elige el rango en selección y reporta en confirmación."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    frame = pd.read_csv(args.corpus).sort_values(["match_date", "match_id"])
    rows = _observations(frame)
    center, scale, vectors = _components(rows)

    selection_scores: dict[int, float] = {}
    cached: dict[int, tuple] = {}
    for rank in range(1, len(METRICS) + 1):
        baseline, factored, splits = _run(rows, center, scale, vectors, rank)
        cached[rank] = (baseline, factored, splits)
        mask = splits == "selection"
        selection_scores[rank] = float(np.mean(factored[mask]))
        print(f"  selección rango={rank} MSE={selection_scores[rank]:.4f} "
              f"(baseline {np.mean(baseline[mask]):.4f})", flush=True)

    best = min(selection_scores, key=selection_scores.get)
    print(f"\nrango elegido en selección: {best}\n", flush=True)

    baseline, factored, splits = cached[best]
    mask = splits == "confirmation"
    deltas = baseline[mask] - factored[mask]
    stats = _bootstrap(deltas)

    report = {
        "metrics": list(METRICS),
        "selected_rank": best,
        "selection_mse_by_rank": selection_scores,
        "confirmation_observations": int(mask.sum()),
        "baseline_mse": float(np.mean(baseline[mask])),
        "factor_mse": float(np.mean(factored[mask])),
        "factor_vs_baseline": stats,
    }

    print(f"confirmación ({report['confirmation_observations']} obs)", flush=True)
    print(f"  MSE baseline {report['baseline_mse']:.4f} → "
          f"factores {report['factor_mse']:.4f}", flush=True)
    print(f"  delta {stats['mean']:+.6f} "
          f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
          f"→ {stats['verdict']}", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "factor_counts.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nartefacto: {path}", flush=True)


if __name__ == "__main__":
    main()
