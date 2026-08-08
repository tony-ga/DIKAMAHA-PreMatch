"""Flujo local de predicción pre-match para partidos multi-liga.

La primera vertical usa únicamente ventanas históricas anteriores al kickoff y
un prior Poisson estructural compatible con Dixon-Coles. Markov permanece fuera
de la salida oficial hasta superar su gate OOS.

Requirements:
    - numpy

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import gzip
import json
import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any

from src.btts_probability import (
    ArtifactBttsProbabilityProvider,
    BttsProbabilityPort,
)
from src.official_goal_chain import DixonColesKalmanGoalModel, GoalModelPort
from src.prematch_snapshot_registry import SnapshotRegistryError, resolve_active_snapshot
from src.team_count_market_runtime import (
    ArtifactTeamCountMarketProvider,
    TeamCountMarketProvider,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WINDOWS = ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json"
STATES = ("available", "history_insufficient", "unsupported_league")
LOGGER = logging.getLogger(__name__)
_SHARED_GOAL_MODEL = DixonColesKalmanGoalModel()
_SHARED_BTTS_PROVIDER = ArtifactBttsProbabilityProvider()


class PrematchUnavailableError(ValueError):
    """Indica que el snapshot no permite una predicción segura."""


@dataclass(frozen=True, slots=True)
class UpcomingMatchInput:
    """Solicitud normalizada de un partido próximo."""

    league_slug: str
    home_team_id: int
    away_team_id: int
    kickoff_ts: str
    match_id: int | None = None


@dataclass(frozen=True, slots=True)
class UpcomingPrediction:
    """Salida de mercados estructurales con auditoría causal."""

    status: str
    match_id: int | None
    league_slug: str
    home_team_id: int
    away_team_id: int
    kickoff_ts: str
    lambda_home: float
    lambda_away: float
    probability_home: float
    probability_draw: float
    probability_away: float
    probability_over_2_5: float
    probability_btts: float
    expected_home_goals: float
    expected_away_goals: float
    model: str
    provenance: dict[str, Any]
    audit: dict[str, Any]
    experimental_team_markets: dict[str, Any] | None = None


def _parse_ts(value: str) -> datetime:
    """Normaliza un timestamp ISO y exige zona horaria."""

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise PrematchUnavailableError("kickoff_must_include_timezone")
    return parsed.astimezone(timezone.utc)


@lru_cache(maxsize=4)
def _load_windows(path: str) -> tuple[dict[str, Any], ...]:
    """Carga y cachea el snapshot de ventanas sin mutarlo."""

    source = Path(path)
    if not source.exists():
        raise PrematchUnavailableError("historical_snapshot_missing")
    if source.suffix == ".gz":
        with gzip.open(source, "rt", encoding="utf-8") as stream:
            payload = json.load(stream)
    else:
        payload = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(payload, list) or not payload:
        raise PrematchUnavailableError("historical_snapshot_empty")
    return tuple(row for row in payload if isinstance(row, dict))


def _matches(rows: tuple[dict[str, Any], ...]) -> list[dict[str, Any]]:
    """Agrega ventanas a un registro por partido y localía."""

    grouped: dict[int, dict[str, Any]] = {}
    for row in rows:
        match_id = int(row["match_id"])
        item = grouped.setdefault(match_id, {"match_id": match_id, "match_date": str(row["match_date"]), "league_slug": str(row["league_slug"]), "home_team_id": None, "away_team_id": None, "home_goals": 0.0, "away_goals": 0.0})
        side = "home" if bool(row["is_home"]) else "away"
        item[f"{side}_team_id"] = int(row["team_id"])
        item[f"{side}_goals"] += float(row.get("goals", 0.0) or 0.0)
    return [row for row in grouped.values() if row["home_team_id"] and row["away_team_id"]]


@lru_cache(maxsize=4)
def _load_matches(path: str) -> tuple[dict[str, Any], ...]:
    """Carga una vez por versión el agregado histórico por partido."""

    return tuple(_matches(_load_windows(path)))


def _league_matches(matches: list[dict[str, Any]], request: UpcomingMatchInput) -> list[dict[str, Any]]:
    """Selecciona partidos de la misma liga y anteriores al kickoff."""

    cutoff = _parse_ts(request.kickoff_ts)
    return [row for row in matches if str(row["league_slug"]) == request.league_slug and int(row["match_id"]) != request.match_id and _parse_ts(str(row["match_date"])) < cutoff]


def _team_stats(matches: list[dict[str, Any]]) -> dict[int, dict[str, float]]:
    """Calcula goles a favor, contra y apariciones por equipo."""

    stats: dict[int, dict[str, float]] = defaultdict(lambda: {"for": 0.0, "against": 0.0, "appearances": 0.0})
    for row in matches:
        home, away = int(row["home_team_id"]), int(row["away_team_id"])
        stats[home]["for"] += float(row["home_goals"]); stats[home]["against"] += float(row["away_goals"]); stats[home]["appearances"] += 1.0
        stats[away]["for"] += float(row["away_goals"]); stats[away]["against"] += float(row["home_goals"]); stats[away]["appearances"] += 1.0
    return dict(stats)


def _lambdas(matches: list[dict[str, Any]], request: UpcomingMatchInput) -> tuple[float, float, dict[str, Any]]:
    """Calcula tasas estructurales con shrinkage por liga y equipo."""

    if len(matches) < 8:
        raise PrematchUnavailableError("league_history_below_minimum")
    total = max(len(matches), 1)
    league_rate = sum(row["home_goals"] + row["away_goals"] for row in matches) / (2.0 * total)
    home_rate = sum(row["home_goals"] for row in matches) / total
    away_rate = sum(row["away_goals"] for row in matches) / total
    stats = _team_stats(matches); prior = 1.0
    params = {team: {name: math.log((values[name] + prior) / (values["appearances"] * max(league_rate, 1e-3) + prior)) for name in ("for", "against")} for team, values in stats.items()}
    attack_mean = sum(item["for"] for item in params.values()) / max(len(params), 1); defense_mean = sum(item["against"] for item in params.values()) / max(len(params), 1)
    home_param = params.get(request.home_team_id, {"for": 0.0, "against": 0.0}); away_param = params.get(request.away_team_id, {"for": 0.0, "against": 0.0})
    home_advantage = math.log(max(home_rate + prior / total, 1e-6) / max(away_rate + prior / total, 1e-6))
    home_attack = home_param["for"] - attack_mean; away_defense = away_param["against"] - defense_mean
    away_attack = away_param["for"] - attack_mean; home_defense = home_param["against"] - defense_mean
    home = math.exp(math.log(max(league_rate, 1e-3)) + home_advantage + home_attack - away_defense)
    away = math.exp(math.log(max(league_rate, 1e-3)) + away_attack - home_defense)
    latest = max(_parse_ts(str(row["match_date"])) for row in matches)
    age_days = max(0, (_parse_ts(request.kickoff_ts) - latest).days)
    return home, away, {"history_matches": total, "history_cutoff_ts": latest.isoformat(), "history_age_days": age_days, "home_team_history": int(stats.get(request.home_team_id, {}).get("appearances", 0)), "away_team_history": int(stats.get(request.away_team_id, {}).get("appearances", 0))}


def _markets(home: float, away: float, max_goals: int = 12) -> dict[str, float]:
    """Deriva mercados analíticos de dos Poisson independientes."""

    home_probs = [_poisson(value, home) for value in range(max_goals + 1)]
    away_probs = [_poisson(value, away) for value in range(max_goals + 1)]
    matrix = {(h, a): home_probs[h] * away_probs[a] for h in range(max_goals + 1) for a in range(max_goals + 1)}
    total = sum(matrix.values())
    return {"home": sum(value for (h, a), value in matrix.items() if h > a) / total, "draw": sum(value for (h, a), value in matrix.items() if h == a) / total, "away": sum(value for (h, a), value in matrix.items() if h < a) / total, "over_2_5": sum(value for (h, a), value in matrix.items() if h + a > 2) / total, "btts": sum(value for (h, a), value in matrix.items() if h > 0 and a > 0) / total}


def _poisson(goals: int, rate: float) -> float:
    """Calcula la masa puntual Poisson sin scipy."""

    return math.exp(-rate) * rate**goals / math.factorial(goals)


def _source_label(path: Path) -> str:
    """Representa la fuente sin fallar si está fuera del workspace."""

    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


class UniversalPrematchEngine:
    """Resuelve predicciones estructurales desde el snapshot local."""

    def __init__(
        self, windows_path: Path | None = None,
        team_market_provider: TeamCountMarketProvider | None = None,
        team_markets_enabled: bool = True,
        goal_model: GoalModelPort | None = None,
        official_goal_chain_enabled: bool = True,
        btts_provider: BttsProbabilityPort | None = None,
    ) -> None:
        """Configura el proveedor histórico versionado sin realizar red."""

        self._goal_model = goal_model or _SHARED_GOAL_MODEL
        self._btts_provider = btts_provider or _SHARED_BTTS_PROVIDER
        self._official_goal_chain_enabled = official_goal_chain_enabled
        self._team_market_provider = (
            team_market_provider or ArtifactTeamCountMarketProvider()
            if team_markets_enabled else None)
        if windows_path is not None:
            self._windows_path = windows_path
            return
        try:
            self._windows_path = resolve_active_snapshot()
        except SnapshotRegistryError as error:
            raise PrematchUnavailableError("active_snapshot_unavailable") from error

    def predict(self, request: UpcomingMatchInput) -> UpcomingPrediction:
        """Genera una predicción usando sólo historia anterior al kickoff."""

        self._validate(request)
        matches = _league_matches(_load_matches(str(self._windows_path)), request)
        home, away, markets, chain_provenance, chain_audit = (
            self._goal_output(matches, request))
        _, _, coverage = _lambdas(matches, request)
        warning = coverage["history_age_days"] > 45
        provenance = {**chain_provenance, "source": _source_label(self._windows_path), "snapshot_id": self._windows_path.parent.name, "snapshot_versioned": self._windows_path.parent.parent.name == "prematch_snapshots"}
        audit = {**coverage, "history_freshness_warning": warning,
                 **chain_audit}
        shadow = self._team_markets(request)
        return UpcomingPrediction(
            "available", request.match_id, request.league_slug,
            request.home_team_id, request.away_team_id, request.kickoff_ts,
            home, away, markets["home"], markets["draw"], markets["away"],
            markets["over_2_5"], markets["btts"], home, away,
            str(chain_provenance["router_model"]), provenance, audit, shadow)

    def _goal_output(
        self, matches: list[dict[str, Any]], request: UpcomingMatchInput,
    ) -> tuple[float, float, dict[str, float], dict[str, Any], dict[str, Any]]:
        """Selecciona la cadena candidata o el baseline oficial vigente."""

        if self._official_goal_chain_enabled:
            try:
                return self._selective_goal_chain(matches, request)
            except (KeyError, ValueError, RuntimeError, FloatingPointError) as error:
                LOGGER.warning("official_goal_chain_fallback: %s", error)
                return self._baseline_goal_output(
                    matches, request, str(error))
        return self._baseline_goal_output(matches, request)

    def _selective_goal_chain(
        self, matches: list[dict[str, Any]], request: UpcomingMatchInput,
    ) -> tuple[float, float, dict[str, float], dict[str, Any], dict[str, Any]]:
        """Promueve 1X2/over y conserva BTTS en su baseline estable."""

        result = self._goal_model.predict(
            matches, request.home_team_id, request.away_team_id,
            request.kickoff_ts)
        base_home, base_away, _ = _lambdas(matches, request)
        baseline = _markets(base_home, base_away)
        markets = _result_markets(result)
        btts, btts_meta = self._btts_output(matches, baseline["btts"])
        markets["btts"] = btts
        provenance = {
            **result.provenance,
            "router_model": "selective_dc_kalman_official",
            "promotion_status": "selective_official",
            "market_models": {
                "1x2": "dixon_coles_kalman",
                "over_2_5": "dixon_coles_kalman",
                "btts": btts_meta["model"]},
            "btts_calibration": btts_meta}
        return result.lambda_home, result.lambda_away, markets, provenance, result.audit

    def _btts_output(
        self, matches: list[dict[str, Any]], fallback: float,
    ) -> tuple[float, dict[str, Any]]:
        """Aplica Fase 106 con fallback seguro al baseline anterior."""

        try:
            return self._btts_provider.predict(matches)
        except (OSError, TypeError, ValueError, FloatingPointError,
                json.JSONDecodeError) as error:
            LOGGER.warning("btts_probability_fallback: %s", error)
            return fallback, {
                "model": "structural_poisson_baseline",
                "version": "fallback", "reason": str(error)}

    @staticmethod
    def _baseline_goal_output(
        matches: list[dict[str, Any]], request: UpcomingMatchInput,
        fallback_reason: str | None = None,
    ) -> tuple[float, float, dict[str, float], dict[str, Any], dict[str, Any]]:
        """Conserva el baseline completo como rollback seguro."""

        home, away, _ = _lambdas(matches, request)
        provenance = {
            "router_model": "structural_poisson_baseline",
            "model_version": "phase_48_structural_baseline_v1",
            "dixon_coles_used": False, "kalman_used": False,
            "markov_used": False, "promotion_status": "baseline_only",
            "fallback_reason": fallback_reason}
        return home, away, _markets(home, away), provenance, {
            "target_match_data_used": False, "cutoff_causal": True}

    def _team_markets(
        self, request: UpcomingMatchInput,
    ) -> dict[str, Any] | None:
        """Calcula el sidecar después del pronóstico oficial."""

        if self._team_market_provider is None:
            return None
        rows = _load_windows(str(self._windows_path))
        return self._team_market_provider.predict(
            rows, request, self._windows_path)

    @staticmethod
    def _validate(request: UpcomingMatchInput) -> None:
        """Valida identidad, localía, liga y futuro del partido."""

        if not request.league_slug or not request.league_slug.replace(".", "").replace("_", "").isalnum():
            raise PrematchUnavailableError("invalid_league_slug")
        if request.home_team_id <= 0 or request.away_team_id <= 0 or request.home_team_id == request.away_team_id:
            raise PrematchUnavailableError("invalid_team_identity")
        if _parse_ts(request.kickoff_ts) <= datetime.now(timezone.utc):
            raise PrematchUnavailableError("kickoff_is_not_future")


def _result_markets(result: Any) -> dict[str, float]:
    """Adapta el contrato tipado de la cadena a los campos universales."""

    return {
        "home": result.probability_home,
        "draw": result.probability_draw,
        "away": result.probability_away,
        "over_2_5": result.probability_over_2_5,
        "btts": result.probability_btts,
    }
