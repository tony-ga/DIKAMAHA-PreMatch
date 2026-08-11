"""Pruebas del descubrimiento multi-liga."""

import json

import scripts.run_multileague_discovery as discovery
from scripts.run_multileague_discovery import _dates, _merge_references


def _reference(league: str, match_id: str) -> dict[str, str]:
    """Construye una referencia mínima con la clave de deduplicación."""

    return {"league_slug": league, "provider_match_id": match_id, "competition_id": match_id}


def test_incremental_discovery_keeps_previously_discovered_leagues(tmp_path, monkeypatch) -> None:
    """Descubrir una liga nueva no puede borrar las ligas ya descubiertas.

    Fase 53 valida contra ``references.json`` antes de ingerir, de modo que
    reescribirlo con sólo las ligas de la corrida dejaría al resto del catálogo
    como ``undocumented_league``.
    """

    monkeypatch.setattr(discovery, "OUTPUT", tmp_path)
    (tmp_path / "references.json").write_text(
        json.dumps([_reference("esp.1", "1"), _reference("eng.1", "2")]), encoding="utf-8")

    rows, audit = _merge_references([_reference("ned.1", "3")], replace=False)

    assert {row["league_slug"] for row in rows} == {"esp.1", "eng.1", "ned.1"}
    assert audit["mode"] == "merge"
    assert audit["previous_reference_count"] == 2
    assert audit["merged_reference_count"] == 3


def test_rediscovering_the_same_match_does_not_duplicate_it(tmp_path, monkeypatch) -> None:
    """La clave liga/partido/competición mantiene la fusión idempotente."""

    monkeypatch.setattr(discovery, "OUTPUT", tmp_path)
    (tmp_path / "references.json").write_text(
        json.dumps([_reference("esp.1", "1")]), encoding="utf-8")

    rows, audit = _merge_references([_reference("esp.1", "1")], replace=False)

    assert len(rows) == 1
    assert audit["merged_reference_count"] == 1


def test_replace_mode_rebuilds_references_from_scratch(tmp_path, monkeypatch) -> None:
    """La reconstrucción total sigue disponible, pero debe pedirse explícitamente."""

    monkeypatch.setattr(discovery, "OUTPUT", tmp_path)
    (tmp_path / "references.json").write_text(
        json.dumps([_reference("esp.1", "1")]), encoding="utf-8")

    rows, audit = _merge_references([_reference("ned.1", "3")], replace=True)

    assert {row["league_slug"] for row in rows} == {"ned.1"}
    assert audit["mode"] == "replace"
    assert audit["previous_reference_count"] == 0


def test_dates_are_inclusive() -> None:
    """La fecha final se incluye en el recorrido."""

    assert _dates("20251201", "20251203") == ["20251201", "20251202", "20251203"]


def test_date_window_is_bounded() -> None:
    """El discovery evita rangos excesivos por corrida."""

    try:
        _dates("20240101", "20260101")
    except ValueError as error:
        assert str(error) == "date_range_exceeds_366_days"
    else:
        raise AssertionError("expected bounded date range")

# Version: 1.0.0
# Created: 2026-07-26
