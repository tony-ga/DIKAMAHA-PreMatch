"""Ingesta de formaciones tácticas desde ESPN para el corpus causal.

Las alineaciones se publican alrededor de una hora antes del kickoff, así que la
formación es un dato **pre-match** legítimo bajo `DEC-001`: no describe lo que
ocurrió, sino la intención declarada antes de empezar.

Comparte diseño con `fetch_match_officials.py` -resumible, concurrencia moderada
porque el servicio en producción consume la misma API pública sin clave, y sólo
se guarda lo que se va a usar-.

Uso:
    python -m scripts.fetch_match_formations --limit 100
    python -m scripts.fetch_match_formations

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
DEFAULT_OUTPUT = ROOT / "artifacts/match_formations/formations.jsonl"
SUMMARY = ("https://site.api.espn.com/apis/site/v2/sports/soccer/"
           "{league}/summary?event={event}")
USER_AGENT = "Mozilla/5.0"

_lock = threading.Lock()


def _formations(payload: dict[str, Any]) -> dict[str, Any]:
    """Extrae la formación declarada de cada lado."""

    result: dict[str, Any] = {"home": None, "away": None}
    for entry in payload.get("rosters") or []:
        side = entry.get("homeAway")
        if side in result:
            formation = entry.get("formation")
            result[side] = str(formation) if formation else None
    return result


def _fetch(league: str, event: int, timeout: float) -> dict[str, Any]:
    """Descarga un `summary` y devuelve sólo sus formaciones."""

    url = SUMMARY.format(league=league, event=event)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = json.load(response)
    return {"match_id": event, "league_slug": league, **_formations(payload)}


def main() -> None:
    """Recorre el corpus descargando formaciones, saltando lo ya guardado."""

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
                    except Exception:  # noqa: BLE001
                        continue

    pending = [item for item in targets if item[1] not in done]
    if args.limit:
        pending = pending[:args.limit]

    print(f"corpus={len(targets)} ya descargados={len(done)} "
          f"pendientes={len(pending)}", flush=True)

    counters = {"ok": 0, "with_both": 0, "failed": 0}
    started = time.time()
    handle = args.output.open("a", encoding="utf-8")

    def _work(item: tuple[str, int]) -> None:
        """Descarga un partido y anota el resultado."""

        league, event = item
        time.sleep(args.delay * (0.5 + random.random()))
        try:
            record = _fetch(league, event, args.timeout)
        except (urllib.error.URLError, urllib.error.HTTPError,
                TimeoutError, json.JSONDecodeError, OSError):
            with _lock:
                counters["failed"] += 1
            return
        with _lock:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            counters["ok"] += 1
            if record["home"] and record["away"]:
                counters["with_both"] += 1
            if counters["ok"] % 500 == 0:
                handle.flush()
                elapsed = time.time() - started
                print(f"  {counters['ok']} descargados "
                      f"({counters['with_both']} con ambas formaciones, "
                      f"{elapsed:.0f}s)", flush=True)

    try:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            list(pool.map(_work, pending))
    finally:
        handle.flush()
        handle.close()

    summary = {
        **counters,
        "pending_at_start": len(pending),
        "elapsed_seconds": round(time.time() - started, 1),
        "output": str(args.output),
    }
    (args.output.parent / "fetch_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True), flush=True)


if __name__ == "__main__":
    main()
