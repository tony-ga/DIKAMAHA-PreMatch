"""Pruebas del contrato de validación ESPN R1."""
from __future__ import annotations

from pathlib import Path

from src.espn_phase_7_15_r1 import _status_counts
from src.espn_prospective_connector import EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector, extract_event_id, scoreboard_references


def test_event_id_extraction_direct_and_reference() -> None:
    """Acepta sólo identificadores numéricos documentados por ESPN."""

    assert extract_event_id({"id": "77"}) == "77"
    assert extract_event_id({"$ref": "https://sports.core.api.espn.com/v2/events/88"}) == "88"
    assert extract_event_id({"id": "bad"}) is None


def test_scoreboard_references_are_competition_scoped() -> None:
    """Extrae event_id y competition_id del primer competition válido."""

    payload = {"events": [{"id": "77", "competitions": [{"id": "88"}]}]}
    assert scoreboard_references(payload) == [{"provider_match_id": "77", "competition_id": "88"}]


def test_status_buckets_use_state() -> None:
    """Distingue scheduled, in_progress y completed del estado ESPN."""

    events = [{"competitions": [{"status": {"type": {"state": state}}}]} for state in ("pre", "in", "post")]
    assert _status_counts(events) == {"scheduled": 1, "in_progress": 1, "completed": 1, "unknown": 0}


def test_summary_endpoint_is_documented_and_event_scoped(tmp_path: Path) -> None:
    """El método summary usa el league configurado y parámetro event."""

    class Response:
        status_code = 200

        def raise_for_status(self) -> None:
            return None

        def json(self) -> dict[str, object]:
            return {"header": {"id": "77"}}

    class Session:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.url = ""
            self.params: dict[str, str] = {}

        def get(self, url: str, params: dict[str, str], timeout: tuple[int, int]) -> Response:
            self.url, self.params = url, params
            return Response()

    session = Session()
    connector = EspnProspectiveConnector(EspnConnectorConfig(league="esp.1", cache_dir=tmp_path), session=session)  # type: ignore[arg-type]
    assert connector.summary("77") == {"header": {"id": "77"}}
    assert session.url.endswith("/soccer/esp.1/summary")
    assert session.params == {"event": "77"}


def test_invalid_summary_id_is_rejected(tmp_path: Path) -> None:
    """Impide construir una consulta summary con ID inventado."""

    connector = EspnProspectiveConnector(EspnConnectorConfig(cache_dir=tmp_path))
    try:
        connector.summary("not-an-id")
    except EspnConnectorError as error:
        assert "invalid_summary_event_id" in str(error)
    else:
        raise AssertionError("summary debe rechazar IDs no numéricos")

# Version: 1.0.0
# Created: 2026-07-16
