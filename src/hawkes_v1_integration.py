"""Integración shadow de Hawkes v1 sobre la salida oficial Markov.

Hawkes permanece desactivado por defecto. Cuando se habilita explícitamente
en shadow mode, calcula una salida paralela sin reemplazar Markov.

Version: 2.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass
from typing import Any

try:
    from src.hawkes_v1 import HawkesConfig, HawkesV1
except ModuleNotFoundError:  # pragma: no cover
    from hawkes_v1 import HawkesConfig, HawkesV1

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class HawkesIntegrationConfig:
    """Configuración inmutable del gate shadow."""

    hawkes_enabled: bool = False
    hawkes_shadow_mode: bool = False
    official_prediction: bool = False
    config_version: str = "hawkes_shadow_integration_v1"
    frozen_candidate: str = "alpha_reduced"
    overexcitation_relative_threshold: float = 0.50

    def __post_init__(self) -> None:
        """Valida que Hawkes solo pueda operar como shadow experimental."""

        if self.hawkes_enabled != self.hawkes_shadow_mode:
            raise ValueError("Hawkes shadow requiere hawkes_enabled=true y hawkes_shadow_mode=true.")
        if self.official_prediction and self.hawkes_enabled:
            raise ValueError("Hawkes shadow no está permitido en predicciones oficiales.")
        if self.overexcitation_relative_threshold <= 0:
            raise ValueError("El umbral de sobreexcitación debe ser positivo.")


def frozen_alpha_reduced_config() -> HawkesConfig:
    """Devuelve el candidato congelado en Fases 5.4–5.6."""

    return HawkesConfig(
        model_version="hawkes_v1:alpha_reduced",
        memory_minutes=30.0,
        alpha_self=0.12,
        alpha_cross=0.048,
        beta=0.25,
        branching_matrix=((0.39, 0.17), (0.17, 0.39)),
    )


def _relative_delta(markov: float, hawkes: float) -> float:
    """Calcula uplift relativo sobre una intensidad Markov positiva."""

    return (hawkes - markov) / markov


def _disabled_output(
    markov_snapshot: dict[str, Any],
    config: HawkesIntegrationConfig,
) -> dict[str, Any]:
    """Construye el estado explícito de shadow desactivado."""

    return {
        "enabled": False,
        "shadow_mode": False,
        "status": "disabled",
        "integration_version": config.config_version,
        "frozen_candidate": config.frozen_candidate,
        "official_source": "markov_v1",
        "experimental_output": None,
    }


def _experimental_output(
    result: dict[str, Any],
    engine: HawkesV1,
    config: HawkesIntegrationConfig,
) -> dict[str, Any]:
    """Construye el bloque shadow auditable sin mercados."""

    markov_home = float(result["lambda_markov_home"])
    markov_away = float(result["lambda_markov_away"])
    hawkes_home = float(result["lambda_hawkes_home"])
    hawkes_away = float(result["lambda_hawkes_away"])
    relative_home = _relative_delta(markov_home, hawkes_home)
    relative_away = _relative_delta(markov_away, hawkes_away)
    overexcited = max(relative_home, relative_away) >= config.overexcitation_relative_threshold
    warnings = list(result["warnings"])
    if overexcited:
        warnings.append("overexcitation_relative_uplift")
    return {
        "lambda_hawkes_home": hawkes_home,
        "lambda_hawkes_away": hawkes_away,
        "absolute_difference_home": hawkes_home - markov_home,
        "absolute_difference_away": hawkes_away - markov_away,
        "relative_difference_home": relative_home,
        "relative_difference_away": relative_away,
        "event_contributions": result["event_contributions"],
        "events_used": result["events_used"],
        "events_audit": result["events_audit"],
        "stability": {
            "spectral_radius": float(result["spectral_radius"]),
            "subcritical": float(result["spectral_radius"]) < 1.0,
            "positive_finite": all(
                math.isfinite(value) and value > 0.0 for value in (hawkes_home, hawkes_away)
            ),
            "status": "stable" if not result["warnings"] else "warning",
        },
        "overexcitation_warning": overexcited,
        "warnings": warnings,
        "parameters": asdict(engine.config),
        "provenance": {
            "hawkes_model_hash": engine.model_hash(),
            "hawkes_version": engine.config.model_version,
            "candidate": config.frozen_candidate,
            "experimental_only": True,
            "parameters_calibrated": False,
            "source_phase": "phase_5_6_hawkes_v1_closure",
            "markov": result["markov_provenance"],
        },
    }


def integrate_hawkes_optional(
    markov_snapshot: dict[str, Any],
    valid_events: list[dict[str, Any]],
    integration_config: HawkesIntegrationConfig | None = None,
    hawkes_config: HawkesConfig | None = None,
) -> dict[str, Any]:
    """Calcula Hawkes en paralelo sin alterar las intensidades Markov."""

    config = integration_config or HawkesIntegrationConfig()
    if not config.hawkes_enabled:
        LOGGER.info("Hawkes shadow permanece desactivado.")
        return _disabled_output(markov_snapshot, config)
    engine = HawkesV1(hawkes_config or frozen_alpha_reduced_config())
    result = engine.predict_snapshot(
        match_id=int(markov_snapshot["match_id"]),
        snapshot_ts=str(markov_snapshot["snapshot_ts"]),
        lambda_markov_home=float(markov_snapshot["lambda_markov_home"]),
        lambda_markov_away=float(markov_snapshot["lambda_markov_away"]),
        home_team_id=int(markov_snapshot["home_team_id"]),
        away_team_id=int(markov_snapshot["away_team_id"]),
        events=valid_events,
        markov_provenance={
            "markov_matrix_synthetic": True,
            "markov_model_hash": markov_snapshot.get("markov_model_hash"),
            "markov_version": "markov_v1",
        },
    )
    return {
        "enabled": True,
        "shadow_mode": True,
        "status": "experimental_shadow",
        "integration_version": config.config_version,
        "frozen_candidate": config.frozen_candidate,
        "official_source": "markov_v1",
        "experimental_output": _experimental_output(result, engine, config),
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    snapshot = {
        "match_id": 1,
        "snapshot_ts": "2025-01-01T12:20:00+00:00",
        "lambda_markov_home": 1.5,
        "lambda_markov_away": 1.1,
        "home_team_id": 1,
        "away_team_id": 2,
    }
    disabled = integrate_hawkes_optional(snapshot, [])
    assert disabled["experimental_output"] is None
    enabled = integrate_hawkes_optional(
        snapshot,
        [{"event_id": "e1", "event_ts": "2025-01-01T12:19:00+00:00", "team_id": 1, "event_type": "shot_on_target"}],
        HawkesIntegrationConfig(hawkes_enabled=True, hawkes_shadow_mode=True),
    )
    assert enabled["experimental_output"]["stability"]["subcritical"]

# Version: 2.0.0
# Created: 2026-07-16
