"""Temperatura de 1X2 condicionada por localía del favorito (`DEC-211`).

La investigación de fallos de predicción sobre 1,000 partidos encontró que un
favorito que juega de visitante falla más que uno que juega de local
(-10.75pp, IC95% no cruza cero). `DEC-201` midió que la temperatura de
calibración 1X2 no debería depender de la liga -indistinguible y peor que la
global-, pero nunca se probó si depende de la localía del favorito, que es
una hipótesis distinta.

Mismo protocolo y mismo corpus que `scripts/evaluate_candidates.py`
(`DEC-200`/`DEC-201`): selección elige el parámetro, confirmación lo mide,
partido completo como unidad IID, bootstrap pareado de 10,000 réplicas. El
grupo -favorito local o favorito visitante- se deriva de la probabilidad ya
servida (peso 0.642848 + T global 1.198935 adoptados), nunca del marcador del
partido objetivo. La contracción jerárquica hacia la T global sigue
`scripts/evaluate_hierarchical_blend.py` (`DEC-202`), con la salvedad -
documentada explícitamente en el reporte- de que aquí sólo hay dos grupos, así
que `tau^2` entre grupos es una estimación débil con dos puntos.

La comparación es siempre contra la composición ya adoptada y servida en
`artifacts/phase_124_temperature_calibration/match_result_1x2.json` (peso
0.642848, T 1.198935), no contra `T=1` ni contra el peso sin recalibrar.

Uso:
    python -m scripts.evaluate_favorite_venue_temperature

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
from src.temperature_calibration import (
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    apply_temperature,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "artifacts/walkforward_predictions/baseline.jsonl"
DEFAULT_ADOPTED = ROOT / "artifacts/phase_124_temperature_calibration/match_result_1x2.json"
DEFAULT_OUTPUT = ROOT / "artifacts/candidate_evaluation"


def _load_adopted(path: Path) -> dict[str, float]:
    """Lee el peso y la temperatura ya servidos en producción."""

    config = json.loads(path.read_text(encoding="utf-8"))
    return {
        "weight": float(config["blend_weight"]),
        "temperature": float(config["temperature"]),
    }


def _served(row: dict[str, Any], weight: float, temperature: float) -> dict[str, float]:
    """Probabilidad exactamente como la sirve producción hoy."""

    return apply_temperature(_blend(row, weight), temperature)


def _favorite_venue(probabilities: dict[str, float]) -> str | None:
    """Deriva qué lado es favorito a partir de la probabilidad ya servida.

    Devuelve `None` cuando el argmax es empate ("X"), igual que la
    investigación original excluyó los favoritos de empate.
    """

    best = max(probabilities, key=probabilities.get)
    if best == "1":
        return "home"
    if best == "2":
        return "away"
    return None


def _fit_temperature_on(rows: Sequence[dict[str, Any]], weight: float) -> float:
    """Temperatura que minimiza log-loss del blend (sin T global) en `rows`."""

    def objective(temperature: float) -> float:
        return float(np.mean([
            _log_loss(apply_temperature(_blend(row, weight), temperature), row["outcome"])
            for row in rows
        ]))

    result = minimize_scalar(
        objective, bounds=(TEMPERATURE_MIN, TEMPERATURE_MAX), method="bounded")
    return float(result.x)


def evaluate(rows: list[dict[str, Any]], adopted: dict[str, float]) -> dict[str, Any]:
    """Ajusta T por localía del favorito en selección y mide en confirmación."""

    weight = adopted["weight"]
    global_temperature = adopted["temperature"]

    selection = [row for row in rows if row["split"] == "selection"]
    confirmation = [row for row in rows if row["split"] == "confirmation"]

    for row in selection + confirmation:
        row["_served"] = _served(row, weight, global_temperature)
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

    raw_temperature = {
        venue: _fit_temperature_on(group_rows, weight)
        for venue, group_rows in selection_by_venue.items()
    }

    values = np.array(list(raw_temperature.values()), dtype=float)
    tau_squared = float(np.var(values, ddof=1)) if len(values) > 1 else 0.0
    counts = {venue: len(rows_) for venue, rows_ in selection_by_venue.items()}
    scale = float(np.median(list(counts.values()))) * tau_squared

    shrinkage_lambda: dict[str, float] = {}
    shrunk_temperature: dict[str, float] = {}
    for venue, temperature in raw_temperature.items():
        sigma_squared = scale / counts[venue] if counts[venue] else float("inf")
        lam = (sigma_squared / (sigma_squared + tau_squared)
               if tau_squared > 0 else 1.0)
        shrinkage_lambda[venue] = lam
        shrunk_temperature[venue] = (
            lam * global_temperature + (1 - lam) * temperature)

    def _candidate_probabilities(temperature_of: dict[str, float]) -> list[dict[str, float]]:
        return [
            apply_temperature(
                _blend(row, weight),
                temperature_of[row["_favorite_venue"]])
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

    shrunk_candidate = _candidate_probabilities(shrunk_temperature)
    raw_candidate = _candidate_probabilities(raw_temperature)

    return {
        "protocol": "selection_fits_confirmation_reports",
        "unit": "complete_match",
        "grouping": "favorite_venue (home | away), derived from served argmax",
        "adopted_weight": weight,
        "adopted_global_temperature": global_temperature,
        "selection_matches_total": len(selection),
        "selection_matches_by_venue": counts,
        "confirmation_matches_total": len(confirmation),
        "confirmation_matches_favored": len(confirmation_favored),
        "confirmation_excluded_draw_favorite": (
            len(confirmation) - len(confirmation_favored)),
        "raw_temperature_by_venue": raw_temperature,
        "tau_squared_between_venues": tau_squared,
        "caveat_two_groups": (
            "tau^2 se estima con solo 2 grupos (home/away); es una "
            "estimacion debil de la varianza entre grupos, a diferencia de "
            "DEC-202 que tenia muchas ligas. Interpretar con cautela."
        ),
        "shrinkage_lambda_by_venue": shrinkage_lambda,
        "shrunk_temperature_by_venue": shrunk_temperature,
        "hierarchical_vs_served": _compare(shrunk_candidate),
        "unshrunk_vs_served": _compare(raw_candidate),
    }


def main() -> None:
    """Ejecuta el candidato y publica el reporte."""

    rows = _load(DEFAULT_INPUT)
    adopted = _load_adopted(DEFAULT_ADOPTED)
    report = evaluate(rows, adopted)

    DEFAULT_OUTPUT.mkdir(parents=True, exist_ok=True)
    output_path = DEFAULT_OUTPUT / "favorite_venue_temperature.json"
    output_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(f"selección total={report['selection_matches_total']} "
          f"(local={report['selection_matches_by_venue']['home']}, "
          f"visitante={report['selection_matches_by_venue']['away']})", flush=True)
    print(f"confirmación favorecida={report['confirmation_matches_favored']} "
          f"(excluidos por empate favorito="
          f"{report['confirmation_excluded_draw_favorite']})\n", flush=True)
    print(f"T global adoptada = {report['adopted_global_temperature']:.6f}", flush=True)
    print(f"T local (sin contraer)     = "
          f"{report['raw_temperature_by_venue']['home']:.6f}", flush=True)
    print(f"T visitante (sin contraer) = "
          f"{report['raw_temperature_by_venue']['away']:.6f}", flush=True)
    print(f"tau^2 entre grupos = {report['tau_squared_between_venues']:.6f}", flush=True)
    print(f"T local (contraida)     = "
          f"{report['shrunk_temperature_by_venue']['home']:.6f}", flush=True)
    print(f"T visitante (contraida) = "
          f"{report['shrunk_temperature_by_venue']['away']:.6f}\n", flush=True)

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
