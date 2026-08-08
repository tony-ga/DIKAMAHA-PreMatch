"""Pruebas aisladas del conector ESPN sin tráfico externo ni PostgreSQL."""

from __future__ import annotations

from pathlib import Path

from src.espn_prospective_connector import (
    EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector,
    EspnResourceUnavailable,
    scoreboard_references,
)


class _Response:
    """Respuesta HTTP mínima controlada para las pruebas del cliente."""

    def __init__(self, status: int, payload: dict) -> None:
        """Inicializa estado y JSON determinista."""

        self.status_code, self._payload = status, payload

    def raise_for_status(self) -> None:
        """Emula el fallo requests para estados no exitosos."""

        if self.status_code >= 400:
            from requests import HTTPError
            raise HTTPError(f"status={self.status_code}")

    def json(self) -> dict:
        """Devuelve el JSON sintético de la respuesta."""

        return self._payload


class _Session:
    """Sesión sin red que conserva la última llamada para auditoría."""

    def __init__(self, response: _Response) -> None:
        """Inicializa headers y respuesta controlada."""

        self.headers: dict[str, str] = {}
        self.response, self.calls = response, []

    def get(self, url: str, **kwargs: object) -> _Response:
        """Registra una solicitud sin acceder a la red."""

        self.calls.append((url, kwargs))
        return self.response


def _connector(tmp_path: Path, response: _Response) -> EspnProspectiveConnector:
    """Construye cliente sintético con caché temporal aislada."""

    return EspnProspectiveConnector(EspnConnectorConfig(cache_dir=tmp_path, cache_ttl_seconds=0), _Session(response))


def test_scoreboard_uses_only_documented_host_and_date(tmp_path: Path) -> None:
    """Usa scoreboard autorizado y conserva el parámetro dates documentado."""

    connector = _connector(tmp_path, _Response(200, {"events": []}))
    assert connector.scoreboard("20260720") == {"events": []}
    assert connector.session.calls[0][0].startswith("https://site.api.espn.com/")
    assert connector.session.calls[0][1]["params"] == {"dates": "20260720"}


def test_scoreboard_references_ignore_malformed_rows() -> None:
    """No fabrica match IDs ni competición para filas incompletas."""

    payload = {"events": [{"id": "77", "competitions": [{"id": "88"}]}, {"id": "bad"}, "invalid"]}
    assert scoreboard_references(payload) == [{"provider_match_id": "77", "competition_id": "88"}]


def test_rate_limit_is_controlled_without_exposing_payload(tmp_path: Path) -> None:
    """Convierte un 429 en error controlado y no habilita dominios alternos."""

    connector = _connector(tmp_path, _Response(429, {"sensitive": "not_logged"}))
    try:
        connector.calendar()
    except EspnConnectorError as error:
        assert str(error) == "espn_rate_limited"
    else:
        raise AssertionError("429 debe producir error controlado")


def test_circuit_breaker_rejects_after_failures(tmp_path: Path) -> None:
    """Evita nuevas solicitudes tras superar el límite de fallos consecutivos."""

    connector = _connector(tmp_path, _Response(500, {}))
    connector.failures = connector.config.max_failures
    try:
        connector.calendar()
    except EspnConnectorError as error:
        assert str(error) == "circuit_breaker_open"
    else:
        raise AssertionError("el circuito debe bloquear solicitudes adicionales")


def test_site_403_uses_espn_web_fallback_and_preserves_provenance(tmp_path: Path) -> None:
    """Un bloqueo Akamai regional no inutiliza Site API ni oculta el host real."""

    class SequenceSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.calls: list[str] = []

        def get(self, url: str, **_kwargs: object) -> _Response:
            self.calls.append(url)
            return _Response(403, {}) if len(self.calls) == 1 else _Response(200, {"events": []})

    session = SequenceSession()
    connector = EspnProspectiveConnector(
        EspnConnectorConfig(cache_dir=tmp_path), session=session,  # type: ignore[arg-type]
    )

    result = connector.scoreboard_fetch_result("20260720")

    assert result.payload == {"events": []}
    assert result.source_url.startswith("https://site.web.api.espn.com/")
    assert session.calls[0].startswith("https://site.api.espn.com/")
    assert session.calls[1].startswith("https://site.web.api.espn.com/")


def test_site_403_fallback_also_covers_v2_standings_path(tmp_path: Path) -> None:
    """El cambio de host conserva `/apis/v2`, no sólo `/apis/site/v2`."""

    class SequenceSession:
        def __init__(self) -> None:
            self.headers: dict[str, str] = {}
            self.calls: list[str] = []

        def get(self, url: str, **_kwargs: object) -> _Response:
            self.calls.append(url)
            return _Response(403, {}) if len(self.calls) == 1 else _Response(200, {"children": []})

    session = SequenceSession()
    connector = EspnProspectiveConnector(
        EspnConnectorConfig(cache_dir=tmp_path), session=session,  # type: ignore[arg-type]
    )

    request = connector.resource_request("standings")
    result = connector.fetch_request_result(request, use_cache=False)

    assert result.payload == {"children": []}
    assert session.calls == [
        "https://site.api.espn.com/apis/v2/sports/soccer/esp.1/standings",
        "https://site.web.api.espn.com/apis/v2/sports/soccer/esp.1/standings",
    ]
    assert result.source_url == session.calls[1]


def test_site_fallback_fails_closed_when_both_hosts_reject(tmp_path: Path) -> None:
    connector = _connector(tmp_path, _Response(403, {}))

    try:
        connector.scoreboard_fetch_result("20260720", use_cache=False)
    except EspnResourceUnavailable as error:
        assert str(error) == "espn_resource_unavailable:403"
        assert len(connector.session.calls) == 2
    else:
        raise AssertionError("dos respuestas 403 deben fallar cerrado")


# Version: 1.0.0
# Created: 2026-07-16
