"""Taxonomía común para eventos de play-by-play de ESPN.

La taxonomía distingue señales que alimentan ventanas/modelos de eventos
válidos pero auxiliares. Los auxiliares conservan provenance y no entran en
Markov; los tipos no reconocidos siguen siendo ``unclassified``.

Version: 1.2.0
Created: 2026-07-29
"""

from __future__ import annotations

from typing import Final

MODELABLE_EVENTS: Final[frozenset[str]] = frozenset({
    "goal", "yellow", "red", "substitution", "shot_on_target",
    "shot_off_target", "shot_blocked", "corner", "foul",
    "penalty_awarded", "penalty_scored",
})
AUXILIARY_EVENT = "auxiliary"
KNOWN_EVENTS: Final[frozenset[str]] = MODELABLE_EVENTS | {AUXILIARY_EVENT}

_ALIASES: Final[dict[str, str]] = {
    "yellowcard": "yellow", "yellow_card": "yellow", "yellow-card": "yellow",
    "redcard": "red", "red_card": "red", "red-card": "red",
    "shot": "shot_on_target", "shot_off_target": "shot_off_target",
    "shot-off-target": "shot_off_target", "shot_on_target": "shot_on_target",
    "shot-on-target": "shot_on_target", "shot_blocked": "shot_blocked",
    "shot-blocked": "shot_blocked", "corner": "corner", "corner_awarded": "corner",
    "corner-awarded": "corner", "penalty": "penalty_awarded",
    "penalty___scored": "penalty_scored", "penalty---scored": "penalty_scored",
    "foul": "foul", "goal": "goal", "goal___header": "goal",
    "goal---header": "goal", "own_goal": "goal", "own-goal": "goal",
    "substitution": "substitution",
}
_AUXILIARY_RAW_TYPES: Final[frozenset[str]] = frozenset({
    "throw_in", "throw-in", "free_kick", "free-kick", "blocked_pass",
    "blocked-pass", "save", "offside", "assist", "assists_shot", "assists-shot",
    "handball", "kickoff", "halftime", "start_2nd_half", "start-2nd-half",
    "end_regular_time", "end-regular-time",
    "penalty___missed", "penalty---missed", "penalty___hit_woodwork",
    "penalty---hit-woodwork", "start_shootout", "start-shootout", "end_match",
    "end-match", "halftime_extra_time", "halftime-extra-time", "start_extra_time",
    "start-extra-time", "start_2nd_half_extra_time", "start-2nd-half-extra-time",
    "end_extra_time", "end-extra-time", "var___red_card_upgrade",
    "var---red-card-upgrade",
    "penalty___saved", "penalty---saved", "shot_hit_woodwork", "shot-hit-woodwork",
    "deleted_after_review", "deleted-after-review", "var___referee_decision_cancelled",
    "var---referee-decision-cancelled", "var___referee_decision_confirmed",
    "var---referee-decision-confirmed",
    "pass", "ball_touch", "ball-touch", "out", "clear", "cross", "take_on",
    "take-on", "tackle", "aerial", "interception", "attempted_tackle",
    "attempted-tackle", "goal_kick", "goal-kick", "dispossessed", "end_delay",
    "end-delay", "start_delay", "start-delay", "drop_of_ball", "drop-of-ball",
    "keeper_sweeper", "keeper-sweeper", "shield_ball_opp", "shield-ball-opp",
    "punch", "claim",
})


def normalize_raw_type(value: object) -> str | None:
    """Normaliza la etiqueta ESPN sin convertirla todavía en una señal."""

    if not isinstance(value, str) or not value.strip():
        return None
    return value.strip().lower().replace(" ", "_")


def classify_event_type(raw_value: object, scoring_play: bool = False) -> str:
    """Clasifica un evento en señal modelable, auxiliar o desconocido.

    Args:
        raw_value: Tipo ESPN original o etiqueta ya normalizada.
        scoring_play: Indicador explícito de ESPN para una anotación.

    Returns:
        Etiqueta canónica del contrato de eventos.
    """

    raw = normalize_raw_type(raw_value)
    if scoring_play:
        return "goal"
    if raw is None:
        return "unclassified"
    if raw in _AUXILIARY_RAW_TYPES:
        return AUXILIARY_EVENT
    return _ALIASES.get(raw, raw if raw in KNOWN_EVENTS else "unclassified")


def classify_play(play: dict[str, object]) -> tuple[str, str | None]:
    """Extrae y clasifica el tipo de una jugada ESPN."""

    detail = play.get("type")
    detail_map = detail if isinstance(detail, dict) else {}
    raw = detail_map.get("type") or detail_map.get("text")
    return classify_event_type(raw, play.get("scoringPlay") is True), normalize_raw_type(raw)


# Version: 1.2.0
# Created: 2026-07-29
