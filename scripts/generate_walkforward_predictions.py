"""Genera predicciones walk-forward sobre el corpus causal completo.

Es el arnés común de todos los candidatos de `arquitectura_matematica_v1`. La
cadena Dixon-Coles/Kalman es cara -un ajuste por predicción-, así que se ejecuta
**una sola vez** y se guarda por partido:

- las probabilidades del blend oficial;
- las de Dixon-Coles solo;
- las de Kalman solo.

Con esas tres vistas guardadas, la calibración por temperatura, la reestimación
del peso de mezcla y cualquier comparador posterior se evalúan sin volver a
tocar la cadena. Sólo un cambio en el estado latente -por ejemplo la tasa de
ruido de proceso- obliga a regenerar.

El `split` no lo decide este script: viene congelado del corpus de Fase 74, de
modo que ningún candidato pueda elegir la partición donde se mide (R2).

Uso:
    python -m scripts.generate_walkforward_predictions --splits selection confirmation

# Requirements:
#   pandas>=2.0
#   numpy>=1.24

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable
from unittest.mock import patch

import pandas as pd

from src.kalman_v2 import KalmanV2Config, poisson_matrix
from src.official_goal_chain import DixonColesKalmanGoalModel

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "artifacts/match_level_corpus/matches.csv"
MAX_GOALS = 12
HOME_ADVANTAGE_FRACTION = 0.2


def _markets_from_lambdas(
    lambda_home: float, lambda_away: float, tau: float,
) -> dict[str, float]:
    """Deriva 1X2 desde un par de intensidades, con la misma rejilla oficial."""

    matrix = poisson_matrix(lambda_home, lambda_away, MAX_GOALS, tau, True)
    import numpy as np

    return {
        "1": float(np.tril(matrix, -1).sum()),
        "X": float(np.trace(matrix)),
        "2": float(np.triu(matrix, 1).sum()),
    }


def _outcome(home_goals: int, away_goals: int) -> str:
    """Deriva el resultado 1X2 observado."""

    if home_goals > away_goals:
        return "1"
    if home_goals < away_goals:
        return "2"
    return "X"


def _league_rows(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """Normaliza los partidos de una liga al contrato de la cadena."""

    return [
        {
            "match_id": int(record["match_id"]),
            "match_date": pd.Timestamp(record["match_date"]).isoformat(),
            "home_team_id": int(record["home_team_id"]),
            "away_team_id": int(record["away_team_id"]),
            "home_goals": int(record["home_goals"]),
            "away_goals": int(record["away_goals"]),
            "split": str(record["split"]),
            "league_slug": str(record["league_slug"]),
        }
        for record in frame.to_dict("records")
    ]


def run(
    corpus: Path, output: Path, splits: Iterable[str], rate: float,
    min_history: int,
) -> dict[str, Any]:
    """Recorre cada liga en orden y emite predicciones causales."""

    frame = pd.read_csv(corpus)
    frame = frame.sort_values(["league_slug", "match_date", "match_id"])
    wanted = set(splits)

    config = KalmanV2Config(
        process_noise_attack=rate,
        process_noise_defense=rate,
        process_noise_home_advantage=rate * HOME_ADVANTAGE_FRACTION,
    )

    if output.exists():
        # Dos procesos escribiendo el mismo JSONL producen líneas entrelazadas
        # que no fallan al escribirse, sólo al leerse. Negarse a sobrescribir es
        # más barato que descubrir la corrupción después de medir sobre ella.
        raise FileExistsError(
            f"{output} ya existe; muévelo o bórralo antes de regenerar")

    output.parent.mkdir(parents=True, exist_ok=True)
    counters = {"emitted": 0, "skipped_history": 0, "failed": 0}
    failure_reasons: dict[str, int] = {}
    started = time.time()

    with output.open("w", encoding="utf-8") as handle, patch(
        "src.official_goal_chain.KalmanV2Config",
        lambda *args, **kwargs: replace(config),
    ):
        for league, league_frame in frame.groupby("league_slug", sort=True):
            rows = _league_rows(league_frame)
            model = DixonColesKalmanGoalModel()
            for index, target in enumerate(rows):
                if target["split"] not in wanted:
                    continue
                if index < min_history:
                    counters["skipped_history"] += 1
                    continue
                # La historia son los partidos **estrictamente anteriores** al
                # kickoff, no los que preceden en el orden de la lista. Una
                # jornada tiene varios partidos simultáneos -y la última los
                # tiene todos por reglamento-; ninguno de ellos puede informar
                # la predicción de otro. Cortar por posición en vez de por
                # tiempo los colaría, que es la fuga que `DEC-113` cerró en el
                # entrenamiento y que aquí reaparecería por la puerta de atrás.
                cutoff = target["match_date"]
                history = [
                    {key: row[key] for key in (
                        "match_id", "match_date", "home_team_id",
                        "away_team_id", "home_goals", "away_goals")}
                    for row in rows[:index] if row["match_date"] < cutoff
                ]
                try:
                    prediction = model.predict(
                        history,
                        target["home_team_id"],
                        target["away_team_id"],
                        target["match_date"],
                    )
                except Exception as error:  # noqa: BLE001
                    # La razón importa: un fallo repartido al azar reduce la
                    # muestra, pero uno concentrado en ciertas ligas sesga el
                    # bloque de confirmación sin que nada lo declare.
                    reason = str(error) or type(error).__name__
                    failure_reasons[reason] = failure_reasons.get(reason, 0) + 1
                    counters["failed"] += 1
                    continue

                audit = prediction.audit
                tau = float(audit["tau_dc"])
                try:
                    dc_only = _markets_from_lambdas(
                        audit["dc_lambda_home"], audit["dc_lambda_away"], tau)
                    kalman_only = _markets_from_lambdas(
                        audit["kalman_lambda_home"],
                        audit["kalman_lambda_away"], tau)
                except Exception:  # noqa: BLE001
                    counters["failed"] += 1
                    continue

                handle.write(json.dumps({
                    "match_id": target["match_id"],
                    "match_date": target["match_date"],
                    "league_slug": league,
                    "split": target["split"],
                    "outcome": _outcome(
                        target["home_goals"], target["away_goals"]),
                    "home_goals": target["home_goals"],
                    "away_goals": target["away_goals"],
                    "blended": {
                        "1": prediction.probability_home,
                        "X": prediction.probability_draw,
                        "2": prediction.probability_away,
                    },
                    "dixon_coles": dc_only,
                    "kalman": kalman_only,
                    "lambda_blended": [
                        prediction.lambda_home, prediction.lambda_away],
                    "lambda_dixon_coles": [
                        audit["dc_lambda_home"], audit["dc_lambda_away"]],
                    "lambda_kalman": [
                        audit["kalman_lambda_home"],
                        audit["kalman_lambda_away"]],
                    "tau_dc": tau,
                    "probability_over_2_5": prediction.probability_over_2_5,
                    "probability_btts": prediction.probability_btts,
                }) + "\n")
                counters["emitted"] += 1

                if counters["emitted"] % 100 == 0:
                    elapsed = time.time() - started
                    print(f"  {counters['emitted']} emitidas "
                          f"({elapsed:.0f}s, liga actual {league})", flush=True)

    summary = {
        **counters,
        "process_noise_rate": rate,
        "splits": sorted(wanted),
        "failure_reasons": dict(
            sorted(failure_reasons.items(), key=lambda item: -item[1])),
        "elapsed_seconds": round(time.time() - started, 1),
        "output": str(output),
    }
    (output.parent / f"{output.stem}_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    return summary


def main() -> None:
    """Ejecuta el arnés con la configuración solicitada."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path,
                        default=ROOT / "artifacts/walkforward_predictions/baseline.jsonl")
    parser.add_argument("--splits", nargs="+",
                        default=["selection", "confirmation"])
    parser.add_argument("--rate", type=float, default=0.0)
    parser.add_argument("--min-history", type=int, default=40)
    args = parser.parse_args()

    summary = run(
        args.corpus, args.output, args.splits, args.rate, args.min_history)
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
