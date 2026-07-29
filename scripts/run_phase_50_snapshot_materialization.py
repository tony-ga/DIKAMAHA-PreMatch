"""Materializa y activa el snapshot pre-match versionado inicial.

La operación es idempotente: si el mismo identificador ya existe con el mismo
hash, se reutiliza. Nunca sobrescribe una versión existente con otro contenido.

Requirements:
    - Python 3.10+

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.prematch_snapshot_registry import DEFAULT_REGISTRY_ROOT, activate_snapshot, publish_snapshot, resolve_active_snapshot

OUTPUT = ROOT / "artifacts/phase_50_versioned_snapshot_materialization_v1"
LOGGER = logging.getLogger(__name__)


def _write(name: str, payload: Any) -> None:
    """Publica un artefacto JSON de forma atómica."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def _parser() -> argparse.ArgumentParser:
    """Define los parámetros de materialización."""

    parser = argparse.ArgumentParser(description="Materializa un snapshot pre-match inmutable.")
    parser.add_argument("--source", type=Path, default=ROOT / "artifacts/phase_38_multileague_event_windows_v1/event_windows.json")
    parser.add_argument("--snapshot-id", default="phase38_multileague_v1_20260727")
    parser.add_argument("--root", type=Path, default=DEFAULT_REGISTRY_ROOT)
    parser.add_argument("--no-activate", action="store_true")
    return parser


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Publica, activa y audita la versión solicitada."""

    manifest = publish_snapshot(args.source, args.snapshot_id, args.root)
    activation = None if args.no_activate else activate_snapshot(args.snapshot_id, args.root)
    active_path = resolve_active_snapshot(root=args.root) if not args.no_activate else None
    audit = {
        "classification": "versioned_snapshot_materialized" if args.no_activate else "versioned_snapshot_activated",
        "snapshot_id": manifest.snapshot_id,
        "active_path": str(active_path) if active_path else None,
        "hash_verified": True,
        "immutable_publication": True,
        "rollback_supported": True,
        "canonical_source_overwritten": False,
        "markov_promoted": False,
        "evaluation_executed": False,
    }
    _write("config.json", {"source": str(args.source), "registry_root": str(args.root), "snapshot_id": args.snapshot_id, "activate": not args.no_activate})
    _write("manifest.json", manifest.as_dict())
    _write("activation.json", activation or {"active": False})
    _write("audit.json", audit)
    report = ["# Fase 50 — materialización de snapshot versionado", "", f"**Clasificación:** `{audit['classification']}`", "", f"- snapshot: `{manifest.snapshot_id}`", f"- filas: `{manifest.row_count}`", f"- partidos: `{manifest.match_count}`", f"- ligas: `{manifest.league_count}`", f"- hash verificado: `True`", f"- rollback disponible: `True`", "- snapshot fuente sobrescrito: `False`", "- Markov promovido: `False`", "- evaluación ejecutada: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        LOGGER.info("Fase 50: %s", run(_parser().parse_args())["classification"])
    except (OSError, ValueError, RuntimeError) as error:
        LOGGER.error("Materialización rechazada: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
