"""Materializa la evidencia reproducible de integridad de Fase 113.

Requirements:
    numpy>=2
    pandas>=2

Version: 1.0.0
Created: 2026-08-07
"""
from __future__ import annotations

import hashlib
import json
import math
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_phase_84a_team_count_markets import (  # noqa: E402
    _aggregate_match,
    _read_rows,
)
from scripts.run_phase_104_official_goal_chain import _frame  # noqa: E402
from src.dixon_coles_v1 import low_score_tau  # noqa: E402
from src.team_count_markets import (  # noqa: E402
    negative_binomial_distribution,
)
from src.temporal_integrity import (  # noqa: E402
    aligned_fraction_boundaries,
    kickoff_buckets,
    normalize_kickoff_splits,
    split_boundary_is_causal,
)
from src.universal_prematch import (  # noqa: E402
    UniversalPrematchEngine,
    UpcomingMatchInput,
)

OUTPUT = ROOT / "artifacts/phase_113_model_integrity_audit"
PHASES = {
    "phase84a": ROOT / "artifacts/phase_84a_team_count_markets",
    "phase88": ROOT / "artifacts/phase_88_team_market_markov",
    "phase95": ROOT / "artifacts/phase_95_market_probability_calibration",
    "phase96": ROOT / "artifacts/phase_96_market_dependency_exposure",
    "phase103": ROOT / "artifacts/phase_103_distributional_market_walkforward",
    "phase104": ROOT / "artifacts/phase_104_official_goal_chain",
    "phase105": ROOT / "artifacts/phase_105_historical_1000_complete",
    "phase106": ROOT / "artifacts/phase_106_probability_repair",
}


def _sha(path: Path) -> str:
    """Calcula SHA-256 por streaming."""

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read(path: Path) -> Any:
    """Lee JSON UTF-8."""

    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(name: str, payload: Any) -> None:
    """Escribe JSON determinista."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / name).write_text(
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )


def _artifact_integrity(path: Path) -> dict[str, Any]:
    """Verifica todos los archivos declarados por un manifiesto de hashes."""

    hashes_path = path / "hashes.json"
    if not hashes_path.is_file():
        return {"passed": False, "reason": "hashes_missing"}
    hashes = _read(hashes_path)
    failures = []
    for name, expected in hashes.items():
        candidate = path / str(name)
        if (Path(str(name)).name != str(name) or not candidate.is_file()
                or _sha(candidate) != expected):
            failures.append(str(name))
    return {
        "passed": not failures,
        "declared_files": len(hashes),
        "failed_files": failures,
        "hashes_sha256": _sha(hashes_path),
    }


def _phase74_temporal_audit() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Mide simultáneos y fronteras heredadas sobre el corpus causal."""

    grouped: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for row in _read_rows():
        grouped[int(row["match_id"])].append(row)
    raw_matches = sorted(
        (_aggregate_match(rows) for rows in grouped.values()),
        key=lambda row: (str(row["match_date"]), int(row["match_id"])),
    )
    simultaneous = [bucket for bucket in kickoff_buckets(raw_matches)
                    if len(bucket) > 1]
    raw_cross = [bucket for bucket in kickoff_buckets(raw_matches)
                 if len({row["split"] for row in bucket}) > 1]
    normalized = normalize_kickoff_splits(raw_matches)
    normalized_cross = [bucket for bucket in kickoff_buckets(normalized)
                        if len({row["split"] for row in bucket}) > 1]
    return {
        "matches": len(raw_matches),
        "same_league_kickoff_buckets": len(simultaneous),
        "rows_previously_exposed_to_intra_kickoff_leakage": sum(
            len(bucket) - 1 for bucket in simultaneous),
        "raw_cross_split_kickoff_buckets": len(raw_cross),
        "normalized_cross_split_kickoff_buckets": len(normalized_cross),
        "same_kickoff_batch_safe": len(normalized_cross) == 0,
    }, normalized


