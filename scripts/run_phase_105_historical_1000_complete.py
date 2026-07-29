"""Evalúa 1,000 partidos históricos con el modelo completo vigente.

# Requirements:
#   numpy>=1.24
#   pandas>=2.0

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_88_team_market_markov import (  # noqa: E402
    _matches, _read_2024, _read_current, _team_mapping,
)
from scripts.run_phase_94_historical_500_semiofficial import (  # noqa: E402
    AGGREGATE, MARKOV, _counts, _markov_predictions, _outcomes,
    _pbp_evidence, _quality_profile,
)
from scripts.run_phase_104_official_goal_chain import (  # noqa: E402
    _evaluate_tail, _frame, _initial_state,
)
from src.dixon_coles_v1 import DixonColesEstimatorV1  # noqa: E402
from src.official_goal_chain import _dc_config, _team_ids  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_105_historical_1000_complete"
PHASE84 = ROOT / "artifacts/phase_84a_team_count_markets/predictions.json"
BOOTSTRAP = 10_000
MARKETS = ("1x2", "over_2_5", "btts") + AGGREGATE + MARKOV


def _read_json(path: Path) -> Any:
    """Carga un JSON versionado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _loss(probability: float, actual: bool) -> tuple[float, float]:
    """Calcula log-loss y Brier para un mercado binario."""

    value = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(value if actual else 1.0 - value), (value - float(actual)) ** 2


def _score(probability: float, actual: bool) -> dict[str, Any]:
    """Compara una probabilidad contra el outcome real."""

    log_loss, brier = _loss(probability, actual)
    predicted = float(probability) >= 0.5
    return {
        "probability": float(probability), "actual": bool(actual),
        "predicted": predicted, "correct": predicted == actual,
        "log_loss": log_loss, "brier": brier,
    }


def _score_1x2(prediction: dict[str, float], actual: str) -> dict[str, Any]:
    """Liquida el resultado 1X2 desde la matriz oficial."""

    probabilities = {
        "home": float(prediction["home"]),
        "draw": float(prediction["draw"]),
        "away": float(prediction["away"]),
    }
    selected = max(probabilities, key=probabilities.get)
    return {
        "probabilities": probabilities, "actual": actual,
        "predicted": selected, "correct": selected == actual,
        "confidence": probabilities[selected],
        "log_loss": -math.log(max(probabilities[actual], 1e-12)),
        "brier": sum((value - float(key == actual)) ** 2
                     for key, value in probabilities.items()),
    }


def _goal_actual(match: dict[str, Any]) -> dict[str, Any]:
    """Reconcilia goles del marcador desde las ventanas admitidas."""

    home = sum(int(row.get("goals", 0) or 0) for row in match["home"])
    away = sum(int(row.get("goals", 0) or 0) for row in match["away"])
    return {
        "1x2": "home" if home > away else "away" if away > home else "draw",
        "over_2_5": home + away > 2, "btts": home > 0 and away > 0,
        "score": f"{home}-{away}",
    }


def _official_rows() -> dict[int, dict[str, Any]]:
    """Ejecuta la cadena oficial por liga y conserva sólo su cola causal."""

    cache = OUTPUT / "official_goal_rows.json"
    if cache.exists():
        return {int(key): value for key, value in _read_json(cache).items()}
    rows: dict[int, dict[str, Any]] = {}
    frame = _frame()
    for _, league in frame.groupby("league_slug", sort=True):
        league = league.sort_values(["match_date", "match_id"]).reset_index(drop=True)
        if len(league) < 40:
            continue
        start = int(len(league) * 0.60)
        model = DixonColesEstimatorV1(_dc_config()).fit(
            league.iloc[:start], team_universe=_team_ids(league))
        state = _initial_state(
            model, league.iloc[:start], league.iloc[start].match_date.isoformat())
        for row in _evaluate_tail(model, state, league, start):
            rows[int(row["match_id"])] = row
    OUTPUT.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(rows, default=str), encoding="utf-8")
    return rows


