"""Pruebas estáticas del perímetro Nginx de Fase 7.4."""

from __future__ import annotations

from pathlib import Path

CONFIG = Path("deploy/phase_7_4/nginx.conf")


def _config() -> str:
    """Devuelve la configuración versionada."""

    return CONFIG.read_text(encoding="utf-8")


def test_proxy_has_tls_limits_and_timeouts() -> None:
    """Exige TLS, límites de body/conexión y timeouts explícitos."""

    text = _config()
    for directive in (
        "listen 8443 ssl",
        "ssl_protocols TLSv1.2 TLSv1.3",
        "client_max_body_size 64k",
        "rate=50r/s",
        "limit_conn dikamaha_connections 32",
        "proxy_connect_timeout 2s",
        "proxy_read_timeout 12s",
        "proxy_send_timeout 12s",
    ):
        assert directive in text


def test_proxy_rejects_chunked_and_protects_openapi() -> None:
    """Rechaza chunked y no expone documentación interactiva."""

    text = _config()
    assert "$chunked_request = 1" in text
    assert "return 411" in text
    assert "location = /openapi.json" in text
    assert text.count("return 404") >= 3


def test_proxy_preserves_request_id_and_omits_sensitive_logs() -> None:
    """Propaga request id sin registrar auth ni cuerpos."""

    text = _config()
    assert "proxy_set_header X-Request-ID $request_id_header" in text
    assert "add_header X-Request-ID $request_id_header always" in text
    assert "$http_x_dikamaha_key" not in text
    assert "$request_body" not in text


def test_proxy_has_restrictive_cors_and_controlled_errors() -> None:
    """Mantiene CORS allowlist y errores explícitos."""

    text = _config()
    assert '"https://staging.local" 1' in text
    for status in ("403", "413", "429", "503"):
        assert f"return {status}" in text


# Version: 1.0.0
# Created: 2026-07-16
