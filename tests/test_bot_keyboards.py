from __future__ import annotations

import sys
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

import unittest

from pulse_desk.bot.keyboards import back_home, section_nav


class SectionNavTests(unittest.TestCase):
    def test_one_row_home_then_refresh(self):
        rows = section_nav(b"menu_stats")
        self.assertEqual(len(rows), 1)
        labels = [b.text for b in rows[0]]
        self.assertEqual(labels, ["⬅️ Домой", "🔄 Обновить"])

    def test_home_button_targets_menu_main(self):
        rows = section_nav(b"menu_stats")
        self.assertEqual(rows[0][0].data, b"menu_main")

    def test_refresh_button_carries_given_callback(self):
        rows = section_nav(b"menu_market")
        self.assertEqual(rows[0][1].data, b"menu_market")


class BackHomeTests(unittest.TestCase):
    def test_single_home_button(self):
        rows = back_home()
        self.assertEqual(len(rows), 1)
        self.assertEqual(len(rows[0]), 1)
        self.assertEqual(rows[0][0].data, b"menu_main")
        self.assertEqual(rows[0][0].text, "⬅️ Домой")


from pulse_desk.bot.keyboards import (
    feed_filters_keyboard, feed_keyboard, feed_presets_keyboard, ping_card_keyboard,
    ping_giveaway_keyboard, ping_status_keyboard, ping_tags_keyboard,
)
from pulse_desk.bot.views import FEED_TYPES, FeedFilter, feed_filter_cb


class FeedKeyboardTests(unittest.TestCase):
    """Лента теперь носит всю выборку в кнопке, а не один код фильтра.

    Старая форма (`mon:feed:<фильтр>`) осталась только входом: такие кнопки
    ещё живут в чатах, и клавиатура обязана их принимать.
    """

    ITEMS = [(842, "🔥 14:02 @chan"), (840, "• 13:40 @chan2")]

    def test_each_item_is_a_row_opening_that_ping(self):
        rows = feed_keyboard(self.ITEMS, FeedFilter())
        self.assertEqual(rows[0][0].data, b"mon:open:842")
        self.assertEqual(rows[1][0].data, b"mon:open:840")

    def test_quick_type_row_carries_the_whole_state(self):
        state = FeedFilter(favorite=True, sort="p")
        filt = feed_keyboard(self.ITEMS, state)[len(self.ITEMS)]
        datas = [b.data for b in filt]
        self.assertEqual(len(datas), 4)
        # switching type keeps favourite and sort, and returns to page 1
        self.assertIn(feed_filter_cb(state.with_(type="w")), datas)
        self.assertTrue(all(d.startswith(b"mon:f:") for d in datas))

    def test_active_type_is_marked(self):
        filt = feed_keyboard(self.ITEMS, FeedFilter(type="g"))[len(self.ITEMS)]
        active = [b.text for b in filt if b.data == feed_filter_cb(FeedFilter(type="g"))][0]
        inactive = [b.text for b in filt if b.data == feed_filter_cb(FeedFilter(type="a"))][0]
        self.assertNotEqual(active, "Розыгрыши")
        self.assertEqual(inactive, "Все")

    def test_legacy_string_filter_is_still_accepted(self):
        rows = feed_keyboard(self.ITEMS, "win")
        self.assertEqual(rows[-1][1].data, feed_filter_cb(FeedFilter(type="w")))

    def test_toolbar_hides_bulk_read_from_a_guest(self):
        guest = [b.data for b in feed_keyboard(self.ITEMS, FeedFilter())[len(self.ITEMS) + 1]]
        owner = [b.data for b in feed_keyboard(self.ITEMS, FeedFilter(), is_admin=True)[len(self.ITEMS) + 1]]
        self.assertNotIn(b"mon:ra:a:a:0:d:0:0:1", guest)
        self.assertIn(b"mon:ra:a:a:0:d:0:0:1", owner)
        self.assertIn(b"mon:q", guest)

    def test_footer_home_and_refresh_keep_the_selection(self):
        state = FeedFilter(type="w", favorite=True)
        footer = feed_keyboard(self.ITEMS, state)[-1]
        self.assertEqual(footer[0].data, b"menu_main")
        self.assertEqual(footer[1].data, feed_filter_cb(state))

    def test_empty_feed_still_has_types_toolbar_and_footer(self):
        self.assertEqual(len(feed_keyboard([], FeedFilter())), 3)

    def test_no_pager_on_single_page(self):
        datas = [b.data for row in feed_keyboard(self.ITEMS, FeedFilter()) for b in row]
        self.assertNotIn(FeedFilter(page=2).cb(), datas)

    def test_pager_next_when_more(self):
        rows = feed_keyboard(self.ITEMS, FeedFilter(), has_more=True)
        datas = [b.data for row in rows for b in row]
        self.assertIn(FeedFilter(page=2).cb(), datas)
        # Telegram has no disabled buttons, so page 1 simply has no "newer" arrow.
        texts = [b.text for row in rows for b in row]
        self.assertFalse(any("Новее" in text for text in texts))

    def test_pager_walks_both_ways_from_page_two(self):
        state = FeedFilter(type="g", page=2)
        datas = [b.data for row in feed_keyboard(self.ITEMS, state, has_more=True) for b in row]
        self.assertIn(state.cb(1), datas)
        self.assertIn(state.cb(3), datas)

    def test_refresh_keeps_page(self):
        state = FeedFilter(page=3)
        self.assertEqual(feed_keyboard(self.ITEMS, state)[-1][1].data, state.cb())


