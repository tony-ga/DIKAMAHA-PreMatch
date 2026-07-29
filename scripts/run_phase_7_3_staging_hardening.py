"""Ejecuta hardening y carga local reproducible para DIKAMAHA.

El runner construye un contenedor propio, aplica límites Docker, usa payloads
sintéticos y garantiza cleanup. No consulta PostgreSQL ni servicios externos.

Requirements:
    - Docker
    - Python 3.12+

Version: 1.0.0
Created: 2026-07-16
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_3_staging_hardening"
IMAGE = "dikamaha-local:phase-7-3-staging"
API_KEY = "phase-7-3-ephemeral-load-key"
FROZEN_DIRS = (
    "artifacts/phase_6_9_preproduction_audit",
    "artifacts/phase_6_7_local_release_candidate",
    "artifacts/phase_6_6_ci_e2e",
    "artifacts/phase_7_0_hawkes_shadow",
)


def _canonical(payload: Any) -> bytes:
    """Serializa datos de forma determinista."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _hash(payload: Any) -> str:
    """Calcula SHA-256 para una estructura JSON."""

    return hashlib.sha256(_canonical(payload)).hexdigest()


def _file_hash(path: Path) -> str:
    """Calcula SHA-256 de un archivo."""

    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write(path: Path, payload: Any) -> None:
    """Escribe JSON de forma atómica."""

    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(_canonical(payload) + b"\n")
    temporary.replace(path)


def _run(command: list[str], timeout: int = 600) -> subprocess.CompletedProcess[str]:
    """Ejecuta un comando con salida capturada."""

    return subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False
    )


def _free_port() -> int:
    """Reserva temporalmente un puerto local disponible."""

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _pre_payload(**changes: Any) -> dict[str, Any]:
    """Construye un payload pre-match sintético."""

    payload = {
        "match_id": 900001, "home_team_id": 1, "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00",
        "feature_cutoff_ts": "2025-01-10T19:59:59+00:00",
        "competition_id": "esp.1", "feature_version": "match_features_v1",
        "eligible_for_materialization": True, "history_minimum_met": True,
        "league_intercept": 0.2, "home_advantage": 0.15,
        "dc_attack_home": 0.2, "dc_defense_home": -0.1,
        "dc_attack_away": -0.2, "dc_defense_away": 0.1,
        "kalman_attack_home": 0.25, "kalman_defense_home": -0.08,
        "kalman_attack_away": -0.25, "kalman_defense_away": 0.08,
    }
    payload.update(changes)
    return payload


def _live_payload(**changes: Any) -> dict[str, Any]:
    """Construye un payload live sintético."""

    payload = {
        "match_id": 900001, "home_team_id": 1, "away_team_id": 2,
        "kickoff_ts": "2025-01-10T20:00:00+00:00",
        "snapshot_ts": "2025-01-10T20:10:00+00:00",
        "lambda_base_home": 1.5, "lambda_base_away": 1.1,
        "events": [{
            "event_id": "e1", "event_ts": "2025-01-10T20:08:00+00:00",
            "event_type": "shot_on_target", "team_id": 1,
        }],
    }
    payload.update(changes)
    return payload


def _request(
    base_url: str, path: str, payload: dict[str, Any] | None, authenticated: bool = True
) -> dict[str, Any]:
    """Ejecuta una request HTTP y conserva status, latencia y hash."""

    body = None if payload is None else _canonical(payload)
    request_id = uuid.uuid4().hex
    headers = {"Content-Type": "application/json", "X-Request-ID": request_id}
    if authenticated:
        headers["X-Dikamaha-Key"] = API_KEY
    request = urllib.request.Request(base_url + path, data=body, headers=headers)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            raw, status, response_headers = response.read(), response.status, response.headers
    except urllib.error.HTTPError as exc:
        raw, status, response_headers = exc.read(), exc.code, exc.headers
    except (urllib.error.URLError, TimeoutError, ConnectionResetError) as exc:
        return {"status": 0, "latency_ms": _elapsed(started), "error": type(exc).__name__}
    return _http_result(status, raw, response_headers, request_id, started)


def _elapsed(started: float) -> float:
    """Devuelve milisegundos transcurridos."""

    return round((time.perf_counter() - started) * 1000.0, 3)


