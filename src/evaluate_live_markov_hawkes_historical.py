"""Evaluación histórica causal de Markov Live y Hawkes residual.

Reconstruye snapshots pseudo-live desde PostgreSQL en modo read-only. Los
priors se estiman walk-forward y los parámetros se seleccionan en bloques
temporales separados antes de puntuar confirmación.

Version: 1.0.0
Created: 2026-08-07
"""

from __future__ import annotations

import hashlib
import json
import math
from collections import defaultdict
from dataclasses import asdict, dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

import numpy as np
from sqlalchemy import create_engine, text

try:
    from src.hawkes_live_v2 import HawkesLiveConfig, HawkesLiveV2
    from src.markov_live_v1 import MarkovLiveConfig, MarkovLiveInput, MarkovLiveV1
except ModuleNotFoundError:  # pragma: no cover
    from hawkes_live_v2 import HawkesLiveConfig, HawkesLiveV2
    from markov_live_v1 import MarkovLiveConfig, MarkovLiveInput, MarkovLiveV1


MODEL_EVENT_TYPES = (
    "goal", "shot_on_target", "shot_off_target", "shot_blocked", "corner",
    "foul", "yellow", "red", "substitution", "penalty_awarded",
    "penalty_scored",
)
NEXT_EVENT_TYPES = frozenset({
    "goal", "shot_on_target", "corner", "yellow", "red", "substitution",
})


