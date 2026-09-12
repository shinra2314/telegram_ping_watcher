"""The callback registry that replaces the if-chain.

The chain's ordering was implicit — a branch matched because it happened to sit
above another one. Here the same precedence is a rule (exact beats family), so
these tests pin the two cases that used to depend on line order: a bare ``sal``
screen next to the ``sal:<view>`` family, and a family that must not swallow a
longer name that merely starts with the same letters.
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

from pulse_desk.bot.router import ADMIN_ONLY, FEATURE_DENIED, CallbackRouter, Click

from _fakes import FakeEvent


def _has_feature(perms: dict, code: str) -> bool:
    return code in (perms or {}).get("features", [])


def _click(data: str, role: str = "admin", perms: dict | None = None) -> Click:
    return Click(
        event=FakeEvent(data=data.encode()),
        data=data,
        role=role,
        perms=perms if perms is not None else {"features": []},
        has_feature=_has_feature,
    )


class ClickTests(unittest.TestCase):
    def test_segments_split_on_colon(self):
        self.assertEqual(_click("gw:f:date:0:2").seg, ["gw", "f", "date", "0", "2"])

    def test_arg_defaults_when_short(self):
        click = _click("an")
        self.assertEqual(click.arg(1, "sum"), "sum")

    def test_int_arg_is_none_on_garbage(self):
        self.assertIsNone(_click("mon:open:abc").int_arg(2))
        self.assertIsNone(_click("mon:open").int_arg(2))
        self.assertEqual(_click("mon:open:42").int_arg(2), 42)

    def test_tail_after_underscore(self):
        self.assertEqual(_click("hidebc_tok3n").tail(), "tok3n")

    def test_admin_passes_every_feature_gate(self):
        self.assertTrue(_click("x", role="admin").feature_ok("market"))

    def test_viewer_needs_the_grant(self):
        self.assertFalse(_click("x", role="viewer").feature_ok("market"))
        granted = _click("x", role="viewer", perms={"features": ["market"]})
        self.assertTrue(granted.feature_ok("market"))


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.router = CallbackRouter()
        self.calls: list[str] = []

    def _record(self, label: str):
        async def handler(click: Click) -> None:
            self.calls.append(f"{label}:{click.data}")
        return handler

    async def test_exact_beats_family(self):
        self.router.group("sal")(self._record("family"))
        self.router.exact("sal")(self._record("screen"))
        self.assertTrue(await self.router.dispatch(_click("sal")))
        self.assertTrue(await self.router.dispatch(_click("sal:m:2026-09")))
        self.assertEqual(self.calls, ["screen:sal", "family:sal:m:2026-09"])

    async def test_family_matches_bare_name_too(self):
        self.router.group("cv")(self._record("cv"))
        self.assertTrue(await self.router.dispatch(_click("cv")))
        self.assertTrue(await self.router.dispatch(_click("cv:p:100:usd:uah")))
        self.assertEqual(len(self.calls), 2)

    async def test_underscore_family(self):
        self.router.group("blockmember", sep="_")(self._record("block"))
        self.assertTrue(await self.router.dispatch(_click("blockmember_42")))
        self.assertEqual(self.calls, ["block:blockmember_42"])

    async def test_family_does_not_swallow_a_longer_token(self):
        # `read_7` must not be claimed by a `readall` family and vice versa.
        self.router.group("read", sep="_")(self._record("read"))
        self.assertFalse(await self.router.dispatch(_click("readall_7")))
        self.assertTrue(await self.router.dispatch(_click("read_7")))
        self.assertEqual(self.calls, ["read:read_7"])

    async def test_unknown_is_not_claimed(self):
        self.assertFalse(await self.router.dispatch(_click("whatever")))
        self.assertEqual(self.calls, [])

    async def test_last_registration_wins_for_the_same_name(self):
        self.router.exact("x")(self._record("first"))
        self.router.exact("x")(self._record("second"))
        await self.router.dispatch(_click("x"))
        self.assertEqual(self.calls, ["second:x"])


class GateTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.router = CallbackRouter()
        self.ran = False

    def _handler(self):
        async def handler(click: Click) -> None:
            self.ran = True
        return handler

    async def test_admin_gate_blocks_viewer_with_owner_wording(self):
        self.router.group("rl", admin=True)(self._handler())
        click = _click("rl:now", role="viewer")
        self.assertTrue(await self.router.dispatch(click))
        self.assertFalse(self.ran)
        self.assertEqual(click.event.answers[-1]["text"], ADMIN_ONLY)
        self.assertTrue(click.event.answers[-1]["alert"])

    async def test_feature_gate_blocks_with_section_wording(self):
        self.router.group("an", feature="analytics")(self._handler())
        click = _click("an:sum", role="viewer")
        self.assertTrue(await self.router.dispatch(click))
        self.assertFalse(self.ran)
        self.assertEqual(click.event.answers[-1]["text"], FEATURE_DENIED)

    async def test_feature_gate_passes_for_admin(self):
        self.router.group("an", feature="analytics")(self._handler())
        self.assertTrue(await self.router.dispatch(_click("an:sum", role="admin")))
        self.assertTrue(self.ran)

    async def test_feature_gate_passes_for_granted_viewer(self):
        self.router.group("an", feature="analytics")(self._handler())
        click = _click("an:sum", role="viewer", perms={"features": ["analytics"]})
        self.assertTrue(await self.router.dispatch(click))
        self.assertTrue(self.ran)


class RegistrationTests(unittest.TestCase):
    def test_unknown_separator_is_rejected(self):
        with self.assertRaises(ValueError):
            CallbackRouter().group("x", sep="|")

    def test_resolve_reports_ownership_without_running(self):
        router = CallbackRouter()
        async def handler(click: Click) -> None:  # pragma: no cover - never run
            raise AssertionError("must not run")
        router.group("gw")(handler)
        self.assertIsNotNone(router.resolve("gw:open:5"))
        self.assertIsNone(router.resolve("menu_main"))


if __name__ == "__main__":
    unittest.main()
