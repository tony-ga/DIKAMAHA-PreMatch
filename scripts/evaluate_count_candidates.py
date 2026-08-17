"""Contracción jerárquica de tasas de conteo y filtro de matrices aleatorias.

Dos candidatos de `arquitectura_matematica_v1` que parecían depender de una
ingesta nueva y no dependen: las micro-ventanas de Fase 74 traen córners, tiros,
tiros a puerta, tarjetas y faltas por equipo, así que el corpus a nivel partido
los cubre.

**1. Contracción jerárquica (B.2, R5).** Al predecir el conteo de un equipo se
mezcla su historia con la media de liga. Hoy esa mezcla usa un `shrinkage`
constante -el mismo para un equipo con 3 partidos y otro con 60-. R5 deriva el
peso de los datos: `w_j = sigma_j²/(sigma_j²+tau²)`, con `tau²` la varianza
**entre equipos** y `sigma_j²` la incertidumbre dentro del equipo `j`. Se compara
contra el shrinkage fijo y contra la media de liga pura.

**2. Marchenko-Pastur (C.2).** El proyecto estima una correlación escalar por
métrica entre local y visitante. Con doce variables de conteo se puede construir
la matriz completa y contrastar sus autovalores contra el rango que produciría el
puro azar: los de dentro son ruido de muestreo, sólo los de fuera son estructura.

Todo se ajusta en `selection` y se reporta en `confirmation`, con la partición
congelada de Fase 74 y el partido como unidad.

Uso:
    python -m scripts.evaluate_count_candidates

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
DEFAULT_OUTPUT = ROOT / "artifacts/candidate_evaluation"

METRICS = ("corners", "shots", "shots_on_target", "yellow_cards", "fouls")
FIXED_SHRINKAGE = 5.0
MIN_HISTORY = 3


def _bootstrap(deltas: np.ndarray, replicates: int = 10000, seed: int = 42):
    """IC95% percentil del delta medio remuestreando partidos."""

    rng = np.random.default_rng(seed)
    means = np.array([
        float(np.mean(rng.choice(deltas, size=len(deltas), replace=True)))
        for _ in range(replicates)])
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return {
        "mean": float(np.mean(deltas)), "ci_low": low, "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high),
        "verdict": (
            "indistinguible" if low <= 0.0 <= high
            else ("mejora confirmada" if np.mean(deltas) > 0
                  else "degradación confirmada")),
    }


def _team_observations(frame: pd.DataFrame, metric: str) -> list[dict[str, Any]]:
    """Aplana el corpus a observaciones equipo-partido, en orden causal."""

    rows: list[dict[str, Any]] = []
    for record in frame.to_dict("records"):
        for side in ("home", "away"):
            rows.append({
                "match_id": record["match_id"],
                "match_date": record["match_date"],
                "league": record["league_slug"],
                "split": record["split"],
                "team_id": record[f"{side}_team_id"],
                "value": float(record[f"{side}_{metric}"]),
            })
    rows.sort(key=lambda item: (item["match_date"], item["match_id"]))
    return rows


def _evaluate_metric(rows: list[dict[str, Any]], metric: str) -> dict[str, Any]:
    """Compara media de liga, shrinkage fijo y contracción jerárquica."""

    league_sum: dict[str, float] = defaultdict(float)
    league_count: dict[str, int] = defaultdict(int)
    team_sum: dict[tuple[str, int], float] = defaultdict(float)
    team_count: dict[tuple[str, int], int] = defaultdict(int)
    team_sq: dict[tuple[str, int], float] = defaultdict(float)

    errors = {"league": [], "fixed": [], "hierarchical": []}
    splits: list[str] = []

    for row in rows:
        league, team = row["league"], (row["league"], row["team_id"])
        observed = row["value"]

        if league_count[league] >= 20 and team_count[team] >= MIN_HISTORY:
            league_mean = league_sum[league] / league_count[league]
            team_mean = team_sum[team] / team_count[team]
            n = team_count[team]

            # `tau²`: dispersión real entre equipos de la liga, aproximada por
            # la varianza de las medias observadas menos el ruido de muestreo.
            within = max(
                team_sq[team] / n - team_mean ** 2, 1e-6)
            tau_squared = max(
                league_sum[league] / league_count[league] * 0.0 + _between(
                    league, team_sum, team_count), 1e-6)
            sigma_squared = within / n
            weight = sigma_squared / (sigma_squared + tau_squared)

            hierarchical = weight * league_mean + (1 - weight) * team_mean
            fixed = ((team_sum[team] + FIXED_SHRINKAGE * league_mean)
                     / (n + FIXED_SHRINKAGE))

            errors["league"].append((league_mean - observed) ** 2)
            errors["fixed"].append((fixed - observed) ** 2)
            errors["hierarchical"].append((hierarchical - observed) ** 2)
            splits.append(row["split"])

        league_sum[league] += observed
        league_count[league] += 1
        team_sum[team] += observed
        team_count[team] += 1
        team_sq[team] += observed ** 2

    mask = np.array([split == "confirmation" for split in splits], dtype=bool)
    if mask.sum() < 50:
        return {"metric": metric, "skipped": "muestra insuficiente"}

    league_err = np.array(errors["league"])[mask]
    fixed_err = np.array(errors["fixed"])[mask]
    hier_err = np.array(errors["hierarchical"])[mask]

    return {
        "metric": metric,
        "confirmation_observations": int(mask.sum()),
        "mse_league_mean": float(np.mean(league_err)),
        "mse_fixed_shrinkage": float(np.mean(fixed_err)),
        "mse_hierarchical": float(np.mean(hier_err)),
        "hierarchical_vs_fixed": _bootstrap(fixed_err - hier_err),
        "hierarchical_vs_league_mean": _bootstrap(league_err - hier_err),
        "fixed_vs_league_mean": _bootstrap(league_err - fixed_err),
    }


_between_cache: dict[str, float] = {}


def _between(
    league: str, team_sum: dict[tuple[str, int], float],
    team_count: dict[tuple[str, int], int],
) -> float:
    """Varianza entre equipos de la liga, con sus medias actuales."""

    means = [
        team_sum[key] / team_count[key]
        for key in team_count
        if key[0] == league and team_count[key] >= MIN_HISTORY
    ]
    if len(means) < 3:
        return 1e-6
    return float(np.var(means, ddof=1))


def _marchenko_pastur(frame: pd.DataFrame) -> dict[str, Any]:
    """Contrasta los autovalores de la correlación contra el rango del azar."""

    columns = [
        f"{side}_{metric}" for side in ("home", "away") for metric in METRICS]
    data = frame[columns].to_numpy(dtype=float)
    observations, variables = data.shape

    standardized = (data - data.mean(axis=0)) / (data.std(axis=0, ddof=1) + 1e-12)
    correlation = np.corrcoef(standardized, rowvar=False)
    eigenvalues = np.sort(np.linalg.eigvalsh(correlation))[::-1]

    ratio = variables / observations
    upper = (1 + np.sqrt(ratio)) ** 2
    lower = (1 - np.sqrt(ratio)) ** 2

    outside = [float(value) for value in eigenvalues if value > upper]
    return {
        "observations": int(observations),
        "variables": int(variables),
        "ratio_q": float(ratio),
        "mp_lower_bound": float(lower),
        "mp_upper_bound": float(upper),
        "eigenvalues": [float(value) for value in eigenvalues],
        "eigenvalues_above_noise": outside,
        "signal_components": len(outside),
        "variance_explained_by_signal": float(sum(outside) / variables),
    }


def main() -> None:
    """Mide los dos candidatos y publica su evidencia."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    frame = pd.read_csv(args.corpus)
    frame = frame.sort_values(["match_date", "match_id"])

    report: dict[str, Any] = {"hierarchical_counts": {}}
    for metric in METRICS:
        result = _evaluate_metric(_team_observations(frame, metric), metric)
        report["hierarchical_counts"][metric] = result
        if "skipped" in result:
            print(f"{metric}: {result['skipped']}", flush=True)
            continue
        print(f"--- {metric} ({result['confirmation_observations']} obs) ---",
              flush=True)
        print(f"  MSE media de liga {result['mse_league_mean']:.4f} | "
              f"shrinkage fijo {result['mse_fixed_shrinkage']:.4f} | "
              f"jerárquico {result['mse_hierarchical']:.4f}", flush=True)
        for name in ("hierarchical_vs_fixed", "hierarchical_vs_league_mean"):
            stats = result[name]
            print(f"  {name}: {stats['mean']:+.6f} "
                  f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
                  f"→ {stats['verdict']}", flush=True)
        print(flush=True)

    confirmation = frame[frame["split"] == "confirmation"]
    report["marchenko_pastur"] = _marchenko_pastur(confirmation)
    mp = report["marchenko_pastur"]
    print("--- Marchenko-Pastur sobre la correlación de conteos ---", flush=True)
    print(f"  {mp['variables']} variables, {mp['observations']} partidos, "
          f"q={mp['ratio_q']:.5f}", flush=True)
    print(f"  banda de ruido [{mp['mp_lower_bound']:.4f}, "
          f"{mp['mp_upper_bound']:.4f}]", flush=True)
    print(f"  autovalores fuera de la banda: {mp['signal_components']} "
          f"de {mp['variables']}", flush=True)
    print(f"  varianza en componentes de señal: "
          f"{mp['variance_explained_by_signal']:.1%}", flush=True)

    args.output.mkdir(parents=True, exist_ok=True)
    path = args.output / "count_candidates.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")
    print(f"\nartefacto: {path}", flush=True)


if __name__ == "__main__":
    main()