def _goal_split_audit() -> dict[str, Any]:
    """Compara fronteras fraccionales antiguas y alineadas."""

    frame = _frame()
    original_violations = 0
    aligned_violations = 0
    eligible_rows = 0
    cold_starts = 0
    leagues = 0
    for _, league in frame.groupby("league_slug", sort=True):
        league = league.sort_values(
            ["match_date", "match_id"]).reset_index(drop=True)
        if len(league) < 40:
            continue
        leagues += 1
        original = (int(len(league) * 0.60), int(len(league) * 0.80))
        original_violations += sum(
            not split_boundary_is_causal(league, boundary)
            for boundary in original)
        aligned = aligned_fraction_boundaries(league, (0.60, 0.80))
        aligned_violations += sum(
            not split_boundary_is_causal(league, boundary)
            for boundary in aligned)
        known = set(league.iloc[:aligned[1]]["home_team_id"]) | set(
            league.iloc[:aligned[1]]["away_team_id"])
        tail = league.iloc[aligned[1]:]
        eligible_rows += len(tail)
        cold_starts += int((
            ~tail["home_team_id"].isin(known)
            | ~tail["away_team_id"].isin(known)).sum())
    return {
        "leagues": leagues,
        "original_split_boundary_violations": original_violations,
        "aligned_split_boundary_violations": aligned_violations,
        "evaluation_rows": eligible_rows,
        "cold_start_rows_excluded": cold_starts,
    }


def _math_audit() -> dict[str, Any]:
    """Comprueba fórmulas exactas y PMF con colas adaptativas."""

    home, away, rho = 1.7, 0.6, 0.12
    dixon_coles = {
        "tau_00": low_score_tau(0, 0, home, away, rho),
        "tau_10": low_score_tau(1, 0, home, away, rho),
        "tau_01": low_score_tau(0, 1, home, away, rho),
        "tau_11": low_score_tau(1, 1, home, away, rho),
    }
    expected = {
        "tau_00": 1.0 - home * away * rho,
        "tau_10": 1.0 + away * rho,
        "tau_01": 1.0 + home * rho,
        "tau_11": 1.0 - rho,
    }
    pmf_checks = []
    for rate, dispersion in ((0.0, 0.2), (8.0, 0.2), (24.0, 0.475)):
        distribution = negative_binomial_distribution(
            rate, dispersion, maximum=30)
        mass = sum(distribution.values())
        mean = sum(count * value for count, value in distribution.items())
        pmf_checks.append({
            "rate": rate, "dispersion": dispersion,
            "support_max": max(distribution), "mass": mass,
            "mean": mean,
            "passed": math.isclose(mass, 1.0, abs_tol=1e-10)
            and math.isclose(mean, rate, abs_tol=1e-8),
        })
    return {
        "dixon_coles_values": dixon_coles,
        "dixon_coles_formula_exact": all(
            math.isclose(dixon_coles[key], value, abs_tol=1e-12)
            for key, value in expected.items()),
        "negative_binomial_checks": pmf_checks,
        "negative_binomial_all_pass": all(
            item["passed"] for item in pmf_checks),
    }


def _runtime_smoke() -> dict[str, Any]:
    """Ejecuta una predicción real contra el snapshot activo sellado."""

    request = UpcomingMatchInput(
        league_slug="esp.1", home_team_id=94, away_team_id=86,
        kickoff_ts="2030-01-10T20:00:00+00:00", match_id=9_900_113)
    prediction = UniversalPrematchEngine().predict(request)
    shadow = prediction.experimental_team_markets or {}
    return {
        "status": prediction.status,
        "router_model": prediction.model,
        "goal_probabilities_normalized": math.isclose(
            prediction.probability_home + prediction.probability_draw
            + prediction.probability_away, 1.0, abs_tol=1e-10),
        "cutoff_causal": prediction.audit.get("cutoff_causal") is True,
        "shadow_status": shadow.get("status"),
        "shadow_market_count": len(shadow.get("probabilities", {})),
        "shadow_audit_passed": all(
            shadow.get("audit", {}).get(key) is True
            for key in (
                "cutoff_causal", "pmf_valid", "probabilities_valid",
                "expected_counts_valid", "over_under_complementary",
                "over_under_monotonic",
            )),
    }


