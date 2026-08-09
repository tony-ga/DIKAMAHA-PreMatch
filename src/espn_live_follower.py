"""Captura ESPN live raw-first y normalización para inferencia shadow.

El módulo ejecuta un solo ciclo por llamada. La cadencia y el ciclo de vida se
mantienen fuera del motor matemático para que tests y replay no usen sleeps.

Version: 1.0.0
Created: 2026-08-07
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

try:
    from src.espn_event_taxonomy import classify_play
    from src.espn_prospective_connector import (
        CORE_BASE,
        SITE_BASE,
        EspnConnectorError,
        EspnFetchResult,
        EspnProspectiveConnector,
        EspnResourceUnavailable,
        scoreboard_references,
    )
    from src.prematch_data_contracts import (
        CaptureKind,
        EntityType,
        RawResponseRepository,
        RawResponseWrite,
    )
except ModuleNotFoundError:  # pragma: no cover
    from espn_event_taxonomy import classify_play
    from espn_prospective_connector import (
        CORE_BASE,
        SITE_BASE,
        EspnConnectorError,
        EspnFetchResult,
        EspnProspectiveConnector,
        EspnResourceUnavailable,
        scoreboard_references,
    )
    from prematch_data_contracts import CaptureKind, EntityType, RawResponseRepository, RawResponseWrite


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _utc(parsed)


@dataclass(frozen=True, slots=True)
class LiveCaptureReceipt:
    """Referencia mínima de una respuesta persistida antes del parseo."""

    response_id: str
    response_hash: str
    fetched_at: str
    resource: str


class LiveRawStore(Protocol):
    """Puerto de persistencia append-only para un ciclo live."""

    def store(
        self,
        *,
        resource: str,
        endpoint: str,
        params: dict[str, Any],
        result: EspnFetchResult,
        league_slug: str,
        event_id: str | None,
        poll_sequence: int,
    ) -> LiveCaptureReceipt:
        """Persiste y devuelve una referencia sin parsear el payload."""


class InMemoryLiveRawStore:
    """Store inmutable por observación para pruebas y dry-runs."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []

    def store(self, **kwargs: Any) -> LiveCaptureReceipt:
        result = kwargs["result"]
        response_hash = _stable_hash(result.payload)
        response_id = str(len(self.rows) + 1)
        self.rows.append({**kwargs, "response_id": response_id, "response_hash": response_hash})
        return LiveCaptureReceipt(response_id, response_hash, result.source_fetched_at.isoformat(), str(kwargs["resource"]))


