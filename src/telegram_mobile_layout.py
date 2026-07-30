"""Contrato verificable de legibilidad para mensajes móviles de Telegram.

Version: 1.0.0
Created: 2026-07-29
"""
from __future__ import annotations

import html
import re
import unicodedata

MESSAGE_LIMIT = 3900
PROSE_WIDTH = 72
TABLE_WIDTH = 40
_TAG = re.compile(r"<[^>]+>")


def visible_text(value: str) -> str:
    """Retira etiquetas HTML y decodifica entidades para medir la pantalla."""

    return html.unescape(_TAG.sub("", value))


def display_width(value: str) -> int:
    """Calcula columnas visibles considerando caracteres anchos y emojis."""

    return sum(
        2 if unicodedata.east_asian_width(char) in {"W", "F"} else 1
        for char in value)


def mobile_layout_issues(message: str) -> list[str]:
    """Devuelve incumplimientos del contrato móvil conservador."""

    issues = ["message_too_long"] if len(message) > MESSAGE_LIMIT else []
    inside_pre = False
    for number, raw_line in enumerate(message.splitlines(), 1):
        inside_pre = inside_pre or "<pre>" in raw_line
        width = display_width(visible_text(raw_line))
        limit = TABLE_WIDTH if inside_pre else PROSE_WIDTH
        if width > limit:
            issues.append(f"line_{number}_width_{width}_limit_{limit}")
        inside_pre = inside_pre and "</pre>" not in raw_line
    return issues


def keyboard_layout_issues(keyboard: dict[str, object] | None) -> list[str]:
    """Detecta botones cuya etiqueta puede truncarse en Telegram móvil."""

    if not isinstance(keyboard, dict):
        return []
    issues: list[str] = []
    rows = keyboard.get("inline_keyboard")
    for row_number, row in enumerate(rows if isinstance(rows, list) else []):
        for column, button in enumerate(row if isinstance(row, list) else []):
            text = button.get("text") if isinstance(button, dict) else ""
            if display_width(str(text)) > 32:
                issues.append(f"button_{row_number}_{column}_too_wide")
    return issues


# Version: 1.0.0
# Created: 2026-07-29
