from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import json

from pulse_desk.access_control import (
    Window,
    find_undoable,
    next_boundary,
    parse_repeat_rule,
    plan_undo,
    resolve_access,
    window_contains,
    window_from_row,
)


def utc(y, mo, d, h=0, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)


def win(**kwargs):
    base = dict(
        id=1,
        enabled=True,
        active=True,
        start_at=None,
        end_at=None,
        tz="UTC",
        repeat={"type": "none"},
        priority=100,
        updated_at="2026-01-01T00:00:00",
    )
    base.update(kwargs)
    return Window(**base)


class ParseRepeatRuleTests(unittest.TestCase):
    def test_dict_passthrough(self):
        self.assertEqual(parse_repeat_rule({"type": "daily", "from": "09:00", "to": "18:00"})["type"], "daily")

    def test_json_string(self):
        self.assertEqual(parse_repeat_rule('{"type": "weekly", "days": [1, 2]}')["days"], [1, 2])

    def test_none_and_garbage_default_to_none(self):
        for raw in (None, "", "not json", "[1,2]", 42):
            self.assertEqual(parse_repeat_rule(raw), {"type": "none"}, raw)


class WindowContainsAbsoluteTests(unittest.TestCase):
    def test_none_window_inside_abs_bounds(self):
        w = win(repeat={"type": "none"}, start_at=utc(2026, 6, 1), end_at=utc(2026, 6, 30))
        self.assertTrue(window_contains(w, utc(2026, 6, 15)))

    def test_expired_one_shot_not_contained(self):
        w = win(repeat={"type": "none"}, start_at=utc(2026, 6, 1), end_at=utc(2026, 6, 2))
        self.assertFalse(window_contains(w, utc(2026, 6, 15)))

    def test_none_window_without_bounds_is_never_contained(self):
        self.assertFalse(window_contains(win(repeat={"type": "none"}), utc(2026, 6, 15)))

    def test_inactive_window_never_contained(self):
        w = win(active=False, repeat={"type": "none"}, start_at=utc(2026, 6, 1), end_at=utc(2026, 6, 30))
        self.assertFalse(window_contains(w, utc(2026, 6, 15)))


class WindowContainsDailyTests(unittest.TestCase):
    def test_inside_same_day_range(self):
        w = win(repeat={"type": "daily", "from": "09:00", "to": "18:00"})
        self.assertTrue(window_contains(w, utc(2026, 6, 15, 12)))
        self.assertFalse(window_contains(w, utc(2026, 6, 15, 8)))

    def test_end_is_exclusive(self):
        w = win(repeat={"type": "daily", "from": "09:00", "to": "18:00"})
        self.assertFalse(window_contains(w, utc(2026, 6, 15, 18)))

    def test_midnight_wrap(self):
        w = win(repeat={"type": "daily", "from": "23:00", "to": "08:00"})
        self.assertTrue(window_contains(w, utc(2026, 6, 15, 2)))    # 02:00
        self.assertTrue(window_contains(w, utc(2026, 6, 15, 23, 30)))
        self.assertFalse(window_contains(w, utc(2026, 6, 15, 12)))


class WindowContainsWeeklyTests(unittest.TestCase):
    def test_weekday_match(self):
        # 2026-06-15 is a Monday (ISO weekday 1)
        w = win(repeat={"type": "weekly", "days": [1, 2, 3, 4, 5], "from": "09:00", "to": "18:00"})
        self.assertTrue(window_contains(w, utc(2026, 6, 15, 12)))   # Monday noon
        self.assertFalse(window_contains(w, utc(2026, 6, 20, 12)))  # Saturday noon

    def test_overnight_belongs_to_start_day(self):
        # Friday 22:00 -> Saturday 06:00. Sat 02:00 is covered via Friday membership.
        w = win(repeat={"type": "weekly", "days": [5], "from": "22:00", "to": "06:00"})
        self.assertTrue(window_contains(w, utc(2026, 6, 19, 23)))   # Friday 23:00
        self.assertTrue(window_contains(w, utc(2026, 6, 20, 2)))    # Saturday 02:00 (Fri carryover)
        self.assertFalse(window_contains(w, utc(2026, 6, 20, 12)))  # Saturday noon


