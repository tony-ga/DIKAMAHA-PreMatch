"""Contratos para un corpus secuencial causal multi-resolución.

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from typing import Any, Iterable

from src.event_windows_v1 import EventWindowsConfig, build_windows


@dataclass(frozen=True, slots=True)
class SequenceResolution:
    """Define una resolución temporal reglamentaria."""

    minutes: int

    @property
    def window_count(self) -> int:
        """Devuelve el número de ventanas que cubre 90 minutos."""

        return 90 // self.minutes

    @property
    def version(self) -> str:
        """Devuelve la versión semántica de la resolución."""

        return f"causal_sequence_{self.minutes}m_v1"


def build_resolution(
    match: dict[str, Any],
    events: list[dict[str, Any]],
    resolution: SequenceResolution,
) -> list[dict[str, Any]]:
    """Materializa un partido sin usar eventos futuros como contexto inicial."""

    config = EventWindowsConfig(
        version=resolution.version,
        competition_id=str(match["competition_id"]),
        competition_name=str(match["league_slug"]),
        window_minutes=resolution.minutes,
        regular_window_count=resolution.window_count,
    )
    windows, _ = build_windows([match], events, config)
    return [_decorate(row, match, resolution) for row in windows]


def _decorate(
    row: dict[str, Any],
    match: dict[str, Any],
    resolution: SequenceResolution,
) -> dict[str, Any]:
    """Añade provenance y un cutoff causal explícito a una ventana."""

    row["league_slug"] = str(match["league_slug"])
    row["resolution_minutes"] = resolution.minutes
    row["feature_cutoff_seconds"] = int(row["window_start_minute"]) * 60
    row["observation_end_seconds"] = _observation_end(row)
    row["context_is_strictly_prior"] = True
    return row


def _observation_end(row: dict[str, Any]) -> int | None:
    """Convierte el final regular a segundos; ``None`` absorbe añadido."""

    value = row["window_end_minute"]
    return None if value is None else int(value) * 60


def score_reconciles(
    windows: Iterable[dict[str, Any]],
    home_score: int,
    away_score: int,
) -> bool:
    """Comprueba que los goles de la secuencia igualan el marcador final."""

    scores = Counter()
    for row in windows:
        scores["home" if row["is_home"] else "away"] += int(row["goals"])
    return scores["home"] == home_score and scores["away"] == away_score


def stable_hash(value: Any) -> str:
    """Calcula SHA-256 determinista para manifiestos y auditorías."""

    raw = json.dumps(value, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# Requirements: sin dependencias externas adicionales.
# Version: 1.0.0 - 2026-07-27
