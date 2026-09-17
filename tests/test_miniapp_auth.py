from __future__ import annotations

import json
import sys
import time
import unittest
from pathlib import Path
from urllib.parse import urlencode

SRC_DIR = Path(__file__).resolve().parents[1] / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.miniapp_auth import InitDataError, data_check_string, sign, verify

TOKEN = "123456:AAHtesttokenvaluethatisnotreal"


def make_init_data(tg_id: int = 42, auth_date: int | None = None, token: str = TOKEN) -> str:
    """Build a correctly signed initData string, the way Telegram would."""
    payload = {
        "auth_date": str(auth_date if auth_date is not None else int(time.time())),
        "query_id": "AAF_test",
        "user": json.dumps(
            {"id": tg_id, "first_name": "Ostap", "username": "bender"},
            separators=(",", ":"),
        ),
    }
    unsigned = urlencode(payload)
    return urlencode({**payload, "hash": sign(unsigned, token)})


class DataCheckStringTests(unittest.TestCase):
    def test_sorted_and_hash_removed(self):
        pairs = [("b", "2"), ("hash", "x"), ("a", "1")]
        self.assertEqual(data_check_string(pairs), "a=1\nb=2")

    def test_empty_pairs(self):
        self.assertEqual(data_check_string([]), "")


class VerifyTests(unittest.TestCase):
    def test_valid_init_data_returns_the_user(self):
        user = verify(make_init_data(), TOKEN)
        self.assertEqual(user.tg_id, 42)
        self.assertEqual(user.username, "bender")
        self.assertEqual(user.first_name, "Ostap")

    def test_tampered_value_is_rejected(self):
        forged = make_init_data().replace("Ostap", "Kisa")
        with self.assertRaises(InitDataError):
            verify(forged, TOKEN)

    def test_another_bot_token_is_rejected(self):
        with self.assertRaises(InitDataError):
            verify(make_init_data(token="999:otherbottoken"), TOKEN)

    def test_missing_hash_is_rejected(self):
        with self.assertRaises(InitDataError):
            verify(urlencode({"auth_date": "1", "user": "{}"}), TOKEN)

    def test_empty_string_is_rejected(self):
        with self.assertRaises(InitDataError):
            verify("", TOKEN)

    def test_stale_auth_date_is_rejected(self):
        old = int(time.time()) - 60 * 60 * 25
        with self.assertRaises(InitDataError):
            verify(make_init_data(auth_date=old), TOKEN)

    def test_fresh_auth_date_at_the_edge_is_accepted(self):
        edge = int(time.time()) - 60 * 60 * 23
        self.assertEqual(verify(make_init_data(auth_date=edge), TOKEN).tg_id, 42)

    def test_zero_auth_date_is_rejected(self):
        with self.assertRaises(InitDataError):
            verify(make_init_data(auth_date=0), TOKEN)

    def test_unparseable_user_json_is_rejected(self):
        payload = {"auth_date": str(int(time.time())), "user": "not-json"}
        raw = urlencode({**payload, "hash": sign(urlencode(payload), TOKEN)})
        with self.assertRaises(InitDataError):
            verify(raw, TOKEN)

    def test_empty_bot_token_is_rejected(self):
        with self.assertRaises(InitDataError):
            verify(make_init_data(), "")

    def test_percent_encoded_values_round_trip(self):
        """The user blob is JSON full of quotes and braces — all percent-encoded."""
        user = verify(make_init_data(tg_id=987654321), TOKEN)
        self.assertEqual(user.tg_id, 987654321)


if __name__ == "__main__":
    unittest.main()
