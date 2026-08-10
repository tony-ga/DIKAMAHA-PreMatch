"""Pruebas del proxy visual PNG sin red."""
import json
from datetime import datetime, timezone
from pathlib import Path

from src.dikamaha_service import _global_team_search, _upcoming_dates
from src.espn_prospective_connector import _valid_scoreboard_dates
from src.espn_user_explorer import LEAGUES
from src.provider_media import _transparent_png

ROOT = Path(__file__).resolve().parents[1]


def _png(color_type: int, extra: bytes = b"") -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00" * 17 + bytes([color_type]) + extra


def test_accepts_png_with_alpha_channel() -> None:
    assert _transparent_png(_png(6))


def test_rejects_opaque_and_non_png_media() -> None:
    assert not _transparent_png(_png(2))
    assert not _transparent_png(b"jpeg")


def test_scoreboard_range_and_upcoming_window_are_bounded() -> None:
    assert _valid_scoreboard_dates("20260809-20260822")
    assert not _valid_scoreboard_dates("20260822-20260809")
    assert len(_upcoming_dates(datetime(2026, 8, 9, tzinfo=timezone.utc), None)) == 14


def test_global_team_search_preserves_league_identity() -> None:
    class Explorer:
        def teams(self, league: str, query: str) -> list[dict[str, str]]:
            return [{"id": "1", "name": query, "league_slug": league}]

    rows = _global_team_search(Explorer(), "Barnet")  # type: ignore[arg-type]
    assert len(rows) == 49
    assert {row["league_slug"] for row in rows} >= {"eng.4", "eng.5"}


def test_visual_catalog_matches_the_49_phase_36_competitions() -> None:
    config = json.loads((
        ROOT / "artifacts" / "phase_36_multileague_discovery" / "config.json"
    ).read_text(encoding="utf-8"))

    assert len(LEAGUES) == 49
    assert {slug for slug, _ in LEAGUES} == set(config["leagues"])
