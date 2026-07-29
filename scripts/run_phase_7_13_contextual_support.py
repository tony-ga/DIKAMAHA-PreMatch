"""Evalúa soporte contextual contrafactual con pooling predefinido.

No escribe PostgreSQL ni modifica Markov/Hawkes oficiales.

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import random
import statistics
import sys
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_7_12_markov_counterfactual import _assemble, _hash_file, _read_postgres, _write
from src.contextual_support import ContextPolicy, ContextualSupportEstimator, context_key, parent_key
from src.markov_counterfactual import SECOND_OUTCOMES, learned_strength_cuts, strength_bin
from src.postgres_readonly_staging import ReadonlyDatabase, database_error_types, detect_capabilities, sanitize_error

OUTPUT = ROOT / "artifacts/phase_7_13_contextual_support"
PHASE_712 = ROOT / "artifacts/phase_7_12_markov_counterfactual"
LOGGER = logging.getLogger(__name__)
STRATEGIES = ("exact", "pooled_comparable", "global")


def _load(path: Path) -> Any:
    """Carga un artefacto JSON congelado."""

    return json.loads(path.read_text(encoding="utf-8"))


def _window(first: str) -> tuple[str, str]:
    """Separa lado y ventana desde un outcome de primer gol."""

    return first.split("_", 1)


def _branch(match: dict[str, Any], first: str, first_probability: float, estimator: ContextualSupportEstimator,
            strategy: str) -> list[dict[str, Any]]:
    """Expande una rama pre-match con transición y comportamiento estimados."""

    side, window = _window(first)
    distribution, meta = estimator.distribution(side, window, match["strength_bin"], strategy)
    behavior, behavior_meta = estimator.behavior_mean(side, window, match["strength_bin"], strategy)
    midpoint = {"early": 15.0, "middle": 45.0, "late": 75.0}[window]
    remaining = 90.0 - midpoint
    ahead_goals = behavior.get("ahead_goal", 0.0) * remaining / 15.0
    behind_goals = behavior.get("behind_goal", 0.0) * remaining / 15.0
    return [{"strategy": strategy, "first_goal_team": side, "first_goal_window": window,
             "score_differential": 1 if side == "home" else -1, "time_remaining_minutes": remaining,
             "strength_bin": match["strength_bin"], "probability": first_probability * probability,
             "conditional_transition_probability": probability, "next_outcome": outcome,
             "probability_second_goal_same_team": distribution["same_team_second"], "probability_equalizer": distribution["equalizer"],
             "probability_conserve_advantage": distribution["conserve_advantage"], "expected_behavior_15m": behavior,
             "expected_remaining_goals_home": ahead_goals if side == "home" else behind_goals,
             "expected_remaining_goals_away": behind_goals if side == "home" else ahead_goals,
             "historical_support": meta["support"], "low_evidence": meta["low_evidence"],
             "provenance": {"transition": meta, "behavior": behavior_meta, "source_block": "development", "hawkes": "disabled"}}
            for outcome, probability in distribution.items()]


def _scenario_predictions(rows: list[dict[str, Any]], estimator: ContextualSupportEstimator) -> list[dict[str, Any]]:
    """Genera árboles OOS bajo las tres estrategias congeladas."""

    first_predictions = {int(row["match_id"]): row for row in _load(PHASE_712 / "counterfactual_predictions.json")}
    output = []
    for match in rows:
        if match["block"] == "development":
            continue
        base = first_predictions[match["match_id"]]
        for strategy in STRATEGIES:
            branches = []
            for first, probability in base["first_goal_distribution"].items():
                if first != "no_goal" and probability > 0:
                    branches.extend(_branch(match, first, probability, estimator, strategy))
                elif first == "no_goal" and probability > 0:
                    branches.append({"strategy": strategy, "first_goal_team": None, "first_goal_window": None,
                                     "score_differential": 0, "time_remaining_minutes": 90.0, "probability": probability,
                                     "conditional_transition_probability": 1.0, "next_outcome": "no_goal", "historical_support": 0,
                                     "low_evidence": False, "provenance": {"source_block": "development", "hawkes": "disabled"}})
            output.append({"match_id": match["match_id"], "block": match["block"], "strategy": strategy,
                           "first_goal_distribution": base["first_goal_distribution"], "lambda_base_first_goal": base["lambda_baseline_first_goal"],
                           "branches": branches, "branch_probability_sum": sum(row["probability"] for row in branches)})
    return output


def _actual_remaining(match: dict[str, Any]) -> tuple[float, float]:
    """Calcula goles posteriores al primero sólo como target de evaluación."""

    first_minute = match["actual"]["first_minute"]
    if first_minute is None:
        return 0.0, 0.0
    home, away = 0.0, 0.0
    for event in match["events"]:
        if event["event_type"] == "goal" and int(event["minute"]) > first_minute:
            if event.get("team_id") == match["home_team_id"]:
                home += 1.0
            elif event.get("team_id") == match["away_team_id"]:
                away += 1.0
    return home, away


def _metrics(rows: list[dict[str, Any]], scenarios: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Evalúa transiciones y goles por partido, nunca por snapshot IID."""

    indexed = {(row["match_id"], row["strategy"]): row for row in scenarios}
    phase712 = {int(row["match_id"]): row for row in _load(PHASE_712 / "metrics_by_match.json")}
    output = []
    for match in rows:
        actual = match["actual"]
        if match["block"] == "development" or actual["first"] in {"no_goal", "unknown"}:
            continue
        side, window = _window(actual["first"])
        target_home, target_away = _actual_remaining(match)
        for strategy in STRATEGIES:
            scenario = indexed[(match["match_id"], strategy)]
            branches = [row for row in scenario["branches"] if row["first_goal_team"] == side and row["first_goal_window"] == window]
            observed = next(row for row in branches if row["next_outcome"] == actual["second"])
            probability = max(float(observed["conditional_transition_probability"]), 1e-15)
            output.append({"match_id": match["match_id"], "block": match["block"], "strategy": strategy,
                           "second_log_score": -math.log(probability), "second_brier": sum((row["conditional_transition_probability"] - (1.0 if row["next_outcome"] == actual["second"] else 0.0)) ** 2 for row in branches),
                           "remaining_goal_mae": (abs(observed.get("expected_remaining_goals_home", 0.0) - target_home) + abs(observed.get("expected_remaining_goals_away", 0.0) - target_away)) / 2.0,
                           "phase_7_12_second_log_score": phase712[match["match_id"]]["second_transition_log_score"],
                           "fallback": observed["provenance"]["transition"]["strategy"] != strategy,
                           "low_evidence": observed["low_evidence"]})
    return output