def _experimental_report_audit() -> dict[str, Any]:
    """Clasifica afirmaciones económicas no canónicas sin modificar al usuario."""

    paths = [
        ROOT / "REPORTE_COMPLETO_FIABILIDAD.md",
        ROOT / "scripts/run_phase_110_extended_reliability_evaluation.py",
    ]
    findings = []
    for path in paths:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        synthetic_odds = bool(re.search(
            r"odds_mean|1\s*/\s*(?:prob|p\b)|cuota.*invers",
            text, flags=re.IGNORECASE))
        economic_claims = bool(re.search(
            r"\bROI\b|\bKelly\b|rentabil|parlay|combinad",
            text, flags=re.IGNORECASE))
        blocking_reasons = ["outside_versioned_phase_contract"]
        if synthetic_odds:
            blocking_reasons.append("synthetic_odds_are_not_market_evidence")
        if economic_claims:
            blocking_reasons.append("economic_claims_not_validated")
        findings.append({
            "path": str(path.relative_to(ROOT)),
            "sha256": _sha(path),
            "synthetic_odds_detected": synthetic_odds,
            "economic_claims_detected": economic_claims,
            "canonical": False,
            "promotion_eligible": False,
            "blocking_reasons": blocking_reasons,
        })
    return {
        "files": findings,
        "classification": "noncanonical_outputs_blocked"
        if findings else "no_noncanonical_outputs_detected",
    }


def _metrics() -> dict[str, Any]:
    """Resume la evidencia numérica regenerada."""

    phase84 = _read(PHASES["phase84a"] / "audit.json")
    phase88 = _read(PHASES["phase88"] / "config.json")
    phase104 = _read(PHASES["phase104"] / "metrics.json")
    phase105 = _read(PHASES["phase105"] / "final_report.json")
    phase106 = _read(PHASES["phase106"] / "metrics.json")
    return {
        "phase84a_enabled_shadow_markets": phase84[
            "enabled_shadow_markets"],
        "phase88_enabled_shadow_markets": phase88[
            "enabled_shadow_markets"],
        "phase104": phase104,
        "phase105": {
            "matches": phase105["coverage"]["matches"],
            "decisions": phase105["coverage"]["decisions"],
            "all_markets_accuracy": phase105["families"][
                "all_markets"]["accuracy"],
            "all_markets_log_loss": phase105["families"][
                "all_markets"]["log_loss"],
            "all_markets_normalized_brier": phase105["families"][
                "all_markets"]["normalized_brier"],
            "raw_mixed_brier_suppressed": phase105["families"][
                "all_markets"]["brier"] is None,
            "bootstrap_log_loss_ci95": phase105[
                "bootstrap_log_loss_ci95"],
        },
        "phase106": phase106,
    }


def _report(
    audit: dict[str, Any], metrics: dict[str, Any], coverage: dict[str, Any],
) -> str:
    """Renderiza el informe final de integridad."""

    p104 = metrics["phase104"]
    p105 = metrics["phase105"]
    return "\n".join([
        "# Fase 113 — auditoría integral de modelos", "",
        f"Estado: **{audit['classification']}**.", "",
        "## Integridad matemática y causal", "",
        f"- fórmula Dixon-Coles exacta: `{audit['math']['dixon_coles_formula_exact']}`",
        f"- PMF binomial negativa: `{audit['math']['negative_binomial_all_pass']}`",
        f"- filas antes expuestas intra-kickoff: `{coverage['phase74']['rows_previously_exposed_to_intra_kickoff_leakage']}`",
        f"- fronteras Phase 104 antiguas/alineadas: `{coverage['goal_splits']['original_split_boundary_violations']}` / `{coverage['goal_splits']['aligned_split_boundary_violations']}`",
        f"- cold starts excluidos del gate: `{coverage['goal_splits']['cold_start_rows_excluded']}`",
        f"- artefactos íntegros: `{audit['all_artifacts_valid']}`",
        f"- runtime real válido: `{audit['runtime_passed']}`", "",
        "## Evidencia regenerada", "",
        f"- 1X2 LL: `{p104['1x2']['candidate_log_loss']:.6f}` vs `{p104['1x2']['baseline_log_loss']:.6f}`; pass `{p104['1x2']['passed']}`",
        f"- Over 2.5 LL: `{p104['over_2_5']['candidate_log_loss']:.6f}` vs `{p104['over_2_5']['baseline_log_loss']:.6f}`; pass `{p104['over_2_5']['passed']}`",
        f"- BTTS gate estructural: `{p104['btts']['passed']}`; reparación Fase 106: `{metrics['phase106']['passed']}`",
        f"- Fase 105: `{p105['matches']}` partidos, `{p105['decisions']}` decisiones, accuracy `{p105['all_markets_accuracy']:.2%}`, Brier normalizado `{p105['all_markets_normalized_brier']:.6f}`",
        f"- mercados shadow vigentes: `{len(metrics['phase84a_enabled_shadow_markets']) + len(metrics['phase88_enabled_shadow_markets'])}`", "",
        "## Gobierno", "",
        "Los reportes y scripts fuera del contrato versionado son no canónicos y quedan fuera de promoción. Derivar cuotas de la probabilidad del propio modelo no demuestra ROI, edge ni Kelly.",
        "",
    ])


