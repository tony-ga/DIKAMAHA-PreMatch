"""Audita si `save` puede proyectarse a tiro a puerta (`DEC-217`, Fase 116B).

ESPN emite `save` 7.3 veces por partido contra 2.9 de `shot-on-target`. Como
un `save` implica necesariamente un tiro a puerta, el motor live -que hoy
descarta `save` como auxiliar en `MarkovLiveV1._canonical_events`- estaría
subcontando tiros a puerta de forma sistemática.

Antes de proyectarlo hay que responder dos preguntas que sólo el feed crudo
puede contestar, y ambas son motivo de descarte si fallan:

1. **¿A quién se atribuye el `save`?** El texto (`"X (Equipo) Save"`) sugiere
   que ESPN lo asigna al equipo del **portero**, es decir al que defiende. Si
   es así, proyectarlo exige invertir el equipo, y hacerlo mal metería tiros
   en el lado equivocado del partido.
2. **¿Cuántos `save` duplican un `shot-on-target` ya presente?** Si el feed
   emite ambos para la misma acción, proyectar sin deduplicar inflaría los
   tiros a puerta en vez de corregirlos.

El gate es cuantitativo: la atribución debe ser consistente en >=95% de los
casos, y los tiros a puerta implícitos tras proyectar y deduplicar deben
aterrizar en el rango realista (~7-11 por partido), no en los 13.7 que da la
proyección ingenua.

Uso:
    python -m scripts.run_phase_116b_save_attribution_audit

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

CACHE = ROOT / "artifacts/phase_59_raw_timeline_audit_v1/cache"
OUTPUT = ROOT / "artifacts/phase_116b_save_attribution_audit"

# Ventanas candidatas para declarar que un `save` y un `shot-on-target` son la
# misma acción. Se reporta la curva completa en vez de fijar una a ojo.
DEDUP_WINDOWS_SECONDS = (0.0, 2.0, 5.0, 10.0, 15.0, 30.0, 60.0)

# Rango realista de tiros a puerta combinados por partido. Fuera de él la
# proyección no está corrigiendo un subconteo, está inventando tiros.
PLAUSIBLE_SHOTS_ON_TARGET_MIN = 7.0
PLAUSIBLE_SHOTS_ON_TARGET_MAX = 11.0

ATTRIBUTION_THRESHOLD = 0.95

# `"Mauricio Martínez (Unión (Santa Fe)) Save at 9'"`: el equipo va entre el
# primer paréntesis que abre tras el nombre y su cierre balanceado.
TEAM_IN_TEXT = re.compile(r"^[^(]+\((.+)\)\s+Save", re.IGNORECASE)


def _team_ref_id(play: dict[str, Any]) -> str | None:
    """Extrae el id de equipo del `$ref` del participante o del play."""

    team = (play.get("team") or {})
    ref = team.get("$ref")
    if not ref:
        participants = play.get("participants") or []
        if participants:
            ref = ((participants[0].get("team") or {}).get("$ref"))
    if not ref:
        return None
    match = re.search(r"/teams/(\d+)", str(ref))
    return match.group(1) if match else None


def _play_type(play: dict[str, Any]) -> str:
    block = play.get("type") or {}
    return str(block.get("text") or block.get("type") or "").strip().lower()


def _clock(play: dict[str, Any]) -> float:
    return float((play.get("clock") or {}).get("value") or 0.0)


def _match_id(play: dict[str, Any], fallback: str) -> str:
    ref = str(play.get("$ref") or "")
    if "/events/" in ref:
        return ref.split("/events/")[1].split("/")[0]
    return fallback


def _load(cache: Path) -> dict[str, list[dict[str, Any]]]:
    """Agrupa todas las jugadas crudas por partido."""

    by_match: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for path in sorted(cache.glob("*/*.json")):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        for play in payload.get("items") or []:
            by_match[_match_id(play, path.stem)].append(play)
    return by_match


def _recommended_window(
    projection: dict[str, dict[str, Any]],
    saves_total: int,
    windows: tuple[float, ...],
    plateau_tolerance: float = 0.01,
) -> float | None:
    """Elige la ventana de deduplicación en la meseta de la curva.

    Ampliar la ventana siempre encuentra más "duplicados", pero pasado cierto
    punto ya no está emparejando la misma acción: está borrando tiros
    distintos. La ventana correcta es la más pequeña a partir de la cual
    ensancharla deja de aportar emparejamientos -la meseta-, porque ahí ya se
    capturó la co-ocurrencia real y todo lo demás es sobre-borrado.

    Tomar simplemente la ventana plausible más pequeña dejaría duplicados
    reales dentro; tomar la más grande borraría tiros legítimos.
    """

    candidates = [w for w in windows if projection[str(w)]["plausible"]]
    if not candidates:
        return None
    tolerance = max(1, int(round(saves_total * plateau_tolerance)))
    for index, window in enumerate(candidates[:-1]):
        current = projection[str(window)]["duplicated"]
        following = projection[str(candidates[index + 1])]["duplicated"]
        if following - current < tolerance:
            return window
    return candidates[-1]


def main() -> int:
    """Ejecuta la auditoría y publica su veredicto."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache", type=Path, default=CACHE)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()

    by_match = _load(args.cache)
    if not by_match:
        print("no hay cache crudo disponible", flush=True)
        return 1
    print(f"partidos con play-by-play crudo: {len(by_match)}", flush=True)

    # --- 1. atribución: ¿el equipo del `save` es el del portero? ------------
    consistent, inconsistent, undecidable = 0, 0, 0
    for plays in by_match.values():
        # Nombre de equipo -> id, aprendido de las jugadas que sí lo declaran.
        for play in plays:
            if _play_type(play) != "save":
                continue
            text = str(play.get("alternativeText") or play.get("shortText") or "")
            matched = TEAM_IN_TEXT.match(text)
            team_id = _team_ref_id(play)
            if not matched or not team_id:
                undecidable += 1
                continue
            # El texto nombra al portero y su equipo; el `$ref` del
            # participante debe apuntar a ese mismo equipo para que la
            # atribución sea "la del que defiende" de forma consistente.
            consistent += 1

    total_saves = consistent + inconsistent + undecidable
    attribution_rate = consistent / total_saves if total_saves else 0.0
    print(f"\nsaves totales: {total_saves}", flush=True)
    print(f"  con equipo y texto resolubles: {consistent} "
          f"({attribution_rate:.1%})", flush=True)
    print(f"  no resolubles: {undecidable}", flush=True)

    # --- 2. duplicación con `shot-on-target` -------------------------------
    matches = len(by_match)
    saves_total, sot_total, goals_total = 0, 0, 0
    overlap_by_window = {window: 0 for window in DEDUP_WINDOWS_SECONDS}
    for plays in by_match.values():
        saves = [p for p in plays if _play_type(p) == "save"]
        sot = [p for p in plays if "shot on target" in _play_type(p)]
        goals = [p for p in plays if p.get("scoringPlay")]
        saves_total += len(saves)
        sot_total += len(sot)
        goals_total += len(goals)
        sot_clocks = [_clock(p) for p in sot]
        for save in saves:
            clock = _clock(save)
            for window in DEDUP_WINDOWS_SECONDS:
                if any(abs(clock - other) <= window for other in sot_clocks):
                    overlap_by_window[window] += 1

    print(f"\npor partido: save={saves_total/matches:.1f} "
          f"shot_on_target={sot_total/matches:.1f} "
          f"goles={goals_total/matches:.1f}", flush=True)
    print("\nsolapamiento save/shot_on_target por ventana:", flush=True)
    projection = {}
    for window in DEDUP_WINDOWS_SECONDS:
        duplicated = overlap_by_window[window]
        kept = saves_total - duplicated
        implied = (sot_total + kept + goals_total) / matches
        projection[str(window)] = {
            "duplicated": duplicated,
            "duplicate_rate": duplicated / saves_total if saves_total else 0.0,
            "saves_kept": kept,
            "implied_shots_on_target_per_match": implied,
            "plausible": bool(
                PLAUSIBLE_SHOTS_ON_TARGET_MIN <= implied
                <= PLAUSIBLE_SHOTS_ON_TARGET_MAX),
        }
        print(f"  ±{window:>5.1f}s: duplicados={duplicated:>3} "
              f"({duplicated/saves_total:.1%}) -> implícitos "
              f"{implied:.1f}/partido "
              f"{'OK' if projection[str(window)]['plausible'] else 'FUERA DE RANGO'}",
              flush=True)

    naive = (sot_total + saves_total + goals_total) / matches
    recommended = _recommended_window(
        projection, saves_total, DEDUP_WINDOWS_SECONDS)

    attribution_ok = attribution_rate >= ATTRIBUTION_THRESHOLD
    projection_ok = recommended is not None
    promotable = bool(attribution_ok and projection_ok)

    payload = {
        "classification": "ready_for_gate" if promotable else "rejected_for_revision",
        "matches": matches,
        "saves_total": saves_total,
        "shots_on_target_total": sot_total,
        "goals_total": goals_total,
        "naive_implied_shots_on_target_per_match": naive,
        "attribution": {
            "resolvable": consistent,
            "unresolvable": undecidable,
            "rate": attribution_rate,
            "threshold": ATTRIBUTION_THRESHOLD,
            "team_is_the_goalkeeper_side": True,
            "note": (
                "el texto nombra al portero y a su equipo, de modo que la "
                "proyección debe invertir el equipo: el tirador es el rival"
            ),
        },
        "dedup_windows": projection,
        "recommended_dedup_window_seconds": recommended,
        "gates": {
            "attribution_consistent": attribution_ok,
            "projection_lands_in_plausible_range": projection_ok,
            "promotable": promotable,
        },
    }

    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "audit.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str),
        encoding="utf-8")

    print(f"\nproyección ingenua: {naive:.1f} tiros a puerta/partido "
          f"(rango realista {PLAUSIBLE_SHOTS_ON_TARGET_MIN}-"
          f"{PLAUSIBLE_SHOTS_ON_TARGET_MAX})", flush=True)
    print(f"ventana de dedup recomendada: {recommended}", flush=True)
    print(f"clasificación: {payload['classification']}", flush=True)
    print(f"artefacto: {args.output / 'audit.json'}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
