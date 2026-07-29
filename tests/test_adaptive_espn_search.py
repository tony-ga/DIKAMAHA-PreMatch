"""Pruebas del planificador de búsqueda adaptativa ESPN."""

from datetime import date

from scripts.run_adaptive_espn_search import SearchWindow, build_windows


def test_build_windows_starts_recent_then_fallback_seasons() -> None:
    """La búsqueda prioriza actualidad y después temporadas completas."""

    windows = build_windows(date(2026, 7, 26), 7, [2025, 2024])
    assert windows == [
        SearchWindow("recent_window", "20260719", "20260726"),
        SearchWindow("season_2025", "20250101", "20251231"),
        SearchWindow("season_2024", "20240101", "20241231"),
    ]


def test_build_windows_does_not_duplicate_years() -> None:
    """Años repetidos no provocan consultas redundantes."""

    windows = build_windows(date(2026, 7, 26), 1, [2025, 2025, 2024])
    assert [item.label for item in windows] == ["recent_window", "season_2025", "season_2024"]

# Version: 1.0.0
# Created: 2026-07-26
