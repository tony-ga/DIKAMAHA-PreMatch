"""Parser reconciliado de outcomes para mercados agregados.

Version: 1.0.0
Created: 2026-07-28
"""
from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any

from src.count_market_prospective import FrozenCountOutcome, FrozenCountPrediction
from src.espn_event_taxonomy import classify_play
from src.prematch_raw_store import canonical_hash

TEAM_REF = re.compile(r"/teams/(\d+)")


class CountOutcomeParser(ABC):
    """Puerto para parsers post-match reconciliados."""

    @abstractmethod
    def parse(
        self, prediction: FrozenCountPrediction, summary: dict[str, Any],
        plays: dict[str, Any], captured_at: datetime,
    ) -> FrozenCountOutcome:
        """Convierte raws persistidos en outcomes verificables."""


def _competition(summary: dict[str, Any]) -> dict[str, Any]:
    """Extrae una competición final y con identidad."""

    header = summary.get("header")
    competitions = header.get("competitions") if isinstance(header, dict) else None
    competition = competitions[0] if isinstance(competitions, list) and competitions else None
    if not isinstance(competition, dict):
        raise ValueError("outcome_summary_competition_missing")
    status = (competition.get("status") or {}).get("type") or {}
    if not bool(status.get("completed")) and status.get("state") != "post":
        raise ValueError("outcome_match_not_final")
    return competition


def _teams(competition: dict[str, Any]) -> dict[str, int]:
    """Obtiene IDs home/away sin inferencia."""

    competitors = competition.get("competitors")
    if not isinstance(competitors, list):
        raise ValueError("outcome_competitors_missing")
    output = {}
    for row in competitors:
        team = row.get("team") if isinstance(row, dict) else None
        identifier = team.get("id") if isinstance(team, dict) else None
        if row.get("homeAway") in {"home", "away"} and str(identifier).isdigit():
            output[str(row["homeAway"])] = int(identifier)
    if output.keys() != {"home", "away"}:
        raise ValueError("outcome_orientation_missing")
    return output


def _integer(value: Any) -> int:
    """Convierte displayValue entero sin imputación."""

    text = str(value).strip()
    if not text.isdigit():
        raise ValueError("outcome_stat_not_integer")
    return int(text)


def _boxscore(summary: dict[str, Any]) -> dict[int, dict[str, int]]:
    """Extrae los conteos agregados requeridos."""

    teams = (summary.get("boxscore") or {}).get("teams")
    if not isinstance(teams, list):
        raise ValueError("outcome_boxscore_missing")
    mapping = {"wonCorners": "corners", "totalShots": "shots",
               "yellowCards": "yellow_cards"}
    output = {}
    for row in teams:
        team, statistics = row.get("team") or {}, row.get("statistics")
        if not str(team.get("id")).isdigit() or not isinstance(statistics, list):
            continue
        values = {mapping[item["name"]]: _integer(item.get("displayValue"))
                  for item in statistics if item.get("name") in mapping}
        if set(values) >= set(mapping.values()):
            output[int(team["id"])] = values
    return output


def _team_id(play: dict[str, Any]) -> int | None:
    """Extrae identidad directa o desde referencia Core."""

    team = play.get("team")
    if not isinstance(team, dict):
        return None
    direct = team.get("id")
    if str(direct).isdigit():
        return int(direct)
    match = TEAM_REF.search(str(team.get("$ref") or ""))
    return int(match.group(1)) if match else None


def _yellow_cards(plays: dict[str, Any]) -> tuple[dict[int, int], int]:
    """Cuenta amarillas válidas y las anteriores a 45:00."""

    items = plays.get("items")
    if not isinstance(items, list):
        raise ValueError("outcome_plays_missing")
    totals, first_half = {}, 0
    for play in items:
        if not isinstance(play, dict) or bool(play.get("annulled")):
            continue
        event_type, _ = classify_play(play)
        if event_type != "yellow":
            continue
        team_id = _team_id(play)
        clock = (play.get("clock") or {}).get("value")
        if team_id is None or not isinstance(clock, (int, float)):
            raise ValueError("outcome_yellow_identity_or_clock_missing")
        totals[team_id] = totals.get(team_id, 0) + 1
        first_half += int(float(clock) < 2700.0)
    return totals, first_half


def _validate_identity(
    prediction: FrozenCountPrediction, summary: dict[str, Any],
    competition: dict[str, Any], teams: dict[str, int],
) -> None:
    """Valida evento, orientación y kickoff congelados."""

    if int((summary.get("header") or {}).get("id") or 0) != prediction.match_id:
        raise ValueError("outcome_match_identity_mismatch")
    if teams != {"home": prediction.home_team_id,
                 "away": prediction.away_team_id}:
        raise ValueError("outcome_team_identity_mismatch")
    kickoff = datetime.fromisoformat(
        str(competition.get("date")).replace("Z", "+00:00"))
    if kickoff != prediction.kickoff_ts:
        raise ValueError("outcome_kickoff_mismatch")


