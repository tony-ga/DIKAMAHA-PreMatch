"""Explorador ESPN de sólo lectura para interfaces de usuario.

Todos los recursos pasan por ``EspnProspectiveConnector``, que conserva la
respuesta cruda en caché antes de que este módulo la normalice.

Requirements:
    - requests
    - tenacity

Version: 1.1.0
Created: 2026-07-29
"""
from __future__ import annotations

import re
import unicodedata
from abc import ABC, abstractmethod
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from src.espn_event_taxonomy import classify_play
from src.espn_fixture_resolver import scoreboard_fixtures
from src.espn_prospective_connector import (
    EspnConnectorConfig,
    EspnProspectiveConnector,
)

LEAGUES = (
    ("arg.1", "Liga Profesional Argentina"),
    ("bra.1", "Brasileirão"),
    ("chi.1", "Primera División de Chile"),
    ("col.1", "Liga BetPlay Colombia"),
    ("mex.1", "Liga MX"),
    ("per.1", "Liga 1 Perú"),
    ("uru.1", "Primera División de Uruguay"),
    ("usa.1", "Major League Soccer"),
    ("esp.1", "LaLiga"),
    ("esp.2", "LaLiga 2"),
    ("esp.copa_del_rey", "Copa del Rey"),
    ("esp.super_cup", "Supercopa de España"),
    ("esp.w.1", "Liga F España"),
    ("eng.1", "Premier League"),
    ("eng.2", "Championship"),
    ("eng.3", "League One"),
    ("eng.4", "League Two"),
    ("eng.5", "National League"),
    ("eng.fa", "FA Cup"),
    ("eng.league_cup", "Carabao Cup"),
    ("eng.w.1", "Women's Super League"),
    ("ger.1", "Bundesliga"),
    ("ita.1", "Serie A"),
    ("fra.1", "Ligue 1"),
    ("ned.1", "Eredivisie"),
    ("por.1", "Primeira Liga"),
    ("tur.1", "Süper Lig"),
    ("bel.1", "Pro League Bélgica"),
    ("sco.1", "Scottish Premiership"),
    ("den.1", "Superliga Dinamarca"),
    ("nor.1", "Eliteserien"),
    ("ksa.1", "Saudi Pro League"),
    ("jpn.1", "J1 League"),
    ("conmebol.america", "Copa América"),
    ("conmebol.libertadores", "Copa Libertadores"),
    ("conmebol.sudamericana", "Copa Sudamericana"),
    ("concacaf.champions", "Concacaf Champions Cup"),
    ("concacaf.gold", "Copa Oro Concacaf"),
    ("concacaf.leagues.cup", "Leagues Cup"),
    ("concacaf.nations.league", "Liga de Naciones Concacaf"),
    ("uefa.champions", "UEFA Champions League"),
    ("uefa.champions_qual", "Clasificación UEFA Champions League"),
    ("uefa.europa", "UEFA Europa League"),
    ("uefa.europa_qual", "Clasificación UEFA Europa League"),
    ("uefa.europa.conf", "UEFA Conference League"),
    ("uefa.europa.conf_qual", "Clasificación UEFA Conference League"),
    ("uefa.euro", "UEFA Euro"),
    ("uefa.nations", "UEFA Nations League"),
    ("uefa.super_cup", "Supercopa de la UEFA"),
    ("uefa.wchampions", "UEFA Women's Champions League"),
    ("uefa.weuro", "UEFA Women's Euro"),
    ("fifa.world", "Copa Mundial FIFA"),
    ("fifa.wwc", "Copa Mundial Femenina FIFA"),
    ("fifa.cwc", "Mundial de Clubes FIFA"),
    ("fifa.intercontinental_cup", "Copa Intercontinental FIFA"),
    ("fifa.friendly", "Amistosos internacionales"),
    ("fifa.friendly.w", "Amistosos internacionales femeninos"),
    ("fifa.olympics", "Fútbol Olímpico masculino"),
    ("fifa.w.olympics", "Fútbol Olímpico femenino"),
    ("fifa.worldq", "Eliminatorias Mundial FIFA"),
    ("fifa.worldq.concacaf", "Eliminatorias Mundial Concacaf"),
    ("fifa.worldq.conmebol", "Eliminatorias Mundial Conmebol"),
    ("fifa.worldq.uefa", "Eliminatorias Mundial UEFA"),
)
IMPORTANT_TYPES = frozenset({
    "goal", "penalty---scored", "own-goal", "yellow-card", "red-card",
    "substitution", "shot-on-target", "shot-off-target", "shot-blocked",
    "corner-awarded", "foul", "offside", "save", "halftime",
    "start-2nd-half", "end-regular-time",
})
COUNT_TYPES = {
    "goals": {"goal", "penalty---scored", "own-goal"},
    "shots": {
        "goal", "penalty---scored", "own-goal", "shot-on-target",
        "shot-off-target", "shot-blocked",
    },
    "shots_on_target": {
        "goal", "penalty---scored", "own-goal", "shot-on-target",
    },
    "corners": {"corner-awarded"},
    "yellow_cards": {"yellow-card"},
    "red_cards": {"red-card"},
    "fouls": {"foul"},
    "offsides": {"offside"},
    "saves": {"save"},
    "substitutions": {"substitution"},
}


