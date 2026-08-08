"""Pruebas de paginación completa del timeline ESPN."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from src.espn_prospective_connector import (
    EspnConnectorConfig,
    EspnProspectiveConnector,
    _summary_play_payload,
)


class _Response:
    """Respuesta HTTP mínima."""

    status_code = 200

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        """Simula respuesta correcta."""

    def json(self) -> dict[str, Any]:
        """Devuelve payload configurado."""

        return self._payload


class _Session:
    """Sesión que expone dos páginas deterministas."""

    def __init__(self) -> None:
        self.headers: dict[str, str] = {}
        self.pages: list[int] = []

    def get(self, _url: str, **kwargs: Any) -> _Response:
        """Responde según el parámetro page."""

        page = int(kwargs["params"]["page"])
        self.pages.append(page)
        payload = {"pageIndex": page, "pageCount": 2,
                   "items": [{"id": str(page)}], "count": 2}
        return _Response(payload)


def test_plays_fetches_and_preserves_every_page(tmp_path: Path) -> None:
    """Impide truncar timelines por encima de una página."""

    session = _Session()
    connector = EspnProspectiveConnector(
        EspnConnectorConfig(cache_dir=tmp_path, cache_ttl_seconds=0),
        session=session,
    )
    payload = connector.plays("1", "1")
    assert session.pages == [1, 2]
    assert [row["id"] for row in payload["items"]] == ["1", "2"]
    assert payload["_sourcePageCount"] == 2
    assert len(payload["_sourcePages"]) == 2


def test_live_fetch_bypasses_cache_without_overwriting_it(tmp_path: Path) -> None:
    """Dos polls frescos deben volver a consultar todas las páginas."""

    session = _Session()
    connector = EspnProspectiveConnector(
        EspnConnectorConfig(cache_dir=tmp_path, cache_ttl_seconds=300),
        session=session,
    )
    first = connector.plays_fetch_result("1", "1", use_cache=False)
    second = connector.plays_fetch_result("1", "1", use_cache=False)
    assert session.pages == [1, 2, 1, 2]
    assert first.from_cache is second.from_cache is False
    assert list(tmp_path.glob("*.json")) == []


def test_summary_commentary_fallback_maps_team_identity() -> None:
    """Convierte commentary y conserva el summary raw completo."""

    summary = {
        "header": {"competitions": [{"competitors": [
            {"team": {"id": "10", "displayName": "Local FC"}},
            {"team": {"id": "20", "displayName": "Visitor FC"}},
        ]}]},
        "commentary": [
            {"text": "First Half begins."},
            {"play": {"id": "1", "type": {"type": "goal"},
                      "clock": {"value": 900},
                      "team": {"displayName": "Local FC"}}},
        ],
    }
    core_page = {"items": [], "pageCount": 1}
    result = _summary_play_payload(summary, [core_page])
    assert result["count"] == 1
    assert result["items"][0]["team"]["id"] == "10"
    assert result["_fallbackSummary"] == summary
    assert result["_coreSourcePages"] == [core_page]


# Version: 1.0.0 - 2026-07-27
