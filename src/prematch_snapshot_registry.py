"""Registro versionado y fail-closed de snapshots pre-match.

Cada snapshot es inmutable después de publicarse. El puntero activo se cambia
de forma atómica y conserva historial suficiente para rollback.

Requirements:
    - Python 3.10+

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import IO, Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGISTRY_ROOT = ROOT / "artifacts/prematch_snapshots"
SNAPSHOT_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
REQUIRED_COLUMNS = frozenset({"match_id", "match_date", "league_slug", "team_id", "is_home", "goals"})


class SnapshotRegistryError(ValueError):
    """Error controlado de integridad, selección o publicación."""


@dataclass(frozen=True, slots=True)
class SnapshotManifest:
    """Metadatos verificables de un snapshot inmutable."""

    snapshot_id: str
    schema_version: str
    source_sha256: str
    row_count: int
    match_count: int
    league_count: int
    leagues: tuple[str, ...]
    created_at_utc: str
    event_windows_path: str

    def as_dict(self) -> dict[str, Any]:
        """Devuelve el manifiesto en formato JSON estable."""

        return {
            "snapshot_id": self.snapshot_id,
            "schema_version": self.schema_version,
            "source_sha256": self.source_sha256,
            "row_count": self.row_count,
            "match_count": self.match_count,
            "league_count": self.league_count,
            "leagues": list(self.leagues),
            "created_at_utc": self.created_at_utc,
            "event_windows_path": self.event_windows_path,
        }


def _root(root: Path | None = None) -> Path:
    """Resuelve el registro desde argumento o variable de entorno."""

    return (root or Path(os.getenv("DIKAMAHA_PREMATCH_SNAPSHOT_ROOT", str(DEFAULT_REGISTRY_ROOT)))).resolve()


def _validate_id(snapshot_id: str) -> None:
    """Rechaza identificadores que puedan escapar del registro."""

    if not SNAPSHOT_ID_PATTERN.fullmatch(snapshot_id):
        raise SnapshotRegistryError("invalid_snapshot_id")


def _sha256(path: Path) -> str:
    """Calcula el hash del archivo por bloques."""

    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _open_snapshot(path: Path, mode: str) -> IO[Any]:
    """Abre snapshots JSON planos o comprimidos con gzip."""

    if path.suffix == ".gz":
        return gzip.open(path, mode, encoding=None if "b" in mode else "utf-8")
    return path.open(mode, encoding=None if "b" in mode else "utf-8")


def _snapshot_sha256(path: Path) -> str:
    """Calcula el hash lógico sobre el JSON descomprimido."""

    digest = hashlib.sha256()
    with _open_snapshot(path, "rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _data_path(registry: Path, snapshot_id: str) -> Path:
    """Selecciona el JSON plano o su representación gzip."""

    directory = registry / snapshot_id
    candidates = (directory / "event_windows.json", directory / "event_windows.json.gz")
    for candidate in candidates:
        if candidate.exists() and candidate.is_file():
            return candidate.resolve()
    raise SnapshotRegistryError("snapshot_integrity_failed")


def _load_rows(source: Path) -> tuple[list[dict[str, Any]], str]:
    """Carga y valida la forma mínima del snapshot fuente."""

    if not source.exists() or not source.is_file():
        raise SnapshotRegistryError("snapshot_source_missing")
    try:
        payload = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotRegistryError("snapshot_source_invalid_json") from error
    if not isinstance(payload, list) or not payload:
        raise SnapshotRegistryError("snapshot_source_empty")
    rows = [row for row in payload if isinstance(row, dict)]
    if len(rows) != len(payload) or any(not REQUIRED_COLUMNS.issubset(row) for row in rows):
        raise SnapshotRegistryError("snapshot_schema_invalid")
    return rows, _sha256(source)


def _manifest(snapshot_id: str, rows: list[dict[str, Any]], source_hash: str) -> SnapshotManifest:
    """Construye metadatos sin conservar payloads adicionales."""

    leagues = tuple(sorted({str(row["league_slug"]) for row in rows}))
    matches = {int(row["match_id"]) for row in rows}
    return SnapshotManifest(snapshot_id, "event_windows_v1", source_hash, len(rows), len(matches), len(leagues), leagues, datetime.now(timezone.utc).isoformat(), f"{snapshot_id}/event_windows.json")


def _write_json(path: Path, payload: Any) -> None:
    """Escribe JSON con reemplazo atómico."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def publish_snapshot(source: Path, snapshot_id: str, root: Path | None = None) -> SnapshotManifest:
    """Publica un snapshot inmutable y devuelve su manifiesto."""

    _validate_id(snapshot_id)
    rows, source_hash = _load_rows(source.resolve())
    registry = _root(root)
    target = registry / snapshot_id
    event_path = target / "event_windows.json"
    manifest_path = target / "manifest.json"
    if target.exists():
        if not manifest_path.exists() or not event_path.exists():
            raise SnapshotRegistryError("snapshot_directory_incomplete")
        existing = load_manifest(snapshot_id, registry)
        if existing.source_sha256 != source_hash:
            raise SnapshotRegistryError("snapshot_id_already_published_with_other_content")
        snapshot_path(snapshot_id, registry)
        return existing
    target.mkdir(parents=True, exist_ok=False)
    try:
        event_path.write_bytes(source.resolve().read_bytes())
        manifest = _manifest(snapshot_id, rows, source_hash)
        _write_json(manifest_path, manifest.as_dict())
    except (OSError, ValueError) as error:
        raise SnapshotRegistryError("snapshot_publication_failed") from error
    return manifest