class FootballDataExplorer(ABC):
    """Puerto de consulta para datos deportivos de presentación."""

    @abstractmethod
    def leagues(self) -> list[dict[str, str]]:
        """Lista ligas navegables."""

    @abstractmethod
    def fixtures(self, league: str, date: str) -> list[dict[str, Any]]:
        """Lista partidos de una liga y fecha."""

    @abstractmethod
    def plays(
        self, league: str, match_id: str, competition_id: str,
        scope: str = "key",
    ) -> dict[str, Any]:
        """Devuelve play-by-play normalizado."""

    @abstractmethod
    def statistics(
        self, league: str, match_id: str, competition_id: str,
    ) -> dict[str, Any]:
        """Devuelve estadísticas por periodo y totales."""

    @abstractmethod
    def teams(self, league: str, query: str = "") -> list[dict[str, Any]]:
        """Lista o busca equipos."""

    @abstractmethod
    def roster(self, league: str, team_id: str) -> dict[str, Any]:
        """Devuelve jugadores de un equipo."""

    @abstractmethod
    def player(
        self, league: str, team_id: str, player_id: str,
    ) -> dict[str, Any]:
        """Devuelve perfil y estadísticas de jugador."""


class EspnFootballDataExplorer(FootballDataExplorer):
    """Implementación sobre el conector ESPN raw-first existente."""

    def leagues(self) -> list[dict[str, str]]:
        """Lista el catálogo congelado de ligas operativas."""

        return [{"slug": slug, "name": name} for slug, name in LEAGUES]

    def fixtures(self, league: str, date: str) -> list[dict[str, Any]]:
        """Normaliza el scoreboard cacheado de la fecha seleccionada."""

        payload = self._connector(league).scoreboard(_valid_date(date))
        fixtures = scoreboard_fixtures(payload, _valid_league(league))
        scores = _score_index(payload)
        return [
            {**asdict(row), **scores.get(row.match_id, {})}
            for row in fixtures
        ]

    def plays(
        self, league: str, match_id: str, competition_id: str,
        scope: str = "key",
    ) -> dict[str, Any]:
        """Obtiene todas las páginas y filtra sólo para presentación."""

        connector = self._connector(league)
        payload = connector.plays(
            _valid_id(match_id), _valid_id(competition_id))
        rows = [_play(row) for row in payload.get("items", [])]
        normalized = [row for row in rows if row is not None]
        selected = normalized if scope == "all" else [
            row for row in normalized if row["type"] in IMPORTANT_TYPES]
        return {
            "plays": selected, "count": len(selected),
            "raw_count": len(normalized), "scope": scope,
            "source_page_count": int(payload.get("_sourcePageCount") or 0),
        }

    def statistics(
        self, league: str, match_id: str, competition_id: str,
    ) -> dict[str, Any]:
        """Combina conteos temporales de plays con boxscore total."""

        connector = self._connector(league)
        plays = connector.plays(
            _valid_id(match_id), _valid_id(competition_id))
        summary = connector.summary(_valid_id(match_id))
        teams = _summary_teams(summary)
        periods = _period_statistics(plays.get("items", []), teams)
        score = _summary_score(summary)
        return {
            "teams": teams, "periods": periods,
            "boxscore": _boxscore(summary), "reconciled": _reconciled(periods),
            "score": score,
            "score_reconciled": _score_reconciled(periods, score),
        }

    def teams(self, league: str, query: str = "") -> list[dict[str, Any]]:
        """Lista equipos ESPN y aplica búsqueda textual normalizada."""

        connector = self._connector(league)
        request = connector.resource_request("teams")
        rows = [{**row, "league_slug": league} for row in _teams(connector.fetch_request(request))]
        needle = _normal(query)
        if needle:
            rows = [
                row for row in rows
                if needle in _normal(" ".join(str(row.get(key, "")) for key in ("name", "short_name", "abbreviation", "location")))
            ]
        return rows

    def roster(self, league: str, team_id: str) -> dict[str, Any]:
        """Normaliza plantilla y estadísticas acumuladas disponibles."""

        connector = self._connector(league)
        request = connector.resource_request(
            "roster", team_id=_valid_id(team_id))
        payload = connector.fetch_request(request)
        return {
            "team": _team_identity(payload.get("team")),
            "season": payload.get("season", {}),
            "players": [_roster_player(row) for row in payload.get(
                "athletes", []) if isinstance(row, dict)],
        }

    def player(
        self, league: str, team_id: str, player_id: str,
    ) -> dict[str, Any]:
        """Combina perfil Core y estadísticas presentes en roster."""

        connector = self._connector(league)
        profile = connector.fetch_request(connector.resource_request(
            "athlete", athlete_id=_valid_id(player_id)))
        roster = self.roster(league, team_id)
        player = next((
            row for row in roster["players"]
            if str(row.get("id")) == str(player_id)
        ), {})
        return {**_player_profile(profile), "statistics": player.get(
            "statistics", []), "team": roster.get("team", {})}

    @staticmethod
    def _connector(league: str) -> EspnProspectiveConnector:
        """Construye conector con caché compartida por liga."""

        return EspnProspectiveConnector(EspnConnectorConfig(
            league=_valid_league(league)))


