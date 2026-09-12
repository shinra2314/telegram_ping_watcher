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


EDIT_SWEEP_IDLE_INTERVAL_SECONDS = 3600.0
MIN_SCAN_GAP_SECONDS = 30.0


def channel_has_new_messages(latest_message_id: object, sweep_start_id: object) -> bool:
    """True when a channel posted something past its sweep checkpoint.

    The dialog list already carries every channel's newest message id, so an
    idle channel can be skipped without spending a history request on it.
    Missing or zero ids never skip — on unknown data we scan.
    """

    try:
        latest = int(latest_message_id or 0)
        start = int(sweep_start_id or 0)
    except (TypeError, ValueError):
        return True
    if latest <= 0 or start <= 0:
        return True
    return latest > start


def edit_sweep_due(
    last_run_at: float | None,
    now: float,
    interval: float = EDIT_SWEEP_IDLE_INTERVAL_SECONDS,
) -> bool:
    """Whether an idle channel is due for its recent-window (edit) pass.

    Edits leave the channel's top message id untouched, so idle channels still
    need re-reading — just far less often than every sweep.
    """

    if last_run_at is None:
        return True
    try:
        return (float(now) - float(last_run_at)) >= float(interval)
    except (TypeError, ValueError):
        return True


def next_scan_delay(
    interval_seconds: object,
    elapsed_seconds: object,
    minimum: float = MIN_SCAN_GAP_SECONDS,
) -> float:
    """Sleep only what is left of the scan cycle.

    Sleeping the full interval *after* the sweep made the real gap between
    sweeps ``interval + sweep duration`` — a 20-minute sweep on a 15-minute
    interval meant a new message could sit unseen for over half an hour.
    """

    try:
        interval = float(interval_seconds)
        elapsed = max(0.0, float(elapsed_seconds))
    except (TypeError, ValueError):
        return float(minimum)
    return max(float(minimum), interval - elapsed)


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
