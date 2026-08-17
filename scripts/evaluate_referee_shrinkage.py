"""Contracción jerárquica de tarjetas por árbitro (D.1 de la auditoría).

Que unos árbitros pitan más tarjetas que otros es sabido; la pregunta medible es
si **conocer al árbitro antes del partido mejora la predicción** de tarjetas por
encima de conocer sólo la liga y los equipos. Un árbitro con tres partidos en el
histórico no puede sostener su propia tasa, y R5 da la forma de usarlo igualmente
sin sobreajustar:

    tasa_j = w_j · tasa_liga + (1 - w_j) · tasa_arbitro_j
    w_j    = sigma_j² / (sigma_j² + tau²)

`tau²` es la dispersión real entre árbitros y `sigma_j²` la incertidumbre dentro
del árbitro `j`, que decrece con su número de partidos. Un árbitro nuevo se
contrae casi del todo hacia la liga; uno con historia larga conserva su tasa.

El baseline no es ingenuo: es la predicción que ya se puede hacer **sin** el
árbitro -media de liga combinada con el historial de tarjetas de los dos
equipos-. La pregunta es si el árbitro aporta *sobre eso*.

Todo causal: la tasa de un árbitro sale sólo de sus partidos anteriores al que se
predice. Partición congelada de Fase 74; se reporta en `confirmation`.

Uso:
    python -m scripts.evaluate_referee_shrinkage

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
DEFAULT_OFFICIALS = ROOT / "artifacts/match_officials/officials.jsonl"
DEFAULT_OUTPUT = ROOT / "artifacts/candidate_evaluation"

MIN_REFEREE_MATCHES = 3
MIN_TEAM_MATCHES = 3
MIN_LEAGUE_MATCHES = 30
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
        verdict = ("mejora confirmada" if mean > 0
                   else "degradación confirmada")
    return {
        "mean": mean, "ci_low": low, "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high), "verdict": verdict,
    }


def _referees(path: Path) -> dict[int, str]:
    """Mapa partido → árbitro principal, tomando el de menor `order`."""

    mapping: dict[int, str] = {}
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            officials = [
                item for item in row.get("officials", [])
                if item.get("name")
            ]
            if not officials:
                continue
            main = min(
                officials, key=lambda item: item.get("order") or 99)
            mapping[int(row["match_id"])] = main["name"].strip()
    return mapping


def main() -> None:
    """Mide si el árbitro aporta sobre la predicción sin árbitro."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--officials", type=Path, default=DEFAULT_OFFICIALS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    referees = _referees(args.officials)
    frame = pd.read_csv(args.corpus).sort_values(["match_date", "match_id"])
    frame["referee"] = frame["match_id"].map(referees)
    covered = frame["referee"].notna()
    print(f"partidos con árbitro: {int(covered.sum())} de {len(frame)} "
          f"({covered.mean():.1%})", flush=True)
    print(f"árbitros distintos: {frame['referee'].nunique()}\n", flush=True)

    league_sum: dict[str, float] = defaultdict(float)
    league_count: dict[str, int] = defaultdict(int)
    team_sum: dict[tuple[str, int], float] = defaultdict(float)
    team_count: dict[tuple[str, int], int] = defaultdict(int)
    ref_sum: dict[str, float] = defaultdict(float)
    ref_count: dict[str, int] = defaultdict(int)
    ref_means: dict[str, list[float]] = defaultdict(list)

    without, with_ref, splits = [], [], []

    for record in frame.to_dict("records"):
        league = str(record["league_slug"])
        referee = record["referee"]
        total = float(record["home_yellow_cards"] + record["away_yellow_cards"])
        home = (league, int(record["home_team_id"]))
        away = (league, int(record["away_team_id"]))

        usable = (
            league_count[league] >= MIN_LEAGUE_MATCHES
            and team_count[home] >= MIN_TEAM_MATCHES
            and team_count[away] >= MIN_TEAM_MATCHES
            and isinstance(referee, str)
            and ref_count[referee] >= MIN_REFEREE_MATCHES
        )

        if usable:
            league_mean = league_sum[league] / league_count[league]
            # Baseline sin árbitro: liga corregida por lo que los dos equipos
            # se desvían de ella en su propio historial.
            home_dev = team_sum[home] / team_count[home] - league_mean
            away_dev = team_sum[away] / team_count[away] - league_mean
            baseline = league_mean + 0.5 * (home_dev + away_dev)

            # Contracción del árbitro hacia la liga según su muestra (R5).
            observed_means = [
                value for value in ref_means.values() if value
            ]
            between = float(np.var(
                [np.mean(values) for values in observed_means], ddof=1
            )) if len(observed_means) > 2 else 0.0
            n = ref_count[referee]
            ref_mean = ref_sum[referee] / n
            within = max(float(np.var(ref_means[referee], ddof=1))
                         if n > 1 else 1.0, 1e-6)
            sigma_squared = within / n
            weight = (sigma_squared / (sigma_squared + between)
                      if between > 0 else 1.0)
            referee_effect = (1 - weight) * (ref_mean - league_mean)

            without.append((baseline - total) ** 2)
            with_ref.append((baseline + referee_effect - total) ** 2)
            splits.append(str(record["split"]))

        league_sum[league] += total
        league_count[league] += 1
        team_sum[home] += total
        team_count[home] += 1
        team_sum[away] += total
        team_count[away] += 1
        if isinstance(referee, str):
            ref_sum[referee] += total
            ref_count[referee] += 1
            ref_means[referee].append(total)

    mask = np.array([split == "confirmation" for split in splits], dtype=bool)
    without_arr = np.array(without)[mask]
    with_arr = np.array(with_ref)[mask]
    stats = _bootstrap(without_arr - with_arr)

    report = {
        "target": "yellow_cards_total",
        "confirmation_matches": int(mask.sum()),
        "distinct_referees": int(frame["referee"].nunique()),
        "coverage": float(covered.mean()),
        "mse_without_referee": float(np.mean(without_arr)),
        "mse_with_referee": float(np.mean(with_arr)),
        "referee_vs_no_referee": stats,
    }

    print(f"confirmación: {report['confirmation_matches']} partidos", flush=True)
    print(f"  MSE sin árbitro {report['mse_without_referee']:.4f} → "
          f"con árbitro {report['mse_with_referee']:.4f}", flush=True)
    print(f"  delta {stats['mean']:+.6f} "
          f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
          f"→ {stats['verdict']}", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "referee_shrinkage.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nartefacto: {path}", flush=True)


if __name__ == "__main__":
    main()
