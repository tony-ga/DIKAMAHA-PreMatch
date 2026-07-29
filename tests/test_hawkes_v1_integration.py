"""Pruebas de cierre experimental para la integracion de Hawkes v1."""

from __future__ import annotations

import pandas as pd

from src.hawkes_v1_integration import HawkesIntegrationConfig, integrate_hawkes_optional
from src.markov_v1 import MarkovV1, generate_synthetic_markov_dataset


def _markov_snapshot() -> dict[str, object]:
    """Construye un snapshot Markov sintetico reutilizable."""
    frame = generate_synthetic_markov_dataset()
    match = frame[frame["match_id"] == 1].copy()
    snapshot = MarkovV1().predict_snapshot(
        match[match["event_ts"] <= pd.Timestamp("2025-01-01T12:12:00+00:00", tz="UTC")],
        1.5,
        1.2,
        "2025-01-01T12:12:00+00:00",
    )
    snapshot["home_team_id"] = 1
    snapshot["away_team_id"] = 2
    return snapshot


def test_hawkes_disabled_by_default() -> None:
    """Hawkes debe permanecer desactivado por defecto."""
    result = integrate_hawkes_optional(_markov_snapshot(), [])
    assert result["enabled"] is False
    assert result["shadow_mode"] is False
    assert result["experimental_output"] is None


def test_markov_output_has_no_hawkes_dependency() -> None:
    """Markov no debe requerir campos Hawkes para producir snapshots."""
    snapshot = _markov_snapshot()
    assert "lambda_markov_home" in snapshot
    assert "lambda_hawkes_home" not in snapshot
    assert "lambda_hawkes_away" not in snapshot


def test_hawkes_requires_explicit_flag() -> None:
    """Activar Hawkes debe requerir una bandera explicita."""
    events = [{
        "event_id": "e1",
        "event_ts": "2025-01-01T12:11:30+00:00",
        "team_id": 1,
        "event_type": "shot_on_target",
    }]
    result = integrate_hawkes_optional(
        _markov_snapshot(),
        events,
        HawkesIntegrationConfig(hawkes_enabled=True, hawkes_shadow_mode=True),
    )
    shadow = result["experimental_output"]
    assert result["enabled"] is True and result["shadow_mode"] is True
    assert shadow["lambda_hawkes_home"] is not None
    assert shadow["lambda_hawkes_away"] is not None
    assert shadow["parameters"]["model_version"] == "hawkes_v1:alpha_reduced"
    assert shadow["stability"]["spectral_radius"] == 0.56
    assert shadow["stability"]["subcritical"] is True


def test_shadow_rejects_incoherent_or_official_activation() -> None:
    """Rechaza shadow parcial y cualquier uso oficial."""

    import pytest

    with pytest.raises(ValueError, match="requiere"):
        HawkesIntegrationConfig(hawkes_enabled=True)
    with pytest.raises(ValueError, match="oficiales"):
        HawkesIntegrationConfig(
            hawkes_enabled=True,
            hawkes_shadow_mode=True,
            official_prediction=True,
        )


def test_shadow_is_deterministic_and_auditable() -> None:
    """Conserva contribuciones, provenance y replay determinista."""

    events = [
        {"event_id": "e1", "event_ts": "2025-01-01T12:11:30+00:00", "team_id": 1, "event_type": "shot_on_target"},
        {"event_id": "e1", "event_ts": "2025-01-01T12:11:30+00:00", "team_id": 1, "event_type": "shot_on_target"},
        {"event_id": "null", "event_ts": "2025-01-01T12:11:00+00:00", "team_id": None, "event_type": "corner"},
        {"event_id": "unknown", "event_ts": "2025-01-01T12:10:30+00:00", "team_id": 1, "event_type": "unknown"},
        {"event_id": "annulled", "event_ts": "2025-01-01T12:10:00+00:00", "team_id": 1, "event_type": "goal", "annulled": True},
    ]
    config = HawkesIntegrationConfig(hawkes_enabled=True, hawkes_shadow_mode=True)
    first = integrate_hawkes_optional(_markov_snapshot(), events, config)
    second = integrate_hawkes_optional(_markov_snapshot(), events, config)
    assert first == second
    shadow = first["experimental_output"]
    assert len(shadow["events_used"]) == 1
    assert len(shadow["events_audit"]) == 4
    assert shadow["provenance"]["experimental_only"] is True
    assert shadow["provenance"]["parameters_calibrated"] is False


def test_overexcitation_warning_is_visible() -> None:
    """Advierte uplift excesivo sin sustituir la salida Markov."""

    events = [
        {"event_id": f"shot-{index}", "event_ts": "2025-01-01T12:11:59+00:00", "team_id": 1, "event_type": "shot_on_target"}
        for index in range(10)
    ]
    result = integrate_hawkes_optional(
        _markov_snapshot(),
        events,
        HawkesIntegrationConfig(hawkes_enabled=True, hawkes_shadow_mode=True),
    )
    shadow = result["experimental_output"]
    assert shadow["overexcitation_warning"] is True
    assert "overexcitation_relative_uplift" in shadow["warnings"]
    assert result["official_source"] == "markov_v1"