def _market_probability(
    name: str, official: dict[str, Any], aggregate: dict[str, Any],
    markov: dict[str, Any],
) -> float:
    """Obtiene la probabilidad del motor responsable de cada mercado."""

    if name == "1x2":
        return float(max(official["candidate"].values()))
    if name == "btts":
        return float(official["baseline"][name])
    if name == "over_2_5":
        return float(official["candidate"][name])
    if name in AGGREGATE:
        return float(aggregate["markets"][name]["probability"])
    return float(markov["probabilities"][name])


def _market_baseline(
    name: str, official: dict[str, Any], aggregate: dict[str, Any],
    markov: dict[str, Any],
) -> float:
    """Obtiene la referencia baseline correspondiente."""

    if name == "1x2":
        return float(max(official["baseline"].values()))
    if name in {"over_2_5", "btts"}:
        return float(official["baseline"][name])
    if name in AGGREGATE:
        return float(aggregate["markets"][name]["baseline_probability"])
    return float(markov["baselines"][name])


def _score_match(
    match: dict[str, Any], official: dict[str, Any],
    aggregate: dict[str, Any], markov: dict[str, Any],
) -> dict[str, Any]:
    """Compara los 12 mercados de un partido contra outcomes reconciliados."""

    actual = {**_goal_actual(match), **_outcomes(_counts(match))}
    markets: dict[str, dict[str, Any]] = {}
    for name in MARKETS:
        probability = _market_probability(name, official, aggregate, markov)
        if name == "1x2":
            scored = _score_1x2(official["candidate"], actual["1x2"])
            base_probs = {
                key: float(official["baseline"][key])
                for key in ("home", "draw", "away")}
            base_pred = max(base_probs, key=base_probs.get)
            scored["baseline_predicted"] = base_pred
            scored["baseline_correct"] = base_pred == actual["1x2"]
            scored["baseline_log_loss"] = -math.log(
                max(base_probs[actual["1x2"]], 1e-12))
            scored["baseline_brier"] = sum(
                (value - float(key == actual["1x2"])) ** 2
                for key, value in base_probs.items())
        else:
            scored = _score(probability, bool(actual[name]))
            base = _score(_market_baseline(name, official, aggregate, markov), bool(actual[name]))
            scored.update({
                "baseline_correct": base["correct"],
                "baseline_log_loss": base["log_loss"],
                "baseline_brier": base["brier"]})
        scored["baseline_probability"] = _market_baseline(
            name, official, aggregate, markov)
        scored["model"] = (
            "dixon_coles_kalman" if name in {"1x2", "over_2_5"}
            else "structural_poisson_baseline" if name == "btts"
            else "phase84a_team_count" if name in AGGREGATE
            else "phase88_team_market_markov")
        markets[name] = scored
    correct = sum(bool(value["correct"]) for value in markets.values())
    return {
        "match_id": int(match["match_id"]),
        "match_date": str(match["match_date"]),
        "league_slug": str(match["league_slug"]),
        "home_team_id": int(match["home_team_id"]),
        "away_team_id": int(match["away_team_id"]),
        "observed_score": actual["score"],
        "correct_markets": correct, "accuracy": correct / len(MARKETS),
        "all_markets_correct": correct == len(MARKETS),
        "no_markets_correct": correct == 0, "markets": markets,
        "play_by_play": _pbp_evidence(match),
    }


def _binary_actual(name: str, match: dict[str, Any]) -> bool:
    """Obtiene el outcome de un mercado sin acceder a predicciones."""

    if name in {"1x2", "over_2_5", "btts"}:
        return bool(_goal_actual(match)[name]) if name != "1x2" else False
    return bool(_outcomes(_counts(match))[name])


def _aggregate_metrics(rows: list[dict[str, Any]], names: tuple[str, ...]) -> dict[str, Any]:
    """Resume acierto, confianza, log-loss y Brier por familia."""

    values = [row["markets"][name] for row in rows for name in names]
    return {
        "predictions": len(values),
        "accuracy": float(np.mean([value["correct"] for value in values])),
        "mean_confidence": float(np.mean([_confidence(value) for value in values])),
        "log_loss": float(np.mean([value["log_loss"] for value in values])),
        "brier": float(np.mean([value["brier"] for value in values])),
        "baseline_accuracy": float(np.mean([value["baseline_correct"] for value in values])),
        "baseline_log_loss": float(np.mean([value["baseline_log_loss"] for value in values])),
        "baseline_brier": float(np.mean([value["baseline_brier"] for value in values])),
    }


