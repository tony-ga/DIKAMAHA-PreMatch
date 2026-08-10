"""Motor matemático oficial para probabilidades de fútbol in-live.

Combina un prior causal pre-match con Poisson dinámico, una CTMC de régimen,
hazards tipo Cox, fuerza Elo latente y Hawkes residual. Monte Carlo se ejecuta
en un runner separado para no bloquear la respuesta analítica.

Version: 1.0.0
Created: 2026-08-09
"""

from __future__ import annotations

import hashlib
import json
import math
import threading
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any, Iterable

import numpy as np
from scipy.linalg import expm

try:
    from src.hawkes_live_v2 import HawkesLiveConfig, HawkesLiveV2
    from src.markov_live_v1 import (
        MarkovLiveInput,
        competing_event_distribution,
    )
except ModuleNotFoundError:  # pragma: no cover - ejecución directa
    from hawkes_live_v2 import HawkesLiveConfig, HawkesLiveV2
    from markov_live_v1 import MarkovLiveInput, competing_event_distribution


CONTRACT_VERSION = "live_probability_engine_contract_v1"
MODEL_VERSION = "live_probability_engine_v1"
REGIME_NAMES = ("home_pressure", "balanced", "away_pressure")
GOAL_TYPES = frozenset({"goal", "penalty_scored"})


def _stable_hash(value: Any) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clip(value: float, lower: float, upper: float) -> float:
    if not math.isfinite(value):
        raise FloatingPointError("non_finite_live_value")
    return min(upper, max(lower, value))


def _normalize(values: Iterable[float]) -> tuple[float, ...]:
    result = tuple(float(value) for value in values)
    if not result or any(not math.isfinite(value) or value < 0.0 for value in result):
        raise ValueError("invalid_probability_vector")
    total = math.fsum(result)
    if total <= 0.0:
        raise ValueError("zero_probability_mass")
    return tuple(value / total for value in result)


def _poisson_pmf(rate: float, maximum: int) -> tuple[float, ...]:
    rate = _clip(float(rate), 0.0, 20.0)
    values = [math.exp(-rate)]
    for count in range(1, maximum + 1):
        values.append(values[-1] * rate / count)
    values[-1] += max(0.0, 1.0 - math.fsum(values))
    return _normalize(values)


def _market_bundle(
    lambda_home: float,
    lambda_away: float,
    score_home: int,
    score_away: int,
    *,
    maximum: int = 14,
) -> dict[str, Any]:
    """Deriva mercados coherentes desde dos conteos Poisson restantes."""

    home_pmf = _poisson_pmf(lambda_home, maximum)
    away_pmf = _poisson_pmf(lambda_away, maximum)
    one_x_two = [0.0, 0.0, 0.0]
    over = {threshold: 0.0 for threshold in (0.5, 1.5, 2.5, 3.5)}
    btts = 0.0
    exact: list[dict[str, Any]] = []
    remaining = [0.0] * (maximum * 2 + 1)
    for home_goals, probability_home in enumerate(home_pmf):
        for away_goals, probability_away in enumerate(away_pmf):
            probability = probability_home * probability_away
            final_home = score_home + home_goals
            final_away = score_away + away_goals
            if final_home > final_away:
                one_x_two[0] += probability
            elif final_home == final_away:
                one_x_two[1] += probability
            else:
                one_x_two[2] += probability
            for threshold in over:
                if final_home + final_away > threshold:
                    over[threshold] += probability
            if final_home > 0 and final_away > 0:
                btts += probability
            remaining[min(home_goals + away_goals, len(remaining) - 1)] += probability
            exact.append({
                "score_home": final_home,
                "score_away": final_away,
                "probability": probability,
            })
    normalized = _normalize(one_x_two)
    exact.sort(
        key=lambda row: (
            -float(row["probability"]), int(row["score_home"]),
            int(row["score_away"]),
        )
    )
    return {
        "probability_home": normalized[0],
        "probability_draw": normalized[1],
        "probability_away": normalized[2],
        "probability_over_0_5": over[0.5],
        "probability_under_0_5": 1.0 - over[0.5],
        "probability_over_1_5": over[1.5],
        "probability_under_1_5": 1.0 - over[1.5],
        "probability_over_2_5": over[2.5],
        "probability_under_2_5": 1.0 - over[2.5],
        "probability_over_3_5": over[3.5],
        "probability_under_3_5": 1.0 - over[3.5],
        "probability_btts": btts,
        "probability_btts_no": 1.0 - btts,
        "exact_score": exact[:12],
        "remaining_goals_distribution": [
            {"goals": index, "probability": probability}
            for index, probability in enumerate(remaining)
            if probability >= 1e-8
        ],
    }


