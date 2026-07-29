"""Pruebas aisladas del conector ESPN sin tráfico externo ni PostgreSQL."""

from __future__ import annotations

from pathlib import Path

from src.espn_prospective_connector import (
    EspnConnectorConfig, EspnConnectorError, EspnProspectiveConnector,
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


# Version: 1.0.0
# Created: 2026-07-16
