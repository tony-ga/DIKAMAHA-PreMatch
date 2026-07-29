"""Publica, activa y revierte snapshots pre-match versionados.

No modifica el snapshot fuente. La activación sólo cambia un puntero atómico
después de validar hash, esquema y conteo de filas.

Requirements:
    - Python 3.10+

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.prematch_snapshot_registry import (
    DEFAULT_REGISTRY_ROOT,
    SnapshotRegistryError,
    activate_snapshot,
    publish_snapshot,
    resolve_active_snapshot,
    rollback_snapshot,
)

LOGGER = logging.getLogger(__name__)


def _parser() -> argparse.ArgumentParser:
    """Define comandos explícitos de administración."""

    parser = argparse.ArgumentParser(description="Administra snapshots pre-match con rollback.")
    parser.add_argument("--root", type=Path, default=DEFAULT_REGISTRY_ROOT)
    subparsers = parser.add_subparsers(dest="command", required=True)
    publish = subparsers.add_parser("publish")
    publish.add_argument("--source", type=Path, required=True)
    publish.add_argument("--snapshot-id", required=True)
    publish.add_argument("--activate", action="store_true")
    activate = subparsers.add_parser("activate")
    activate.add_argument("snapshot_id")
    rollback = subparsers.add_parser("rollback")
    rollback.add_argument("--to", dest="snapshot_id", default=None)
    subparsers.add_parser("status")
    return parser


def _status(root: Path) -> dict[str, Any]:
    """Devuelve el estado activo sin modificar el registro."""

    try:
        path = resolve_active_snapshot(root=root)
    except SnapshotRegistryError as error:
        return {"active": False, "error": str(error), "root": str(root.resolve())}
    return {"active": True, "path": str(path), "root": str(root.resolve())}


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta una operación de registro y devuelve JSON serializable."""

    root = args.root.resolve()
    if args.command == "publish":
        manifest = publish_snapshot(args.source, args.snapshot_id, root)
        result: dict[str, Any] = {"operation": "publish", "manifest": manifest.as_dict()}
        if args.activate:
            result["activation"] = activate_snapshot(args.snapshot_id, root)
        return result
    if args.command == "activate":
        return {"operation": "activate", "activation": activate_snapshot(args.snapshot_id, root)}
    if args.command == "rollback":
        return {"operation": "rollback", "activation": rollback_snapshot(args.snapshot_id, root)}
    return {"operation": "status", **_status(root)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        print(json.dumps(run(_parser().parse_args()), indent=2, sort_keys=True))
    except (OSError, SnapshotRegistryError, ValueError) as error:
        LOGGER.error("Operación de snapshot rechazada: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