class FeedFiltersKeyboardTests(unittest.TestCase):
    def test_every_type_is_offered(self):
        rows = feed_filters_keyboard(FeedFilter())
        datas = [b.data for row in rows for b in row]
        self.assertTrue(all(any(d.startswith(b"mon:ff:" + code.encode()) for d in datas)
                            for code, _db, _label in FEED_TYPES))

    def test_cycle_buttons_show_the_current_value(self):
        rows = feed_filters_keyboard(FeedFilter(status="n", sort="p"))
        texts = [b.text for row in rows for b in row]
        self.assertTrue(any("Новые" in t for t in texts))
        self.assertTrue(any("Приоритет" in t for t in texts))

    def test_clear_search_appears_only_when_a_query_is_applied(self):
        plain = [b.data for row in feed_filters_keyboard(FeedFilter()) for b in row]
        with_q = [b.data for row in feed_filters_keyboard(FeedFilter(query=True)) for b in row]
        self.assertFalse(any(d.startswith(b"mon:qx") for d in plain))
        self.assertTrue(any(d.startswith(b"mon:qx") for d in with_q))

    def test_export_is_owner_only(self):
        guest = [b.data for row in feed_filters_keyboard(FeedFilter()) for b in row]
        owner = [b.data for row in feed_filters_keyboard(FeedFilter(), is_admin=True) for b in row]
        self.assertFalse(any(d.startswith(b"mon:ex") for d in guest))
        self.assertTrue(any(d.startswith(b"mon:ex:c") for d in owner))

    def test_reset_returns_the_default_selection(self):
        datas = [b.data for row in feed_filters_keyboard(FeedFilter(type="w", favorite=True))
                 for b in row]
        self.assertIn(b"mon:ff:a:a:0:d:0:0:1", datas)

    def test_back_returns_to_the_feed_with_the_same_selection(self):
        state = FeedFilter(type="w", status="n")
        self.assertEqual(feed_filters_keyboard(state)[-1][0].data, state.cb())


class FeedPresetsKeyboardTests(unittest.TestCase):
    def test_presets_are_addressed_by_index(self):
        rows = feed_presets_keyboard(["Победы за неделю", "Гивы"], FeedFilter())
        self.assertEqual(rows[0][0].data, b"mon:ps:0")
        self.assertEqual(rows[0][1].data, b"mon:pd:0")
        self.assertEqual(rows[1][0].data, b"mon:ps:1")

    def test_empty_list_says_so_without_a_dead_button(self):
        rows = feed_presets_keyboard([], FeedFilter())
        self.assertEqual(rows[0][0].data, b"noop")

    def test_saving_carries_the_current_selection(self):
        state = FeedFilter(type="w", favorite=True)
        datas = [b.data for row in feed_presets_keyboard([], state) for b in row]
        self.assertIn(b"mon:pn:w:a:1:d:0:0:1", datas)


