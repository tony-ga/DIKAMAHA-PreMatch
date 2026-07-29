"""Pruebas del simulador pre-match coherente de Fase 79."""

import json
from pathlib import Path

import numpy as np

from src.dual_markov_simulator import (
    DualMarkovSimulator,
    HierarchicalTransitionKernel,
    SimulationConfig,
    SimulationRequest,
)

PARAMETERS = Path(
    "artifacts/phase_78_context_transitions/transition_parameters.json")


def _request(league: str = "arg.1") -> SimulationRequest:
    """Construye un partido puramente pre-match."""

    initial = (0.14, 0.18, 0.12, 0.16, 0.22, 0.18)
    return SimulationRequest(
        999_079, league, 1, 2, "2026-07-28T00:00:00Z",
        1.45, 0.95, initial, initial)


def _simulator(seed: int = 79) -> DualMarkovSimulator:
    """Construye el solver contra parámetros auditados."""

    kernel = HierarchicalTransitionKernel.from_path(PARAMETERS)
    return DualMarkovSimulator(
        kernel, (0.65, 0.95, 1.45, 0.78, 1.08, 1.62),
        SimulationConfig(simulations=500, seed=seed))


def test_same_seed_produces_identical_prediction() -> None:
    """El replay reproduce contenido y hash."""

    first = _simulator().simulate(_request())
    second = _simulator().simulate(_request())
    assert first == second
    assert first["prediction_hash"] == second["prediction_hash"]


def test_mass_probabilities_and_style_are_valid() -> None:
    """Conserva lambdas, normalización 1X2 y estilo."""

    result = _simulator().simulate(_request())
    audit, markets = result["audit"], result["markets"]
    assert max(audit["home_mass_error"], audit["away_mass_error"]) < 1e-6
    assert abs(markets["home_win"] + markets["draw"]
               + markets["away_win"] - 1.0) < 1e-9
    assert audit["home_style_changes"] == audit["away_style_changes"] == 0


def test_unknown_league_uses_core_without_flat_copy() -> None:
    """El modo core opera y conserva una curva temporal propia."""

    result = _simulator().simulate(_request("unknown.999"))
    curve = [row["any_goal"] for row in result["window_15m"]]
    assert len(curve) == 6
    assert np.ptp(curve) > 0.0
    assert result["audit"]["target_post_cutoff_reads"] == 0


def test_transition_payload_is_normalized() -> None:
    """Todos los contextos producen distribuciones válidas."""

    payload = json.loads(PARAMETERS.read_text(encoding="utf-8"))
    kernel = HierarchicalTransitionKernel(payload)
    probability = kernel.probability("missing.1", True, 0, 0, 2)
    assert np.isclose(probability.sum(), 1.0)
    assert np.all(probability >= 0.0)


def test_trajectory_distributions_are_normalized_and_shadow() -> None:
    """Los mercados secuenciales son válidos pero no promovidos."""

    result = _simulator().simulate(_request())
    markets = result["trajectory_markets"]
    assert abs(sum(markets["first_goal_window"].values()) - 1.0) < 1e-9
    assert abs(sum(markets["scoring_windows"].values()) - 1.0) < 1e-9
    assert result["classification"] == "experimental_shadow_not_promoted"