def explorer_dates(mode: str, days: int = 8) -> list[dict[str, str]]:
    """Crea botones de calendario relativos al día UTC actual."""

    today = datetime.now(timezone.utc).date()
    direction = 1 if mode == "future" else -1
    values = [today + timedelta(days=direction * index) for index in range(days)]
    return [{
        "date": value.strftime("%Y%m%d"),
        "label": value.strftime("%d/%m"),
    } for value in values]


def _valid_league(value: str) -> str:
    """Valida slug ESPN sin permitir rutas."""

    if not re.fullmatch(r"[A-Za-z0-9._]+", value):
        raise ValueError("invalid_league")
    return value


def _valid_id(value: str) -> str:
    """Valida identificadores numéricos ESPN."""

    if not str(value).isdigit():
        raise ValueError("invalid_espn_id")
    return str(value)


def _valid_date(value: str) -> str:
    """Valida fecha compacta de scoreboard."""

    try:
        datetime.strptime(value, "%Y%m%d")
    except ValueError as error:
        raise ValueError("invalid_date") from error
    return value


def _normal(value: str) -> str:
    """Normaliza texto para búsqueda tolerante a mayúsculas."""

    decomposed = unicodedata.normalize("NFKD", value)
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(plain.casefold().strip().split())


def _score_index(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Indexa marcador y estado sin alterar orientación."""

    output: dict[int, dict[str, Any]] = {}
    for event in payload.get("events", []):
        if not isinstance(event, dict) or not str(event.get("id", "")).isdigit():
            continue
        competition = (event.get("competitions") or [{}])[0]
        competitors = competition.get("competitors", [])
        scores = {
            row.get("homeAway"): row.get("score")
            for row in competitors if isinstance(row, dict)
        }
        output[int(event["id"])] = {
            "home_score": scores.get("home"), "away_score": scores.get("away"),
            "status_detail": ((event.get("status") or {}).get("type") or {}).get(
                "detail", ""),
        }
    return output


def _play(value: Any) -> dict[str, Any] | None:
    """Normaliza un play sin inventar texto o equipo."""

    if not isinstance(value, dict):
        return None
    play_type = value.get("type") if isinstance(value.get("type"), dict) else {}
    clock = value.get("clock") if isinstance(value.get("clock"), dict) else {}
    period = value.get("period") if isinstance(value.get("period"), dict) else {}
    return {
        "id": str(value.get("id", "")),
        "type": _play_type(value),
        "label": str(play_type.get("text") or "Evento"),
        "clock": str(clock.get("displayValue") or ""),
        "period": int(period.get("number") or 0),
        "team_id": _ref_id(value.get("team")),
        "text": str(value.get("text") or value.get("shortText") or ""),
    }


def _play_type(value: dict[str, Any]) -> str:
    """Convierte la taxonomía compartida al contrato visual existente."""

    canonical, raw = classify_play(value)
    aliases = {
        "penalty_scored": "goal", "yellow": "yellow-card",
        "red": "red-card", "shot_on_target": "shot-on-target",
        "shot_off_target": "shot-off-target",
        "shot_blocked": "shot-blocked", "corner": "corner-awarded",
    }
    if canonical in aliases:
        return aliases[canonical]
    if canonical not in {"unclassified", "auxiliary"}:
        return canonical
    return str(raw or "unknown").replace("_", "-")


def _ref_id(value: Any) -> str | None:
    """Extrae team ID desde objeto o referencia Core."""

    if not isinstance(value, dict):
        return None
    if str(value.get("id", "")).isdigit():
        return str(value["id"])
    reference = value.get("$ref")
    if not isinstance(reference, str):
        return None
    segments = [item for item in urlparse(reference).path.split("/") if item]
    return segments[-1] if segments and segments[-1].isdigit() else None


def _summary_teams(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Obtiene orientación, ID y nombre desde header."""

    header = summary.get("header") or {}
    competition = (header.get("competitions") or [{}])[0]
    output: dict[str, dict[str, Any]] = {}
    for row in competition.get("competitors", []):
        team = row.get("team") if isinstance(row, dict) else None
        side = row.get("homeAway") if isinstance(row, dict) else None
        if isinstance(team, dict) and side in {"home", "away"}:
            output[str(side)] = _team_identity(team)
    return output


def _summary_score(summary: dict[str, Any]) -> dict[str, int | None]:
    """Extrae el marcador oficial del header ESPN por orientación."""

    competition = ((summary.get("header") or {}).get(
        "competitions") or [{}])[0]
    values: dict[str, int | None] = {"home": None, "away": None}
    for row in competition.get("competitors", []):
        side = row.get("homeAway") if isinstance(row, dict) else None
        if side not in values:
            continue
        try:
            values[str(side)] = int(float(row.get("score")))
        except (TypeError, ValueError):
            values[str(side)] = None
    return values


def _score_reconciled(
    periods: dict[str, dict[str, dict[str, int]]],
    score: dict[str, int | None],
) -> bool:
    """Compara goles derivados del PBP contra el marcador oficial."""

    return all(
        score.get(side) is not None
        and periods.get(side, {}).get("total", {}).get("goals") == score[side]
        for side in ("home", "away")
    )


def _team_identity(value: Any) -> dict[str, Any]:
    """Reduce identidad de equipo a campos de presentación."""

    team = value if isinstance(value, dict) else {}
    return {
        "id": str(team.get("id") or ""),
        "name": str(team.get("displayName") or team.get("name") or ""),
        "abbreviation": str(team.get("abbreviation") or ""),
        "short_name": str(team.get("shortDisplayName") or ""),
        "location": str(team.get("location") or ""),
        "logo": _logo(team),
    }


def _logo(team: dict[str, Any]) -> str | None:
    """Obtiene logo directo o el primero de la lista."""

    if isinstance(team.get("logo"), str):
        return str(team["logo"])
    logos = team.get("logos")
    if isinstance(logos, list) and logos and isinstance(logos[0], dict):
        return str(logos[0].get("href") or "") or None
    return None


def _period_statistics(
    plays: list[Any], teams: dict[str, dict[str, Any]],
) -> dict[str, dict[str, dict[str, int]]]:
    """Cuenta eventos por equipo, mitad y total."""

    team_ids = {
        side: str(team.get("id")) for side, team in teams.items()
    }
    counts = {
        side: {"first_half": Counter(), "second_half": Counter()}
        for side in ("home", "away")
    }
    for raw in plays:
        row = _play(raw)
        side = _side(row.get("team_id") if row else None, team_ids)
        if row is None or side is None or row["period"] not in {1, 2}:
            continue
        period = "first_half" if row["period"] == 1 else "second_half"
        _count_play(counts[side][period], row["type"])
    return {side: _with_total(periods) for side, periods in counts.items()}


def _side(team_id: str | None, team_ids: dict[str, str]) -> str | None:
    """Resuelve orientación exacta por team ID."""

    return next((
        side for side, identifier in team_ids.items()
        if team_id is not None and identifier == str(team_id)
    ), None)


def _count_play(counter: Counter[str], play_type: str) -> None:
    """Incrementa las métricas cuyo target contiene el tipo."""

    for metric, types in COUNT_TYPES.items():
        if play_type in types:
            counter[metric] += 1


def _with_total(
    periods: dict[str, Counter[str]],
) -> dict[str, dict[str, int]]:
    """Añade total como suma exacta de ambas mitades."""

    first, second = periods["first_half"], periods["second_half"]
    keys = sorted(set(COUNT_TYPES) | set(first) | set(second))
    return {
        "first_half": {key: int(first[key]) for key in keys},
        "second_half": {key: int(second[key]) for key in keys},
        "total": {key: int(first[key] + second[key]) for key in keys},
    }


def _reconciled(
    periods: dict[str, dict[str, dict[str, int]]],
) -> bool:
    """Comprueba que toda métrica total sea suma de 1T y 2T."""

    for team in periods.values():
        for metric, total in team["total"].items():
            if total != team["first_half"][metric] + team["second_half"][metric]:
                return False
    return True


def _boxscore(summary: dict[str, Any]) -> list[dict[str, Any]]:
    """Reduce boxscore total sin mezclarlo con particiones temporales."""

    boxscore = summary.get("boxscore") or {}
    output: list[dict[str, Any]] = []
    for row in boxscore.get("teams", []):
        if not isinstance(row, dict):
            continue
        statistics = {
            str(item.get("name")): str(item.get("displayValue", ""))
            for item in row.get("statistics", []) if isinstance(item, dict)
        }
        output.append({
            "side": str(row.get("homeAway") or ""),
            "team": _team_identity(row.get("team")),
            "statistics": statistics,
        })
    return output


def _teams(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae equipos Site API manteniendo IDs ESPN."""

    sports = payload.get("sports") or []
    leagues = sports[0].get("leagues", []) if sports else []
    rows = leagues[0].get("teams", []) if leagues else []
    teams = [_team_identity(row.get("team")) for row in rows if isinstance(row, dict)]
    return sorted(teams, key=lambda row: _normal(str(row["name"])))


def _roster_player(row: dict[str, Any]) -> dict[str, Any]:
    """Reduce atleta de roster con sus estadísticas acumuladas."""

    position = row.get("position") if isinstance(row.get("position"), dict) else {}
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("displayName") or row.get("fullName") or ""),
        "short_name": str(row.get("shortName") or ""),
        "jersey": str(row.get("jersey") or ""),
        "position": str(position.get("displayName") or position.get("name") or ""),
        "age": row.get("age"),
        "headshot": _headshot(row),
        "statistics": _player_statistics(row.get("statistics")),
    }


def _player_statistics(value: Any) -> list[dict[str, str]]:
    """Aplana categorías estadísticas disponibles en roster."""

    splits = value.get("splits") if isinstance(value, dict) else {}
    categories = splits.get("categories", []) if isinstance(splits, dict) else []
    output: list[dict[str, str]] = []
    for category in categories:
        if not isinstance(category, dict):
            continue
        for stat in category.get("stats", []):
            if isinstance(stat, dict):
                output.append({
                    "name": str(stat.get("name") or ""),
                    "label": str(stat.get("displayName") or ""),
                    "value": str(stat.get("displayValue") or "0"),
                })
    return output


def _player_profile(row: dict[str, Any]) -> dict[str, Any]:
    """Reduce perfil Core a información personal estable."""

    position = row.get("position") if isinstance(row.get("position"), dict) else {}
    status = row.get("status") if isinstance(row.get("status"), dict) else {}
    birthplace = row.get("birthPlace") if isinstance(row.get("birthPlace"), dict) else {}
    return {
        "id": str(row.get("id") or ""),
        "name": str(row.get("displayName") or row.get("fullName") or ""),
        "age": row.get("age"),
        "date_of_birth": str(row.get("dateOfBirth") or ""),
        "birth_place": ", ".join(str(value) for value in birthplace.values() if value),
        "citizenship": str(row.get("citizenship") or ""),
        "height": str(row.get("displayHeight") or ""),
        "weight": str(row.get("displayWeight") or ""),
        "position": str(position.get("displayName") or position.get("name") or ""),
        "active": bool(row.get("active", status.get("id") == "1")),
        "headshot": _headshot(row),
    }


def _headshot(row: dict[str, Any]) -> str | None:
    """Obtiene retrato publicado sin construir URLs del proveedor."""

    value = row.get("headshot")
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        return str(value.get("href") or value.get("url") or "") or None
    return None


# Version: 1.0.0
# Created: 2026-07-29
