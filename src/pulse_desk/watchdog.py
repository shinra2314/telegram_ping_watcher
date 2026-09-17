"""Pure watchdog logic: classify background-job health and diff alert state.

No I/O — fully unit-testable, mirrors ``access_control.py``. The loop in
``loops.py`` feeds it heartbeat ages and acts on the returned alerts/recoveries.

A job is "stale" when it hasn't reported a successful cycle within its
threshold, "missing" when it isn't running at all. Both are unhealthy and
trigger an admin alert exactly once per outage (de-duped via ``diff_health``).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

# Jobs started only when their feature is configured. They report a heartbeat
# every cycle; the window is ten polls (at least ten minutes). The panel server
# and its tunnel are not here: they block on a server/child process and have no
# cycle to report, so a heartbeat window would page about a healthy process.
FEATURE_JOBS = frozenset({"obsidian-sync", "salary-sync"})


@dataclass(frozen=True)
class JobHealth:
    name: str
    healthy: bool
    reason: str  # "ok" | "stale" | "missing" | "starting"
    age_seconds: int | None
    threshold_seconds: int


def classify_job(
    name: str,
    *,
    running: bool,
    age_seconds: int | None,
    threshold_seconds: int,
) -> JobHealth:
    """Decide whether one job is healthy right now.

    ``age_seconds`` is seconds since the job last reported a successful cycle,
    or since it started if it has never succeeded yet. ``None`` means the job
    only just started and has no age to judge — treated as healthy
    ("starting") so a fresh boot never pages the admin.
    """
    if not running:
        return JobHealth(name, False, "missing", age_seconds, threshold_seconds)
    if age_seconds is None:
        return JobHealth(name, True, "starting", None, threshold_seconds)
    if age_seconds > threshold_seconds:
        return JobHealth(name, False, "stale", age_seconds, threshold_seconds)
    return JobHealth(name, True, "ok", age_seconds, threshold_seconds)


def diff_health(
    previously_unhealthy: set[str],
    healths: list[JobHealth],
) -> tuple[list[JobHealth], list[JobHealth]]:
    """Split current health into newly-failing and newly-recovered jobs.

    Anything unhealthy that was healthy before is a *new alert*. Anything
    healthy that was unhealthy before is a *recovery*. Jobs that stay unhealthy
    produce neither, so the admin is paged once per outage, not every tick.
    """
    new_alerts = [h for h in healths if not h.healthy and h.name not in previously_unhealthy]
    recoveries = [h for h in healths if h.healthy and h.name in previously_unhealthy]
    return new_alerts, recoveries


def default_thresholds(
    *,
    scan_interval_seconds: int,
    market_poll_seconds: int,
    flood_wait_max_seconds: int = 1800,
    bot_configured: bool = False,
    enabled_features: Optional[dict[str, int]] = None,
) -> dict[str, int]:
    """Max seconds each monitored job may go without a successful cycle.

    Derived from each job's natural cadence × slack + grace, so widening a
    runtime interval (e.g. ``SCAN_INTERVAL_SECONDS``) widens its window too and
    never produces a false "stale" alert. Always-on jobs are listed
    unconditionally; feature-gated loops (``FEATURE_JOBS``) only when
    ``enabled_features`` names them, mapped to their poll interval in seconds,
    so a feature that is simply turned off never pages.

    ``auto-scan`` reports progress *per channel* while it works (see
    ``scan_engine.scan_single_account``), so the only legitimately silent gaps
    are the idle sleep between sweeps and a single capped ``FloodWait`` stall.
    Its window is therefore ``interval + flood_wait_max + grace`` — wide enough
    that a throttled-but-healthy scan never pages, while a wedged loop (no
    progress at all) still trips after the window.
    """
    thresholds = {
        "auto-scan": max(600, scan_interval_seconds + flood_wait_max_seconds + 300),
        "broadcast-approval": 300,
        "source-scores": 1200,
        "access-scheduler": 360,
        "market-fetch": max(600, market_poll_seconds * 3 + 120),
        # 20 s poll; a wedged outbox holds every delayed member copy.
        "pending-sends": 300,
        # 30 s poll; a wedged loop means an owed ping card never goes out.
        "notify-retry": 300,
        # Hourly pass; VACUUM plus a zipped backup can take a few minutes.
        "maintenance": 2 * 3600,
        # Both tick every 30 s (they no longer sleep to their target time).
        "daily-digest": 600,
        "roulette-reminder": 600,
        # 60 s tick: stale prompts, abandoned logins, scheduled deletions.
        "bot-janitor": 600,
        # 5 min tick plus an hourly get_me probe per account (20 s timeout each).
        "account-health": 1800,
        # 60 s tick; sends only on Mondays and the 1st.
        "weekly-report": 600,
    }
    for name, poll_seconds in (enabled_features or {}).items():
        if name in FEATURE_JOBS:
            thresholds[name] = max(600, int(poll_seconds or 0) * 10)
    if bot_configured:
        # The loop ticks at least every BOT_CONNECTION_POLL_SECONDS (15 s), and a
        # reconnect backoff tops out at 300 s, so silence past that means the
        # supervisor itself is gone — and with it every incoming button press.
        thresholds["bot-connection"] = 600
    return thresholds


def format_age(age_seconds: int | None) -> str:
    """Compact human-friendly duration for alert messages (ru)."""
    if age_seconds is None:
        return "—"
    if age_seconds < 90:
        return f"{age_seconds} с"
    if age_seconds < 5400:
        return f"{age_seconds // 60} мин"
    return f"{age_seconds // 3600} ч"
