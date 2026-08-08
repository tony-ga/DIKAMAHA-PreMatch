"""Utilidades para conservar causalidad en kickoffs simultáneos.

Version: 1.0.0
Created: 2026-08-07
"""
from __future__ import annotations

from itertools import groupby
from typing import Any, Iterable, Iterator

import pandas as pd


def normalized_kickoff(value: Any) -> pd.Timestamp:
    """Normaliza un kickoff a UTC y rechaza timestamps inválidos."""

    timestamp = pd.to_datetime(value, utc=True, errors="raise")
    if pd.isna(timestamp):
        raise ValueError("invalid_kickoff_timestamp")
    return pd.Timestamp(timestamp)


def kickoff_key(row: dict[str, Any]) -> tuple[str, pd.Timestamp]:
    """Devuelve la identidad causal de un lote pre-match."""

    return str(row["league_slug"]), normalized_kickoff(row["match_date"])


def kickoff_buckets(
    rows: Iterable[dict[str, Any]],
) -> Iterator[list[dict[str, Any]]]:
    """Agrupa partidos de la misma liga y kickoff antes de actualizar."""

    ordered = sorted(rows, key=lambda row: (
        normalized_kickoff(row["match_date"]),
        str(row["league_slug"]),
        int(row["match_id"]),
    ))
    for _, bucket in groupby(ordered, key=kickoff_key):
        yield list(bucket)


def global_kickoff_buckets(
    rows: Iterable[dict[str, Any]],
) -> Iterator[list[dict[str, Any]]]:
    """Agrupa por timestamp global para modelos con historia entre ligas."""

    ordered = sorted(rows, key=lambda row: (
        normalized_kickoff(row["match_date"]), int(row["match_id"])))
    for _, bucket in groupby(
            ordered, key=lambda row: normalized_kickoff(row["match_date"])):
        yield list(bucket)


def atomic_warmup_end(
    rows: list[dict[str, Any]], minimum: int,
) -> int:
    """Extiende el warm-up para no evaluar parte de un kickoff compartido."""

    if minimum < 0 or minimum > len(rows):
        raise ValueError("warmup_out_of_range")
    boundary = minimum
    while (
        0 < boundary < len(rows)
        and normalized_kickoff(rows[boundary - 1]["match_date"])
        == normalized_kickoff(rows[boundary]["match_date"])
    ):
        boundary += 1
    return boundary


def normalize_kickoff_splits(
    rows: Iterable[dict[str, Any]],
    split_order: tuple[str, ...] = ("fit", "selection", "confirmation"),
) -> list[dict[str, Any]]:
    """Mueve un kickoff completo al split temporal más tardío que lo toca."""

    rank = {name: index for index, name in enumerate(split_order)}
    output: list[dict[str, Any]] = []
    for bucket in kickoff_buckets(rows):
        try:
            chosen = max(
                (str(row["split"]) for row in bucket), key=rank.__getitem__)
        except KeyError as error:
            raise ValueError(f"unknown_temporal_split:{error.args[0]}") from error
        output.extend({**row, "split": chosen} for row in bucket)
    return output


def aligned_fraction_boundaries(
    frame: pd.DataFrame, fractions: tuple[float, ...],
    timestamp_column: str = "match_date",
) -> tuple[int, ...]:
    """Alinea índices fraccionales al inicio de un kickoff completo."""

    if frame.empty:
        raise ValueError("cannot_split_empty_frame")
    timestamps = pd.to_datetime(
        frame[timestamp_column], utc=True, errors="raise").reset_index(drop=True)
    boundaries: list[int] = []
    for fraction in fractions:
        if not 0.0 < fraction < 1.0:
            raise ValueError("split_fraction_out_of_range")
        boundary = int(len(frame) * fraction)
        boundary = min(max(boundary, 1), len(frame) - 1)
        while boundary > 0 and timestamps.iloc[boundary] == timestamps.iloc[boundary - 1]:
            boundary -= 1
        if boundary <= 0 or (boundaries and boundary <= boundaries[-1]):
            raise ValueError("kickoff_aligned_split_is_empty")
        boundaries.append(boundary)
    return tuple(boundaries)


def split_boundary_is_causal(
    frame: pd.DataFrame, boundary: int,
    timestamp_column: str = "match_date",
) -> bool:
    """Comprueba que una frontera no divide un kickoff."""

    if boundary <= 0 or boundary >= len(frame):
        return False
    timestamps = pd.to_datetime(frame[timestamp_column], utc=True, errors="raise")
    return bool(timestamps.iloc[boundary - 1] < timestamps.iloc[boundary])
