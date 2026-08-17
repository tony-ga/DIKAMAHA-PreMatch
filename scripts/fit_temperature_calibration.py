"""Ajusta la temperatura de recalibración de 1X2 (`DEC-199`).

R6 de `model_composition v1` exige que `T` se estime sobre un bloque de
validación separado del que ajustó el modelo base y que el resultado se reporte
sobre un bloque que no participó en esa elección. El protocolo replica el de
`fit_kalman_process_noise.py`:

- **selección**: se ajusta `T` sobre las predicciones walk-forward de este bloque;
- **confirmación**: bloque cronológicamente posterior, nunca usado para elegir
  nada, donde se mide si recalibrar mejora o empeora.

Cada predicción usa exclusivamente historia anterior a su propio kickoff.

Uso:
    python -m scripts.fit_temperature_calibration --matches tmp/dec197/matches.csv

# Requirements:
#   pandas>=2.0
#   scipy>=1.10

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any, Sequence

import pandas as pd

from src.official_goal_chain import DixonColesKalmanGoalModel
from src.temperature_calibration import (
    apply_temperature,
    fit_temperature,
    negative_log_likelihood,
)

LABELS = ("1", "X", "2")


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


def _walk_forward(
    rows: Sequence[dict[str, Any]], indices: Sequence[int],
) -> list[tuple[dict[str, float], str]]:
    """Genera predicciones causales para los índices dados."""

    model = DixonColesKalmanGoalModel()
    records: list[tuple[dict[str, float], str]] = []
    for index in indices:
        target = rows[index]
        try:
            prediction = model.predict(
                rows[:index],
                int(target["home_team_id"]),
                int(target["away_team_id"]),
                target["match_date"],
            )
        except Exception:  # noqa: BLE001 - un fixture no predecible no aborta
            continue
        records.append((
            {
                "1": prediction.probability_home,
                "X": prediction.probability_draw,
                "2": prediction.probability_away,
            },
            _outcome(target),
        ))
    return records


def _brier(
    records: Sequence[tuple[dict[str, float], str]], temperature: float,
) -> float:
    """Brier multiclase medio bajo una temperatura."""

    total = 0.0
    for probabilities, observed in records:
        calibrated = apply_temperature(probabilities, temperature)
        total += sum(
            (value - float(label == observed)) ** 2
            for label, value in calibrated.items())
    return total / len(records)


def _reliability(
    records: Sequence[tuple[dict[str, float], str]], temperature: float,
) -> dict[str, Any]:
    """Compara confianza declarada contra frecuencia observada en el argmax."""

    declared, hits = 0.0, 0
    for probabilities, observed in records:
        calibrated = apply_temperature(probabilities, temperature)
        best = max(calibrated, key=calibrated.get)
        declared += calibrated[best]
        hits += int(best == observed)
    return {
        "mean_declared_confidence": declared / len(records),
        "observed_accuracy": hits / len(records),
        "gap": declared / len(records) - hits / len(records),
    }


def main() -> None:
    """Ajusta `T` en selección y lo evalúa en confirmación."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--matches", type=Path, required=True)
    parser.add_argument("--history-start", type=int, default=250)
    parser.add_argument("--output", type=Path,
                        default=Path("tmp/dec197/temperature_fit.json"))
    args = parser.parse_args()

    rows = _load(args.matches)
    evaluable = list(range(args.history_start, len(rows)))
    midpoint = args.history_start + len(evaluable) // 2
    selection = list(range(args.history_start, midpoint))
    confirmation = list(range(midpoint, len(rows)))

    print(f"histórico={len(rows)} selección={len(selection)} "
          f"confirmación={len(confirmation)}", flush=True)

    selection_records = _walk_forward(rows, selection)
    confirmation_records = _walk_forward(rows, confirmation)
    print(f"predicciones: selección={len(selection_records)} "
          f"confirmación={len(confirmation_records)}", flush=True)

    fit = fit_temperature(selection_records)
    temperature = fit["temperature"]
    print(f"\nT ajustada en selección = {temperature:.4f}", flush=True)
    print(f"  log-loss selección {fit['nll_uncalibrated']:.6f} -> "
          f"{fit['nll_calibrated']:.6f}", flush=True)

    baseline_nll = negative_log_likelihood(confirmation_records, 1.0)
    calibrated_nll = negative_log_likelihood(confirmation_records, temperature)
    payload = {
        "protocol": "walk_forward_selection_then_confirmation",
        "unit": "complete_match",
        "labels": list(LABELS),
        "selection": {**fit, "matches": len(selection_records)},
        "confirmation": {
            "matches": len(confirmation_records),
            "log_loss_uncalibrated": baseline_nll,
            "log_loss_calibrated": calibrated_nll,
            "log_loss_delta": baseline_nll - calibrated_nll,
            "brier_uncalibrated": _brier(confirmation_records, 1.0),
            "brier_calibrated": _brier(confirmation_records, temperature),
            "reliability_uncalibrated": _reliability(confirmation_records, 1.0),
            "reliability_calibrated": _reliability(
                confirmation_records, temperature),
        },
        "temperature": temperature,
    }
    payload["confirmation"]["brier_delta"] = (
        payload["confirmation"]["brier_uncalibrated"]
        - payload["confirmation"]["brier_calibrated"])

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    confirmation_block = payload["confirmation"]
    print(f"\nconfirmación ({confirmation_block['matches']} partidos):",
          flush=True)
    print(f"  log-loss {confirmation_block['log_loss_uncalibrated']:.6f} -> "
          f"{confirmation_block['log_loss_calibrated']:.6f} "
          f"(delta {confirmation_block['log_loss_delta']:+.6f})", flush=True)
    print(f"  brier    {confirmation_block['brier_uncalibrated']:.6f} -> "
          f"{confirmation_block['brier_calibrated']:.6f} "
          f"(delta {confirmation_block['brier_delta']:+.6f})", flush=True)
    before = confirmation_block["reliability_uncalibrated"]
    after = confirmation_block["reliability_calibrated"]
    print(f"  confianza declarada {before['mean_declared_confidence']:.4f} -> "
          f"{after['mean_declared_confidence']:.4f}", flush=True)
    print(f"  acierto observado   {before['observed_accuracy']:.4f}", flush=True)
    print(f"  brecha {before['gap']:+.4f} -> {after['gap']:+.4f}", flush=True)
    print(f"\nartefacto: {args.output}", flush=True)


if __name__ == "__main__":
    main()
