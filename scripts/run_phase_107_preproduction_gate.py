"""Ejecuta el gate HTTP de preproducción de Fase 107.

# Requirements:
#   requests>=2.31

Version: 1.0.0
Created: 2026-07-29
"""

from __future__ import annotations

import argparse
import json
import logging
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests

LOGGER = logging.getLogger(__name__)
OUTPUT = Path("artifacts/phase_107_railway_user_pilot")


def _parser() -> argparse.ArgumentParser:
    """Construye argumentos reproducibles del gate."""

    parser = argparse.ArgumentParser(description="Gate Fase 107")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--requests", type=int, default=100)
    return parser


def _headers(api_key: str) -> dict[str, str]:
    """Construye headers sin registrar la credencial."""

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-Dikamaha-Key"] = api_key
    return headers


def _fixture(base_url: str, headers: dict[str, str]) -> dict[str, Any]:
    """Obtiene un fixture futuro y reduce su contrato."""

    response = requests.get(
        f"{base_url}/v1/upcoming", params={"limit": 1},
        headers=headers, timeout=40)
    response.raise_for_status()
    fixture = response.json()["fixtures"][0]
    return {key: fixture[key] for key in (
        "league_slug", "home_team_id", "away_team_id", "kickoff_ts",
        "match_id")}


def _one(
    base_url: str, headers: dict[str, str], payload: dict[str, Any], index: int,
) -> dict[str, Any]:
    """Ejecuta una predicción y mide su latencia."""

    started = time.perf_counter()
    try:
        response = requests.post(
            f"{base_url}/v1/predict/upcoming", json=payload,
            headers={**headers, "X-Request-ID": f"phase107-{index}"},
            timeout=45)
        return {"status": response.status_code,
                "latency_ms": (time.perf_counter() - started) * 1000}
    except requests.RequestException:
        return {"status": 0, "latency_ms": (time.perf_counter() - started) * 1000}


def _concurrent(
    base_url: str, headers: dict[str, str],
    payload: dict[str, Any], total: int,
) -> list[dict[str, Any]]:
    """Dispara el lote completo de forma simultánea."""

    with ThreadPoolExecutor(max_workers=total) as pool:
        futures = [
            pool.submit(_one, base_url, headers, payload, index)
            for index in range(total)]
        return [future.result() for future in as_completed(futures)]


def _edge_cases(base_url: str, headers: dict[str, str]) -> dict[str, int]:
    """Comprueba contrato inválido, tamaño y disponibilidad posterior."""

    invalid = requests.post(
        f"{base_url}/v1/predict/upcoming", json={},
        headers=headers, timeout=10)
    large = requests.post(
        f"{base_url}/v1/predict/upcoming", data=b"x" * 70000,
        headers=headers, timeout=10)
    health = requests.get(f"{base_url}/v1/health", timeout=10)
    ready = requests.get(f"{base_url}/v1/readiness", timeout=10)
    return {"invalid": invalid.status_code, "oversized": large.status_code,
            "health": health.status_code, "readiness": ready.status_code}


def _summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Agrega estados, latencia y disponibilidad del lote."""

    statuses: dict[str, int] = {}
    for row in rows:
        key = str(row["status"])
        statuses[key] = statuses.get(key, 0) + 1
    latencies = sorted(float(row["latency_ms"]) for row in rows)
    p95 = latencies[max(0, round(0.95 * len(latencies)) - 1)]
    return {"requests": len(rows), "statuses": statuses,
            "latency_ms": {"mean": statistics.fmean(latencies), "p95": p95,
                           "max": max(latencies)}}


def _classification(summary: dict[str, Any], edges: dict[str, int]) -> str:
    """Aplica el gate conservador sin exigir que todo sea aceptado."""

    statuses = summary["statuses"]
    accounted = sum(statuses.get(str(code), 0) for code in (200, 429, 503))
    edges_ok = edges == {
        "invalid": 422, "oversized": 413, "health": 200, "readiness": 200}
    return "validated" if accounted == summary["requests"] and edges_ok else (
        "rejected_for_revision")


def run(args: argparse.Namespace) -> dict[str, Any]:
    """Ejecuta el gate y persiste evidencia auditable."""

    base_url = args.base_url.rstrip("/")
    headers = _headers(args.api_key)
    payload = _fixture(base_url, headers)
    rows = _concurrent(base_url, headers, payload, args.requests)
    summary = _summary(rows)
    edges = _edge_cases(base_url, headers)
    result = {"classification": _classification(summary, edges),
              "load": summary, "edge_cases": edges}
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT / "audit.json").write_text(
        json.dumps(result, indent=2, sort_keys=True), encoding="utf-8")
    LOGGER.info("phase107_gate_completed classification=%s",
                result["classification"])
    return result


def main() -> int:
    """Ejecuta CLI y devuelve estado distinto de cero al fallar."""

    logging.basicConfig(level=logging.INFO)
    result = run(_parser().parse_args())
    return 0 if result["classification"] == "validated" else 1


if __name__ == "__main__":
    raise SystemExit(main())


# Version: 1.0.0
# Created: 2026-07-29
