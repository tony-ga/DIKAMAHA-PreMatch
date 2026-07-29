"""Taxonomía candidata de estados con amenaza ofensiva y exposición defensiva."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

STATES = ("equilibrio", "presion", "repliegue", "desorganizacion")


@dataclass(frozen=True, slots=True)
class StateLabelingV2Config:
    """Umbrales aprendidos sólo con el bloque de desarrollo."""

    version: str = "state_labeling_v2_candidate"
    pressure_quantile: float = 0.75
    disorganization_quantile: float = 0.75
    retreat_margin_max: float = 0.0


def enrich(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Añade métricas rivales de la misma ventana sin mirar el futuro."""

    index = {(int(row["match_id"]), int(row["team_id"]), int(row["window_index"])): row for row in rows}
    output = []
    for row in rows:
        rival = index.get((int(row["match_id"]), int(row["opponent_team_id"]), int(row["window_index"])), {})
        output.append({**row, "goals_conceded": float(rival.get("goals", 0.0))})
    return output


def derived(row: dict[str, Any]) -> dict[str, float]:
    """Calcula amenaza, exposición, presión y disciplina."""

    threat = 3.0 * float(row["goals"]) + 2.0 * float(row["shots_on_target"]) + 0.5 * float(row["shots"]) + 0.5 * float(row["corners"]) + 0.5 * float(row["pressure"])
    exposure = 3.0 * float(row.get("goals_conceded", 0.0)) + 0.5 * float(row["shots_conceded"]) + 0.5 * float(row["corners_conceded"]) + 0.5 * float(row["pressure_conceded"])
    return {"threat": threat, "exposure": exposure, "threat_margin": threat - exposure, "discipline": float(row["fouls"]) + float(row["yellow_cards"]) + 2.0 * float(row["red_cards"])}


def _quantile(values: list[float], quantile: float) -> float:
    """Calcula cuantíl lineal sin dependencia de pandas."""

    ordered = sorted(values)
    if not ordered:
        return 0.0
    position = (len(ordered) - 1) * quantile
    lower, upper = int(position), min(int(position) + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + fraction * (ordered[upper] - ordered[lower])


def fit(rows: list[dict[str, Any]], config: StateLabelingV2Config | None = None) -> dict[str, Any]:
    """Aprende umbrales cuantílicos desde desarrollo."""

    active = config or StateLabelingV2Config()
    values = [derived(row) for row in rows]
    return {"config": asdict(active), "pressure_threshold": _quantile([item["threat_margin"] for item in values], active.pressure_quantile), "disorganization_threshold": _quantile([item["exposure"] for item in values], active.disorganization_quantile)}


def label(row: dict[str, Any], thresholds: dict[str, Any]) -> tuple[str, dict[str, float]]:
    """Asigna estado v2 usando sólo la ventana actual y marcador inicial."""

    values = derived(row)
    if row.get("event_coverage") != "observed_timeline":
        return "unknown", values
    if float(row["red_cards"]) >= 1.0 or values["exposure"] >= float(thresholds["disorganization_threshold"]):
        return "desorganizacion", values
    if float(row["goal_difference_start"]) >= 1.0 and values["threat_margin"] <= float(thresholds["config"]["retreat_margin_max"]):
        return "repliegue", values
    if values["threat_margin"] >= float(thresholds["pressure_threshold"]):
        return "presion", values
    return "equilibrio", values