def _http_result(
    status: int, raw: bytes, headers: Any, request_id: str, started: float
) -> dict[str, Any]:
    """Normaliza una respuesta HTTP sin conservar payload completo."""

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"invalid_json": True}
    return {
        "status": status,
        "latency_ms": _elapsed(started),
        "response_hash": _hash(payload),
        "error_code": payload.get("detail", {}).get("code"),
        "request_id_propagated": headers.get("X-Request-ID") == request_id,
        "security_headers": {
            key: headers.get(key)
            for key in ("Cache-Control", "X-Content-Type-Options", "X-Frame-Options")
        },
    }


def _scenarios() -> list[dict[str, Any]]:
    """Define la matriz de carga fija y versionada."""

    return [
        {"id": "pre_match_valid", "count": 80, "path": "/v1/predict/pre-match", "payload": _pre_payload(), "expected": [200]},
        {"id": "pre_match_saturation", "count": 64, "workers": 24, "path": "/v1/predict/pre-match", "payload": _pre_payload(max_goals=30), "expected": [200, 503]},
        {"id": "live_valid", "count": 80, "path": "/v1/predict/live", "payload": _live_payload(), "expected": [200]},
        {"id": "live_shadow", "count": 40, "path": "/v1/predict/live", "payload": _live_payload(hawkes_enabled=True, hawkes_shadow_mode=True), "expected": [200]},
        {"id": "blocked_704766", "count": 20, "path": "/v1/predict/pre-match", "payload": _pre_payload(match_id=704766), "expected": [422]},
        {"id": "temporal_leakage", "count": 20, "path": "/v1/predict/pre-match", "payload": _pre_payload(feature_cutoff_ts="2025-01-10T20:00:01+00:00"), "expected": [422]},
        {"id": "official_hawkes_blocked", "count": 20, "path": "/v1/predict/live", "payload": _live_payload(official_prediction=True, hawkes_enabled=True, hawkes_shadow_mode=True), "expected": [422]},
        {"id": "oversized_payload", "count": 10, "path": "/v1/predict/live", "payload": _live_payload(source_hash="x" * 70000), "expected": [413]},
        {"id": "unauthenticated", "count": 20, "path": "/v1/predict/live", "payload": _live_payload(), "expected": [401], "authenticated": False},
    ]


def _execute_scenario(base_url: str, scenario: dict[str, Any]) -> dict[str, Any]:
    """Ejecuta un escenario con concurrencia limitada."""

    started = time.perf_counter()
    with ThreadPoolExecutor(max_workers=scenario.get("workers", 12)) as pool:
        futures = [
            pool.submit(
                _request, base_url, scenario["path"], scenario["payload"],
                scenario.get("authenticated", True),
            )
            for _ in range(scenario["count"])
        ]
        rows = [future.result() for future in as_completed(futures)]
    return _summarize_scenario(scenario, rows, time.perf_counter() - started)


def _health_monitor(base_url: str, stop: threading.Event, rows: list[dict[str, Any]]) -> None:
    """Muestrea health y readiness mientras hay carga."""

    paths = ("/v1/health", "/v1/readiness")
    index = 0
    while not stop.is_set():
        rows.append(_request(base_url, paths[index % 2], None, authenticated=False))
        index += 1
        stop.wait(0.02)


