"""Reevalúa el ruido de proceso de Kalman sobre el corpus completo (`DEC-197`).

`DEC-197` se cerró sin conclusión midiendo sobre 46 partidos de una liga, donde
el error estándar de log-loss (~0.076) era un orden de magnitud mayor que
cualquier delta plausible. `DEC-201` mostró que esa muestra invertía el signo de
un resultado real, así que el rechazo anterior no era evidencia contra la tasa:
era ausencia de evidencia.

Aquí se compara sobre 1,845 partidos de confirmación en 30 ligas. Las
probabilidades se recomponen desde las **lambdas crudas** de cada componente, no
desde el campo `blended` guardado: así todas las tasas se evalúan con el mismo
peso de mezcla y sin calibración, de modo que la única diferencia entre ellas es
el estado de Kalman, que es lo que la tasa cambia.

Uso:
    python -m scripts.evaluate_process_noise_full

# Requirements:
#   numpy>=1.24

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from src.kalman_v2 import poisson_matrix
from src.official_goal_chain import BLEND_WEIGHT_DIXON_COLES

ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS = ROOT / "artifacts/walkforward_predictions"
DEFAULT_OUTPUT = ROOT / "artifacts/dec_197_kalman_process_noise"


def _load(path: Path) -> dict[int, dict[str, Any]]:
    """Carga predicciones indexadas por partido."""

    rows: dict[int, dict[str, Any]] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                row = json.loads(line)
                rows[int(row["match_id"])] = row
    return rows


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


def _losses(
    rows: dict[int, dict[str, Any]], keys: Sequence[int],
) -> tuple[np.ndarray, np.ndarray]:
    """Log-loss y Brier por partido."""

    log_losses, briers = [], []
    for key in keys:
        row = rows[key]
        markets = _markets(row)
        observed = row["outcome"]
        log_losses.append(-math.log(max(markets[observed], 1e-15)))
        briers.append(sum(
            (value - float(label == observed)) ** 2
            for label, value in markets.items()))
    return np.array(log_losses), np.array(briers)


def _bootstrap(deltas: np.ndarray, replicates: int = 10000, seed: int = 42):
    """IC95% percentil del delta medio remuestreando partidos."""

    rng = np.random.default_rng(seed)
    means = np.array([
        float(np.mean(rng.choice(deltas, size=len(deltas), replace=True)))
        for _ in range(replicates)])
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return {
        "mean": float(np.mean(deltas)),
        "ci_low": low, "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high),
        "verdict": (
            "indistinguible" if low <= 0.0 <= high
            else ("mejora confirmada" if np.mean(deltas) > 0
                  else "degradación confirmada")),
    }


def main() -> None:
    """Selecciona la tasa en selección y la confirma aparte."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    runs = {
        0.0: _load(PREDICTIONS / "baseline.jsonl"),
        0.005: _load(PREDICTIONS / "rate_0.005.jsonl"),
        0.02: _load(PREDICTIONS / "rate_0.02.jsonl"),
    }

    common = set.intersection(*(set(run) for run in runs.values()))
    selection = sorted(
        key for key in common if runs[0.0][key]["split"] == "selection")
    confirmation = sorted(
        key for key in common if runs[0.0][key]["split"] == "confirmation")

    print(f"selección={len(selection)} confirmación={len(confirmation)}\n",
          flush=True)

    selection_scores = {}
    for rate, rows in runs.items():
        log_losses, _ = _losses(rows, selection)
        selection_scores[rate] = float(np.mean(log_losses))
        print(f"  selección rate={rate:<8} log_loss={selection_scores[rate]:.6f}",
              flush=True)

    best = min(selection_scores, key=selection_scores.get)
    print(f"\nmejor en selección: rate={best}\n", flush=True)

    base_log, base_brier = _losses(runs[0.0], confirmation)
    report: dict[str, Any] = {
        "corpus": "phase_74_match_level",
        "unit": "complete_match",
        "blend_weight": BLEND_WEIGHT_DIXON_COLES,
        "calibrated": False,
        "selection_matches": len(selection),
        "confirmation_matches": len(confirmation),
        "selection_log_loss": selection_scores,
        "selected_rate": best,
        "confirmation": {},
    }

    for rate in sorted(runs):
        if rate == 0.0:
            continue
        cand_log, cand_brier = _losses(runs[rate], confirmation)
        block = {
            "baseline_log_loss": float(np.mean(base_log)),
            "candidate_log_loss": float(np.mean(cand_log)),
            "log_loss": _bootstrap(base_log - cand_log),
            "brier": _bootstrap(base_brier - cand_brier),
        }
        report["confirmation"][str(rate)] = block
        print(f"--- confirmación rate={rate} ---", flush=True)
        for metric in ("log_loss", "brier"):
            stats = block[metric]
            print(f"  {metric}: {stats['mean']:+.6f} "
                  f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
                  f"→ {stats['verdict']}", flush=True)
        print(flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "full_corpus_reevaluation.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"artefacto: {path}", flush=True)


if __name__ == "__main__":
    main()
