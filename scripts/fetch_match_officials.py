"""Ingesta de árbitros desde ESPN para el corpus causal.

Los candidatos de contracción jerárquica por árbitro (`arquitectura_matematica_v1`,
D.1) no se podían medir porque el corpus de Fase 74 no trae ese campo. ESPN sí lo
expone en `summary.gameInfo.officials`, verificado con una llamada real.

Tres decisiones de diseño que importan aquí:

- **Resumible.** Un barrido de miles de partidos se interrumpe; el script salta
  lo ya descargado en vez de repetirlo, de modo que reanudar es barato y no
  duplica carga sobre el proveedor.
- **Concurrencia moderada.** El servicio en producción del proyecto consume esta
  misma API pública sin clave. Un barrido agresivo puede provocar throttling por
  IP y el primero en notarlo sería un usuario real, no este proceso.
- **Sólo se guarda lo que se va a usar.** El `summary` completo pesa cientos de
  KB por partido; aquí se extraen los árbitros y se descarta el resto.

Uso:
    python -m scripts.fetch_match_officials --limit 200      # prueba acotada
    python -m scripts.fetch_match_officials                  # barrido completo

# Requirements:
#   pandas>=2.0

Version: 1.0.0
Created: 2026-08-16
"""

from __future__ import annotations

import argparse
import json
import random
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORPUS = ROOT / "artifacts/match_level_corpus/matches.csv"
DEFAULT_OUTPUT = ROOT / "artifacts/match_officials/officials.jsonl"
SUMMARY = ("https://site.api.espn.com/apis/site/v2/sports/soccer/"
           "{league}/summary?event={event}")
# ESPN rechaza con 403 los User-Agent que se identifican como herramienta; el
# mismo endpoint responde con normalidad ante una cadena de navegador. Es el
# mismo encabezado que ya usa el resto del proyecto contra esta API.
USER_AGENT = "Mozilla/5.0"

_lock = threading.Lock()


def _officials(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Extrae los árbitros del `summary`, tolerando ausencia."""

    info = payload.get("gameInfo") or {}
    officials = info.get("officials") or payload.get("officials") or []
    return [
        {
            "name": str(item.get("displayName") or item.get("fullName") or ""),
            "order": item.get("order"),
            "position": (item.get("position") or {}).get("displayName"),
        }
        for item in officials
        if isinstance(item, dict)
    ]


def _fetch(league: str, event: int, timeout: float) -> dict[str, Any]:
    """Descarga un `summary` y devuelve sólo sus árbitros."""

    url = SUMMARY.format(league=league, event=event)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    return {
        "match_id": event,
        "league_slug": league,
        "officials": _officials(payload),
    }


def main() -> None:
    """Recorre el corpus descargando árbitros, saltando lo ya guardado."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--delay", type=float, default=0.12)
    parser.add_argument("--timeout", type=float, default=25.0)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()

    frame = pd.read_csv(args.corpus).sort_values(["match_date", "match_id"])
    targets = [
        (str(row["league_slug"]), int(row["match_id"]))
        for row in frame.to_dict("records")
    ]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    done: set[int] = set()
    if args.output.exists():
        with args.output.open("r", encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    try:
                        done.add(int(json.loads(line)["match_id"]))
                    except Exception:  # noqa: BLE001 - línea parcial al reanudar
                        continue

    pending = [item for item in targets if item[1] not in done]
    if args.limit:
        pending = pending[:args.limit]

    print(f"corpus={len(targets)} ya descargados={len(done)} "
          f"pendientes={len(pending)}", flush=True)

    counters = {"ok": 0, "with_officials": 0, "failed": 0}
    started = time.time()
    handle = args.output.open("a", encoding="utf-8")

    def _work(item: tuple[str, int]) -> None:
        """Descarga un partido y anota el resultado."""

        league, event = item
        # Jitter: dispersa las peticiones para no llegar en ráfagas sincronizadas.
        time.sleep(args.delay * (0.5 + random.random()))
        try:
            record = _fetch(league, event, args.timeout)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, json.JSONDecodeError, OSError) as error:
            with _lock:
                counters["failed"] += 1
                if counters["failed"] % 50 == 0:
                    print(f"  fallos acumulados: {counters['failed']} "
                          f"(último: {type(error).__name__})", flush=True)
            return
        with _lock:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            counters["ok"] += 1
            if record["officials"]:
                counters["with_officials"] += 1
            if counters["ok"] % 250 == 0:
                handle.flush()
                elapsed = time.time() - started
                rate = counters["ok"] / max(elapsed, 1e-9)
                print(f"  {counters['ok']} descargados "
                      f"({counters['with_officials']} con árbitro, "
                      f"{elapsed:.0f}s, {rate:.1f}/s)", flush=True)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(_work, pending))
    finally:
        handle.flush()
        handle.close()

    summary = {
        **counters,
        "pending_at_start": len(pending),
        "already_present": len(done),
        "elapsed_seconds": round(time.time() - started, 1),
        "output": str(args.output),
    }
    (args.output.parent / "fetch_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