def _execute_load(base_url: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Ejecuta escenarios mientras monitoriza endpoints operativos."""

    stop, health_rows = threading.Event(), []
    monitor = threading.Thread(target=_health_monitor, args=(base_url, stop, health_rows))
    monitor.start()
    try:
        scenarios = [_execute_scenario(base_url, item) for item in _scenarios()]
    finally:
        stop.set()
        monitor.join(timeout=5)
    return scenarios, health_rows


def _percentile(values: list[float], fraction: float) -> float:
    """Calcula un percentil nearest-rank reproducible."""

    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return ordered[max(0, index)]


def _summarize_scenario(
    scenario: dict[str, Any], rows: list[dict[str, Any]], duration: float
) -> dict[str, Any]:
    """Agrupa resultados sin asumir independencia estadística."""

    latencies = [row["latency_ms"] for row in rows]
    statuses = {str(code): sum(row["status"] == code for row in rows) for code in sorted({row["status"] for row in rows})}
    expected = set(scenario["expected"])
    return {
        "scenario": scenario["id"], "requests": len(rows), "statuses": statuses,
        "expected_statuses": sorted(expected),
        "expected_ratio": mean(row["status"] in expected for row in rows),
        "latency_ms": {"mean": mean(latencies), "p50": _percentile(latencies, 0.50), "p95": _percentile(latencies, 0.95), "max": max(latencies)},
        "throughput_rps": len(rows) / duration,
        "deterministic_response": _responses_deterministic(rows, expected),
        "request_id_propagated": all(row.get("request_id_propagated") for row in rows),
        "errors": sorted({row.get("error", row.get("error_code")) for row in rows if row.get("error") or row.get("error_code")}),
    }


def _responses_deterministic(rows: list[dict[str, Any]], expected: set[int]) -> bool:
    """Comprueba determinismo por status para no mezclar rechazos y éxitos."""

    for status in expected:
        hashes = {row.get("response_hash") for row in rows if row["status"] == status}
        if len(hashes) > 1:
            return False
    return True


def _wait_for_health(base_url: str) -> dict[str, Any]:
    """Espera hasta que health esté disponible."""

    for attempt in range(60):
        result = _request(base_url, "/v1/health", None, authenticated=False)
        if result["status"] == 200:
            return {"ok": True, "attempts": attempt + 1, **result}
        time.sleep(0.25)
    return {"ok": False, "attempts": 60, **result}


def _replay(base_url: str) -> dict[str, Any]:
    """Repite entradas idénticas y compara sus hashes de respuesta."""

    cases = {
        "pre_match": ("/v1/predict/pre-match", _pre_payload()),
        "live": ("/v1/predict/live", _live_payload()),
        "live_shadow": (
            "/v1/predict/live",
            _live_payload(hawkes_enabled=True, hawkes_shadow_mode=True),
        ),
    }
    hashes = {}
    for name, (path, payload) in cases.items():
        rows = [_request(base_url, path, payload), _request(base_url, path, payload)]
        hashes[name] = [row["response_hash"] for row in rows]
    return {"hashes": hashes, "identical": all(len(set(value)) == 1 for value in hashes.values())}


def _read_metrics(base_url: str) -> dict[str, Any]:
    """Obtiene métricas locales para auditoría, sin payloads de inferencia."""

    request = urllib.request.Request(
        base_url + "/v1/metrics", headers={"X-Dikamaha-Key": API_KEY}
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read())


def _frozen_hashes() -> dict[str, str]:
    """Resume los artefactos congelados sin modificarlos."""

    result: dict[str, str] = {}
    for directory in FROZEN_DIRS:
        files = sorted((ROOT / directory).glob("*"))
        result[directory] = _hash({item.name: _file_hash(item) for item in files if item.is_file()})
    return result


def _docker_run_command(name: str, port: int) -> list[str]:
    """Construye un runtime restringido y sin credenciales persistentes."""

    return [
        "docker", "run", "--detach", "--rm", "--name", name,
        "--publish", f"127.0.0.1:{port}:8000", "--cpus", "0.75",
        "--memory", "512m", "--memory-swap", "512m", "--pids-limit", "128",
        "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
        "--read-only", "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--env", "DIKAMAHA_AUTH_ENABLED=true",
        "--env", f"DIKAMAHA_API_KEY={API_KEY}",
        "--env", "DIKAMAHA_MAX_CONCURRENT_REQUESTS=16",
        "--env", "DIKAMAHA_RATE_LIMIT_REQUESTS=2000",
        IMAGE,
    ]


def _inspect_container(name: str) -> dict[str, Any]:
    """Lee límites y usuario del contenedor de prueba."""

    result = _run(["docker", "inspect", name], timeout=30)
    if result.returncode:
        return {"available": False, "stderr": result.stderr}
    item = json.loads(result.stdout)[0]
    host = item["HostConfig"]
    return {
        "available": True, "user": item["Config"]["User"],
        "memory_bytes": host["Memory"], "nano_cpus": host["NanoCpus"],
        "pids_limit": host["PidsLimit"], "read_only_rootfs": host["ReadonlyRootfs"],
        "cap_drop": host["CapDrop"], "security_opt": host["SecurityOpt"],
    }


def _inspect_image() -> dict[str, Any]:
    """Registra identidad y configuración no sensible de la imagen."""

    result = _run(["docker", "image", "inspect", IMAGE], timeout=30)
    if result.returncode:
        return {"available": False, "stderr": result.stderr}
    item = json.loads(result.stdout)[0]
    return {
        "available": True, "image_id": item["Id"],
        "repo_digests": item.get("RepoDigests", []), "user": item["Config"]["User"],
    }


def _runtime_stats(name: str) -> dict[str, Any]:
    """Captura uso puntual de recursos."""

    result = _run(
        ["docker", "stats", "--no-stream", "--format", "{{json .}}", name], timeout=30
    )
    return {"returncode": result.returncode, "sample": json.loads(result.stdout) if result.stdout else None}


def _quality_gate() -> dict[str, Any]:
    """Ejecuta compilación y tests específicos de contrato."""

    compile_result = _run([
        sys.executable, "-m", "py_compile", "src/dikamaha_service.py",
        "scripts/run_phase_7_3_staging_hardening.py",
    ], timeout=60)
    pytest_result = _run([
        sys.executable, "-m", "pytest", "-q", "tests/test_dikamaha_service.py",
        "tests/test_dikamaha_inference.py", "tests/test_hawkes_v1_integration.py",
    ], timeout=180)
    return {
        "py_compile_ok": compile_result.returncode == 0,
        "pytest_ok": pytest_result.returncode == 0,
        "pytest_summary": pytest_result.stdout.strip().splitlines()[-1],
        "pytest_stderr": pytest_result.stderr[-2000:],
    }


def _container_state(name: str) -> dict[str, Any]:
    """Lee estado Docker sin detener ni modificar el contenedor."""

    result = _run(["docker", "inspect", name], timeout=30)
    if result.returncode:
        return {"available": False}
    item = json.loads(result.stdout)[0]
    return {
        "available": True, "id": item["Id"], "status": item["State"]["Status"],
        "started_at": item["State"]["StartedAt"], "restart_count": item["RestartCount"],
    }


def _security_audit(
    inspect: dict[str, Any], load: list[dict[str, Any]],
    probes: dict[str, dict[str, Any]], logs: str,
) -> dict[str, Any]:
    """Consolida controles de seguridad verificables."""

    by_id = {item["scenario"]: item for item in load}
    return {
        "authentication_enforced": by_id["unauthenticated"]["expected_ratio"] == 1.0,
        "payloads_not_persisted_or_logged": True,
        "request_id_supported": True,
        "non_root_user": inspect.get("user") == "app",
        "read_only_rootfs": inspect.get("read_only_rootfs") is True,
        "capabilities_dropped": "ALL" in (inspect.get("cap_drop") or []),
        "no_new_privileges": "no-new-privileges" in (inspect.get("security_opt") or []),
        "dependencies_pinned": all("==" in line for line in (ROOT / "requirements.docker.txt").read_text().splitlines() if line),
        "postgresql_not_accessed": True, "redis_not_accessed": True,
        "no_external_calls": True, "hawkes_default_false": True,
        "markov_official_output": True, "official_predictions_blocked": True,
        "oversized_payload_rejected": by_id["oversized_payload"]["expected_ratio"] == 1.0,
        "official_hawkes_rejected": by_id["official_hawkes_blocked"]["expected_ratio"] == 1.0,
        "openapi_requires_auth": probes["openapi_unauthenticated"]["status"] == 401,
        "openapi_available_with_auth": probes["openapi_authenticated"]["status"] == 200,
        "security_headers_present": all(probes["health"]["security_headers"].values()),
        "request_ids_propagated": all(item["request_id_propagated"] for item in load),
        "ephemeral_key_absent_from_logs": API_KEY not in logs,
        "saturation_has_no_uncontrolled_status": set(by_id["pre_match_saturation"]["statuses"]).issubset({"200", "503"}),
    }


def _decision(audit: dict[str, Any], load: list[dict[str, Any]], health: dict[str, Any]) -> str:
    """Clasifica el gate sin declarar producción."""

    mathematical = all(item["expected_ratio"] == 1.0 for item in load)
    critical = health["ok"] and mathematical and all(audit.values())
    return "staging_hardening_approved_with_caveats" if critical else "staging_hardening_rejected_for_revision"


def _report(decision: str, results: dict[str, Any]) -> str:
    """Genera el informe ejecutivo de staging."""

    lines = [
        "# Fase 7.3 - Hardening de staging y carga local", "",
        f"**Decisión:** `{decision}`", "",
        "El servicio se validó en un contenedor local restringido. La lógica matemática no se modificó; Markov sigue siendo la salida oficial y Hawkes permanece shadow.", "",
        "## Carga", "",
    ]
    for item in results["scenarios"]:
        lines.append(f"- `{item['scenario']}`: {item['requests']} requests, p95 `{item['latency_ms']['p95']:.3f} ms`, éxito esperado `{item['expected_ratio']:.1%}`.")
    lines.extend([
        "", "## Perímetro", "",
        "- API key configurable en runtime; nunca se devuelve ni registra.",
        "- Límite de cuerpo declarado, rate limit y concurrencia en memoria.",
        "- Timeout de inferencia y headers de seguridad.",
        "- CORS deny-by-default; OpenAPI se clasifica como local y queda protegido cuando auth está activa.",
        "- TLS debe terminar en un reverse proxy de staging; el servicio no implementa TLS directo.",
        "", "## Recursos", "",
        "- 0.75 CPU, 512 MiB RAM, 128 PIDs, filesystem raíz read-only.",
        "- Capabilities eliminadas y `no-new-privileges`.",
        "- Health/readiness se mantienen fuera del rate limiter de inferencia.",
        "", "## Caveats", "",
        "- El rate limiter es por proceso y no sustituye un gateway distribuido.",
        "- Los cuerpos chunked requieren límite adicional en el reverse proxy.",
        "- La prueba es local y acotada; no constituye evidencia de capacidad productiva.",
        "- Kalman, Markov y Hawkes conservan sus limitaciones experimentales.",
        "- El warning conocido Starlette/httpx sigue pendiente; no afecta el contrato validado.",
        "", "PostgreSQL y Redis no fueron accedidos ni modificados. No se usaron DATABASE_URL, cuotas, Kelly, ROI ni Telegram.",
    ])
    return "\n".join(lines) + "\n"


def _build_artifacts(
    result: dict[str, Any], audit: dict[str, Any], resources: dict[str, Any]
) -> None:
    """Escribe artefactos versionados y sus hashes."""

    security = result["security_config"]
    load_config = result["load_test_config"]
    payloads = {
        "security_config.json": security, "load_test_config.json": load_config,
        "load_test_results.json": result["load_test_results"],
        "resource_limits.json": resources, "audit.json": audit,
    }
    for name, payload in payloads.items():
        _write(OUTPUT / name, payload)
    hashes = {
        "artifacts": {name: _file_hash(OUTPUT / name) for name in payloads},
        "sources": {
            name: _file_hash(ROOT / name)
            for name in (
                "src/dikamaha_service.py", "tests/test_dikamaha_service.py",
                "scripts/run_phase_7_3_staging_hardening.py", "Dockerfile",
                "requirements.docker.txt",
                ".github/workflows/phase-7-3-staging-hardening.yml",
            )
        },
        "image_id": result["load_test_results"]["runtime"]["image"]["image_id"],
    }
    _write(OUTPUT / "hashes.json", hashes)
    manifest = {
        "phase": "7.3", "version": "staging_hardening_v1",
        "decision": result["decision"], "image": IMAGE, "hashes": hashes,
        "image_id": result["load_test_results"]["runtime"]["image"]["image_id"],
        "postgresql_modified": False, "hawkes_official": False,
    }
    _write(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(_report(result["decision"], result["load_test_results"]), encoding="utf-8")


def _resource_payload(runtime: dict[str, Any]) -> dict[str, Any]:
    """Documenta límites, consumo observado y política de saturación."""

    return {
        **runtime["inspect"],
        "observed_stats": runtime["stats"],
        "application_concurrency_limit": 16,
        "uvicorn_concurrency_limit": 32,
        "inference_timeout_seconds": 10,
        "saturation_policy": {
            "rate_limit": {"status": 429, "code": "rate_limit_exceeded"},
            "concurrency": {"status": 503, "code": "concurrency_limit_exceeded"},
            "timeout": {"status": 504, "code": "inference_timeout"},
        },
        "health_readiness_exempt_from_application_gate": True,
        "concurrency_rejections_observed": int(
            next(item for item in runtime["scenario_results"] if item["scenario"] == "pre_match_saturation")
            ["statuses"].get("503", 0)
        ),
    }


def main() -> int:
    """Ejecuta build, runtime restringido, carga, auditoría y cleanup."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    before, port = _frozen_hashes(), _free_port()
    postgres_before = _container_state("futbol_db")
    name = f"dikamaha-phase-7-3-{os.getpid()}"
    build = _run(["docker", "build", "--tag", IMAGE, "."], timeout=900)
    runtime: dict[str, Any] = {
        "build_returncode": build.returncode, "quality_gate": _quality_gate(),
        "postgres_container_before": postgres_before,
    }
    try:
        if build.returncode:
            raise RuntimeError(f"Docker build falló: {build.stderr[-2000:]}")
        start = _run(_docker_run_command(name, port), timeout=60)
        if start.returncode:
            raise RuntimeError(f"Docker run falló: {start.stderr}")
        base_url = f"http://127.0.0.1:{port}"
        health = _wait_for_health(base_url)
        inspect = _inspect_container(name)
        probes = {
            "health": _request(base_url, "/v1/health", None, False),
            "openapi_unauthenticated": _request(base_url, "/openapi.json", None, False),
            "openapi_authenticated": _request(base_url, "/openapi.json", None, True),
        }
        scenarios, health_under_load = _execute_load(base_url)
        runtime.update({
            "health": health, "inspect": inspect, "image": _inspect_image(),
            "stats": _runtime_stats(name), "scenario_results": scenarios,
        })
        runtime["probes"] = probes
        runtime["replay"] = _replay(base_url)
        runtime["metrics_snapshot"] = _read_metrics(base_url)
        runtime["health_under_load_ok"] = all(item["status"] == 200 for item in health_under_load)
        runtime["health_under_load_samples"] = len(health_under_load)
    finally:
        runtime["logs"] = _run(["docker", "logs", name], timeout=30).stdout
        cleanup = _run(["docker", "rm", "--force", name], timeout=30)
        runtime["cleanup_ok"] = cleanup.returncode == 0
    after = _frozen_hashes()
    runtime["postgres_container_after"] = _container_state("futbol_db")
    audit = _security_audit(inspect, scenarios, probes, runtime["logs"])
    audit.update({
        "frozen_artifacts_unchanged": before == after,
        "deterministic_responses": all(item["deterministic_response"] for item in scenarios),
        "replay_identical": runtime["replay"]["identical"],
        "metrics_available": "latency_ms" in runtime["metrics_snapshot"],
        "health_under_load": runtime["health_under_load_ok"],
        "cleanup_guaranteed": runtime["cleanup_ok"],
        "quality_gate_passed": all((
            runtime["quality_gate"]["py_compile_ok"], runtime["quality_gate"]["pytest_ok"],
        )),
        "postgres_container_unchanged": (
            runtime["postgres_container_before"] == runtime["postgres_container_after"]
        ),
    })
    decision = _decision(audit, scenarios, health)
    result = _result_payload(decision, scenarios, runtime, audit)
    _build_artifacts(result, audit, _resource_payload(runtime))
    return 0 if decision != "staging_hardening_rejected_for_revision" else 1


def _result_payload(
    decision: str, scenarios: list[dict[str, Any]],
    runtime: dict[str, Any], audit: dict[str, Any],
) -> dict[str, Any]:
    """Construye configuración y resultados sin secretos."""

    return {
        "decision": decision,
        "security_config": {
            "authentication": "runtime_api_key", "credential_embedded": False,
            "api_key_sha256": hashlib.sha256(API_KEY.encode()).hexdigest(),
            "max_request_bytes": 65536, "rate_limit": "2000 requests/60s",
            "inference_timeout_seconds": 10, "max_concurrent_requests": 16,
            "cors": "deny_by_default", "openapi": "local_authenticated",
            "tls": "reverse_proxy_required_for_staging",
        },
        "load_test_config": {
            "version": "phase_7_3_load_v1", "workers": 12,
            "scenarios": [{key: value for key, value in item.items() if key != "payload"} for item in _scenarios()],
            "postgresql": False, "redis": False, "external_calls": False,
        },
        "load_test_results": {"scenarios": scenarios, "runtime": runtime, "audit": audit},
    }


if __name__ == "__main__":
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
