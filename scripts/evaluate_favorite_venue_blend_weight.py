"""Peso de mezcla condicionado por localía del favorito (`DEC-211`, Fase 128).

Fase 127 midió que segmentar la temperatura de 1X2 por localía del favorito es
indistinguible de la T global (`IC95% [-0.000085, +0.000054]` en log-loss).
Este candidato prueba la otra pieza de la composición: el peso de mezcla
Dixon-Coles/Kalman, con el mismo grupo -favorito local vs favorito visitante-
y la misma contracción jerárquica que `scripts/evaluate_hierarchical_blend.py`
(`DEC-202`, que probó lo mismo por liga).

El grupo se deriva de la probabilidad ya servida (peso 0.642848 + T global
1.198935), nunca del marcador del partido objetivo. La comparación es siempre
contra la composición ya adoptada, aplicando el peso por grupo y manteniendo
la T global fija -aislar qué pieza aporta, no mover las dos a la vez-.

Uso:
    python -m scripts.evaluate_favorite_venue_blend_weight

# Requirements:
#   numpy>=1.24
#   scipy>=1.10

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Sequence

import numpy as np
from scipy.optimize import minimize_scalar

from scripts.evaluate_candidates import (  # noqa: E402
    _blend,
    _brier,
    _load,
    _log_loss,
    _paired_bootstrap,
)
from scripts.evaluate_favorite_venue_temperature import (  # noqa: E402
    _favorite_venue,
    _load_adopted,
    _served,
    DEFAULT_ADOPTED,
    DEFAULT_INPUT,
)
from src.temperature_calibration import apply_temperature

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "artifacts/candidate_evaluation"


def _fit_weight_on(rows: Sequence[dict[str, Any]]) -> float:
    """Peso de mezcla que minimiza log-loss del blend (sin T) en `rows`."""

    def objective(weight: float) -> float:
        return float(np.mean([
            _log_loss(_blend(row, weight), row["outcome"]) for row in rows]))

    result = minimize_scalar(objective, bounds=(0.0, 1.0), method="bounded")
    return float(result.x)


def evaluate(rows: list[dict[str, Any]], adopted: dict[str, float]) -> dict[str, Any]:
    """Ajusta el peso por localía del favorito en selección, mide en confirmación."""

    global_weight = adopted["weight"]
    global_temperature = adopted["temperature"]

    selection = [row for row in rows if row["split"] == "selection"]
    confirmation = [row for row in rows if row["split"] == "confirmation"]

    for row in selection + confirmation:
        row["_served"] = _served(row, global_weight, global_temperature)
        row["_favorite_venue"] = _favorite_venue(row["_served"])

    selection_by_venue: dict[str, list[dict[str, Any]]] = {"home": [], "away": []}
    for row in selection:
        if row["_favorite_venue"] is not None:
            selection_by_venue[row["_favorite_venue"]].append(row)

    confirmation_favored = [
        row for row in confirmation if row["_favorite_venue"] is not None
    ]
    outcomes = [row["outcome"] for row in confirmation_favored]
    baseline_served = [row["_served"] for row in confirmation_favored]

    raw_weight = {
        venue: _fit_weight_on(group_rows)
        for venue, group_rows in selection_by_venue.items()
    }

    values = np.array(list(raw_weight.values()), dtype=float)
    tau_squared = float(np.var(values, ddof=1)) if len(values) > 1 else 0.0
    counts = {venue: len(rows_) for venue, rows_ in selection_by_venue.items()}
    scale = float(np.median(list(counts.values()))) * tau_squared

    shrinkage_lambda: dict[str, float] = {}
    shrunk_weight: dict[str, float] = {}
    for venue, weight in raw_weight.items():
        sigma_squared = scale / counts[venue] if counts[venue] else float("inf")
        lam = (sigma_squared / (sigma_squared + tau_squared)
               if tau_squared > 0 else 1.0)
        shrinkage_lambda[venue] = lam
        shrunk_weight[venue] = lam * global_weight + (1 - lam) * weight

    def _candidate_probabilities(weight_of: dict[str, float]) -> list[dict[str, float]]:
        return [
            apply_temperature(
                _blend(row, weight_of[row["_favorite_venue"]]),
                global_temperature)
            for row in confirmation_favored
        ]

    def _compare(candidate: list[dict[str, float]]) -> dict[str, Any]:
        log_loss = np.array([
            _log_loss(base, outcome) - _log_loss(cand, outcome)
            for base, cand, outcome in zip(baseline_served, candidate, outcomes)
        ], dtype=float)
        brier = np.array([
            _brier(base, outcome) - _brier(cand, outcome)
            for base, cand, outcome in zip(baseline_served, candidate, outcomes)
        ], dtype=float)
        return {
            "log_loss": _paired_bootstrap(log_loss),
            "brier": _paired_bootstrap(brier),
        }

    shrunk_candidate = _candidate_probabilities(shrunk_weight)
    raw_candidate = _candidate_probabilities(raw_weight)

    return {
        "protocol": "selection_fits_confirmation_reports",
        "unit": "complete_match",
        "grouping": "favorite_venue (home | away), derived from served argmax",
        "adopted_weight": global_weight,
        "adopted_global_temperature": global_temperature,
        "selection_matches_by_venue": counts,
        "confirmation_matches_favored": len(confirmation_favored),
        "raw_weight_by_venue": raw_weight,
        "tau_squared_between_venues": tau_squared,
        "caveat_two_groups": (
            "tau^2 se estima con solo 2 grupos (home/away); estimacion debil, "
            "misma salvedad que Fase 127."
        ),
        "shrinkage_lambda_by_venue": shrinkage_lambda,
        "shrunk_weight_by_venue": shrunk_weight,
        "hierarchical_vs_served": _compare(shrunk_candidate),
        "unshrunk_vs_served": _compare(raw_candidate),
    }


def main() -> None:
    """Ejecuta el candidato y publica el reporte."""

    rows = _load(DEFAULT_INPUT)
    adopted = _load_adopted(DEFAULT_ADOPTED)
    report = evaluate(rows, adopted)

    DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True)
    output_path = DEFAULT_OUTPUT / "favorite_venue_blend_weight.json"
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(f"selección por venue: {report['selection_matches_by_venue']}", flush=True)
    print(f"confirmación favorecida={report['confirmation_matches_favored']}\n",
          flush=True)
    print(f"peso global adoptado = {report['adopted_weight']:.6f}", flush=True)
    print(f"peso local (sin contraer)     = "
          f"{report['raw_weight_by_venue']['home']:.6f}", flush=True)
    print(f"peso visitante (sin contraer) = "
          f"{report['raw_weight_by_venue']['away']:.6f}", flush=True)
    print(f"tau^2 entre grupos = {report['tau_squared_between_venues']:.6f}", flush=True)
    print(f"peso local (contraido)     = "
          f"{report['shrunk_weight_by_venue']['home']:.6f}", flush=True)
    print(f"peso visitante (contraido) = "
          f"{report['shrunk_weight_by_venue']['away']:.6f}\n", flush=True)

    for name in ("hierarchical_vs_served", "unshrunk_vs_served"):
        print(f"--- {name} ---", flush=True)
        for metric in ("log_loss", "brier"):
            stats = report[name][metric]
            print(f"  {metric}: {stats['mean']:+.6f} "
                  f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
                  f"→ {stats['verdict']}", flush=True)
        print(flush=True)

    print(f"artefacto: {output_path}", flush=True)


if __name__ == "__main__":
    main()