class PingCardKeyboardTests(unittest.TestCase):
    def test_admin_gets_fav_and_read(self):
        rows = ping_card_keyboard(842, is_admin=True)
        datas = [b.data for row in rows for b in row]
        self.assertIn(b"ping:fav:842", datas)
        self.assertIn(b"ping:read:842", datas)

    def test_viewer_has_no_mutating_actions(self):
        rows = ping_card_keyboard(842, is_admin=False)
        datas = [b.data for row in rows for b in row]
        self.assertNotIn(b"ping:fav:842", datas)
        self.assertNotIn(b"ping:read:842", datas)

    def test_back_returns_to_feed_and_refresh_reopens(self):
        footer = ping_card_keyboard(842, is_admin=False)[-1]
        self.assertEqual(footer[0].data, FeedFilter().cb())
        self.assertEqual(footer[1].data, b"mon:open:842")

    def test_back_returns_to_the_selection_the_card_was_opened_from(self):
        state = FeedFilter(type="w", favorite=True, page=2)
        footer = ping_card_keyboard(842, is_admin=False, state=state)[-1]
        self.assertEqual(footer[0].data, state.cb())
        self.assertEqual(footer[1].data, b"mon:open:842:w:a:1:d:0:0:2")

    def test_owner_gets_the_full_action_set(self):
        row = {"id": 842, "status": "new", "is_giveaway": 1, "giveaway_status": "pending"}
        datas = [b.data for r in ping_card_keyboard(row, is_admin=True) for b in r]
        self.assertTrue(any(d.startswith(b"pg:st:842") for d in datas))
        self.assertTrue(any(d.startswith(b"pg:nt:842") for d in datas))
        self.assertTrue(any(d.startswith(b"pg:gw:842") for d in datas))
        self.assertTrue(any(d.startswith(b"pg:hs:842") for d in datas))
        self.assertTrue(any(d.startswith(b"pg:tg:842") for d in datas))

    def test_giveaway_actions_are_hidden_for_a_plain_mention(self):
        datas = [b.data for r in ping_card_keyboard({"id": 842}, is_admin=True) for b in r]
        self.assertFalse(any(d.startswith(b"pg:gw:") for d in datas))
        self.assertFalse(any(d.startswith(b"pg:hs:") for d in datas))

    def test_a_real_link_becomes_a_url_button(self):
        rows = ping_card_keyboard({"id": 1, "link": "https://t.me/c/1/2"}, is_admin=False)
        self.assertTrue(any(getattr(b, "url", None) for row in rows for b in row))

    def test_a_placeholder_link_is_not_a_button(self):
        # `link` sometimes holds the reason there is no link ("нет ссылки"), and
        # Telegram rejects a url button whose url is not one.
        rows = ping_card_keyboard({"id": 1, "link": "нет ссылки"}, is_admin=False)
        self.assertFalse(any(getattr(b, "url", None) for row in rows for b in row))

    def test_status_submenu_marks_the_current_value_and_returns(self):
        rows = ping_status_keyboard(842, "important", FeedFilter(type="w"))
        marked = [b.text for row in rows for b in row if b.text.startswith("▸")]
        self.assertEqual(marked, ["▸Важное"])
        self.assertEqual(rows[-1][0].data, b"mon:open:842:w:a:0:d:0:0:1")

    def test_giveaway_submenu_offers_every_outcome(self):
        datas = [b.data for row in ping_giveaway_keyboard(842, "") for b in row]
        self.assertTrue(any(d.startswith(b"pg:gws:842:claimed") for d in datas))
        self.assertTrue(any(d.startswith(b"pg:gws:842:missed_unsubscribe") for d in datas))

    def test_every_ping_callback_fits_the_telegram_limit(self):
        row = {"id": 999999999, "status": "new", "is_giveaway": 1}
        deep = FeedFilter(type="w", status="s", favorite=True, sort="p",
                          ascending=True, query=True, page=9999)
        keyboards = [
            ping_card_keyboard(row, is_admin=True, state=deep),
            ping_status_keyboard(999999999, "new", deep),
            ping_giveaway_keyboard(999999999, "missed_unsubscribe", deep),
            ping_tags_keyboard(999999999, ["розыгрыш", "долг"], deep),
        ]
        for rows in keyboards:
            for r in rows:
                for b in r:
                    if b.data:
                        self.assertLessEqual(len(b.data), 64, b.data)

    def test_tags_are_removed_by_index_and_added_by_prompt(self):
        rows = ping_tags_keyboard(842, ["гив", "долг"])
        self.assertTrue(rows[0][0].data.startswith(b"pg:tgd:842:0"))
        self.assertTrue(rows[1][0].data.startswith(b"pg:tgd:842:1"))
        self.assertTrue(any(b.data.startswith(b"pg:tga:842")
                            for row in rows for b in row))

    def test_empty_tag_list_has_no_dead_button(self):
        self.assertEqual(ping_tags_keyboard(842, [])[0][0].data, b"noop")


