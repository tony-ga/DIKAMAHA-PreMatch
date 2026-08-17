"""Evalúa los candidatos de `arquitectura_matematica_v1` sobre el corpus completo.

Consume las predicciones walk-forward ya generadas, de modo que cada candidato se
mide sin volver a ejecutar la cadena. Todos comparten el mismo protocolo, que es
lo que hace comparables sus resultados:

- el parámetro se elige en **selección** y se reporta en **confirmación**, con la
  partición congelada por Fase 74 (R2 — nadie elige dónde se mide);
- el efecto se resume con IC bootstrap pareado y el **partido completo** como
  unidad IID (R7, `DEC-006`);
- un intervalo que cruza cero significa que la muestra no distingue el candidato
  del baseline, no que el candidato mejore.

Candidatos evaluados:

1. **Peso de mezcla aprendido** (B.3): reestima `w` en
   `exp(w·ln λ_DC + (1-w)·ln λ_Kalman)` frente al `0.8` congelado de Fase 42.
2. **Escalado de temperatura global** (`DEC-199`).
3. **Escalado de temperatura por liga**: la continuación que `DEC-199` dejó
   abierta al encontrar que el sesgo de calibración cambia de signo entre bloques.

Uso:
    python -m scripts.evaluate_candidates

# Requirements:
#   numpy>=1.24
#   scipy>=1.10

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
from scipy.optimize import minimize_scalar

from src.kalman_v2 import poisson_matrix
from src.temperature_calibration import (
    TEMPERATURE_MAX,
    TEMPERATURE_MIN,
    apply_temperature,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "artifacts/walkforward_predictions/baseline.jsonl"
DEFAULT_OUTPUT = ROOT / "artifacts/candidate_evaluation"
LABELS = ("1", "X", "2")
FROZEN_BLEND_WEIGHT = 0.8
MIN_LEAGUE_MATCHES = 60


def _load(path: Path) -> list[dict[str, Any]]:
    """Carga las predicciones walk-forward."""

    with path.open("r", encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def _log_loss(probabilities: dict[str, float], outcome: str) -> float:
    """Log-verosimilitud negativa de un partido."""

    return -math.log(max(probabilities[outcome], 1e-15))


def _brier(probabilities: dict[str, float], outcome: str) -> float:
    """Brier multiclase de un partido."""

    return sum(
        (value - float(label == outcome)) ** 2
        for label, value in probabilities.items())


def _blend(row: dict[str, Any], weight: float) -> dict[str, float]:
    """Recompone 1X2 con un peso de mezcla dado, con el `tau` del propio ajuste."""

    dc_home, dc_away = row["lambda_dixon_coles"]
    k_home, k_away = row["lambda_kalman"]
    home = math.exp(weight * math.log(dc_home) + (1 - weight) * math.log(k_home))
    away = math.exp(weight * math.log(dc_away) + (1 - weight) * math.log(k_away))
    matrix = poisson_matrix(home, away, 12, float(row["tau_dc"]), True)
    return {
        "1": float(np.tril(matrix, -1).sum()),
        "X": float(np.trace(matrix)),
        "2": float(np.triu(matrix, 1).sum()),
    }


def _paired_bootstrap(
    deltas: np.ndarray, replicates: int = 10000, seed: int = 42,
) -> dict[str, Any]:
    """IC95% percentil del delta medio remuestreando partidos."""

    rng = np.random.default_rng(seed)
    means = np.array([
        float(np.mean(rng.choice(deltas, size=len(deltas), replace=True)))
        for _ in range(replicates)
    ])
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return {
        "mean": float(np.mean(deltas)),
        "ci_low": low,
        "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high),
        "verdict": (
            "indistinguible" if low <= 0.0 <= high
            else ("mejora confirmada" if np.mean(deltas) > 0
                  else "degradación confirmada")),
    }


def _compare(
    baseline: Sequence[dict[str, float]],
    candidate: Sequence[dict[str, float]],
    outcomes: Sequence[str],
) -> dict[str, Any]:
    """Compara dos conjuntos de probabilidades sobre los mismos partidos."""

    # Delta positivo = el candidato pierde menos, es decir mejora.
    log_loss = np.array([
        _log_loss(base, outcome) - _log_loss(cand, outcome)
        for base, cand, outcome in zip(baseline, candidate, outcomes)
    ], dtype=float)
    brier = np.array([
        _brier(base, outcome) - _brier(cand, outcome)
        for base, cand, outcome in zip(baseline, candidate, outcomes)
    ], dtype=float)
    return {
        "matches": len(outcomes),
        "baseline_log_loss": float(np.mean([
            _log_loss(base, outcome)
            for base, outcome in zip(baseline, outcomes)])),
        "candidate_log_loss": float(np.mean([
            _log_loss(cand, outcome)
            for cand, outcome in zip(candidate, outcomes)])),
        "log_loss": _paired_bootstrap(log_loss),
        "brier": _paired_bootstrap(brier),
    }


def _fit_blend_weight(rows: Sequence[dict[str, Any]]) -> float:
    """Elige el peso de mezcla que minimiza log-loss en selección."""

    def objective(weight: float) -> float:
        """Log-loss medio bajo un peso dado."""

        return float(np.mean([
            _log_loss(_blend(row, weight), row["outcome"]) for row in rows]))

    result = minimize_scalar(objective, bounds=(0.0, 1.0), method="bounded")
    return float(result.x)


def _fit_temperature(rows: Sequence[dict[str, Any]]) -> float:
    """Elige la temperatura que minimiza log-loss en selección."""

    def objective(temperature: float) -> float:
        """Log-loss medio bajo una temperatura dada."""

        return float(np.mean([
            _log_loss(
                apply_temperature(row["blended"], temperature), row["outcome"])
            for row in rows]))

    result = minimize_scalar(
        objective, bounds=(TEMPERATURE_MIN, TEMPERATURE_MAX), method="bounded")
    return float(result.x)


def _reliability(rows: Sequence[dict[str, Any]], key: str) -> dict[str, float]:
    """Confianza declarada media frente a acierto observado en el argmax."""

    declared = float(np.mean([max(row[key].values()) for row in rows]))
    hits = float(np.mean([
        max(row[key], key=row[key].get) == row["outcome"] for row in rows]))
    return {"declared": declared, "observed": hits, "gap": declared - hits}


def evaluate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Ejecuta los tres candidatos y devuelve su evidencia."""

    selection = [row for row in rows if row["split"] == "selection"]
    confirmation = [row for row in rows if row["split"] == "confirmation"]
    outcomes = [row["outcome"] for row in confirmation]
    baseline = [row["blended"] for row in confirmation]

    report: dict[str, Any] = {
        "protocol": "selection_fits_confirmation_reports",
        "unit": "complete_match",
        "selection_matches": len(selection),
        "confirmation_matches": len(confirmation),
        "leagues": len({row["league_slug"] for row in confirmation}),
        "baseline_reliability": _reliability(confirmation, "blended"),
    }

    # --- 1. Peso de mezcla aprendido -------------------------------------
    weight = _fit_blend_weight(selection)
    report["blend_weight"] = {
        "frozen_weight": FROZEN_BLEND_WEIGHT,
        "fitted_weight": weight,
        **_compare(
            baseline,
            [_blend(row, weight) for row in confirmation],
            outcomes),
    }

    # --- 2. Temperatura global -------------------------------------------
    temperature = _fit_temperature(selection)
    report["temperature_global"] = {
        "fitted_temperature": temperature,
        **_compare(
            baseline,
            [apply_temperature(row["blended"], temperature)
             for row in confirmation],
            outcomes),
    }

    # --- 3. Temperatura por liga -----------------------------------------
    by_league_selection: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in selection:
        by_league_selection[row["league_slug"]].append(row)

    per_league: dict[str, float] = {}
    for league, league_rows in by_league_selection.items():
        if len(league_rows) >= MIN_LEAGUE_MATCHES:
            per_league[league] = _fit_temperature(league_rows)

    # Una liga sin muestra suficiente se queda en la temperatura global: es la
    # degradación segura, no publicar un parámetro que no se pudo estimar.
    calibrated = [
        apply_temperature(
            row["blended"], per_league.get(row["league_slug"], temperature))
        for row in confirmation
    ]
    report["temperature_per_league"] = {
        "leagues_with_own_temperature": len(per_league),
        "min_matches_per_league": MIN_LEAGUE_MATCHES,
        "temperatures": dict(sorted(per_league.items())),
        "fallback_temperature": temperature,
        **_compare(baseline, calibrated, outcomes),
    }

    # --- 4. Composición: peso aprendido y DESPUÉS temperatura -------------
    # Los dos candidatos anteriores no son independientes: `T` se ajustó sobre
    # el blend congelado en 0.8. Si el peso cambia, las probabilidades cambian y
    # esa `T` deja de corresponder al modelo. R6 obliga a calibrar la salida del
    # modelo final, así que aquí se reajusta `T` sobre el blend ya reponderado,
    # ambos en selección, y se mide la composición completa en confirmación.
    reblended_selection = [
        {**row, "blended": _blend(row, weight)} for row in selection]
    composed_temperature = _fit_temperature(reblended_selection)
    composed = [
        apply_temperature(_blend(row, weight), composed_temperature)
        for row in confirmation
    ]
    report["blend_then_temperature"] = {
        "fitted_weight": weight,
        "temperature_refitted_on_reblended": composed_temperature,
        "temperature_if_not_refitted": temperature,
        **_compare(baseline, composed, outcomes),
        "reliability_after": _reliability(
            [{"calibrated": probabilities, "outcome": outcome}
             for probabilities, outcome in zip(composed, outcomes)],
            "calibrated"),
    }

    # --- 5. Efecto colateral sobre Over 2.5 y BTTS -----------------------
    # El peso de mezcla no es un parámetro de 1X2: cambia las lambdas, y de esas
    # mismas lambdas salen Over 2.5 y BTTS. Mejorar 1X2 degradando dos mercados
    # servidos sería un mal negocio que ninguna métrica de 1X2 revelaría.
    report["side_effects"] = {
        market: _compare_binary(confirmation, weight, market)
        for market in ("over_2_5", "btts")
    }

    return report


