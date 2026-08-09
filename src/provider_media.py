"""Proxy binario seguro para medios visuales publicados por ESPN."""
from __future__ import annotations

from urllib.parse import urlparse

import requests

MEDIA_HOSTS = frozenset({"a.espncdn.com", "cdn.espn.com", "secure.espncdn.com"})
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
MAX_MEDIA_BYTES = 2_000_000


class ProviderMediaError(ValueError):
    """Rechazo controlado de URL, formato o tamaño de imagen."""


def fetch_transparent_png(url: str) -> bytes:
    """Descarga un PNG con transparencia desde hosts visuales permitidos."""

    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in MEDIA_HOSTS:
        raise ProviderMediaError("provider_media_url_not_allowed")
    response = requests.get(url, timeout=(3.0, 8.0), stream=True)
    response.raise_for_status()
    content = bytearray()
    for chunk in response.iter_content(64 * 1024):
        content.extend(chunk)
        if len(content) > MAX_MEDIA_BYTES:
            raise ProviderMediaError("provider_media_too_large")
    payload = bytes(content)
    if not _transparent_png(payload):
        raise ProviderMediaError("provider_media_must_be_transparent_png")
    return payload


def _transparent_png(payload: bytes) -> bool:
    """Valida firma PNG y presencia de canal alfa o chunk tRNS."""

    if len(payload) < 26 or not payload.startswith(PNG_SIGNATURE):
        return False
    color_type = payload[25]
    return color_type in {4, 6} or b"tRNS" in payload

