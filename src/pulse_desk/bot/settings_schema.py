"""Описание редактируемых полей — данные вместо тринадцати одинаковых веток.

Веб правил тонкие настройки формой с тринадцатью числовыми инпутами. В боте
каждое такое поле — это экран, кнопка, заявка на ввод, разбор текста, проверка
границ и сообщение об ошибке; написанные руками, они разъезжаются между собой
уже на третьем поле.

Поэтому поле описывается один раз: где живёт, как называется, что допустимо и
чем меряется. Меню, валидация, подписи и текст ошибки строятся из описания, и
новая настройка — это одна строчка в таблице.

Модуль чистый: ни базы, ни Telethon. Он только *решает*, что значение годное,
и как его показать; пишет значения :mod:`pulse_desk.bot.persist`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from .. import watch_settings as ws


@dataclass(frozen=True)
class Field:
    """Одна настройка: как её показать, как проверить, куда положить."""

    key: str                        # ключ внутри группы (как в settings KV)
    label: str                      # человеческое имя на кнопке и в карточке
    kind: str                       # int | float | bool | time | choice | text
    group: str = "runtime"          # группа настроек — она же ключ в settings KV
    unit: str = ""                  # «сек», «дн», «%» — печатается рядом с числом
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    presets: tuple[Any, ...] = ()   # быстрые кнопки, чтобы не печатать текст
    choices: tuple[str, ...] = ()   # для kind="choice"
    hint: str = ""                  # что это значит — строкой под полем

    @property
    def code(self) -> str:
        """Короткий адрес поля в callback-данных (64 байта на всё)."""
        return f"{self.group}.{self.key}"

    def format(self, value: Any) -> str:
        """Значение так, как его читают на карточке."""
        if self.kind == "bool":
            return "вкл" if value else "выкл"
        if value in (None, ""):
            return "—"
        if self.kind == "float":
            text = f"{float(value):g}"
        else:
            text = str(value)
        return f"{text} {self.unit}".strip()

    def parse(self, raw: str) -> tuple[Optional[Any], Optional[str]]:
        """(значение, ошибка). Ошибка — готовая фраза для пользователя."""
        text = (raw or "").strip()
        if self.kind in ("int", "float"):
            try:
                value = int(text) if self.kind == "int" else float(text.replace(",", "."))
            except ValueError:
                return None, f"❌ Нужно число{self.range_hint()}."
            if self.minimum is not None and value < self.minimum:
                return None, f"❌ Минимум {self.format(self.minimum)}."
            if self.maximum is not None and value > self.maximum:
                return None, f"❌ Максимум {self.format(self.maximum)}."
            return value, None
        if self.kind == "time":
            from ..bot_prefs import parse_hhmm

            parsed = parse_hhmm(text)
            return (parsed, None) if parsed else (None, "❌ Формат: `09:00`.")
        if self.kind == "choice":
            lowered = text.lower()
            if lowered not in self.choices:
                return None, "❌ Допустимо: " + ", ".join(f"`{c}`" for c in self.choices) + "."
            return lowered, None
        if self.kind == "bool":
            return text.lower() in ("1", "да", "вкл", "true", "on"), None
        return text, None

    def range_hint(self) -> str:
        if self.minimum is None and self.maximum is None:
            return ""
        low = "" if self.minimum is None else f"{self.minimum:g}"
        high = "" if self.maximum is None else f"{self.maximum:g}"
        return f" от {low} до {high}" if low and high else f" не меньше {low}" if low else f" не больше {high}"

    def prompt(self) -> str:
        """Текст заявки на ввод — то, что человек видит перед тем как печатать."""
        parts = [f"Пришлите значение для «{self.label}»{self.range_hint()}"]
        if self.unit:
            parts.append(f"в {self.unit}")
        line = " ".join(parts) + "."
        return f"{line}\n{self.hint}" if self.hint else line


# Ровно те тринадцать полей, что были формой «Работа» в вебе. Границы взяты из
# `watch_settings.sanitize_runtime_settings` — она остаётся последним словом,
# здесь они продублированы только чтобы отказать до записи, с внятной фразой.
RUNTIME_FIELDS: tuple[Field, ...] = (
    Field("scan_interval_seconds", "Интервал скана", "int", unit="сек",
          minimum=60, maximum=86400, presets=(300, 600, 900, 1800),
          hint="Считается от начала цикла: время самого прохода не прибавляется."),
    Field("scan_account_concurrency", "Аккаунтов параллельно", "int",
          minimum=1, maximum=8, presets=(1, 2, 3, 4)),
    Field("scan_history_limit", "Глубина истории", "int", unit="сообщений",
          minimum=0, maximum=100000, presets=(0, 100, 500, 2000),
          hint="`0` — без ограничения."),
    Field("edit_scan_recent_messages", "Окно правок", "int", unit="сообщений",
          minimum=0, maximum=1000, presets=(0, 50, 100, 300),
          hint="Победы чаще приходят правкой поста, а не новым сообщением."),
    Field("startup_scan_delay_seconds", "Пауза перед стартом", "int", unit="сек",
          minimum=0, maximum=300, presets=(0, 15, 30, 60)),
    Field("market_poll_seconds", "Опрос курсов", "int", unit="сек",
          minimum=60, maximum=86400, presets=(300, 600, 1800, 3600)),
    Field("market_alert_change_pct", "Порог алерта", "float", unit="%",
          minimum=0.1, maximum=100.0, presets=(3, 5, 10, 15)),
    Field("market_retention_days", "Хранить историю курсов", "int", unit="дн",
          minimum=1, maximum=365, presets=(7, 14, 30, 90)),
    Field("giveaway_action_account", "Аккаунт для действий", "text",
          hint="Юзернейм без «@»; от его лица бот смотрит розыгрыши."),
    Field("giveaway_review_mode", "Режим проверки", "choice",
          choices=("manual", "assisted", "strict")),
    Field("giveaway_analyze_recent_messages", "Глубина анализа", "int", unit="сообщений",
          minimum=5, maximum=300, presets=(25, 50, 100, 200)),
    Field("giveaway_inactive_channel_days", "Канал считается мёртвым через", "int", unit="дн",
          minimum=1, maximum=365, presets=(7, 14, 30, 60)),
    Field("giveaway_min_action_delay_seconds", "Пауза между действиями", "int", unit="сек",
          minimum=0, maximum=3600, presets=(15, 45, 120, 300)),
)

NOTIFICATION_FIELDS: tuple[Field, ...] = (
    Field("cooldown_seconds", "Кулдаун уведомлений", "int", group="notifications",
          unit="сек", minimum=0, maximum=3600, presets=(0, 30, 120, 600)),
)

ALL_FIELDS: tuple[Field, ...] = RUNTIME_FIELDS + NOTIFICATION_FIELDS
BY_CODE: dict[str, Field] = {f.code: f for f in ALL_FIELDS}


def field_by_code(code: str) -> Optional[Field]:
    return BY_CODE.get(code)


def fields_of(group: str) -> tuple[Field, ...]:
    return tuple(f for f in ALL_FIELDS if f.group == group)


def apply_value(values: dict[str, Any], field: Field, value: Any) -> dict[str, Any]:
    """Копия настроек группы с изменённым полем — исходник не трогаем."""
    updated = dict(values or {})
    updated[field.key] = value
    return updated


def sanitized_runtime(values: dict[str, Any]) -> dict[str, Any]:
    """Пропустить через тот же санитайзер, что и веб: он — источник правды."""
    return ws.sanitize_runtime_settings(values)
