"""Pruebas del contrato local de inferencia DIKAMAHA v1."""

from __future__ import annotations

from dataclasses import asdict

import pytest

from src.dikamaha_inference import DikamahaInferenceEngine, LiveSnapshotInput, PreMatchInput


def _pre_input(**changes: object) -> PreMatchInput:
    """Construye una entrada pre-match valida."""

    values = {
        "match_id": 900001, "home_team_id": 1, "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00",
        "feature_cutoff_ts": "2025-01-10T19:59:59+00:00",
        "competition_id": "esp.1", "feature_version": "match_features_v1",
        "eligible_for_materialization": True, "history_minimum_met": True,
        "league_intercept": 0.2, "home_advantage": 0.15,
        "dc_attack_home": 0.2, "dc_defense_home": -0.1,
        "dc_attack_away": -0.2, "dc_defense_away": 0.1,
        "kalman_attack_home": 0.25, "kalman_defense_home": -0.08,
        "kalman_attack_away": -0.25, "kalman_defense_away": 0.08,
        "source_hash": "synthetic-input-v1",
    }
    values.update(changes)
    return PreMatchInput(**values)


def _live_input(**changes: object) -> LiveSnapshotInput:
    """Construye una entrada live valida."""

    values = {
        "match_id": 900001, "home_team_id": 1, "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00",
        "snapshot_ts": "2025-01-10T20:10:00+00:00",
        "lambda_base_home": 1.5, "lambda_base_away": 1.1,
        "events": ({"event_id": "e1", "event_ts": "2025-01-10T20:08:00+00:00", "event_type": "shot_on_target", "team_id": 1},),
    }
    values.update(changes)
    return LiveSnapshotInput(**values)


def test_pre_match_contract_and_poisson_markets() -> None:
    """Valida mercados Poisson, versiones y determinismo."""

    engine = DikamahaInferenceEngine()
    first = engine.predict_pre_match(_pre_input())
    second = engine.predict_pre_match(_pre_input())
    assert first == second
    assert abs(first.probability_home + first.probability_draw + first.probability_away - 1.0) < 1e-10
    assert abs(sum(map(sum, first.score_matrix)) - 1.0) < 1e-10
    assert first.audit.passed and first.provenance.kalman_experimental


def test_pre_match_rejects_blocked_match_and_leakage() -> None:
    """Rechaza 704766 y cortes posteriores al kickoff."""

    engine = DikamahaInferenceEngine()
    with pytest.raises(ValueError, match="704766"):
        engine.predict_pre_match(_pre_input(match_id=704766))
    with pytest.raises(ValueError, match="feature_cutoff_ts"):
        engine.predict_pre_match(_pre_input(feature_cutoff_ts="2025-01-10T20:00:01+00:00"))


def test_live_markov_output_has_no_probabilities_or_hawkes() -> None:
    """Confirma Markov vigente y Hawkes desactivado por defecto."""

    output = DikamahaInferenceEngine().predict_live(_live_input())
    assert output.hawkes_applied is False
    assert output.experimental_hawkes is None
    assert output.lambda_markov_home > 0 and output.lambda_markov_away > 0
    assert not any("prob" in key for key in asdict(output))
    assert output.markov_audit["context_factor"] == 1.0
    assert output.official_source == "markov_v1"


def test_live_rejects_future_event_and_official_hawkes() -> None:
    """Rechaza eventos futuros y activacion oficial de Hawkes."""

    engine = DikamahaInferenceEngine()
    future = ({"event_id": "future", "event_ts": "2025-01-10T20:11:00+00:00", "event_type": "goal", "team_id": 1},)
    with pytest.raises(ValueError, match="event_ts"):
        engine.predict_live(_live_input(events=future))
    with pytest.raises(ValueError, match="oficiales"):
        engine.predict_live(_live_input(official_prediction=True, hawkes_enabled=True, hawkes_shadow_mode=True))


def test_explicit_experimental_hawkes_does_not_change_markov() -> None:
    """Confirma que el gate Hawkes no altera la salida Markov."""

    engine = DikamahaInferenceEngine()
    disabled = engine.predict_live(_live_input())
    enabled = engine.predict_live(_live_input(hawkes_enabled=True, hawkes_shadow_mode=True))
    assert enabled.hawkes_applied is True
    assert enabled.lambda_markov_home == disabled.lambda_markov_home
    assert enabled.lambda_markov_away == disabled.lambda_markov_away
    assert enabled.home_state == disabled.home_state
    assert enabled.away_state == disabled.away_state
    assert enabled.experimental_hawkes is not None
    assert enabled.experimental_hawkes["stability"]["subcritical"] is True
    assert enabled.provenance.hawkes_shadow_mode is True


def test_shadow_requires_both_flags_and_replays_deterministically() -> None:
    """Exige gate coherente y produce un bloque shadow determinista."""

    engine = DikamahaInferenceEngine()
    with pytest.raises(ValueError, match="shadow"):
        engine.predict_live(_live_input(hawkes_enabled=True))
    first = engine.predict_live(_live_input(hawkes_enabled=True, hawkes_shadow_mode=True))
    second = engine.predict_live(_live_input(hawkes_enabled=True, hawkes_shadow_mode=True))
    assert first == second
    assert first.audit.passed
    assert first.experimental_hawkes["provenance"]["candidate"] == "alpha_reduced"


# Version: 1.0.0
# Created: 2026-07-15