class TimezoneDstTests(unittest.TestCase):
    def test_dst_offset_handled_both_seasons(self):
        # 09:00-10:00 Europe/Kyiv. Summer = UTC+3, winter = UTC+2.
        w = win(tz="Europe/Kyiv", repeat={"type": "daily", "from": "09:00", "to": "10:00"})
        self.assertTrue(window_contains(w, utc(2026, 7, 1, 6, 30)))   # 09:30 Kyiv summer
        self.assertTrue(window_contains(w, utc(2026, 1, 1, 7, 30)))   # 09:30 Kyiv winter
        self.assertFalse(window_contains(w, utc(2026, 7, 1, 7, 30)))  # 10:30 Kyiv summer


class CronWindowTests(unittest.TestCase):
    def test_inside_cron_fire_window(self):
        # 09:00 weekdays, 60-min duration. 2026-06-15 is Monday.
        w = win(repeat={"type": "cron", "expr": "0 9 * * 1-5", "dur_min": 60})
        self.assertTrue(window_contains(w, utc(2026, 6, 15, 9, 30)))
        self.assertFalse(window_contains(w, utc(2026, 6, 15, 11, 0)))
        self.assertFalse(window_contains(w, utc(2026, 6, 20, 9, 30)))  # Saturday

    def test_cron_without_expr_is_false(self):
        self.assertFalse(window_contains(win(repeat={"type": "cron"}), utc(2026, 6, 15, 9, 30)))


def _audit(id, action, schedule_id=None, old_value=None, new_value=None):
    return {
        "id": id,
        "action": action,
        "schedule_id": schedule_id,
        "old_value": json.dumps(old_value) if old_value is not None else None,
        "new_value": json.dumps(new_value) if new_value is not None else None,
    }


class FindUndoableTests(unittest.TestCase):
    def test_returns_newest_reversible(self):
        rows = [_audit(5, "create", schedule_id=10), _audit(4, "manual_off", schedule_id=9)]
        self.assertEqual(find_undoable(rows)["id"], 5)

    def test_skips_already_undone(self):
        rows = [
            _audit(6, "undo", new_value={"undone_audit_id": 5}),
            _audit(5, "create", schedule_id=10),
            _audit(4, "delete", schedule_id=9),
        ]
        self.assertEqual(find_undoable(rows)["id"], 4)

    def test_skips_flip_and_undo(self):
        rows = [_audit(7, "flip"), _audit(6, "undo", new_value={"undone_audit_id": 1})]
        self.assertIsNone(find_undoable(rows))

    def test_empty(self):
        self.assertIsNone(find_undoable([]))


class PlanUndoTests(unittest.TestCase):
    def test_create_deactivates(self):
        self.assertEqual(plan_undo(_audit(1, "create", schedule_id=10)), {"op": "deactivate", "schedule_id": 10})

    def test_manual_off_deactivates(self):
        self.assertEqual(plan_undo(_audit(1, "manual_off", schedule_id=11)), {"op": "deactivate", "schedule_id": 11})

    def test_delete_reactivates(self):
        self.assertEqual(plan_undo(_audit(1, "delete", schedule_id=12)), {"op": "reactivate", "schedule_id": 12})

    def test_manual_on_reactivates_many(self):
        plan = plan_undo(_audit(1, "manual_on", new_value={"cancelled_ids": [1, 2, 3]}))
        self.assertEqual(plan, {"op": "reactivate_many", "schedule_ids": [1, 2, 3]})

    def test_policy_restores_old(self):
        plan = plan_undo(_audit(1, "policy", old_value={"policy": "allow"}, new_value={"policy": "deny"}))
        self.assertEqual(plan, {"op": "set_policy", "policy": "allow"})