def run() -> dict[str, Any]:
    """Ejecuta todos los gates y publica el artefacto sellado."""

    phase74, _ = _phase74_temporal_audit()
    goal_splits = _goal_split_audit()
    math_audit = _math_audit()
    runtime = _runtime_smoke()
    artifacts = {
        name: _artifact_integrity(path) for name, path in PHASES.items()}
    reports = _experimental_report_audit()
    metrics = _metrics()
    coverage = {"phase74": phase74, "goal_splits": goal_splits}
    all_artifacts_valid = all(
        item["passed"] for item in artifacts.values())
    runtime_passed = all((
        runtime["status"] == "available",
        runtime["router_model"] == "selective_dc_kalman_official",
        runtime["goal_probabilities_normalized"], runtime["cutoff_causal"],
        runtime["shadow_status"] == "experimental_shadow_not_promoted",
        runtime["shadow_market_count"] == 8,
        runtime["shadow_audit_passed"],
    ))
    passed = all((
        phase74["same_kickoff_batch_safe"],
        goal_splits["aligned_split_boundary_violations"] == 0,
        math_audit["dixon_coles_formula_exact"],
        math_audit["negative_binomial_all_pass"],
        all_artifacts_valid, runtime_passed,
        metrics["phase104"]["1x2"]["passed"],
        metrics["phase104"]["over_2_5"]["passed"],
        not metrics["phase104"]["btts"]["passed"],
        metrics["phase106"]["passed"],
        metrics["phase105"]["raw_mixed_brier_suppressed"],
        all(not item["promotion_eligible"] for item in reports["files"]),
    ))
    audit = {
        "classification": "passed_with_selective_outputs" if passed
        else "failed_keep_fallbacks",
        "math": math_audit,
        "runtime": runtime,
        "runtime_passed": runtime_passed,
        "artifact_integrity": artifacts,
        "all_artifacts_valid": all_artifacts_valid,
        "experimental_reports": reports,
        "official_goal_markets": ["1x2", "over_2_5"],
        "btts_provider": "phase106_causal_league_rate",
        "automatic_staking_enabled": False,
        "odds_roi_clv_kelly_validated": False,
    }
    config = {
        "version": "model_integrity_v1",
        "phase": 113,
        "dixon_coles_orientation": "x_home_y_away",
        "same_kickoff_policy": "predict_all_then_batch_update",
        "split_policy": "complete_kickoff",
        "artifact_policy": "verify_all_declared_hashes_fail_closed",
    }
    input_paths = {
        "model_integrity_spec": ROOT / "docs/specs/model_integrity_v1.md",
        "phase74_source": ROOT / (
            "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"),
        **{
            f"{name}_hashes": path / "hashes.json"
            for name, path in PHASES.items()
        },
    }
    manifest = {
        name: {"path": str(path.relative_to(ROOT)), "sha256": _sha(path)}
        for name, path in input_paths.items()
    }
    report = _report(audit, metrics, coverage)
    _write_json("config.json", config)
    _write_json("input_manifest.json", manifest)
    _write_json("coverage.json", coverage)
    _write_json("audit.json", audit)
    _write_json("metrics.json", metrics)
    for name in ("validation_report.md", "final_report.md"):
        (OUTPUT / name).write_text(report, encoding="utf-8")
    _write_json("hashes.json", {
        path.name: _sha(path) for path in sorted(OUTPUT.iterdir())
        if path.name != "hashes.json"
    })
    return {"audit": audit, "coverage": coverage, "metrics": metrics}


if __name__ == "__main__":
    RESULT = run()
    if RESULT["audit"]["classification"] != "passed_with_selective_outputs":
        raise SystemExit(1)
    print(json.dumps({
        "classification": RESULT["audit"]["classification"],
        "shadow_market_count": RESULT["audit"]["runtime"][
            "shadow_market_count"],
    }, sort_keys=True))
