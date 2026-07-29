"""Pruebas de clasificación e ingesta raw-first para Fase 100."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from src.espn_context_ingestion import (
    EspnContextIngestionService,
    IngestionConfig,
    _athlete_ids,
    _team_ids,
)
from src.prematch_data_contracts import StoredRawResponse


class _Provider:
    """Proveedor raw-first sintético que impide realizar red en pruebas."""

    def __init__(self) -> None:
        """Inicializa registros y payloads de replay deterministas."""

        self.calls: list[str] = []
        self.payloads: dict[int, dict[str, Any]] = {}

    def fetch(self, resource: str, **_kwargs: Any) -> StoredRawResponse:
        """Confirma una captura cruda simulada antes de permitir replay."""

        identifier = len(self.calls) + 1
        self.calls.append(resource)
        self.payloads[identifier] = _payload(resource)
        now = datetime(2026, 7, 29, tzinfo=timezone.utc)
        return StoredRawResponse(identifier, "a" * 64, "b" * 64, now)

    def replay(self, response_id: int) -> dict[str, Any]:
        """Devuelve sólo el payload previamente confirmado."""

        return self.payloads[response_id]


def _payload(resource: str) -> dict[str, Any]:
    """Entrega formas mínimas válidas para cada recurso probado."""

    if resource == "teams":
        return {"sports": [{"leagues": [{"teams": [{"team": {"id": "7"}}]}]}]}
    return {"events": []} if resource == "scoreboard" else {"items": []}


def test_ingestion_uses_raw_first_and_excludes_sensitive_classes_by_default() -> None:
    """La captura base no solicita live, settlement, odds ni perfiles masivos."""

    provider = _Provider()
    service = EspnContextIngestionService(provider, IngestionConfig(max_teams_per_league=1))  # type: ignore[arg-type]
    result = service.ingest_league("mex.1", ["20260729"])
    assert result["raw_first"] is True
    assert {"team", "roster", "team_schedule", "injuries"}.issubset(provider.calls)
    assert not {"odds", "plays", "situation", "probabilities", "athlete"}.intersection(provider.calls)


def test_identity_extractors_only_accept_explicit_numeric_ids() -> None:
    """No se fabrican identidades desde texto ni referencias parciales."""

    teams = {"sports": [{"leagues": [{"teams": [
        {"team": {"id": "2"}}, {"team": {"id": "invalid"}}, {"team": {"id": "1"}},
    ]}]}]}
    athletes = {"items": [{"id": "9"}, {"id": "x"}, {"id": "3"}]}
    assert _team_ids(teams) == ["1", "2"]
    assert _athlete_ids(athletes) == ["3", "9"]


# Version: 1.0.0
# Created: 2026-07-29
