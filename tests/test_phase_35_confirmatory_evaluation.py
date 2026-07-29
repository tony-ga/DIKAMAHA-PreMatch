"""Pruebas del scoring post-match separado de inferencia."""

from src.phase_35_confirmatory_evaluation import _metrics, _score


def _prediction() -> dict:
    """Construye una predicción mínima para todos los targets."""

    names = ("first_half_goal", "second_half_goal", "home_recovery_draw_or_win", "away_recovery_draw_or_win", "home_reaches_level_after_half", "away_reaches_level_after_half", "home_comeback_win", "away_comeback_win")
    return {"match_id": 1, "cutoff_ts": "2026-01-01T12:00:00+00:00", **{f"routed_probability_{name}": 0.5 for name in names}, **{f"baseline_{name}": 0.4 for name in names}}


def _target() -> dict:
    """Construye un target mínimo posterior al partido."""

    names = ("first_half_goal", "second_half_goal", "home_recovery_draw_or_win", "away_recovery_draw_or_win", "home_reaches_level_after_half", "away_reaches_level_after_half", "home_comeback_win", "away_comeback_win")
    return {"match_id": 1, **{name: False for name in names}, "home_trailing_at_half": False, "away_trailing_at_half": False}


def test_score_reads_targets_only_after_prediction() -> None:
    """El scoring produce pérdidas únicamente en la fase post-match."""

    scored = _score([_prediction()], [_target()])
    assert scored[0]["target_first_half_goal"] is False
    assert "loss_first_half_goal" in scored[0]


def test_metrics_without_support_do_not_confirm() -> None:
    """Una muestra pequeña no alcanza el gate confirmatorio."""

    metrics = _metrics(_score([_prediction()], [_target()]), [_target()])
    assert metrics["first_half_goal"]["support_sufficient"] is False
    assert metrics["first_half_goal"]["bootstrap"]["improvement_confirmed"] is False

# Version: 1.0.0
# Created: 2026-07-26
