"""Smoke test local para validar imports y conexión a la base de datos."""

from __future__ import annotations

import sys

from src.database.manager import DatabaseConnectionError, DatabaseManager


def main() -> int:
    """Intenta importar el manager y conectar a la base de datos."""

    try:
        manager = DatabaseManager()
        manager.create_tables()
        print("Conexión OK. DatabaseManager importado correctamente.")
        return 0
    except DatabaseConnectionError as exc:
        print(f"Fallo de conexión: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
