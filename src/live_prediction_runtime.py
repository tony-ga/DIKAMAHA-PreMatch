"""Runtime read-only para descubrimiento y predicción de fixtures live.

Markov Live es siempre el baseline. Hawkes se aplica únicamente como residual
selectivo de acuerdo con la política congelada de Fase 114.
"""

from __future__ import annotations

import json
import math
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from src.dikamaha_inference import DikamahaInferenceEngine, LiveSnapshotInput
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
) -> dict[str, Any]:
    """Ejecuta Markov y compone Hawkes sin alterar el router oficial."""

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
    return {
        "provider_event_id": event_id,
        "status": "shadow_predicted",
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


class LivePredictionRuntime:
    """Orquesta ESPN raw-first, prior causal y modelos live shadow."""

    def __init__(
        self,
        prematch_engine: UniversalPrematchEngine,
        inference_engine: DikamahaInferenceEngine,
        *,
        connector_factory: ConnectorFactory | None = None,
        policy_path: Path = DEFAULT_HAWKES_POLICY,
    ) -> None:
        self._prematch = prematch_engine
        self._inference = inference_engine
        self._connector_factory = connector_factory or self._connector
        self._policy = load_hawkes_league_policy(policy_path)

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
        ))[:30]
        if not slugs:
            raise ValueError("live_league_required")
        day = _valid_date(selected_date)
        bounded = min(max(int(limit), 1), 20)
        with ThreadPoolExecutor(max_workers=min(8, len(slugs))) as pool:
            batches = list(pool.map(lambda slug: self._league_active(slug, day), slugs))
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
            "date": day,
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
        store = InMemoryLiveRawStore()
        snapshots = EspnLiveMatchFollower(connector, store).poll_once(
            _valid_date(selected_date))
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
            self._inference, snapshot, prior, self._policy)
        result["fixture"] = {
            "league_slug": league,
            "match_id": int(match_id),
            "competition_id": str(snapshot["competition_id"]),
            "home_team_id": int(snapshot["home_team_id"]),
            "away_team_id": int(snapshot["away_team_id"]),
            "home_team_name": str(snapshot.get("home_team_name") or snapshot["home_team_id"]),
            "away_team_name": str(snapshot.get("away_team_name") or snapshot["away_team_id"]),
            "kickoff_ts": str(snapshot["kickoff_ts"]),
            "provider_status": str(snapshot["provider_status"]),
            "provider_status_detail": str(snapshot.get("provider_status_detail") or ""),
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
        return result


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
                "name": "Markov Live v1",
                "mode": "shadow",
                "scope": "live_universal_baseline",
            },
            {
                "name": "Hawkes Live v2 residual",
                "mode": "shadow",
                "scope": "live_goal_residual_selected_leagues",
            },
            {
                "name": "Markov + Hawkes combinado",
                "mode": "shadow",
                "scope": "live_complementary_output",
            },
        ],
        "hawkes_policy": {
            "version": str(policy["version"]),
            "allowed_league_count": len(policy["allowed_leagues"]),
            "rho_goal": float(policy["rho_goal"]),
            "rho_next_event": float(policy["rho_next_event"]),
        },
        "official_router_modified": False,
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
    "predict_shadow_snapshot",
]
