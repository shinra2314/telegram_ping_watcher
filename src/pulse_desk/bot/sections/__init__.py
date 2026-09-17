"""Feature sections: one module per area of the bot.

Each module owns its renderers and its callback handlers, and exposes
``register(router)`` so ``init_bot`` does not have to know what callbacks the
section answers. Import order here is the only ordering that matters, and the
router's exact-beats-family rule makes even that irrelevant for dispatch.
"""
from __future__ import annotations

from . import (
    accounts, analytics, backups, broadcast, converter, dashboard, debts, diagnostics, feed,
    giveaways, home, ignored, keys, legacy, market, members, obsidian, prefs, report, roulette, salary, scan,
    services, settings, system, undo, vacation,
)

SECTIONS = (
    accounts, analytics, backups, broadcast, converter, dashboard, debts, diagnostics, feed,
    giveaways, home, ignored, keys, legacy, market, members, obsidian, prefs, report, roulette, salary, scan,
    services, settings, system, undo, vacation,
)


def register_all(router) -> None:
    for section in SECTIONS:
        section.register(router)
