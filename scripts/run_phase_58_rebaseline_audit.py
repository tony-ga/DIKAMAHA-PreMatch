"""Audita el baseline Dixon-Coles/Kalman antes de rediseñar Markov.

La fase sólo lee artefactos existentes, no entrena ni activa modelos. Su salida
define el punto de comparación obligatorio para el Markov residual selectivo.

Requirements:
    - Python 3.10+

Version: 1.0.0
Created: 2026-07-27
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_58_rebaseline_audit_v1"
LOGGER = logging.getLogger(__name__)


def _load(relative: str) -> dict[str, Any]:
    """Carga un artefacto JSON obligatorio del repositorio."""

    path = ROOT / relative
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"artifact_not_object:{relative}")
    return payload


def _comparison(metrics: dict[str, Any]) -> dict[str, Any]:
    """Resume el OOS canónico por mercado y selecciona el menor log-loss."""

    confirmation = metrics["confirmation"]
    markets = {"log_loss_1x2": "log_loss_1x2", "log_loss_btts": "log_loss_btts", "log_loss_over_2_5": "log_loss_over_2_5"}
    winners = {market: min(confirmation, key=lambda name: float(confirmation[name][metric])) for market, metric in markets.items()}
    dc = confirmation["dixon_coles"]
    dck = confirmation["dixon_coles_kalman"]
    deltas = {metric: float(dck[metric]) - float(dc[metric]) for metric in markets.values()}
    return {"confirmation": confirmation, "market_winners": winners, "dixon_coles_kalman_minus_dixon_coles": deltas, "dc_kalman_beats_dc_all_markets": all(value < 0 for value in deltas.values())}


def _write(name: str, payload: Any) -> None:
    """Escribe un artefacto JSON con reemplazo atómico."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    target = OUTPUT / name
    temporary = target.with_suffix(target.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
    temporary.replace(target)


def run() -> dict[str, Any]:
    """Construye la auditoría de rebaseline sin modificar el router."""

    metrics = _load("artifacts/phase_05_evaluation_protocol_v1/metrics.json")
    bootstrap = _load("artifacts/phase_05_evaluation_protocol_v1/bootstrap_results.json")
    kalman_dry_run = _load("artifacts/phase_3_13_kalman_v2_real_dry_run/kalman_v2_comparisons.json")
    comparison = _comparison(metrics)
    audit = {"classification": "rebaseline_audit_ready_for_new_oos", "canonical_oos": comparison, "canonical_bootstrap": bootstrap, "kalman_dry_run": kalman_dry_run, "production_router_changed": False, "markov_promoted": False, "new_training_executed": False, "independent_cohort_available": False, "next_gate": "lock_dc_kalman_and_collect_independent_cohort", "known_limitations": ["dc_kalman_no_supera_a_dc_en_todos_los_mercados", "kalman_dry_run_no_es_evidencia_confirmatoria", "no_hay_cohorte_independiente_bloqueada"]}
    _write("audit.json", audit)
    report = ["# Fase 58 — auditoría de rebaseline", "", f"**Clasificación:** `{audit['classification']}`", "", "## Hallazgos", "", f"- Dixon-Coles + Kalman supera a Dixon-Coles en todos los mercados OOS canónicos: `{comparison['dc_kalman_beats_dc_all_markets']}`", f"- ganador 1X2: `{comparison['market_winners']['log_loss_1x2']}`", f"- ganador BTTS: `{comparison['market_winners']['log_loss_btts']}`", f"- ganador Over 2.5: `{comparison['market_winners']['log_loss_over_2_5']}`", "- el dry-run de Kalman se conserva como diagnóstico, no como confirmación", "- Markov no fue promovido y el router no cambió", "", "## Siguiente gate", "", "Bloquear el comparador Dixon-Coles/Kalman por fold, recopilar una cohorte independiente y evaluar después un Markov residual selectivo."]
    (OUTPUT / "final_report.md").write_text("\n".join(report) + "\n", encoding="utf-8")
    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "hashes.json"}
    _write("hashes.json", hashes)
    return audit


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
    try:
        LOGGER.info("Fase 58: %s", run()["classification"])
    except (OSError, ValueError, json.JSONDecodeError) as error:
        LOGGER.error("Auditoría de rebaseline rechazada: %s", error)
        raise SystemExit(2) from error

# Version: 1.0.0
# Created: 2026-07-27