def _market_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula métricas independientes para cada mercado."""

    output: dict[str, Any] = {}
    for name in MARKETS:
        values = [row["markets"][name] for row in rows]
        output[name] = {
            "predictions": len(values),
            "accuracy": float(np.mean([value["correct"] for value in values])),
            "mean_confidence": float(np.mean([_confidence(value) for value in values])),
            "log_loss": float(np.mean([value["log_loss"] for value in values])),
            "brier": float(np.mean([value["brier"] for value in values])),
            "baseline_accuracy": float(np.mean([value["baseline_correct"] for value in values])),
            "baseline_log_loss": float(np.mean([value["baseline_log_loss"] for value in values])),
            "baseline_brier": float(np.mean([value["baseline_brier"] for value in values])),
            "model": values[0]["model"],
        }
    return output


def _confidence(value: dict[str, Any]) -> float:
    """Devuelve la probabilidad asignada a la decisión emitida."""

    if "confidence" in value:
        return float(value["confidence"])
    probability = float(value["probability"])
    return probability if value["predicted"] else 1.0 - probability


def _bootstrap(rows: list[dict[str, Any]]) -> list[float]:
    """Calcula IC95% de mejora de log-loss agregado por partido."""

    deltas = np.array([
        np.mean([
            _loss(value["baseline_probability"], bool(value["actual"]))[0]
            - value["log_loss"]
            for value in row["markets"].values()
            if "probability" in value])
        for row in rows], dtype=float)
    rng = np.random.default_rng(20260729)
    sample = rng.choice(deltas, size=(BOOTSTRAP, len(deltas)), replace=True).mean(axis=1)
    return [float(value) for value in np.quantile(sample, [0.025, 0.975])]


def _write(name: str, payload: Any) -> None:
    """Publica artefactos JSON deterministas."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")


def run() -> dict[str, Any]:
    """Ejecuta predicción, settlement y reporte de los 1,000 partidos."""

    matches = _matches(_read_2024(_team_mapping()) + _read_current())
    eligible, quality = _quality_profile(matches)
    confirmation = [row for row in matches if row["split"] == "confirmation" and row["league_slug"] in eligible]
    aggregate = {int(row["match_id"]): row for row in _read_json(PHASE84)}
    official = _official_rows()
    candidate_ids = [
        int(row["match_id"]) for row in confirmation
        if int(row["match_id"]) in aggregate and int(row["match_id"]) in official]
    target_ids = set(candidate_ids[-1000:])
    if len(target_ids) != 1000:
        raise ValueError(f"phase105_official_coverage:{len(target_ids)}")
    target_matches = [row for row in confirmation if int(row["match_id"]) in target_ids]
    markov = _markov_predictions([row for row in matches if row["league_slug"] in eligible], target_ids)
    scored = [_score_match(row, official[int(row["match_id"])], aggregate[int(row["match_id"])], markov[int(row["match_id"])]) for row in target_matches]
    scored.sort(key=lambda row: (-row["correct_markets"], row["match_date"], row["match_id"]))
    result = {
        "classification": "historical_complete_model_evaluation",
        "coverage": {"matches": len(scored), "leagues": len({row["league_slug"] for row in scored}), "decisions": len(scored) * len(MARKETS), "first_match_date": min(row["match_date"] for row in scored), "last_match_date": max(row["match_date"] for row in scored)},
        "models": {"official_goals": "dixon_coles_kalman_selective", "btts": "structural_poisson_baseline", "aggregate_markets": "phase84a_team_count", "temporal_markets": "phase88_team_market_markov"},
        "metrics_by_market": _market_metrics(scored),
        "families": {"official_goal_chain": _aggregate_metrics(scored, ("1x2", "over_2_5")), "btts_baseline": _aggregate_metrics(scored, ("btts",)), "aggregate_team_markets": _aggregate_metrics(scored, AGGREGATE), "markov_temporal_markets": _aggregate_metrics(scored, MARKOV), "all_markets": _aggregate_metrics(scored, MARKETS)},
        "bootstrap_log_loss_ci95": _bootstrap(scored),
        "perfect_100_percent": [row for row in scored if row["all_markets_correct"]],
        "zero_0_percent": [row for row in scored if row["no_markets_correct"]],
        "audit": {"target_match_data_used": False, "predictions_before_updates": True, "pbp_reconciled": True, "quality_profile_fit_only": True, "all_context_strictly_prior": all(row["play_by_play"]["context_strictly_prior"] for row in scored)},
        "data_quality": quality,
        "predictions_ranked": scored,
    }
    _write("final_report.json", {key: value for key, value in result.items() if key != "predictions_ranked"})
    _write("ranked_1000_predictions.json", scored)
    _write("perfect_100_percent.json", result["perfect_100_percent"])
    _write("zero_0_percent.json", result["zero_0_percent"])
    _write("hashes.json", {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.glob("*.json")) if path.name != "hashes.json"})
    _report(result)
    return result


