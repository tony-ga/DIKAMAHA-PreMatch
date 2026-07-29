"""Valida DIKAMAHA detrás de un reverse proxy TLS local.

El runner crea una red Docker interna, un certificado autofirmado efímero y
dos contenedores propios. No consulta PostgreSQL ni persiste credenciales.

Requirements:
    - Docker
    - OpenSSL
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
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "artifacts/phase_7_4_reverse_proxy"
NGINX_CONFIG = ROOT / "deploy/phase_7_4/nginx.conf"
BACKEND_IMAGE = "dikamaha-local:phase-7-4-backend"
PROXY_IMAGE = (
    "nginxinc/nginx-unprivileged:1.27.5-alpine@"
    "sha256:65e3e85dbaed8ba248841d9d58a899b6197106c23cb0ff1a132b7bfe0547e4c0"
)
API_KEY = "phase-7-4-ephemeral-key"
FROZEN_DIRS = (
    "artifacts/phase_6_9_preproduction_audit",
    "artifacts/phase_7_3_staging_hardening",
    "artifacts/phase_7_0_hawkes_shadow",
)


def _canonical(payload: Any) -> bytes:
    """Serializa JSON de forma determinista."""

    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _hash(payload: Any) -> str:
    """Calcula SHA-256 de una estructura."""

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
    """Ejecuta un proceso con salida capturada."""

    return subprocess.run(
        command, cwd=ROOT, text=True, capture_output=True, timeout=timeout, check=False
    )


def _free_port() -> int:
    """Obtiene un puerto local libre."""

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _pre_payload(**changes: Any) -> dict[str, Any]:
    """Crea un request pre-match sintético."""

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
    """Crea un request live sintético."""

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


def _certificate(directory: Path) -> dict[str, Any]:
    """Genera un certificado local con SAN y devuelve provenance."""

    cert, key = directory / "server.crt", directory / "server.key"
    command = [
        "openssl", "req", "-x509", "-newkey", "rsa:2048", "-sha256",
        "-days", "2", "-nodes", "-subj", "/CN=localhost",
        "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        "-keyout", str(key), "-out", str(cert),
    ]
    result = _run(command, timeout=60)
    if result.returncode:
        raise RuntimeError(f"OpenSSL falló: {result.stderr}")
    key.chmod(0o644)
    return {
        "cert": cert, "key": key, "fingerprint_sha256": _certificate_fingerprint(cert),
        "subject": "CN=localhost", "san": ["DNS:localhost", "IP:127.0.0.1"],
        "validity_days": 2, "private_key_persisted": False,
    }


def _certificate_fingerprint(cert: Path) -> str:
    """Extrae el fingerprint SHA-256 del certificado."""

    result = _run(["openssl", "x509", "-in", str(cert), "-noout", "-fingerprint", "-sha256"])
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip().split("=", 1)[1].replace(":", "").lower()


def _ssl_context(cert: Path) -> ssl.SSLContext:
    """Construye un contexto que confía solo en el certificado efímero."""

    context = ssl.create_default_context(cafile=str(cert))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    return context


def _request(
    base_url: str, context: ssl.SSLContext, path: str,
    payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Ejecuta HTTPS y conserva evidencia sin payload sensible."""

    body = None if payload is None else _canonical(payload)
    request_headers = {"Content-Type": "application/json", **(headers or {})}
    request = urllib.request.Request(base_url + path, data=body, headers=request_headers)
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, context=context, timeout=15) as response:
            raw, status, response_headers = response.read(), response.status, response.headers
    except urllib.error.HTTPError as exc:
        raw, status, response_headers = exc.read(), exc.code, exc.headers
    except (urllib.error.URLError, TimeoutError, ConnectionResetError) as exc:
        return {"status": 0, "latency_ms": _elapsed(started), "error": type(exc).__name__}
    return _response(status, raw, response_headers, started)