class FileLiveRawStore:
    """Store local content-addressed; nunca sobrescribe una captura distinta."""

    def __init__(self, root: Path = Path("data/live/raw_v1")) -> None:
        self.root = root

    def store(self, **kwargs: Any) -> LiveCaptureReceipt:
        result: EspnFetchResult = kwargs["result"]
        resource = str(kwargs["resource"])
        event_id = str(kwargs.get("event_id") or "scoreboard")
        league_slug = str(kwargs["league_slug"])
        if not re.fullmatch(r"[A-Za-z0-9._-]+", league_slug) or not re.fullmatch(r"[A-Za-z0-9._-]+", event_id):
            raise ValueError("unsafe_live_raw_path_component")
        response_hash = _stable_hash(result.payload)
        timestamp = result.source_fetched_at.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        folder = self.root / league_slug / event_id
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / f"{timestamp}-{int(kwargs['poll_sequence']):08d}-{resource}-{response_hash}.json"
        record = {
            "resource": resource,
            "endpoint": str(kwargs["endpoint"]),
            "params": dict(kwargs["params"]),
            "http_status": result.http_status,
            "source_fetched_at": result.source_fetched_at.isoformat(),
            "from_cache": result.from_cache,
            "response_hash": response_hash,
            "payload": result.payload,
        }
        encoded = json.dumps(record, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        try:
            with path.open("x", encoding="utf-8") as handle:
                handle.write(encoded)
        except FileExistsError:
            existing = path.read_text(encoding="utf-8")
            if existing != encoded:
                raise ValueError("live_raw_append_only_collision")
        return LiveCaptureReceipt(str(path), response_hash, result.source_fetched_at.isoformat(), resource)


class RepositoryLiveRawStore:
    """Adaptador del ledger live al repositorio `raw_responses` existente."""

    def __init__(self, repository: RawResponseRepository) -> None:
        self.repository = repository

    def store(self, **kwargs: Any) -> LiveCaptureReceipt:
        result: EspnFetchResult = kwargs["result"]
        resource = str(kwargs["resource"])
        event_id = kwargs.get("event_id")
        stored = self.repository.store(RawResponseWrite(
            provider="espn_unofficial",
            endpoint=str(kwargs["endpoint"]),
            request_params=dict(kwargs["params"]),
            response_json=result.payload,
            fetched_at=result.source_fetched_at,
            entity_type=EntityType.EVENT if event_id else EntityType.LEAGUE,
            capture_kind=CaptureKind.LIVE_SNAPSHOT,
            entity_id=str(event_id) if event_id else str(kwargs["league_slug"]),
            scope_event_id=str(event_id) if event_id else None,
            league_slug=str(kwargs["league_slug"]),
            snapshot_bucket=f"L{int(result.source_fetched_at.timestamp() * 1000):013d}",
            http_status=result.http_status,
            parser_version="live_event_stream_v1",
        ))
        return LiveCaptureReceipt(str(stored.id), stored.response_hash, stored.fetched_at.isoformat(), resource)


@dataclass(frozen=True, slots=True)
class EspnLivePollConfig:
    """Cadencias y límites seguros del follower."""

    scoreboard_interval_seconds: float = 30.0
    active_match_interval_seconds: float = 10.0
    situation_interval_seconds: float = 20.0
    maximum_active_matches_per_poll: int = 40
    active_states: tuple[str, ...] = ("in", "live")


class EspnLiveMatchFollower:
    """Descubre y captura partidos live de una liga en ciclos explícitos."""

    def __init__(
        self,
        connector: EspnProspectiveConnector,
        raw_store: LiveRawStore,
        config: EspnLivePollConfig | None = None,
    ) -> None:
        self.connector = connector
        self.raw_store = raw_store
        self.config = config or EspnLivePollConfig()
        self._poll_sequence = 0
        self.last_errors: list[dict[str, str]] = []
        self._last_clock_by_event: dict[str, float] = {}
        self._last_fetched_at_by_event: dict[str, datetime] = {}

    def poll_once(self, date: str) -> list[dict[str, Any]]:
        """Captura scoreboard y todos los fixtures activos de un ciclo."""

        self._poll_sequence += 1
        self.last_errors = []
        league = self.connector.config.league
        scoreboard = self.connector.scoreboard_fetch_result(date, use_cache=False)
        scoreboard_receipt = self.raw_store.store(
            resource="scoreboard",
            endpoint=scoreboard.source_url or f"{SITE_BASE}/{league}/scoreboard",
            params={"dates": date},
            result=scoreboard,
            league_slug=league,
            event_id=None,
            poll_sequence=self._poll_sequence,
        )
        active = self._active_events(scoreboard.payload)
        snapshots = []
        for event in active[: self.config.maximum_active_matches_per_poll]:
            refs = scoreboard_references({"events": [event]})
            if not refs:
                continue
            ref = refs[0]
            try:
                snapshot = self._poll_event(event, ref, scoreboard_receipt)
                self._validate_snapshot_progress(snapshot)
                snapshots.append(snapshot)
            except (EspnConnectorError, EspnResourceUnavailable, ValueError, OSError) as exc:
                self.last_errors.append({
                    "provider_event_id": str(event.get("id") or "unknown"),
                    "error": str(exc)[:160],
                })
        return snapshots

    def _validate_snapshot_progress(self, snapshot: dict[str, Any]) -> None:
        """Rechaza regresiones entre polls después de conservar el raw."""

        event_id = str(snapshot["provider_event_id"])
        clock = float(snapshot["match_clock_seconds"])
        fetched_at = _parse_ts(str(snapshot["source_fetched_at"]))
        previous_clock = self._last_clock_by_event.get(event_id)
        previous_fetched_at = self._last_fetched_at_by_event.get(event_id)
        if previous_clock is not None and clock + 1e-9 < previous_clock:
            raise ValueError("live_snapshot_clock_regression")
        if previous_fetched_at is not None and fetched_at < previous_fetched_at:
            raise ValueError("live_snapshot_timestamp_regression")
        self._last_clock_by_event[event_id] = clock
        self._last_fetched_at_by_event[event_id] = fetched_at

    def _poll_event(
        self,
        scoreboard_event: dict[str, Any],
        reference: dict[str, str],
        scoreboard_receipt: LiveCaptureReceipt,
    ) -> dict[str, Any]:
        league = self.connector.config.league
        event_id = reference["provider_match_id"]
        competition_id = reference["competition_id"]
        event_result = self.connector.event_fetch_result(event_id, use_cache=False)
        event_receipt = self.raw_store.store(
            resource="event", endpoint=(
                event_result.source_url
                or f"{CORE_BASE}/leagues/{league}/events/{event_id}"
            ),
            params={}, result=event_result, league_slug=league, event_id=event_id,
            poll_sequence=self._poll_sequence,
        )
        plays_result = self.connector.plays_fetch_result(event_id, competition_id, use_cache=False)
        fallback = plays_result.payload.get("_fallbackEndpoint") == "summary_commentary"
        plays_receipt = self.raw_store.store(
            resource="summary_fallback" if fallback else "plays",
            endpoint=plays_result.source_url or (
                f"{SITE_BASE}/{league}/summary" if fallback
                else f"{CORE_BASE}/leagues/{league}/events/{event_id}/competitions/{competition_id}/plays"
            ),
            params={"event": event_id} if fallback else {"limit": 300},
            result=plays_result, league_slug=league,
            event_id=event_id, poll_sequence=self._poll_sequence,
        )
        situation_payload: dict[str, Any] | None = None
        situation_receipt: LiveCaptureReceipt | None = None
        try:
            request = self.connector.resource_request(
                "situation", event_id=event_id, competition_id=competition_id,
            )
            situation_result = self.connector.fetch_request_result(request, use_cache=False)
            situation_receipt = self.raw_store.store(
                resource="situation", endpoint=situation_result.source_url or request.url,
                params=request.params,
                result=situation_result, league_slug=league, event_id=event_id,
                poll_sequence=self._poll_sequence,
            )
            situation_payload = situation_result.payload
        except (EspnConnectorError, EspnResourceUnavailable):
            situation_payload = None
        receipts = [scoreboard_receipt, event_receipt, plays_receipt]
        if situation_receipt is not None:
            receipts.append(situation_receipt)
        fetched_at = max(_parse_ts(receipt.fetched_at) for receipt in receipts)
        return normalize_live_snapshot(
            league_slug=league,
            scoreboard_event=scoreboard_event,
            event_payload=event_result.payload,
            plays_payload=plays_result.payload,
            situation_payload=situation_payload,
            source_fetched_at=fetched_at,
            raw_receipts=receipts,
            poll_sequence=self._poll_sequence,
        )

    def _active_events(self, scoreboard: dict[str, Any]) -> list[dict[str, Any]]:
        events = scoreboard.get("events")
        if not isinstance(events, list):
            raise ValueError("malformed_live_scoreboard")
        output = []
        for event in events:
            if not isinstance(event, dict):
                continue
            competitions = event.get("competitions")
            competition = competitions[0] if isinstance(competitions, list) and competitions else {}
            status = (competition.get("status") or event.get("status") or {}) if isinstance(competition, dict) else {}
            status_type = status.get("type") if isinstance(status, dict) else {}
            if not isinstance(status_type, dict):
                status_type = {}
            state = str(status_type.get("state") or "unknown").lower()
            if state in self.config.active_states:
                output.append(event)
        return sorted(output, key=lambda row: str(row.get("id", "")))


def normalize_live_snapshot(
    *,
    league_slug: str,
    scoreboard_event: dict[str, Any],
    event_payload: dict[str, Any],
    plays_payload: dict[str, Any],
    situation_payload: dict[str, Any] | None,
    source_fetched_at: datetime,
    raw_receipts: list[LiveCaptureReceipt],
    poll_sequence: int,
) -> dict[str, Any]:
    """Normaliza después de comprobar que todas las fuentes fueron persistidas."""

    if not raw_receipts:
        raise ValueError("raw_first_receipt_required")
    competitions = scoreboard_event.get("competitions")
    if not isinstance(competitions, list) or not competitions or not isinstance(competitions[0], dict):
        raise ValueError("live_competition_missing")
    competition = competitions[0]
    competitors = {
        str(row.get("homeAway")): row
        for row in competition.get("competitors", [])
        if isinstance(row, dict)
    }
    home = competitors.get("home")
    away = competitors.get("away")
    if not isinstance(home, dict) or not isinstance(away, dict):
        raise ValueError("live_orientation_missing")
    home_id = _team_id(home)
    away_id = _team_id(away)
    event_id = str(scoreboard_event.get("id") or "")
    competition_id = str(competition.get("id") or "")
    if not event_id or not competition_id or home_id == away_id:
        raise ValueError("invalid_live_identity")
    kickoff = _parse_ts(str(scoreboard_event.get("date")))
    status = competition.get("status") or scoreboard_event.get("status") or {}
    status_type = status.get("type") if isinstance(status, dict) else {}
    if not isinstance(status_type, dict):
        status_type = {}
    period = int(status.get("period") or 1) if isinstance(status, dict) else 1
    clock_candidates = [
        _clock_seconds(status.get("clock") if isinstance(status, dict) else None),
        _clock_seconds(competition.get("clock")),
        _display_clock_seconds(
            status.get("displayClock") if isinstance(status, dict) else None,
        ),
    ]
    valid_clocks = [value for value in clock_candidates if value is not None]
    if not valid_clocks:
        raise ValueError("live_clock_missing")
    clock = max(valid_clocks)
    clock = _period_aware_clock(float(clock), period)
    events = []
    raw_items = plays_payload.get("items")
    if not isinstance(raw_items, list):
        raise ValueError("malformed_live_plays")
    for play in raw_items:
        if not isinstance(play, dict):
            continue
        canonical, raw_type = classify_play(play)
        play_period = _period_number(play.get("period"), period)
        play_clock = _clock_seconds(play.get("clock"))
        if play_clock is None:
            continue
        play_clock = _period_aware_clock(play_clock, play_period)
        team_id = _play_team_id(play)
        play_id = str(play.get("id") or "").strip()
        semantic_id = play_id or _stable_hash({
            "event_id": event_id, "clock": play_clock, "period": play_period,
            "type": raw_type, "team_id": team_id,
            "text": play.get("text") or play.get("shortText"),
        })
        events.append({
            "event_id": semantic_id,
            "event_ts": (kickoff + timedelta(seconds=play_clock)).isoformat(),
            "event_type": canonical,
            "event_type_raw": raw_type,
            "team_id": team_id,
            "period": play_period,
            "match_clock_seconds": play_clock,
            "observed_at": source_fetched_at.isoformat(),
            "annulled": bool(play.get("annulled", False) or play.get("deleted", False)),
            "event_time_quality": "provider_clock",
            "text": str(play.get("text") or play.get("shortText") or "")[:500],
        })
    events.sort(key=lambda row: (row["match_clock_seconds"], row["event_id"]))
    payload = {
        "contract_version": "live_event_stream_v1",
        "league_slug": league_slug,
        "provider_event_id": event_id,
        "competition_id": competition_id,
        "home_team_id": home_id,
        "away_team_id": away_id,
        "home_team_name": _team_name(home),
        "away_team_name": _team_name(away),
        "home_team_logo": _team_logo(home),
        "away_team_logo": _team_logo(away),
        "kickoff_ts": kickoff.isoformat(),
        "source_fetched_at": _utc(source_fetched_at).isoformat(),
        "provider_status": str((status_type or {}).get("state") or "unknown").lower(),
        "provider_status_detail": str((status_type or {}).get("detail") or (status_type or {}).get("description") or ""),
        "period": period,
        "match_clock_seconds": clock,
        "score_home": _score(home),
        "score_away": _score(away),
        "events": events,
        "situation": situation_payload,
        "poll_sequence": int(poll_sequence),
        "raw_receipts": [asdict(receipt) for receipt in raw_receipts],
        "event_payload_hash": _stable_hash(event_payload),
        "plays_payload_hash": _stable_hash(plays_payload),
        "situation_payload_hash": _stable_hash(situation_payload) if situation_payload is not None else None,
    }
    payload["source_hash"] = _stable_hash(payload)
    return payload


def live_inference_payload(
    snapshot: dict[str, Any],
    *,
    lambda_base_home: float,
    lambda_base_away: float,
    enable_hawkes: bool = True,
    hawkes_rho: float | None = None,
    hawkes_rho_goal: float | None = None,
    hawkes_rho_next_event: float | None = None,
    prior_source_hash: str = "",
) -> dict[str, Any]:
    """Une un snapshot ESPN con un prior pre-match previamente congelado."""

    for value in (lambda_base_home, lambda_base_away):
        if not math.isfinite(value) or value <= 0.0:
            raise ValueError("invalid_frozen_prematch_intensity")
    if snapshot.get("contract_version") != "live_event_stream_v1":
        raise ValueError("incompatible_live_snapshot_contract")
    source_hash = _stable_hash({
        "live_snapshot": str(snapshot["source_hash"]),
        "frozen_prematch_prior": str(prior_source_hash),
    })
    payload = {
        "match_id": int(snapshot["provider_event_id"]),
        "home_team_id": int(snapshot["home_team_id"]),
        "away_team_id": int(snapshot["away_team_id"]),
        "kickoff_ts": str(snapshot["kickoff_ts"]),
        "snapshot_ts": str(snapshot["source_fetched_at"]),
        "lambda_base_home": float(lambda_base_home),
        "lambda_base_away": float(lambda_base_away),
        "events": tuple(dict(event) for event in snapshot.get("events", [])),
        "official_prediction": False,
        "hawkes_enabled": bool(enable_hawkes),
        "hawkes_shadow_mode": bool(enable_hawkes),
        "source_hash": source_hash,
        "league_slug": str(snapshot["league_slug"]),
        "provider_event_id": str(snapshot["provider_event_id"]),
        "competition_id": str(snapshot["competition_id"]),
        "period": int(snapshot["period"]),
        "match_clock_seconds": float(snapshot["match_clock_seconds"]),
        "score_home": int(snapshot["score_home"]),
        "score_away": int(snapshot["score_away"]),
        "source_fetched_at": str(snapshot["source_fetched_at"]),
        "markov_live_enabled": True,
        "markov_live_shadow_mode": True,
        "hawkes_rho": hawkes_rho,
        "hawkes_rho_goal": hawkes_rho_goal,
        "hawkes_rho_next_event": hawkes_rho_next_event,
    }
    return payload


def _team_id(competitor: dict[str, Any]) -> int:
    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    value = team.get("id") or competitor.get("id")
    if value is None or not str(value).isdigit() or int(value) <= 0:
        raise ValueError("invalid_live_team_id")
    return int(value)


def _team_name(competitor: dict[str, Any]) -> str:
    """Obtiene el nombre ESPN sin inferir orientación ni identidad."""

    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    value = (
        team.get("displayName") or team.get("name")
        or team.get("shortDisplayName") or competitor.get("displayName")
    )
    return str(value or _team_id(competitor))


def _team_logo(competitor: dict[str, Any]) -> str | None:
    """Extrae logo publicado por el proveedor sin construir una URL."""

    team = competitor.get("team") if isinstance(competitor.get("team"), dict) else {}
    if isinstance(team.get("logo"), str):
        return str(team["logo"])
    logos = team.get("logos")
    if isinstance(logos, list) and logos and isinstance(logos[0], dict):
        return str(logos[0].get("href") or "") or None
    return None


def _score(competitor: dict[str, Any]) -> int:
    value = competitor.get("score", 0)
    if isinstance(value, dict):
        value = value.get("value") or value.get("displayValue") or 0
    try:
        score = int(float(str(value)))
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_live_score") from exc
    if score < 0:
        raise ValueError("negative_live_score")
    return score


def _play_team_id(play: dict[str, Any]) -> int | None:
    team = play.get("team")
    if isinstance(team, dict):
        value = team.get("id")
        if value is not None and str(value).isdigit():
            return int(value)
        reference = team.get("$ref")
        if isinstance(reference, str):
            match = re.search(r"/teams/(\d+)", urlparse(reference).path)
            if match:
                return int(match.group(1))
    return None


def _period_number(value: Any, default: int) -> int:
    if isinstance(value, dict):
        value = value.get("number") or value.get("value")
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(5, parsed))


