"""Versión regularizada de la corrección de sesgo por clase para el
favorito visitante (continuación de `evaluate_favorite_venue_bias_
correction.py`).

La versión sin regularizar ajustó dos parámetros con sólo 477 partidos de
favorito visitante en selección: la dirección fue correcta (mejora media
positiva) pero inestable -24.05% de los partidos de confirmación voltearon
quién es favorito-, y el IC95% cruzó cero. Es la firma clásica de
sobreajuste con muestra chica, no de ausencia de señal.

Esta versión selecciona la fuerza de penalización L2 sobre los dos sesgos
con validación cruzada de 3 folds DENTRO de selección -nunca toca
confirmación para elegir nada-, y sólo entonces mide en confirmación.

Uso:
    python -m scripts.evaluate_favorite_venue_bias_correction_regularized

# Requirements:
#   numpy>=1.24
#   scipy>=1.10

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

from scripts.evaluate_candidates import (  # noqa: E402
    _brier,
    _load,
    _log_loss,
    _paired_bootstrap,
)
from scripts.evaluate_favorite_venue_bias_correction import (  # noqa: E402
    _apply_bias,
    _favorite_underdog_codes,
)
from scripts.evaluate_favorite_venue_temperature import (  # noqa: E402
    _favorite_venue,
    _load_adopted,
    _served,
    DEFAULT_ADOPTED,
    DEFAULT_INPUT,
)

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/candidate_evaluation/favorite_venue_bias_correction_regularized.json"
PENALTIES = (0.0, 1.0, 2.0, 4.0, 8.0, 16.0)
N_FOLDS = 3
RNG_SEED = 20260818


def _fit_bias_penalized(
    rows: list[dict[str, Any]], venue: str, penalty: float,
) -> tuple[float, float]:
    favorite_code, underdog_code = _favorite_underdog_codes(venue)

    def objective(params: np.ndarray) -> float:
        bias_favorite, bias_underdog = params
        total = 0.0
        for row in rows:
            calibrated = _apply_bias(
                row["_served"], favorite_code, underdog_code,
                bias_favorite, bias_underdog)
            total -= math.log(max(calibrated[row["outcome"]], 1e-15))
        nll = total / len(rows)
        return nll + penalty * (bias_favorite ** 2 + bias_underdog ** 2)

    result = minimize(
        objective, x0=np.array([0.0, 0.0]), method="Nelder-Mead",
        options={"xatol": 1e-6, "fatol": 1e-9, "maxiter": 2000},
    )
    if not result.success:
        raise RuntimeError("bias_correction_did_not_converge")
    return float(result.x[0]), float(result.x[1])


def _nll_on(
    rows: list[dict[str, Any]], venue: str,
    bias_favorite: float, bias_underdog: float,
) -> float:
    favorite_code, underdog_code = _favorite_underdog_codes(venue)
    total = 0.0
    for row in rows:
        calibrated = _apply_bias(
            row["_served"], favorite_code, underdog_code,
            bias_favorite, bias_underdog)
        total -= math.log(max(calibrated[row["outcome"]], 1e-15))
    return total / len(rows)


def _select_penalty(
    selection_rows: list[dict[str, Any]], venue: str,
) -> tuple[float, dict[str, float]]:
    """Elige la penalización L2 por validación cruzada de 3 folds en selección."""

    rng = np.random.default_rng(RNG_SEED)
    shuffled = list(selection_rows)
    rng.shuffle(shuffled)
    folds = np.array_split(shuffled, N_FOLDS)

    scores: dict[str, float] = {}
    for penalty in PENALTIES:
        fold_nlls = []
        for k in range(N_FOLDS):
            held_out = list(folds[k])
            train = [row for j in range(N_FOLDS) if j != k for row in folds[j]]
            bias_favorite, bias_underdog = _fit_bias_penalized(train, venue, penalty)
            fold_nlls.append(_nll_on(held_out, venue, bias_favorite, bias_underdog))
        scores[str(penalty)] = float(np.mean(fold_nlls))
    best_penalty = min(PENALTIES, key=lambda value: scores[str(value)])
    return best_penalty, scores


def evaluate(rows: list[dict[str, Any]], adopted: dict[str, float]) -> dict[str, Any]:
    weight, temperature = adopted["weight"], adopted["temperature"]
    selection = [row for row in rows if row["split"] == "selection"]
    confirmation = [row for row in rows if row["split"] == "confirmation"]

    for row in selection + confirmation:
        row["_served"] = _served(row, weight, temperature)
        row["_favorite_venue"] = _favorite_venue(row["_served"])

    report: dict[str, Any] = {}
    for venue in ("home", "away"):
        selection_group = [
            row for row in selection if row["_favorite_venue"] == venue]
        confirmation_group = [
            row for row in confirmation if row["_favorite_venue"] == venue]

        best_penalty, cv_scores = _select_penalty(selection_group, venue)
        bias_favorite, bias_underdog = _fit_bias_penalized(
            selection_group, venue, best_penalty)
        favorite_code, underdog_code = _favorite_underdog_codes(venue)

        baseline = [row["_served"] for row in confirmation_group]
        candidate = [
            _apply_bias(
                row["_served"], favorite_code, underdog_code,
                bias_favorite, bias_underdog)
            for row in confirmation_group
        ]
        outcomes = [row["outcome"] for row in confirmation_group]

        log_loss_delta = np.array([
            _log_loss(base, outcome) - _log_loss(cand, outcome)
            for base, cand, outcome in zip(baseline, candidate, outcomes)
        ])
        brier_delta = np.array([
            _brier(base, outcome) - _brier(cand, outcome)
            for base, cand, outcome in zip(baseline, candidate, outcomes)
        ])
        argmax_flips = sum(
            1 for base, cand in zip(baseline, candidate)
            if max(base, key=base.get) != max(cand, key=cand.get))

        report[venue] = {
            "selection_matches": len(selection_group),
            "confirmation_matches": len(confirmation_group),
            "cv_scores_by_penalty": cv_scores,
            "selected_penalty": best_penalty,
            "bias_favorite_logit": bias_favorite,
            "bias_underdog_logit": bias_underdog,
            "log_loss": _paired_bootstrap(log_loss_delta),
            "brier": _paired_bootstrap(brier_delta),
            "argmax_flips_in_confirmation": argmax_flips,
            "argmax_flip_rate": argmax_flips / len(confirmation_group),
        }
    return report


def main() -> None:
    rows = _load(DEFAULT_INPUT)
    adopted = _load_adopted(DEFAULT_ADOPTED)
    report = evaluate(rows, adopted)

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    for venue, block in report.items():
        print(f"--- favorito {venue} (selección={block['selection_matches']}, "
              f"confirmación={block['confirmation_matches']}) ---", flush=True)
        print(f"  penalización L2 elegida por CV = {block['selected_penalty']}",
              flush=True)
        print(f"  sesgo favorito (logit) = {block['bias_favorite_logit']:+.4f}",
              flush=True)
        print(f"  sesgo no-favorito (logit) = {block['bias_underdog_logit']:+.4f}",
              flush=True)
        for metric in ("log_loss", "brier"):
            stats = block[metric]
            print(f"  {metric}: {stats['mean']:+.6f} "
                  f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
                  f"→ {stats['verdict']}", flush=True)
        print(f"  volteos de argmax en confirmación: "
              f"{block['argmax_flips_in_confirmation']} "
              f"({block['argmax_flip_rate']:.2%})", flush=True)
        print(flush=True)

    print(f"artefacto: {OUTPUT}", flush=True)


if __name__ == "__main__":
    main()