from pulse_desk.bot.keyboards import (
    giveaway_accounts_keyboard, giveaway_card_keyboard, giveaway_feed_keyboard,
)
from pulse_desk.bot.views import GiveawayFilter


class GiveawayKeyboardTests(unittest.TestCase):
    def test_items_open_giveaway_carrying_state(self):
        rows = giveaway_feed_keyboard([(50, "⏰ 06-27 @gw")])
        self.assertEqual(rows[0][0].data, b"gw:open:50:d:0:-1:1")

    def test_feed_footer_home_and_refresh(self):
        rows = giveaway_feed_keyboard([])
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"menu_main")
        self.assertEqual(footer[1].data, b"menu_giveaways")

    def test_no_pager_on_single_page(self):
        datas = [b.data for row in giveaway_feed_keyboard([(50, "gw")]) for b in row]
        self.assertNotIn(b"gw:f:d:0:-1:2", datas)

    def test_pager_next_when_more(self):
        rows = giveaway_feed_keyboard([(50, "gw")], page=1, has_more=True)
        datas = [b.data for row in rows for b in row]
        self.assertIn(b"gw:f:d:0:-1:2", datas)
        self.assertNotIn(b"gw:f:d:0:-1:0", datas)  # no prev on page 1

    def test_pager_prev_returns_to_plain_callback_on_page_2(self):
        rows = giveaway_feed_keyboard([(50, "gw")], page=2, has_more=True)
        datas = [b.data for row in rows for b in row]
        self.assertIn(b"menu_giveaways", datas)  # prev → unfiltered page 1 plain form
        self.assertIn(b"gw:f:d:0:-1:3", datas)

    def test_pager_prev_keeps_filter_on_page_2(self):
        state = GiveawayFilter(sort="p", wins=True, account=2, page=2)
        rows = giveaway_feed_keyboard([(50, "gw")], page=2, has_more=True, state=state)
        datas = [b.data for row in rows for b in row]
        self.assertIn(b"gw:f:p:1:2:1", datas)  # filtered page 1 keeps its state
        self.assertNotIn(b"menu_giveaways", datas)

    def test_refresh_keeps_page(self):
        rows = giveaway_feed_keyboard([(50, "gw")], page=3)
        self.assertEqual(rows[-1][1].data, b"gw:f:d:0:-1:3")

    def test_sort_row_marks_active_and_offers_message_date(self):
        rows = giveaway_feed_keyboard([(50, "gw")])
        sort_row = rows[1]
        self.assertTrue(sort_row[0].text.startswith("▸"))
        self.assertEqual([b.data for b in sort_row], [b"gw:f:d:0:-1:1", b"gw:f:p:0:-1:1"])

    def test_sort_switch_resets_to_page_one(self):
        rows = giveaway_feed_keyboard([(50, "gw")], page=4, state=GiveawayFilter(page=4))
        self.assertIn(b"gw:f:p:0:-1:1", [b.data for b in rows[1]])

    def test_wins_toggle_and_account_picker(self):
        rows = giveaway_feed_keyboard([(50, "gw")], accounts=["muver"])
        wins_btn, acct_btn = rows[2]
        self.assertEqual(wins_btn.data, b"gw:f:d:1:-1:1")
        self.assertEqual(acct_btn.data, b"gw:a:d:0:-1:0")
        self.assertIn("Все", acct_btn.text)

    def test_account_picker_callback_keeps_current_selection(self):
        state = GiveawayFilter(sort="p", wins=True, account=2, page=3)
        rows = giveaway_feed_keyboard([(50, "gw")], page=3, state=state, accounts=["a", "b", "c"])
        self.assertEqual(rows[2][1].data, b"gw:a:p:1:2:0")

    def test_selected_account_shown_on_button(self):
        state = GiveawayFilter(account=0)
        rows = giveaway_feed_keyboard([(50, "gw")], state=state, accounts=["muver"])
        self.assertIn("@muver", rows[2][1].text)

    def test_owner_gets_tidy_mode_keeping_the_filter(self):
        state = GiveawayFilter(sort="p", wins=True, account=1, page=2)
        datas = [b.data for row in giveaway_feed_keyboard([(50, "gw")], page=2, state=state, is_admin=True)
                 for b in row]
        self.assertIn(b"gw:x:p:1:1:2", datas)
        guest = [b.data for row in giveaway_feed_keyboard([(50, "gw")], page=2, state=state) for b in row]
        self.assertNotIn(b"gw:x:p:1:1:2", guest)

    def test_no_tidy_mode_for_an_empty_queue(self):
        datas = [b.data for row in giveaway_feed_keyboard([], is_admin=True) for b in row]
        self.assertFalse(any(d.startswith(b"gw:x:") for d in datas))

    def test_tidy_mode_rows_remove_and_pager_stays_in_mode(self):
        state = GiveawayFilter(wins=True, page=2)
        rows = giveaway_feed_keyboard([(50, "🔥 09-16 NK Chat")], page=2, has_more=True,
                                      state=state, is_admin=True, removing=True)
        self.assertEqual(rows[0][0].data, b"gw:rm:50:d:1:-1:2")
        self.assertTrue(rows[0][0].text.startswith("✖ "))
        self.assertEqual([b.data for b in rows[1]], [b"gw:x:d:1:-1:1", b"noop", b"gw:x:d:1:-1:3"])
        # «Готово» leaves the mode on the same filtered page.
        self.assertEqual([(b.text, b.data) for b in rows[-1]], [("✅ Готово", b"gw:f:d:1:-1:2")])
        self.assertEqual(len(rows), 3)  # no filters or footer while tidying

    def test_accounts_picker_rows_select_account(self):
        counts = {"muver": {"wins": 2, "giveaways": 5}}
        rows = giveaway_accounts_keyboard(["muver", "other"], counts, state=GiveawayFilter(wins=True))
        self.assertEqual(rows[0][0].data, b"gw:f:d:1:0:1")
        self.assertIn("🏆 2", rows[0][0].text)
        self.assertEqual(rows[1][0].data, b"gw:f:d:1:1:1")
        self.assertEqual(rows[-2][0].data, b"gw:f:d:1:-1:1")  # все аккаунты

    def test_accounts_picker_pages_beyond_eight(self):
        rows = giveaway_accounts_keyboard([f"a{i}" for i in range(10)], {})
        datas = [b.data for row in rows for b in row]
        self.assertIn(b"gw:a:d:0:-1:1", datas)

    def test_accounts_picker_back_returns_to_the_filtered_feed(self):
        rows = giveaway_accounts_keyboard(["muver"], {}, state=GiveawayFilter(sort="p", account=0))
        self.assertEqual(rows[-1][0].data, b"gw:f:p:0:0:1")

    def test_card_back_to_section_and_refresh(self):
        rows = giveaway_card_keyboard(50)
        footer = rows[-1]
        self.assertEqual(footer[0].data, b"menu_giveaways")
        self.assertEqual(footer[1].data, b"gw:open:50:d:0:-1:1")

    def test_card_back_returns_to_the_filtered_list(self):
        rows = giveaway_card_keyboard(50, GiveawayFilter(sort="p", wins=True, account=1, page=2))
        self.assertEqual(rows[-1][0].data, b"gw:f:p:1:1:2")

    def test_card_has_no_action_buttons(self):
        datas = [b.data for row in giveaway_card_keyboard(50) for b in row]
        self.assertNotIn(b"ping:fav:50", datas)
        self.assertEqual(len(datas), 2)  # read-only: only back + refresh