def _clock_seconds(value: Any) -> float | None:
    if isinstance(value, dict):
        candidates = [
            _clock_seconds(value.get("value")),
            _display_clock_seconds(value.get("displayValue")),
        ]
        valid = [candidate for candidate in candidates if candidate is not None]
        return max(valid) if valid else None
    if not isinstance(value, (int, float)):
        return None
    parsed = float(value)
    return parsed if math.isfinite(parsed) and parsed >= 0.0 else None


def _display_clock_seconds(value: Any) -> float | None:
    if not isinstance(value, str):
        return None
    stoppage = re.fullmatch(
        r"\s*(\d+)\s*'?\s*\+\s*(\d+)(?::(\d{1,2}))?\s*'?\s*",
        value,
    )
    if stoppage:
        return float(
            (int(stoppage.group(1)) + int(stoppage.group(2))) * 60
            + int(stoppage.group(3) or 0)
        )
    match = re.fullmatch(r"\s*(\d+)(?::(\d{1,2}))?\s*'?\s*", value)
    if not match:
        return None
    return float(int(match.group(1)) * 60 + int(match.group(2) or 0))


def _period_aware_clock(value: float, period: int) -> float:
    thresholds = {2: 45.0 * 60.0, 3: 90.0 * 60.0, 4: 105.0 * 60.0, 5: 120.0 * 60.0}
    threshold = thresholds.get(period)
    if threshold is not None and value < threshold:
        previous = {2: 45.0, 3: 90.0, 4: 105.0, 5: 120.0}[period] * 60.0
        return previous + value
    return value


__all__ = [
    "EspnLiveMatchFollower", "EspnLivePollConfig", "FileLiveRawStore",
    "InMemoryLiveRawStore", "LiveCaptureReceipt", "RepositoryLiveRawStore",
    "live_inference_payload", "normalize_live_snapshot",
]
