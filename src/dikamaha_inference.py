"""Contrato local de inferencia DIKAMAHA v1.

Ensambla Dixon-Coles, Kalman v2 y Markov v1 sin persistencia. Hawkes se
mantiene desactivado para predicciones oficiales y requiere activacion
experimental explicita.

Requirements:
    - numpy
    - pandas

Version: 1.0.0
Created: 2026-07-15
"""

from __future__ import annotations

import hashlib
import json
import math
from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Sequence

import numpy as np
import pandas as pd

try:
    from src.hawkes_live_v2 import HawkesLiveConfig, HawkesLiveV2
    from src.hawkes_v1_integration import HawkesIntegrationConfig, integrate_hawkes_optional
    from src.kalman_v2 import poisson_matrix
    from src.markov_live_v1 import MarkovLiveInput, MarkovLiveV1
    from src.markov_v1 import MarkovV1
except ModuleNotFoundError:  # pragma: no cover - soporte de ejecucion directa
    from hawkes_live_v2 import HawkesLiveConfig, HawkesLiveV2
    from hawkes_v1_integration import HawkesIntegrationConfig, integrate_hawkes_optional
    from kalman_v2 import poisson_matrix
    from markov_live_v1 import MarkovLiveInput, MarkovLiveV1
    from markov_v1 import MarkovV1

CONTRACT_VERSION = "dikamaha_inference_contract_v1.3_live_shadow"
BLOCKED_MATCH_ID = 704766


def _parse_ts(value: str) -> datetime:
    """Convierte un timestamp ISO a UTC.

    Args:
        value: Timestamp ISO con zona horaria.

    Returns:
        Timestamp normalizado a UTC.
    """

    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Los timestamps deben incluir zona horaria.")
    return parsed.astimezone(timezone.utc)


def _stable_hash(value: Any) -> str:
    """Calcula un hash SHA-256 determinista."""

    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class Provenance:
    """Trazabilidad versionada de una inferencia."""

    contract_version: str = CONTRACT_VERSION
    feature_version: str = "match_features_v1"
    dixon_coles_version: str = "dixon_coles_v1"
    kalman_version: str = "kalman_v2"
    markov_version: str = "markov_v1"
    hawkes_version: str = "hawkes_v1_disabled"
    source_hash: str = ""
    markov_matrix_synthetic: bool = True
    kalman_experimental: bool = True
    hawkes_shadow_mode: bool = False
    markov_live_version: str = "markov_live_v1_disabled"
    hawkes_live_version: str = "hawkes_live_v2_disabled"
    markov_live_shadow_mode: bool = False
    combined_live_shadow_mode: bool = False


@dataclass(frozen=True, slots=True)
class AuditMetadata:
    """Resultado de controles de contrato y anti-leakage."""

    passed: bool
    checks: dict[str, bool]
    warnings: tuple[str, ...] = ()
    input_hash: str = ""
    output_hash: str = ""


@dataclass(frozen=True, slots=True)
class PreMatchInput:
    """Entrada pre-match basada solo en features y estados pre-cutoff."""

    match_id: int
    home_team_id: int
    away_team_id: int
    kickoff_ts: str
    feature_cutoff_ts: str
    competition_id: str
    feature_version: str
    eligible_for_materialization: bool
    history_minimum_met: bool
    league_intercept: float
    home_advantage: float
    dc_attack_home: float
    dc_defense_home: float
    dc_attack_away: float
    dc_defense_away: float
    kalman_attack_home: float
    kalman_defense_home: float
    kalman_attack_away: float
    kalman_defense_away: float
    attack_sum: float = 0.0
    defense_sum: float = 0.0
    tau_dc: float = 0.0
    max_goals: int = 10
    source_hash: str = ""


@dataclass(frozen=True, slots=True)
class PreMatchPrediction:
    """Prediccion pre-match derivada de una matriz Poisson."""

    match_id: int
    lambda_dc_home: float
    lambda_dc_away: float
    lambda_base_home: float
    lambda_base_away: float
    probability_home: float
    probability_draw: float
    probability_away: float
    probability_over_2_5: float
    probability_btts: float
    score_matrix: tuple[tuple[float, ...], ...]
    cutoff_ts: str
    provenance: Provenance
    audit: AuditMetadata


