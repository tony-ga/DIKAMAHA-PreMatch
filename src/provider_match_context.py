"""Contexto analítico externo de partido, normalizado y sólo de presentación.

El proveedor no publica su predictor para todos los eventos de fútbol. Este
módulo conserva esa ausencia como ``not_published`` y nunca infiere una
probabilidad analítica desde ``pickcenter`` u otras cuotas.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Iterable

from src.espn_prospective_connector import (
    EspnConnectorConfig,
    EspnProspectiveConnector,
)

CONTRACT_VERSION = "provider_match_context_v1"
MARKET_CONTRACT_VERSION = "provider_market_tape_v1"
_LEAGUE_PATTERN = re.compile(r"^[A-Za-z0-9._]+$")
_PROBABILITY_CONTAINER_KEYS = frozenset({
    "predictor", "winprobability", "winProbability", "probabilities",
})


def _probability(value: Any) -> float | None:
    """Normaliza porcentajes explícitos expresados en [0, 1] o [0, 100]."""

    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(numeric) or numeric < 0.0:
        return None
    if numeric > 1.0:
        numeric /= 100.0
    return numeric if numeric <= 1.0 else None


def _first_value(row: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in row:
            return row[name]
    return None


def _triplet(row: Any) -> dict[str, float] | None:
    """Extrae exclusivamente un triplete 1X2 publicado de forma explícita."""

    if not isinstance(row, dict):
        return None
    home_row = row.get("homeTeam") if isinstance(row.get("homeTeam"), dict) else {}
    away_row = row.get("awayTeam") if isinstance(row.get("awayTeam"), dict) else {}
    tie_row = row.get("tie") if isinstance(row.get("tie"), dict) else {}
    home = _probability(_first_value(row, (
        "homeWinPercentage", "homeWinProbability", "homeProbability",
        "home", "homeWinPct",
    )))
    if home is None:
        home = _probability(_first_value(home_row, (
            "winPercentage", "winProbability", "gameProjection",
        )))
    draw = _probability(_first_value(row, (
        "tiePercentage", "drawPercentage", "tieProbability",
        "drawProbability", "tie", "draw",
    )))
    if draw is None:
        draw = _probability(_first_value(tie_row, (
            "percentage", "probability", "gameProjection",
        )))
    away = _probability(_first_value(row, (
        "awayWinPercentage", "awayWinProbability", "awayProbability",
        "away", "awayWinPct",
    )))
    if away is None:
        away = _probability(_first_value(away_row, (
            "winPercentage", "winProbability", "gameProjection",
        )))
    if home is None or draw is None or away is None:
        return None
    total = home + draw + away
    if not 0.97 <= total <= 1.03:
        return None
    return {
        "home": home / total,
        "draw": draw / total,
        "away": away / total,
    }


def _named_probability_nodes(payload: Any) -> list[Any]:
    """Busca sólo contenedores cuyo nombre declara semántica predictiva."""

    output: list[Any] = []

    def visit(value: Any) -> None:
        if isinstance(value, dict):
            for key, child in value.items():
                if key in _PROBABILITY_CONTAINER_KEYS:
                    output.append(child)
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(payload)
    return output


def _display_minute(row: dict[str, Any], fallback: int) -> int:
    clock = row.get("clock")
    display = clock.get("displayValue") if isinstance(clock, dict) else None
    raw = row.get("minute", display)
    match = re.search(r"\d+", str(raw or ""))
    return max(0, int(match.group())) if match else fallback


def _history(nodes: list[Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[tuple[int, float, float, float]] = set()
    for node in nodes:
        candidates = node if isinstance(node, list) else []
        if isinstance(node, dict):
            for key in ("history", "items", "entries", "probabilities"):
                value = node.get(key)
                if isinstance(value, list):
                    candidates = [*candidates, *value]
        for index, candidate in enumerate(candidates):
            values = _triplet(candidate)
            if values is None or not isinstance(candidate, dict):
                continue
            minute = _display_minute(candidate, index)
            key = (minute, values["home"], values["draw"], values["away"])
            if key in seen:
                continue
            seen.add(key)
            output.append({
                "minute": minute,
                "period": int(candidate.get("period") or 0),
                **values,
            })
    return sorted(output, key=lambda row: (row["minute"], row["period"]))


def _quote(value: Any) -> dict[str, str] | None:
    """Conserva línea/cuota publicada sin derivar probabilidad ni señal."""

    if not isinstance(value, dict):
        return None
    output = {
        key: str(value[key]) for key in ("line", "odds")
        if value.get(key) is not None and value.get(key) != ""
    }
    return output or None


def _market_sides(
    value: Any, sides: tuple[str, ...],
) -> dict[str, dict[str, dict[str, str]]]:
    if not isinstance(value, dict):
        return {}
    output: dict[str, dict[str, dict[str, str]]] = {}
    for side in sides:
        row = value.get(side)
        if not isinstance(row, dict):
            continue
        cuts = {
            cut: quote for cut in ("open", "close", "live")
            if (quote := _quote(row.get(cut))) is not None
        }
        if cuts:
            output[side] = cuts
    return output


def _market_provider(row: Any) -> dict[str, Any] | None:
    """Reduce un proveedor a una cinta read-only, sin links de ejecución."""

    if not isinstance(row, dict):
        return None
    provider = row.get("provider") if isinstance(row.get("provider"), dict) else {}
    markets = {
        "moneyline": _market_sides(row.get("moneyline"), ("home", "draw", "away")),
        "spread": _market_sides(row.get("pointSpread"), ("home", "away")),
        "total": _market_sides(row.get("total"), ("over", "under")),
    }
    markets = {key: value for key, value in markets.items() if value}
    if not markets:
        return None
    return {
        "provider_id": str(provider.get("id") or ""),
        "provider_name": str(provider.get("name") or provider.get("displayName") or "Proveedor"),
        "details": str(row.get("details") or ""),
        "markets": markets,
    }


def _market_rows(payload: dict[str, Any]) -> list[Any]:
    source = payload.get("pickcenter")
    if not source:
        source = payload.get("odds")
    if isinstance(source, dict):
        return [source]
    return source if isinstance(source, list) else []


def _market_context(summary: dict[str, Any]) -> dict[str, Any]:
    providers = [
        normalized for row in _market_rows(summary)
        if (normalized := _market_provider(row)) is not None
    ]
    return {
        "status": (
            "financial_isolated_available" if providers else "not_published"
        ),
        "provider_count": len(providers),
        "providers": providers,
        "consumed_by_models": False,
        "odds_exposed": bool(providers),
        "derived_probabilities": False,
        "recommendation_available": False,
    }


def _team_identity(competition: dict[str, Any], side: str) -> dict[str, Any]:
    competitors = competition.get("competitors")
    rows = competitors if isinstance(competitors, list) else []
    competitor = next((
        row for row in rows if isinstance(row, dict) and row.get("homeAway") == side
    ), {})
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    logos = team.get("logos") if isinstance(team.get("logos"), list) else []
    logo = next((str(row.get("href")) for row in logos if isinstance(row, dict) and row.get("href")), None)
    return {
        "id": str(team.get("id") or competitor.get("id") or ""),
        "name": str(team.get("displayName") or team.get("shortDisplayName") or team.get("name") or "Equipo"),
        "logo": logo,
    }


def normalize_provider_market_catalog(
    scoreboard: dict[str, Any], *, league: str, date: str,
    source_fetched_at: datetime | str,
) -> dict[str, Any]:
    """Normaliza `activeodds=true` sin exponer links ni fabricar pronósticos."""

    events = scoreboard.get("events")
    fixtures: list[dict[str, Any]] = []
    for event in events if isinstance(events, list) else []:
        if not isinstance(event, dict):
            continue
        competitions = event.get("competitions")
        competition = competitions[0] if isinstance(competitions, list) and competitions and isinstance(competitions[0], dict) else {}
        market = _market_context({"odds": competition.get("odds")})
        if market["status"] != "financial_isolated_available":
            continue
        status = competition.get("status") if isinstance(competition.get("status"), dict) else {}
        status_type = status.get("type") if isinstance(status.get("type"), dict) else {}
        fixtures.append({
            "event_id": str(event.get("id") or competition.get("id") or ""),
            "league_slug": league,
            "kickoff_ts": str(event.get("date") or competition.get("date") or ""),
            "status": str(status_type.get("state") or "pre"),
            "status_detail": str(status_type.get("detail") or status_type.get("shortDetail") or ""),
            "home_team": _team_identity(competition, "home"),
            "away_team": _team_identity(competition, "away"),
            "market_context": market,
        })
    fetched_at = source_fetched_at.isoformat() if isinstance(source_fetched_at, datetime) else str(source_fetched_at)
    return {
        "contract_version": MARKET_CONTRACT_VERSION,
        "league_slug": league,
        "date": date,
        "status": "available" if fixtures else "not_published",
        "fixtures": fixtures,
        "count": len(fixtures),
        "source_name": "ESPN",
        "source_fetched_at": fetched_at,
        "role": "financial_isolated_display_only",
        "not_model_feature": True,
    }


def normalize_provider_match_context(
    summary: dict[str, Any], *, event_id: str, league: str,
    scope: str, source_fetched_at: datetime | str,
) -> dict[str, Any]:
    """Normaliza el predictor publicado sin alterar las capas DIKAMAHA."""

    if scope not in {"pre_match", "live"}:
        raise ValueError("invalid_provider_predictor_scope")
    nodes = _named_probability_nodes(summary)
    probabilities = next(
        (values for node in nodes if (values := _triplet(node)) is not None),
        None,
    )
    history = _history(nodes)
    if probabilities is None and history:
        probabilities = {
            key: history[-1][key] for key in ("home", "draw", "away")
        }
    fetched_at = (
        source_fetched_at.isoformat()
        if isinstance(source_fetched_at, datetime) else str(source_fetched_at)
    )
    return {
        "contract_version": CONTRACT_VERSION,
        "event_id": str(event_id),
        "league_slug": str(league),
        "scope": scope,
        "status": "available" if probabilities is not None else "not_published",
        "role": "external_benchmark_display_only",
        "model_name": "ESPN Soccer Predictor",
        "probabilities": probabilities,
        "history": history,
        "coverage": {
            "predictor": probabilities is not None,
            "history": bool(history),
        },
        "market_context": _market_context(summary),
        "source_name": "ESPN",
        "source_fetched_at": fetched_at,
        "not_model_feature": True,
        "replaces_dikamaha_models": False,
    }


class ProviderMatchContextService:
    """Obtiene y normaliza el summary raw-first de un evento."""

    def fetch(self, league: str, event_id: str, scope: str) -> dict[str, Any]:
        candidate = str(league).strip()
        if not _LEAGUE_PATTERN.fullmatch(candidate):
            raise ValueError("invalid_provider_predictor_league")
        if not str(event_id).isdigit():
            raise ValueError("invalid_provider_predictor_event_id")
        if scope not in {"pre_match", "live"}:
            raise ValueError("invalid_provider_predictor_scope")
        connector = EspnProspectiveConnector(EspnConnectorConfig(league=candidate))
        result = connector.summary_fetch_result(
            str(event_id), use_cache=False, include_predictor=True,
            preserve_raw=True,
        )
        return normalize_provider_match_context(
            result.payload,
            event_id=str(event_id),
            league=candidate,
            scope=scope,
            source_fetched_at=result.source_fetched_at,
        )

    def markets(self, league: str, date: str) -> dict[str, Any]:
        """Consulta la cinta global de una competición y fecha exacta."""

        candidate = str(league).strip()
        if not _LEAGUE_PATTERN.fullmatch(candidate):
            raise ValueError("invalid_provider_market_league")
        try:
            datetime.strptime(str(date), "%Y%m%d")
        except ValueError as error:
            raise ValueError("invalid_provider_market_date") from error
        connector = EspnProspectiveConnector(EspnConnectorConfig(league=candidate))
        result = connector.scoreboard_fetch_result(
            str(date), use_cache=False, active_odds=True, preserve_raw=True,
        )
        return normalize_provider_market_catalog(
            result.payload, league=candidate, date=str(date),
            source_fetched_at=result.source_fetched_at,
        )


__all__ = [
    "CONTRACT_VERSION", "MARKET_CONTRACT_VERSION", "ProviderMatchContextService",
    "normalize_provider_market_catalog", "normalize_provider_match_context",
]
