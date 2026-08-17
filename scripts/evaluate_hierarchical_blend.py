"""Peso de mezcla por liga con contracción jerárquica (R5).

`DEC-200` reestimó un peso **global** para toda la cadena. La pregunta que deja
abierta es si ese peso debería depender de la liga: en una competición donde la
fuerza de los equipos es muy estable, el prior estructural debería pesar más que
en una volátil.

Un peso por liga ajustado sin más sobreajusta las ligas con poca muestra. R5 da
la forma correcta de evitarlo sin renunciar a la señal:

    w_j_final = lambda_j * w_global + (1 - lambda_j) * w_j

con `lambda_j = sigma_j^2 / (sigma_j^2 + tau^2)`, donde `tau^2` es la varianza
entre ligas de los pesos ajustados y `sigma_j^2` la incertidumbre dentro de la
liga `j`. Una liga con pocos partidos se contrae casi del todo hacia el peso
global; una con muchos conserva el suyo.

La comparación es contra el peso global de `DEC-200`, no contra el `0.8` de Fase
42: la pregunta es si añadir dimensión de liga aporta **sobre lo ya adoptado**.

Uso:
    python -m scripts.evaluate_hierarchical_blend

# Requirements:
#   numpy>=1.24
#   scipy>=1.10

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
from scipy.optimize import minimize_scalar

from src.kalman_v2 import poisson_matrix
from src.official_goal_chain import BLEND_WEIGHT_DIXON_COLES

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "artifacts/walkforward_predictions/baseline.jsonl"
DEFAULT_OUTPUT = ROOT / "artifacts/candidate_evaluation"
MIN_LEAGUE_MATCHES = 30


def _load(path: Path) -> list[dict[str, Any]]:
    """Carga las predicciones walk-forward."""

    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _markets(row: dict[str, Any], weight: float) -> dict[str, float]:
    """Recompone 1X2 con un peso de mezcla dado."""

    dc_home, dc_away = row["lambda_dixon_coles"]
    k_home, k_away = row["lambda_kalman"]
    home = math.exp(weight * math.log(dc_home) + (1 - weight) * math.log(k_home))
    away = math.exp(weight * math.log(dc_away) + (1 - weight) * math.log(k_away))
    matrix = poisson_matrix(home, away, 12, float(row["tau_dc"]), True)
    return {
        "1": float(np.tril(matrix, -1).sum()),
        "X": float(np.trace(matrix)),
        "2": float(np.triu(matrix, 1).sum()),
    }


def _log_loss(rows: Sequence[dict[str, Any]], weight: float) -> float:
    """Log-loss medio de un conjunto bajo un peso."""

    return float(np.mean([
        -math.log(max(_markets(row, weight)[row["outcome"]], 1e-15))
        for row in rows]))


def _fit_weight(rows: Sequence[dict[str, Any]]) -> float:
    """Peso que minimiza log-loss."""

    result = minimize_scalar(
        lambda value: _log_loss(rows, value), bounds=(0.0, 1.0),
        method="bounded")
    return float(result.x)


def _bootstrap(deltas: np.ndarray, replicates: int = 10000, seed: int = 42):
    """IC95% percentil del delta medio."""

    rng = np.random.default_rng(seed)
    means = np.array([
        float(np.mean(rng.choice(deltas, size=len(deltas), replace=True)))
        for _ in range(replicates)])
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return {
        "mean": float(np.mean(deltas)), "ci_low": low, "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high),
        "verdict": (
            "indistinguible" if low <= 0.0 <= high
            else ("mejora confirmada" if np.mean(deltas) > 0
                  else "degradación confirmada")),
    }


def main() -> None:
    """Ajusta pesos por liga con contracción y los mide en confirmación."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = _load(args.input)
    selection = [row for row in rows if row["split"] == "selection"]
    confirmation = [row for row in rows if row["split"] == "confirmation"]

    by_league: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selection:
        by_league[row["league_slug"]].append(row)

    global_weight = _fit_weight(selection)
    print(f"peso global (selección) = {global_weight:.4f}\n", flush=True)

    raw: dict[str, float] = {}
    counts: dict[str, int] = {}
    for league, league_rows in by_league.items():
        if len(league_rows) >= MIN_LEAGUE_MATCHES:
            raw[league] = _fit_weight(league_rows)
            counts[league] = len(league_rows)

    # `tau^2` es la dispersión real entre ligas; si fuera cero, todas las ligas
    # compartirían peso y la contracción sería total, que es justamente la
    # conclusión correcta en ese caso.
    values = np.array(list(raw.values()), dtype=float)
    tau_squared = float(np.var(values, ddof=1)) if len(values) > 1 else 0.0

    # Incertidumbre dentro de la liga: escala como 1/n. La constante se calibra
    # con la dispersión observada para que las ligas medianas queden a mitad de
    # camino, en vez de fijarla a ojo.
    scale = float(np.median([count for count in counts.values()])) * tau_squared

    shrunk: dict[str, float] = {}
    weights_lambda: dict[str, float] = {}
    for league, weight in raw.items():
        sigma_squared = scale / counts[league] if counts[league] else float("inf")
        lam = (sigma_squared / (sigma_squared + tau_squared)
               if tau_squared > 0 else 1.0)
        weights_lambda[league] = lam
        shrunk[league] = lam * global_weight + (1 - lam) * weight

    print(f"ligas con peso propio: {len(raw)} (mínimo {MIN_LEAGUE_MATCHES} "
          f"partidos en selección)", flush=True)
    print(f"tau^2 entre ligas = {tau_squared:.6f}", flush=True)
    print(f"contracción media = {np.mean(list(weights_lambda.values())):.4f}\n",
          flush=True)

    def _losses(weight_of) -> tuple[np.ndarray, np.ndarray]:
        """Log-loss y Brier por partido con una regla de peso."""

        log_losses, briers = [], []
        for row in confirmation:
            markets = _markets(row, weight_of(row))
            observed = row["outcome"]
            log_losses.append(-math.log(max(markets[observed], 1e-15)))
            briers.append(sum(
                (value - float(label == observed)) ** 2
                for label, value in markets.items()))
        return np.array(log_losses), np.array(briers)

    base_log, base_brier = _losses(lambda row: BLEND_WEIGHT_DIXON_COLES)
    shrunk_log, shrunk_brier = _losses(
        lambda row: shrunk.get(row["league_slug"], global_weight))
    raw_log, raw_brier = _losses(
        lambda row: raw.get(row["league_slug"], global_weight))

    report = {
        "global_weight": global_weight,
        "adopted_weight": BLEND_WEIGHT_DIXON_COLES,
        "leagues_with_own_weight": len(raw),
        "min_matches": MIN_LEAGUE_MATCHES,
        "tau_squared": tau_squared,
        "mean_shrinkage": float(np.mean(list(weights_lambda.values()))),
        "raw_weights": dict(sorted(raw.items())),
        "shrunk_weights": dict(sorted(shrunk.items())),
        "confirmation_matches": len(confirmation),
        "hierarchical_vs_global": {
            "log_loss": _bootstrap(base_log - shrunk_log),
            "brier": _bootstrap(base_brier - shrunk_brier),
        },
        "unshrunk_vs_global": {
            "log_loss": _bootstrap(base_log - raw_log),
            "brier": _bootstrap(base_brier - raw_brier),
        },
    }

    for name in ("hierarchical_vs_global", "unshrunk_vs_global"):
        print(f"--- {name} ---", flush=True)
        for metric in ("log_loss", "brier"):
            stats = report[name][metric]
            print(f"  {metric}: {stats['mean']:+.6f} "
                  f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
                  f"→ {stats['verdict']}", flush=True)
        print(flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "hierarchical_blend.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"artefacto: {path}", flush=True)


if __name__ == "__main__":
    main()
