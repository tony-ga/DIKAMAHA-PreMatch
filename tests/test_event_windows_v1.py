"""Pruebas deterministas para `event_windows v1`."""
from __future__ import annotations

from src.event_windows_v1 import EventWindowsConfig, build_windows


def _match() -> dict[str, object]:
    """Construye un partido mínimo con orientación válida."""
    return {"match_id": 10, "home_team_id": 1, "away_team_id": 2, "match_date": "2025-01-01T20:00:00+00:00", "season": "2025"}


def _event(event_id: int, minute: int, team_id: int | None, event_type: str, annulled: bool = False) -> dict[str, object]:
    """Construye un evento timeline mínimo y ordenable."""
    return {"event_id": event_id, "match_id": 10, "minute": minute, "second": 0, "team_id": team_id, "event_type": event_type, "annulled": annulled}


def test_windows_have_fixed_grain_and_causal_score() -> None:
    """Cada partido genera doce filas y el gol sólo afecta la ventana siguiente."""
    windows, audit = build_windows([_match()], [_event(1, 14, 1, "goal")], EventWindowsConfig())
    home = [row for row in windows if row["team_id"] == 1]
    assert len(windows) == 12
    assert home[0]["goals"] == 1
    assert home[0]["score_for_start"] == 0
    assert home[1]["score_for_start"] == 1
    assert audit["out_of_range_clocks"] == 0


def test_annulled_and_null_team_events_are_audited_not_counted() -> None:
    """Eventos ambiguos quedan en auditoría y no alteran métricas predictivas."""
    events = [_event(1, 20, 1, "corner", True), _event(2, 20, None, "yellow")]
    windows, _ = build_windows([_match()], events, EventWindowsConfig())
    home = next(row for row in windows if row["team_id"] == 1 and row["window_index"] == 1)
    assert home["corners"] == 0
    assert home["annulled_event_count"] == 1
    assert home["null_team_event_count"] == 1


def test_pressure_and_conceded_values_follow_opponent_events() -> None:
    """Presión propia y concedida se calculan sólo desde la ventana actual."""
    events = [_event(1, 31, 1, "shot_on_target"), _event(2, 32, 2, "corner")]
    windows, _ = build_windows([_match()], events, EventWindowsConfig())
    home = next(row for row in windows if row["team_id"] == 1 and row["window_index"] == 2)
    away = next(row for row in windows if row["team_id"] == 2 and row["window_index"] == 2)
    assert home["pressure"] == 1
    assert home["pressure_conceded"] == 1
    assert away["shots_conceded"] == 1

