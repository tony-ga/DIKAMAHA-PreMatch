"""¿Es real la asimetría de localía en la reacción in-play, o solo ausente
en el motor? (`DEC-214`, Fase 129).

El diagnóstico de solo lectura contra `LiveProbabilityEngineV1` (Fase 116)
mostró que el motor es perfectamente simétrico -diferencia = 0.0 exacta-
entre "favorito local anota primero" y "favorito visitante anota primero",
con inputs sintéticos. Antes de proponer cualquier cambio al motor, este
script mide si el swing EMPÍRICO real -no sintético- de "quién anota
primero" y "quién va ganando al descanso" difiere según la localía del
favorito, sobre el mismo corpus de 1,000 partidos que produjo los hallazgos
originales (`DEC-211`). No requiere PostgreSQL: reutiliza artefactos locales
ya materializados.

Unidad IID: partido completo. Bootstrap por partido, 5,000 réplicas.

Uso:
    python -m scripts.analyze_favorite_venue_inplay_swing

# Requirements:
#   numpy>=1.24
#   pandas>=2.0

Version: 1.0.0
Created: 2026-08-18
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
PREDICTIONS_PATH = (
    ROOT / "artifacts/phase_105_historical_1000_complete/ranked_1000_predictions.json")
WINDOWS_PATH = ROOT / "artifacts/phase_74_causal_sequence_corpus/micro_windows_15m.jsonl"
OUTPUT_PATH = ROOT / "artifacts/candidate_evaluation/favorite_venue_inplay_swing.json"
RNG_SEED = 20260818
N_BOOT = 5000


def _load_predictions() -> list[dict[str, Any]]:
    return json.loads(PREDICTIONS_PATH.read_text(encoding="utf-8"))


def _load_windows(match_ids: set[int]) -> pd.DataFrame:
    rows = []
    with WINDOWS_PATH.open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)
            if record["match_id"] in match_ids:
                rows.append(record)
    return pd.DataFrame(rows)


def _match_features(windows: pd.DataFrame) -> pd.DataFrame:
    """Primer gol y marcador al descanso por partido, desde ventanas de 15 min."""

    records = []
    for match_id, group in windows.groupby("match_id"):
        home = group[group["is_home"]].sort_values("window_index")
        away = group[~group["is_home"]].sort_values("window_index")
        if len(home) != 6 or len(away) != 6:
            continue

        scoring_windows = []
        for _, row in home.iterrows():
            if row["goals"] > 0:
                scoring_windows.append((row["window_index"], "home"))
        for _, row in away.iterrows():
            if row["goals"] > 0:
                scoring_windows.append((row["window_index"], "away"))
        first_goal_side = None
        if scoring_windows:
            min_index = min(item[0] for item in scoring_windows)
            sides = {side for index, side in scoring_windows if index == min_index}
            first_goal_side = "both" if len(sides) == 2 else sides.pop()

        ht_home = int(home.loc[home["window_index"] == 3, "score_for_start"].iloc[0])
        ht_away = int(away.loc[away["window_index"] == 3, "score_for_start"].iloc[0])
        if ht_home > ht_away:
            ht_leader = "home"
        elif ht_away > ht_home:
            ht_leader = "away"
        else:
            ht_leader = "draw"

        records.append({
            "match_id": match_id,
            "first_goal_side": first_goal_side,
            "ht_leader": ht_leader,
        })
    return pd.DataFrame(records)


def _bootstrap_diff(values_a: np.ndarray, values_b: np.ndarray) -> dict[str, float]:
    rng = np.random.default_rng(RNG_SEED)
    a = np.asarray(values_a, dtype=float)
    b = np.asarray(values_b, dtype=float)
    obs = float(np.mean(a) - np.mean(b))
    diffs = np.empty(N_BOOT)
    for i in range(N_BOOT):
        sample_a = a[rng.integers(0, len(a), len(a))]
        sample_b = b[rng.integers(0, len(b), len(b))]
        diffs[i] = sample_a.mean() - sample_b.mean()
    low, high = float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))
    return {
        "mean_a": float(np.mean(a)), "n_a": int(len(a)),
        "mean_b": float(np.mean(b)), "n_b": int(len(b)),
        "diff": obs, "ci_low": low, "ci_high": high,
        "crosses_zero": bool(low <= 0.0 <= high),
    }


def evaluate() -> dict[str, Any]:
    predictions = _load_predictions()
    match_ids = {row["match_id"] for row in predictions}
    windows = _load_windows(match_ids)
    features = _match_features(windows)

    rows = []
    for row in predictions:
        market = row["markets"].get("1x2")
        if market is None or market["predicted"] not in ("home", "away"):
            continue
        rows.append({
            "match_id": row["match_id"],
            "favorite": market["predicted"],
            "underdog": "away" if market["predicted"] == "home" else "home",
            "actual": market["actual"],
        })
    favorites = pd.DataFrame(rows).merge(features, on="match_id", how="inner")

    favorites["favorite_non_loss"] = (
        (favorites["actual"] == favorites["favorite"])
        | (favorites["actual"] == "draw")
    ).astype(float)

    def _swing_for_venue(venue: str) -> dict[str, Any]:
        subset = favorites[favorites["favorite"] == venue]
        scored_first = subset[subset["first_goal_side"] == subset["favorite"]]
        conceded_first = subset[subset["first_goal_side"] == subset["underdog"]]
        first_goal_swing = _bootstrap_diff(
            scored_first["favorite_non_loss"].values,
            conceded_first["favorite_non_loss"].values,
        )

        led_ht = subset[subset["ht_leader"] == subset["favorite"]]
        trailed_ht = subset[subset["ht_leader"] == subset["underdog"]]
        halftime_swing = _bootstrap_diff(
            led_ht["favorite_non_loss"].values,
            trailed_ht["favorite_non_loss"].values,
        )
        return {
            "n_favorites": int(len(subset)),
            "first_goal_swing": first_goal_swing,
            "halftime_swing": halftime_swing,
        }

    by_venue = {venue: _swing_for_venue(venue) for venue in ("home", "away")}

    def _diff_of_swings(key: str) -> dict[str, float]:
        rng = np.random.default_rng(RNG_SEED)
        home_subset = favorites[favorites["favorite"] == "home"]
        away_subset = favorites[favorites["favorite"] == "away"]

        def _swing_sample(subset: pd.DataFrame) -> float:
            if key == "first_goal_swing":
                a = subset[subset["first_goal_side"] == subset["favorite"]]
                b = subset[subset["first_goal_side"] == subset["underdog"]]
            else:
                a = subset[subset["ht_leader"] == subset["favorite"]]
                b = subset[subset["ht_leader"] == subset["underdog"]]
            return float(
                a["favorite_non_loss"].mean() - b["favorite_non_loss"].mean())

        observed = (
            by_venue["home"][key]["diff"] - by_venue["away"][key]["diff"])
        diffs = np.empty(N_BOOT)
        for i in range(N_BOOT):
            home_sample = home_subset.iloc[
                rng.integers(0, len(home_subset), len(home_subset))]
            away_sample = away_subset.iloc[
                rng.integers(0, len(away_subset), len(away_subset))]
            diffs[i] = _swing_sample(home_sample) - _swing_sample(away_sample)
        low, high = float(np.percentile(diffs, 2.5)), float(np.percentile(diffs, 97.5))
        return {
            "observed_diff_of_swings": observed,
            "ci_low": low, "ci_high": high,
            "crosses_zero": bool(low <= 0.0 <= high),
        }

    return {
        "unit": "complete_match",
        "bootstrap_replicates": N_BOOT,
        "by_favorite_venue": by_venue,
        "asymmetry_first_goal_swing": _diff_of_swings("first_goal_swing"),
        "asymmetry_halftime_swing": _diff_of_swings("halftime_swing"),
    }


def main() -> None:
    report = evaluate()
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT_PATH.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    for venue in ("home", "away"):
        block = report["by_favorite_venue"][venue]
        print(f"--- favorito {venue} (n={block['n_favorites']}) ---", flush=True)
        fg = block["first_goal_swing"]
        print(f"  swing quien anota primero: {fg['diff']:+.4f} "
              f"IC95% [{fg['ci_low']:+.4f}, {fg['ci_high']:+.4f}]", flush=True)
        ht = block["halftime_swing"]
        print(f"  swing estado al descanso: {ht['diff']:+.4f} "
              f"IC95% [{ht['ci_low']:+.4f}, {ht['ci_high']:+.4f}]", flush=True)
        print(flush=True)

    fg_asym = report["asymmetry_first_goal_swing"]
    print(f"asimetria (local - visitante) swing primer gol: "
          f"{fg_asym['observed_diff_of_swings']:+.4f} IC95% "
          f"[{fg_asym['ci_low']:+.4f}, {fg_asym['ci_high']:+.4f}] "
          f"{'cruza cero' if fg_asym['crosses_zero'] else 'NO cruza cero'}",
          flush=True)
    ht_asym = report["asymmetry_halftime_swing"]
    print(f"asimetria (local - visitante) swing descanso: "
          f"{ht_asym['observed_diff_of_swings']:+.4f} IC95% "
          f"[{ht_asym['ci_low']:+.4f}, {ht_asym['ci_high']:+.4f}] "
          f"{'cruza cero' if ht_asym['crosses_zero'] else 'NO cruza cero'}",
          flush=True)
    print(f"\nartefacto: {OUTPUT_PATH}", flush=True)


if __name__ == "__main__":
    main()
