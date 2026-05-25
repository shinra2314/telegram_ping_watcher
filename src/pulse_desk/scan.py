from __future__ import annotations

from typing import Mapping


def channel_sweep_start_id(checkpoints: Mapping[str, int]) -> int | None:
    """Return a safe min_id for one-pass channel scanning.

    A channel sweep is only safe after every tracked username has an existing
    checkpoint for the channel. First scans keep the older per-username search
    path so historical matches are not skipped.
    """

    if not checkpoints:
        return None
    values = [int(value or 0) for value in checkpoints.values()]
    if any(value <= 0 for value in values):
        return None
    return min(values)


def clamp_runtime_int(value: object, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def normalize_scan_history_limit(value: object, default: int = 0) -> int:
    """Return 0 for an unlimited Telegram history scan."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, parsed)


def normalize_recent_edit_scan_limit(value: object, default: int = 20) -> int:
    """Clamp the recent-message sweep used to catch edited giveaway posts."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(0, min(500, parsed))
