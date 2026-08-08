"""Residual Hawkes complementario sobre Markov Live.

Hawkes no reemplaza el baseline Markov: modula hazards en escala logarítmica
y conserva por separado residual y salida combinada.

Version: 2.0.0
Created: 2026-08-07
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from typing import Any

try:
    from src.markov_live_v1 import competing_event_distribution, remaining_goal_markets
except ModuleNotFoundError:  # pragma: no cover
    from markov_live_v1 import competing_event_distribution, remaining_goal_markets


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class HawkesLiveConfig:
    """Configuración conservadora y no calibrada del residual shadow."""

    model_version: str = "hawkes_live_v2_residual_shadow"
    rho: float = 1.0
    rho_goal: float | None = None
    rho_next_event: float | None = 0.0
    beta_per_minute: float = 0.35
    memory_minutes: float = 20.0
    alpha_self: float = 0.08
    alpha_cross: float = 0.03
    maximum_log_residual: float = 0.45
    maximum_multiplier: float = 1.60
    warning_radius: float = 0.90


class HawkesLiveV2:
    """Calcula excitación residual y la compone con un snapshot Markov Live."""

    SOURCE_WEIGHTS = {
        "goal": 1.00,
        "penalty_scored": 1.00,
        "penalty_awarded": 0.90,
        "shot_on_target": 0.70,
        "shot_blocked": 0.40,
        "shot_off_target": 0.25,
        "corner": 0.35,
        "red": 0.75,
        "yellow": 0.15,
        "foul": 0.08,
        "substitution": 0.05,
    }

    def __init__(self, config: HawkesLiveConfig | None = None) -> None:
        self.config = config or HawkesLiveConfig()
        self._validate_config()

    def _validate_config(self) -> None:
        shrinkages = [self.config.rho]
        shrinkages.extend(
            value for value in (self.config.rho_goal, self.config.rho_next_event)
            if value is not None
        )
        if any(not 0.0 <= value <= 1.0 for value in shrinkages):
            raise ValueError("hawkes_rho_out_of_range")
        if self.config.beta_per_minute <= 0.0 or self.config.memory_minutes <= 0.0:
            raise ValueError("invalid_hawkes_decay")
        if min(self.config.alpha_self, self.config.alpha_cross) < 0.0:
            raise ValueError("negative_hawkes_alpha")
        if self.spectral_radius() >= 1.0:
            raise ValueError("hawkes_branching_matrix_not_subcritical")

    def spectral_radius(self) -> float:
        """Radio espectral de la matriz self/cross simétrica."""

        return (self.config.alpha_self + self.config.alpha_cross) / self.config.beta_per_minute

    def combine(
        self,
        markov_live: dict[str, Any],
        events: list[dict[str, Any]],
        *,
        home_team_id: int,
        away_team_id: int,
        score_home: int,
        score_away: int,
    ) -> dict[str, Any]:
        """Aplica el residual a goles y hazards de próximo evento."""

        if not markov_live.get("audit", {}).get("passed", False):
            raise ValueError("markov_live_audit_required")
        snapshot_clock = float(markov_live["match_clock_seconds"])
        residuals, contributions, audit = self._residuals(
            events, snapshot_clock, home_team_id, away_team_id,
        )
        lambda_markov_home = float(markov_live["lambda_remaining_home"])
        lambda_markov_away = float(markov_live["lambda_remaining_away"])
        rho_goal = self.config.rho if self.config.rho_goal is None else self.config.rho_goal
        rho_next = self.config.rho if self.config.rho_next_event is None else self.config.rho_next_event
        goal_effect = rho_goal > 0.0 and any(
            residuals[key] > 0.0 for key in ("home:goal", "away:goal")
        )
        next_effect = rho_next > 0.0 and any(
            residuals.get(key, 0.0) > 0.0
            for key in markov_live["next_event"]["rates_per_minute"]
        )
        no_effect = not goal_effect and not next_effect
        if goal_effect:
            lambda_home = self._combine_value(
                lambda_markov_home, residuals["home:goal"], rho_goal,
            )
            lambda_away = self._combine_value(
                lambda_markov_away, residuals["away:goal"], rho_goal,
            )
            markets = remaining_goal_markets(lambda_home, lambda_away, score_home, score_away)
        else:
            lambda_home = lambda_markov_home
            lambda_away = lambda_markov_away
            markets = dict(markov_live["markets"])
        if next_effect:
            rates = {
                key: self._combine_value(
                    float(value), residuals.get(key, 0.0), rho_next,
                )
                for key, value in markov_live["next_event"]["rates_per_minute"].items()
            }
            next_event = competing_event_distribution(
                rates, float(markov_live["next_event"]["horizon_minutes"]),
            )
        else:
            next_event = {
                **markov_live["next_event"],
                "rates_per_minute": dict(markov_live["next_event"]["rates_per_minute"]),
                "probabilities": dict(markov_live["next_event"]["probabilities"]),
            }
        radius = self.spectral_radius()
        residual_block = {
            "status": "experimental_shadow_not_promoted",
            "model_version": self.config.model_version,
            "rho": self.config.rho,
            "rho_by_target": {"goal_markets": rho_goal, "next_event": rho_next},
            "log_residuals": dict(sorted(residuals.items())),
            "event_contributions": contributions,
            "events_audit": audit,
            "stability": {
                "spectral_radius": radius,
                "subcritical": radius < 1.0,
                "warning": radius >= self.config.warning_radius,
            },
            "provenance": {
                "config_hash": _stable_hash(asdict(self.config)),
                "parameters_calibrated": False,
                "composition": "log_lambda_markov_plus_rho_times_hawkes",
            },
        }
        combined = {
            "status": "experimental_shadow_not_promoted",
            "model_version": "markov_live_v1_plus_hawkes_live_v2",
            "lambda_remaining_home": lambda_home,
            "lambda_remaining_away": lambda_away,
            "markets": markets,
            "next_event": next_event,
            "fallback_exact_markov_live": no_effect,
            "markov_live_output_hash": markov_live.get("output_hash", ""),
        }
        combined["output_hash"] = _stable_hash(combined)
        return {"hawkes_residual": residual_block, "combined_live": combined}

    def _combine_value(self, baseline: float, residual: float, rho: float) -> float:
        if baseline < 0.0 or not math.isfinite(baseline):
            raise ValueError("invalid_markov_baseline")
        log_residual = min(self.config.maximum_log_residual, max(0.0, residual))
        multiplier = min(self.config.maximum_multiplier, math.exp(rho * log_residual))
        value = baseline * multiplier
        if not math.isfinite(value) or value < 0.0:
            raise FloatingPointError("invalid_combined_hazard")
        return value

    def _residuals(
        self,
        events: list[dict[str, Any]],
        snapshot_clock_seconds: float,
        home_team_id: int,
        away_team_id: int,
    ) -> tuple[dict[str, float], list[dict[str, Any]], dict[str, int]]:
        targets = (
            "home:goal", "away:goal", "home:shot_on_target", "away:shot_on_target",
            "home:corner", "away:corner", "home:card", "away:card",
            "home:substitution", "away:substitution",
        )
        residuals = {target: 0.0 for target in targets}
        contributions: list[dict[str, Any]] = []
        seen: set[str] = set()
        audit = {"received": len(events), "used": 0, "duplicates": 0, "future": 0, "outside_memory": 0, "ignored": 0}
        ordered = sorted(events, key=self._event_sort_key)
        for event in ordered:
            event_id = str(event.get("event_id") or "").strip()
            if event_id and event_id in seen:
                audit["duplicates"] += 1
                continue
            if event_id:
                seen.add(event_id)
            event_type = str(event.get("event_type", ""))
            weight = self.SOURCE_WEIGHTS.get(event_type)
            team_id = event.get("team_id")
            clock = event.get("match_clock_seconds")
            if weight is None or bool(event.get("annulled", False)) or team_id not in {home_team_id, away_team_id}:
                audit["ignored"] += 1
                continue
            if not isinstance(clock, (int, float)) or not math.isfinite(float(clock)):
                audit["ignored"] += 1
                continue
            age_minutes = (snapshot_clock_seconds - float(clock)) / 60.0
            if age_minutes < -1e-9:
                raise ValueError("future_event_after_hawkes_snapshot")
            if age_minutes > self.config.memory_minutes:
                audit["outside_memory"] += 1
                continue
            source_side = "home" if int(team_id) == home_team_id else "away"
            if event_type == "red":
                source_side = "away" if source_side == "home" else "home"
            decay = math.exp(-self.config.beta_per_minute * max(0.0, age_minutes))
            event_rows = []
            for target in targets:
                target_side = target.split(":", 1)[0]
                alpha = self.config.alpha_self if target_side == source_side else self.config.alpha_cross
                target_type = target.split(":", 1)[1]
                affinity = self._affinity(event_type, target_type)
                contribution = alpha * weight * affinity * decay
                residuals[target] += contribution
                if contribution > 0.0:
                    event_rows.append({"target": target, "value": contribution})
            contributions.append({
                "event_id": event_id,
                "event_type": event_type,
                "source_side": source_side,
                "age_minutes": max(0.0, age_minutes),
                "contributions": event_rows,
            })
            audit["used"] += 1
        residuals = {
            key: min(self.config.maximum_log_residual, max(0.0, value))
            for key, value in residuals.items()
        }
        return residuals, contributions, audit

    @staticmethod
    def _event_sort_key(event: dict[str, Any]) -> tuple[float, str]:
        """Ordena sin convertir clocks ausentes durante la comparación."""

        value = event.get("match_clock_seconds")
        clock = float(value) if isinstance(value, (int, float)) and math.isfinite(float(value)) else -1.0
        return clock, str(event.get("event_id", ""))

    @staticmethod
    def _affinity(source_type: str, target_type: str) -> float:
        if target_type == "goal":
            return 1.0 if source_type in {"goal", "penalty_scored", "penalty_awarded", "shot_on_target"} else 0.45
        if target_type == "card":
            return 1.0 if source_type in {"yellow", "red", "foul"} else 0.15
        if target_type == "substitution":
            return 1.0 if source_type == "substitution" else 0.10
        return 1.0 if source_type == target_type else 0.30


__all__ = ["HawkesLiveConfig", "HawkesLiveV2"]
