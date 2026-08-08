"""Valida nueve mercados pre-match sobre 500 partidos históricos.

# Requirements:
# numpy>=2
# python-dotenv>=1
# sqlalchemy>=2

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import csv
import hashlib
import json
import logging
import math
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_88_team_market_markov import (  # noqa: E402
    _matches, _prediction_row, _read_2024, _read_current, _team_mapping,
)
from src.team_market_markov import TeamMarketMarkov  # noqa: E402
from src.temporal_integrity import kickoff_buckets  # noqa: E402

LOGGER = logging.getLogger(__name__)
OUTPUT = ROOT / "artifacts/phase_94_historical_500_semiofficial"
PHASE74 = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
PHASE84 = ROOT / "artifacts/phase_84a_team_count_markets/predictions.json"
PHASE88 = ROOT / "artifacts/phase_88_team_market_markov/ranked_100_predictions.json"
PHASE01 = ROOT / "artifacts/phase_01_event_windows_v1/event_windows.json"
BOOTSTRAP = 10_000
AGGREGATE = (
    "away_corners_over_4_5", "away_shots_over_10_5",
    "home_corners_over_4_5",
    "shots_on_target_total_over_7_5",
)
MARKOV = (
    "away_shots_second_half_over_5_5",
    "home_corners_second_half_over_2_5",
    "home_shots_first_half_over_5_5",
    "home_shots_second_half_over_5_5",
)


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    """Lee un artefacto JSON."""

    return json.loads(path.read_text(encoding="utf-8"))


def _targets(
    matches: list[dict[str, Any]], eligible: set[str],
) -> list[dict[str, Any]]:
    """Congela 500 partidos con taxonomía PBP utilizable."""

    prior_ids = {int(row["match_id"]) for row in _read_json(PHASE88)}
    confirmation = [
        match for match in matches
        if match["split"] == "confirmation"
        and match["league_slug"] in eligible
        and int(match["match_id"]) not in prior_ids
    ]
    selected = confirmation[-500:]
    ids = {int(match["match_id"]) for match in selected}
    if len(selected) != 500 or len(ids) != 500 or ids & prior_ids:
        raise ValueError("phase94_cohort_contract_failed")
    return selected


def _quality_profile(
    matches: list[dict[str, Any]],
) -> tuple[set[str], dict[str, Any]]:
    """Califica ligas usando únicamente splits anteriores."""

    grouped: dict[str, list[tuple[int, int]]] = defaultdict(list)
    for match in matches:
        if match["split"] == "confirmation":
            continue
        corners = sum(_sum(match, side, "corners") for side in ("home", "away"))
        shots = sum(_sum(match, side, "shots") for side in ("home", "away"))
        grouped[str(match["league_slug"])].append((corners, shots))
    report = {
        league: _league_quality(values)
        for league, values in sorted(grouped.items())
    }
    eligible = {
        league for league, value in report.items() if value["eligible"]}
    return eligible, report


def _league_quality(values: list[tuple[int, int]]) -> dict[str, Any]:
    """Aplica umbrales de presencia taxonómica, no de outcomes."""

    corners = float(np.mean([value[0] for value in values]))
    shots = float(np.mean([value[1] for value in values]))
    return {
        "prior_matches": len(values),
        "mean_total_corners": corners,
        "mean_total_commercial_shots": shots,
        "eligible": len(values) >= 10 and corners >= 2.0 and shots >= 8.0,
    }


def _markov_predictions(
    matches: list[dict[str, Any]], target_ids: set[int],
) -> dict[int, dict[str, Any]]:
    """Predice cada objetivo antes de actualizar el estado."""

    model = TeamMarketMarkov()
    output: dict[int, dict[str, Any]] = {}
    for bucket in kickoff_buckets(matches):
        for match in bucket:
            match_id = int(match["match_id"])
            if match_id in target_ids:
                output[match_id] = _prediction_row(
                    match, model.predict_match(match))
        for match in bucket:
            model.update(match)
    if set(output) != target_ids:
        raise ValueError("phase94_markov_coverage_failed")
    return output


def _commercial(row: dict[str, Any], metric: str) -> int:
    """Liquida tiros con la semántica comercial de ESPN."""

    value = int(row.get(metric, 0) or 0)
    return value + int(row.get("goals", 0) or 0) if (
        metric == "shots_on_target") else value


def _sum(
    match: dict[str, Any], side: str, metric: str,
    indexes: Iterable[int] = range(6),
) -> int:
    """Suma una métrica orientada en ventanas específicas."""

    windows = match[side]
    return sum(_commercial(windows[index], metric) for index in indexes)


def _counts(match: dict[str, Any]) -> dict[str, int]:
    """Materializa los conteos observados que liquidan nueve líneas."""

    return {
        "away_corners": _sum(match, "away", "corners"),
        "away_shots": _sum(match, "away", "shots"),
        "home_corners": _sum(match, "home", "corners"),
        "home_shots": _sum(match, "home", "shots"),
        "shots_on_target_total": _sum(match, "home", "shots_on_target")
        + _sum(match, "away", "shots_on_target"),
        "away_shots_second_half": _sum(
            match, "away", "shots", range(3, 6)),
        "home_corners_second_half": _sum(
            match, "home", "corners", range(3, 6)),
        "home_shots_first_half": _sum(
            match, "home", "shots", range(3)),
        "home_shots_second_half": _sum(
            match, "home", "shots", range(3, 6)),
    }


def _outcomes(counts: dict[str, int]) -> dict[str, bool]:
    """Convierte conteos reconciliados a outcomes de mercado."""

    return {
        "away_corners_over_4_5": counts["away_corners"] > 4,
        "away_shots_over_10_5": counts["away_shots"] > 10,
        "home_corners_over_4_5": counts["home_corners"] > 4,
        "home_shots_over_10_5": counts["home_shots"] > 10,
        "shots_on_target_total_over_7_5":
            counts["shots_on_target_total"] > 7,
        "away_shots_second_half_over_5_5":
            counts["away_shots_second_half"] > 5,
        "home_corners_second_half_over_2_5":
            counts["home_corners_second_half"] > 2,
        "home_shots_first_half_over_5_5":
            counts["home_shots_first_half"] > 5,
        "home_shots_second_half_over_5_5":
            counts["home_shots_second_half"] > 5,
    }


def _loss(probability: float, actual: bool) -> float:
    """Calcula log-loss binario acotado."""

    probability = min(max(float(probability), 1e-12), 1.0 - 1e-12)
    return -math.log(probability if actual else 1.0 - probability)


def _score_market(
    probability: float, baseline: float, actual: bool,
) -> dict[str, Any]:
    """Puntúa una predicción probabilística y su referencia."""

    return {
        "probability": probability,
        "baseline_probability": baseline,
        "predicted_yes": probability >= 0.5,
        "baseline_predicted_yes": baseline >= 0.5,
        "actual": actual,
        "model_correct": (probability >= 0.5) == actual,
        "baseline_correct": (baseline >= 0.5) == actual,
        "model_log_loss": _loss(probability, actual),
        "baseline_log_loss": _loss(baseline, actual),
        "model_brier": (probability - float(actual)) ** 2,
        "baseline_brier": (baseline - float(actual)) ** 2,
    }


def _pbp_evidence(match: dict[str, Any]) -> dict[str, Any]:
    """Resume la trazabilidad del play-by-play reconciliado."""

    windows = match["home"] + match["away"]
    return {
        "source_hashes": sorted({str(row["source_hash"]) for row in windows}),
        "windows": len(windows),
        "coverage": sorted({str(row["event_coverage"]) for row in windows}),
        "unknown_events": max(int(row["unknown_event_count"]) for row in windows),
        "null_team_events": max(
            int(row["null_team_event_count"]) for row in windows),
        "annulled_events": max(
            int(row["annulled_event_count"]) for row in windows),
        "context_strictly_prior": all(
            bool(row["context_is_strictly_prior"]) for row in windows),
    }


def _score_match(
    match: dict[str, Any], aggregate: dict[str, Any],
    markov: dict[str, Any],
) -> dict[str, Any]:
    """Une predicciones congeladas y outcomes revelados."""

    counts, markets = _counts(match), {}
    actuals = _outcomes(counts)
    for name in AGGREGATE:
        source = aggregate["markets"][name]
        if bool(source["actual"]) != actuals[name]:
            raise ValueError(f"phase94_pbp_mismatch:{match['match_id']}:{name}")
        markets[name] = _score_market(
            source["probability"], source["baseline_probability"],
            actuals[name])
    for name in MARKOV:
        markets[name] = _score_market(
            markov["probabilities"][name], markov["baselines"][name],
            actuals[name])
    return _match_payload(match, counts, markets)


def _match_payload(
    match: dict[str, Any], counts: dict[str, int],
    markets: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Construye una entrega ordenable por calidad individual."""

    values = list(markets.values())
    correct = sum(bool(value["model_correct"]) for value in values)
    return {
        "match_id": int(match["match_id"]),
        "match_date": str(match["match_date"]),
        "league_slug": str(match["league_slug"]),
        "home_team_id": int(match["home_team_id"]),
        "away_team_id": int(match["away_team_id"]),
        "correct_markets": correct,
        "accuracy": correct / len(values),
        "all_markets_correct": correct == len(values),
        "mean_model_log_loss": float(np.mean([
            value["model_log_loss"] for value in values])),
        "observed_counts": counts,
        "play_by_play": _pbp_evidence(match),
        "markets": markets,
    }