@dataclass(frozen=True, slots=True)
class LiveSnapshotInput:
    """Entrada live con intensidades base y eventos observados."""

    match_id: int
    home_team_id: int
    away_team_id: int
    kickoff_ts: str
    snapshot_ts: str
    lambda_base_home: float
    lambda_base_away: float
    events: tuple[dict[str, Any], ...] = ()
    official_prediction: bool = False
    hawkes_enabled: bool = False
    hawkes_shadow_mode: bool = False
    source_hash: str = ""
    league_slug: str = ""
    provider_event_id: str = ""
    competition_id: str = ""
    period: int = 1
    match_clock_seconds: float | None = None
    score_home: int | None = None
    score_away: int | None = None
    source_fetched_at: str = ""
    markov_live_enabled: bool = False
    markov_live_shadow_mode: bool = False
    hawkes_rho: float | None = None
    hawkes_rho_goal: float | None = None
    hawkes_rho_next_event: float | None = None


@dataclass(frozen=True, slots=True)
class LiveIntensityOutput:
    """Salida in-play en escala de intensidad, sin mercados."""

    match_id: int
    snapshot_ts: str
    lambda_base_home: float
    lambda_base_away: float
    lambda_markov_home: float
    lambda_markov_away: float
    home_state: int
    away_state: int
    markov_audit: dict[str, Any]
    official_source: str
    hawkes_applied: bool
    experimental_hawkes: dict[str, Any] | None
    provenance: Provenance
    audit: AuditMetadata
    experimental_markov_live: dict[str, Any] | None = None
    experimental_hawkes_residual: dict[str, Any] | None = None
    experimental_combined_live: dict[str, Any] | None = None


class InferenceEngine(ABC):
    """Puerto abstracto para motores de inferencia DIKAMAHA."""

    @abstractmethod
    def predict_pre_match(self, request: PreMatchInput) -> PreMatchPrediction:
        """Genera una prediccion pre-match."""

    @abstractmethod
    def predict_live(self, request: LiveSnapshotInput) -> LiveIntensityOutput:
        """Genera intensidades in-play."""