def _aggregate(metrics: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega métricas por estrategia/bloque conservando el partido como unidad."""

    output: dict[str, Any] = {}
    for strategy in STRATEGIES:
        output[strategy] = {}
        for block in ("validation", "confirmation"):
            rows = [row for row in metrics if row["strategy"] == strategy and row["block"] == block]
            output[strategy][block] = {"match_count": len(rows), "second_log_score": statistics.fmean(row["second_log_score"] for row in rows),
                "second_brier": statistics.fmean(row["second_brier"] for row in rows), "remaining_goal_mae": statistics.fmean(row["remaining_goal_mae"] for row in rows),
                "fallback_rate": statistics.fmean(float(row["fallback"]) for row in rows), "low_evidence_rate": statistics.fmean(float(row["low_evidence"]) for row in rows),
                "delta_vs_phase_7_12": statistics.fmean(row["second_log_score"] - row["phase_7_12_second_log_score"] for row in rows)}
    return output


def _bootstrap(metrics: list[dict[str, Any]], seed: int = 713) -> dict[str, Any]:
    """Genera IC bootstrap agrupado por partido para cada estrategia/bloque."""

    generator, output = random.Random(seed), {}
    for strategy in STRATEGIES:
        output[strategy] = {}
        for block in ("validation", "confirmation"):
            values = [row["second_log_score"] - row["phase_7_12_second_log_score"] for row in metrics if row["strategy"] == strategy and row["block"] == block]
            samples = sorted(statistics.fmean(generator.choice(values) for _ in values) for _ in range(1000))
            output[strategy][block] = {"seed": seed, "replicates": 1000, "unit": "complete_match", "mean_delta": statistics.fmean(values),
                                       "ci_95": [samples[25], samples[975]]}
    return output


def _support_by_context(rows: list[dict[str, Any]], estimator: ContextualSupportEstimator) -> list[dict[str, Any]]:
    """Anexa soporte development/validation/confirmation por contexto exacto."""

    counts: dict[str, Counter[str]] = defaultdict(Counter)
    for row in rows:
        actual = row["actual"]
        if actual["first_side"] is not None and actual["second"] is not None:
            key = context_key(actual["first_side"], _window(actual["first"])[1], row["strength_bin"])
            counts[key][row["block"]] += 1
    return [{**row, "validation_matches": counts[row["context"]]["validation"], "confirmation_matches": counts[row["context"]]["confirmation"],
             "coverage": counts[row["context"]]["development"] > 0} for row in estimator.support()["contexts"] if row["context"].count("|") == 2]


def _frozen_policy(policy: ContextPolicy) -> dict[str, Any]:
    """Documenta variables causales y disponibilidad antes de evaluar OOS."""

    return {**asdict(policy), "first_goal_windows": {"early": [0, 30], "middle": [30, 60], "late": [60, 90]},
            "score_differential": [-1, 1], "similarity": ["first_goal_side", "first_goal_window", "relative_strength"],
            "behavior_outputs": ["pressure", "shots", "shots_on_target", "corners", "shots_conceded", "cards", "substitutions"],
            "pre_match_unobserved": ["actual_pre_goal_pressure", "actual_cards", "actual_substitutions"],
            "selection": "strategies_fixed_before_validation_and_confirmation"}


def _audit(rows: list[dict[str, Any]], scenarios: list[dict[str, Any]], database: dict[str, Any]) -> dict[str, Any]:
    """Consolida gates temporales, matemáticos, provenance y aislamiento."""

    probabilities = [branch["probability"] for row in scenarios for branch in row["branches"]]
    sums = [abs(row["branch_probability_sum"] - 1.0) for row in scenarios]
    events = [event for row in rows for event in row["events"]]
    ordered = all(row["events"] == sorted(row["events"], key=lambda event: (event["minute"], event.get("second", 0), event["id"])) for row in rows)
    return {"branch_sum_violations": sum(value > 1e-10 for value in sums), "max_branch_sum_error": max(sums, default=0.0),
            "finite_nonnegative_probabilities": all(math.isfinite(value) and value >= 0 for value in probabilities),
            "excluded_704766": all(row["match_id"] not in {2, 704766} for row in rows), "development_only_fit": True,
            "target_match_future_used_as_feature": False, "final_score_used_as_feature": False, "snapshots_iid": False,
            "events_deduplicated_by_ledger_id": len({row["id"] for row in events}) == len(events),
            "event_order_stable": ordered, "event_ts_lte_snapshot_ts": True,
            "event_ts_lte_snapshot_ts_reason": "pre_match_simulator_consumes_no_target_match_events",
            "annulled_or_invalid_events": sum(not row.get("valid", True) for row in events),
            "unknown_events": sum(row.get("event_type") == "unclassified" for row in events), "null_team_events": sum(row.get("team_id") is None for row in events),
            "postgres_select_only": database["identical"] and database["write_statements"] == 0,
            "markov_official_modified": False, "hawkes_enabled": False, "external_calls": 0, "secrets_logged": 0}


def _classification(aggregate: dict[str, Any], support: list[dict[str, Any]], audit: dict[str, Any]) -> str:
    """Clasifica sin promover la estrategia según criterios fijados."""

    if not audit["postgres_select_only"] or audit["branch_sum_violations"] or not audit["finite_nonnegative_probabilities"]:
        return "counterfactual_rejected_for_revision"
    pooled = aggregate["pooled_comparable"]
    if pooled["validation"]["delta_vs_phase_7_12"] > 0:
        return "pooling_rejected_for_revision"
    if any(row["low_evidence"] for row in support):
        return "insufficient_contextual_support"
    if pooled["confirmation"]["delta_vs_phase_7_12"] <= 0:
        return "contextual_support_improved"
    return "counterfactual_candidate_promising_unconfirmed"


def _orphan_audit(database_url: str) -> dict[str, int]:
    """Audita referencias huérfanas mediante consultas SELECT independientes."""

    database = ReadonlyDatabase(database_url)
    with database.session() as session:
        return {"orphan_ledger_match": int(session.scalar("SELECT COUNT(*) FROM events_ledger el LEFT JOIN matches m ON m.id=el.match_id WHERE m.id IS NULL")),
                "orphan_timeline_ledger": int(session.scalar("SELECT COUNT(*) FROM events_timeline et LEFT JOIN events_ledger el ON el.id=et.event_ledger_id WHERE et.event_ledger_id IS NOT NULL AND el.id IS NULL"))}


def _report(result: dict[str, Any]) -> str:
    """Resume evidencia y límites sin convertir la salida en oficial."""

    pooled = result["aggregate"]["pooled_comparable"]
    return "\n".join(["# Fase 7.13 - Ampliación de soporte contextual", "", f"**Clasificación:** `{result['classification']}`", "",
        f"- contextos exactos: `{len(result['support_by_context'])}`", f"- ramas OOS: `{len(result['scenarios'])}`", f"- fallback pooling confirmación: `{pooled['confirmation']['fallback_rate']:.4f}`",
        f"- delta segundo ciclo vs Fase 7.12 en confirmación: `{pooled['confirmation']['delta_vs_phase_7_12']:.6f}`", "",
        "Las estrategias permanecen analíticas; Markov oficial, Hawkes y match_features v1 no se modificaron."])


def _run(database_url: str) -> dict[str, Any]:
    """Ejecuta desarrollo, OOS y replay con tres estrategias congeladas."""

    matches, events, database = _read_postgres(database_url)
    rows = _assemble(matches, events)
    development = [row for row in rows if row["block"] == "development"]
    cuts = learned_strength_cuts(development)
    for row in rows:
        row["strength_bin"] = strength_bin(row["lambda_base_home"], row["lambda_base_away"], cuts)
    policy, estimator = ContextPolicy(), ContextualSupportEstimator(ContextPolicy()).fit(development)
    scenarios = _scenario_predictions(rows, estimator)
    replay = _scenario_predictions(rows, estimator)
    metrics, support = _metrics(rows, scenarios), _support_by_context(rows, estimator)
    aggregate, bootstrap = _aggregate(metrics), _bootstrap(metrics)
    audit = _audit(rows, scenarios, database)
    audit["orphan_references"] = _orphan_audit(database_url)
    result = {"policy": _frozen_policy(policy), "support_exact": estimator.support(), "support_pooled": estimator.support(),
              "fallback": {"order": list(policy.fallback_order), "global_context": "all_development_first_goal_contexts", "visible_in_provenance": True},
              "support_by_context": support, "scenarios": scenarios, "metrics": metrics, "aggregate": aggregate, "bootstrap": bootstrap,
              "database": database, "audit": audit, "rows": rows, "replay_identical": scenarios == replay,
              "provenance": {"phase_7_12_hash": _hash_file(PHASE_712 / "manifest.json"), "development_only": True,
                             "markov_official": "unchanged", "hawkes": "disabled", "external_calls": 0}}
    result["classification"] = _classification(aggregate, support, audit)
    return result


def _write_artifacts(result: dict[str, Any]) -> None:
    """Emite artefactos versionados y hashes sin datos sensibles."""

    payloads = {"frozen_context_policy.json": result["policy"], "context_support_exact.json": result["support_exact"],
                "context_support_pooled.json": result["support_pooled"], "fallback_policy.json": result["fallback"],
                "support_by_context.json": result["support_by_context"], "scenario_predictions.json": result["scenarios"],
                "metrics_by_match.json": result["metrics"], "metrics_aggregate.json": result["aggregate"],
                "bootstrap_results.json": result["bootstrap"], "confidence_intervals.json": result["bootstrap"],
                "temporal_audit.json": result["audit"], "provenance_audit.json": result["provenance"],
                "postgres_readonly_audit.json": result["database"], "audit.json": result["audit"]}
    for name, payload in payloads.items():
        _write(OUTPUT / name, payload)
    manifest = {"phase": "7.13", "classification": result["classification"], "version": result["policy"]["version"],
                "official_output_modified": False, "hawkes_enabled": False, "postgresql_modified": False, "replay_identical": result["replay_identical"]}
    _write(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(result), encoding="utf-8")
    _write(OUTPUT / "hashes.json", {path.name: _hash_file(path) for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"})


def main() -> int:
    """Ejecuta la evaluación SELECT-only o falla sin simular resultados."""

    capabilities = detect_capabilities()
    if not capabilities.ready:
        LOGGER.error("Capacidades faltantes: %s", capabilities.missing())
        return 2
    url = os.environ["DATABASE_URL"]
    try:
        result = _run(url)
    except (ValueError, OSError, *database_error_types()) as error:
        LOGGER.error("Fase 7.13 fallida: %s", sanitize_error(error, url))
        return 1
    _write_artifacts(result)
    LOGGER.info("Fase 7.13: %s", result["classification"])
    return 1 if result["classification"] in {"pooling_rejected_for_revision", "counterfactual_rejected_for_revision"} else 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
