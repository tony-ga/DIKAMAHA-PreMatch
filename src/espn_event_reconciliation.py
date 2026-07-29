"""Reconciliación conservadora de eventos ESPN contra el marcador final.

La capa nunca altera el payload crudo. Devuelve copias con exclusiones
auditables únicamente cuando existe evidencia interna suficiente.

Version: 1.0.0
Created: 2026-07-28
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

GOAL_SCORE = re.compile(r"^Goal!.*?\s(\d+),\s.*?\s(\d+)\.", re.IGNORECASE)


def reconcile_staging_events(
    rows: list[dict[str, Any]],
    home_score: int,
    away_score: int,
    home_team_id: int | None = None,
    away_team_id: int | None = None,
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Marca duplicados y tandas sin modificar las filas fuente."""

    output = [dict(row) for row in rows]
    if _score(rows, home_team_id, away_team_id) == (home_score, away_score):
        return output, {}
    reasons: Counter[str] = Counter()
    _exclude_shootout(output, home_score + away_score, reasons)
    _exclude_repeated_scores(output, reasons)
    _exclude_near_duplicates(output, reasons)
    if _score(output, home_team_id, away_team_id) != (home_score, away_score):
        return [dict(row) for row in rows], {}
    return output, dict(sorted(reasons.items()))


def _score(
    rows: list[dict[str, Any]],
    home_team_id: int | None,
    away_team_id: int | None,
) -> tuple[int, int] | None:
    """Cuenta goles por orientación cuando ambas identidades están disponibles."""

    if home_team_id is None or away_team_id is None:
        return None
    valid = [row for row in rows if _is_goal(row)]
    home = sum(row.get("team_provider_id") == home_team_id for row in valid)
    away = sum(row.get("team_provider_id") == away_team_id for row in valid)
    return home, away


def _is_goal(row: dict[str, Any]) -> bool:
    """Indica si una fila representa un gol todavía válido."""

    return str(row.get("event_type") or "").lower() == "goal" and not bool(
        row.get("annulled")
    )


def _is_penalty_score(row: dict[str, Any]) -> bool:
    """Reconoce la etiqueta ESPN usada durante tandas de penaltis."""

    raw = str(row.get("event_type_raw") or "").lower().replace("-", "_")
    return raw == "penalty___scored" and int(row.get("minute") or 0) >= 90


def _exclude_shootout(
    rows: list[dict[str, Any]],
    expected_goals: int,
    reasons: Counter[str],
) -> None:
    """Excluye una secuencia de tanda incompatible con el marcador final."""

    penalties = [row for row in rows if _is_goal(row) and _is_penalty_score(row)]
    observed = sum(_is_goal(row) for row in rows)
    teams = {row.get("team_provider_id") for row in penalties}
    if len(penalties) < 3 or observed <= expected_goals or len(teams) < 2:
        return
    for row in penalties:
        _exclude(row, "penalty_shootout", reasons)


def _score_signature(row: dict[str, Any]) -> tuple[int, int] | None:
    """Extrae la progresión explícita del texto del proveedor."""

    match = GOAL_SCORE.search(str(row.get("event_text") or ""))
    return (int(match.group(1)), int(match.group(2))) if match else None


def _exclude_repeated_scores(
    rows: list[dict[str, Any]],
    reasons: Counter[str],
) -> None:
    """Conserva la última ocurrencia de una misma progresión publicada."""

    by_score: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for row in rows:
        signature = _score_signature(row) if _is_goal(row) else None
        if signature is not None:
            by_score.setdefault(signature, []).append(row)
    for candidates in by_score.values():
        for row in candidates[:-1]:
            _exclude(row, "repeated_score_snapshot", reasons)


def _exclude_near_duplicates(
    rows: list[dict[str, Any]],
    reasons: Counter[str],
) -> None:
    """Suprime representaciones paralelas del mismo gol ESPN."""

    goals = sorted(
        (row for row in rows if _is_goal(row)),
        key=lambda row: (_seconds(row), int(row.get("event_index") or 0)),
    )
    for index, left in enumerate(goals):
        if not _is_goal(left):
            continue
        for right in goals[index + 1:]:
            if _seconds(right) - _seconds(left) > 10:
                break
            if left.get("team_provider_id") != right.get("team_provider_id"):
                continue
            left_score = _score_signature(left)
            right_score = _score_signature(right)
            if left_score is not None and right_score is not None:
                if left_score != right_score:
                    continue
            _exclude(_less_informative(left, right), "near_duplicate_goal", reasons)
            break


def _seconds(row: dict[str, Any]) -> int:
    """Convierte minuto y segundo a posición temporal comparable."""

    return 60 * int(row.get("minute") or 0) + int(row.get("second") or 0)


def _less_informative(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    """Selecciona la representación con menor evidencia textual."""

    left_score = _score_signature(left)
    right_score = _score_signature(right)
    if left_score is not None and right_score is None:
        return right
    if right_score is not None and left_score is None:
        return left
    return left


def _exclude(
    row: dict[str, Any],
    reason: str,
    reasons: Counter[str],
) -> None:
    """Marca una copia como excluida y contabiliza la decisión."""

    if bool(row.get("annulled")):
        return
    row["annulled"] = True
    row["reconciliation_reason"] = reason
    reasons[reason] += 1
