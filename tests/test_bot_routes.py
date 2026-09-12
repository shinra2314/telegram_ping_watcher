"""Every button the bot draws must resolve to a registered section.

`callback_handler` is now a two-liner: ask the router, else answer "кнопка
устарела". That makes an unregistered section silently indistinguishable from a
stale button — the user taps 🎁 and is told to reopen the menu. This test is the
guard: it walks the callback strings the keyboards actually emit and asserts the
router claims each one, with the gate the section intended.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for path in (str(ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.bot import sections
from pulse_desk.bot.router import CallbackRouter

# One representative callback per screen the bot can draw, grouped by the gate
# the section declared. Extend this when a section grows a new prefix.
VIEWER_ROUTES = {
    "menu_main": None,
    "menu_help": None,
    "noop": None,
    "menu_summary": "stats",
    "menu_stats": "stats",
    "menu_status": "status",
    "menu_market": "market",
    "mk:p:d": "market",
    "mk:p:w": "market",
    "menu_giveaways": "giveaways",
    "menu_recent": "recent",
    "an:sum": "analytics",
    "an:flow": "analytics",
    "mon:feed:all": "recent",
    "mon:feed:win:3": "recent",
    "mon:open:42": "recent",
    "mon:ff:a:a:0:d:0:0:1": "recent",
    "mon:cs:a:a:0:d:0:0:1": "recent",
    "mon:q": "recent",
    "mon:pr": "recent",
    "mon:ex:c:a:a:0:d:0:0:1": "recent",
    "gw:feed:2": "giveaways",
    "gw:f:detected:0:0:1": "giveaways",
    "gw:a:detected:0:0:0": "giveaways",
    "gw:open:42:detected:0:0:1": "giveaways",
    # Действия на карточке гейтятся внутри секции (роль владельца), но само
    # семейство остаётся гостевым — иначе гость не откроет и сам список.
    "gw:an:42": "giveaways",
    "gw:sv:42:claimed": "giveaways",
    "gw:cl:0": "giveaways",
    "gw:lvgo:-100123": "giveaways",
    "cv": "market",
    "cv:in": "market",
    "cv:p:100:usd:uah": "market",
}

# Owner-only screens: a guest must be told "только владелец", never shown them.
ADMIN_ROUTES = [
    "menu_keys", "menu_scan", "menu_logs", "menu_restart", "menu_debts", "menu_obsidian",
    "db:s:a:1", "db:open:42:a:1", "db:m:42:a:1", "db:c:42:a:1", "db:x:42:a:1",
    "db:go:a:1", "db:clr:a:1",
    "ob:p:1", "ob:t:0:1", "ob:sync:1", "ob:cfg", "ob:en", "ob:wr", "ob:path",
    "ac", "ac:off:0",
    "sv", "sv:start:0", "sv:stop:0", "sv:re:0", "sv:log:0", "sv:allup", "sv:alldown",
    "bk", "bk:new", "bk:get:0",
    "dg", "dg:ev", "dg:sc", "dg:hi",
    "scan:start",
    "key:12", "key:f:12:_all", "key:e:12:_x", "key:rm:12", "key:delgo:12",
    "adm:home", "adm:members", "adm:access", "adm:restart:go", "adm:newkey",
    "mem:open:7", "mem:block:7", "mem:access:7",
    "acc:close:7", "acc:open:7", "acc:log:7",
    "rl", "rl:now", "rl:skip", "rl:tog", "rl:in",
    "bc:ok:3", "bc:no:3",
    "ping:fav:42", "ping:read:42",
    "pg:st:42:a:a:0:d:0:0:1", "pg:sts:42:read:a:a:0:d:0:0:1",
    "pg:gw:42:a:a:0:d:0:0:1", "pg:nt:42:a:a:0:d:0:0:1", "pg:tg:42:a:a:0:d:0:0:1",
    "fav_42", "read_42", "gconfirm_42", "gskip_42",
    "revokekey_5", "blockmember_7", "unblockmember_7",
    "accshow_7", "accoff_7", "accon_7",
    "hidebc_tok3n",
]

# Reachable by any member: their own prefs, their own engagement, and the
# cancel button that clears a stuck prompt.
MEMBER_ROUTES = ["pf", "pf_mu", "pf_sc", "pf_gw", "bcm:in:42", "bcm:skip:42", "st", "st_x",
                 # Настройки сидят на одном семействе `st_`, а роль проверяется
                 # внутри: иначе гость не смог бы отменить свой же ввод.
                 "st_r", "st_f_0", "st_v_0_1", "st_i_0", "st_l", "st_la", "st_ld_0"]

# The salary section answers "Неизвестная команда" instead of declaring a gate,
# so its routes carry no router-level gate at all.
UNGATED_ROUTES = ["sal", "sal:m:2026-09", "sal:top:2026-09", "sal:who:2026-09:2"]


def build_router() -> CallbackRouter:
    router = CallbackRouter()
    sections.register_all(router)
    return router


class RouteCoverageTests(unittest.TestCase):
    def setUp(self):
        self.router = build_router()

    def test_every_known_callback_is_claimed(self):
        every = (list(VIEWER_ROUTES) + ADMIN_ROUTES + MEMBER_ROUTES + UNGATED_ROUTES)
        unclaimed = [data for data in every if self.router.resolve(data) is None]
        self.assertEqual(unclaimed, [], f"unrouted callbacks: {unclaimed}")

    def test_feature_gates_match_the_section(self):
        for data, feature in VIEWER_ROUTES.items():
            route = self.router.resolve(data)
            self.assertIsNotNone(route, data)
            self.assertEqual(route.feature, feature, data)
            self.assertFalse(route.admin, f"{data} must not be owner-only")

    def test_owner_only_routes_are_gated(self):
        for data in ADMIN_ROUTES:
            route = self.router.resolve(data)
            self.assertIsNotNone(route, data)
            self.assertTrue(route.admin, f"{data} must be owner-only")

    def test_member_routes_are_not_owner_only(self):
        for data in MEMBER_ROUTES:
            route = self.router.resolve(data)
            self.assertIsNotNone(route, data)
            self.assertFalse(route.admin, f"{data} must stay open to members")

    def test_unknown_callback_is_left_to_the_stale_button_reply(self):
        for data in ("menu_nope", "zz:1", "totally_unknown"):
            self.assertIsNone(self.router.resolve(data), data)

    def test_bare_salary_screen_and_its_views_differ(self):
        # `sal` is the section's home card; `sal:<view>` is a screen inside it.
        self.assertIsNot(self.router.resolve("sal"), self.router.resolve("sal:top:2026-09"))


class PromptRegistryTests(unittest.TestCase):
    def test_every_armed_prompt_has_a_consumer(self):
        from pulse_desk.bot import pending

        sections.register_all(CallbackRouter())
        missing = [kind for kind in pending.INPUT_PROMPTS if kind not in pending._CONSUMERS]
        self.assertEqual(missing, [], f"prompts with no consumer: {missing}")

    def test_personal_prompts_are_not_owner_only(self):
        from pulse_desk.bot import pending

        for kind in ("convert", "min_score"):
            self.assertIn(kind, pending._CONSUMERS, kind)
            self.assertFalse(pending._CONSUMERS[kind][1], f"{kind} must stay open to members")


if __name__ == "__main__":
    unittest.main()