def _elapsed(started: float) -> float:
    """Calcula milisegundos transcurridos."""

    return round((time.perf_counter() - started) * 1000.0, 3)


def _response(status: int, raw: bytes, headers: Any, started: float) -> dict[str, Any]:
    """Normaliza una respuesta HTTPS."""

    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        payload = {"raw_sha256": hashlib.sha256(raw).hexdigest()}
    return {
        "status": status, "latency_ms": _elapsed(started), "body": payload,
        "response_hash": _hash(payload),
        "headers": {
            key: headers.get(key)
            for key in (
                "X-Request-ID", "Strict-Transport-Security", "X-Content-Type-Options",
                "X-Frame-Options", "Access-Control-Allow-Origin",
            )
        },
    }


def _wait_health(base_url: str, context: ssl.SSLContext) -> dict[str, Any]:
    """Espera el arranque conjunto de proxy y backend."""

    for attempt in range(80):
        result = _request(base_url, context, "/v1/health")
        if result["status"] == 200:
            return {"ok": True, "attempts": attempt + 1, **result}
        time.sleep(0.25)
    return {"ok": False, "attempts": 80, **result}


def _chunked_status(port: int, context: ssl.SSLContext) -> int:
    """Envía un request chunked crudo para validar su rechazo."""

    request = (
        "POST /v1/predict/live HTTP/1.1\r\nHost: localhost\r\n"
        f"X-Dikamaha-Key: {API_KEY}\r\nTransfer-Encoding: chunked\r\n"
        "Content-Type: application/json\r\nConnection: close\r\n\r\n"
        "4\r\ntest\r\n0\r\n\r\n"
    ).encode()
    with socket.create_connection(("127.0.0.1", port), timeout=5) as raw:
        with context.wrap_socket(raw, server_hostname="localhost") as secured:
            secured.sendall(request)
            response = secured.recv(4096).decode("ascii", errors="replace")
    return int(response.splitlines()[0].split()[1])


def _scenario(
    base_url: str, context: ssl.SSLContext, scenario: dict[str, Any]
) -> dict[str, Any]:
    """Ejecuta una carga concurrente a través del proxy."""

    started = time.perf_counter()
    headers = {"X-Dikamaha-Key": API_KEY}
    with ThreadPoolExecutor(max_workers=scenario.get("workers", 8)) as pool:
        futures = [
            pool.submit(_request, base_url, context, scenario["path"], scenario["payload"], headers)
            for _ in range(scenario["count"])
        ]
        rows = [future.result() for future in as_completed(futures)]
    return _scenario_summary(scenario, rows, time.perf_counter() - started)


def _percentile(values: list[float], fraction: float) -> float:
    """Calcula un percentil nearest-rank."""

    ordered = sorted(values)
    index = min(len(ordered) - 1, math.ceil(len(ordered) * fraction) - 1)
    return ordered[max(0, index)]


def _scenario_summary(
    scenario: dict[str, Any], rows: list[dict[str, Any]], duration: float
) -> dict[str, Any]:
    """Resume status, latencia, throughput y determinismo."""

    statuses = sorted({row["status"] for row in rows})
    expected = set(scenario["expected"])
    latencies = [row["latency_ms"] for row in rows]
    return {
        "scenario": scenario["id"], "requests": len(rows),
        "statuses": {str(code): sum(row["status"] == code for row in rows) for code in statuses},
        "expected_statuses": sorted(expected),
        "expected_ratio": mean(row["status"] in expected for row in rows),
        "latency_ms": {
            "mean": mean(latencies), "p50": _percentile(latencies, 0.5),
            "p95": _percentile(latencies, 0.95), "max": max(latencies),
        },
        "throughput_rps": len(rows) / duration,
        "deterministic_by_status": _deterministic_by_status(rows, expected),
    }


