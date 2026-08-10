"""Runtime read-only para descubrimiento y predicción de fixtures live.

Markov Live es siempre el baseline. Hawkes se aplica únicamente como residual
selectivo de acuerdo con la política congelada de Fase 114.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from src.dikamaha_inference import DikamahaInferenceEngine, LiveSnapshotInput
from src.hawkes_live_v2 import HawkesLiveConfig
from src.live_probability_engine_v1 import (
    LiveProbabilityEngineV1,
    MonteCarloDiagnosticRunner,
)
from src.markov_live_v1 import MarkovLiveInput
from src.espn_fixture_resolver import scoreboard_fixtures
from src.espn_live_follower import (
    EspnLiveMatchFollower,
    InMemoryLiveRawStore,
    live_inference_payload,
)
from src.espn_prospective_connector import (
    EspnConnectorConfig,
    EspnConnectorError,
    EspnProspectiveConnector,
    EspnResourceUnavailable,
)
from src.universal_prematch import UniversalPrematchEngine, UpcomingMatchInput

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_HAWKES_POLICY = (
    ROOT / "artifacts" / "phase_114_live_markov_hawkes_v1"
    / "hawkes_league_policy.json"
)
LIVE_STATES = frozenset({"in", "live"})
ConnectorFactory = Callable[[str], EspnProspectiveConnector]


def _valid_league(value: str) -> str:
    candidate = str(value).strip()
    if not re.fullmatch(r"[A-Za-z0-9._]+", candidate):
        raise ValueError("invalid_live_league")
    return candidate


def _valid_date(value: str | None) -> str:
    candidate = value or datetime.now(timezone.utc).strftime("%Y%m%d")
    try:
        datetime.strptime(candidate, "%Y%m%d")
    except ValueError as error:
        raise ValueError("invalid_live_date") from error
    return candidate


def _candidate_live_dates(
    value: str | None, *, now: datetime | None = None,
) -> tuple[str, ...]:
    """Devuelve la ventana ESPN relevante sin romper una fecha explícita.

    ESPN agrupa algunos eventos por la fecha local de la competición. Cerca de
    medianoche UTC un partido todavía activo puede permanecer en el scoreboard
    del día anterior, por lo que el catálogo automático inspecciona D-1, D y
    D+1. Una fecha solicitada por el cliente conserva semántica exacta.
    """

    if value is not None:
        return (_valid_date(value),)
    reference = now or datetime.now(timezone.utc)
    if reference.tzinfo is None:
        reference = reference.replace(tzinfo=timezone.utc)
    day = reference.astimezone(timezone.utc).date()
    return tuple(
        (day + timedelta(days=offset)).strftime("%Y%m%d")
        for offset in (-1, 0, 1)
    )


def load_hawkes_league_policy(
    path: Path = DEFAULT_HAWKES_POLICY,
) -> dict[str, Any]:
    """Carga y valida la política seleccionada sin usar confirmación."""

    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        payload.get("version") != "hawkes_live_v2_league_admission_v1"
        or payload.get("selection_split") != "validation_only"
        or payload.get("confirmation_used_for_selection") is not False
        or not isinstance(payload.get("allowed_leagues"), list)
    ):
        raise ValueError("invalid_hawkes_league_policy")
    allowed = sorted({_valid_league(str(value)) for value in payload["allowed_leagues"]})
    rho_goal = float(payload.get("rho_goal"))
    rho_next = float(payload.get("rho_next_event"))
    if not all(math.isfinite(value) and 0.0 <= value <= 1.0 for value in (
        rho_goal, rho_next,
    )):
        raise ValueError("invalid_hawkes_policy_rho")
    return {
        **payload,
        "allowed_leagues": allowed,
        "rho_goal": rho_goal,
        "rho_next_event": rho_next,
    }


def predict_shadow_snapshot(
    engine: DikamahaInferenceEngine,
    snapshot: dict[str, Any],
    prior: dict[str, Any],
    policy: dict[str, Any],
    *,
    probability_engine: LiveProbabilityEngineV1 | None = None,
    monte_carlo: MonteCarloDiagnosticRunner | None = None,
    probability_engine_enabled: bool = True,
    probability_engine_official: bool = True,
    fallback_enabled: bool = True,
) -> dict[str, Any]:
    """Ejecuta capas legadas y compone el motor live oficial."""

    event_id = str(snapshot["provider_event_id"])
    identity = {
        "provider_event_id": event_id,
        "home_team_id": str(snapshot["home_team_id"]),
        "away_team_id": str(snapshot["away_team_id"]),
        "league_slug": str(snapshot["league_slug"]),
    }
    for key, expected in identity.items():
        if prior.get(key) is not None and str(prior[key]) != expected:
            raise ValueError("invalid_frozen_prematch_identity")
    kickoff = datetime.fromisoformat(
        str(snapshot["kickoff_ts"]).replace("Z", "+00:00"))
    cutoff = datetime.fromisoformat(
        str(prior["cutoff_ts"]).replace("Z", "+00:00"))
    if cutoff.tzinfo is None or kickoff.tzinfo is None or cutoff >= kickoff:
        raise ValueError("invalid_frozen_prematch_cutoff")
    admitted = str(snapshot["league_slug"]) in set(policy["allowed_leagues"])
    rho_goal = float(policy["rho_goal"]) if admitted else 0.0
    rho_next = float(policy["rho_next_event"]) if admitted else 0.0
    request = live_inference_payload(
        snapshot,
        lambda_base_home=float(prior["lambda_base_home"]),
        lambda_base_away=float(prior["lambda_base_away"]),
        enable_hawkes=True,
        hawkes_rho_goal=rho_goal,
        hawkes_rho_next_event=rho_next,
        prior_source_hash=str(prior["source_hash"]),
    )
    output = engine.predict_live(LiveSnapshotInput(**request))
    result = {
        "provider_event_id": event_id,
        "status": "live_predicted",
        "snapshot_source_hash": snapshot["source_hash"],
        "prior": prior,
        "experimental_markov_live": output.experimental_markov_live,
        "experimental_hawkes_residual": output.experimental_hawkes_residual,
        "experimental_combined_live": output.experimental_combined_live,
        "hawkes_league_admission": {
            "policy_applied": True,
            "admitted": admitted,
            "rho_goal": rho_goal,
            "rho_next_event": rho_next,
            "fallback_exact_markov_live": bool(
                output.experimental_combined_live
                and output.experimental_combined_live.get(
                    "fallback_exact_markov_live")
            ),
        },
        "audit": asdict(output.audit),
    }
    result.update(compose_official_live_output(
        output,
        LiveSnapshotInput(**request),
        probability_engine=probability_engine,
        monte_carlo=monte_carlo,
        enabled=probability_engine_enabled,
        official=probability_engine_official,
        fallback_enabled=fallback_enabled,
    ))
    return result


def compose_official_live_output(
    output: Any,
    request: LiveSnapshotInput,
    *,
    probability_engine: LiveProbabilityEngineV1 | None = None,
    monte_carlo: MonteCarloDiagnosticRunner | None = None,
    enabled: bool = True,
    official: bool = True,
    fallback_enabled: bool = True,
) -> dict[str, Any]:
    """Promueve el motor compuesto y aplica fallback explícito y reversible."""

    markov_live = output.experimental_markov_live
    if not enabled or markov_live is None:
        return {
            "official_source": output.official_source,
            "official_live_prediction": None,
            "live_probability_engine": {
                "status": "disabled" if not enabled else "snapshot_incomplete",
                "fallback": output.official_source,
            },
        }
    source_hash = request.source_hash or hashlib.sha256(
        json.dumps(
            {
                "match_id": request.match_id,
                "league_slug": request.league_slug,
                "kickoff_ts": request.kickoff_ts,
                "snapshot_ts": request.snapshot_ts,
                "home_team_id": request.home_team_id,
                "away_team_id": request.away_team_id,
                "score_home": request.score_home,
                "score_away": request.score_away,
                "period": request.period,
                "match_clock_seconds": request.match_clock_seconds,
                "events": request.events,
            },
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    live_request = MarkovLiveInput(
        match_id=request.match_id,
        home_team_id=request.home_team_id,
        away_team_id=request.away_team_id,
        kickoff_ts=request.kickoff_ts,
        snapshot_ts=request.snapshot_ts,
        match_clock_seconds=float(request.match_clock_seconds or 0.0),
        period=request.period,
        score_home=int(request.score_home or 0),
        score_away=int(request.score_away or 0),
        lambda_base_home=request.lambda_base_home,
        lambda_base_away=request.lambda_base_away,
        events=tuple(dict(event) for event in request.events),
        league_slug=request.league_slug,
        source_hash=source_hash,
    )
    hawkes = HawkesLiveConfig(
        rho=request.hawkes_rho if request.hawkes_rho is not None else 1.0,
        rho_goal=request.hawkes_rho_goal,
        rho_next_event=request.hawkes_rho_next_event,
    )
    try:
        composed = (probability_engine or LiveProbabilityEngineV1()).predict(
            live_request, markov_live, hawkes_config=hawkes,
        )
    except (TypeError, ValueError, OverflowError, FloatingPointError) as exc:
        if not fallback_enabled:
            raise
        return _official_markov_fallback(output, request, type(exc).__name__)
    if not official:
        composed["status"] = "candidate_not_official"
        composed["official_source"] = output.official_source
        composed["official_live_prediction"]["status"] = "candidate"
    diagnostic = {
        "status": "disabled", "simulations": 0, "blocking": False,
    }
    if monte_carlo is not None:
        diagnostic = monte_carlo.submit(
            request.match_id,
            source_hash,
            composed["official_live_prediction"],
        )
    composed["live_probability_engine"]["monte_carlo_diagnostic"] = diagnostic
    composed["official_live_prediction"]["confidence"][
        "monte_carlo_status"
    ] = diagnostic["status"]
    return composed


def _official_markov_fallback(
    output: Any, request: LiveSnapshotInput, reason: str,
) -> dict[str, Any]:
    markov = dict(output.experimental_markov_live or {})
    markets = dict(markov.get("markets") or {})
    return {
        "status": "official_fallback",
        "official_source": "markov_live_v1_fallback",
        "official_live_prediction": {
            "status": "official_fallback",
            "model_version": "markov_live_v1_shadow",
            "markets": markets,
            "periods": {},
            "remaining_intensities": {
                "home": float(markov.get("lambda_remaining_home", 0.0)),
                "away": float(markov.get("lambda_remaining_away", 0.0)),
            },
            "next_event": markov.get("next_event") or {},
            "exact_score": [],
            "remaining_goals_distribution": [],
            "goal_horizons": {},
            "confidence": {
                "classification": "fallback",
                "monte_carlo_status": "not_scheduled",
            },
            "updated_at": request.snapshot_ts,
            "fallback": {
                "applied": True,
                "source": "markov_live_v1",
                "reason": reason,
            },
        },
        "live_probability_engine": {
            "status": "fallback_applied",
            "model_version": "live_probability_engine_v1",
            "audit": {"passed": False, "reason": reason},
            "monte_carlo_diagnostic": {
                "status": "not_scheduled", "simulations": 0,
                "blocking": False,
            },
        },
    }


class LivePredictionRuntime:
    """Orquesta ESPN raw-first, prior causal y el motor live oficial."""

    def __init__(
        self,
        prematch_engine: UniversalPrematchEngine,
        inference_engine: DikamahaInferenceEngine,
        *,
        connector_factory: ConnectorFactory | None = None,
        policy_path: Path = DEFAULT_HAWKES_POLICY,
        probability_engine: LiveProbabilityEngineV1 | None = None,
        monte_carlo_runner: MonteCarloDiagnosticRunner | None = None,
        probability_engine_enabled: bool = True,
        probability_engine_official: bool = True,
        monte_carlo_enabled: bool = True,
        monte_carlo_simulations: int = 20_000,
        fallback_enabled: bool = True,
        refresh_seconds: int = 15,
    ) -> None:
        self._prematch = prematch_engine
        self._inference = inference_engine
        self._connector_factory = connector_factory or self._connector
        self._policy = load_hawkes_league_policy(policy_path)
        self._probability_engine = probability_engine or LiveProbabilityEngineV1()
        self._monte_carlo = monte_carlo_runner or (
            MonteCarloDiagnosticRunner(monte_carlo_simulations)
            if monte_carlo_enabled else None
        )
        self._probability_engine_enabled = probability_engine_enabled
        self._probability_engine_official = probability_engine_official
        self._fallback_enabled = fallback_enabled
        self._refresh_seconds = max(5, min(int(refresh_seconds), 60))

    @staticmethod
    def _connector(league: str) -> EspnProspectiveConnector:
        return EspnProspectiveConnector(EspnConnectorConfig(league=league))

    @property
    def policy(self) -> dict[str, Any]:
        """Expone sólo una copia serializable de la política activa."""

        return dict(self._policy)

    def list_active(
        self, leagues: str, limit: int = 12, selected_date: str | None = None,
    ) -> dict[str, Any]:
        """Lista fixtures ESPN cuyo estado actual es live/in."""

        slugs = list(dict.fromkeys(
            _valid_league(value) for value in leagues.split(",") if value.strip()
        ))[:64]
        if not slugs:
            raise ValueError("live_league_required")
        days = _candidate_live_dates(selected_date)
        bounded = min(max(int(limit), 1), 20)
        targets = [(slug, day) for slug in slugs for day in days]
        with ThreadPoolExecutor(max_workers=min(12, len(targets))) as pool:
            batches = list(pool.map(
                lambda target: self._league_active(*target), targets,
            ))
        rows = [row for batch, _ in batches for row in batch]
        errors = sum(error for _, error in batches)
        unique = {int(row["match_id"]): row for row in rows}
        fixtures = sorted(
            unique.values(), key=lambda row: (
                str(row.get("kickoff_ts", "")), int(row["match_id"]),
            ),
        )[:bounded]
        return {
            "fixtures": fixtures,
            "count": len(fixtures),
            "date": days[len(days) // 2],
            "dates": list(days),
            "date_count": len(days),
            "league_count": len(slugs),
            "partial_failure_count": errors,
            "status": "live_shadow_catalog",
        }

    def _league_active(
        self, league: str, day: str,
    ) -> tuple[list[dict[str, Any]], int]:
        try:
            payload = self._connector_factory(league).scoreboard(day)
            score = _scoreboard_index(payload)
            rows = [
                {**asdict(fixture), **score.get(fixture.match_id, {})}
                for fixture in scoreboard_fixtures(payload, league)
                if fixture.provider_status in LIVE_STATES
            ]
            return rows, 0
        except (EspnConnectorError, EspnResourceUnavailable, ValueError, OSError):
            return [], 1

    def predict_fixture(
        self, league_slug: str, match_id: int,
        selected_date: str | None = None,
    ) -> dict[str, Any]:
        """Captura un snapshot activo y ejecuta todas las capas live."""

        league = _valid_league(league_slug)
        if int(match_id) <= 0:
            raise ValueError("invalid_live_match_id")
        connector = self._connector_factory(league)
        days = _candidate_live_dates(selected_date)
        fixture_day = self._find_active_fixture_day(
            connector, league, int(match_id), days,
        )
        if fixture_day is None:
            raise ValueError("live_fixture_not_active")
        store = InMemoryLiveRawStore()
        snapshots = EspnLiveMatchFollower(connector, store).poll_once(fixture_day)
        snapshot = next((
            row for row in snapshots
            if str(row.get("provider_event_id")) == str(match_id)
        ), None)
        if snapshot is None:
            raise ValueError("live_fixture_not_active")
        prior = self._prematch.reconstruct_live_prior(UpcomingMatchInput(
            league_slug=league,
            home_team_id=int(snapshot["home_team_id"]),
            away_team_id=int(snapshot["away_team_id"]),
            kickoff_ts=str(snapshot["kickoff_ts"]),
            match_id=int(match_id),
        ))
        result = predict_shadow_snapshot(
            self._inference, snapshot, prior, self._policy,
            probability_engine=self._probability_engine,
            monte_carlo=self._monte_carlo,
            probability_engine_enabled=self._probability_engine_enabled,
            probability_engine_official=self._probability_engine_official,
            fallback_enabled=self._fallback_enabled,
        )
        result["fixture"] = {
            "league_slug": league,
            "match_id": int(match_id),
            "competition_id": str(snapshot["competition_id"]),
            "home_team_id": int(snapshot["home_team_id"]),
            "away_team_id": int(snapshot["away_team_id"]),
            "home_team_name": str(snapshot.get("home_team_name") or snapshot["home_team_id"]),
            "away_team_name": str(snapshot.get("away_team_name") or snapshot["away_team_id"]),
            "home_team_logo": snapshot.get("home_team_logo"),
            "away_team_logo": snapshot.get("away_team_logo"),
            "kickoff_ts": str(snapshot["kickoff_ts"]),
            "provider_status": str(snapshot["provider_status"]),
            "provider_status_detail": str(snapshot.get("provider_status_detail") or ""),
            "source_fetched_at": str(snapshot["source_fetched_at"]),
            "period": int(snapshot["period"]),
            "match_clock_seconds": float(snapshot["match_clock_seconds"]),
            "score_home": int(snapshot["score_home"]),
            "score_away": int(snapshot["score_away"]),
        }
        result["raw_capture"] = {
            "mode": "ephemeral_raw_first_readonly",
            "receipt_count": len(store.rows),
            "source_hash": str(snapshot["source_hash"]),
        }
        result.update(_observed_live_presentation(snapshot))
        result["automatic_refresh_recommended_seconds"] = (
            self._refresh_seconds
        )
        return result

    def officialize(
        self, output: Any, request: LiveSnapshotInput,
    ) -> dict[str, Any]:
        """Aplica el mismo contrato oficial a snapshots HTTP ya normalizados."""

        return compose_official_live_output(
            output,
            request,
            probability_engine=self._probability_engine,
            monte_carlo=self._monte_carlo,
            enabled=self._probability_engine_enabled,
            official=self._probability_engine_official,
            fallback_enabled=self._fallback_enabled,
        )

    def close(self) -> None:
        """Cierra exclusivamente el executor Monte Carlo propio del runtime."""

        if self._monte_carlo is not None:
            self._monte_carlo.shutdown()

    @staticmethod
    def _find_active_fixture_day(
        connector: EspnProspectiveConnector,
        league: str,
        match_id: int,
        days: tuple[str, ...],
    ) -> str | None:
        """Localiza el scoreboard del fixture antes de capturar sus recursos."""

        for day in days:
            try:
                fixtures = scoreboard_fixtures(connector.scoreboard(day), league)
            except (
                EspnConnectorError, EspnResourceUnavailable, ValueError, OSError,
            ):
                continue
            if any(
                fixture.match_id == match_id
                and fixture.provider_status in LIVE_STATES
                for fixture in fixtures
            ):
                return day
        return None


_PRESENTATION_EVENTS = frozenset({
    "goal", "yellow", "red", "substitution", "shot_on_target",
    "shot_off_target", "shot_blocked", "corner", "foul",
    "penalty_awarded", "penalty_scored",
})
_PRESENTATION_AUXILIARY = frozenset({
    "save", "offside", "penalty_missed", "penalty___missed",
    "penalty_saved", "penalty___saved", "shot_hit_woodwork",
    "var_red_card_upgrade", "var___red_card_upgrade",
    "var_referee_decision_cancelled", "var___referee_decision_cancelled",
    "var_referee_decision_confirmed", "var___referee_decision_confirmed",
})
_PRESSURE_WEIGHTS = {
    "goal": 25.0,
    "penalty_scored": 25.0,
    "shot_on_target": 8.0,
    "shot_off_target": 4.0,
    "corner": 3.0,
    "foul": 1.0,
}
_PRESSURE_WINDOW_MINUTES = 5
_REGULATION_MINUTES = 90


def _match_dynamics(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Construye una curva causal de presión sólo para presentación."""

    home_id = int(snapshot["home_team_id"])
    away_id = int(snapshot["away_team_id"])
    home_name = str(snapshot.get("home_team_name") or home_id)
    away_name = str(snapshot.get("away_team_name") or away_id)
    raw = [0.0] * _REGULATION_MINUTES
    goals: list[dict[str, Any]] = []
    for event in snapshot.get("events", []):
        if not isinstance(event, dict) or bool(event.get("annulled")):
            continue
        team_id = _optional_int(event.get("team_id"))
        side = "home" if team_id == home_id else "away" if team_id == away_id else None
        canonical = str(event.get("event_type") or "").lower().replace("-", "_")
        weight = _PRESSURE_WEIGHTS.get(canonical)
        if side is None or weight is None:
            continue
        clock_seconds = max(0.0, float(event.get("match_clock_seconds") or 0.0))
        minute = min(_REGULATION_MINUTES, max(1, int(clock_seconds // 60) + 1))
        raw[minute - 1] += weight if side == "home" else -weight
        if canonical in {"goal", "penalty_scored"}:
            goals.append({
                "minute": minute,
                "team_side": side,
                "team_name": home_name if side == "home" else away_name,
            })
    radius = _PRESSURE_WINDOW_MINUTES // 2
    smoothed = [
        sum(raw[max(0, index - radius):min(_REGULATION_MINUTES, index + radius + 1)])
        / _PRESSURE_WINDOW_MINUTES
        for index in range(_REGULATION_MINUTES)
    ]
    current_seconds = max(0.0, float(snapshot.get("match_clock_seconds") or 0.0))
    current_minute = min(
        _REGULATION_MINUTES, max(1, int(current_seconds // 60) + 1),
    )
    return {
        "contract_version": "match_pressure_v1",
        "status": "display_only_heuristic",
        "weights": {
            "goal": 25,
            "shot_on_target": 8,
            "shot_off_target": 4,
            "corner": 3,
            "foul": 1,
        },
        "smoothing": {
            "method": "centered_moving_average",
            "window_minutes": _PRESSURE_WINDOW_MINUTES,
        },
        "neutral_axis": 0,
        "regulation_minutes": _REGULATION_MINUTES,
        "current_minute": current_minute,
        "points": [
            {
                "minute": index + 1,
                "raw_score": round(raw[index], 6),
                "smoothed_score": round(value, 6),
                "home_pressure": round(max(value, 0.0), 6),
                "away_pressure": round(min(value, 0.0), 6),
            }
            for index, value in enumerate(smoothed)
        ],
        "goal_markers": goals,
        "not_model_feature": True,
    }


def _observed_live_presentation(snapshot: dict[str, Any]) -> dict[str, Any]:
    """Agrega el play-by-play sólo para presentación, sin tocar inferencia."""

    home_id = int(snapshot["home_team_id"])
    away_id = int(snapshot["away_team_id"])
    home_name = str(snapshot.get("home_team_name") or home_id)
    away_name = str(snapshot.get("away_team_name") or away_id)
    stats = {
        "home": _empty_observed_stats(int(snapshot["score_home"])),
        "away": _empty_observed_stats(int(snapshot["score_away"])),
        "unassigned": _empty_observed_stats(0),
    }
    actions: list[dict[str, Any]] = []
    for event in snapshot.get("events", []):
        if not isinstance(event, dict) or bool(event.get("annulled")):
            continue
        team_id = _optional_int(event.get("team_id"))
        side = "home" if team_id == home_id else "away" if team_id == away_id else "unassigned"
        canonical = str(event.get("event_type") or "unclassified")
        raw_type = str(event.get("event_type_raw") or "").lower().replace("-", "_")
        _count_observed_event(stats[side], canonical, raw_type)
        if canonical not in _PRESENTATION_EVENTS and raw_type not in _PRESENTATION_AUXILIARY:
            continue
        clock_seconds = float(event.get("match_clock_seconds") or 0.0)
        actions.append({
            "event_id": str(event.get("event_id") or ""),
            "event_type": canonical,
            "event_type_raw": raw_type,
            "team_id": team_id,
            "team_side": side,
            "team_name": home_name if side == "home" else away_name if side == "away" else "Sin asignar",
            "period": int(event.get("period") or 1),
            "match_clock_seconds": clock_seconds,
            "minute": max(1, int(clock_seconds // 60) + 1),
            "text": str(event.get("text") or "")[:500],
        })
    for row in stats.values():
        row["shots"] = (
            row["shots_on_target"] + row["shots_off_target"]
            + row["shots_blocked"]
        )
    actions.sort(
        key=lambda row: (float(row["match_clock_seconds"]), str(row["event_id"])),
        reverse=True,
    )
    return {
        "observed_live_statistics": {
            "source": "provider_play_by_play",
            "as_of": str(snapshot["source_fetched_at"]),
            "home": stats["home"],
            "away": stats["away"],
            "unassigned": stats["unassigned"],
        },
        "recent_actions": actions[:24],
        "match_dynamics": _match_dynamics(snapshot),
        "automatic_refresh_recommended_seconds": 15,
    }


def _empty_observed_stats(goals: int) -> dict[str, int]:
    return {
        "goals": goals,
        "shots": 0,
        "shots_on_target": 0,
        "shots_off_target": 0,
        "shots_blocked": 0,
        "corners": 0,
        "yellow_cards": 0,
        "red_cards": 0,
        "fouls": 0,
        "offsides": 0,
        "saves": 0,
        "substitutions": 0,
        "penalties": 0,
    }


def _count_observed_event(
    row: dict[str, int], canonical: str, raw_type: str,
) -> None:
    mapping = {
        "shot_on_target": "shots_on_target",
        "shot_off_target": "shots_off_target",
        "shot_blocked": "shots_blocked",
        "corner": "corners",
        "yellow": "yellow_cards",
        "red": "red_cards",
        "foul": "fouls",
        "substitution": "substitutions",
        "penalty_awarded": "penalties",
    }
    field = mapping.get(canonical)
    if field is not None:
        row[field] += 1
    if raw_type == "offside":
        row["offsides"] += 1
    elif raw_type == "save":
        row["saves"] += 1


def _optional_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def model_inventory(policy: dict[str, Any]) -> dict[str, Any]:
    """Declara modelos que realmente operan y su clasificación visible."""

    return {
        "status": "operational",
        "models": [
            {
                "name": "Dixon-Coles + Kalman",
                "mode": "official",
                "scope": "pre_match_1x2_and_over_2_5",
            },
            {
                "name": "BTTS causal calibrado",
                "mode": "official",
                "scope": "pre_match_btts",
            },
            {
                "name": "Mercados de conteo + Markov",
                "mode": "shadow",
                "scope": "pre_match_team_markets_by_period",
            },
            {
                "name": "Motor probabilístico Live v1",
                "mode": "official",
                "scope": "live_1x2_periods_goals_exact_score_next_event",
                "components": [
                    "Poisson dinámico", "CTMC", "Hazard Cox",
                    "Elo dinámico", "Hawkes residual",
                    "Monte Carlo diagnóstico",
                ],
            },
            {
                "name": "Markov Live v1",
                "mode": "compatibility_fallback",
                "scope": "live_fallback_and_component_diagnostic",
            },
            {
                "name": "Hawkes Live v2 residual",
                "mode": "official_component",
                "scope": "live_goal_residual_selected_leagues",
            },
            {
                "name": "Markov + Hawkes combinado",
                "mode": "compatibility_alias",
                "scope": "legacy_live_complementary_output",
            },
        ],
        "hawkes_policy": {
            "version": str(policy["version"]),
            "allowed_league_count": len(policy["allowed_leagues"]),
            "rho_goal": float(policy["rho_goal"]),
            "rho_next_event": float(policy["rho_next_event"]),
        },
        "official_router_modified": True,
        "official_live_source": "live_probability_engine_v1",
        "pre_match_router_modified": False,
    }


def _scoreboard_index(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    """Extrae marcador y reloj sin cambiar la orientación ESPN."""

    output: dict[int, dict[str, Any]] = {}
    for event in payload.get("events", []):
        if not isinstance(event, dict) or not str(event.get("id", "")).isdigit():
            continue
        competitions = event.get("competitions")
        competition = competitions[0] if isinstance(competitions, list) and competitions else {}
        if not isinstance(competition, dict):
            continue
        competitors = {
            str(row.get("homeAway")): row for row in competition.get("competitors", [])
            if isinstance(row, dict)
        }
        status = competition.get("status") or event.get("status") or {}
        status_type = status.get("type") if isinstance(status, dict) else {}
        output[int(event["id"])] = {
            "home_score": _score(competitors.get("home")),
            "away_score": _score(competitors.get("away")),
            "period": int(status.get("period") or 1) if isinstance(status, dict) else 1,
            "display_clock": str(status.get("displayClock") or "") if isinstance(status, dict) else "",
            "provider_status_detail": str(
                status_type.get("detail") or status_type.get("description") or ""
            ) if isinstance(status_type, dict) else "",
        }
    return output


def _score(value: Any) -> int | None:
    row = value if isinstance(value, dict) else {}
    raw = row.get("score")
    if isinstance(raw, dict):
        raw = raw.get("value") or raw.get("displayValue")
    try:
        return int(float(str(raw)))
    except (TypeError, ValueError):
        return None


__all__ = [
    "LivePredictionRuntime", "load_hawkes_league_policy", "model_inventory",
    "predict_shadow_snapshot", "_candidate_live_dates",
    "_match_dynamics", "_observed_live_presentation",
]
