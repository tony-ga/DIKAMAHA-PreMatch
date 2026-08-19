"""Filtro Markov causal para inferencia in-play en modo shadow.

El modelo parte de lambdas pre-match y actualiza un posterior de régimen con
eventos observados hasta el snapshot. No entrena ni consulta fuentes externas.

Version: 1.0.0
Created: 2026-08-07
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


STATE_NAMES = ("balanced", "home_pressure", "away_pressure", "chaotic")
MODEL_EVENT_TYPES = frozenset({
    "goal", "shot_on_target", "shot_off_target", "shot_blocked", "corner",
    "foul", "yellow", "red", "substitution", "penalty_awarded",
    "penalty_scored",
})
# Tipo auxiliar que puede proyectarse a un tipo modelable (`DEC-217`). NO se
# añade a `MODEL_EVENT_TYPES`: la cadena está validada sobre 7,400 partidos
# con ese conjunto exacto, y ampliarlo cambiaría el posterior de régimen de
# todos ellos. La proyección ocurre en el borde, antes del filtro.
DERIVED_SAVE_TYPE = "save"


def _is_save(event: dict[str, Any], event_type: str) -> bool:
    """Reconoce un `save` venga como tipo canónico o como tipo crudo.

    En producción el follower (`src/espn_live_follower.py`) emite el par
    `event_type="auxiliary"` / `event_type_raw="save"`, porque la taxonomía
    colapsa todos los auxiliares en una sola etiqueta canónica. Mirar sólo
    `event_type` haría que la proyección jamás se disparase con datos reales
    aunque el flag estuviera activo -un fallo silencioso, sin excepción ni
    registro-, así que el tipo crudo es la señal primaria y el canónico sólo
    el respaldo para llamadores que ya normalizan por su cuenta.
    """

    raw = str(event.get("event_type_raw") or "").strip().lower()
    if raw:
        return raw.replace("-", "_") == DERIVED_SAVE_TYPE
    return event_type == DERIVED_SAVE_TYPE


def _stable_hash(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _parse_ts(value: str) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone_required")
    return parsed.astimezone(timezone.utc)


def _normalize(values: Iterable[float]) -> tuple[float, ...]:
    output = tuple(float(value) for value in values)
    if not output or any(not math.isfinite(value) or value < 0.0 for value in output):
        raise ValueError("invalid_probability_vector")
    total = math.fsum(output)
    if total <= 0.0:
        raise ValueError("zero_probability_mass")
    normalized = tuple(value / total for value in output)
    if not math.isclose(math.fsum(normalized), 1.0, abs_tol=1e-12):
        raise FloatingPointError("probability_vector_not_normalized")
    return normalized


def _poisson_pmf(rate: float, maximum: int = 14) -> tuple[float, ...]:
    if not math.isfinite(rate) or rate < 0.0:
        raise ValueError("invalid_poisson_rate")
    values = [math.exp(-rate)]
    for count in range(1, maximum + 1):
        values.append(values[-1] * rate / count)
    values[-1] += max(0.0, 1.0 - math.fsum(values))
    return _normalize(values)


def remaining_goal_markets(
    lambda_home: float,
    lambda_away: float,
    score_home: int,
    score_away: int,
    *,
    max_additional_goals: int = 14,
) -> dict[str, float]:
    """Deriva mercados finales desde goles restantes Poisson independientes."""

    if min(score_home, score_away) < 0:
        raise ValueError("negative_score")
    home_pmf = _poisson_pmf(lambda_home, max_additional_goals)
    away_pmf = _poisson_pmf(lambda_away, max_additional_goals)
    home = draw = away = over = btts = 0.0
    for home_goals, probability_home in enumerate(home_pmf):
        for away_goals, probability_away in enumerate(away_pmf):
            probability = probability_home * probability_away
            final_home = score_home + home_goals
            final_away = score_away + away_goals
            if final_home > final_away:
                home += probability
            elif final_home == final_away:
                draw += probability
            else:
                away += probability
            if final_home + final_away > 2:
                over += probability
            if final_home > 0 and final_away > 0:
                btts += probability
    one_x_two = _normalize((home, draw, away))
    return {
        "probability_home": one_x_two[0],
        "probability_draw": one_x_two[1],
        "probability_away": one_x_two[2],
        "probability_over_2_5": min(1.0, max(0.0, over)),
        "probability_btts": min(1.0, max(0.0, btts)),
    }


def competing_event_distribution(
    rates_per_minute: dict[str, float], horizon_minutes: float,
) -> dict[str, Any]:
    """Convierte hazards constantes en probabilidades de riesgos competitivos."""

    if not math.isfinite(horizon_minutes) or horizon_minutes < 0.0:
        raise ValueError("invalid_event_horizon")
    ordered = dict(sorted((str(key), float(value)) for key, value in rates_per_minute.items()))
    if any(not math.isfinite(value) or value < 0.0 for value in ordered.values()):
        raise ValueError("invalid_event_hazard")
    total = math.fsum(ordered.values())
    if total == 0.0:
        probabilities = {key: 0.0 for key in ordered}
        no_event = 1.0
    else:
        event_mass = 1.0 - math.exp(-total * horizon_minutes)
        probabilities = {key: value / total * event_mass for key, value in ordered.items()}
        no_event = math.exp(-total * horizon_minutes)
    normalization_error = abs(math.fsum(probabilities.values()) + no_event - 1.0)
    if normalization_error > 1e-10:
        raise FloatingPointError("next_event_probabilities_not_normalized")
    return {
        "horizon_minutes": float(horizon_minutes),
        "rates_per_minute": ordered,
        "probabilities": probabilities,
        "probability_no_event": no_event,
        "normalization_error": normalization_error,
    }


@dataclass(frozen=True, slots=True)
class MarkovLiveConfig:
    """Parámetros congelados de la primera implementación shadow."""

    model_version: str = "markov_live_v1_shadow"
    regulation_minutes: float = 90.0
    extra_time_minutes: float = 120.0
    transition_step_minutes: float = 5.0
    minimum_intensity: float = 1e-8
    maximum_remaining_intensity: float = 12.0
    horizon_minutes: float = 5.0
    stoppage_live_tail_seconds: float = 60.0
    prior: tuple[float, float, float, float] = (0.55, 0.18, 0.18, 0.09)
    transition_matrix: tuple[tuple[float, ...], ...] = (
        (0.78, 0.08, 0.08, 0.06),
        (0.14, 0.70, 0.04, 0.12),
        (0.14, 0.04, 0.70, 0.12),
        (0.18, 0.15, 0.15, 0.52),
    )
    state_goal_multipliers_home: tuple[float, ...] = (1.0, 1.24, 0.86, 1.15)
    state_goal_multipliers_away: tuple[float, ...] = (1.0, 0.86, 1.24, 1.15)
    # Proyección de `save` a tiro a puerta (`DEC-217`). Desactivada por
    # defecto: activarla cambia el conjunto de eventos que alimenta la cadena,
    # y esta cadena está validada sobre 7,400 partidos/34 ligas con el
    # conjunto histórico. Ver `artifacts/phase_116b_save_attribution_audit/`.
    enable_derived_save_projection: bool = False
    # Ventana para declarar que un `save` y un `shot_on_target` son la misma
    # acción. 5 s es donde la curva de solapamiento medida sobre el feed real
    # se estabiliza: por debajo quedan duplicados reales sin emparejar, por
    # encima se empiezan a borrar tiros distintos.
    derived_save_dedup_seconds: float = 5.0


@dataclass(frozen=True, slots=True)
class MarkovLiveInput:
    """Snapshot causal mínimo requerido por el filtro."""

    match_id: int
    home_team_id: int
    away_team_id: int
    kickoff_ts: str
    snapshot_ts: str
    match_clock_seconds: float
    period: int
    score_home: int
    score_away: int
    lambda_base_home: float
    lambda_base_away: float
    events: tuple[dict[str, Any], ...] = ()
    league_slug: str = ""
    source_hash: str = ""


class MarkovLiveV1:
    """Filtro Bayesiano Markov observable, determinista y fail-closed."""

    EVENT_WEIGHTS = {
        "goal": 1.60,
        "penalty_scored": 1.60,
        "penalty_awarded": 1.35,
        "shot_on_target": 1.00,
        "shot_blocked": 0.65,
        "shot_off_target": 0.45,
        "corner": 0.50,
        "red": 1.20,
        "yellow": 0.25,
        "foul": 0.15,
        "substitution": 0.10,
    }

    BASE_EVENT_RATES = {
        "shot_on_target": 0.070,
        "corner": 0.055,
        "card": 0.030,
        "substitution": 0.024,
    }

    def __init__(self, config: MarkovLiveConfig | None = None) -> None:
        self.config = config or MarkovLiveConfig()
        self._validate_config()

    def _validate_config(self) -> None:
        matrix = self.config.transition_matrix
        if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
            raise ValueError("markov_live_transition_must_be_4x4")
        for row in matrix:
            if any(not math.isfinite(value) or value < 0.0 for value in row):
                raise ValueError("invalid_markov_live_transition")
            if not math.isclose(math.fsum(row), 1.0, abs_tol=1e-12):
                raise ValueError("markov_live_transition_not_normalized")
        _normalize(self.config.prior)
        if self.config.transition_step_minutes <= 0.0:
            raise ValueError("invalid_markov_live_time_scale")
        if self.config.stoppage_live_tail_seconds <= 0.0:
            raise ValueError("invalid_markov_live_stoppage_tail")

    def predict(self, request: MarkovLiveInput) -> dict[str, Any]:
        """Actualiza estado e intensidades usando sólo evidencia hasta el corte."""

        self._validate_input(request)
        events, audit = self._canonical_events(request)
        self._validate_score_reconciliation(request, events)
        posterior = tuple(self.config.prior)
        step_seconds = self.config.transition_step_minutes * 60.0
        previous_step = 0
        for event in events:
            event_clock = float(event["match_clock_seconds"])
            event_step = int(event_clock // step_seconds)
            posterior = self._propagate_steps(posterior, event_step - previous_step)
            posterior = self._observe(posterior, event, request)
            previous_step = event_step
        snapshot_step = int(request.match_clock_seconds // step_seconds)
        posterior = self._propagate_steps(posterior, snapshot_step - previous_step)
        duration_seconds = self._duration_seconds(request.period, request.match_clock_seconds)
        remaining_seconds = max(0.0, duration_seconds - request.match_clock_seconds)
        remaining_fraction = remaining_seconds / duration_seconds
        state_home = self._state_multiplier(posterior, self.config.state_goal_multipliers_home, home=True)
        state_away = self._state_multiplier(posterior, self.config.state_goal_multipliers_away, home=False)
        score_home, score_away = self._score_multipliers(request, duration_seconds)
        lambda_home = self._bounded_intensity(request.lambda_base_home * remaining_fraction * state_home * score_home)
        lambda_away = self._bounded_intensity(request.lambda_base_away * remaining_fraction * state_away * score_away)
        markets = remaining_goal_markets(lambda_home, lambda_away, request.score_home, request.score_away)
        hazards = self._event_hazards(lambda_home, lambda_away, remaining_seconds, state_home, state_away)
        next_event = competing_event_distribution(
            hazards,
            min(self.config.horizon_minutes, remaining_seconds / 60.0),
        )
        dominant = max(range(len(posterior)), key=lambda index: (posterior[index], -index))
        checks = {
            "snapshot_after_kickoff": _parse_ts(request.snapshot_ts) >= _parse_ts(request.kickoff_ts),
            "events_not_future": audit["future_events"] == 0,
            "clock_monotonic": audit["clock_regressions"] == 0,
            "score_reconciled": True,
            "posterior_normalized": math.isclose(math.fsum(posterior), 1.0, abs_tol=1e-12),
            "remaining_time_nonnegative": remaining_seconds >= 0.0,
            "intensities_positive_finite": all(math.isfinite(value) and value >= 0.0 for value in (lambda_home, lambda_away)),
            "markets_normalized": math.isclose(markets["probability_home"] + markets["probability_draw"] + markets["probability_away"], 1.0, abs_tol=1e-10),
            "next_event_normalized": next_event["normalization_error"] <= 1e-10,
        }
        output = {
            "status": "experimental_shadow_not_promoted",
            "model_version": self.config.model_version,
            "match_id": request.match_id,
            "snapshot_ts": request.snapshot_ts,
            "match_clock_seconds": float(request.match_clock_seconds),
            "remaining_seconds": remaining_seconds,
            "state": {
                "dominant": STATE_NAMES[dominant],
                "posterior": {STATE_NAMES[index]: posterior[index] for index in range(4)},
            },
            "lambda_remaining_home": lambda_home,
            "lambda_remaining_away": lambda_away,
            "markets": markets,
            "next_event": next_event,
            "events_used": events,
            "events_audit": audit,
            "provenance": {
                "source_hash": request.source_hash,
                "config_hash": _stable_hash(asdict(self.config)),
                "parameters_calibrated": False,
                "pre_match_router_modified": False,
            },
            "audit": {"passed": all(checks.values()), "checks": checks},
        }
        output["output_hash"] = _stable_hash(output)
        return output

    def _validate_input(self, request: MarkovLiveInput) -> None:
        if request.match_id <= 0 or request.home_team_id <= 0 or request.away_team_id <= 0:
            raise ValueError("invalid_live_identity")
        if request.home_team_id == request.away_team_id:
            raise ValueError("live_teams_must_differ")
        if _parse_ts(request.snapshot_ts) < _parse_ts(request.kickoff_ts):
            raise ValueError("snapshot_before_kickoff")
        if not math.isfinite(request.match_clock_seconds) or request.match_clock_seconds < 0.0:
            raise ValueError("invalid_match_clock")
        if request.period < 1 or request.period > 5:
            raise ValueError("invalid_match_period")
        period_minimum = {2: 45.0, 3: 90.0, 4: 105.0, 5: 120.0}.get(request.period)
        if period_minimum is not None and request.match_clock_seconds < period_minimum * 60.0:
            raise ValueError("match_clock_not_period_aware")
        if min(request.score_home, request.score_away) < 0:
            raise ValueError("negative_score")
        for value in (request.lambda_base_home, request.lambda_base_away):
            if not math.isfinite(value) or value <= 0.0:
                raise ValueError("invalid_base_intensity")

    def _canonical_events(self, request: MarkovLiveInput) -> tuple[list[dict[str, Any]], dict[str, int]]:
        accepted: list[dict[str, Any]] = []
        seen: set[str] = set()
        audit = {
            "received": len(request.events), "accepted": 0, "duplicates": 0,
            "future_events": 0, "annulled": 0, "unmodelled": 0,
            "missing_clock": 0, "missing_team": 0, "clock_regressions": 0,
        }
        previous_clock = -1.0
        candidates: list[dict[str, Any]] = []
        for raw in request.events:
            event = dict(raw)
            clock = event.get("match_clock_seconds")
            if clock is None and event.get("event_ts"):
                clock = (_parse_ts(str(event["event_ts"])) - _parse_ts(request.kickoff_ts)).total_seconds()
            if not isinstance(clock, (int, float)) or not math.isfinite(float(clock)) or float(clock) < 0.0:
                audit["missing_clock"] += 1
                continue
            event["match_clock_seconds"] = float(clock)
            candidates.append(event)
        candidates.sort(key=lambda row: (float(row["match_clock_seconds"]), str(row.get("event_id", ""))))
        for event in candidates:
            clock = float(event["match_clock_seconds"])
            if clock > request.match_clock_seconds + 1e-9:
                raise ValueError("future_event_after_live_snapshot")
            try:
                event_period = int(event.get("period") or request.period)
            except (TypeError, ValueError) as exc:
                raise ValueError("invalid_live_event_period") from exc
            if event_period < 1 or event_period > 5:
                raise ValueError("invalid_live_event_period")
            if event_period > request.period:
                raise ValueError("future_event_period_after_live_snapshot")
            if clock < previous_clock:
                audit["clock_regressions"] += 1
                continue
            previous_clock = clock
            event_type = str(event.get("event_type", "unclassified"))
            if bool(event.get("annulled", False)):
                audit["annulled"] += 1
                continue
            source_event_type = event_type
            team_id = event.get("team_id")
            if (self.config.enable_derived_save_projection
                    and _is_save(event, event_type)):
                source_event_type = DERIVED_SAVE_TYPE
                projected = self._project_save(
                    event, request, accepted, audit)
                if projected is None:
                    continue
                event_type, team_id = projected
            if event_type not in MODEL_EVENT_TYPES:
                audit["unmodelled"] += 1
                continue
            if team_id not in {request.home_team_id, request.away_team_id}:
                audit["missing_team"] += 1
                continue
            event_id = str(event.get("event_id") or "").strip()
            key = event_id or _stable_hash({
                "clock": clock, "event_type": event_type, "team_id": team_id,
                "period": event.get("period"), "text": event.get("text"),
            })
            if key in seen:
                audit["duplicates"] += 1
                continue
            seen.add(key)
            row = {
                "event_id": key,
                "event_type": event_type,
                "team_id": int(team_id),
                "period": event_period,
                "match_clock_seconds": clock,
            }
            if source_event_type != event_type:
                # Conserva de qué evento crudo salió esta fila. Sin esto, una
                # proyección sería indistinguible de un tiro reportado como
                # tal por el proveedor, y la auditoría no podría separarlas.
                row["source_event_type"] = source_event_type
            accepted.append(row)
        audit["accepted"] = len(accepted)
        return accepted, audit

    def _project_save(
        self,
        event: dict[str, Any],
        request: MarkovLiveInput,
        accepted: list[dict[str, Any]],
        audit: dict[str, int],
    ) -> tuple[str, int] | None:
        """Convierte un `save` en el tiro a puerta que necesariamente lo causó.

        Dos correcciones obligatorias, ambas medidas sobre el feed real en
        `artifacts/phase_116b_save_attribution_audit/`:

        1. **El equipo se invierte.** ESPN atribuye el `save` al equipo del
           portero -el que defiende-, así que el tirador es el rival. Sin
           invertir, la proyección sumaría tiros al lado equivocado.
        2. **Se deduplica.** El 43% de los `save` coexisten con un
           `shot_on_target` del proveedor para la misma acción; proyectarlos
           todos inflaría los tiros a puerta en vez de corregir el subconteo.

        Devuelve ``None`` cuando el evento debe descartarse.
        """

        team_id = event.get("team_id")
        if team_id == request.home_team_id:
            shooter = request.away_team_id
        elif team_id == request.away_team_id:
            shooter = request.home_team_id
        else:
            audit["missing_team"] += 1
            return None

        clock = float(event["match_clock_seconds"])
        window = float(self.config.derived_save_dedup_seconds)
        for previous in accepted:
            if previous["event_type"] != "shot_on_target":
                continue
            if previous["team_id"] != shooter:
                continue
            if abs(float(previous["match_clock_seconds"]) - clock) <= window:
                audit["derived_duplicates"] = (
                    audit.get("derived_duplicates", 0) + 1)
                return None

        audit["derived_projected"] = audit.get("derived_projected", 0) + 1
        return "shot_on_target", int(shooter)

    def _propagate_steps(self, posterior: tuple[float, ...], steps: int) -> tuple[float, ...]:
        if steps < 0:
            raise ValueError("event_clock_regression")
        output = posterior
        for _ in range(steps):
            output = _normalize(
                math.fsum(output[row] * self.config.transition_matrix[row][column] for row in range(4))
                for column in range(4)
            )
        return output

    @staticmethod
    def _validate_score_reconciliation(
        request: MarkovLiveInput, events: list[dict[str, Any]],
    ) -> None:
        """Exige que los goles válidos del timeline reproduzcan el marcador."""

        goal_types = {"goal", "penalty_scored"}
        home = sum(
            event["event_type"] in goal_types and event["team_id"] == request.home_team_id
            for event in events
        )
        away = sum(
            event["event_type"] in goal_types and event["team_id"] == request.away_team_id
            for event in events
        )
        if home != request.score_home or away != request.score_away:
            raise ValueError("live_score_play_by_play_mismatch")

    def _observe(
        self, posterior: tuple[float, ...], event: dict[str, Any], request: MarkovLiveInput,
    ) -> tuple[float, ...]:
        event_type = str(event["event_type"])
        weight = self.EVENT_WEIGHTS[event_type]
        is_home = int(event["team_id"]) == request.home_team_id
        if event_type == "red":
            is_home = not is_home
        pressure_home = weight if is_home else -0.45 * weight
        pressure_away = weight if not is_home else -0.45 * weight
        chaos = weight * (0.65 if event_type in {"goal", "penalty_scored", "red", "penalty_awarded"} else 0.20)
        likelihood = (
            1.0,
            math.exp(max(-2.0, min(2.0, pressure_home))),
            math.exp(max(-2.0, min(2.0, pressure_away))),
            math.exp(max(0.0, min(2.0, chaos))),
        )
        return _normalize(posterior[index] * likelihood[index] for index in range(4))

    def _state_multiplier(self, posterior: tuple[float, ...], multipliers: tuple[float, ...], *, home: bool) -> float:
        current = math.fsum(posterior[index] * multipliers[index] for index in range(4))
        prior_home = self.config.state_goal_multipliers_home if home else self.config.state_goal_multipliers_away
        anchor = math.fsum(self.config.prior[index] * prior_home[index] for index in range(4))
        return current / anchor

    @staticmethod
    def _score_multipliers(request: MarkovLiveInput, duration_seconds: float) -> tuple[float, float]:
        difference = request.score_home - request.score_away
        late = min(1.0, max(0.0, request.match_clock_seconds / duration_seconds))
        chasing = 1.0 + 0.15 * late
        protecting = 1.0 - 0.07 * late
        if difference < 0:
            return chasing, protecting
        if difference > 0:
            return protecting, chasing
        return 1.0, 1.0

    def _bounded_intensity(self, value: float) -> float:
        if not math.isfinite(value):
            raise FloatingPointError("non_finite_markov_live_intensity")
        return min(self.config.maximum_remaining_intensity, max(0.0, value))

    def _event_hazards(
        self,
        lambda_home: float,
        lambda_away: float,
        remaining_seconds: float,
        state_home: float,
        state_away: float,
    ) -> dict[str, float]:
        if remaining_seconds <= 0.0:
            return {
                "home:goal": 0.0,
                "away:goal": 0.0,
                **{
                    f"{side}:{event_type}": 0.0
                    for side in ("home", "away")
                    for event_type in self.BASE_EVENT_RATES
                },
            }
        remaining_minutes = max(1.0, remaining_seconds / 60.0)
        rates = {
            "home:goal": lambda_home / remaining_minutes,
            "away:goal": lambda_away / remaining_minutes,
        }
        for side, multiplier in (("home", state_home), ("away", state_away)):
            for event_type, base_rate in self.BASE_EVENT_RATES.items():
                rates[f"{side}:{event_type}"] = max(0.0, base_rate * multiplier)
        return rates

    def _duration_seconds(self, period: int, match_clock_seconds: float) -> float:
        extra_time = period >= 3
        minutes = self.config.extra_time_minutes if extra_time else self.config.regulation_minutes
        scheduled = minutes * 60.0
        if period in {2, 4} and match_clock_seconds > scheduled:
            return match_clock_seconds + self.config.stoppage_live_tail_seconds
        return scheduled


__all__ = [
    "MarkovLiveConfig", "MarkovLiveInput", "MarkovLiveV1",
    "competing_event_distribution", "remaining_goal_markets",
]
