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

from src.ladder_audit import METRIC_LADDERS
from src.ladder_reliability_view import LadderReliabilityView
from src.metric_coverage import MetricCoverage
from src.team_count_market_runtime import (
    DEFAULT_ARTIFACT, ArtifactTeamCountMarketProvider)
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

    El `kickoff_ts` es futuro y no el del partido original: `_validate`
    rechaza cualquier solicitud cuyo kickoff ya pasó, así que fijar la fecha
    real convertía esta prueba en una bomba de tiempo que empezó a fallar el
    mismo 2026-08-13. Lo que la prueba ancla es la liga y los dos equipos
    -de donde sale el veredicto de cobertura-, no el instante; `match_id`
    conserva la trazabilidad del caso reportado.
    """

    request = UpcomingMatchInput(
        league_slug="uefa.europa.conf_qual", home_team_id=6748,
        away_team_id=541, kickoff_ts="2030-01-10T20:00:00+00:00",
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


def test_second_half_is_audited_like_any_other_period() -> None:
    """Invierte el invariante anterior, con motivo medido.

    Hasta Fase 124 esta prueba afirmaba lo contrario -"ningún grupo debe
    declarar `second_half`"- porque `METRIC_LADDERS` no tenía ninguna entrada
    de segunda mitad y ese periodo sólo lo modelaba Markov, sin auditar. El
    usuario pidió explícitamente predicción de segundo tiempo en la escalera
    auditada, así que se añadieron las métricas de 2T al artefacto de
    conteos y se auditaron con los **mismos** gates que el resto: calibración
    medida e intervalo bootstrap sobre la tasa base.

    La prueba no exige que aparezca una métrica concreta: exige que si algo
    de segunda mitad se publica, sea porque pasó la auditoría. Qué métricas
    lo consigan lo decide el dato, no esta prueba.
    """

    shadow = UniversalPrematchEngine().predict(
        _request(match_id=990104)).experimental_team_markets

    rows = shadow["audited_market_ladder_view"]
    second_half = [row for row in rows if row["period"] == "second_half"]
    assert second_half, "la escalera debe cubrir el segundo tiempo tras Fase 124"
    assert all(
        line["reliability"] in {"model_edge", "base_rate_driven"}
        for row in second_half for line in row["lines"])


def test_period_groups_use_the_canonical_period_names() -> None:
    """Los grupos declaran los nombres de periodo que el resto del sistema usa.

    `LADDER_MAXIMUMS` sigue indexando por `"half"` -las dos mitades comparten
    techo-, pero eso ahora se normaliza dentro de `maximums_key` y nunca sale
    al contrato. El resto del sistema -`MARKET_METADATA`,
    `explorer_statistics.periods[side]` y el filtro de periodo del frontend
    (`audited-ladder.tsx`)- sólo reconoce `first_half`/`second_half`/
    `full_match`; cuando `"half"` se publicaba sin traducir, esos grupos
    desaparecían en silencio de la Mini App (DEC-179).
    """

    shadow = UniversalPrematchEngine().predict(
        _request(match_id=990105)).experimental_team_markets

    periods = {row["period"] for row in shadow["audited_market_ladder_view"]}
    assert "half" not in periods
    assert periods <= {"first_half", "second_half", "full_match"}
    assert "first_half" in periods


def test_zero_weight_metrics_are_omitted() -> None:
    """DEC-183: sin señal de equipo, no se publica como si la tuviera.

    `_expected` mezcla `weight * modelo + (1 - weight) * baseline`, y
    `baseline` depende sólo de (liga, localía), nunca del equipo, así que una
    métrica con `weight == 0.0` da el mismo número para cualquier partido de
    esa liga. El invariante se comprueba sobre la función pura porque el
    artefacto vigente ya no tiene ninguna métrica en cero -Fase 124 corrigió
    la fuga que ponía córners a `0.0`-, y aun así la regla debe seguir en pie
    para el día que una métrica futura sí lo esté.
    """

    engine = UniversalPrematchEngine()
    shadow = engine.predict(UpcomingMatchInput(
        league_slug="esp.1", home_team_id=86, away_team_id=17534,
        kickoff_ts="2030-01-10T20:00:00+00:00",
        match_id=990106)).experimental_team_markets
    published = {row["metric"] for row in shadow["audited_market_ladder_view"]}
    assert published, "la escalera no debe llegar vacía en una liga sana"

    config = json.loads(
        (DEFAULT_ARTIFACT / "config.json").read_text(encoding="utf-8"))
    zero_weight = {
        metric for metric, weight in config["model_weights"].items()
        if float(weight) <= 0.0}
    base_metrics = {METRIC_LADDERS[name][0] for name in zero_weight
                    if name in METRIC_LADDERS}
    assert not (base_metrics & published)


def test_corner_intensities_vary_by_team_again() -> None:
    """Regresión del síntoma que DEC-183 diagnosticó, ahora por la causa real.

    El usuario reportó que "Mayor probabilidad" mostraba prácticamente las
    mismas cifras en todos los partidos. DEC-183 lo reprodujo -`home_corners`
    esperado `9.072` y `away_corners` `7.277`, idénticos byte a byte en tres
    enfrentamientos distintos de `esp.1`- y lo atribuyó a que córners no
    tenía señal, retirándolo de la escalera.

    La causa real era una fuga en la selección del peso de mezcla: se hacía
    sobre las filas contaminadas que el resto del pipeline de DEC-173 sí
    excluía. Corregida, el peso de córners es `0.9` y las intensidades
    vuelven a depender de qué dos equipos juegan.
    """

    engine = UniversalPrematchEngine()
    strong_vs_weak = engine.predict(UpcomingMatchInput(
        league_slug="esp.1", home_team_id=86, away_team_id=17534,
        kickoff_ts="2030-01-10T20:00:00+00:00",
        match_id=990106)).experimental_team_markets
    weak_vs_weak = engine.predict(UpcomingMatchInput(
        league_slug="esp.1", home_team_id=17534, away_team_id=95,
        kickoff_ts="2030-01-10T20:00:00+00:00",
        match_id=990107)).experimental_team_markets

    for name in ("corners", "corners_first_half", "shots"):
        first = strong_vs_weak["expected_counts"]["home"][name]
        second = weak_vs_weak["expected_counts"]["home"][name]
        assert first != second, f"{name} no varía por equipo"


# Version: 1.0.0
# Created: 2026-08-12
