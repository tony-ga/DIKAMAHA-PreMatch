"""Planificación causal de snapshots pre-match por buckets temporales.

Version: 1.0.0
Created: 2026-07-27
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True, slots=True)
class CutoffBucket:
    """Ventana real asociada a un corte nominal pre-match."""

    name: str
    minimum_hours: float
    maximum_hours: float

    def contains(self, hours_until_kickoff: float) -> bool:
        """Indica si la distancia al kickoff pertenece al bucket."""

        return self.minimum_hours < hours_until_kickoff <= self.maximum_hours


@dataclass(frozen=True, slots=True)
class UpcomingFixture:
    """Fixture programado normalizado desde scoreboard."""

    event_id: str
    competition_id: str
    league_slug: str
    kickoff_ts: datetime
    home_team_id: str
    away_team_id: str


@dataclass(frozen=True, slots=True)
class SnapshotJob:
    """Trabajo idempotente para un fixture y bucket."""

    fixture: UpcomingFixture
    bucket: CutoffBucket
    observed_at: datetime


BUCKETS = (
    CutoffBucket("T-168h", 120.0, 240.0),
    CutoffBucket("T-72h", 48.0, 120.0),
    CutoffBucket("T-24h", 12.0, 48.0),
    CutoffBucket("T-6h", 3.75, 12.0),
    CutoffBucket("T-90m", 0.0, 3.75),
)


def fixtures_from_scoreboard(
    payload: dict[str, Any],
    league_slug: str,
) -> list[UpcomingFixture]:
    """Extrae únicamente fixtures programados con identidad completa."""

    events = payload.get("events")
    if not isinstance(events, list):
        raise ValueError("malformed_scoreboard_events")
    fixtures = [_fixture(event, league_slug) for event in events if isinstance(event, dict)]
    return sorted((item for item in fixtures if item is not None), key=_fixture_key)


def due_jobs(
    fixtures: list[UpcomingFixture],
    observed_at: datetime,
) -> list[SnapshotJob]:
    """Asigna como máximo un bucket vigente a cada fixture."""

    now = _utc(observed_at)
    jobs = []
    for fixture in fixtures:
        hours = (fixture.kickoff_ts - now).total_seconds() / 3600.0
        bucket = next((item for item in BUCKETS if item.contains(hours)), None)
        if bucket is not None:
            jobs.append(SnapshotJob(fixture, bucket, now))
    return jobs


def _fixture(event: dict[str, Any], league_slug: str) -> UpcomingFixture | None:
    """Normaliza un evento programado o lo excluye explícitamente."""

    if not _is_scheduled(event):
        return None
    competitions = event.get("competitions")
    competition = competitions[0] if isinstance(competitions, list) and competitions else None
    if not isinstance(competition, dict):
        return None
    teams = _team_ids(competition.get("competitors"))
    if teams is None:
        return None
    event_id = str(event.get("id", "")).strip()
    competition_id = str(competition.get("id", "")).strip()
    kickoff = _parse_utc(competition.get("date") or event.get("date"))
    if not event_id or not competition_id or kickoff is None:
        return None
    return UpcomingFixture(event_id, competition_id, league_slug, kickoff, *teams)


def _team_ids(competitors: Any) -> tuple[str, str] | None:
    """Extrae IDs home/away sin inferir orientaciones ausentes."""

    if not isinstance(competitors, list):
        return None
    mapped = {}
    for competitor in competitors:
        if not isinstance(competitor, dict):
            continue
        side = competitor.get("homeAway")
        team = competitor.get("team")
        value = team.get("id") if isinstance(team, dict) else None
        if side in {"home", "away"} and value is not None:
            mapped[side] = str(value)
    return (mapped["home"], mapped["away"]) if mapped.keys() >= {"home", "away"} else None


def _is_scheduled(event: dict[str, Any]) -> bool:
    """Acepta sólo eventos ESPN en estado pre y no completados."""

    status = event.get("status")
    kind = status.get("type") if isinstance(status, dict) else None
    return isinstance(kind, dict) and kind.get("state") == "pre" and not kind.get("completed", False)


def _parse_utc(value: Any) -> datetime | None:
    """Convierte timestamps ISO ESPN a UTC."""

    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc(parsed)


def _utc(value: datetime) -> datetime:
    """Normaliza un datetime consciente a UTC."""

    if value.tzinfo is None:
        raise ValueError("timezone_required")
    return value.astimezone(timezone.utc)


def _fixture_key(fixture: UpcomingFixture) -> tuple[datetime, str]:
    """Ordena fixtures de manera estable."""

    return fixture.kickoff_ts, fixture.event_id


# Version: 1.0.0
# Created: 2026-07-27
