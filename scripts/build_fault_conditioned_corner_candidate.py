"""Córners por equipo condicionados por faltas propias esperadas (`DEC-213`,
Fase 131).

La investigación de fallos de predicción encontró que, cuando un equipo
comete más faltas de las esperadas, falla más su línea de córners
(`home_corners_over_4_5`: +1.17 faltas de diferencia, IC95% `[0.57, 1.77]`;
`away_corners_over_4_5`: +0.95, IC95% `[0.39, 1.50]`). El modelo de córners
de Fase 84A (`scripts/run_phase_84a_team_count_markets.py`) construye su
vector de features concatenando el perfil causal -propio/rival/liga, con
suavizado- de las 11 métricas ya declaradas en `METRICS`, pero "faltas" no es
una de ellas: nunca ha sido covariable de nada.

Este script reutiliza exactamente la construcción de features, el corte
fit/selection/confirmation, la selección de alpha/mezcla y el `_gate()` de
Fase 84A -sin tocar ese módulo-, y añade un bloque de perfil de faltas propio
(mismo suavizado causal `_profile_values` que ya usan las otras 11 métricas)
sólo para el target `corners` (FULL_MATCH), que es la métrica de las dos
líneas con el hallazgo confirmado. El baseline de esta comparación es el
modelo de córners servido hoy -mismas features, sin faltas-, reconstruido con
el mismo código para que la comparación sea exacta, no una aproximación.

Uso:
    python -m scripts.build_fault_conditioned_corner_candidate

# Requirements:
#   joblib>=1.4
#   numpy>=2.0
#   scikit-learn>=1.5

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from scripts.run_phase_84a_team_count_markets import (  # noqa: E402
    MARKET_LINES,
    METRICS,
    _add,
    _add_league,
    _baselines,
    _binary_score,
    _dispersion,
    _features,
    _gate,
    _league_rate,
    _matches,
    _profile_values,
    _read_rows,
    _select_alpha,
)
from src.team_count_markets import (  # noqa: E402
    SklearnPoissonSolver,
    negative_binomial_over_probability,
    poisson_deviance,
)
from src.temporal_integrity import kickoff_buckets  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_131_fault_conditioned_corners"
TARGET_METRIC = "corners"
CANDIDATE_MARKETS = ("home_corners_over_4_5", "away_corners_over_4_5")
RNG_SEED = 20260818
N_BOOT = 10000


def _team_fouls(rows: list[dict[str, Any]]) -> dict[int, dict[bool, float]]:
    """Suma faltas por partido y equipo (partido completo, sin faltar sesgar)."""

    output: dict[int, dict[bool, float]] = defaultdict(lambda: {True: 0.0, False: 0.0})
    for row in rows:
        match_id = int(row["match_id"])
        output[match_id][bool(row["is_home"])] += float(row.get("fouls", 0) or 0)
    return output


def _attach_fouls(
    matches: list[dict[str, Any]], fouls_by_match: dict[int, dict[bool, float]],
) -> None:
    """Añade el target de faltas a los dicts home/away ya construidos."""

    for match in matches:
        totals = fouls_by_match[int(match["match_id"])]
        match["home"]["fouls"] = totals[True]
        match["away"]["fouls"] = totals[False]


def _fouls_safe_default(matches: list[dict[str, Any]]) -> float:
    """Media de faltas por equipo-partido en `fit`, para el suavizado causal."""

    values = [
        match[side]["fouls"]
        for match in matches if match["split"] == "fit"
        for side in ("home", "away")
    ]
    return float(np.mean(values))


def _build_examples(
    matches: list[dict[str, Any]], safe_default: float,
) -> list[dict[str, Any]]:
    """Genera ejemplos con features baseline y candidata, en orden causal."""

    team_history: dict[Any, list[float]] = {}
    league_history: dict[Any, list[float]] = {}
    fouls_team_history: dict[Any, list[float]] = {}
    fouls_league_history: dict[Any, list[float]] = {}
    output: list[dict[str, Any]] = []

    for bucket in kickoff_buckets(matches):
        bucket_examples = []
        for match in bucket:
            league_name = str(match["league_slug"])
            for home in (True, False):
                side, rival_side = ("home", "away") if home else ("away", "home")
                own_id = int(match[f"{side}_team_id"])
                rival_id = int(match[f"{rival_side}_team_id"])

                baseline_features = _features(
                    match, home, team_history, league_history)

                own_fouls = fouls_team_history.get(
                    (league_name, own_id, "fouls"), [0.0, 0.0, 0.0])
                rival_fouls = fouls_team_history.get(
                    (league_name, rival_id, "fouls"), [0.0, 0.0, 0.0])
                league_fouls = fouls_league_history.get(
                    (league_name, home, "fouls"), [0.0, 0.0])
                fouls_block = _profile_values(
                    own_fouls, rival_fouls, league_fouls, safe_default)
                candidate_features = baseline_features + fouls_block

                bucket_examples.append({
                    "match_id": match["match_id"], "league_slug": league_name,
                    "split": match["split"], "is_home": home,
                    "team_id": own_id, "opponent_team_id": rival_id,
                    "baseline_features": baseline_features,
                    "candidate_features": candidate_features,
                    "targets": match[side],
                    "baselines": _baselines(match, home, league_history),
                })
        output.extend(bucket_examples)
        for match in bucket:
            for home in (True, False):
                side, rival_side = ("home", "away") if home else ("away", "home")
                own_id = int(match[f"{side}_team_id"])
                league_name = str(match["league_slug"])
                own_fouls_value = match[side]["fouls"]
                rival_fouls_value = match[rival_side]["fouls"]
                _add(
                    fouls_team_history, (league_name, own_id, "fouls"),
                    own_fouls_value, rival_fouls_value)
                _add_league(
                    fouls_league_history, (league_name, home, "fouls"),
                    own_fouls_value)
        # Actualiza el historial de METRICS (corners, tiros, tarjetas...)
        # despues de todo el bucket, exactamente como `_examples` original.
        for match in bucket:
            for home in (True, False):
                side, rival_side = ("home", "away") if home else ("away", "home")
                own_id = int(match[f"{side}_team_id"])
                rival_id = int(match[f"{rival_side}_team_id"])
                league_name = str(match["league_slug"])
                for spec in METRICS:
                    own_value = match[side][spec.name]
                    rival_value = match[rival_side][spec.name]
                    _add(team_history, (league_name, own_id, spec.name),
                         own_value, rival_value)
                    _add_league(
                        league_history, (league_name, home, spec.name), own_value)
    return output


def _matrix_for(
    rows: list[dict[str, Any]], key: str,
) -> tuple[np.ndarray, np.ndarray]:
    """Matriz de features (`baseline_features` o `candidate_features`) y target."""

    features = np.asarray([row[key] for row in rows], dtype=float)
    targets = np.asarray([row["targets"][TARGET_METRIC] for row in rows], dtype=float)
    return features, targets


def _fit_variant(examples: list[dict[str, Any]], key: str) -> dict[str, Any]:
    """Ajusta alpha/mezcla en fit+selection y predice confirmation, para un
    conjunto de features dado (baseline o candidata)."""

    fit_rows = [row for row in examples if row["split"] == "fit"]
    selection_rows = [row for row in examples if row["split"] == "selection"]
    confirmation_rows = [row for row in examples if row["split"] == "confirmation"]
    train_rows = fit_rows + selection_rows

    # `_select_alpha` espera `row["features"]`; se alimenta con una vista que
    # apunta al bloque de features correspondiente sin duplicar `_matrix`.
    def _shimmed(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{**row, "features": row[key]} for row in rows]

    alpha, selection_scores, weight = _select_alpha(
        _shimmed(fit_rows), _shimmed(selection_rows), TARGET_METRIC)

    x_train, y_train = _matrix_for(train_rows, key)
    solver = SklearnPoissonSolver(alpha)
    solver.fit(x_train, y_train)
    dispersion = _dispersion(y_train)

    x_confirmation, _ = _matrix_for(confirmation_rows, key)
    raw_predicted = solver.predict(x_confirmation)
    expected = [
        float(weight * raw + (1.0 - weight) * row["baselines"][TARGET_METRIC])
        for raw, row in zip(raw_predicted, confirmation_rows)
    ]

    scored = []
    for row, expected_value in zip(confirmation_rows, expected):
        actual = row["targets"][TARGET_METRIC]
        baseline_value = row["baselines"][TARGET_METRIC]
        scored.append({
            "match_id": row["match_id"], "league_slug": row["league_slug"],
            "is_home": row["is_home"], "team_id": row["team_id"],
            "opponent_team_id": row["opponent_team_id"],
            "actual": actual, "expected": expected_value,
            "baseline": baseline_value,
            "metrics": {TARGET_METRIC: {
                "model_mae": abs(actual - expected_value),
                "baseline_mae": abs(actual - baseline_value),
                "model_deviance": poisson_deviance(actual, expected_value),
                "baseline_deviance": poisson_deviance(actual, baseline_value),
            }},
        })
    return {
        "alpha": alpha, "weight": weight, "dispersion": dispersion,
        "selection_scores": selection_scores, "scored": scored,
    }


def _count_metrics(scored: list[dict[str, Any]]) -> dict[str, Any]:
    """Deviance/MAE medios y estabilidad por liga, para un único conteo."""

    values = {
        key: float(np.mean([row["metrics"][TARGET_METRIC][key] for row in scored]))
        for key in ("model_mae", "baseline_mae", "model_deviance", "baseline_deviance")
    }
    values["league_nonnegative_rate"] = _league_rate(scored, TARGET_METRIC)
    return values


def _match_markets(
    scored: list[dict[str, Any]], dispersion: float,
) -> list[dict[str, Any]]:
    """Reconstruye las líneas comerciales de córners local/visitante."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in scored:
        grouped[int(row["match_id"])].append(row)

    rows = []
    for match_id, pair in grouped.items():
        home = next(row for row in pair if row["is_home"])
        away = next(row for row in pair if not row["is_home"])
        markets = {}
        for name in CANDIDATE_MARKETS:
            _, side, line = MARKET_LINES[name]
            team_row = home if side == "home" else away
            model_probability = negative_binomial_over_probability(
                team_row["expected"], dispersion, line)
            baseline_probability = negative_binomial_over_probability(
                team_row["baseline"], dispersion, line)
            actual = team_row["actual"] > line
            markets[name] = _binary_score(
                model_probability, baseline_probability, actual)
        rows.append({
            "match_id": match_id, "league_slug": home["league_slug"],
            "markets": markets,
        })
    return rows


