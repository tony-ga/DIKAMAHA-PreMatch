"""Validación de extremo a extremo de la proyección de `save` (`DEC-217`).

Fase 116B auditó el feed crudo de forma estática y las pruebas unitarias usan
eventos construidos a mano. Ninguna de las dos cosas demuestra que la cadena
completa funcione: play crudo de ESPN → `classify_play` → payload del
follower → `MarkovLiveInput` → `MarkovLiveV1` con la proyección activa.

Este script recorre esa cadena real sobre el cache de Fase 59 y comprueba
cuatro cosas que sólo se ven de extremo a extremo:

1. que los `save` sobrevivan a `classify_play` con `event_type_raw` intacto
   -si la taxonomía los colapsara sin conservar el crudo, la proyección sería
   inalcanzable-;
2. que la inversión de equipo asigne el tiro al rival del portero;
3. que la deduplicación se dispare con la ventana configurada;
4. que el conteo de tiros a puerta resultante aterrice en el rango realista.

No promueve nada: la activación sigue bloqueada porque la base histórica no
contiene `save` y los pesos del motor se calibraron sin ellos. Lo que esto
establece es que, el día que ese bloqueo se levante, el mecanismo ya está
verificado contra datos reales y no sólo contra pruebas sintéticas.

Uso:
    python -m scripts.run_phase_116e_save_projection_e2e

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.espn_event_taxonomy import classify_play  # noqa: E402
from src.markov_live_v1 import (  # noqa: E402
    MarkovLiveConfig,
    MarkovLiveInput,
    MarkovLiveV1,
)

CACHE = ROOT / "artifacts/phase_59_raw_timeline_audit_v1/cache"
OUTPUT = ROOT / "artifacts/phase_116e_save_projection_e2e"
PLAUSIBLE_MIN, PLAUSIBLE_MAX = 7.0, 11.0


def _team_id(play: dict[str, Any]) -> int | None:
    team = play.get("team") or {}
    ref = team.get("$ref")
    if not ref:
        participants = play.get("participants") or []
        if participants:
            ref = (participants[0].get("team") or {}).get("$ref")
    if not ref:
        return None
    found = re.search(r"/teams/(\d+)", str(ref))
    return int(found.group(1)) if found else None


def _match_id(play: dict[str, Any], fallback: str) -> str:
    ref = str(play.get("$ref") or "")
    if "/events/" in ref:
        return ref.split("/events/")[1].split("/")[0]
    return fallback


def _load(cache: Path) -> dict[str, list[dict[str, Any]]]:
    by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(cache.glob("*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for play in payload.get("items") or []:
            by_match[_match_id(play, path.stem)].append(play)
    return by_match


def _follower_events(plays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reproduce la forma que emite `src/espn_live_follower.py`."""

    events = []
    for play in plays:
        clock = (play.get("clock") or {}).get("value")
        if clock is None:
            continue
        team_id = _team_id(play)
        if team_id is None:
            continue
        canonical, raw_type = classify_play(play)
        events.append({
            "event_id": str(play.get("id") or ""),
            "event_type": canonical,
            "event_type_raw": raw_type,
            "team_id": team_id,
            "period": int((play.get("period") or {}).get("number") or 1),
            "match_clock_seconds": float(clock),
        })
    return sorted(events, key=lambda row: row["match_clock_seconds"])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    by_match = _load(args.cache)
    print(f"partidos en cache: {len(by_match)}", flush=True)

    # --- 1. ¿sobreviven los `save` a la clasificación del follower? ---------
    canonical_of_save: Counter[str] = Counter()
    raw_of_save: Counter[str] = Counter()
    for plays in by_match.values():
        for event in _follower_events(plays):
            if "save" in str(event["event_type_raw"] or "").lower():
                canonical_of_save[event["event_type"]] += 1
                raw_of_save[event["event_type_raw"]] += 1
    survives = bool(raw_of_save)
    print(f"\nsaves tras classify_play: {sum(raw_of_save.values())}", flush=True)
    print(f"  event_type canónico: {dict(canonical_of_save)}", flush=True)
    print(f"  event_type_raw preservado: {dict(raw_of_save)}", flush=True)

    # --- 2..4. cadena completa contra MarkovLiveV1 --------------------------
    off = MarkovLiveV1()
    on = MarkovLiveV1(MarkovLiveConfig(enable_derived_save_projection=True))

    totals = Counter()
    per_match_shots_off, per_match_shots_on = [], []
    identical_when_off = 0
    evaluated = 0
    attribution_errors = 0

    for match_id, plays in by_match.items():
        events = _follower_events(plays)
        if not events:
            continue
        teams = [t for t, _ in Counter(e["team_id"] for e in events).most_common(2)]
        if len(teams) < 2:
            continue
        home_id, away_id = teams[0], teams[1]
        clock = max(e["match_clock_seconds"] for e in events)

        goals = [
            e for e in events
            if e["event_type"] in {"goal", "penalty_scored"}
        ]
        request_kwargs = dict(
            match_id=int(match_id) if match_id.isdigit() else 1,
            home_team_id=home_id, away_team_id=away_id,
            kickoff_ts="2026-08-09T20:00:00+00:00",
            snapshot_ts="2026-08-09T22:00:00+00:00",
            match_clock_seconds=clock,
            period=2,
            score_home=sum(g["team_id"] == home_id for g in goals),
            score_away=sum(g["team_id"] == away_id for g in goals),
            lambda_base_home=1.4, lambda_base_away=1.2,
            events=tuple(events), league_slug="e2e", source_hash=match_id,
        )
        try:
            baseline = off.predict(MarkovLiveInput(**request_kwargs))
            projected = on.predict(MarkovLiveInput(**request_kwargs))
        except ValueError:
            # marcador que no reconcilia con el timeline: el guard fail-closed
            # del propio modelo, no un fallo de la proyección
            continue

        evaluated += 1
        audit = projected["events_audit"]
        totals["derived_projected"] += audit.get("derived_projected", 0)
        totals["derived_duplicates"] += audit.get("derived_duplicates", 0)

        base_shots = sum(
            1 for e in baseline["events_used"] if e["event_type"] == "shot_on_target")
        proj_shots = sum(
            1 for e in projected["events_used"] if e["event_type"] == "shot_on_target")
        per_match_shots_off.append(base_shots)
        per_match_shots_on.append(proj_shots)

        # la proyección debe atribuir el tiro al rival del portero
        for event in projected["events_used"]:
            if event.get("source_event_type") != "save":
                continue
            original = next(
                (e for e in events if e["event_id"] == event["event_id"]), None)
            if original is not None and original["team_id"] == event["team_id"]:
                attribution_errors += 1

    off_mean = sum(per_match_shots_off) / max(len(per_match_shots_off), 1)
    on_mean = sum(per_match_shots_on) / max(len(per_match_shots_on), 1)

    print(f"\npartidos evaluados de extremo a extremo: {evaluated}", flush=True)
    print(f"  saves proyectados: {totals['derived_projected']}", flush=True)
    print(f"  saves deduplicados: {totals['derived_duplicates']}", flush=True)
    print(f"  errores de atribución: {attribution_errors}", flush=True)
    print(f"\n  tiros a puerta/partido SIN proyección: {off_mean:.2f}", flush=True)
    print(f"  tiros a puerta/partido CON proyección: {on_mean:.2f} "
          f"(rango realista {PLAUSIBLE_MIN}-{PLAUSIBLE_MAX})", flush=True)

    checks = {
        "save_survives_follower_classification": survives,
        "raw_type_preserved": bool(raw_of_save),
        "attribution_inverts_to_the_shooter": attribution_errors == 0,
        "deduplication_fires": totals["derived_duplicates"] > 0,
        "projected_shots_land_in_plausible_range": bool(
            PLAUSIBLE_MIN <= on_mean <= PLAUSIBLE_MAX),
        "baseline_undercounts": bool(off_mean < PLAUSIBLE_MIN),
    }
    mechanism_ok = all(checks.values())

    payload = {
        "classification": (
            "mechanism_verified_activation_still_blocked" if mechanism_ok
            else "mechanism_defective"),
        "matches_in_cache": len(by_match),
        "matches_evaluated": evaluated,
        "saves_after_classification": sum(raw_of_save.values()),
        "canonical_type_of_save": dict(canonical_of_save),
        "derived_projected": totals["derived_projected"],
        "derived_duplicates": totals["derived_duplicates"],
        "attribution_errors": attribution_errors,
        "shots_on_target_per_match": {
            "without_projection": off_mean, "with_projection": on_mean,
            "plausible_range": [PLAUSIBLE_MIN, PLAUSIBLE_MAX],
        },
        "checks": checks,
        "activation_blocked_because": (
            "la base histórica no contiene eventos `save`, así que el "
            "candidato no puede medirse contra el replay; y los pesos del "
            "motor se calibraron sobre ese corpus sin saves"
        ),
    }
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "e2e.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8")

    print("\ncomprobaciones:", flush=True)
    for name, ok in checks.items():
        print(f"  {'OK ' if ok else 'FALLA'} {name}", flush=True)
    print(f"\nclasificación: {payload['classification']}", flush=True)
    print(f"artefacto: {args.output / 'e2e.json'}", flush=True)
    return 0 if mechanism_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
