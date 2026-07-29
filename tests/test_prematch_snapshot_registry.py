"""Pruebas de publicación, activación e integridad de snapshots."""

from __future__ import annotations

import gzip
import json

import pytest

from src.prematch_snapshot_registry import (
    SnapshotRegistryError,
    activate_snapshot,
    publish_snapshot,
    resolve_active_snapshot,
    rollback_snapshot,
)


def _source(path, match_id: int) -> None:
    """Crea un snapshot mínimo con esquema compatible."""

    payload = [{"match_id": match_id, "match_date": "2030-01-01T12:00:00+00:00", "league_slug": "esp.1", "team_id": 1, "is_home": True, "goals": 0}]
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_publish_activate_and_rollback_are_integrity_checked(tmp_path) -> None:
    """El registro conserva dos versiones y vuelve a la anterior."""

    source_a, source_b = tmp_path / "a.json", tmp_path / "b.json"
    _source(source_a, 1)
    _source(source_b, 2)
    registry = tmp_path / "registry"
    first = publish_snapshot(source_a, "snapshot_a", registry)
    publish_snapshot(source_b, "snapshot_b", registry)
    activate_snapshot(first.snapshot_id, registry)
    activate_snapshot("snapshot_b", registry)
    assert resolve_active_snapshot(root=registry).parent.name == "snapshot_b"
    rollback_snapshot(root=registry)
    assert resolve_active_snapshot(root=registry).parent.name == "snapshot_a"


def test_snapshot_tampering_fails_closed(tmp_path) -> None:
    """Una modificación manual no puede entrar al servicio."""

    source = tmp_path / "source.json"
    _source(source, 1)
    registry = tmp_path / "registry"
    publish_snapshot(source, "snapshot_a", registry)
    event_path = registry / "snapshot_a" / "event_windows.json"
    event_path.write_text("[]", encoding="utf-8")
    with pytest.raises(SnapshotRegistryError, match="snapshot_integrity_failed"):
        activate_snapshot("snapshot_a", registry)


def test_compressed_snapshot_preserves_logical_integrity(tmp_path) -> None:
    """El runtime acepta gzip y valida el hash del JSON original."""

    source = tmp_path / "source.json"
    _source(source, 1)
    registry = tmp_path / "registry"
    publish_snapshot(source, "snapshot_a", registry)
    event_path = registry / "snapshot_a" / "event_windows.json"
    compressed = event_path.with_suffix(".json.gz")
    with event_path.open("rb") as source_stream, gzip.open(compressed, "wb") as target_stream:
        target_stream.write(source_stream.read())
    event_path.unlink()
    assert resolve_active_snapshot("snapshot_a", registry) == compressed.resolve()