def _market_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    output = {}
    for market in CANDIDATE_MARKETS:
        values = [row["markets"][market] for row in rows]
        output[market] = {
            key: float(np.mean([value[key] for value in values]))
            for key in ("model_log_loss", "baseline_log_loss",
                        "model_brier", "baseline_brier")
        }
    return output


def _bootstrap_delta_by_match(
    baseline_rows: list[dict[str, Any]], candidate_rows: list[dict[str, Any]],
    market: str, metric: str,
) -> dict[str, Any]:
    """IC95% bootstrap por partido del delta baseline-menos-candidato."""

    baseline_by_match = {row["match_id"]: row["markets"][market][metric]
                          for row in baseline_rows}
    candidate_by_match = {row["match_id"]: row["markets"][market][metric]
                           for row in candidate_rows}
    match_ids = sorted(set(baseline_by_match) & set(candidate_by_match))
    deltas = np.array([
        baseline_by_match[match_id] - candidate_by_match[match_id]
        for match_id in match_ids
    ])
    rng = np.random.default_rng(RNG_SEED)
    means = np.array([
        float(np.mean(rng.choice(deltas, size=len(deltas), replace=True)))
        for _ in range(N_BOOT)
    ])
    low, high = float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))
    return {
        "mean": float(np.mean(deltas)), "ci_low": low, "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high),
        "verdict": (
            "indistinguible" if low <= 0.0 <= high
            else ("candidato mejora" if np.mean(deltas) > 0
                  else "candidato degrada")),
    }