from pulse_desk.bot.keyboards import (
    keys_keyboard, management_grid, member_access_keyboard,
    member_card_keyboard, members_list_keyboard,
)


class ManagementKeyboardTests(unittest.TestCase):
    def test_hub_has_core_sections(self):
        datas = [b.data for row in management_grid() for b in row]
        for cb in (b"st", b"menu_keys", b"adm:members", b"adm:access",
                   b"menu_scan", b"menu_logs", b"menu_restart", b"menu_main"):
            self.assertIn(cb, datas)

    def test_members_list_rows_open_member(self):
        rows = members_list_keyboard([(7, "🟢 Иван")])
        self.assertEqual(rows[0][0].data, b"mem:open:7")
        self.assertEqual(rows[-1][0].data, b"adm:home")

    def test_member_card_block_toggle(self):
        active = [b.data for row in member_card_keyboard(7, blocked=False) for b in row]
        self.assertIn(b"mem:block:7", active)
        blocked = [b.data for row in member_card_keyboard(7, blocked=True) for b in row]
        self.assertIn(b"mem:unblock:7", blocked)

    def test_member_card_has_access_and_back(self):
        datas = [b.data for row in member_card_keyboard(7, blocked=False) for b in row]
        self.assertIn(b"mem:access:7", datas)
        self.assertIn(b"adm:members", datas)

    def test_member_access_close_open(self):
        datas = [b.data for row in member_access_keyboard(7) for b in row]
        for cb in (b"acc:close:7", b"acc:close2h:7", b"acc:open:7",
                   b"acc:morning:7", b"acc:undo:7", b"acc:log:7",
                   b"mem:open:7", b"mem:access:7"):
            self.assertIn(cb, datas)

    def test_keys_keyboard_create_and_back(self):
        datas = [b.data for row in keys_keyboard() for b in row]
        self.assertIn(b"adm:newkey", datas)
        self.assertIn(b"adm:home", datas)

    def test_keys_keyboard_row_per_key_opens_panel(self):
        rows = keys_keyboard([(3, "🟢 #3 друзья"), (5, "🚫 #5 без метки")])
        datas = [b.data for row in rows for b in row]
        self.assertIn(b"key:3", datas)
        self.assertIn(b"key:5", datas)
        self.assertIn(b"adm:newkey", datas)

    def test_keys_keyboard_caps_the_list(self):
        rows = keys_keyboard([(i, f"#{i}") for i in range(30)])
        opens = [b.data for row in rows for b in row if b.data.startswith(b"key:")]
        self.assertEqual(len(opens), KEYS_LIST_LIMIT)