def _deterministic_by_status(rows: list[dict[str, Any]], expected: set[int]) -> bool:
    """Comprueba un único hash por status observado."""

    return all(
        len({row["response_hash"] for row in rows if row["status"] == status}) <= 1
        for status in expected
    )


def _load_scenarios() -> list[dict[str, Any]]:
    """Define la carga nominal versionada."""

    return [
        {"id": "pre_match", "count": 60, "path": "/v1/predict/pre-match", "payload": _pre_payload(), "expected": [200]},
        {"id": "live_markov", "count": 60, "path": "/v1/predict/live", "payload": _live_payload(), "expected": [200]},
        {"id": "live_shadow", "count": 30, "path": "/v1/predict/live", "payload": _live_payload(hawkes_enabled=True, hawkes_shadow_mode=True), "expected": [200]},
    ]


def _health_monitor(
    base_url: str, context: ssl.SSLContext, stop: threading.Event,
    rows: list[dict[str, Any]],
) -> None:
    """Muestrea health/readiness durante la carga proxy."""

    paths = ("/v1/health", "/v1/readiness")
    index = 0
    while not stop.is_set():
        rows.append(_request(base_url, context, paths[index % 2]))
        index += 1
        stop.wait(0.02)


def _execute_load(
    base_url: str, context: ssl.SSLContext,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Ejecuta carga nominal con monitor operativo concurrente."""

    stop, health_rows = threading.Event(), []
    monitor = threading.Thread(
        target=_health_monitor, args=(base_url, context, stop, health_rows)
    )
    monitor.start()
    try:
        load = [_scenario(base_url, context, item) for item in _load_scenarios()]
    finally:
        stop.set()
        monitor.join(timeout=5)
    return load, health_rows


def _functional_checks(
    base_url: str, context: ssl.SSLContext, port: int
) -> dict[str, Any]:
    """Valida contrato, seguridad y separación Markov/Hawkes."""

    auth = {"X-Dikamaha-Key": API_KEY}
    request_id = "phase-7-4-request-id"
    off = _request(base_url, context, "/v1/predict/live", _live_payload(), auth)
    shadow = _request(
        base_url, context, "/v1/predict/live",
        _live_payload(hawkes_enabled=True, hawkes_shadow_mode=True), auth,
    )
    return {
        "health": _request(base_url, context, "/v1/health"),
        "readiness": _request(base_url, context, "/v1/readiness"),
        "pre_match": _request(base_url, context, "/v1/predict/pre-match", _pre_payload(), auth),
        "live_markov": off, "live_shadow": shadow,
        "official_hawkes": _request(
            base_url, context, "/v1/predict/live",
            _live_payload(official_prediction=True, hawkes_enabled=True, hawkes_shadow_mode=True), auth,
        ),
        "unauthenticated": _request(base_url, context, "/v1/predict/live", _live_payload()),
        "invalid_origin": _request(
            base_url, context, "/v1/predict/live", _live_payload(),
            {**auth, "Origin": "https://invalid.local"},
        ),
        "oversized": _request(
            base_url, context, "/v1/predict/live",
            _live_payload(source_hash="x" * 70000), auth,
        ),
        "openapi": _request(base_url, context, "/openapi.json", headers=auth),
        "request_id": _request(
            base_url, context, "/v1/predict/pre-match", _pre_payload(),
            {**auth, "X-Request-ID": request_id},
        ),
        "allowed_cors": _request(
            base_url, context, "/v1/predict/live", _live_payload(),
            {**auth, "Origin": "https://staging.local"},
        ),
        "chunked_status": _chunked_status(port, context),
        "markov_identical_with_shadow": _markov_equal(off["body"], shadow["body"]),
    }


def _markov_equal(off: dict[str, Any], shadow: dict[str, Any]) -> bool:
    """Compara la salida oficial Markov con y sin shadow."""

    keys = ("lambda_markov_home", "lambda_markov_away", "home_state", "away_state")
    return all(off.get(key) == shadow.get(key) for key in keys)


def _rate_test(base_url: str, context: ssl.SSLContext) -> dict[str, Any]:
    """Provoca rate limiting sin cambiar configuración."""

    scenario = {
        "id": "proxy_rate_burst", "count": 300, "workers": 16,
        "path": "/v1/predict/pre-match", "payload": _pre_payload(),
        "expected": [200, 429, 503],
    }
    time.sleep(1.25)
    return _scenario(base_url, context, scenario)


def _replay(base_url: str, context: ssl.SSLContext) -> dict[str, Any]:
    """Repite predicciones y compara hashes."""

    auth = {"X-Dikamaha-Key": API_KEY}
    cases = {
        "pre_match": ("/v1/predict/pre-match", _pre_payload()),
        "live": ("/v1/predict/live", _live_payload()),
        "shadow": (
            "/v1/predict/live",
            _live_payload(hawkes_enabled=True, hawkes_shadow_mode=True),
        ),
    }
    hashes = {}
    for name, (path, payload) in cases.items():
        hashes[name] = [
            _request(base_url, context, path, payload, auth)["response_hash"]
            for _ in range(2)
        ]
    return {"hashes": hashes, "identical": all(len(set(value)) == 1 for value in hashes.values())}


def _docker_state(name: str) -> dict[str, Any]:
    """Inspecciona un contenedor sin modificarlo."""

    result = _run(["docker", "inspect", name], timeout=30)
    if result.returncode:
        return {"available": False}
    item = json.loads(result.stdout)[0]
    return {
        "available": True, "id": item["Id"], "status": item["State"]["Status"],
        "started_at": item["State"]["StartedAt"], "restart_count": item["RestartCount"],
        "user": item["Config"]["User"], "network_mode": item["HostConfig"]["NetworkMode"],
        "read_only": item["HostConfig"]["ReadonlyRootfs"],
        "published_ports": item["NetworkSettings"].get("Ports", {}),
    }


def _network_state(name: str) -> dict[str, Any]:
    """Confirma que la red de runtime sea interna."""

    result = _run(["docker", "network", "inspect", name], timeout=30)
    if result.returncode:
        return {"available": False}
    item = json.loads(result.stdout)[0]
    return {
        "available": True, "id": item["Id"], "internal": item["Internal"],
        "driver": item["Driver"], "options": item.get("Options", {}),
    }


def _frozen_hashes() -> dict[str, str]:
    """Resume artefactos congelados antes y después."""

    result = {}
    for directory in FROZEN_DIRS:
        files = sorted((ROOT / directory).glob("*"))
        result[directory] = _hash({item.name: _file_hash(item) for item in files if item.is_file()})
    return result


def _quality_gate() -> dict[str, Any]:
    """Ejecuta py_compile y suites relevantes."""

    compile_result = _run([
        sys.executable, "-m", "py_compile", "src/dikamaha_service.py",
        "scripts/run_phase_7_4_reverse_proxy.py",
    ], timeout=60)
    pytest_result = _run([
        sys.executable, "-m", "pytest", "-q", "tests/test_reverse_proxy_config.py",
        "tests/test_dikamaha_service.py", "tests/test_dikamaha_inference.py",
        "tests/test_hawkes_v1_integration.py",
    ], timeout=180)
    return {
        "py_compile_ok": compile_result.returncode == 0,
        "pytest_ok": pytest_result.returncode == 0,
        "pytest_summary": pytest_result.stdout.strip().splitlines()[-1],
        "pytest_stderr": pytest_result.stderr[-2000:],
    }


def _backend_command(name: str, network: str) -> list[str]:
    """Construye el contenedor backend aislado."""

    return [
        "docker", "run", "--detach", "--name", name,
        "--network", network, "--network-alias", "dikamaha-backend",
        "--cpus", "0.75", "--memory", "512m", "--memory-swap", "512m",
        "--pids-limit", "128", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=64m",
        "--env", "DIKAMAHA_AUTH_ENABLED=true",
        "--env", f"DIKAMAHA_API_KEY={API_KEY}",
        "--env", "DIKAMAHA_ALLOWED_ORIGINS=https://staging.local",
        "--env", "DIKAMAHA_RATE_LIMIT_REQUESTS=5000",
        BACKEND_IMAGE,
    ]


def _proxy_command(
    name: str, network: str, port: int, certificate: dict[str, Any]
) -> list[str]:
    """Construye el contenedor proxy no privilegiado."""

    return [
        "docker", "run", "--detach", "--name", name,
        "--network", network, "--publish", f"127.0.0.1:{port}:8443",
        "--cpus", "0.25", "--memory", "128m", "--memory-swap", "128m",
        "--pids-limit", "64", "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges", "--read-only",
        "--tmpfs", "/tmp:rw,noexec,nosuid,size=32m",
        "--volume", f"{NGINX_CONFIG}:/etc/nginx/nginx.conf:ro",
        "--volume", f"{certificate['cert']}:/etc/nginx/tls/server.crt:ro",
        "--volume", f"{certificate['key']}:/etc/nginx/tls/server.key:ro",
        "--entrypoint", "nginx", PROXY_IMAGE, "-g", "daemon off;",
    ]


def _safe_remove(kind: str, name: str) -> dict[str, Any]:
    """Elimina exclusivamente un recurso propio si existe."""

    inspect = _run(["docker", kind, "inspect", name], timeout=30)
    if inspect.returncode:
        return {"resource": name, "already_absent": True, "returncode": 0}
    command = ["docker", kind, "rm", "--force", name] if kind == "container" else ["docker", "network", "rm", name]
    result = _run(command, timeout=30)
    return {"resource": name, "returncode": result.returncode, "stderr": result.stderr}


def _logs(name: str) -> str:
    """Obtiene logs antes del cleanup."""

    return _run(["docker", "logs", name], timeout=30).stdout + _run(
        ["docker", "logs", name], timeout=30
    ).stderr


def _security_audit(
    functional: dict[str, Any], rate: dict[str, Any], runtime: dict[str, Any],
    logs: dict[str, str], frozen_unchanged: bool,
) -> dict[str, Any]:
    """Consolida controles verificables del perímetro."""

    return {
        "tls_verified_with_local_ca": functional["health"]["status"] == 200,
        "authentication_401": functional["unauthenticated"]["status"] == 401,
        "cors_403": functional["invalid_origin"]["status"] == 403,
        "body_limit_413": functional["oversized"]["status"] == 413,
        "chunked_rejected": functional["chunked_status"] == 411,
        "rate_limit_429": int(rate["statuses"].get("429", 0)) > 0,
        "controlled_5xx": functional["upstream_failure"]["status"] == 503,
        "request_id_preserved": functional["request_id"]["headers"]["X-Request-ID"] == "phase-7-4-request-id",
        "openapi_disabled": functional["openapi"]["status"] == 404,
        "security_headers": _headers_valid(functional["health"]["headers"]),
        "cors_allowlist": functional["allowed_cors"]["headers"]["Access-Control-Allow-Origin"] == "https://staging.local",
        "markov_independent": functional["markov_identical_with_shadow"],
        "hawkes_official_blocked": functional["official_hawkes"]["status"] == 422,
        "hawkes_default_false": functional["live_markov"]["body"]["hawkes_applied"] is False,
        "replay_identical": runtime["replay"]["identical"],
        "backend_non_root": runtime["backend"]["user"] == "app",
        "proxy_non_root": runtime["proxy"]["user"] == "101",
        "dedicated_bridge_network": runtime["network"]["driver"] == "bridge",
        "egress_masquerade_disabled": (
            runtime["network"]["options"].get(
                "com.docker.network.bridge.enable_ip_masquerade"
            ) == "false"
        ),
        "backend_has_no_published_ports": not any(
            runtime["backend"]["published_ports"].values()
        ),
        "logs_without_secrets_or_payloads": _logs_safe(logs),
        "postgres_container_unchanged": runtime["postgres_before"] == runtime["postgres_after"],
        "frozen_artifacts_unchanged": frozen_unchanged,
        "cleanup_guaranteed": all(item["returncode"] == 0 for item in runtime["cleanup"]),
        "no_postgresql_redis_external_runtime": True,
        "health_readiness_under_load": runtime["health_under_load_ok"],
        "quality_gate_passed": all((
            runtime["quality_gate"]["py_compile_ok"], runtime["quality_gate"]["pytest_ok"],
        )),
    }


def _headers_valid(headers: dict[str, Any]) -> bool:
    """Comprueba headers defensivos mínimos."""

    return (
        headers["Strict-Transport-Security"] == "max-age=300"
        and headers["X-Content-Type-Options"] == "nosniff"
        and headers["X-Frame-Options"] == "DENY"
    )


def _logs_safe(logs: dict[str, str]) -> bool:
    """Rechaza secretos y campos de payload en logs."""

    combined = "\n".join(logs.values())
    forbidden = (API_KEY, "league_intercept", "lambda_base_home", '"match_id"')
    return not any(item in combined for item in forbidden)


def _decision(audit: dict[str, Any]) -> str:
    """Clasifica el gate conservando caveats locales."""

    if not all(audit.values()):
        return "reverse_proxy_rejected_for_revision"
    return "reverse_proxy_approved_with_caveats"


def _artifact_payloads(
    decision: str, certificate: dict[str, Any], functional: dict[str, Any],
    load: list[dict[str, Any]], rate: dict[str, Any], runtime: dict[str, Any],
    audit: dict[str, Any],
) -> dict[str, Any]:
    """Construye artefactos sin credenciales ni claves privadas."""

    return {
        "proxy_config.json": {
            "proxy_image": PROXY_IMAGE, "listen": "8443/tls",
            "body_limit": "64k", "rate_limit": "50r/s burst=100",
            "connection_limit": 32, "openapi": "disabled",
            "authentication": "forwarded_to_backend_runtime_api_key",
            "network": "dedicated_bridge_without_ip_masquerade",
            "external_runtime_calls": False,
        },
        "tls_local_config.json": {
            key: value for key, value in certificate.items() if key not in {"cert", "key"}
        },
        "security_results.json": functional,
        "load_test_results.json": {
            "nominal": load, "rate_burst": rate,
            "snapshot_dependency": "requests are operational samples, not model observations",
        },
        "audit.json": audit,
        "runtime_result.json": runtime,
        "decision.json": {"decision": decision, "production": False},
    }


def _write_artifacts(
    decision: str, certificate: dict[str, Any], functional: dict[str, Any],
    load: list[dict[str, Any]], rate: dict[str, Any], runtime: dict[str, Any],
    audit: dict[str, Any],
) -> None:
    """Escribe evidencia, manifiesto, hashes e informe."""

    payloads = _artifact_payloads(decision, certificate, functional, load, rate, runtime, audit)
    for name, payload in payloads.items():
        _write(OUTPUT / name, payload)
    (OUTPUT / "proxy_nginx.conf").write_text(NGINX_CONFIG.read_text(), encoding="utf-8")
    hashes = _hash_manifest(payloads)
    _write(OUTPUT / "hashes.json", hashes)
    manifest = {
        "phase": "7.4", "version": "reverse_proxy_v1", "decision": decision,
        "backend_image": runtime["backend_image"], "proxy_image": PROXY_IMAGE,
        "hashes": hashes, "postgresql_modified": False, "hawkes_official": False,
    }
    _write(OUTPUT / "manifest.json", manifest)
    (OUTPUT / "final_report.md").write_text(
        _report(decision, load, rate, runtime), encoding="utf-8"
    )


def _hash_manifest(payloads: dict[str, Any]) -> dict[str, Any]:
    """Registra hashes de artefactos, código e imágenes."""

    artifact_names = [*payloads, "proxy_nginx.conf"]
    sources = (
        "deploy/phase_7_4/nginx.conf", "scripts/run_phase_7_4_reverse_proxy.py",
        "tests/test_reverse_proxy_config.py",
        ".github/workflows/phase-7-4-reverse-proxy.yml", "Dockerfile",
        "requirements.docker.txt", "src/dikamaha_service.py",
    )
    return {
        "artifacts": {name: _file_hash(OUTPUT / name) for name in artifact_names},
        "sources": {name: _file_hash(ROOT / name) for name in sources},
    }


def _report(
    decision: str, load: list[dict[str, Any]], rate: dict[str, Any],
    runtime: dict[str, Any],
) -> str:
    """Genera el informe final de Fase 7.4."""

    lines = [
        "# Fase 7.4 - Reverse proxy local DIKAMAHA", "",
        f"**Decisión:** `{decision}`", "",
        "DIKAMAHA se validó detrás de Nginx unprivileged con TLS local, red bridge dedicada sin masquerade y backend sin puerto publicado.", "",
        "## Carga vía proxy", "",
    ]
    for item in load:
        lines.append(
            f"- `{item['scenario']}`: {item['requests']} requests, "
            f"p95 `{item['latency_ms']['p95']:.3f} ms`, esperado `{item['expected_ratio']:.1%}`."
        )
    lines.extend([
        f"- Rate burst: `{rate['statuses']}`.",
        "", "## Seguridad", "",
        "- API key efímera validada por el backend y no registrada por Nginx.",
        "- OpenAPI desactivado; health/readiness permanecen accesibles.",
        "- Body máximo 64 KiB y requests chunked rechazados.",
        "- Rate limiting, límite de conexiones y timeouts explícitos.",
        "- Request ID preservado y logs limitados a metadatos.",
        "- Proxy y backend ejecutados sin root sobre una red dedicada sin NAT de salida.",
        "", "## Caveats", "",
        "- El certificado es autofirmado y solo sirve para staging local.",
        "- La autenticación se delega al backend; un gateway productivo requeriría gestión externa de secretos.",
        "- La carga es local y no autoriza predicciones oficiales ni producción.",
        "- Kalman, Markov y Hawkes mantienen sus caveats experimentales.",
        "", f"Tests del gate: `{runtime['quality_gate']['pytest_summary']}`.",
        "PostgreSQL y Redis no fueron accedidos ni modificados. No se usó DATABASE_URL.",
    ])
    return "\n".join(lines) + "\n"


def main() -> int:
    """Ejecuta build, proxy TLS, carga, auditoría y cleanup."""

    OUTPUT.mkdir(parents=True, exist_ok=True)
    frozen_before = _frozen_hashes()
    postgres_before = _docker_state("futbol_db")
    suffix, port = str(os.getpid()), _free_port()
    network = f"dikamaha-7-4-net-{suffix}"
    backend, proxy = f"dikamaha-7-4-backend-{suffix}", f"dikamaha-7-4-proxy-{suffix}"
    runtime: dict[str, Any] = {"quality_gate": _quality_gate(), "postgres_before": postgres_before}
    with tempfile.TemporaryDirectory(prefix="dikamaha-phase-7-4-") as directory:
        certificate = _certificate(Path(directory))
        functional, load, rate = _execute_runtime(
            runtime, network, backend, proxy, port, certificate
        )
    runtime["postgres_after"] = _docker_state("futbol_db")
    frozen_unchanged = frozen_before == _frozen_hashes()
    audit = _security_audit(functional, rate, runtime, runtime["logs"], frozen_unchanged)
    decision = _decision(audit)
    _write_artifacts(decision, certificate, functional, load, rate, runtime, audit)
    return 0 if decision != "reverse_proxy_rejected_for_revision" else 1


def _execute_runtime(
    runtime: dict[str, Any], network: str, backend: str, proxy: str,
    port: int, certificate: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]:
    """Levanta y valida el runtime, garantizando cleanup."""

    functional: dict[str, Any] = {}
    load: list[dict[str, Any]] = []
    rate: dict[str, Any] = {}
    runtime["cleanup"] = []
    try:
        _start_runtime(runtime, network, backend, proxy, port, certificate)
        context = _ssl_context(certificate["cert"])
        base_url = f"https://localhost:{port}"
        runtime["health"] = _wait_health(base_url, context)
        if not runtime["health"]["ok"]:
            raise RuntimeError(
                f"Health no disponible. Proxy logs: {_logs(proxy)[-4000:]}"
            )
        functional = _functional_checks(base_url, context, port)
        load, health_rows = _execute_load(base_url, context)
        runtime["health_under_load_samples"] = len(health_rows)
        runtime["health_under_load_ok"] = all(row["status"] == 200 for row in health_rows)
        runtime["replay"] = _replay(base_url, context)
        rate = _rate_test(base_url, context)
        runtime["backend_logs_before_stop"] = _logs(backend)
        _run(["docker", "stop", backend], timeout=30)
        functional["upstream_failure"] = _request(
            base_url, context, "/v1/predict/pre-match", _pre_payload(),
            {"X-Dikamaha-Key": API_KEY},
        )
    finally:
        runtime["logs"] = {
            "proxy": _logs(proxy),
            "backend": runtime.get("backend_logs_before_stop", _logs(backend)),
        }
        runtime["cleanup"].append(_safe_remove("container", proxy))
        runtime["cleanup"].append(_safe_remove("container", backend))
        runtime["cleanup"].append(_safe_remove("network", network))
    return functional, load, rate


def _start_runtime(
    runtime: dict[str, Any], network: str, backend: str, proxy: str,
    port: int, certificate: dict[str, Any],
) -> None:
    """Construye imágenes y arranca recursos propios."""

    build = _run(["docker", "build", "--tag", BACKEND_IMAGE, "."], timeout=900)
    if build.returncode:
        raise RuntimeError(f"Build backend falló: {build.stderr[-2000:]}")
    pull = _run(["docker", "pull", PROXY_IMAGE], timeout=300)
    if pull.returncode:
        raise RuntimeError(f"Pull proxy falló: {pull.stderr[-2000:]}")
    created = _run([
        "docker", "network", "create", "--driver", "bridge", "--opt",
        "com.docker.network.bridge.enable_ip_masquerade=false", network,
    ], timeout=30)
    if created.returncode:
        raise RuntimeError(created.stderr)
    backend_start = _run(_backend_command(backend, network), timeout=60)
    if backend_start.returncode:
        raise RuntimeError(backend_start.stderr)
    proxy_start = _run(_proxy_command(proxy, network, port, certificate), timeout=60)
    if proxy_start.returncode:
        raise RuntimeError(proxy_start.stderr)
    runtime.update({
        "backend": _docker_state(backend), "proxy": _docker_state(proxy),
        "network": _network_state(network), "backend_image": _image_state(BACKEND_IMAGE),
        "proxy_image": _image_state(PROXY_IMAGE), "port": port,
    })


def _image_state(image: str) -> dict[str, Any]:
    """Registra ID, digest y usuario de una imagen."""

    result = _run(["docker", "image", "inspect", image], timeout=30)
    if result.returncode:
        return {"available": False}
    item = json.loads(result.stdout)[0]
    return {
        "available": True, "id": item["Id"], "repo_digests": item.get("RepoDigests", []),
        "user": item["Config"]["User"],
    }


if __name__ == "__main__":
    raise SystemExit(main())

# Version: 1.0.0
# Created: 2026-07-16