@dataclass(frozen=True, slots=True)
class HistoricalLiveConfig:
    """Contrato reproducible del gate histórico Fase 114."""

    version: str = "phase_114_historical_live_validation_v1"
    snapshot_minutes: tuple[int, ...] = (15, 30, 45, 60, 75)
    minimum_league_history: int = 30
    minimum_team_history: int = 5
    league_prior_matches: float = 40.0
    team_prior_matches: float = 8.0
    global_prior_matches: float = 100.0
    development_fraction: float = 0.60
    validation_fraction: float = 0.20
    markov_state_scales: tuple[float, ...] = (0.0, 0.5, 1.0, 1.5)
    hawkes_goal_rhos: tuple[float, ...] = (0.0, 0.10, 0.20, 0.35, 0.50, 0.75, 1.0)
    hawkes_next_event_rhos: tuple[float, ...] = (0.0, 0.10, 0.20, 0.35, 0.50)
    objective_goal_weight: float = 0.75
    objective_next_event_weight: float = 0.25
    bootstrap_replicates: int = 2000
    bootstrap_seed: int = 11407
    minimum_historical_matches: int = 5000
    minimum_historical_leagues: int = 20
    minimum_non_degraded_league_fraction: float = 0.70
    minimum_hawkes_admission_matches: int = 30
    minimum_admitted_hawkes_leagues: int = 5


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), default=str,
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc(value: datetime | str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _match_query() -> str:
    return """
        WITH goal_counts AS (
            SELECT
                m.id,
                COUNT(*) FILTER (
                    WHERE e.event_type IN ('goal', 'penalty_scored')
                      AND NOT e.annulled
                      AND e.team_provider_id = m.home_provider_team_id
                ) AS home_goals,
                COUNT(*) FILTER (
                    WHERE e.event_type IN ('goal', 'penalty_scored')
                      AND NOT e.annulled
                      AND e.team_provider_id = m.away_provider_team_id
                ) AS away_goals,
                BOOL_OR(
                    COALESCE(NULLIF(e.raw_data -> 'period' ->> 'number', '')::int, 1) > 2
                ) AS has_extra_period
            FROM prospective_staging_v2.matches m
            LEFT JOIN prospective_staging_v2.events e
              ON e.provider = m.provider
             AND e.provider_match_id = m.provider_match_id
            WHERE m.complete
            GROUP BY m.id
        )
        SELECT
            m.id AS row_id,
            m.provider_match_id,
            m.kickoff_ts,
            m.home_provider_team_id AS home_team_id,
            m.away_provider_team_id AS away_team_id,
            m.home_score,
            m.away_score,
            m.league_slug
        FROM prospective_staging_v2.matches m
        JOIN goal_counts g ON g.id = m.id
        WHERE m.complete
          AND m.home_score IS NOT NULL
          AND m.away_score IS NOT NULL
          AND m.home_provider_team_id IS NOT NULL
          AND m.away_provider_team_id IS NOT NULL
          AND m.provider_match_id ~ '^[0-9]+$'
          AND m.home_score = g.home_goals
          AND m.away_score = g.away_goals
          AND NOT COALESCE(g.has_extra_period, FALSE)
        ORDER BY m.kickoff_ts, m.provider_match_id
    """


def _event_query() -> str:
    allowed = ",".join(f"'{value}'" for value in MODEL_EVENT_TYPES)
    return f"""
        SELECT
            e.provider_match_id,
            COALESCE(NULLIF(e.provider_event_id, ''), e.event_hash) AS event_id,
            e.event_type,
            e.team_provider_id AS team_id,
            e.annulled,
            COALESCE(
                NULLIF(e.raw_data -> 'period' ->> 'number', '')::int,
                CASE WHEN e.minute >= 45 THEN 2 ELSE 1 END
            ) AS period,
            COALESCE(
                NULLIF(e.raw_data -> 'clock' ->> 'value', '')::double precision,
                e.minute * 60.0 + e.second
            ) AS match_clock_seconds
        FROM prospective_staging_v2.events e
        WHERE e.event_type IN ({allowed})
          AND NOT e.annulled
          AND e.provider_match_id ~ '^[0-9]+$'
        ORDER BY e.provider_match_id, match_clock_seconds, event_id
    """


def read_historical_database(
    database_url: str,
) -> tuple[list[dict[str, Any]], dict[int, list[dict[str, Any]]], dict[str, Any]]:
    """Lee el staging sin escrituras y devuelve sólo campos modelables."""

    engine = create_engine(database_url, future=True, pool_pre_ping=True)
    with engine.connect() as connection:
        connection.execute(text("SET TRANSACTION READ ONLY"))
        before = {
            "matches": int(connection.execute(text(
                "SELECT COUNT(*) FROM prospective_staging_v2.matches"
            )).scalar_one()),
            "events": int(connection.execute(text(
                "SELECT COUNT(*) FROM prospective_staging_v2.events"
            )).scalar_one()),
        }
        matches = [dict(row) for row in connection.execute(text(_match_query())).mappings()]
        eligible_ids = {int(row["provider_match_id"]) for row in matches}
        events: dict[int, list[dict[str, Any]]] = defaultdict(list)
        hasher = hashlib.sha256()
        for raw in connection.execute(text(_event_query())).mappings():
            match_id = int(raw["provider_match_id"])
            if match_id not in eligible_ids:
                continue
            row = {
                "event_id": str(raw["event_id"]),
                "event_type": str(raw["event_type"]),
                "team_id": int(raw["team_id"]) if raw["team_id"] is not None else None,
                "period": int(raw["period"]),
                "match_clock_seconds": float(raw["match_clock_seconds"]),
            }
            events[match_id].append(row)
            hasher.update(json.dumps(
                {"match_id": match_id, **row},
                sort_keys=True, separators=(",", ":"),
            ).encode("utf-8"))
        after = {
            "matches": int(connection.execute(text(
                "SELECT COUNT(*) FROM prospective_staging_v2.matches"
            )).scalar_one()),
            "events": int(connection.execute(text(
                "SELECT COUNT(*) FROM prospective_staging_v2.events"
            )).scalar_one()),
        }
    engine.dispose()
    match_hash = _stable_hash([{
        key: row[key] for key in (
            "provider_match_id", "kickoff_ts", "home_team_id", "away_team_id",
            "home_score", "away_score", "league_slug",
        )
    } for row in matches])
    return matches, dict(events), {
        "read_only": True,
        "counts_before": before,
        "counts_after": after,
        "counts_identical": before == after,
        "reconciled_regulation_matches": len(matches),
        "represented_leagues": len({str(row["league_slug"]) for row in matches}),
        "model_events": sum(len(rows) for rows in events.values()),
        "match_source_hash": match_hash,
        "event_source_hash": hasher.hexdigest(),
        "postgresql_writes": 0,
    }


def walkforward_priors(
    matches: Sequence[dict[str, Any]], config: HistoricalLiveConfig,
) -> tuple[dict[int, dict[str, Any]], dict[str, Any]]:
    """Estima lambdas usando sólo kickoffs estrictamente anteriores."""

    ordered = sorted(matches, key=lambda row: (_utc(row["kickoff_ts"]), int(row["provider_match_id"])))
    league_stats: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0.0, "home": 0.0, "away": 0.0})
    team_stats: dict[int, dict[str, float]] = defaultdict(lambda: {"n": 0.0, "gf": 0.0, "ga": 0.0})
    global_stats = {"n": 0.0, "home": 0.0, "away": 0.0}
    priors: dict[int, dict[str, Any]] = {}
    history_max: datetime | None = None
    cursor = 0
    while cursor < len(ordered):
        kickoff = _utc(ordered[cursor]["kickoff_ts"])
        end = cursor
        while end < len(ordered) and _utc(ordered[end]["kickoff_ts"]) == kickoff:
            end += 1
        group = ordered[cursor:end]
        for row in group:
            league = str(row["league_slug"])
            home_id, away_id = int(row["home_team_id"]), int(row["away_team_id"])
            league_row = league_stats[league]
            home_row, away_row = team_stats[home_id], team_stats[away_id]
            if (
                league_row["n"] >= config.minimum_league_history
                and home_row["n"] >= config.minimum_team_history
                and away_row["n"] >= config.minimum_team_history
            ):
                global_home = (
                    global_stats["home"] + 1.45 * config.global_prior_matches
                ) / (global_stats["n"] + config.global_prior_matches)
                global_away = (
                    global_stats["away"] + 1.15 * config.global_prior_matches
                ) / (global_stats["n"] + config.global_prior_matches)
                league_home = (
                    league_row["home"] + global_home * config.league_prior_matches
                ) / (league_row["n"] + config.league_prior_matches)
                league_away = (
                    league_row["away"] + global_away * config.league_prior_matches
                ) / (league_row["n"] + config.league_prior_matches)
                team_base = max(0.2, (league_home + league_away) / 2.0)
                home_attack = (
                    home_row["gf"] + team_base * config.team_prior_matches
                ) / (home_row["n"] + config.team_prior_matches) / team_base
                home_defence = (
                    home_row["ga"] + team_base * config.team_prior_matches
                ) / (home_row["n"] + config.team_prior_matches) / team_base
                away_attack = (
                    away_row["gf"] + team_base * config.team_prior_matches
                ) / (away_row["n"] + config.team_prior_matches) / team_base
                away_defence = (
                    away_row["ga"] + team_base * config.team_prior_matches
                ) / (away_row["n"] + config.team_prior_matches) / team_base
                lambda_home = min(4.5, max(0.15, league_home * math.sqrt(home_attack * away_defence)))
                lambda_away = min(4.5, max(0.15, league_away * math.sqrt(away_attack * home_defence)))
                match_id = int(row["provider_match_id"])
                priors[match_id] = {
                    "lambda_base_home": lambda_home,
                    "lambda_base_away": lambda_away,
                    "cutoff_ts": history_max.isoformat() if history_max else None,
                    "kickoff_ts": kickoff.isoformat(),
                    "history_matches": int(global_stats["n"]),
                    "league_history_matches": int(league_row["n"]),
                    "home_history_matches": int(home_row["n"]),
                    "away_history_matches": int(away_row["n"]),
                }
                priors[match_id]["source_hash"] = _stable_hash(priors[match_id])
        for row in group:
            league = str(row["league_slug"])
            home_id, away_id = int(row["home_team_id"]), int(row["away_team_id"])
            home_goals, away_goals = int(row["home_score"]), int(row["away_score"])
            league_stats[league]["n"] += 1.0
            league_stats[league]["home"] += home_goals
            league_stats[league]["away"] += away_goals
            for team_id, goals_for, goals_against in (
                (home_id, home_goals, away_goals),
                (away_id, away_goals, home_goals),
            ):
                team_stats[team_id]["n"] += 1.0
                team_stats[team_id]["gf"] += goals_for
                team_stats[team_id]["ga"] += goals_against
            global_stats["n"] += 1.0
            global_stats["home"] += home_goals
            global_stats["away"] += away_goals
        history_max = kickoff
        cursor = end
    causal = all(
        prior["cutoff_ts"] is not None
        and _utc(prior["cutoff_ts"]) < _utc(prior["kickoff_ts"])
        for prior in priors.values()
    )
    return priors, {
        "prior_count": len(priors),
        "strictly_prior": causal,
        "atomic_same_kickoff_updates": True,
        "cold_start_matches_excluded": len(matches) - len(priors),
    }