def _binary_markets(row: dict[str, Any], weight: float) -> float:
    """Recompone Over 2.5 o BTTS con un peso de mezcla dado."""

    dc_home, dc_away = row["lambda_dixon_coles"]
    k_home, k_away = row["lambda_kalman"]
    home = math.exp(weight * math.log(dc_home) + (1 - weight) * math.log(k_home))
    away = math.exp(weight * math.log(dc_away) + (1 - weight) * math.log(k_away))
    return poisson_matrix(home, away, 12, float(row["tau_dc"]), True)


def _compare_binary(
    rows: Sequence[dict[str, Any]], weight: float, market: str,
) -> dict[str, Any]:
    """Compara un mercado binario entre el peso congelado y el ajustado."""

    def _probability(matrix: np.ndarray) -> float:
        """Extrae la probabilidad del mercado desde la matriz conjunta."""

        if market == "over_2_5":
            grid = np.add.outer(
                np.arange(matrix.shape[0]), np.arange(matrix.shape[1]))
            return float(matrix[grid > 2].sum())
        return float(matrix[1:, 1:].sum())

    def _observed(row: dict[str, Any]) -> bool:
        """Deriva el resultado observado del mercado."""

        if market == "over_2_5":
            return row["home_goals"] + row["away_goals"] > 2
        return row["home_goals"] > 0 and row["away_goals"] > 0

    def _losses(current_weight: float) -> tuple[np.ndarray, np.ndarray]:
        """Log-loss y Brier por partido bajo un peso dado."""

        log_losses, briers = [], []
        for row in rows:
            probability = _probability(_binary_markets(row, current_weight))
            observed = _observed(row)
            value = probability if observed else 1.0 - probability
            log_losses.append(-math.log(max(value, 1e-15)))
            briers.append((probability - float(observed)) ** 2)
        return np.array(log_losses), np.array(briers)

    base_log, base_brier = _losses(FROZEN_BLEND_WEIGHT)
    cand_log, cand_brier = _losses(weight)
    return {
        "matches": len(rows),
        "baseline_log_loss": float(np.mean(base_log)),
        "candidate_log_loss": float(np.mean(cand_log)),
        "log_loss": _paired_bootstrap(base_log - cand_log),
        "brier": _paired_bootstrap(base_brier - cand_brier),
    }