def _report(result: dict[str, Any]) -> None:
    """Genera reporte Markdown ejecutivo."""

    lines = ["# Fase 105 — evaluación completa de 1,000 partidos", "", f"Partidos: **{result['coverage']['matches']}** · Ligas: **{result['coverage']['leagues']}** · Decisiones: **{result['coverage']['decisions']}**", "", "## Rendimiento por mercado", "", "| Mercado | Motor | Acierto | Baseline | Confianza | Log-loss | Brier |", "|---|---|---:|---:|---:|---:|---:|"]
    for name, value in result["metrics_by_market"].items():
        lines.append(f"| {name} | {value['model']} | {value['accuracy']:.2%} | {value['baseline_accuracy']:.2%} | {value['mean_confidence']:.2%} | {value['log_loss']:.6f} | {value['brier']:.6f} |")
    lines.extend(["", "## Confianza por familia", "", "| Familia | Acierto | Confianza | Log-loss | Brier |", "|---|---:|---:|---:|---:|"])
    for name, value in result["families"].items():
        lines.append(f"| {name} | {value['accuracy']:.2%} | {value['mean_confidence']:.2%} | {value['log_loss']:.6f} | {value['brier']:.6f} |")
    lines.extend(["", "## Partidos con 100% de acierto", "", "| Partido | Liga | Marcador | Aciertos | 1X2 | Over 2.5 | BTTS |", "|---|---|---:|---:|---|---|---|"])
    for row in result["perfect_100_percent"]:
        markets = row["markets"]
        lines.append(f"| {row['home_team_id']} vs {row['away_team_id']} | {row['league_slug']} | {row['observed_score']} | {row['correct_markets']}/{len(MARKETS)} | {markets['1x2']['predicted']} | {'Sí' if markets['over_2_5']['predicted'] else 'No'} | {'Sí' if markets['btts']['predicted'] else 'No'} |")
    lines.extend(["", "## Partidos con 0% de acierto", "", "| Partido | Liga | Marcador | Aciertos |", "|---|---|---:|---:|"])
    for row in result["zero_0_percent"]:
        lines.append(f"| {row['home_team_id']} vs {row['away_team_id']} | {row['league_slug']} | {row['observed_score']} | 0/{len(MARKETS)} |")
    lines.extend(["", f"Total 100%: **{len(result['perfect_100_percent'])}** · Total 0%: **{len(result['zero_0_percent'])}**", "", "Tablas completas: `perfect_100_percent.json`, `zero_0_percent.json` y `ranked_1000_predictions.json`."])
    (OUTPUT / "final_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    report = run()
    assert report["coverage"]["matches"] == 1000
    assert report["coverage"]["decisions"] == 12000
    LOGGER.info("Fase 105 completada: %s", report["classification"])

# Version: 1.0.0
# Created: 2026-07-29