def temporal_partition(
    matches: Sequence[dict[str, Any]], config: HistoricalLiveConfig,
) -> tuple[dict[int, str], dict[str, Any]]:
    """Divide por grupos de kickoff completos, nunca por snapshots."""

    kickoffs = sorted({_utc(row["kickoff_ts"]) for row in matches})
    if len(kickoffs) < 3:
        raise ValueError("insufficient_atomic_kickoffs")
    development_end = max(1, int(len(kickoffs) * config.development_fraction))
    validation_end = max(
        development_end + 1,
        int(len(kickoffs) * (config.development_fraction + config.validation_fraction)),
    )
    validation_end = min(validation_end, len(kickoffs) - 1)
    development_cutoff = kickoffs[development_end - 1]
    validation_cutoff = kickoffs[validation_end - 1]
    blocks: dict[int, str] = {}
    counts = defaultdict(int)
    for row in matches:
        kickoff = _utc(row["kickoff_ts"])
        block = "development" if kickoff <= development_cutoff else (
            "validation" if kickoff <= validation_cutoff else "confirmation"
        )
        blocks[int(row["provider_match_id"])] = block
        counts[block] += 1
    return blocks, {
        "development_end": development_cutoff.isoformat(),
        "validation_end": validation_cutoff.isoformat(),
        "match_counts": dict(counts),
        "kickoff_counts": {
            "development": sum(value <= development_cutoff for value in kickoffs),
            "validation": sum(development_cutoff < value <= validation_cutoff for value in kickoffs),
            "confirmation": sum(value > validation_cutoff for value in kickoffs),
        },
        "match_overlap": 0,
        "kickoff_overlap": 0,
    }


def _scaled_markov_config(scale: float) -> MarkovLiveConfig:
    base = MarkovLiveConfig()
    if not math.isfinite(scale) or scale < 0.0 or scale > 2.0:
        raise ValueError("invalid_markov_state_scale")
    home = tuple(1.0 + scale * (value - 1.0) for value in base.state_goal_multipliers_home)
    away = tuple(1.0 + scale * (value - 1.0) for value in base.state_goal_multipliers_away)
    return replace(
        base,
        model_version=f"markov_live_v1_historical_scale_{scale:g}",
        state_goal_multipliers_home=home,
        state_goal_multipliers_away=away,
    )