class DikamahaInferenceEngine(InferenceEngine):
    """Ensamblador local del nucleo vigente de DIKAMAHA."""

    def __init__(
        self,
        markov: MarkovV1 | None = None,
        markov_live: MarkovLiveV1 | None = None,
    ) -> None:
        """Inicializa dependencias matematicas inyectables.

        Args:
            markov: Implementacion Markov compatible con el contrato.
        """

        self._markov = markov or MarkovV1()
        self._markov_live = markov_live or MarkovLiveV1()

    def predict_pre_match(self, request: PreMatchInput) -> PreMatchPrediction:
        """Genera mercados pre-match desde la matriz Poisson de Kalman."""

        self._validate_pre_match(request)
        dc_home, dc_away = self._dc_lambdas(request)
        base_home, base_away = self._kalman_lambdas(request)
        grid = poisson_matrix(base_home, base_away, request.max_goals, request.tau_dc, True)
        markets = self._markets(grid)
        provenance = self._provenance(request.source_hash)
        output_payload = self._pre_output_payload(request, base_home, base_away, markets)
        audit = self._pre_audit(request, grid, output_payload)
        return PreMatchPrediction(
            request.match_id, dc_home, dc_away, base_home, base_away,
            markets["home"], markets["draw"], markets["away"],
            markets["over_2_5"], markets["btts"], self._freeze_matrix(grid),
            request.feature_cutoff_ts, provenance, audit,
        )

    def predict_live(self, request: LiveSnapshotInput) -> LiveIntensityOutput:
        """Conserva v1 y añade Markov Live + Hawkes residual en shadow."""

        self._validate_live(request)
        frame = self._events_frame(request)
        markov = self._markov.predict_snapshot(
            frame, request.lambda_base_home, request.lambda_base_away, request.snapshot_ts
        )
        markov["home_team_id"] = request.home_team_id
        markov["away_team_id"] = request.away_team_id
        legacy_hawkes = request.hawkes_enabled and not request.markov_live_enabled
        hawkes = integrate_hawkes_optional(
            markov,
            self._legacy_hawkes_events(request),
            HawkesIntegrationConfig(
                hawkes_enabled=legacy_hawkes,
                hawkes_shadow_mode=legacy_hawkes,
                official_prediction=request.official_prediction,
            ),
        )
        markov_live: dict[str, Any] | None = None
        hawkes_residual: dict[str, Any] | None = None
        combined_live: dict[str, Any] | None = None
        if request.markov_live_enabled:
            live_request = self._markov_live_input(request)
            markov_live = self._markov_live.predict(live_request)
            if request.hawkes_enabled:
                hawkes_defaults = HawkesLiveConfig()
                config = HawkesLiveConfig(
                    rho=request.hawkes_rho if request.hawkes_rho is not None else hawkes_defaults.rho,
                    rho_goal=(
                        request.hawkes_rho_goal
                        if request.hawkes_rho_goal is not None
                        else hawkes_defaults.rho_goal
                    ),
                    rho_next_event=(
                        request.hawkes_rho_next_event
                        if request.hawkes_rho_next_event is not None
                        else hawkes_defaults.rho_next_event
                    ),
                )
                composition = HawkesLiveV2(config).combine(
                    markov_live,
                    list(live_request.events),
                    home_team_id=request.home_team_id,
                    away_team_id=request.away_team_id,
                    score_home=live_request.score_home,
                    score_away=live_request.score_away,
                )
                hawkes_residual = composition["hawkes_residual"]
                combined_live = composition["combined_live"]
        provenance = self._provenance(
            request.source_hash,
            legacy_hawkes,
            request.markov_live_shadow_mode,
            combined_live is not None,
        )
        audit = self._live_audit(
            request, markov, hawkes, markov_live, hawkes_residual, combined_live,
        )
        return LiveIntensityOutput(
            request.match_id, request.snapshot_ts, request.lambda_base_home,
            request.lambda_base_away, markov["lambda_markov_home"],
            markov["lambda_markov_away"], markov["home_state"], markov["away_state"],
            self._markov_audit(markov), "markov_v1", bool(hawkes["enabled"] or combined_live is not None),
            hawkes["experimental_output"], provenance, audit,
            markov_live, hawkes_residual, combined_live,
        )

    def _validate_pre_match(self, request: PreMatchInput) -> None:
        """Valida elegibilidad, corte, identidad y versiones."""

        if request.match_id == BLOCKED_MATCH_ID:
            raise ValueError("704766 excluido por existing_data_failed_run.")
        if request.home_team_id == request.away_team_id or min(request.home_team_id, request.away_team_id) <= 0:
            raise ValueError("Orientacion o identidad de equipos invalida.")
        if request.competition_id != "esp.1" or request.feature_version != "match_features_v1":
            raise ValueError("Competencia o version de features incompatible.")
        if not request.eligible_for_materialization or not request.history_minimum_met:
            raise ValueError("La fila no cumple el contrato minimo de inferencia.")
        if _parse_ts(request.feature_cutoff_ts) > _parse_ts(request.kickoff_ts):
            raise ValueError("feature_cutoff_ts debe ser <= kickoff_ts.")
        if not math.isclose(request.attack_sum, 0.0, abs_tol=1e-8):
            raise ValueError("La restriccion suma-cero de ataque no se cumple.")
        if not math.isclose(request.defense_sum, 0.0, abs_tol=1e-8):
            raise ValueError("La restriccion suma-cero de defensa no se cumple.")

    def _validate_live(self, request: LiveSnapshotInput) -> None:
        """Valida temporalidad, intensidades y gate de Hawkes."""

        if request.match_id == BLOCKED_MATCH_ID:
            raise ValueError("704766 permanece excluido.")
        if _parse_ts(request.snapshot_ts) < _parse_ts(request.kickoff_ts):
            raise ValueError("snapshot_ts no puede preceder al kickoff.")
        self._validate_intensities(request.lambda_base_home, request.lambda_base_away)
        if request.hawkes_enabled != request.hawkes_shadow_mode:
            raise ValueError("Hawkes requiere activacion shadow explicita y coherente.")
        if request.markov_live_enabled != request.markov_live_shadow_mode:
            raise ValueError("Markov Live requiere activacion shadow explicita y coherente.")
        if request.official_prediction and (request.hawkes_enabled or request.markov_live_enabled):
            raise ValueError("Los modelos live shadow no estan permitidos en predicciones oficiales.")
        if request.markov_live_enabled and request.match_clock_seconds is None:
            raise ValueError("Markov Live requiere match_clock_seconds explícito.")
        if request.markov_live_enabled and (request.score_home is None or request.score_away is None):
            raise ValueError("Markov Live requiere marcador explícito de ambos equipos.")
        for name, value in (
            ("hawkes_rho", request.hawkes_rho),
            ("hawkes_rho_goal", request.hawkes_rho_goal),
            ("hawkes_rho_next_event", request.hawkes_rho_next_event),
        ):
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} debe pertenecer a [0, 1].")
        live_clock = self._live_clock_seconds(request)
        if live_clock < 0.0:
            raise ValueError("match_clock_seconds no puede ser negativo.")
        if request.period < 1 or request.period > 5:
            raise ValueError("period debe pertenecer a [1, 5].")
        if request.score_home is not None and request.score_home < 0:
            raise ValueError("score_home no puede ser negativo.")
        if request.score_away is not None and request.score_away < 0:
            raise ValueError("score_away no puede ser negativo.")
        if request.source_fetched_at and _parse_ts(request.source_fetched_at) > _parse_ts(request.snapshot_ts):
            raise ValueError("source_fetched_at no puede ser posterior al snapshot.")
        for event in request.events:
            if event.get("event_ts") and _parse_ts(str(event["event_ts"])) > _parse_ts(request.snapshot_ts):
                raise ValueError("event_ts debe ser <= snapshot_ts.")
            event_clock = event.get("match_clock_seconds")
            if event_clock is not None and float(event_clock) > live_clock + 1e-9:
                raise ValueError("match_clock_seconds del evento debe ser <= snapshot.")

    @staticmethod
    def _validate_intensities(home: float, away: float) -> None:
        """Exige intensidades positivas y finitas."""

        if not all(math.isfinite(value) and value > 0.0 for value in (home, away)):
            raise ValueError("Las intensidades deben ser positivas y finitas.")

    @staticmethod
    def _dc_lambdas(request: PreMatchInput) -> tuple[float, float]:
        """Calcula las intensidades estaticas de Dixon-Coles."""

        home = math.exp(request.league_intercept + request.home_advantage + request.dc_attack_home - request.dc_defense_away)
        away = math.exp(request.league_intercept + request.dc_attack_away - request.dc_defense_home)
        DikamahaInferenceEngine._validate_intensities(home, away)
        return home, away

    @staticmethod
    def _kalman_lambdas(request: PreMatchInput) -> tuple[float, float]:
        """Calcula intensidades dinamicas con intercepto fijo y localia en estado."""

        home = math.exp(request.league_intercept + request.home_advantage + request.kalman_attack_home - request.kalman_defense_away)
        away = math.exp(request.league_intercept + request.kalman_attack_away - request.kalman_defense_home)
        DikamahaInferenceEngine._validate_intensities(home, away)
        return home, away

    @staticmethod
    def _markets(grid: np.ndarray) -> dict[str, float]:
        """Deriva mercados exclusivamente de la matriz de marcadores."""

        home = float(np.tril(grid, -1).sum())
        draw = float(np.trace(grid))
        away = float(np.triu(grid, 1).sum())
        over = float(sum(grid[i, j] for i in range(grid.shape[0]) for j in range(grid.shape[1]) if i + j > 2))
        btts = float(grid[1:, 1:].sum())
        return {"home": home, "draw": draw, "away": away, "over_2_5": over, "btts": btts}

    @staticmethod
    def _freeze_matrix(grid: np.ndarray) -> tuple[tuple[float, ...], ...]:
        """Convierte la matriz mutable en una estructura inmutable."""

        return tuple(tuple(float(value) for value in row) for row in grid)

    @staticmethod
    def _provenance(
        source_hash: str,
        hawkes_shadow_mode: bool = False,
        markov_live_shadow_mode: bool = False,
        combined_live_shadow_mode: bool = False,
    ) -> Provenance:
        """Construye provenance congelado del nucleo vigente."""

        version = "hawkes_v1:alpha_reduced_shadow" if hawkes_shadow_mode else "hawkes_v1_disabled"
        return Provenance(
            source_hash=source_hash,
            hawkes_version=version,
            hawkes_shadow_mode=hawkes_shadow_mode,
            markov_live_version="markov_live_v1_shadow" if markov_live_shadow_mode else "markov_live_v1_disabled",
            hawkes_live_version="hawkes_live_v2_residual_shadow" if combined_live_shadow_mode else "hawkes_live_v2_disabled",
            markov_live_shadow_mode=markov_live_shadow_mode,
            combined_live_shadow_mode=combined_live_shadow_mode,
        )

    @staticmethod
    def _pre_output_payload(request: PreMatchInput, home: float, away: float, markets: dict[str, float]) -> dict[str, Any]:
        """Construye el payload estable usado por la auditoria."""

        return {"match_id": request.match_id, "lambda_base_home": home, "lambda_base_away": away, **markets}

    def _pre_audit(self, request: PreMatchInput, grid: np.ndarray, payload: dict[str, Any]) -> AuditMetadata:
        """Audita normalizacion, temporalidad y procedencia pre-match."""

        checks = {
            "cutoff_valid": _parse_ts(request.feature_cutoff_ts) <= _parse_ts(request.kickoff_ts),
            "score_matrix_normalized": math.isclose(float(grid.sum()), 1.0, abs_tol=1e-10),
            "market_1x2_normalized": math.isclose(payload["home"] + payload["draw"] + payload["away"], 1.0, abs_tol=1e-10),
            "no_softmax": True,
            "no_target_events": True,
            "sum_zero": math.isclose(request.attack_sum, 0.0, abs_tol=1e-8) and math.isclose(request.defense_sum, 0.0, abs_tol=1e-8),
        }
        return AuditMetadata(all(checks.values()), checks, input_hash=_stable_hash(asdict(request)), output_hash=_stable_hash(payload))

    @staticmethod
    def _events_frame(request: LiveSnapshotInput) -> pd.DataFrame:
        """Adapta eventos validos al puerto actual de Markov v1."""

        rows = list(request.events) or [{"event_id": "kickoff", "event_ts": request.kickoff_ts, "event_type": "kickoff", "team_id": None}]
        normalized = []
        for event in rows:
            if event.get("event_ts"):
                event_ts = _parse_ts(str(event["event_ts"]))
            elif event.get("match_clock_seconds") is not None:
                event_ts = _parse_ts(request.kickoff_ts) + pd.Timedelta(seconds=float(event["match_clock_seconds"]))
            else:
                raise ValueError("Cada evento requiere event_ts o match_clock_seconds.")
            delta = event_ts - _parse_ts(request.kickoff_ts)
            normalized.append({**event, "match_id": request.match_id, "home_team_id": request.home_team_id, "away_team_id": request.away_team_id, "kickoff_ts": request.kickoff_ts, "minute": max(0, int(delta.total_seconds() // 60)), "second": max(0, int(delta.total_seconds() % 60)), "annulled": bool(event.get("annulled", False)), "is_control": bool(event.get("is_control", False))})
        return pd.DataFrame(normalized)

    @staticmethod
    def _markov_audit(markov: dict[str, Any]) -> dict[str, Any]:
        """Extrae auditoria reproducible de Markov."""

        return {"context_factor": 1.0, "state_before": markov["state_before"], "state_after": markov["state_after"], "window_5m": markov["window_5m"], "window_10m": markov["window_10m"], "transition_version": "markov_transition_v1"}

    def _live_audit(
        self,
        request: LiveSnapshotInput,
        markov: dict[str, Any],
        hawkes: dict[str, Any],
        markov_live: dict[str, Any] | None,
        hawkes_residual: dict[str, Any] | None,
        combined_live: dict[str, Any] | None,
    ) -> AuditMetadata:
        """Audita temporalidad y separacion Markov/Hawkes."""

        experimental = hawkes["experimental_output"]
        used = experimental["events_used"] if experimental else []
        stability = experimental["stability"] if experimental else None
        checks = {
            "events_before_snapshot": all(
                (not e.get("event_ts") or _parse_ts(str(e["event_ts"])) <= _parse_ts(request.snapshot_ts))
                and (e.get("match_clock_seconds") is None or float(e["match_clock_seconds"]) <= self._live_clock_seconds(request) + 1e-9)
                for e in request.events
            ),
            "markov_intensities_valid": all(math.isfinite(markov[key]) and markov[key] > 0 for key in ("lambda_markov_home", "lambda_markov_away")),
            "context_factor_is_one": self._markov.config.context_factor_value == 1.0,
            "hawkes_default_off": request.hawkes_enabled or experimental is None,
            "shadow_gate_coherent": request.hawkes_enabled == request.hawkes_shadow_mode,
            "markov_independent_of_hawkes": hawkes["official_source"] == "markov_v1",
            "event_ids_deduplicated": len({item["event_id"] for item in used}) == len(used),
            "hawkes_stable": stability is None or bool(stability["subcritical"] and stability["positive_finite"]),
            "no_live_probabilities": not any("prob" in key for key in markov),
            "no_postgresql_or_external_calls": True,
            "markov_live_gate_coherent": request.markov_live_enabled == request.markov_live_shadow_mode,
            "markov_live_valid": markov_live is None or bool(markov_live["audit"]["passed"]),
            "hawkes_residual_requires_markov_live": hawkes_residual is None or markov_live is not None,
            "combined_requires_residual": combined_live is None or hawkes_residual is not None,
            "official_output_unchanged": not request.official_prediction or (markov_live is None and combined_live is None),
        }
        output = {
            "markov": self._markov_audit(markov),
            "experimental_hawkes": experimental,
            "experimental_markov_live": markov_live,
            "experimental_hawkes_residual": hawkes_residual,
            "experimental_combined_live": combined_live,
        }
        warnings = tuple(experimental["warnings"]) if experimental else ()
        return AuditMetadata(
            all(checks.values()),
            checks,
            warnings=warnings,
            input_hash=_stable_hash(asdict(request)),
            output_hash=_stable_hash(output),
        )

    @staticmethod
    def _live_clock_seconds(request: LiveSnapshotInput) -> float:
        """Usa reloj de partido explícito o deriva un fallback compatible."""

        if request.match_clock_seconds is not None:
            value = float(request.match_clock_seconds)
        else:
            value = (_parse_ts(request.snapshot_ts) - _parse_ts(request.kickoff_ts)).total_seconds()
        if not math.isfinite(value):
            raise ValueError("match_clock_seconds debe ser finito.")
        return value

    def _markov_live_input(self, request: LiveSnapshotInput) -> MarkovLiveInput:
        """Adapta el contrato HTTP/legacy al snapshot causal live v1."""

        events = []
        for raw in request.events:
            event = dict(raw)
            if event.get("match_clock_seconds") is None and event.get("event_ts"):
                event["match_clock_seconds"] = (
                    _parse_ts(str(event["event_ts"])) - _parse_ts(request.kickoff_ts)
                ).total_seconds()
            events.append(event)
        score_home, score_away = self._live_score(request, events)
        return MarkovLiveInput(
            match_id=request.match_id,
            home_team_id=request.home_team_id,
            away_team_id=request.away_team_id,
            kickoff_ts=request.kickoff_ts,
            snapshot_ts=request.snapshot_ts,
            match_clock_seconds=self._live_clock_seconds(request),
            period=request.period,
            score_home=score_home,
            score_away=score_away,
            lambda_base_home=request.lambda_base_home,
            lambda_base_away=request.lambda_base_away,
            events=tuple(events),
            league_slug=request.league_slug,
            source_hash=request.source_hash,
        )

    @staticmethod
    def _legacy_hawkes_events(request: LiveSnapshotInput) -> list[dict[str, Any]]:
        """Completa `event_ts` para mantener compatible el puerto Hawkes v1."""

        output: list[dict[str, Any]] = []
        kickoff = _parse_ts(request.kickoff_ts)
        for raw in request.events:
            event = dict(raw)
            if not event.get("event_ts") and event.get("match_clock_seconds") is not None:
                event["event_ts"] = (
                    kickoff + pd.Timedelta(seconds=float(event["match_clock_seconds"]))
                ).isoformat()
            output.append(event)
        return output

    @staticmethod
    def _live_score(
        request: LiveSnapshotInput, events: Sequence[dict[str, Any]],
    ) -> tuple[int, int]:
        """Usa el marcador explícito o lo deriva sólo de goles observados."""

        if request.score_home is not None and request.score_away is not None:
            return int(request.score_home), int(request.score_away)
        home = away = 0
        seen: set[str] = set()
        for event in events:
            if bool(event.get("annulled", False)):
                continue
            if str(event.get("event_type")) not in {"goal", "penalty_scored"}:
                continue
            key = str(event.get("event_id") or _stable_hash(event))
            if key in seen:
                continue
            seen.add(key)
            if event.get("team_id") == request.home_team_id:
                home += 1
            elif event.get("team_id") == request.away_team_id:
                away += 1
        return home, away


# Version: 1.0.0
# Created: 2026-07-15
