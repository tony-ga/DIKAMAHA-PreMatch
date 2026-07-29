"""Pruebas de reconciliación conservadora de eventos ESPN."""

from src.espn_event_reconciliation import reconcile_staging_events


def _goal(
    index: int,
    minute: int,
    second: int,
    team: int,
    text: str,
    raw: str = "goal",
) -> dict[str, object]:
    """Construye una fila staging mínima."""

    return {"event_index": index, "minute": minute, "second": second,
            "team_provider_id": team, "event_type": "goal",
            "event_type_raw": raw, "event_text": text, "annulled": False}


def test_parallel_goal_representations_count_once() -> None:
    """Dos representaciones cercanas del mismo equipo forman un solo gol."""

    rows = [_goal(1, 10, 2, 1, "Goal! Local 1, Visitante 0."),
            _goal(2, 10, 4, 1, "Gol de Jugador")]
    output, audit = reconcile_staging_events(rows, 1, 0, 1, 2)
    assert sum(not row["annulled"] for row in output) == 1
    assert audit == {"near_duplicate_goal": 1}


def test_repeated_score_keeps_latest_provider_correction() -> None:
    """Una progresión repetida conserva su última publicación."""

    rows = [_goal(1, 58, 0, 1, "Goal! Local 1, Visitante 0."),
            _goal(2, 75, 0, 1, "Goal! Local 1, Visitante 0."),
            _goal(3, 90, 0, 2, "Goal! Local 1, Visitante 1.")]
    output, audit = reconcile_staging_events(rows, 1, 1, 1, 2)
    assert output[0]["annulled"] is True
    assert output[1]["annulled"] is False
    assert audit == {"repeated_score_snapshot": 1}


def test_penalty_sequence_is_not_regulation_score() -> None:
    """Una tanda posterior al empate queda fuera del marcador reglamentario."""

    rows = [_goal(index, 91 + index, 0, 1 + index % 2, "Penalty scored",
                  "penalty___scored") for index in range(1, 6)]
    output, audit = reconcile_staging_events(rows, 0, 0, 1, 2)
    assert all(row["annulled"] for row in output)
    assert audit == {"penalty_shootout": 5}


def test_missing_goal_is_never_imputed() -> None:
    """La reconciliación no inventa un evento ausente."""

    rows = [_goal(1, 20, 0, 1, "Goal! Local 1, Visitante 0.")]
    output, audit = reconcile_staging_events(rows, 2, 0, 1, 2)
    assert len(output) == 1 and output[0]["annulled"] is False
    assert audit == {}


def test_two_distinct_goals_at_same_clock_are_preserved() -> None:
    """Marcadores progresivos distintos prevalecen aunque compartan clock."""

    rows = [_goal(1, 45, 0, 1, "Goal! Local 0, Visitante 1."),
            _goal(2, 45, 0, 1, "Goal! Local 0, Visitante 2.")]
    output, audit = reconcile_staging_events(rows, 0, 2, 2, 1)
    assert all(not row["annulled"] for row in output)
    assert audit == {}