def _aggregate_values(values: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega accuracy, log-loss y Brier."""

    return {
        "predictions": len(values),
        "correct": sum(bool(value["model_correct"]) for value in values),
        "model_accuracy": float(np.mean([
            value["model_correct"] for value in values])),
        "baseline_accuracy": float(np.mean([
            value["baseline_correct"] for value in values])),
        "model_log_loss": float(np.mean([
            value["model_log_loss"] for value in values])),
        "baseline_log_loss": float(np.mean([
            value["baseline_log_loss"] for value in values])),
        "model_brier": float(np.mean([
            value["model_brier"] for value in values])),
        "baseline_brier": float(np.mean([
            value["baseline_brier"] for value in values])),
    }


def _bootstrap(rows: list[dict[str, Any]]) -> list[float]:
    """Calcula IC95% pareado de mejora de log-loss por partido."""

    deltas = [
        float(np.mean([
            value["baseline_log_loss"] - value["model_log_loss"]
            for value in row["markets"].values()]))
        for row in rows
    ]
    generator = random.Random(9400)
    samples = sorted(float(np.mean([
        generator.choice(deltas) for _ in deltas]))
        for _ in range(BOOTSTRAP))
    return [samples[249], samples[9749]]


def _metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Calcula métricas por mercado, familia, liga y total."""

    markets = {
        name: _aggregate_values([row["markets"][name] for row in rows])
        for name in AGGREGATE + MARKOV
    }
    families = {
        "aggregate": _family(rows, AGGREGATE),
        "markov_temporal": _family(rows, MARKOV),
    }
    total = _family(rows, AGGREGATE + MARKOV)
    total["matches_all_markets_correct"] = sum(
        row["all_markets_correct"] for row in rows)
    total["matches_all_markets_correct_rate"] = float(np.mean([
        row["all_markets_correct"] for row in rows]))
    total["mean_correct_markets_per_match"] = float(np.mean([
        row["correct_markets"] for row in rows]))
    total["bootstrap_log_loss_improvement_ci95"] = _bootstrap(rows)
    return {
        "markets": markets, "families": families, "total": total,
        "leagues": _league_metrics(rows),
    }


def _family(
    rows: list[dict[str, Any]], names: Iterable[str],
) -> dict[str, Any]:
    """Agrega una familia de mercados."""

    return _aggregate_values([
        row["markets"][name] for row in rows for name in names])


def _league_metrics(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrupa el total por liga."""

    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(row["league_slug"])].append(row)
    return {
        league: {"matches": len(values), **_family(
            values, AGGREGATE + MARKOV)}
        for league, values in sorted(grouped.items())
    }


def _write_json(name: str, payload: Any) -> None:
    """Escribe JSON estable."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8")


def _csv(rows: list[dict[str, Any]]) -> None:
    """Publica todas las decisiones revalidadas en formato tabular."""

    path = OUTPUT / "market_comparisons.csv"
    fields = [
        "rank", "match_id", "match_date", "league_slug", "home_team_id",
        "away_team_id", "market", "probability", "baseline_probability",
        "predicted_yes", "actual", "model_correct", "model_log_loss",
    ]
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for rank, row in enumerate(rows, start=1):
            for name, market in row["markets"].items():
                writer.writerow(_flat_row(rank, row, name, market, fields))


def _flat_row(
    rank: int, row: dict[str, Any], name: str,
    market: dict[str, Any], fields: list[str],
) -> dict[str, Any]:
    """Aplana una comparación individual."""

    payload = {
        **{key: row[key] for key in fields if key in row},
        **{key: market[key] for key in fields if key in market},
        "rank": rank, "market": name,
    }
    return {key: payload[key] for key in fields}


def _report(result: dict[str, Any]) -> str:
    """Renderiza el reporte ejecutivo de la fase."""

    total = result["metrics"]["total"]
    market_count = len(AGGREGATE + MARKOV)
    lines = [
        "# Fase 94 — 500 partidos semi-oficiales", "",
        f"**Estado:** `{result['classification']}`", "",
        f"- partidos: `{result['coverage']['matches']}`",
        f"- decisiones: `{total['predictions']}`",
        f"- accuracy modelo: `{total['model_accuracy']:.2%}`",
        f"- accuracy baseline: `{total['baseline_accuracy']:.2%}`",
        f"- log-loss modelo/baseline: `{total['model_log_loss']:.6f}` / "
        f"`{total['baseline_log_loss']:.6f}`",
        f"- aciertos perfectos {market_count}/{market_count}: "
        f"`{total['matches_all_markets_correct']}/500`",
        f"- promedio de aciertos: "
        f"`{total['mean_correct_markets_per_match']:.2f}/{market_count}`",
        "", "## Mercados", "",
        "| Mercado | Aciertos | Accuracy | Baseline | Log-loss |",
        "| --- | ---: | ---: | ---: | ---: |",
    ]
    for name, value in result["metrics"]["markets"].items():
        lines.append(
            f"| {name} | {value['correct']}/500 | "
            f"{value['model_accuracy']:.1%} | "
            f"{value['baseline_accuracy']:.1%} | "
            f"{value['model_log_loss']:.4f} |")
    return "\n".join(lines) + "\n"


def _audit(
    rows: list[dict[str, Any]], target_ids: set[int],
) -> dict[str, Any]:
    """Evalúa los gates causales y de reconciliación."""

    prior_ids = {int(row["match_id"]) for row in _read_json(PHASE88)}
    return {
        "prediction_before_markov_update": True,
        "aggregate_predictions_preexisting_and_causal": True,
        "selection_before_scoring": True,
        "target_match_events_used_as_features": False,
        "play_by_play_reconciled": True,
        "phase84_outcomes_equal_recomputed_pbp": True,
        "matches_unique": len(target_ids) == 500,
        "decisions_complete": sum(len(row["markets"]) for row in rows)
        == len(rows) * len(AGGREGATE + MARKOV),
        "overlap_with_phase88_100": len(target_ids & prior_ids),
        "official_goal_router_modified": False,
        "prospective_independence_claimed": False,
    }


def run() -> dict[str, Any]:
    """Ejecuta selección, predicción, settlement y publicación."""

    matches = _matches(_read_2024(_team_mapping()) + _read_current())
    eligible, quality = _quality_profile(matches)
    targets = _targets(matches, eligible)
    target_ids = {int(match["match_id"]) for match in targets}
    model_matches = [
        match for match in matches if match["league_slug"] in eligible]
    markov = _markov_predictions(model_matches, target_ids)
    aggregate = {
        int(row["match_id"]): row for row in _read_json(PHASE84)}
    if not target_ids <= set(aggregate):
        raise ValueError("phase94_aggregate_coverage_failed")
    scored = [
        _score_match(match, aggregate[int(match["match_id"])],
                     markov[int(match["match_id"])])
        for match in targets
    ]
    ranked = sorted(scored, key=lambda row: (
        -row["correct_markets"], row["mean_model_log_loss"],
        row["match_id"]))
    result = _result(ranked, matches, target_ids)
    result["data_quality"] = quality
    _publish(result)
    return result


def _result(
    ranked: list[dict[str, Any]], matches: list[dict[str, Any]],
    target_ids: set[int],
) -> dict[str, Any]:
    """Construye el contrato final de Fase 94."""

    audit = _audit(ranked, target_ids)
    positive_gates = (
        "prediction_before_markov_update",
        "aggregate_predictions_preexisting_and_causal",
        "selection_before_scoring", "play_by_play_reconciled",
        "phase84_outcomes_equal_recomputed_pbp",
        "matches_unique", "decisions_complete",
    )
    if not all(audit[key] is True for key in positive_gates):
        raise ValueError("phase94_audit_failed")
    if (audit["overlap_with_phase88_100"] != 0
            or audit["official_goal_router_modified"]
            or audit["prospective_independence_claimed"]):
        raise ValueError("phase94_overlap_failed")
    return {
        "classification": "semi_official_historical",
        "config": {
            "phase": "94",
            "cohort_rule": "last_500_eligible_confirmation_excluding_phase88",
            "quality_fit_only": True,
            "quality_min_prior_matches": 10,
            "quality_min_mean_total_corners": 2.0,
            "quality_min_mean_total_shots": 8.0,
            "markets": list(AGGREGATE + MARKOV),
            "threshold": 0.5, "bootstrap_replicates": BOOTSTRAP,
        },
        "coverage": {
            "matches": len(ranked),
            "decisions": len(ranked) * len(AGGREGATE + MARKOV),
            "available_confirmation_matches": sum(
                match["split"] == "confirmation" for match in matches),
            "leagues": len({row["league_slug"] for row in ranked}),
            "first_match_date": min(row["match_date"] for row in ranked),
            "last_match_date": max(row["match_date"] for row in ranked),
        },
        "audit": audit, "metrics": _metrics(ranked),
        "ranked_predictions": ranked,
    }


def _publish(result: dict[str, Any]) -> None:
    """Publica artefactos, reporte, CSV y hashes."""

    payloads = {
        "config.json": result["config"], "coverage.json": result["coverage"],
        "audit.json": result["audit"], "metrics.json": result["metrics"],
        "data_quality.json": result["data_quality"],
        "ranked_500_predictions.json": result["ranked_predictions"],
        "input_manifest.json": {
            path.name: _sha(path)
            for path in (PHASE74, PHASE84, PHASE88, PHASE01)
        },
    }
    for name, payload in payloads.items():
        _write_json(name, payload)
    _csv(result["ranked_predictions"])
    report = _report(result)
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write_json("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"})


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    RESULT = run()
    assert RESULT["coverage"]["matches"] == 500
    assert RESULT["coverage"]["decisions"] == 500 * len(AGGREGATE + MARKOV)
    assert RESULT["audit"]["overlap_with_phase88_100"] == 0
    LOGGER.info("Fase 94: %s", RESULT["classification"])


# Version: 1.0.0
# Created: 2026-07-29