def load_manifest(snapshot_id: str, root: Path | None = None) -> SnapshotManifest:
    """Carga y valida el manifiesto estructural de un snapshot."""

    _validate_id(snapshot_id)
    path = _root(root) / snapshot_id / "manifest.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if str(payload["snapshot_id"]) != snapshot_id:
            raise SnapshotRegistryError("snapshot_manifest_id_mismatch")
        return SnapshotManifest(snapshot_id=snapshot_id, schema_version=str(payload["schema_version"]), source_sha256=str(payload["source_sha256"]), row_count=int(payload["row_count"]), match_count=int(payload["match_count"]), league_count=int(payload["league_count"]), leagues=tuple(str(item) for item in payload["leagues"]), created_at_utc=str(payload["created_at_utc"]), event_windows_path=str(payload["event_windows_path"]))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
        raise SnapshotRegistryError("snapshot_manifest_invalid") from error


def snapshot_path(snapshot_id: str, root: Path | None = None) -> Path:
    """Valida hash, conteo y ruta antes de devolver el archivo de datos."""

    registry = _root(root)
    manifest = load_manifest(snapshot_id, registry)
    path = _data_path(registry, snapshot_id)
    if registry not in path.parents or _snapshot_sha256(path) != manifest.source_sha256:
        raise SnapshotRegistryError("snapshot_integrity_failed")
    try:
        with _open_snapshot(path, "rt") as stream:
            row_count = len(json.load(stream))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, TypeError) as error:
        raise SnapshotRegistryError("snapshot_integrity_failed") from error
    if row_count != manifest.row_count:
        raise SnapshotRegistryError("snapshot_row_count_mismatch")
    return path


def _active_payload(registry: Path) -> dict[str, Any]:
    """Lee el puntero activo o devuelve un estado vacío."""

    path = registry / "active.json"
    if not path.exists():
        return {"active_snapshot_id": None, "history": []}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise SnapshotRegistryError("active_pointer_invalid") from error
    return payload if isinstance(payload, dict) else {"active_snapshot_id": None, "history": []}


def activate_snapshot(snapshot_id: str, root: Path | None = None) -> dict[str, Any]:
    """Valida y activa un snapshot conservando historial de rollback."""

    registry = _root(root)
    snapshot_path(snapshot_id, registry)
    current = _active_payload(registry)
    history = [snapshot_id] + [str(item) for item in current.get("history", []) if str(item) != snapshot_id]
    payload = {"active_snapshot_id": snapshot_id, "activated_at_utc": datetime.now(timezone.utc).isoformat(), "history": history[:10]}
    registry.mkdir(parents=True, exist_ok=True)
    _write_json(registry / "active.json", payload)
    return payload


def resolve_active_snapshot(snapshot_id: str | None = None, root: Path | None = None) -> Path:
    """Resuelve el snapshot configurado o el puntero activo fail-closed."""

    registry = _root(root)
    selected = snapshot_id or os.getenv("DIKAMAHA_PREMATCH_SNAPSHOT_ID") or _active_payload(registry).get("active_snapshot_id")
    if not selected:
        raise SnapshotRegistryError("active_snapshot_missing")
    return snapshot_path(str(selected), registry)


def rollback_snapshot(snapshot_id: str | None = None, root: Path | None = None) -> dict[str, Any]:
    """Activa el snapshot anterior o uno indicado explícitamente."""

    registry = _root(root)
    current = _active_payload(registry)
    history = [str(item) for item in current.get("history", [])]
    target = snapshot_id or (history[1] if len(history) > 1 else None)
    if not target:
        raise SnapshotRegistryError("rollback_target_missing")
    return activate_snapshot(target, registry)


# Version: 1.1.0
# Created: 2026-07-27
