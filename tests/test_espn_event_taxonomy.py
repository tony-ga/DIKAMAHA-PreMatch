"""Pruebas unitarias de la taxonomía ESPN v1.1."""

from src.espn_event_taxonomy import classify_event_type, classify_play
from scripts.run_phase_38_multileague_event_windows import _is_shootout


def test_modelable_and_auxiliary_event_types_are_distinguished() -> None:
    """Separa señales de eventos válidos sin uso directo en Markov."""

    assert classify_event_type("foul") == "foul"
    assert classify_event_type("throw_in") == "auxiliary"
    assert classify_event_type("save") == "auxiliary"


def test_scoring_play_and_unknown_are_safe() -> None:
    """Prioriza scoringPlay y conserva desconocidos para auditoría."""

    assert classify_event_type("goal___header") == "goal"
    assert classify_event_type("goal---header") == "goal"
    assert classify_event_type("unpublished_provider_type") == "unclassified"
    assert classify_play({"scoringPlay": True, "type": {"type": "penalty___scored"}}) == ("goal", "penalty___scored")


def test_shootout_aliases_are_excluded_from_regular_score() -> None:
    """Reconoce ambas variantes de ESPN para penales de tanda."""

    assert _is_shootout({"event_type_raw": "penalty___scored", "event_text": "Goal (1)"})
    assert _is_shootout({"event_type_raw": "penalty---scored", "event_text": "Goal (1)"})


# Version: 1.1.0
# Created: 2026-07-27