from pulse_desk.bot.keyboards import (
    KEYS_LIST_LIMIT, key_delete_keyboard, key_expiry_keyboard,
    key_members_keyboard, key_panel_keyboard,
)
from pulse_desk.bot_permissions import full_permissions


class KeyPanelKeyboardTests(unittest.TestCase):
    KEY = {"id": 3, "label": "друзья", "role": "viewer", "revoked": 0,
           "expires_at": None, "member_count": 2}

    def _datas(self, key):
        return [b.data for row in key_panel_keyboard(key, full_permissions(), 4) for b in row]

    def test_panel_exposes_every_section(self):
        datas = self._datas(self.KEY)
        for cb in (b"key:f:3", b"key:n:3", b"key:a:3", b"key:d:3", b"key:e:3",
                   b"key:name:3", b"key:role:3", b"key:link:3", b"key:m:3",
                   b"key:del:3", b"menu_keys"):
            self.assertIn(cb, datas)

    def test_active_key_offers_revoke_revoked_key_offers_restore(self):
        active = self._datas(self.KEY)
        self.assertIn(b"key:rm:3", active)
        self.assertNotIn(b"key:on:3", active)
        revoked = self._datas(dict(self.KEY, revoked=1))
        self.assertIn(b"key:on:3", revoked)
        self.assertNotIn(b"key:rm:3", revoked)

    def test_role_button_flips_label_with_current_role(self):
        viewer = [b.text for row in key_panel_keyboard(self.KEY, full_permissions(), 4) for b in row]
        premium = [b.text for row in key_panel_keyboard(dict(self.KEY, role="premium"), full_permissions(), 4) for b in row]
        self.assertIn("⚡ Сделать премиум", viewer)
        self.assertIn("👁 Сделать обычным", premium)

    def test_expiry_keyboard_has_presets_forever_and_custom(self):
        datas = [b.data for row in key_expiry_keyboard(3, self.KEY) for b in row]
        self.assertIn(b"key:e:3:_off", datas)
        self.assertIn(b"key:e:3:7", datas)
        self.assertIn(b"key:e:3:_x", datas)
        self.assertIn(b"key:3", datas)  # back

    def test_members_keyboard_opens_member_cards(self):
        rows = key_members_keyboard(3, [(7, "🟢 Иван")])
        self.assertEqual(rows[0][0].data, b"mem:open:7")
        self.assertEqual(rows[-1][0].data, b"key:3")

    def test_delete_needs_confirmation(self):
        datas = [b.data for row in key_delete_keyboard(3) for b in row]
        self.assertIn(b"key:delgo:3", datas)
        self.assertIn(b"key:3", datas)  # cancel returns to the panel