def _next_event_target(
    events: Sequence[dict[str, Any]], snapshot_clock: float, horizon_minutes: float,
    home_team_id: int, away_team_id: int,
) -> str:
    upper = snapshot_clock + horizon_minutes * 60.0
    for event in events:
        clock = float(event["match_clock_seconds"])
        event_type = str(event["event_type"])
        if clock <= snapshot_clock or clock > upper or event_type not in NEXT_EVENT_TYPES:
            continue
        team_id = event.get("team_id")
        if team_id not in {home_team_id, away_team_id}:
            continue
        side = "home" if int(team_id) == home_team_id else "away"
        target_type = "card" if event_type in {"yellow", "red"} else event_type
        return f"{side}:{target_type}"
    return "no_event"


def _one_x_two_target(home_score: int, away_score: int) -> str:
    return "home" if home_score > away_score else ("away" if home_score < away_score else "draw")


def _probability(markets: dict[str, float], target: str) -> float:
    return float(markets[f"probability_{target}"])


def _next_probability(next_event: dict[str, Any], target: str) -> float:
    if target == "no_event":
        return float(next_event["probability_no_event"])
    return float(next_event["probabilities"].get(target, 0.0))


def _safe_log_loss(probability: float) -> float:
    return -math.log(min(1.0 - 1e-12, max(1e-12, probability)))


def _binary_brier(probability: float, target: bool) -> float:
    return (probability - float(target)) ** 2


def _multiclass_brier(probabilities: dict[str, float], target: str) -> float:
    return math.fsum(
        (float(probability) - float(label == target)) ** 2
        for label, probability in probabilities.items()
    ) / 2.0


def _prediction_losses(
    output: dict[str, Any], targets: dict[str, Any],
) -> dict[str, float]:
    markets = output["markets"]
    one_x_two = {
        key: float(markets[f"probability_{key}"])
        for key in ("home", "draw", "away")
    }
    over_probability = float(markets["probability_over_2_5"])
    btts_probability = float(markets["probability_btts"])
    next_probabilities = dict(output["next_event"]["probabilities"])
    next_probabilities["no_event"] = float(output["next_event"]["probability_no_event"])
    losses = {
        "one_x_two_log_loss": _safe_log_loss(one_x_two[targets["one_x_two"]]),
        "over_2_5_log_loss": _safe_log_loss(
            over_probability if targets["over_2_5"] else 1.0 - over_probability
        ),
        "btts_log_loss": _safe_log_loss(
            btts_probability if targets["btts"] else 1.0 - btts_probability
        ),
        "next_event_log_loss": _safe_log_loss(
            next_probabilities.get(targets["next_event"], 0.0)
        ),
        "one_x_two_brier": _multiclass_brier(one_x_two, targets["one_x_two"]),
        "over_2_5_brier": _binary_brier(over_probability, targets["over_2_5"]),
        "btts_brier": _binary_brier(btts_probability, targets["btts"]),
        "next_event_brier": _multiclass_brier(next_probabilities, targets["next_event"]),
    }
    losses["goal_market_log_loss"] = math.fsum(
        losses[key] for key in (
            "one_x_two_log_loss", "over_2_5_log_loss", "btts_log_loss",
        )
    ) / 3.0
    losses["goal_market_brier"] = math.fsum(
        losses[key] for key in (
            "one_x_two_brier", "over_2_5_brier", "btts_brier",
        )
    ) / 3.0
    return losses


