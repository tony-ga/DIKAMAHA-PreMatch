"""Pruebas de la escalera auditada expuesta en el runtime real.

Distinta de `bounded_market_grid_view` -tres líneas centradas en P(over)≈50%,
sin medir si están calibradas-, esta vista sólo publica lo que
`scripts/run_ladder_audit.py` verificó contra el histórico real. Estas
pruebas usan `ArtifactTeamCountMarketProvider` de verdad, no una
reimplementación: la lección de Fase 118 es que un módulo puede estar
perfectamente probado en aislamiento y no llegar nunca a ejecutarse porque la
composición real no lo conectó.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.ladder_reliability_view import LadderReliabilityView
from src.metric_coverage import MetricCoverage
from src.team_count_market_runtime import ArtifactTeamCountMarketProvider
from src.universal_prematch import UniversalPrematchEngine, UpcomingMatchInput


def _request(league: str = "esp.1", match_id: int = 990100) -> UpcomingMatchInput:
    return UpcomingMatchInput(
        league_slug=league, home_team_id=94, away_team_id=86,
        kickoff_ts="2030-01-10T20:00:00+00:00", match_id=match_id)


def test_healthy_league_exposes_an_audited_ladder() -> None:
    """Usa el artefacto real del repositorio, sin mocks."""

    shadow = UniversalPrematchEngine().predict(
        _request()).experimental_team_markets

    assert shadow is not None
    rows = shadow["audited_market_ladder_view"]
    assert len(rows) > 0
    for row in rows:
        assert row["lines"]
        for line in row["lines"]:
            assert line["reliability"] in ("model_edge", "base_rate_driven")
            assert 0.0 <= line["observed_rate_historical"] <= 1.0
            assert line["sample_size"] > 0


def test_every_line_is_monotonically_ordered_and_bounded() -> None:
    """La probabilidad de superar una línea nunca crece con la línea."""

    shadow = UniversalPrematchEngine().predict(
        _request()).experimental_team_markets

    for row in shadow["audited_market_ladder_view"]:
        lines = sorted(row["lines"], key=lambda item: item["line"])
        probabilities = [line["over_probability"] for line in lines]
        assert all(0.0 <= value <= 1.0 for value in probabilities)
        assert all(
            earlier >= later
            for earlier, later in zip(probabilities, probabilities[1:]))


def test_coverage_suppression_also_applies_to_the_audited_ladder() -> None:
    """`esp.2` no tiene córners ni tiros reales: no deben aparecer aquí."""

    shadow = UniversalPrematchEngine().predict(
        _request("esp.2", 990101)).experimental_team_markets

    metrics = {row["metric"] for row in shadow["audited_market_ladder_view"]}
    assert "corners" not in metrics
    assert "shots" not in metrics


def test_missing_reliability_artifact_yields_an_empty_view(
    tmp_path: Path,
) -> None:
    """Sin evidencia de fiabilidad, no se publica ninguna línea -no todas."""

    provider = ArtifactTeamCountMarketProvider(
        reliability=LadderReliabilityView(tmp_path / "no_existe.json"))
    engine = UniversalPrematchEngine(team_market_provider=provider)

    shadow = engine.predict(_request(match_id=990102)).experimental_team_markets

    assert shadow["audited_market_ladder_view"] == []


def test_missing_coverage_artifact_empties_the_audited_ladder(
    tmp_path: Path,
) -> None:
    """Sin mapa de cobertura, la vista auditada queda vacía.

    **Invierte deliberadamente el invariante anterior** (DEC-174), que
    afirmaba que un mapa ausente no debía vaciar esta vista. DEC-182 mostró
    por qué esa asimetría estaba mal aplicada aquí: los veredictos de
    fiabilidad son globales por (métrica, lado, línea), medidos sobre un
    corpus de ligas sanas, así que una liga sin cobertura medida los heredaba
    y publicaba mercados construidos sobre datos que el proveedor nunca
    entregó -en producción, "menos de 0.5 córners: 96%"-.

    `MetricCoverage.is_absent` conserva su degradación abierta para los
    mercados heredados que protege; sólo esta vista, que ya degradaba cerrada
    ante un artefacto de fiabilidad ausente, extiende esa misma exigencia de
    evidencia positiva a la cobertura.
    """

    provider = ArtifactTeamCountMarketProvider(
        coverage=MetricCoverage(tmp_path / "no_existe.json"))
    engine = UniversalPrematchEngine(team_market_provider=provider)

    shadow = engine.predict(_request(match_id=990103)).experimental_team_markets

    assert shadow["audited_market_ladder_view"] == []


def test_a_league_without_corner_coverage_publishes_no_corner_ladder() -> None:
    """Regresión del caso real Tobol-Partizan (`uefa.europa.conf_qual`).

    El proveedor no entrega córners ni tiros para esa competición y el
    modelo aprendió ~0.65 córners esperados en primera mitad, de modo que la
    escalera llegó a publicar "menos de 0.5" con certeza casi absoluta. Con
    cobertura medida, esas métricas no se publican; las tarjetas, que sí
    tienen cobertura real, siguen disponibles.
    """

    request = UpcomingMatchInput(
        league_slug="uefa.europa.conf_qual", home_team_id=6748,
        away_team_id=541, kickoff_ts="2026-08-13T15:00:00+00:00",
        match_id=401903118)

    shadow = UniversalPrematchEngine().predict(
        request).experimental_team_markets

    metrics = {row["metric"] for row in shadow["audited_market_ladder_view"]}
    assert "corners" not in metrics
    assert "shots" not in metrics
    assert "yellow_cards" in metrics


def test_unavailable_payload_includes_the_empty_field() -> None:
    """El fallback de degradación total declara el campo, no lo omite."""

    payload = ArtifactTeamCountMarketProvider.unavailable("test_reason")

    assert payload["audited_market_ladder_view"] == []


def test_second_half_is_not_claimed_as_audited() -> None:
    """La vista sólo cubre lo que se auditó: Fase 84A, no Fase 88/Markov.

    Ningún grupo de la escalera auditada debe declarar `period ==
    "second_half"`: ese periodo lo modela Markov, no auditado en esta ronda.
    """

    shadow = UniversalPrematchEngine().predict(
        _request(match_id=990104)).experimental_team_markets

    periods = {row["period"] for row in shadow["audited_market_ladder_view"]}
    assert "second_half" not in periods


def test_first_half_groups_use_the_canonical_period_name() -> None:
    """Los grupos de córners/tarjetas de 1ª mitad declaran `"first_half"`.

    `METRIC_LADDERS` usa internamente `"half"` (mismo convenio que
    `LADDER_MAXIMUMS`), pero el resto del sistema -`MARKET_METADATA`,
    `explorer_statistics.periods[side]`, y el filtro de periodo del
    frontend (`audited-ladder.tsx`)- sólo reconoce `"first_half"`. Sin la
    traducción a la salida, estos grupos nunca aparecían en la Mini App: el
    filtro de periodo del frontend los descartaba en silencio.
    """

    shadow = UniversalPrematchEngine().predict(
        _request(match_id=990105)).experimental_team_markets

    periods = {row["period"] for row in shadow["audited_market_ladder_view"]}
    assert "half" not in periods
    assert "first_half" in periods
    first_half_metrics = {
        row["metric"] for row in shadow["audited_market_ladder_view"]
        if row["period"] == "first_half"}
    assert "corners" in first_half_metrics


# Version: 1.0.0
# Created: 2026-08-12
