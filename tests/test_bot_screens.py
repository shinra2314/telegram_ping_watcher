"""Нарисованные экраны: формат чисел, порядок снимков и контракт «не бросать».

Два обещания здесь стоят отдельного теста.

Первое — порядок: ``get_market_history`` отдаёт **новейшее первым**, поэтому
«сейчас» это ``snapshots[0]``. Копия хитмапа, написанная от обратного, меняла
знак у каждого движения: рост печатался падением. Тест ловит именно это.

Второе — экран, который не нарисовался, обязан вернуть ``None``, а не уронить
раздел. Рисование трогает шрифты и файловую систему, и любая из этих причин не
должна мешать человеку открыть карточку.
"""
from __future__ import annotations

import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_DIR = ROOT / "src"
for path in (str(ROOT), str(SRC_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from pulse_desk.bot.render import canvas, screens  # noqa: E402


def snapshot(btc: float, ton: float) -> dict:
    return {
        "bitcoin": {"usd": btc, "usd_market_cap": 1_500_000_000_000},
        "the-open-network": {"usd": ton, "usd_market_cap": 12_000_000_000},
    }


class MarketOrderTests(unittest.TestCase):
    # newest first, exactly as the query returns them
    RISING = [snapshot(110.0, 2.0), snapshot(100.0, 1.0)]

    def test_newest_snapshot_is_the_current_price(self):
        tiles = {t["ticker"]: t for t in screens.market_tiles(self.RISING)}
        self.assertEqual(tiles["BTC"]["price"], 110.0)

    def test_a_rise_is_reported_as_a_rise(self):
        tiles = {t["ticker"]: t for t in screens.market_tiles(self.RISING)}
        self.assertGreater(tiles["BTC"]["pct"], 0, "snapshots[0] must be 'now'")
        self.assertGreater(tiles["TON"]["pct"], 0)

    def test_a_fall_is_reported_as_a_fall(self):
        falling = list(reversed(self.RISING))
        tiles = {t["ticker"]: t for t in screens.market_tiles(falling)}
        self.assertLess(tiles["BTC"]["pct"], 0)

    def test_no_snapshots_means_no_tiles(self):
        self.assertEqual(screens.market_tiles([]), [])

    def test_weights_keep_the_small_coin_visible(self):
        tiles = screens.market_tiles(self.RISING)
        weights = [t["weight"] for t in tiles]
        # square-rooted and floored, so the leader is at most 15x the smallest
        self.assertLessEqual(max(weights) / min(weights), 15.0001)


class NumberFormatTests(unittest.TestCase):
    def test_thousands_are_grouped(self):
        self.assertEqual(screens.group(128456), "128 456")
        self.assertEqual(screens.group(7), "7")

    def test_group_survives_garbage(self):
        self.assertEqual(screens.group(None), "None")

    def test_flat_move_prints_without_a_sign(self):
        # "-0.0%" reads as a fall that did not happen.
        self.assertEqual(screens.pct(-0.004), "0.0%")
        self.assertEqual(screens.pct(0.0), "0.0%")

    def test_real_moves_keep_their_sign(self):
        self.assertEqual(screens.pct(4.51), "+4.5%")
        self.assertEqual(screens.pct(-1.44), "-1.4%")


class DailySeriesTests(unittest.TestCase):
    def test_reads_dict_rows(self):
        self.assertEqual(screens._daily_series({"daily": [{"count": 3}, {"count": 5}]}), [3.0, 5.0])

    def test_reads_plain_numbers(self):
        self.assertEqual(screens._daily_series({"daily": [1, 2]}), [1.0, 2.0])

    def test_takes_only_the_tail(self):
        self.assertEqual(len(screens._daily_series({"daily": list(range(90))}, days=14)), 14)

    def test_missing_data_is_an_empty_series(self):
        self.assertEqual(screens._daily_series({}), [])


class NeverRaisesTests(unittest.TestCase):
    def test_dashboard_card_returns_none_when_it_cannot_draw(self):
        # An unwritable path is the cheapest way to fail inside the render.
        bad = Path("nul://impossible/dash.png")
        self.assertIsNone(screens.build_dashboard_card({}, {}, bad))

    def test_market_card_returns_none_when_it_cannot_draw(self):
        bad = Path("nul://impossible/market.png")
        self.assertIsNone(screens.build_market_card([snapshot(1.0, 1.0)], bad))


class PrimitiveShadowingTests(unittest.TestCase):
    """Имя примитива, занятое переменной, — молчаливая поломка рендера.

    `for tile, rect in ...` внутри модуля, где `tile` это функция рисования,
    роняет карточку на первом же вызове, а контракт «не бросать» превращает это
    в «экран просто текстовый». Дважды так и случилось, поэтому проверяем.
    """

    PRIMITIVES = {name for name in dir(canvas) if callable(getattr(canvas, name))
                  and not name.startswith("_")}

    def _assignments(self, tree: ast.AST) -> set[str]:
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Assign):
                names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.For):
                target = node.target
                parts = target.elts if isinstance(target, ast.Tuple) else [target]
                names |= {p.id for p in parts if isinstance(p, ast.Name)}
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                names |= {a.arg for a in node.args.args}
        return names

    def test_no_render_module_shadows_a_drawing_primitive_it_calls(self):
        for module_path in (Path(screens.__file__), Path(canvas.__file__)):
            tree = ast.parse(module_path.read_text(encoding="utf-8"))
            for fn in [n for n in ast.walk(tree)
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]:
                called = {n.func.id for n in ast.walk(fn)
                          if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
                clash = self._assignments(fn) & called & self.PRIMITIVES
                self.assertEqual(clash, set(),
                                 f"{module_path.name}:{fn.name} shadows {clash}")


if __name__ == "__main__":
    unittest.main()
