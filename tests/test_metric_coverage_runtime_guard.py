"""Ancla que el runtime real aplica el guard de cobertura, no sólo el módulo.

Lección de Fase 118: un módulo puede estar perfectamente probado en
aislamiento y no llegar nunca a ejecutarse porque la composición real no lo
conectó. Estas pruebas usan `ArtifactTeamCountMarketProvider` de verdad,
contra el artefacto de cobertura real del repositorio.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.metric_coverage import MetricCoverage
from src.team_count_market_runtime import ArtifactTeamCountMarketProvider


@dataclass(frozen=True)
class _Request:
    """Solicitud mínima: el guard sólo necesita la liga."""

    league_slug: str


def _ladders() -> list[dict[str, object]]:
    return [
        {"key": "home_corners_full_match", "metric": "corners",
         "team_side": "home", "period": "full_match"},
        {"key": "home_corners_first_half", "metric": "corners",
         "team_side": "home", "period": "first_half"},
        {"key": "home_shots_full_match", "metric": "shots",
         "team_side": "home", "period": "full_match"},
        {"key": "home_cards_full_match", "metric": "yellow_cards",
         "team_side": "home", "period": "full_match"},
    ]


def _markets() -> list[str]:
    return [
        "home_corners_over_4_5", "away_corners_over_4_5",
        "home_corners_second_half_over_2_5",
        "away_shots_over_10_5", "shots_on_target_total_over_7_5",
    ]


def test_league_without_corner_coverage_loses_only_corner_markets() -> None:
    """`eng.league_cup` no publica córners pero sí tiros: sólo se retira lo que falta."""

    provider = ArtifactTeamCountMarketProvider()

    markets, ladders = provider._drop_uncovered(
        _Request("eng.league_cup"), _markets(), _ladders())

    assert not any("corners" in key for key in markets)
    assert "away_shots_over_10_5" in markets
    assert "shots_on_target_total_over_7_5" in markets
    assert {row["metric"] for row in ladders} == {"shots", "yellow_cards"}


def test_league_without_shots_data_also_loses_corners() -> None:
    """`esp.2`: el proveedor nunca envía tiros totales -copia tiros a puerta,
    detectado por alias- además de no publicar córners. `shots_on_target` es
    el dato real y verdadero -el alias sólo contamina `shots`-, así que
    sobrevive; sólo se retiran las líneas que dependen del bloque ausente."""

    provider = ArtifactTeamCountMarketProvider()

    markets, ladders = provider._drop_uncovered(
        _Request("esp.2"), _markets(), _ladders())

    assert markets == ["shots_on_target_total_over_7_5"]
    assert {row["metric"] for row in ladders} == {"yellow_cards"}


def test_corner_suppression_covers_every_period() -> None:
    """Una liga que no publica córners tampoco los publica por mitad."""

    provider = ArtifactTeamCountMarketProvider()

    _, ladders = provider._drop_uncovered(
        _Request("eng.league_cup"), _markets(), _ladders())

    assert not any(row["metric"] == "corners" for row in ladders)


def test_covered_league_keeps_everything() -> None:
    """`esp.1` tiene cobertura real: nada se suprime."""

    provider = ArtifactTeamCountMarketProvider()

    markets, ladders = provider._drop_uncovered(
        _Request("esp.1"), _markets(), _ladders())

    assert markets == _markets()
    assert len(ladders) == len(_ladders())


def test_unknown_league_is_never_suppressed() -> None:
    provider = ArtifactTeamCountMarketProvider()

    markets, ladders = provider._drop_uncovered(
        _Request("liga.inexistente"), _markets(), _ladders())

    assert markets == _markets()
    assert len(ladders) == len(_ladders())


def test_missing_coverage_artifact_degrades_open(tmp_path) -> None:
    """Sin artefacto no se suprime nada: exige evidencia positiva de ausencia."""

    provider = ArtifactTeamCountMarketProvider(
        coverage=MetricCoverage(tmp_path / "ausente.json"))

    markets, ladders = provider._drop_uncovered(
        _Request("esp.2"), _markets(), _ladders())

    assert markets == _markets()
    assert len(ladders) == len(_ladders())


def test_request_without_league_is_not_suppressed() -> None:
    provider = ArtifactTeamCountMarketProvider()

    markets, ladders = provider._drop_uncovered(
        _Request(""), _markets(), _ladders())

    assert markets == _markets()
    assert len(ladders) == len(_ladders())


# Version: 1.0.0
# Created: 2026-08-12
