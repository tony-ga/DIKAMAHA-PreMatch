"""Ajusta la tasa de ruido de proceso de Kalman (`DEC-197`).

`DEC-197` exige, para adoptar el paso de predicción, un backtest walk-forward que
muestre que el filtro con `Q` no degrada log-loss ni Brier frente al actual, con
la unidad IID de `DEC-006` -el partido completo-.

Protocolo, en el orden que fija `model_composition v1`:

- **selección**: bloque cronológico donde se elige la tasa;
- **confirmación**: bloque estrictamente posterior, que no participa en la
  elección y sólo se puntúa una vez (R2 — el parámetro no puede elegirse en el
  mismo bloque donde se reporta su resultado).

Cada predicción usa exclusivamente historia anterior a su propio kickoff, así que
la causalidad de `DEC-001` se mantiene dentro de cada bloque.

Uso:
    python -m scripts.fit_kalman_process_noise --matches tmp/dec197/matches.csv

# Requirements:
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

import pandas as pd

from src.kalman_v2 import KalmanV2Config
from src.official_goal_chain import DixonColesKalmanGoalModel

# Tasas por día para ataque/defensa. La ventaja de local usa un quinto, misma
# proporción que los valores por defecto originales (0.05 frente a 0.01).
DEFAULT_GRID = (0.0, 0.0005, 0.002, 0.005, 0.02, 0.05)
HOME_ADVANTAGE_FRACTION = 0.2


def _load(path: Path) -> list[dict[str, Any]]:
    """Carga el histórico y lo normaliza al contrato de la cadena."""

    frame = pd.read_csv(path)
    frame["match_date"] = pd.to_datetime(frame["match_date"], utc=True)
    frame = frame.sort_values(["match_date", "match_id"]).reset_index(drop=True)
    rows = []
    for record in frame.to_dict("records"):
        rows.append({
            "match_id": int(record["match_id"]),
            "match_date": record["match_date"].isoformat(),
            "home_team_id": int(record["home_team_id"]),
            "away_team_id": int(record["away_team_id"]),
            "home_goals": int(record["home_goals"]),
            "away_goals": int(record["away_goals"]),
        })
    return rows


def _outcome(row: dict[str, Any]) -> str:
    """Deriva el resultado 1X2 observado."""

    if row["home_goals"] > row["away_goals"]:
        return "1"
    if row["home_goals"] < row["away_goals"]:
        return "2"
    return "X"


def _score_block(
    rows: Sequence[dict[str, Any]], indices: Sequence[int], rate: float,
) -> dict[str, Any]:
    """Puntúa un bloque con una tasa de ruido de proceso dada."""

    config = KalmanV2Config(
        process_noise_attack=rate,
        process_noise_defense=rate,
        process_noise_home_advantage=rate * HOME_ADVANTAGE_FRACTION,
    )

    log_losses: list[float] = []
    briers: list[float] = []
    skipped = 0

    with patch(
        "src.official_goal_chain.KalmanV2Config",
        lambda *args, **kwargs: replace(config),
    ):
        model = DixonColesKalmanGoalModel()
        for index in indices:
            target = rows[index]
            history = rows[:index]
            try:
                prediction = model.predict(
                    history,
                    int(target["home_team_id"]),
                    int(target["away_team_id"]),
                    target["match_date"],
                )
            except Exception:  # noqa: BLE001 - un fixture no predecible no aborta
                skipped += 1
                continue

            probabilities = {
                "1": prediction.probability_home,
                "X": prediction.probability_draw,
                "2": prediction.probability_away,
            }
            observed = _outcome(target)
            log_losses.append(-math.log(max(probabilities[observed], 1e-15)))
            briers.append(sum(
                (value - float(label == observed)) ** 2
                for label, value in probabilities.items()))

    if not log_losses:
        raise RuntimeError("bloque sin predicciones puntuables")

    return {
        "rate": rate,
        "matches": len(log_losses),
        "skipped": skipped,
        "log_loss": sum(log_losses) / len(log_losses),
        "brier": sum(briers) / len(briers),
    }


def main() -> None:
    """Ejecuta selección y confirmación sobre la rejilla de tasas."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--history-start", type=int, default=250)
    parser.add_argument("--output", type=Path,
                        default=Path("tmp/dec197/process_noise_fit.json"))
    args = parser.parse_args()

    rows = _load(args.matches)
    evaluable = list(range(args.history_start, len(rows)))
    midpoint = args.history_start + len(evaluable) // 2
    selection = list(range(args.history_start, midpoint))
    confirmation = list(range(midpoint, len(rows)))

    print(f"histórico={len(rows)} selección={len(selection)} "
          f"confirmación={len(confirmation)}", flush=True)

    selection_results = []
    for rate in DEFAULT_GRID:
        result = _score_block(rows, selection, rate)
        selection_results.append(result)
        print(f"  selección rate={rate:<8} log_loss={result['log_loss']:.6f} "
              f"brier={result['brier']:.6f} n={result['matches']}", flush=True)

    best = min(selection_results, key=lambda item: item["log_loss"])
    baseline = next(item for item in selection_results if item["rate"] == 0.0)
    print(f"\nmejor en selección: rate={best['rate']} "
          f"(baseline rate=0.0)", flush=True)

    confirmation_best = _score_block(rows, confirmation, best["rate"])
    confirmation_baseline = _score_block(rows, confirmation, 0.0)

    payload = {
        "protocol": "walk_forward_selection_then_confirmation",
        "unit": "complete_match",
        "history_start": args.history_start,
        "selection": selection_results,
        "selected_rate": best["rate"],
        "confirmation": {
            "candidate": confirmation_best,
            "baseline": confirmation_baseline,
            "log_loss_delta": (
                confirmation_baseline["log_loss"] - confirmation_best["log_loss"]),
            "brier_delta": (
                confirmation_baseline["brier"] - confirmation_best["brier"]),
        },
        "selection_log_loss_delta": baseline["log_loss"] - best["log_loss"],
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    delta = payload["confirmation"]["log_loss_delta"]
    print(f"\nconfirmación rate={best['rate']}: "
          f"log_loss={confirmation_best['log_loss']:.6f} "
          f"vs baseline {confirmation_baseline['log_loss']:.6f}", flush=True)
    print(f"delta log-loss (positivo = mejora): {delta:+.6f}", flush=True)
    print(f"delta brier (positivo = mejora): "
          f"{payload['confirmation']['brier_delta']:+.6f}", flush=True)
    print(f"\nartefacto: {args.output}", flush=True)


if __name__ == "__main__":
    main()