def run() -> dict[str, Any]:
    rows = _read_rows()
    matches = _matches(rows)
    fouls_by_match = _team_fouls(rows)
    _attach_fouls(matches, fouls_by_match)
    safe_default = _fouls_safe_default(matches)
    examples = _build_examples(matches, safe_default)

    baseline_fit = _fit_variant(examples, "baseline_features")
    candidate_fit = _fit_variant(examples, "candidate_features")

    baseline_counts = _count_metrics(baseline_fit["scored"])
    candidate_counts = _count_metrics(candidate_fit["scored"])

    baseline_market_rows = _match_markets(
        baseline_fit["scored"], baseline_fit["dispersion"])
    candidate_market_rows = _match_markets(
        candidate_fit["scored"], candidate_fit["dispersion"])
    baseline_markets = _market_metrics(baseline_market_rows)
    candidate_markets = _market_metrics(candidate_market_rows)

    # El gate exige comparar el candidato contra SU baseline, que aqui es el
    # modelo servido hoy (features sin faltas), reconstruido identico.
    gate_counts = {TARGET_METRIC: {
        "model_deviance": candidate_counts["model_deviance"],
        "baseline_deviance": baseline_counts["model_deviance"],
        "model_mae": candidate_counts["model_mae"],
        "baseline_mae": baseline_counts["model_mae"],
        "league_nonnegative_rate": candidate_counts["league_nonnegative_rate"],
    }}
    gate_markets = {
        name: {
            "model_log_loss": candidate_markets[name]["model_log_loss"],
            "baseline_log_loss": baseline_markets[name]["model_log_loss"],
            "model_brier": candidate_markets[name]["model_brier"],
            "baseline_brier": baseline_markets[name]["model_brier"],
        }
        for name in CANDIDATE_MARKETS
    }
    gate = _gate(gate_counts, gate_markets)

    bootstrap = {
        market: {
            metric: _bootstrap_delta_by_match(
                baseline_market_rows, candidate_market_rows, market, metric)
            for metric in ("model_log_loss", "model_brier")
        }
        for market in CANDIDATE_MARKETS
    }

    return {
        "fouls_safe_default": safe_default,
        "selection_scores": {
            "baseline": baseline_fit["selection_scores"],
            "candidate": candidate_fit["selection_scores"],
        },
        "alpha": {"baseline": baseline_fit["alpha"], "candidate": candidate_fit["alpha"]},
        "weight": {"baseline": baseline_fit["weight"], "candidate": candidate_fit["weight"]},
        "count_metrics": {
            "baseline": baseline_counts, "candidate": candidate_counts,
        },
        "market_metrics": {
            "baseline": baseline_markets, "candidate": candidate_markets,
        },
        "gate": gate,
        "bootstrap_by_match": bootstrap,
        "confirmation_matches": len(baseline_market_rows),
    }


