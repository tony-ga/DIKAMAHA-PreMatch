"""IC bootstrap pareado del ruido de proceso de Kalman (`DEC-197`).

El barrido de `fit_kalman_process_noise.py` compara medias. Una media no dice si
la diferencia es distinguible del azar, y con bloques de ~60 partidos esa
pregunta domina el resultado: `DEC-006` exige intervalo bootstrap con el partido
completo como unidad IID (R7 de `model_composition v1`).

Este script vuelve a puntuar el bloque de confirmación con las dos tasas,
guarda la pérdida **por partido** y remuestrea los partidos en pares -misma
selección de índices para ambas tasas-, que es lo que corresponde cuando las dos
mediciones provienen de los mismos partidos.

Uso:
    python -m scripts.bootstrap_kalman_process_noise \\
        --matches tmp/dec197/matches.csv --candidate-rate 0.02

# Requirements:
#   numpy>=1.24
#   pandas>=2.0

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import replace
from pathlib import Path
from typing import Any, Sequence
from unittest.mock import patch

import numpy as np
import pandas as pd

from src.kalman_v2 import KalmanV2Config
from src.official_goal_chain import DixonColesKalmanGoalModel

HOME_ADVANTAGE_FRACTION = 0.2


def _load(path: Path) -> list[dict[str, Any]]:
    """Carga el histórico y lo normaliza al contrato de la cadena."""

    frame = pd.read_csv(path)
    frame["match_date"] = pd.to_datetime(frame["match_date"], utc=True)
    frame = frame.sort_values(["match_date", "match_id"]).reset_index(drop=True)
    return [
        {
            "match_id": int(record["match_id"]),
            "match_date": record["match_date"].isoformat(),
            "home_team_id": int(record["home_team_id"]),
            "away_team_id": int(record["away_team_id"]),
            "home_goals": int(record["home_goals"]),
            "away_goals": int(record["away_goals"]),
        }
        for record in frame.to_dict("records")
    ]


def _outcome(row: dict[str, Any]) -> str:
    """Deriva el resultado 1X2 observado."""

    if row["home_goals"] > row["away_goals"]:
        return "1"
    if row["home_goals"] < row["away_goals"]:
        return "2"
    return "X"


def _per_match_losses(
    rows: Sequence[dict[str, Any]], indices: Sequence[int], rate: float,
) -> dict[int, tuple[float, float]]:
    """Devuelve `{match_id: (log_loss, brier)}` para una tasa dada."""

    config = KalmanV2Config(
        process_noise_attack=rate,
        process_noise_defense=rate,
        process_noise_home_advantage=rate * HOME_ADVANTAGE_FRACTION,
    )
    losses: dict[int, tuple[float, float]] = {}

    with patch(
        "src.official_goal_chain.KalmanV2Config",
        lambda *args, **kwargs: replace(config),
    ):
        model = DixonColesKalmanGoalModel()
        for index in indices:
            target = rows[index]
            try:
                prediction = model.predict(
                    rows[:index],
                    int(target["home_team_id"]),
                    int(target["away_team_id"]),
                    target["match_date"],
                )
            except Exception:  # noqa: BLE001
                continue
            probabilities = {
                "1": prediction.probability_home,
                "X": prediction.probability_draw,
                "2": prediction.probability_away,
            }
            observed = _outcome(target)
            losses[int(target["match_id"])] = (
                -math.log(max(probabilities[observed], 1e-15)),
                sum((value - float(label == observed)) ** 2
                    for label, value in probabilities.items()),
            )
    return losses


def _paired_bootstrap(
    deltas: np.ndarray, replicates: int, seed: int,
) -> dict[str, float]:
    """IC95% percentil del delta medio, remuestreando partidos."""

    rng = np.random.default_rng(seed)
    size = len(deltas)
    means = np.empty(replicates, dtype=float)
    for index in range(replicates):
        means[index] = float(np.mean(rng.choice(deltas, size=size, replace=True)))
    return {
        "mean": float(np.mean(deltas)),
        "ci_low": float(np.percentile(means, 2.5)),
        "ci_high": float(np.percentile(means, 97.5)),
        "crosses_zero": bool(
            np.percentile(means, 2.5) <= 0.0 <= np.percentile(means, 97.5)),
    }


def main() -> None:
    """Compara baseline y candidato con IC bootstrap pareado."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--candidate-rate", type=float, required=True)
    parser.add_argument("--history-start", type=int, default=250)
    parser.add_argument("--replicates", type=int, default=10000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output", type=Path,
                        default=Path("tmp/dec197/process_noise_bootstrap.json"))
    args = parser.parse_args()

    rows = _load(args.matches)
    evaluable = list(range(args.history_start, len(rows)))
    midpoint = args.history_start + len(evaluable) // 2
    confirmation = list(range(midpoint, len(rows)))

    print(f"confirmación: {len(confirmation)} partidos", flush=True)

    baseline = _per_match_losses(rows, confirmation, 0.0)
    candidate = _per_match_losses(rows, confirmation, args.candidate_rate)
    shared = sorted(set(baseline) & set(candidate))
    print(f"partidos puntuados por ambas tasas: {len(shared)}", flush=True)

    # Delta positivo = el candidato mejora (pierde menos que el baseline).
    log_loss_deltas = np.array(
        [baseline[key][0] - candidate[key][0] for key in shared], dtype=float)
    brier_deltas = np.array(
        [baseline[key][1] - candidate[key][1] for key in shared], dtype=float)

    payload = {
        "unit": "complete_match",
        "candidate_rate": args.candidate_rate,
        "matches": len(shared),
        "replicates": args.replicates,
        "log_loss": _paired_bootstrap(
            log_loss_deltas, args.replicates, args.seed),
        "brier": _paired_bootstrap(brier_deltas, args.replicates, args.seed),
        "baseline_log_loss": float(
            np.mean([baseline[key][0] for key in shared])),
        "candidate_log_loss": float(
            np.mean([candidate[key][0] for key in shared])),
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    for metric in ("log_loss", "brier"):
        block = payload[metric]
        verdict = ("cruza cero — indistinguible"
                   if block["crosses_zero"]
                   else ("mejora confirmada" if block["mean"] > 0
                         else "degradación confirmada"))
        print(f"\n{metric}: delta medio {block['mean']:+.6f}", flush=True)
        print(f"  IC95% [{block['ci_low']:+.6f}, {block['ci_high']:+.6f}] "
              f"→ {verdict}", flush=True)

    print(f"\nartefacto: {args.output}", flush=True)


if __name__ == "__main__":
    main()
