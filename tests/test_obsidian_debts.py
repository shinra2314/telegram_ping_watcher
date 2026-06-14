"""Unit tests for the Obsidian debts sync core (pure functions).

Covers parsing the hand-maintained ``Долги.md`` note, t.me link
normalisation, single-line checkbox edits, append rendering, and the
note<->app reconciliation rules.  No filesystem or DB access here.
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pulse_desk.obsidian_debts import (  # noqa: E402
    build_snapshot,
    compute_appends,
    normalize_tme_link,
    parse_note,
    reconcile_status,
    render_append_line,
    set_checkbox,
)

SAMPLE_NOTE = """\
---
tags:
  - утилиты
  - розыгрыш
---

`BUTTON[home, task-note]`

# 💸 Долги — розыгрыши

```dataviewjs
const tasks = dv.current().file.tasks;
- [ ] this line lives inside a code fence and must be ignored
```

---
# @w3v8f0rm 100 мне
- [x] https://t.me/VortexXcs/681 Дигл Head Treated ✅ 2026-05-14
- [ ] https://t.me/h0wl_dragon/4769 USP-S | Взгляд в прошлое
# @MuverGT 50 [[Илья|Пумбе]] 50 мне
- [x] https://t.me/skynutss/119 м4а1 ночной ужас пумбе ✅ 2026-05-17
- [ ] доллар криптой мне
"""


class ParseNoteTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_note(SAMPLE_NOTE)

    def test_only_at_headers_become_sections(self):
        # "# 💸 Долги — розыгрыши" is not @-prefixed → not a section.
        self.assertEqual([s.username for s in self.parsed.sections], ["w3v8f0rm", "MuverGT"])

    def test_split_rule_captured(self):
        self.assertEqual(self.parsed.sections[0].split_rule, "100 мне")
        self.assertEqual(self.parsed.sections[1].split_rule, "50 [[Илья|Пумбе]] 50 мне")

    def test_code_fence_items_ignored(self):
        # The `- [ ]` inside the dataviewjs fence must not be parsed.
        all_titles = " ".join(i.title for i in self.parsed.items)
        self.assertNotIn("code fence", all_titles)
        self.assertEqual(len(self.parsed.items), 4)

    def test_item_fields(self):
        first = self.parsed.sections[0].items[0]
        self.assertTrue(first.checked)
        self.assertEqual(first.link_norm, "t.me/vortexxcs/681")
        self.assertEqual(first.done_date, "2026-05-14")
        self.assertIn("Дигл Head Treated", first.title)
        self.assertEqual(first.username, "w3v8f0rm")

        second = self.parsed.sections[0].items[1]
        self.assertFalse(second.checked)
        self.assertEqual(second.link_norm, "t.me/h0wl_dragon/4769")
        self.assertIsNone(second.done_date)

    def test_item_without_link(self):
        manual = self.parsed.sections[1].items[1]
        self.assertIsNone(manual.link)
        self.assertIsNone(manual.link_norm)
        self.assertIn("доллар криптой", manual.title)

    def test_line_index_points_at_raw_line(self):
        first = self.parsed.sections[0].items[0]
        self.assertEqual(self.parsed.lines[first.line_index], first.raw)


class NormalizeLinkTests(unittest.TestCase):
    def test_basic(self):
        self.assertEqual(normalize_tme_link("https://t.me/VortexXcs/681"), "t.me/vortexxcs/681")

    def test_strips_trailing_punctuation(self):
        self.assertEqual(normalize_tme_link("https://t.me/ezjko/388,"), "t.me/ezjko/388")
        self.assertEqual(normalize_tme_link("(https://t.me/ezjko/388)"), "t.me/ezjko/388")

    def test_no_scheme(self):
        self.assertEqual(normalize_tme_link("t.me/cs_winner"), "t.me/cs_winner")

    def test_private_channel(self):
        self.assertEqual(normalize_tme_link("https://t.me/c/2148404508/9186"), "t.me/c/2148404508/9186")

    def test_embedded_in_text(self):
        self.assertEqual(
            normalize_tme_link("- [x] https://t.me/RushSite/3718 юсп билет в ад ✅ 2026-05-29"),
            "t.me/rushsite/3718",
        )

    def test_none_when_absent(self):
        self.assertIsNone(normalize_tme_link("доллар криптой мне"))


class SetCheckboxTests(unittest.TestCase):
    def test_check_and_stamp_date(self):
        out = set_checkbox("- [ ] https://t.me/x/1 prize", True, "2026-06-14")
        self.assertEqual(out, "- [x] https://t.me/x/1 prize ✅ 2026-06-14")

    def test_uncheck_removes_date(self):
        out = set_checkbox("- [x] foo ✅ 2026-01-01", False)
        self.assertEqual(out, "- [ ] foo")

    def test_preserves_indentation(self):
        out = set_checkbox("\t- [ ] foo", True, "2026-06-14")
        self.assertEqual(out, "\t- [x] foo ✅ 2026-06-14")

    def test_does_not_double_stamp(self):
        out = set_checkbox("- [x] foo ✅ 2026-01-01", True, "2026-06-14")
        self.assertEqual(out, "- [x] foo ✅ 2026-06-14")


class RenderAppendTests(unittest.TestCase):
    def test_link_and_title(self):
        self.assertEqual(render_append_line("https://t.me/a/1", "prize"), "- [ ] https://t.me/a/1 prize")

    def test_link_only(self):
        self.assertEqual(render_append_line("https://t.me/a/1"), "- [ ] https://t.me/a/1")


class ReconcileStatusTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_note(SAMPLE_NOTE)

    def test_note_checked_promotes_pending_ping(self):
        pings = [{"id": 1, "link": "https://t.me/VortexXcs/681", "giveaway_status": "pending", "action_status": "claim_prize", "mentions": "[]"}]
        updates = reconcile_status(self.parsed, pings)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["ping_id"], 1)
        self.assertEqual(updates[0]["giveaway_status"], "claimed")
        self.assertEqual(updates[0]["action_status"], "claimed")

    def test_note_unchecked_reverts_claimed_ping(self):
        pings = [{"id": 2, "link": "https://t.me/h0wl_dragon/4769", "giveaway_status": "claimed", "action_status": "claimed", "mentions": "[]"}]
        updates = reconcile_status(self.parsed, pings)
        self.assertEqual(len(updates), 1)
        self.assertEqual(updates[0]["ping_id"], 2)
        self.assertEqual(updates[0]["giveaway_status"], "pending")

    def test_negative_status_not_overridden(self):
        # Note has [x] for VortexXcs/681 but app marked it scam → leave it.
        pings = [{"id": 3, "link": "https://t.me/VortexXcs/681", "giveaway_status": "scam", "action_status": "scam", "mentions": "[]"}]
        self.assertEqual(reconcile_status(self.parsed, pings), [])

    def test_already_consistent_no_update(self):
        pings = [{"id": 4, "link": "https://t.me/VortexXcs/681", "giveaway_status": "claimed", "action_status": "claimed", "mentions": "[]"}]
        self.assertEqual(reconcile_status(self.parsed, pings), [])

    def test_ping_absent_from_note_untouched(self):
        pings = [{"id": 5, "link": "https://t.me/unknown/99", "giveaway_status": "claimed", "action_status": "claimed", "mentions": "[]"}]
        self.assertEqual(reconcile_status(self.parsed, pings), [])


class ComputeAppendsTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_note(SAMPLE_NOTE)

    def test_new_win_under_existing_section_is_appended(self):
        debts = [{"link": "https://t.me/fresh/1", "title": "новый приз", "mentions": ["w3v8f0rm"]}]
        appends = compute_appends(self.parsed, debts)
        self.assertEqual(len(appends), 1)
        self.assertEqual(appends[0]["username"], "w3v8f0rm")
        self.assertEqual(appends[0]["link"], "https://t.me/fresh/1")

    def test_existing_link_not_appended(self):
        debts = [{"link": "https://t.me/VortexXcs/681", "title": "dup", "mentions": ["w3v8f0rm"]}]
        self.assertEqual(compute_appends(self.parsed, debts), [])

    def test_missing_section_skipped(self):
        debts = [{"link": "https://t.me/fresh/2", "title": "x", "mentions": ["nobody_here"]}]
        self.assertEqual(compute_appends(self.parsed, debts), [])


class BuildSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.parsed = parse_note(SAMPLE_NOTE)

    def test_overall_progress(self):
        snap = build_snapshot(self.parsed, [])
        # 4 items, 2 checked.
        self.assertEqual(snap["overall"]["total"], 4)
        self.assertEqual(snap["overall"]["done"], 2)
        self.assertEqual(snap["overall"]["pct"], 50)

    def test_group_shape(self):
        snap = build_snapshot(self.parsed, [])
        self.assertEqual(len(snap["groups"]), 2)
        g0 = snap["groups"][0]
        self.assertEqual(g0["username"], "w3v8f0rm")
        self.assertEqual(g0["total"], 2)
        self.assertEqual(g0["done"], 1)

    def test_matched_ping_id_attached(self):
        pings = [{"id": 7, "link": "https://t.me/VortexXcs/681", "giveaway_status": "claimed", "action_status": "claimed", "mentions": "[]"}]
        snap = build_snapshot(self.parsed, pings)
        item = snap["groups"][0]["items"][0]
        self.assertEqual(item["matched_ping_id"], 7)


if __name__ == "__main__":
    unittest.main()