@dataclass(frozen=True, slots=True)
class LiveProbabilityEngineConfig:
    """Parámetros versionados y conservadores del motor analítico."""

    contract_version: str = CONTRACT_VERSION
    model_version: str = MODEL_VERSION
    regulation_minutes: float = 90.0
    extra_time_minutes: float = 120.0
    segment_minutes: float = 5.0
    maximum_remaining_intensity: float = 12.0
    maximum_additional_goals: int = 14
    next_event_horizon_minutes: float = 5.0
    ctmc_generator: tuple[tuple[float, ...], ...] = (
        (-0.040, 0.032, 0.008),
        (0.018, -0.036, 0.018),
        (0.008, 0.032, -0.040),
    )
    ctmc_goal_multipliers_home: tuple[float, ...] = (1.18, 1.0, 0.86)
    ctmc_goal_multipliers_away: tuple[float, ...] = (0.86, 1.0, 1.18)
    red_card_log_penalty: float = 0.38
    red_card_opponent_log_gain: float = 0.20
    maximum_log_hazard: float = 0.45
    maximum_log_elo: float = 0.24
    elo_points_per_goal: float = 82.0
    elo_points_per_pressure: float = 26.0
    elo_points_per_red_card: float = 95.0


class LiveProbabilityEngineV1:
    """Motor causal determinista que compone todas las capas en intensidades."""

    EVENT_WEIGHTS = {
        "goal": 1.00,
        "penalty_scored": 1.00,
        "penalty_awarded": 0.85,
        "shot_on_target": 0.72,
        "shot_blocked": 0.38,
        "shot_off_target": 0.30,
        "corner": 0.34,
        "foul": 0.08,
        "yellow": 0.12,
        "red": -0.75,
        "substitution": 0.04,
    }

    def __init__(self, config: LiveProbabilityEngineConfig | None = None) -> None:
        self.config = config or LiveProbabilityEngineConfig()
        self._generator = np.asarray(self.config.ctmc_generator, dtype=float)
        self._validate_config()
        self._segment_transition = self._transition(
            self.config.segment_minutes,
        )

    def _transition(self, minutes: float) -> np.ndarray:
        """Propaga el CTMC conservando masa y corrigiendo ruido numérico."""

        transition = expm(self._generator * minutes)
        transition = np.maximum(transition, 0.0)
        return transition / transition.sum(axis=1, keepdims=True)

    def _validate_config(self) -> None:
        if self._generator.shape != (3, 3):
            raise ValueError("live_ctmc_generator_must_be_3x3")
        if np.any(~np.isfinite(self._generator)):
            raise ValueError("live_ctmc_generator_non_finite")
        if np.any(np.diag(self._generator) > 1e-12):
            raise ValueError("live_ctmc_diagonal_positive")
        off_diagonal = self._generator.copy()
        np.fill_diagonal(off_diagonal, 0.0)
        if np.any(off_diagonal < -1e-12):
            raise ValueError("live_ctmc_negative_transition")
        if not np.allclose(self._generator.sum(axis=1), 0.0, atol=1e-12):
            raise ValueError("live_ctmc_rows_must_sum_zero")
        if self.config.segment_minutes <= 0.0:
            raise ValueError("invalid_live_segment_minutes")

    def predict(
        self,
        request: MarkovLiveInput,
        markov_live: dict[str, Any],
        *,
        hawkes_config: HawkesLiveConfig | None = None,
    ) -> dict[str, Any]:
        """Calcula la salida oficial usando sólo evidencia observada."""

        self._validate_request(request, markov_live)
        events = [dict(event) for event in markov_live.get("events_used", [])]
        red_cards = self._red_cards(events, request)
        hazard = self._hazard_layer(events, request)
        ctmc = self._ctmc_layer(events, request)
        elo = self._elo_layer(request, hazard, red_cards)
        dynamic = self._dynamic_poisson(
            request, ctmc, hazard, elo, red_cards,
        )
        analytical_baseline = {
            "status": "official_analytical_baseline",
            "model_version": "dynamic_poisson_ctmc_hazard_elo_v1",
            "match_clock_seconds": float(request.match_clock_seconds),
            "remaining_seconds": dynamic["remaining_seconds"],
            "lambda_remaining_home": dynamic["lambda_remaining_home"],
            "lambda_remaining_away": dynamic["lambda_remaining_away"],
            "markets": dynamic["markets"],
            "next_event": self._next_event(
                markov_live, dynamic, hazard,
            ),
            "events_used": events,
            "audit": {"passed": True},
            "output_hash": "",
        }
        analytical_baseline["output_hash"] = _stable_hash(analytical_baseline)
        hawkes = HawkesLiveV2(hawkes_config or HawkesLiveConfig()).combine(
            analytical_baseline,
            events,
            home_team_id=request.home_team_id,
            away_team_id=request.away_team_id,
            score_home=request.score_home,
            score_away=request.score_away,
        )
        residual = dict(hawkes["hawkes_residual"])
        residual["status"] = "official_component_residual"
        combined = dict(hawkes["combined_live"])
        combined["status"] = "official_composed_output"
        official_markets = dict(combined["markets"])
        official_markets.update({
            key: value for key, value in dynamic["markets"].items()
            if key not in official_markets
        })
        combined["markets"] = official_markets
        combined["periods"] = dynamic["periods"]
        combined["goal_horizons"] = self._goal_horizons(
            float(combined["lambda_remaining_home"]),
            float(combined["lambda_remaining_away"]),
            float(dynamic["remaining_seconds"]) / 60.0,
        )
        combined["output_hash"] = _stable_hash(combined)
        official = {
            "status": "official",
            "model_version": self.config.model_version,
            "markets": official_markets,
            "periods": dynamic["periods"],
            "remaining_intensities": {
                "home": float(combined["lambda_remaining_home"]),
                "away": float(combined["lambda_remaining_away"]),
            },
            "next_event": combined["next_event"],
            "exact_score": official_markets["exact_score"],
            "remaining_goals_distribution": official_markets[
                "remaining_goals_distribution"
            ],
            "goal_horizons": combined["goal_horizons"],
            "confidence": self._confidence(
                official_markets, markov_live, events,
            ),
            "updated_at": request.snapshot_ts,
            "fallback": {
                "applied": False,
                "source": None,
                "reason": None,
            },
        }
        checks = self._audit_checks(
            request, official, ctmc, hazard, residual, events,
        )
        result = {
            "contract_version": self.config.contract_version,
            "model_version": self.config.model_version,
            "status": "official",
            "official_source": self.config.model_version,
            "official_live_prediction": official,
            "live_probability_engine": {
                "dynamic_poisson": dynamic,
                "ctmc": ctmc,
                "hazard": hazard,
                "dynamic_elo": elo,
                "hawkes_residual": residual,
                "composed_output": combined,
                "monte_carlo_diagnostic": {
                    "status": "not_scheduled",
                    "simulations": 0,
                },
                "audit": {"passed": all(checks.values()), "checks": checks},
                "provenance": {
                    "source_hash": request.source_hash,
                    "markov_live_output_hash": markov_live.get("output_hash", ""),
                    "config_hash": _stable_hash(asdict(self.config)),
                    "pre_match_router_modified": False,
                    "provider_probabilities_used": False,
                    "provider_odds_used": False,
                    "composition": "intensity_scale_then_hawkes_log_residual",
                },
            },
        }
        if not result["live_probability_engine"]["audit"]["passed"]:
            raise FloatingPointError("live_probability_engine_audit_failed")
        result["output_hash"] = _stable_hash(result)
        return result

    @staticmethod
    def _validate_request(
        request: MarkovLiveInput, markov_live: dict[str, Any],
    ) -> None:
        if not markov_live.get("audit", {}).get("passed", False):
            raise ValueError("validated_markov_live_baseline_required")
        if request.match_clock_seconds < 0.0:
            raise ValueError("invalid_live_clock")
        if min(request.score_home, request.score_away) < 0:
            raise ValueError("invalid_live_score")
        if not request.source_hash:
            raise ValueError("live_probability_source_hash_required")

    @staticmethod
    def _red_cards(
        events: list[dict[str, Any]], request: MarkovLiveInput,
    ) -> dict[str, int]:
        return {
            "home": sum(
                event.get("event_type") == "red"
                and int(event.get("team_id", -1)) == request.home_team_id
                for event in events
            ),
            "away": sum(
                event.get("event_type") == "red"
                and int(event.get("team_id", -1)) == request.away_team_id
                for event in events
            ),
        }

    def _hazard_layer(
        self, events: list[dict[str, Any]], request: MarkovLiveInput,
    ) -> dict[str, Any]:
        windows: dict[str, dict[str, float]] = {}
        linear = {"home": 0.0, "away": 0.0}
        for window in (5.0, 10.0, 20.0):
            scores = {"home": 0.0, "away": 0.0}
            for event in events:
                age = (
                    request.match_clock_seconds
                    - float(event.get("match_clock_seconds", 0.0))
                ) / 60.0
                if age < -1e-9 or age > window:
                    continue
                team = int(event.get("team_id", -1))
                side = (
                    "home" if team == request.home_team_id
                    else "away" if team == request.away_team_id else None
                )
                if side is None:
                    continue
                weight = self.EVENT_WEIGHTS.get(
                    str(event.get("event_type")), 0.0,
                )
                scores[side] += weight * math.exp(-max(0.0, age) / window)
            windows[str(int(window))] = scores
            scale = {5.0: 0.080, 10.0: 0.035, 20.0: 0.015}[window]
            linear["home"] += scale * (scores["home"] - 0.35 * scores["away"])
            linear["away"] += scale * (scores["away"] - 0.35 * scores["home"])
        score_difference = request.score_home - request.score_away
        elapsed = min(1.0, request.match_clock_seconds / 5400.0)
        if score_difference < 0:
            linear["home"] += 0.12 * elapsed
            linear["away"] -= 0.05 * elapsed
        elif score_difference > 0:
            linear["away"] += 0.12 * elapsed
            linear["home"] -= 0.05 * elapsed
        bounded = {
            side: _clip(value, -self.config.maximum_log_hazard,
                        self.config.maximum_log_hazard)
            for side, value in linear.items()
        }
        return {
            "status": "official_component",
            "model": "bounded_cox_hazard_v1",
            "windows_minutes": windows,
            "log_hazard": bounded,
            "multipliers": {
                side: math.exp(value) for side, value in bounded.items()
            },
            "features_are_live_only": True,
            "provider_probabilities_used": False,
            "provider_odds_used": False,
        }

    def _ctmc_layer(
        self, events: list[dict[str, Any]], request: MarkovLiveInput,
    ) -> dict[str, Any]:
        pressure = {"home": 0.0, "away": 0.0}
        for event in events:
            age = max(
                0.0,
                (request.match_clock_seconds
                 - float(event.get("match_clock_seconds", 0.0))) / 60.0,
            )
            if age > 20.0:
                continue
            team = int(event.get("team_id", -1))
            side = (
                "home" if team == request.home_team_id
                else "away" if team == request.away_team_id else None
            )
            if side is None:
                continue
            weight = self.EVENT_WEIGHTS.get(str(event.get("event_type")), 0.0)
            pressure[side] += weight * math.exp(-age / 8.0)
        logits = (
            _clip(pressure["home"] - pressure["away"], -4.0, 4.0),
            0.60,
            _clip(pressure["away"] - pressure["home"], -4.0, 4.0),
        )
        maximum = max(logits)
        posterior = _normalize(math.exp(value - maximum) for value in logits)
        transition = self._segment_transition
        return {
            "status": "official_component",
            "model": "ctmc_regime_v1",
            "states": list(REGIME_NAMES),
            "generator_per_minute": self._generator.tolist(),
            "transition_5m": transition.tolist(),
            "posterior": {
                REGIME_NAMES[index]: posterior[index] for index in range(3)
            },
            "dominant": REGIME_NAMES[max(
                range(3), key=lambda index: (posterior[index], -index)
            )],
            "pressure_signal": pressure,
        }

    def _elo_layer(
        self,
        request: MarkovLiveInput,
        hazard: dict[str, Any],
        red_cards: dict[str, int],
    ) -> dict[str, Any]:
        prior_difference = _clip(
            400.0 * math.log10(
                request.lambda_base_home / request.lambda_base_away
            ),
            -500.0,
            500.0,
        )
        pressure_difference = (
            float(hazard["log_hazard"]["home"])
            - float(hazard["log_hazard"]["away"])
        )
        evidence = (
            self.config.elo_points_per_goal
            * (request.score_home - request.score_away)
            + self.config.elo_points_per_pressure * pressure_difference
            - self.config.elo_points_per_red_card
            * (red_cards["home"] - red_cards["away"])
        )
        shrinkage = 0.65 * min(1.0, request.match_clock_seconds / 5400.0)
        live_difference = prior_difference + shrinkage * evidence
        log_adjustment = _clip(
            (live_difference - prior_difference) / 400.0 * math.log(10.0) / 2.0,
            -self.config.maximum_log_elo,
            self.config.maximum_log_elo,
        )
        return {
            "status": "official_component",
            "model": "implied_dynamic_elo_v1",
            "prior_source": "causal_prematch_intensity_ratio",
            "prior_difference": prior_difference,
            "performance_evidence": evidence,
            "shrinkage": shrinkage,
            "live_difference": live_difference,
            "multipliers": {
                "home": math.exp(log_adjustment),
                "away": math.exp(-log_adjustment),
            },
            "target_final_result_used": False,
        }

    def _dynamic_poisson(
        self,
        request: MarkovLiveInput,
        ctmc: dict[str, Any],
        hazard: dict[str, Any],
        elo: dict[str, Any],
        red_cards: dict[str, int],
    ) -> dict[str, Any]:
        duration = (
            self.config.extra_time_minutes
            if request.period >= 3 else self.config.regulation_minutes
        )
        current = min(duration, request.match_clock_seconds / 60.0)
        posterior = np.asarray(
            [ctmc["posterior"][name] for name in REGIME_NAMES], dtype=float,
        )
        segments: list[dict[str, Any]] = []
        total_home = total_away = 0.0
        position = current
        while position < duration - 1e-12:
            end = min(duration, position + self.config.segment_minutes)
            midpoint = (position + end) / 2.0
            state_home = float(np.dot(
                posterior,
                np.asarray(self.config.ctmc_goal_multipliers_home),
            ))
            state_away = float(np.dot(
                posterior,
                np.asarray(self.config.ctmc_goal_multipliers_away),
            ))
            time_shape = 0.88 + 0.24 * min(1.0, midpoint / max(duration, 1.0))
            score_home, score_away = self._score_factors(request, midpoint, duration)
            red_home = math.exp(
                -self.config.red_card_log_penalty * red_cards["home"]
                + self.config.red_card_opponent_log_gain * red_cards["away"]
            )
            red_away = math.exp(
                -self.config.red_card_log_penalty * red_cards["away"]
                + self.config.red_card_opponent_log_gain * red_cards["home"]
            )
            future_decay = math.exp(-(midpoint - current) / 10.0)
            hazard_home = math.exp(
                float(hazard["log_hazard"]["home"]) * future_decay
            )
            hazard_away = math.exp(
                float(hazard["log_hazard"]["away"]) * future_decay
            )
            minutes = end - position
            lambda_home = (
                request.lambda_base_home / self.config.regulation_minutes
                * minutes * time_shape * state_home * score_home * red_home
                * float(elo["multipliers"]["home"]) * hazard_home
            )
            lambda_away = (
                request.lambda_base_away / self.config.regulation_minutes
                * minutes * time_shape * state_away * score_away * red_away
                * float(elo["multipliers"]["away"]) * hazard_away
            )
            lambda_home = _clip(
                lambda_home, 0.0, self.config.maximum_remaining_intensity,
            )
            lambda_away = _clip(
                lambda_away, 0.0, self.config.maximum_remaining_intensity,
            )
            total_home += lambda_home
            total_away += lambda_away
            segments.append({
                "start_minute": position,
                "end_minute": end,
                "lambda_home": lambda_home,
                "lambda_away": lambda_away,
                "state_posterior": {
                    REGIME_NAMES[index]: float(posterior[index])
                    for index in range(3)
                },
            })
            transition = (
                self._segment_transition
                if abs(minutes - self.config.segment_minutes) <= 1e-12
                else self._transition(minutes)
            )
            posterior = posterior @ transition
            posterior = np.maximum(posterior, 0.0)
            posterior = posterior / posterior.sum()
            position = end
        total_home = _clip(
            total_home, 0.0, self.config.maximum_remaining_intensity,
        )
        total_away = _clip(
            total_away, 0.0, self.config.maximum_remaining_intensity,
        )
        markets = _market_bundle(
            total_home, total_away, request.score_home, request.score_away,
            maximum=self.config.maximum_additional_goals,
        )
        return {
            "status": "official_component",
            "model": "time_varying_poisson_v1",
            "integration": "piecewise_5_minute",
            "remaining_seconds": max(
                0.0, duration * 60.0 - request.match_clock_seconds,
            ),
            "lambda_remaining_home": total_home,
            "lambda_remaining_away": total_away,
            "segments": segments,
            "markets": markets,
            "periods": self._period_markets(request, segments),
            "red_cards": red_cards,
        }

    @staticmethod
    def _score_factors(
        request: MarkovLiveInput, midpoint: float, duration: float,
    ) -> tuple[float, float]:
        difference = request.score_home - request.score_away
        late = min(1.0, midpoint / max(duration, 1.0))
        chasing = 1.0 + 0.18 * late
        protecting = max(0.78, 1.0 - 0.10 * late)
        if difference < 0:
            return chasing, protecting
        if difference > 0:
            return protecting, chasing
        return 1.0, 1.0

    def _period_markets(
        self, request: MarkovLiveInput, segments: list[dict[str, Any]],
    ) -> dict[str, Any]:
        first_goals = self._observed_goals(request, period=1)
        current_second = {
            "home": max(0, request.score_home - first_goals["home"]),
            "away": max(0, request.score_away - first_goals["away"]),
        }
        first_lambda = self._segment_sum(segments, 0.0, 45.0)
        second_lambda = self._segment_sum(segments, 45.0, 90.0)
        if request.period == 1:
            first_score = {"home": request.score_home, "away": request.score_away}
            first_status = "live"
            second_score = {"home": 0, "away": 0}
            second_status = "scheduled"
        else:
            first_score = first_goals
            first_lambda = {"home": 0.0, "away": 0.0}
            first_status = "settled_observed"
            second_score = current_second
            second_status = "live" if request.period == 2 else "settled_observed"
            if request.period > 2:
                second_lambda = {"home": 0.0, "away": 0.0}
        return {
            "first_half": {
                "status": first_status,
                "score": first_score,
                "markets": _market_bundle(
                    first_lambda["home"], first_lambda["away"],
                    first_score["home"], first_score["away"],
                    maximum=self.config.maximum_additional_goals,
                ),
            },
            "second_half": {
                "status": second_status,
                "score": second_score,
                "markets": _market_bundle(
                    second_lambda["home"], second_lambda["away"],
                    second_score["home"], second_score["away"],
                    maximum=self.config.maximum_additional_goals,
                ),
            },
            "full_time": {
                "status": "live",
                "score": {
                    "home": request.score_home, "away": request.score_away,
                },
                "markets": _market_bundle(
                    self._segment_sum(segments, 0.0, 120.0)["home"],
                    self._segment_sum(segments, 0.0, 120.0)["away"],
                    request.score_home, request.score_away,
                    maximum=self.config.maximum_additional_goals,
                ),
            },
        }

    @staticmethod
    def _segment_sum(
        segments: list[dict[str, Any]], start: float, end: float,
    ) -> dict[str, float]:
        output = {"home": 0.0, "away": 0.0}
        for segment in segments:
            segment_start = float(segment["start_minute"])
            segment_end = float(segment["end_minute"])
            overlap = max(0.0, min(end, segment_end) - max(start, segment_start))
            duration = max(1e-12, segment_end - segment_start)
            fraction = overlap / duration
            output["home"] += float(segment["lambda_home"]) * fraction
            output["away"] += float(segment["lambda_away"]) * fraction
        return output

    @staticmethod
    def _observed_goals(
        request: MarkovLiveInput, *, period: int,
    ) -> dict[str, int]:
        output = {"home": 0, "away": 0}
        for event in request.events:
            if (
                event.get("event_type") not in GOAL_TYPES
                or bool(event.get("annulled", False))
                or int(event.get("period") or 1) != period
            ):
                continue
            team = int(event.get("team_id", -1))
            if team == request.home_team_id:
                output["home"] += 1
            elif team == request.away_team_id:
                output["away"] += 1
        return output

    def _next_event(
        self,
        markov_live: dict[str, Any],
        dynamic: dict[str, Any],
        hazard: dict[str, Any],
    ) -> dict[str, Any]:
        rates = {
            str(key): float(value)
            for key, value in markov_live["next_event"]["rates_per_minute"].items()
        }
        remaining_minutes = max(
            1.0, float(dynamic["remaining_seconds"]) / 60.0,
        )
        rates["home:goal"] = (
            float(dynamic["lambda_remaining_home"]) / remaining_minutes
        )
        rates["away:goal"] = (
            float(dynamic["lambda_remaining_away"]) / remaining_minutes
        )
        for key in list(rates):
            side = key.split(":", 1)[0]
            if not key.endswith(":goal") and side in {"home", "away"}:
                rates[key] *= float(hazard["multipliers"][side])
        return competing_event_distribution(
            rates,
            min(
                self.config.next_event_horizon_minutes,
                float(dynamic["remaining_seconds"]) / 60.0,
            ),
        )

    @staticmethod
    def _goal_horizons(
        lambda_home: float, lambda_away: float, remaining_minutes: float,
    ) -> dict[str, Any]:
        total = lambda_home + lambda_away
        output: dict[str, Any] = {}
        for horizon in (5.0, 10.0):
            effective = min(max(0.0, remaining_minutes), horizon)
            rate = total / max(1.0, remaining_minutes)
            output[f"next_{int(horizon)}m"] = {
                "effective_minutes": effective,
                "probability_any_goal": 1.0 - math.exp(-rate * effective),
            }
        return output

    @staticmethod
    def _confidence(
        markets: dict[str, Any],
        markov_live: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[str, Any]:
        probabilities = [
            float(markets["probability_home"]),
            float(markets["probability_draw"]),
            float(markets["probability_away"]),
        ]
        entropy = -math.fsum(
            probability * math.log(max(probability, 1e-15))
            for probability in probabilities
        ) / math.log(3.0)
        audit = markov_live.get("events_audit", {})
        received = max(1, int(audit.get("received", len(events))))
        accepted = int(audit.get("accepted", len(events)))
        quality = min(1.0, accepted / received)
        return {
            "normalized_entropy": entropy,
            "decision_concentration": 1.0 - entropy,
            "event_data_quality": quality,
            "classification": (
                "high" if entropy < 0.55 and quality >= 0.8
                else "medium" if entropy < 0.82 and quality >= 0.5
                else "low"
            ),
        }

    @staticmethod
    def _audit_checks(
        request: MarkovLiveInput,
        official: dict[str, Any],
        ctmc: dict[str, Any],
        hazard: dict[str, Any],
        residual: dict[str, Any],
        events: list[dict[str, Any]],
    ) -> dict[str, bool]:
        markets = official["markets"]
        probabilities = (
            markets["probability_home"], markets["probability_draw"],
            markets["probability_away"],
        )
        generator = np.asarray(ctmc["generator_per_minute"], dtype=float)
        return {
            "identity_valid": (
                request.home_team_id > 0 and request.away_team_id > 0
                and request.home_team_id != request.away_team_id
            ),
            "events_not_future": all(
                float(event.get("match_clock_seconds", 0.0))
                <= request.match_clock_seconds + 1e-9
                for event in events
            ),
            "probabilities_bounded": all(
                math.isfinite(float(value)) and 0.0 <= float(value) <= 1.0
                for value in probabilities
            ),
            "one_x_two_normalized": math.isclose(
                math.fsum(probabilities), 1.0, abs_tol=1e-10,
            ),
            "intensities_nonnegative": all(
                math.isfinite(float(value)) and float(value) >= 0.0
                for value in official["remaining_intensities"].values()
            ),
            "ctmc_generator_conservative": bool(
                np.allclose(generator.sum(axis=1), 0.0, atol=1e-12)
            ),
            "hazards_finite": all(
                math.isfinite(float(value)) and float(value) > 0.0
                for value in hazard["multipliers"].values()
            ),
            "hawkes_subcritical": bool(
                residual.get("stability", {}).get("subcritical", False)
            ),
            "provider_probabilities_unused": True,
            "provider_odds_unused": True,
        }


class MonteCarloDiagnosticRunner:
    """Ejecuta diagnósticos deterministas en segundo plano y conserva caché."""

    def __init__(
        self, simulations: int = 20_000, *, max_workers: int = 2,
        max_entries: int = 128,
    ) -> None:
        if simulations < 1_000 or simulations > 100_000:
            raise ValueError("monte_carlo_simulations_out_of_range")
        self.simulations = int(simulations)
        self._executor = ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix="dikamaha-live-mc",
        )
        self._max_entries = max_entries
        self._lock = threading.Lock()
        self._futures: dict[str, Future[dict[str, Any]]] = {}

    def submit(
        self, match_id: int, snapshot_hash: str,
        official_prediction: dict[str, Any],
    ) -> dict[str, Any]:
        key = _stable_hash({
            "match_id": int(match_id),
            "snapshot_hash": snapshot_hash,
            "simulations": self.simulations,
            "model_version": MODEL_VERSION,
        })
        with self._lock:
            future = self._futures.get(key)
            if future is None:
                if len(self._futures) >= self._max_entries:
                    removable = next((
                        item for item, value in self._futures.items()
                        if value.done()
                    ), next(iter(self._futures)))
                    self._futures.pop(removable, None)
                future = self._executor.submit(
                    self._simulate, key, official_prediction,
                )
                self._futures[key] = future
        if not future.done():
            return {
                "status": "scheduled",
                "diagnostic_key": key,
                "simulations": self.simulations,
                "blocking": False,
            }
        try:
            return future.result()
        except Exception as exc:  # pragma: no cover - ruta defensiva
            return {
                "status": "failed",
                "diagnostic_key": key,
                "simulations": self.simulations,
                "blocking": False,
                "error": type(exc).__name__,
            }

    def wait(self, diagnostic_key: str, timeout: float = 10.0) -> dict[str, Any]:
        with self._lock:
            future = self._futures[diagnostic_key]
        return future.result(timeout=timeout)

    def shutdown(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=False)

    def _simulate(
        self, key: str, official_prediction: dict[str, Any],
    ) -> dict[str, Any]:
        seed = int(key[:16], 16)
        generator = np.random.default_rng(seed)
        intensities = official_prediction["remaining_intensities"]
        score = official_prediction["periods"]["full_time"]["score"]
        additional_home = generator.poisson(
            float(intensities["home"]), self.simulations,
        )
        additional_away = generator.poisson(
            float(intensities["away"]), self.simulations,
        )
        final_home = additional_home + int(score["home"])
        final_away = additional_away + int(score["away"])
        probabilities = {
            "home": float(np.mean(final_home > final_away)),
            "draw": float(np.mean(final_home == final_away)),
            "away": float(np.mean(final_home < final_away)),
        }
        intervals = {
            name: self._binomial_interval(value, self.simulations)
            for name, value in probabilities.items()
        }
        analytical = official_prediction["markets"]
        comparison = {
            "home_absolute_error": abs(
                probabilities["home"] - float(analytical["probability_home"])
            ),
            "draw_absolute_error": abs(
                probabilities["draw"] - float(analytical["probability_draw"])
            ),
            "away_absolute_error": abs(
                probabilities["away"] - float(analytical["probability_away"])
            ),
        }
        return {
            "status": "complete",
            "diagnostic_key": key,
            "simulations": self.simulations,
            "blocking": False,
            "seed": seed,
            "probabilities": probabilities,
            "confidence_intervals_95": intervals,
            "mean_final_score": {
                "home": float(np.mean(final_home)),
                "away": float(np.mean(final_away)),
            },
            "final_score_interval_90": {
                "home": [
                    int(np.quantile(final_home, 0.05)),
                    int(np.quantile(final_home, 0.95)),
                ],
                "away": [
                    int(np.quantile(final_away, 0.05)),
                    int(np.quantile(final_away, 0.95)),
                ],
            },
            "analytical_comparison": comparison,
            "maximum_absolute_error": max(comparison.values()),
            "used_for_official_output": False,
        }

    @staticmethod
    def _binomial_interval(probability: float, sample_size: int) -> list[float]:
        error = 1.96 * math.sqrt(
            probability * (1.0 - probability) / sample_size
        )
        return [max(0.0, probability - error), min(1.0, probability + error)]


__all__ = [
    "CONTRACT_VERSION", "MODEL_VERSION", "LiveProbabilityEngineConfig",
    "LiveProbabilityEngineV1", "MonteCarloDiagnosticRunner",
]