def prediction_rows(
    matches: Sequence[dict[str, Any]],
    events_by_match: dict[int, list[dict[str, Any]]],
    priors: dict[int, dict[str, Any]],
    config: HistoricalLiveConfig,
    *,
    markov_state_scale: float,
    hawkes_rho_goal: float | None,
    hawkes_rho_next_event: float | None,
    hawkes_allowed_leagues: frozenset[str] | None = None,
) -> list[dict[str, Any]]:
    """Reconstruye snapshots y puntúa baseline, Markov y combinación."""

    markov = MarkovLiveV1(_scaled_markov_config(markov_state_scale))
    hawkes = None
    if hawkes_rho_goal is not None or hawkes_rho_next_event is not None:
        hawkes = HawkesLiveV2(HawkesLiveConfig(
            rho=0.0,
            rho_goal=hawkes_rho_goal or 0.0,
            rho_next_event=hawkes_rho_next_event or 0.0,
        ))
    fallback_hawkes = HawkesLiveV2(HawkesLiveConfig(
        rho=0.0, rho_goal=0.0, rho_next_event=0.0,
    )) if hawkes is not None and hawkes_allowed_leagues is not None else None
    rows: list[dict[str, Any]] = []
    for match in sorted(matches, key=lambda row: (_utc(row["kickoff_ts"]), int(row["provider_match_id"]))):
        match_id = int(match["provider_match_id"])
        prior = priors.get(match_id)
        if prior is None:
            continue
        kickoff = _utc(match["kickoff_ts"])
        home_id, away_id = int(match["home_team_id"]), int(match["away_team_id"])
        events = sorted(
            events_by_match.get(match_id, []),
            key=lambda row: (float(row["match_clock_seconds"]), str(row["event_id"])),
        )
        for minute in config.snapshot_minutes:
            clock = float(minute * 60)
            observed = [event for event in events if float(event["match_clock_seconds"]) <= clock]
            score_events = [event for event in observed if event["event_type"] in {"goal", "penalty_scored"}]
            score_home = sum(event.get("team_id") == home_id for event in score_events)
            score_away = sum(event.get("team_id") == away_id for event in score_events)
            request = MarkovLiveInput(
                match_id=match_id,
                home_team_id=home_id,
                away_team_id=away_id,
                kickoff_ts=kickoff.isoformat(),
                snapshot_ts=(kickoff + timedelta(seconds=clock)).isoformat(),
                match_clock_seconds=clock,
                period=1 if minute < 45 else 2,
                score_home=score_home,
                score_away=score_away,
                lambda_base_home=float(prior["lambda_base_home"]),
                lambda_base_away=float(prior["lambda_base_away"]),
                events=tuple(observed),
                league_slug=str(match["league_slug"]),
                source_hash=str(prior["source_hash"]),
            )
            score_request = replace(request, events=tuple(score_events))
            score_time = markov.predict(score_request)
            markov_output = markov.predict(request)
            targets = {
                "one_x_two": _one_x_two_target(int(match["home_score"]), int(match["away_score"])),
                "over_2_5": int(match["home_score"]) + int(match["away_score"]) > 2,
                "btts": int(match["home_score"]) > 0 and int(match["away_score"]) > 0,
                "next_event": _next_event_target(
                    events, clock, markov.config.horizon_minutes, home_id, away_id,
                ),
            }
            model_outputs = {"score_time": score_time, "markov": markov_output}
            if hawkes is not None:
                league = str(match["league_slug"])
                composer = (
                    hawkes
                    if hawkes_allowed_leagues is None or league in hawkes_allowed_leagues
                    else fallback_hawkes
                )
                if composer is None:  # pragma: no cover - invariante defensiva
                    raise RuntimeError("missing_hawkes_composer")
                model_outputs["combined"] = composer.combine(
                    markov_output, observed,
                    home_team_id=home_id,
                    away_team_id=away_id,
                    score_home=score_home,
                    score_away=score_away,
                )["combined_live"]
            row = {
                "match_id": match_id,
                "league_slug": str(match["league_slug"]),
                "kickoff_ts": kickoff.isoformat(),
                "snapshot_minute": minute,
                "score_home": score_home,
                "score_away": score_away,
                "events_observed": len(observed),
                "targets": targets,
                "losses": {
                    name: _prediction_losses(output, targets)
                    for name, output in model_outputs.items()
                },
                "output_hashes": {
                    name: str(output["output_hash"])
                    for name, output in model_outputs.items()
                },
            }
            rows.append(row)
    return rows


def per_match_metrics(
    rows: Sequence[dict[str, Any]], config: HistoricalLiveConfig,
) -> list[dict[str, Any]]:
    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[int(row["match_id"])].append(row)
    output = []
    for match_id, group in sorted(grouped.items()):
        models = sorted(set.intersection(*(
            set(row["losses"]) for row in group
        )))
        item: dict[str, Any] = {
            "match_id": match_id,
            "league_slug": str(group[0]["league_slug"]),
            "snapshot_count": len(group),
            "models": {},
        }
        for model in models:
            metric_names = sorted(group[0]["losses"][model])
            metrics = {
                metric: math.fsum(float(row["losses"][model][metric]) for row in group) / len(group)
                for metric in metric_names
            }
            metrics["objective"] = (
                config.objective_goal_weight * metrics["goal_market_log_loss"]
                + config.objective_next_event_weight * metrics["next_event_log_loss"]
            )
            item["models"][model] = metrics
        output.append(item)
    return output


def aggregate_metrics(per_match: Sequence[dict[str, Any]]) -> dict[str, Any]:
    if not per_match:
        raise ValueError("empty_historical_metrics")
    models = sorted(set.intersection(*(set(row["models"]) for row in per_match)))
    metrics: dict[str, Any] = {}
    for model in models:
        names = sorted(per_match[0]["models"][model])
        metrics[model] = {
            name: math.fsum(float(row["models"][model][name]) for row in per_match) / len(per_match)
            for name in names
        }
    return {
        "match_count": len(per_match),
        "league_count": len({str(row["league_slug"]) for row in per_match}),
        "models": metrics,
    }