from pulse_desk.bot.keyboards import logs_keyboard, restart_confirm_keyboard, scan_panel_keyboard


class ScanPanelKeyboardTests(unittest.TestCase):
    def test_idle_has_start_button(self):
        datas = [b.data for row in scan_panel_keyboard(running=False) for b in row]
        self.assertIn(b"scan:start", datas)
        self.assertIn(b"menu_scan", datas)   # refresh
        self.assertIn(b"adm:home", datas)    # back to hub

    def test_running_hides_start(self):
        datas = [b.data for row in scan_panel_keyboard(running=True) for b in row]
        self.assertNotIn(b"scan:start", datas)
        self.assertIn(b"menu_scan", datas)


class RestartConfirmKeyboardTests(unittest.TestCase):
    def test_confirm_and_cancel(self):
        datas = [b.data for row in restart_confirm_keyboard() for b in row]
        self.assertIn(b"adm:restart:go", datas)
        self.assertIn(b"adm:home", datas)


class LogsKeyboardTests(unittest.TestCase):
    def test_back_and_refresh(self):
        datas = [b.data for row in logs_keyboard() for b in row]
        self.assertIn(b"adm:home", datas)
        self.assertIn(b"menu_logs", datas)


from pulse_desk.bot.keyboards import analytics_keyboard
from pulse_desk.bot.views import ANALYTICS_TABS


class AnalyticsKeyboardTests(unittest.TestCase):
    def test_one_button_per_tab_plus_footer(self):
        datas = [b.data for row in analytics_keyboard("sum") for b in row]
        for code, _ in ANALYTICS_TABS:
            self.assertIn(f"an:{code}".encode(), datas)
        self.assertIn(b"menu_main", datas)

    def test_active_tab_is_marked(self):
        labels = [b.text for row in analytics_keyboard("who") for b in row]
        self.assertIn("▸Люди", labels)
        self.assertIn("Обзор", labels)

    def test_refresh_returns_to_the_active_tab(self):
        rows = analytics_keyboard("time")
        self.assertEqual(rows[-1][-1].data, b"an:time")



if __name__ == "__main__":
    unittest.main()