def main() -> None:
    """Evalúa los candidatos y publica el reporte."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    rows = _load(args.input)
    report = evaluate(rows)

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "report.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(f"selección={report['selection_matches']} "
          f"confirmación={report['confirmation_matches']} "
          f"ligas={report['leagues']}\n", flush=True)
    reliability = report["baseline_reliability"]
    print(f"baseline: confianza {reliability['declared']:.4f} vs acierto "
          f"{reliability['observed']:.4f} (brecha {reliability['gap']:+.4f})\n",
          flush=True)

    for name in ("blend_weight", "temperature_global", "temperature_per_league",
                 "blend_then_temperature"):
        block = report[name]
        print(f"--- {name} ---", flush=True)
        if "fitted_weight" in block:
            frozen = block.get("frozen_weight", FROZEN_BLEND_WEIGHT)
            print(f"  peso ajustado {block['fitted_weight']:.4f} "
                  f"(congelado {frozen})", flush=True)
        if "fitted_temperature" in block:
            print(f"  T ajustada {block['fitted_temperature']:.4f}", flush=True)
        if "leagues_with_own_temperature" in block:
            print(f"  ligas con T propia: "
                  f"{block['leagues_with_own_temperature']}", flush=True)
        if "temperature_refitted_on_reblended" in block:
            print(f"  T reajustada sobre el blend nuevo "
                  f"{block['temperature_refitted_on_reblended']:.4f} "
                  f"(sin reajustar habría sido "
                  f"{block['temperature_if_not_refitted']:.4f})", flush=True)
            after = block["reliability_after"]
            print(f"  fiabilidad tras componer: confianza "
                  f"{after['declared']:.4f} vs acierto {after['observed']:.4f} "
                  f"(brecha {after['gap']:+.4f})", flush=True)
        for metric in ("log_loss", "brier"):
            stats = block[metric]
            print(f"  {metric}: {stats['mean']:+.6f} "
                  f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
                  f"→ {stats['verdict']}", flush=True)
        print(flush=True)

    print("--- efecto colateral del peso sobre otros mercados ---", flush=True)
    for market, block in report["side_effects"].items():
        for metric in ("log_loss", "brier"):
            stats = block[metric]
            print(f"  {market} {metric}: {stats['mean']:+.6f} "
                  f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
                  f"→ {stats['verdict']}", flush=True)
    print(flush=True)

    print(f"artefacto: {args.output / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