class ResolveAccessTests(unittest.TestCase):
    def test_blocked_member_denied_regardless(self):
        member = {"blocked": 1, "access_default_policy": "allow"}
        d = resolve_access(member, [win(enabled=True, repeat={"type": "daily", "from": "00:00", "to": "23:59"})], utc(2026, 6, 15, 12))
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "blocked")

    def test_default_allow_no_windows(self):
        d = resolve_access({"access_default_policy": "allow"}, [], utc(2026, 6, 15, 12))
        self.assertTrue(d.allowed)
        self.assertEqual(d.reason, "default_policy")

    def test_default_deny_no_windows(self):
        d = resolve_access({"access_default_policy": "deny"}, [], utc(2026, 6, 15, 12))
        self.assertFalse(d.allowed)

    def test_blackout_window_denies_default_allow(self):
        member = {"access_default_policy": "allow"}
        w = win(id=7, enabled=False, repeat={"type": "daily", "from": "09:00", "to": "18:00"})
        d = resolve_access(member, [w], utc(2026, 6, 15, 12))
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "window#7")

    def test_allow_window_opens_default_deny(self):
        member = {"access_default_policy": "deny"}
        w = win(id=9, enabled=True, repeat={"type": "daily", "from": "09:00", "to": "18:00"})
        self.assertTrue(resolve_access(member, [w], utc(2026, 6, 15, 12)).allowed)
        self.assertFalse(resolve_access(member, [w], utc(2026, 6, 15, 20)).allowed)

    def test_priority_breaks_overlap(self):
        member = {"access_default_policy": "allow"}
        allow_w = win(id=1, enabled=True, priority=100, repeat={"type": "daily", "from": "00:00", "to": "23:59"})
        block_w = win(id=2, enabled=False, priority=500, repeat={"type": "daily", "from": "09:00", "to": "18:00"})
        d = resolve_access(member, [allow_w, block_w], utc(2026, 6, 15, 12))
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "window#2")

    def test_priority_tie_newest_wins(self):
        member = {"access_default_policy": "allow"}
        older = win(id=1, enabled=True, priority=100, updated_at="2026-01-01T00:00:00", repeat={"type": "daily", "from": "00:00", "to": "23:59"})
        newer = win(id=2, enabled=False, priority=100, updated_at="2026-06-01T00:00:00", repeat={"type": "daily", "from": "00:00", "to": "23:59"})
        d = resolve_access(member, [older, newer], utc(2026, 6, 15, 12))
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "window#2")


class NextBoundaryTests(unittest.TestCase):
    def test_boundary_at_window_open(self):
        w = win(repeat={"type": "daily", "from": "09:00", "to": "18:00"})
        boundary = next_boundary([w], utc(2026, 6, 15, 8, 0))
        self.assertEqual(boundary, utc(2026, 6, 15, 9, 0))

    def test_no_windows_returns_horizon(self):
        now = utc(2026, 6, 15, 8, 0)
        self.assertEqual(next_boundary([], now, horizon_hours=12), now + timedelta(hours=12))


class WindowFromRowTests(unittest.TestCase):
    def test_parses_iso_and_repeat_json(self):
        row = {
            "id": 3,
            "enabled": 0,
            "active": 1,
            "start_at": "2026-06-01T00:00:00",
            "end_at": "2026-06-30T00:00:00",
            "timezone": "Europe/Kyiv",
            "repeat_rule": '{"type": "weekly", "days": [6, 7]}',
            "priority": 200,
            "updated_at": "2026-06-01T10:00:00",
        }
        w = window_from_row(row)
        self.assertEqual(w.id, 3)
        self.assertFalse(w.enabled)
        self.assertTrue(w.active)
        self.assertEqual(w.tz, "Europe/Kyiv")
        self.assertEqual(w.repeat, {"type": "weekly", "days": [6, 7]})
        self.assertEqual(w.priority, 200)
        # naive ISO is interpreted as UTC
        self.assertEqual(w.start_at, utc(2026, 6, 1))

    def test_null_bounds_stay_none(self):
        row = {"id": 1, "enabled": 1, "active": 1, "start_at": None, "end_at": None,
               "timezone": "UTC", "repeat_rule": '{"type":"daily","from":"09:00","to":"18:00"}',
               "priority": 100, "updated_at": ""}
        w = window_from_row(row)
        self.assertIsNone(w.start_at)
        self.assertIsNone(w.end_at)


if __name__ == "__main__":
    unittest.main()
