"""Экран, который может быть картинкой.

Здесь проверяется ровно то ограничение Telegram, на котором легко обжечься:
текстовое сообщение нельзя превратить в медийное правкой. Если бы show_screen
просто звал edit, переход «список → картинка дашборда» тихо падал бы, и кнопка
крутила бы спиннер.
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

from pulse_desk.bot.media import (  # noqa: E402
    CAPTION_LIMIT, cache_key, caption_for, fits_caption, render_cached, show_screen,
)

from _fakes import FakeEvent  # noqa: E402


class _Message:
    def __init__(self, media=None):
        self.media = media
        self.text = ""
        self.buttons: list = []


class MediaEvent(FakeEvent):
    """FakeEvent, умеющий сказать, медийное ли сообщение под кнопкой."""

    def __init__(self, *, has_media: bool = False, **kwargs):
        super().__init__(**kwargs)
        self._message = _Message(media=object() if has_media else None)

    async def get_message(self):
        return self._message


class CaptionTests(unittest.TestCase):
    def test_short_text_untouched(self):
        self.assertEqual(caption_for("короткая карточка"), "короткая карточка")
        self.assertTrue(fits_caption("x" * CAPTION_LIMIT))
        self.assertFalse(fits_caption("x" * (CAPTION_LIMIT + 1)))

    def test_long_text_is_cut_at_a_line_break(self):
        head = "строка\n" * 200          # well past the caption limit
        out = caption_for(head)
        self.assertLessEqual(len(out), CAPTION_LIMIT)
        self.assertTrue(out.endswith("…"))
        # cut on a line boundary, so the last visible line is whole
        self.assertTrue(out[:-1].rstrip().endswith("строка"))

    def test_long_unbroken_text_still_fits(self):
        out = caption_for("x" * (CAPTION_LIMIT * 2))
        self.assertLessEqual(len(out), CAPTION_LIMIT)


class ShowScreenTests(unittest.IsolatedAsyncioTestCase):
    async def test_text_screen_edits_in_place(self):
        event = MediaEvent(data=b"menu_main")
        await show_screen(event, "карточка", buttons=[])
        self.assertEqual(len(event.edits), 1)
        self.assertEqual(event.deleted, 0)

    async def test_text_to_image_deletes_and_resends(self):
        event = MediaEvent(data=b"menu_summary")
        await show_screen(event, "дашборд", buttons=[], image="card.png")
        self.assertEqual(event.deleted, 1)
        self.assertEqual(len(event.responses), 1)
        self.assertEqual(event.responses[0]["kwargs"]["file"], "card.png")

    async def test_image_to_image_edits_in_place(self):
        event = MediaEvent(data=b"menu_summary", has_media=True)
        await show_screen(event, "дашборд", buttons=[], image="card.png")
        self.assertEqual(event.deleted, 0)
        self.assertEqual(len(event.edits), 1)
        self.assertEqual(event.edits[0]["kwargs"]["file"], "card.png")

    async def test_image_to_text_deletes_and_resends(self):
        event = MediaEvent(data=b"menu_recent", has_media=True)
        await show_screen(event, "лента", buttons=[])
        self.assertEqual(event.deleted, 1)
        self.assertEqual(len(event.responses), 1)

    async def test_caption_is_trimmed_for_the_image_form(self):
        event = MediaEvent(data=b"menu_summary")
        await show_screen(event, "я" * (CAPTION_LIMIT + 500), image="card.png")
        sent = event.responses[0]["args"][0]
        self.assertLessEqual(len(sent), CAPTION_LIMIT)


class CacheTests(unittest.IsolatedAsyncioTestCase):
    def test_same_data_same_key(self):
        self.assertEqual(cache_key("dash", 5, "x"), cache_key("dash", 5, "x"))
        self.assertNotEqual(cache_key("dash", 5), cache_key("dash", 6))

    async def test_failed_render_returns_none_instead_of_raising(self):
        def explode(path):
            raise RuntimeError("no fonts")

        self.assertIsNone(await render_cached("dash", cache_key("boom"), explode))


if __name__ == "__main__":
    unittest.main()
