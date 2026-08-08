"""Pruebas fail-closed de la cadena oficial de goles."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.official_goal_chain import DixonColesKalmanGoalModel, GoalModelPort, _frame
from src.universal_prematch import UniversalPrematchEngine, UpcomingMatchInput


def _history() -> list[dict[str, object]]:
    """Crea historia suficiente, válida y estrictamente causal."""

    rows = []
    teams = ((1, 2), (3, 4), (1, 3), (2, 4))
    for index in range(16):
        home, away = teams[index % len(teams)]
        rows.append({
            "match_id": index + 1,
            "match_date": f"2025-01-{index + 1:02d}T12:00:00+00:00",
            "league_slug": "test.1",
            "home_team_id": home,
            "away_team_id": away,
            "home_goals": index % 3,
            "away_goals": (index + 1) % 2,
        })
    return rows


def test_goal_history_must_be_strictly_before_cutoff() -> None:
    """Rechaza historia del objetivo o del futuro en vez de auditarla tarde."""

    rows = _history()
    rows[-1]["match_date"] = "2025-02-01T12:00:00+00:00"
    with pytest.raises(ValueError, match="history_not_strictly_before_cutoff"):
        _frame(rows, "2025-02-01T12:00:00+00:00")


def test_goal_history_rejects_duplicate_match_ids() -> None:
    """La unidad IID partido debe ser única."""

    rows = _history()
    rows[-1]["match_id"] = rows[0]["match_id"]
    with pytest.raises(ValueError, match="duplicate_match_id"):
        _frame(rows, "2025-02-01T12:00:00+00:00")


def test_non_converged_goal_model_is_not_served(monkeypatch: pytest.MonkeyPatch) -> None:
    """Un optimizador fallido activa fallback, nunca una predicción candidata."""

    model = DixonColesKalmanGoalModel()
    monkeypatch.setattr(
        model, "_fit_or_load",
        lambda frame, cutoff: SimpleNamespace(converged=False))
    with pytest.raises(RuntimeError, match="dixon_coles_non_converged"):
        model.predict(_history(), 1, 2, "2025-02-01T12:00:00+00:00")


def test_router_falls_back_when_goal_integrity_gate_fails() -> None:
    """La excepción controlada conserva el baseline estructural disponible."""

    class RejectingGoalModel(GoalModelPort):
        def predict(self, matches, home_team_id, away_team_id, cutoff_ts):
            raise RuntimeError("integrity_gate_failed")

    request = UpcomingMatchInput(
        "test.1", 1, 2, "2025-02-01T12:00:00+00:00", 999)
    output = UniversalPrematchEngine(
        goal_model=RejectingGoalModel())._goal_output(_history(), request)
    assert output[3]["router_model"] == "structural_poisson_baseline"
    assert output[3]["fallback_reason"] == "integrity_gate_failed"