def metrics_by_league(per_match: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in per_match:
        groups[str(row["league_slug"])].append(row)
    return [
        {"league_slug": league, **aggregate_metrics(rows)}
        for league, rows in sorted(groups.items())
    ]


def select_hawkes_league_admission(
    validation_per_match: Sequence[dict[str, Any]],
    config: HistoricalLiveConfig,
    *,
    rho_goal: float,
    rho_next_event: float,
) -> dict[str, Any]:
    """Congela una allowlist usando sólo soporte y mejora en validación."""

    decisions = []
    for row in metrics_by_league(validation_per_match):
        models = row["models"]
        if "combined" not in models or "markov" not in models:
            raise ValueError("hawkes_admission_requires_combined_and_markov")
        delta = (
            float(models["combined"]["goal_market_log_loss"])
            - float(models["markov"]["goal_market_log_loss"])
        )
        admitted = (
            int(row["match_count"]) >= config.minimum_hawkes_admission_matches
            and delta < 0.0
            and rho_goal > 0.0
        )
        decisions.append({
            "league_slug": str(row["league_slug"]),
            "validation_matches": int(row["match_count"]),
            "goal_market_log_loss_delta": delta,
            "admitted": admitted,
            "reason": (
                "validation_improvement_with_support" if admitted
                else "insufficient_support" if int(row["match_count"]) < config.minimum_hawkes_admission_matches
                else "no_validation_improvement"
            ),
        })
    allowed = sorted(row["league_slug"] for row in decisions if row["admitted"])
    return {
        "version": "hawkes_live_v2_league_admission_v1",
        "selection_split": "validation_only",
        "minimum_validation_matches": config.minimum_hawkes_admission_matches,
        "rho_goal": rho_goal,
        "rho_next_event": rho_next_event,
        "allowed_leagues": allowed,
        "allowed_league_count": len(allowed),
        "decisions": decisions,
        "confirmation_used_for_selection": False,
    }


def _candidate_result(
    value: float, per_match: list[dict[str, Any]], model: str,
) -> dict[str, Any]:
    aggregate = aggregate_metrics(per_match)
    return {
        "value": value,
        "match_count": aggregate["match_count"],
        "league_count": aggregate["league_count"],
        "metrics": aggregate["models"][model],
    }


def bootstrap_deltas(
    per_match: Sequence[dict[str, Any]], config: HistoricalLiveConfig,
) -> dict[str, Any]:
    """Bootstrap por partido completo para los dos saltos incrementales."""

    comparisons = {
        "markov_minus_score_time": ("markov", "score_time"),
        "combined_minus_markov": ("combined", "markov"),
    }
    available = set.intersection(*(set(row["models"]) for row in per_match))
    rng = np.random.default_rng(config.bootstrap_seed)
    output: dict[str, Any] = {}
    for label, (candidate, baseline) in comparisons.items():
        if candidate not in available or baseline not in available:
            continue
        output[label] = {}
        for metric in ("goal_market_log_loss", "next_event_log_loss", "objective"):
            values = np.asarray([
                float(row["models"][candidate][metric])
                - float(row["models"][baseline][metric])
                for row in per_match
            ], dtype=float)
            samples = rng.choice(
                values, size=(config.bootstrap_replicates, len(values)), replace=True,
            ).mean(axis=1)
            output[label][metric] = {
                "mean_delta": float(values.mean()),
                "ci_95": [
                    float(np.quantile(samples, 0.025)),
                    float(np.quantile(samples, 0.975)),
                ],
                "probability_improvement": float(np.mean(samples < 0.0)),
                "unit": "complete_match",
                "replicates": config.bootstrap_replicates,
            }
    return output


def evaluate_historical_live(
    database_url: str, config: HistoricalLiveConfig | None = None,
) -> dict[str, Any]:
    """Ejecuta selección anidada y confirmación sin modificar PostgreSQL."""

    cfg = config or HistoricalLiveConfig()
    matches, events, database_audit = read_historical_database(database_url)
    priors, prior_audit = walkforward_priors(matches, cfg)
    eligible = [row for row in matches if int(row["provider_match_id"]) in priors]
    blocks, partition = temporal_partition(eligible, cfg)
    split_rows = {
        block: [row for row in eligible if blocks[int(row["provider_match_id"])] == block]
        for block in ("development", "validation", "confirmation")
    }

    markov_search = []
    for scale in cfg.markov_state_scales:
        predictions = prediction_rows(
            split_rows["development"], events, priors, cfg,
            markov_state_scale=scale,
            hawkes_rho_goal=None, hawkes_rho_next_event=None,
        )
        match_metrics = per_match_metrics(predictions, cfg)
        markov_search.append(_candidate_result(scale, match_metrics, "markov"))
    selected_markov = min(
        markov_search, key=lambda row: (row["metrics"]["objective"], row["value"]),
    )
    markov_scale = float(selected_markov["value"])

    hawkes_goal_search = []
    for rho in cfg.hawkes_goal_rhos:
        predictions = prediction_rows(
            split_rows["validation"], events, priors, cfg,
            markov_state_scale=markov_scale,
            hawkes_rho_goal=rho, hawkes_rho_next_event=0.0,
        )
        match_metrics = per_match_metrics(predictions, cfg)
        hawkes_goal_search.append(_candidate_result(rho, match_metrics, "combined"))
    selected_hawkes_goal = min(
        hawkes_goal_search,
        key=lambda row: (row["metrics"]["goal_market_log_loss"], row["value"]),
    )
    hawkes_rho_goal = float(selected_hawkes_goal["value"])

    hawkes_next_event_search = []
    for rho in cfg.hawkes_next_event_rhos:
        predictions = prediction_rows(
            split_rows["validation"], events, priors, cfg,
            markov_state_scale=markov_scale,
            hawkes_rho_goal=hawkes_rho_goal, hawkes_rho_next_event=rho,
        )
        match_metrics = per_match_metrics(predictions, cfg)
        hawkes_next_event_search.append(
            _candidate_result(rho, match_metrics, "combined")
        )
    selected_hawkes_next_event = min(
        hawkes_next_event_search,
        key=lambda row: (row["metrics"]["next_event_log_loss"], row["value"]),
    )
    hawkes_rho_next_event = float(selected_hawkes_next_event["value"])

    admission_predictions = prediction_rows(
        split_rows["validation"], events, priors, cfg,
        markov_state_scale=markov_scale,
        hawkes_rho_goal=hawkes_rho_goal,
        hawkes_rho_next_event=hawkes_rho_next_event,
    )
    admission_by_match = per_match_metrics(admission_predictions, cfg)
    hawkes_policy = select_hawkes_league_admission(
        admission_by_match, cfg,
        rho_goal=hawkes_rho_goal,
        rho_next_event=hawkes_rho_next_event,
    )
    allowed_hawkes_leagues = frozenset(hawkes_policy["allowed_leagues"])

    confirmation_global_predictions = prediction_rows(
        split_rows["confirmation"], events, priors, cfg,
        markov_state_scale=markov_scale,
        hawkes_rho_goal=hawkes_rho_goal,
        hawkes_rho_next_event=hawkes_rho_next_event,
    )
    confirmation_global_by_match = per_match_metrics(
        confirmation_global_predictions, cfg,
    )

    confirmation_predictions = prediction_rows(
        split_rows["confirmation"], events, priors, cfg,
        markov_state_scale=markov_scale,
        hawkes_rho_goal=hawkes_rho_goal,
        hawkes_rho_next_event=hawkes_rho_next_event,
        hawkes_allowed_leagues=allowed_hawkes_leagues,
    )
    confirmation_by_match = per_match_metrics(confirmation_predictions, cfg)
    confirmation = aggregate_metrics(confirmation_by_match)
    league_metrics = metrics_by_league(confirmation_by_match)
    bootstrap = bootstrap_deltas(confirmation_by_match, cfg)
    global_confirmation = aggregate_metrics(confirmation_global_by_match)
    global_league_metrics = metrics_by_league(confirmation_global_by_match)
    global_bootstrap = bootstrap_deltas(confirmation_global_by_match, cfg)
    coverage_passed = (
        len(eligible) >= cfg.minimum_historical_matches
        and len({str(row["league_slug"]) for row in eligible}) >= cfg.minimum_historical_leagues
    )
    markov_ci = bootstrap["markov_minus_score_time"]["objective"]["ci_95"]
    hawkes_ci = bootstrap["combined_minus_markov"]["objective"]["ci_95"]
    global_hawkes_ci = global_bootstrap["combined_minus_markov"]["objective"]["ci_95"]
    markov_league_fraction = sum(
        row["models"]["markov"]["objective"]
        <= row["models"]["score_time"]["objective"]
        for row in league_metrics
    ) / len(league_metrics)
    hawkes_league_fraction = sum(
        row["models"]["combined"]["objective"]
        <= row["models"]["markov"]["objective"]
        for row in league_metrics
    ) / len(league_metrics)
    global_hawkes_league_fraction = sum(
        row["models"]["combined"]["objective"]
        <= row["models"]["markov"]["objective"]
        for row in global_league_metrics
    ) / len(global_league_metrics)
    confirmation_leagues = {
        str(row["league_slug"]) for row in confirmation_by_match
    }
    admitted_confirmation_leagues = (
        allowed_hawkes_leagues & confirmation_leagues
    )
    markov_confirmed = (
        coverage_passed
        and markov_ci[1] < 0.0
        and markov_league_fraction >= cfg.minimum_non_degraded_league_fraction
    )
    hawkes_aggregate_confirmed = (
        coverage_passed
        and len(admitted_confirmation_leagues) >= cfg.minimum_admitted_hawkes_leagues
        and hawkes_ci[1] < 0.0
    )
    global_hawkes_aggregate_confirmed = (
        coverage_passed
        and (hawkes_rho_goal > 0.0 or hawkes_rho_next_event > 0.0)
        and global_hawkes_ci[1] < 0.0
    )
    hawkes_confirmed = (
        hawkes_aggregate_confirmed
        and hawkes_league_fraction >= cfg.minimum_non_degraded_league_fraction
    )
    if markov_confirmed and hawkes_confirmed:
        classification = "historically_validated_markov_and_selective_hawkes_shadow"
    elif markov_confirmed and hawkes_aggregate_confirmed:
        classification = "historically_validated_markov_hawkes_heterogeneous"
    elif markov_confirmed:
        classification = "historically_validated_markov_hawkes_fallback"
    else:
        classification = "historical_no_confirmed_incremental_value"
    replay_hash = _stable_hash({
        "config": asdict(cfg),
        "selected_markov_scale": markov_scale,
        "selected_hawkes_rho_goal": hawkes_rho_goal,
        "selected_hawkes_rho_next_event": hawkes_rho_next_event,
        "hawkes_league_policy": hawkes_policy,
        "confirmation_global": confirmation_global_by_match,
        "confirmation": confirmation_by_match,
        "bootstrap": bootstrap,
        "global_bootstrap": global_bootstrap,
    })
    return {
        "phase": 114,
        "version": cfg.version,
        "classification": classification,
        "status": "shadow_not_official",
        "config": asdict(cfg),
        "database_audit": database_audit,
        "prior_audit": prior_audit,
        "coverage": {
            "eligible_matches": len(eligible),
            "represented_leagues": len({str(row["league_slug"]) for row in eligible}),
            "snapshot_count_confirmation": len(confirmation_predictions),
            "gate_passed": coverage_passed,
        },
        "partition": partition,
        "selection": {
            "markov_development_search": markov_search,
            "selected_markov_state_scale": markov_scale,
            "hawkes_goal_validation_search": hawkes_goal_search,
            "selected_hawkes_rho_goal": hawkes_rho_goal,
            "hawkes_next_event_validation_search": hawkes_next_event_search,
            "selected_hawkes_rho_next_event": hawkes_rho_next_event,
            "hawkes_league_policy": hawkes_policy,
        },
        "confirmation": confirmation,
        "confirmation_by_league": league_metrics,
        "bootstrap": bootstrap,
        "global_confirmation": global_confirmation,
        "global_confirmation_by_league": global_league_metrics,
        "global_bootstrap": global_bootstrap,
        "gates": {
            "coverage": coverage_passed,
            "priors_strictly_prior": prior_audit["strictly_prior"],
            "atomic_kickoffs": partition["kickoff_overlap"] == 0,
            "database_unchanged": database_audit["counts_identical"],
            "markov_incremental_confirmed": markov_confirmed,
            "hawkes_incremental_confirmed": hawkes_confirmed,
            "hawkes_aggregate_incremental_confirmed": hawkes_aggregate_confirmed,
            "hawkes_global_aggregate_incremental_confirmed": global_hawkes_aggregate_confirmed,
            "markov_non_degraded_league_fraction": markov_league_fraction,
            "hawkes_non_degraded_league_fraction": hawkes_league_fraction,
            "hawkes_global_non_degraded_league_fraction": global_hawkes_league_fraction,
            "hawkes_admitted_league_count": len(allowed_hawkes_leagues),
            "hawkes_admitted_confirmation_league_count": len(admitted_confirmation_leagues),
            "official_router_modified": False,
        },
        "replay_hash": replay_hash,
    }


def write_historical_artifacts(result: dict[str, Any], output: Path) -> None:
    """Escribe únicamente evidencia agregada, nunca payloads raw."""

    output.mkdir(parents=True, exist_ok=True)
    payloads = {
        "historical_validation.json": result,
        "historical_metrics.json": {
            "selection": result["selection"],
            "confirmation": result["confirmation"],
            "confirmation_by_league": result["confirmation_by_league"],
            "bootstrap": result["bootstrap"],
            "global_confirmation": result["global_confirmation"],
            "global_confirmation_by_league": result["global_confirmation_by_league"],
            "global_bootstrap": result["global_bootstrap"],
        },
        "hawkes_league_policy.json": result["selection"]["hawkes_league_policy"],
        "historical_audit.json": {
            "classification": result["classification"],
            "database_audit": result["database_audit"],
            "prior_audit": result["prior_audit"],
            "coverage": result["coverage"],
            "partition": result["partition"],
            "gates": result["gates"],
            "replay_hash": result["replay_hash"],
        },
    }
    for name, payload in payloads.items():
        (output / name).write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str),
            encoding="utf-8",
        )
    markov = result["bootstrap"]["markov_minus_score_time"]["objective"]
    hawkes = result["bootstrap"]["combined_minus_markov"]["objective"]
    report = [
        "# Fase 114 — validación histórica live",
        "",
        f"Clasificación: `{result['classification']}`.",
        "",
        f"- partidos elegibles: `{result['coverage']['eligible_matches']}`;",
        f"- ligas: `{result['coverage']['represented_leagues']}`;",
        f"- snapshots de confirmación: `{result['coverage']['snapshot_count_confirmation']}`;",
        f"- escala Markov seleccionada: `{result['selection']['selected_markov_state_scale']}`;",
        f"- rho Hawkes para goles: `{result['selection']['selected_hawkes_rho_goal']}`;",
        f"- rho Hawkes para próximo evento: `{result['selection']['selected_hawkes_rho_next_event']}`;",
        f"- ligas admitidas para Hawkes: `{result['gates']['hawkes_admitted_league_count']}`;",
        f"- delta objetivo Markov vs score/tiempo: `{markov['mean_delta']}`;",
        f"- IC95 Markov: `{markov['ci_95']}`;",
        f"- delta objetivo combinado vs Markov: `{hawkes['mean_delta']}`;",
        f"- IC95 Hawkes: `{hawkes['ci_95']}`;",
        "",
        "La unidad estadística es el partido completo. PostgreSQL se leyó en",
        "modo read-only; el router oficial no fue modificado.",
    ]
    (output / "historical_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")


__all__ = [
    "HistoricalLiveConfig", "aggregate_metrics", "bootstrap_deltas",
    "evaluate_historical_live", "metrics_by_league", "per_match_metrics",
    "prediction_rows", "read_historical_database", "temporal_partition",
    "select_hawkes_league_admission", "walkforward_priors",
    "write_historical_artifacts",
]