def main() -> None:
    result = run()
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "report.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")

    print(f"faltas safe_default (media fit) = {result['fouls_safe_default']:.4f}",
          flush=True)
    print(f"confirmación: {result['confirmation_matches']} partidos\n", flush=True)

    counts = result["count_metrics"]
    print("--- conteo 'corners' (FULL_MATCH) ---", flush=True)
    print(f"  baseline  deviance={counts['baseline']['model_deviance']:.4f} "
          f"mae={counts['baseline']['model_mae']:.4f} "
          f"ligas+={counts['baseline']['league_nonnegative_rate']:.2%}", flush=True)
    print(f"  candidato deviance={counts['candidate']['model_deviance']:.4f} "
          f"mae={counts['candidate']['model_mae']:.4f} "
          f"ligas+={counts['candidate']['league_nonnegative_rate']:.2%}\n", flush=True)

    for market in CANDIDATE_MARKETS:
        print(f"--- {market} ---", flush=True)
        base = result["market_metrics"]["baseline"][market]
        cand = result["market_metrics"]["candidate"][market]
        print(f"  baseline  log_loss={base['model_log_loss']:.4f} "
              f"brier={base['model_brier']:.4f}", flush=True)
        print(f"  candidato log_loss={cand['model_log_loss']:.4f} "
              f"brier={cand['model_brier']:.4f}", flush=True)
        for metric in ("model_log_loss", "model_brier"):
            stats = result["bootstrap_by_match"][market][metric]
            print(f"  delta {metric} (baseline-candidato): {stats['mean']:+.6f} "
                  f"IC95% [{stats['ci_low']:+.6f}, {stats['ci_high']:+.6f}] "
                  f"→ {stats['verdict']}", flush=True)
        print(flush=True)

    print(f"gate: {json.dumps(result['gate'], indent=2)}", flush=True)
    print(f"\nartefacto: {OUTPUT / 'report.json'}", flush=True)


if __name__ == "__main__":
    main()
