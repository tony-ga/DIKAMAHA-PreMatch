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


def _market_context(summary: dict[str, Any]) -> dict[str, Any]:
    pickcenter = summary.get("pickcenter")
    providers = pickcenter if isinstance(pickcenter, list) else []
    if isinstance(pickcenter, dict):
        providers = [pickcenter]
    return {
        "status": (
            "financial_isolated_available" if providers else "not_published"
        ),
        "provider_count": len(providers),
        "consumed_by_models": False,
        "odds_exposed": False,
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


__all__ = [
    "CONTRACT_VERSION", "ProviderMatchContextService",
    "normalize_provider_match_context",
]
