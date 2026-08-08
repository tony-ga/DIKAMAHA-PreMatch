"""Verificación portable de artefactos versionados del runtime.

Los manifiestos se generan también en estaciones Windows. Git puede
materializar los JSON con LF dentro de la imagen Linux aunque el hash se haya
sellado con CRLF. La verificación conserva el contenido exacto y sólo permite
esa diferencia de representación para archivos de texto conocidos.
"""
from __future__ import annotations

import hashlib
import hmac
import string
from pathlib import Path

_TEXT_ARTIFACT_SUFFIXES = frozenset({".csv", ".json", ".md", ".txt"})
_HEX_DIGITS = frozenset(string.hexdigits)


def artifact_hash_matches(path: Path, expected: object) -> bool:
    """Compara SHA-256 y acepta únicamente normalización LF/CRLF en texto."""

    if (
        not isinstance(expected, str)
        or len(expected) != 64
        or any(character not in _HEX_DIGITS for character in expected)
    ):
        return False
    expected_lower = expected.lower()
    payload = path.read_bytes()
    if _matches(payload, expected_lower):
        return True
    if path.suffix.casefold() not in _TEXT_ARTIFACT_SUFFIXES:
        return False
    normalized_lf = payload.replace(b"\r\n", b"\n")
    if _matches(normalized_lf, expected_lower):
        return True
    normalized_crlf = normalized_lf.replace(b"\n", b"\r\n")
    return _matches(normalized_crlf, expected_lower)


def _matches(payload: bytes, expected: str) -> bool:
    """Realiza una comparación constante entre digests hexadecimales."""

    actual = hashlib.sha256(payload).hexdigest()
    return hmac.compare_digest(actual, expected)


# Version: 1.0.0
# Created: 2026-08-08