class EspnCountOutcomeParser(CountOutcomeParser):
    """Parser ESPN de boxscore y play-by-play."""

    def parse(
        self, prediction: FrozenCountPrediction, summary: dict[str, Any],
        plays: dict[str, Any], captured_at: datetime,
    ) -> FrozenCountOutcome:
        """Reconciliación estricta sin imputaciones."""

        competition = _competition(summary)
        teams = _teams(competition)
        _validate_identity(prediction, summary, competition, teams)
        stats = _boxscore(summary)
        if not set(teams.values()).issubset(stats):
            raise ValueError("outcome_required_stats_missing")
        yellow, first_half = _yellow_cards(plays)
        if any(yellow.get(team_id, 0) != stats[team_id]["yellow_cards"]
               for team_id in teams.values()):
            raise ValueError("outcome_yellow_reconciliation_failed")
        counts = _counts(stats, teams, first_half)
        return FrozenCountOutcome(
            prediction.league_slug, prediction.match_id, captured_at, counts,
            _outcomes(counts), canonical_hash(summary), canonical_hash(plays),
            "accepted")


class EspnMarkovMarketOutcomeParser(CountOutcomeParser):
    """Parser de corners y tiros comerciales por mitad."""

    def parse(
        self, prediction: FrozenCountPrediction, summary: dict[str, Any],
        plays: dict[str, Any], captured_at: datetime,
    ) -> FrozenCountOutcome:
        """Reconcilia mitades contra boxscore antes de liquidar."""

        competition = _competition(summary)
        teams = _teams(competition)
        _validate_identity(prediction, summary, competition, teams)
        stats = _boxscore(summary)
        if not set(teams.values()).issubset(stats):
            raise ValueError("outcome_required_stats_missing")
        temporal = _temporal_counts(plays, set(teams.values()))
        _validate_temporal_totals(stats, temporal, set(teams.values()))
        counts = _markov_counts(temporal, teams)
        return FrozenCountOutcome(
            prediction.league_slug, prediction.match_id, captured_at, counts,
            _markov_outcomes(counts), canonical_hash(summary),
            canonical_hash(plays), "accepted")


def _temporal_counts(
    plays: dict[str, Any], team_ids: set[int],
) -> dict[tuple[int, str, int], int]:
    """Cuenta corners y tiros por equipo y periodo reglamentario."""

    items = plays.get("items")
    if not isinstance(items, list):
        raise ValueError("outcome_plays_missing")
    output: dict[tuple[int, str, int], int] = {}
    for play in items:
        if not isinstance(play, dict) or bool(play.get("annulled")):
            continue
        metric = _temporal_metric(classify_play(play)[0])
        if metric is None:
            continue
        team_id, period = _team_id(play), _period_number(play)
        if team_id not in team_ids or period not in {1, 2}:
            raise ValueError("outcome_temporal_identity_or_period_missing")
        key = (int(team_id), metric, int(period))
        output[key] = output.get(key, 0) + 1
    return output


def _temporal_metric(event_type: str) -> str | None:
    """Mapea la taxonomía a conteos comerciales."""

    if event_type == "corner":
        return "corners"
    if event_type == "goal" or event_type.startswith("shot_"):
        return "shots"
    return None


def _period_number(play: dict[str, Any]) -> int | None:
    """Extrae periodo explícito sin inferir por minuto."""

    period = play.get("period")
    number = period.get("number") if isinstance(period, dict) else None
    return int(number) if str(number).isdigit() else None


def _validate_temporal_totals(
    stats: dict[int, dict[str, int]],
    temporal: dict[tuple[int, str, int], int], team_ids: set[int],
) -> None:
    """Exige igualdad entre suma de mitades y boxscore."""

    for team_id in team_ids:
        for metric in ("corners", "shots"):
            observed = sum(
                temporal.get((team_id, metric, period), 0)
                for period in (1, 2))
            if observed != stats[team_id][metric]:
                raise ValueError(
                    f"outcome_{metric}_temporal_reconciliation_failed")


def _markov_counts(
    temporal: dict[tuple[int, str, int], int],
    teams: dict[str, int],
) -> dict[str, int]:
    """Compone conteos necesarios para cuatro líneas Markov."""

    home, away = teams["home"], teams["away"]
    return {
        "home_corners_second_half": temporal.get(
            (home, "corners", 2), 0),
        "home_shots_first_half": temporal.get((home, "shots", 1), 0),
        "home_shots_second_half": temporal.get((home, "shots", 2), 0),
        "away_shots_second_half": temporal.get((away, "shots", 2), 0),
    }


def _markov_outcomes(counts: dict[str, int]) -> dict[str, bool]:
    """Liquida las cuatro líneas congeladas."""

    return {
        "away_shots_second_half_over_5_5":
            counts["away_shots_second_half"] > 5,
        "home_corners_second_half_over_2_5":
            counts["home_corners_second_half"] > 2,
        "home_shots_first_half_over_5_5":
            counts["home_shots_first_half"] > 5,
        "home_shots_second_half_over_5_5":
            counts["home_shots_second_half"] > 5,
    }


def _counts(
    stats: dict[int, dict[str, int]], teams: dict[str, int], first_half: int,
) -> dict[str, int]:
    """Compone los conteos necesarios por las cuatro líneas."""

    return {
        "home_corners": stats[teams["home"]]["corners"],
        "away_corners": stats[teams["away"]]["corners"],
        "away_shots": stats[teams["away"]]["shots"],
        "first_half_yellow_cards": first_half,
    }


def _outcomes(counts: dict[str, int]) -> dict[str, bool]:
    """Convierte conteos en outcomes de las líneas congeladas."""

    return {
        "home_corners_over_4_5": counts["home_corners"] > 4,
        "away_corners_over_4_5": counts["away_corners"] > 4,
        "away_shots_over_10_5": counts["away_shots"] > 10,
        "first_half_cards_over_1_5": counts["first_half_yellow_cards"] > 1,
    }


# Version: 1.0.0
# Created: 2026-07-28
