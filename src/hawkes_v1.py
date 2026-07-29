"""Hawkes v1 sintético sobre intensidades Markov, sin probabilidades."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Any, Iterable

EXCITING_EVENTS = {
    "goal", "red", "yellow", "shot_on_target", "shot_blocked", "corner",
    "substitution", "penalty_awarded", "penalty_scored",
}


@dataclass(frozen=True)
class HawkesConfig:
    """Configuración inmutable del estimator sintético."""
    model_version: str = "hawkes_v1"
    time_unit: str = "minute"
    memory_minutes: float = 30.0
    alpha_self: float = 0.20
    alpha_cross: float = 0.08
    beta: float = 0.25
    lambda_min: float = 1e-6
    lambda_max: float = 50.0
    warning_radius: float = 0.95
    block_radius: float = 1.0
    branching_matrix: tuple[tuple[float, float], tuple[float, float]] = ((0.39, 0.17), (0.17, 0.39))


def _parse_ts(value: Any) -> datetime:
    """Convierte un timestamp ISO a UTC."""
    if isinstance(value, datetime):
        result = value
    else:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return result if result.tzinfo else result.replace(tzinfo=timezone.utc)


def _radius(matrix: tuple[tuple[float, float], tuple[float, float]]) -> float:
    """Calcula el radio espectral de una matriz 2x2 no negativa."""
    a, b = matrix[0]
    c, d = matrix[1]
    trace = a + d
    discriminant = (a - d) ** 2 + 4 * b * c
    return (trace + math.sqrt(discriminant)) / 2


def _hash(value: Any) -> str:
    """Calcula un hash estable de JSON."""
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


class HawkesV1:
    """Calcula excitación temporal sobre una baseline Markov existente."""

    def __init__(self, config: HawkesConfig | None = None) -> None:
        self.config = config or HawkesConfig()
        self._validate_config()

    def _validate_config(self) -> None:
        """Valida unidades, parámetros y subcriticidad."""
        c = self.config
        if c.time_unit != "minute" or c.memory_minutes <= 0 or c.beta <= 0:
            raise ValueError("Hawkes v1 requiere tiempo en minutos y beta positivo.")
        if c.alpha_self < 0 or c.alpha_cross < 0:
            raise ValueError("Alpha no puede ser negativo.")
        matrix = c.branching_matrix
        if len(matrix) != 2 or any(len(row) != 2 for row in matrix) or any(x < 0 for row in matrix for x in row):
            raise ValueError("La matriz G debe ser 2x2 y no negativa.")
        radius = _radius(matrix)
        if radius >= c.block_radius:
            raise ValueError(f"Matriz G supercrítica: spectral_radius={radius:.6f}")

    def _canonical_events(self, events: Iterable[dict[str, Any]], snapshot: datetime) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Deduplica, corta temporalmente y separa auditoría de excitación."""
        seen: set[str] = set()
        used: list[dict[str, Any]] = []
        audit: list[dict[str, Any]] = []
        for index, event in enumerate(events):
            item = dict(event)
            event_id = str(item.get("event_id", f"missing:{index}"))
            item["event_id"] = event_id
            event_ts = _parse_ts(item["event_ts"])
            item["event_ts"] = event_ts.isoformat()
            if event_id in seen or event_ts > snapshot:
                item["exclusion_reason"] = "duplicate_event_id" if event_id in seen else "future_event"
                audit.append(item)
                continue
            seen.add(event_id)
            event_type = item.get("event_type")
            if event_type not in EXCITING_EVENTS or item.get("annulled") or item.get("team_id") is None:
                item["exclusion_reason"] = "non_exciting_or_invalid_context"
                audit.append(item)
                continue
            age = (snapshot - event_ts).total_seconds() / 60
            if age <= self.config.memory_minutes:
                used.append(item)
            else:
                item["exclusion_reason"] = "outside_memory"
                audit.append(item)
        used.sort(key=lambda x: (x["event_ts"], x["event_id"]))
        return used, audit

    def _contribution(self, event: dict[str, Any], snapshot: datetime, home_team_id: int, away_team_id: int) -> dict[str, float]:
        """Calcula contribución self/cross de un evento."""
        age = (snapshot - _parse_ts(event["event_ts"])).total_seconds() / 60
        decay = math.exp(-self.config.beta * age)
        is_home = event["team_id"] == home_team_id
        self_value = self.config.alpha_self * decay
        cross_value = self.config.alpha_cross * decay
        return {"home": self_value if is_home else cross_value, "away": cross_value if is_home else self_value, "dt_minutes": age}

    def predict_snapshot(self, *, match_id: int, snapshot_ts: str | datetime, lambda_markov_home: float,
                         lambda_markov_away: float, home_team_id: int, away_team_id: int,
                         events: Iterable[dict[str, Any]], markov_provenance: dict[str, Any]) -> dict[str, Any]:
        """Genera intensidades Hawkes para un snapshot sin recalcular Markov."""
        snapshot = _parse_ts(snapshot_ts)
        self._validate_lambda(lambda_markov_home, "home")
        self._validate_lambda(lambda_markov_away, "away")
        used, audit = self._canonical_events(events, snapshot)
        contributions = []
        home_excitation = away_excitation = 0.0
        for event in used:
            contribution = self._contribution(event, snapshot, home_team_id, away_team_id)
            home_excitation += contribution["home"]
            away_excitation += contribution["away"]
            contributions.append({"event_id": event["event_id"], **contribution})
        radius = _radius(self.config.branching_matrix)
        warnings = ["branching_radius_near_limit"] if radius >= self.config.warning_radius else []
        result = {"match_id": match_id, "snapshot_ts": snapshot.isoformat(),
                  "lambda_markov_home": lambda_markov_home, "lambda_markov_away": lambda_markov_away,
                  "lambda_hawkes_home": lambda_markov_home + home_excitation, "lambda_hawkes_away": lambda_markov_away + away_excitation,
                  "events_used": used, "events_audit": audit, "event_contributions": contributions,
                  "alpha_self": self.config.alpha_self, "alpha_cross": self.config.alpha_cross, "beta": self.config.beta,
                  "branching_matrix": self.config.branching_matrix, "spectral_radius": radius,
                  "markov_provenance": markov_provenance, "warnings": warnings}
        self._validate_lambda(result["lambda_hawkes_home"], "hawkes_home")
        self._validate_lambda(result["lambda_hawkes_away"], "hawkes_away")
        return result

    def _validate_lambda(self, value: float, label: str) -> None:
        """Rechaza intensidades no finitas o fuera de límites."""
        if not math.isfinite(value) or value < self.config.lambda_min or value > self.config.lambda_max:
            raise ValueError(f"Intensidad inválida para {label}: {value}")

    def model_hash(self) -> str:
        """Devuelve el hash de la configuración efectiva."""
        return _hash(asdict(self.config))

