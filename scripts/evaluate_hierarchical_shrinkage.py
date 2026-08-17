"""Compara contracción fija contra contracción jerárquica (B.2).

`market_calibration.py` contrae la tasa causal de una liga hacia un prior con un
`shrinkage` **constante por mercado**: la misma contracción para una liga con 40
observaciones que para una con 4,000. B.2 propone derivarla de los datos:

    w_j = σ_j² / (σ_j² + τ²)          (Murphy *PML* p.146, ec. 3.256-3.257)

donde `σ_j²` es la varianza de muestreo de la estimación de la liga -que decrece
al acumular partidos- y `τ²` la varianza real entre ligas. El efecto es que una
liga con poca muestra se contrae mucho y una con mucha casi nada, sin que nadie
elija el número.

Protocolo:

- **fit**: se estima el prior global y `τ²` por método de momentos;
- **selección**: se elige la constante `s` que MEJOR le va a la contracción fija.
  Darle su mejor caso importa: ganarle a una constante mal elegida no
  demostraría nada sobre el método;
- **confirmación**: se comparan ambas, con IC bootstrap remuestreando **partidos
  completos** (R7) -las dos filas de un partido están correlacionadas y tratarlas
  como independientes estrecharía el intervalo artificialmente-.

Cada predicción usa sólo partidos de esa liga estrictamente anteriores al
kickoff.

Uso:
    python -m scripts.evaluate_hierarchical_shrinkage

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
from collections import defaultdict
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "artifacts/team_count_corpus/team_matches.csv"
DEFAULT_OUTPUT = ROOT / "artifacts/hierarchical_shrinkage_evaluation"

# Líneas cercanas a las que el producto ya publica por equipo.
MARKETS = {
    "corners_over_4_5": ("corners", 4.5),
    "shots_over_10_5": ("shots", 10.5),
    "shots_on_target_over_3_5": ("shots_on_target", 3.5),
    "yellow_cards_over_1_5": ("yellow_cards", 1.5),
}

SHRINKAGE_GRID = (5.0, 25.0, 100.0, 250.0, 500.0, 1000.0)
MIN_HISTORY = 30


def _tau_squared(
    groups: Sequence[tuple[int, int]], global_rate: float,
) -> float:
    """Estima la varianza entre ligas por método de momentos.

    La varianza observada entre tasas de liga mezcla la variación real con el
    ruido de muestreo de cada una. Restar la segunda deja la primera; si el
    resultado es negativo, la dispersión observada se explica entera por el
    muestreo y `τ²` es cero -las ligas no difieren de forma detectable-.
    """

    rates = np.array([positives / totals for positives, totals in groups])
    sampling = np.array([
        global_rate * (1.0 - global_rate) / totals for _, totals in groups])
    return float(max(np.var(rates, ddof=1) - float(np.mean(sampling)), 0.0))


def _fixed(positives: float, totals: float, shrinkage: float,
           prior: float) -> float:
    """Contracción con constante fija -la fórmula que sirve producción hoy-."""

    return (positives + shrinkage * prior) / (totals + shrinkage)


def _hierarchical(positives: float, totals: float, prior: float,
                  tau_squared: float) -> float:
    """Contracción con peso derivado de la muestra de la propia liga."""

    if totals <= 0:
        return prior
    rate = positives / totals
    sampling_variance = max(prior * (1.0 - prior) / totals, 1e-12)
    if tau_squared <= 0.0:
        return prior
    weight = sampling_variance / (sampling_variance + tau_squared)
    return weight * prior + (1.0 - weight) * rate


def _log_loss(probability: float, observed: bool) -> float:
    """Log-verosimilitud negativa de una observación binaria."""

    value = probability if observed else 1.0 - probability
    return -math.log(max(min(value, 1.0), 1e-15))


def _bootstrap_by_match(
    per_match: dict[int, float], replicates: int = 10000, seed: int = 42,
) -> dict[str, Any]:
    """IC95% del delta medio remuestreando partidos, no equipo-partidos."""

    deltas = np.array(list(per_match.values()), dtype=float)
    rng = np.random.default_rng(seed)
    means = np.array([
        float(np.mean(rng.choice(deltas, size=len(deltas), replace=True)))
        for _ in range(replicates)
    ])
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return {
        "matches": len(deltas),
        "mean": float(np.mean(deltas)),
        "ci_low": low,
        "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high),
        "verdict": (
            "indistinguible" if low <= 0.0 <= high
            else ("mejora confirmada" if float(np.mean(deltas)) > 0
                  else "degradación confirmada")),
    }


def _walk_forward(
    frame: pd.DataFrame, metric: str, line: float, splits: set[str],
) -> list[dict[str, Any]]:
    """Emite, por cada equipo-partido, la historia causal de su liga."""

    records: list[dict[str, Any]] = []
    for _, league_frame in frame.groupby("league_slug", sort=True):
        league_frame = league_frame.sort_values(
            ["match_date", "match_id", "team_id"])
        positives = 0.0
        totals = 0.0
        pending: list[tuple[str, dict[str, Any]]] = []
        current_date: str | None = None

        for record in league_frame.to_dict("records"):
            date = str(record["match_date"])
            if current_date is not None and date != current_date:
                # Las observaciones de un mismo kickoff se incorporan juntas,
                # después de predecirlas: ninguna informa a su simultánea.
                for _, done in pending:
                    positives += float(done["observed"])
                    totals += 1.0
                pending = []
            current_date = date

            observed = bool(float(record[metric]) > line)
            entry = {
                "match_id": int(record["match_id"]),
                "split": str(record["split"]),
                "observed": observed,
                "history_positives": positives,
                "history_totals": totals,
            }
            pending.append((date, entry))
            if entry["split"] in splits and totals >= MIN_HISTORY:
                records.append(entry)

        for _, done in pending:
            positives += float(done["observed"])
            totals += 1.0

    return records


def evaluate(frame: pd.DataFrame) -> dict[str, Any]:
    """Evalúa los cuatro mercados con ambas contracciones."""

    report: dict[str, Any] = {
        "protocol": "fit_estimates_tau__selection_picks_fixed__confirmation_reports",
        "bootstrap_unit": "complete_match",
        "markets": {},
    }

    for market, (metric, line) in MARKETS.items():
        fit_frame = frame[frame["split"] == "fit"]
        observed = fit_frame[metric] > line
        global_rate = float(observed.mean())

        groups = [
            (int((group[metric] > line).sum()), int(len(group)))
            for _, group in fit_frame.groupby("league_slug")
            if len(group) >= MIN_HISTORY
        ]
        tau_squared = _tau_squared(groups, global_rate)

        records = _walk_forward(frame, metric, line, {"selection", "confirmation"})
        selection = [row for row in records if row["split"] == "selection"]
        confirmation = [row for row in records if row["split"] == "confirmation"]
        if not selection or not confirmation:
            continue

        # La constante fija recibe su mejor valor posible, elegido en selección.
        best_shrinkage = min(
            SHRINKAGE_GRID,
            key=lambda value: float(np.mean([
                _log_loss(
                    _fixed(row["history_positives"], row["history_totals"],
                           value, global_rate),
                    row["observed"])
                for row in selection])))

        per_match: dict[int, list[float]] = defaultdict(list)
        fixed_losses, hierarchical_losses = [], []
        for row in confirmation:
            fixed = _log_loss(
                _fixed(row["history_positives"], row["history_totals"],
                       best_shrinkage, global_rate), row["observed"])
            hierarchical = _log_loss(
                _hierarchical(row["history_positives"], row["history_totals"],
                              global_rate, tau_squared), row["observed"])
            fixed_losses.append(fixed)
            hierarchical_losses.append(hierarchical)
            per_match[row["match_id"]].append(fixed - hierarchical)

        report["markets"][market] = {
            "metric": metric,
            "line": line,
            "global_rate": global_rate,
            "tau_squared": tau_squared,
            "best_fixed_shrinkage": best_shrinkage,
            "team_matches_confirmation": len(confirmation),
            "fixed_log_loss": float(np.mean(fixed_losses)),
            "hierarchical_log_loss": float(np.mean(hierarchical_losses)),
            "log_loss_delta": _bootstrap_by_match(
                {key: float(np.mean(values))
                 for key, values in per_match.items()}),
        }

    return report


def main() -> None:
    """Ejecuta la comparación y publica el reporte."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    frame = pd.read_csv(args.corpus)
    report = evaluate(frame)

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    for market, block in report["markets"].items():
        delta = block["log_loss_delta"]
        print(f"--- {market} (tasa global {block['global_rate']:.4f}) ---",
              flush=True)
        print(f"  tau^2={block['tau_squared']:.6f}  "
              f"mejor shrinkage fijo={block['best_fixed_shrinkage']}", flush=True)
        print(f"  log-loss fija {block['fixed_log_loss']:.6f} vs "
              f"jerárquica {block['hierarchical_log_loss']:.6f}", flush=True)
        print(f"  delta {delta['mean']:+.6f} IC95% "
              f"[{delta['ci_low']:+.6f}, {delta['ci_high']:+.6f}] "
              f"→ {delta['verdict']}  (n={delta['matches']} partidos)\n",
              flush=True)

    print(f"artefacto: {args.output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
