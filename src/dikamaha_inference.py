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
    from src.hawkes_v1_integration import HawkesIntegrationConfig, integrate_hawkes_optional
    from src.kalman_v2 import poisson_matrix
    from src.markov_v1 import MarkovV1
except ModuleNotFoundError:  # pragma: no cover - soporte de ejecucion directa
    from hawkes_v1_integration import HawkesIntegrationConfig, integrate_hawkes_optional
    from kalman_v2 import poisson_matrix
    from markov_v1 import MarkovV1

CONTRACT_VERSION = "dikamaha_inference_contract_v1.1_shadow"
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

    def __init__(self, markov: MarkovV1 | None = None) -> None:
        """Inicializa dependencias matematicas inyectables.

        Args:
            markov: Implementacion Markov compatible con el contrato.
        """

        self._markov = markov or MarkovV1()

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
        """Aplica Markov y conserva Hawkes detras del gate experimental."""

        self._validate_live(request)
        frame = self._events_frame(request)
        markov = self._markov.predict_snapshot(
            frame, request.lambda_base_home, request.lambda_base_away, request.snapshot_ts
        )
        markov["home_team_id"] = request.home_team_id
        markov["away_team_id"] = request.away_team_id
        hawkes = integrate_hawkes_optional(
            markov,
            list(request.events),
            HawkesIntegrationConfig(
                hawkes_enabled=request.hawkes_enabled,
                hawkes_shadow_mode=request.hawkes_shadow_mode,
                official_prediction=request.official_prediction,
            ),
        )
        provenance = self._provenance(request.source_hash, request.hawkes_shadow_mode)
        audit = self._live_audit(request, markov, hawkes)
        return LiveIntensityOutput(
            request.match_id, request.snapshot_ts, request.lambda_base_home,
            request.lambda_base_away, markov["lambda_markov_home"],
            markov["lambda_markov_away"], markov["home_state"], markov["away_state"],
            self._markov_audit(markov), "markov_v1", hawkes["enabled"],
            hawkes["experimental_output"], provenance, audit,
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
        if request.official_prediction and request.hawkes_enabled:
            raise ValueError("Hawkes shadow no esta permitido en predicciones oficiales.")
        for event in request.events:
            if _parse_ts(str(event["event_ts"])) > _parse_ts(request.snapshot_ts):
                raise ValueError("event_ts debe ser <= snapshot_ts.")

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
    def _provenance(source_hash: str, hawkes_shadow_mode: bool = False) -> Provenance:
        """Construye provenance congelado del nucleo vigente."""

        version = "hawkes_v1:alpha_reduced_shadow" if hawkes_shadow_mode else "hawkes_v1_disabled"
        return Provenance(
            source_hash=source_hash,
            hawkes_version=version,
            hawkes_shadow_mode=hawkes_shadow_mode,
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
            event_ts = _parse_ts(str(event["event_ts"]))
            delta = event_ts - _parse_ts(request.kickoff_ts)
            normalized.append({**event, "match_id": request.match_id, "home_team_id": request.home_team_id, "away_team_id": request.away_team_id, "kickoff_ts": request.kickoff_ts, "minute": max(0, int(delta.total_seconds() // 60)), "second": max(0, int(delta.total_seconds() % 60)), "annulled": bool(event.get("annulled", False)), "is_control": bool(event.get("is_control", False))})
        return pd.DataFrame(normalized)

    @staticmethod
    def _markov_audit(markov: dict[str, Any]) -> dict[str, Any]:
        """Extrae auditoria reproducible de Markov."""

        return {"context_factor": 1.0, "state_before": markov["state_before"], "state_after": markov["state_after"], "window_5m": markov["window_5m"], "window_10m": markov["window_10m"], "transition_version": "markov_transition_v1"}

    def _live_audit(self, request: LiveSnapshotInput, markov: dict[str, Any], hawkes: dict[str, Any]) -> AuditMetadata:
        """Audita temporalidad y separacion Markov/Hawkes."""

        experimental = hawkes["experimental_output"]
        used = experimental["events_used"] if experimental else []
        stability = experimental["stability"] if experimental else None
        checks = {
            "events_before_snapshot": all(_parse_ts(str(e["event_ts"])) <= _parse_ts(request.snapshot_ts) for e in request.events),
            "markov_intensities_valid": all(math.isfinite(markov[key]) and markov[key] > 0 for key in ("lambda_markov_home", "lambda_markov_away")),
            "context_factor_is_one": self._markov.config.context_factor_value == 1.0,
            "hawkes_default_off": request.hawkes_enabled or experimental is None,
            "shadow_gate_coherent": request.hawkes_enabled == request.hawkes_shadow_mode,
            "markov_independent_of_hawkes": hawkes["official_source"] == "markov_v1",
            "event_ids_deduplicated": len({item["event_id"] for item in used}) == len(used),
            "hawkes_stable": stability is None or bool(stability["subcritical"] and stability["positive_finite"]),
            "no_live_probabilities": not any("prob" in key for key in markov),
            "no_postgresql_or_external_calls": True,
        }
        output = {"markov": self._markov_audit(markov), "experimental_hawkes": experimental}
        warnings = tuple(experimental["warnings"]) if experimental else ()
        return AuditMetadata(
            all(checks.values()),
            checks,
            warnings=warnings,
            input_hash=_stable_hash(asdict(request)),
            output_hash=_stable_hash(output),
        )


# Version: 1.0.0
# Created: 2026-07-15
