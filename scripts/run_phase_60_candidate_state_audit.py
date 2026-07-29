"""Audita el impacto de la taxonomía v1.1 sobre estados tácticos.

No recalibra Markov ni modifica sus artefactos oficiales; compara etiquetas
recalculadas en el candidato contra la intersección con Fase 39.

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import json
import logging
from collections import Counter
from pathlib import Path
from typing import Any

from src.state_labeling_v1 import StateLabelingConfig, label

ROOT = Path(__file__).resolve().parents[1]
INPUT = ROOT / "artifacts/phase_60_taxonomy_snapshot_candidate_v1/event_windows.json"
ACTIVE = ROOT / "artifacts/phase_39_multileague_state_labeling_v1/state_labels.json"
OUTPUT = ROOT / "artifacts/phase_60_candidate_state_audit_v1"
LOGGER = logging.getLogger(__name__)


def _key(row: dict[str, Any]) -> tuple[int, int, int]:
    """Construye clave estable partido-equipo-ventana."""

    return int(row["match_id"]), int(row["team_id"]), int(row["window_index"])


def _labels(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Recalcula estado exclusivamente desde la ventana candidata."""

    config = StateLabelingConfig()
    output = []
    for row in rows:
        state, values = label(row, config)
        output.append({"match_id": row["match_id"], "team_id": row["team_id"], "window_index": row["window_index"], "state": state, **values, "source_hash": row["source_hash"], "label_version": config.version})
    return output


def _compare(candidate: list[dict[str, Any]], active: list[dict[str, Any]]) -> dict[str, Any]:
    """Compara estado y variables derivadas en filas comunes."""

    left, right = {_key(r): r for r in candidate}, {_key(r): r for r in active}
    common = set(left) & set(right)
    changed = [key for key in common if left[key]["state"] != right[key]["state"]]
    changed_matches = sorted({key[0] for key in changed})
    return {"candidate_rows": len(candidate), "active_label_rows": len(active), "common_rows": len(common), "active_only_rows": len(set(right) - set(left)), "candidate_only_rows": len(set(left) - set(right)), "changed_state_rows": len(changed), "changed_state_matches": len(changed_matches), "changed_state_match_sample": changed_matches[:20]}


def run() -> dict[str, Any]:
    """Publica auditoría de estados sin tocar el modelo oficial."""

    rows = json.loads(INPUT.read_text(encoding="utf-8"))
    active = json.loads(ACTIVE.read_text(encoding="utf-8"))
    labels = _labels(rows)
    comparison = _compare(labels, active)
    distribution = dict(Counter(str(row["state"]) for row in labels))
    result = {"classification": "candidate_states_require_markov_recalibration" if comparison["changed_state_rows"] else "candidate_states_equivalent", "candidate_windows": len(rows), "candidate_labels": len(labels), "candidate_state_distribution": distribution, "comparison": comparison, "targets_used": False, "markov_recalibrated": False, "markov_promoted": False, "router_modified": False}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "audit.json").write_text(json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    report = ["# Fase 60 — auditoría de estados del candidato", "", f"**Clasificación:** `{result['classification']}`", "", f"- filas candidatas: `{len(labels)}`", f"- filas comunes con Fase 39: `{comparison['common_rows']}`", f"- estados modificados: `{comparison['changed_state_rows']}`", f"- partidos afectados: `{comparison['changed_state_matches']}`", f"- distribución candidata: `{distribution}`", "- recalibración Markov: `False`", "- router modificado: `False`"]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    LOGGER.info("Auditoría de estados: %s", result["classification"])
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    raise SystemExit(0 if run()["classification"] == "candidate_states_equivalent" else 1)

# Version: 1.0.0
# Created: 2026-07-27
