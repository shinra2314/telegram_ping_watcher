"""Pure composed-card renderers for the bot UI.

Take already-fetched data, return Markdown text. No I/O, no Telethon — so they
unit-test without a running bot. Data fetching lives in service.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional, Sequence

from ..bot_permissions import (
    ALL_FEATURES,
    ALL_NOTIFY,
    FEATURES,
    NOTIFY_TYPES,
    format_delay,
    parse_permissions,
    permission_delay_minutes,
    render_permissions_summary,
)
from .. import salary
from ..roulette import next_fire_at
from .chrome import bar, chip, empty, header, kv
from .views import (
    ANALYTICS_TABS, DIV, MEMBER_ANALYTICS_TABS, GiveawayFilter, fmt_dt, format_expiry,
    key_expired,
)


def home_card(
    *,
    role: str,
    new_pings: int,
    urgent: int,
    accounts_online: int,
    accounts_total: int,
    last_scan: str,
) -> str:
    """Live dashboard shown on the home screen."""
    badge = "👑 владелец" if role == "admin" else "👁 просмотр"
    accounts = f"{accounts_online}/{accounts_total}"
    urgent_cell = f"{kv('🎁', 'К действию', urgent)}{' 🔥' if urgent else ''}"
    return "\n".join([
        header("🛰", "Pulse Desk", badge),
        f"{kv('🆕', 'Новых пингов', new_pings)}   {urgent_cell}",
        f"{kv('🛰', 'Аккаунты', accounts)} {bar(accounts_online, accounts_total)}",
        kv("🔄", "Скан", last_scan),
        DIV,
        "Выберите раздел 👇",
    ])


def member_home_card(report: dict, accounts: Sequence[str] = ()) -> str:
    """Главный экран держателя ключа: его счётчики, без хозяйства владельца.

    Общая карточка печатает очередь действий по всей базе, флот аккаунтов и
    время скана — то есть ровно то, что гостю видеть незачем (правило
    владельца, 18.09).
    """
    summary = report.get("summary") or {}
    names = ", ".join(f"@{name}" for name in accounts) if accounts else "все аккаунты"
    return "\n".join([
        header("🛰", "Pulse Desk", f"👁 {names}"[:60]),
        f"{kv('📨', 'Упоминаний', summary.get('total', 0))}   {kv('🏆', 'Побед', summary.get('wins', 0))}",
        f"{kv('🕐', 'За 24ч', summary.get('last_24h', 0))}   {kv('🎁', 'Розыгрышей', summary.get('giveaways', 0))}",
        DIV,
        "Выберите раздел 👇",
    ])


def summary_card(*, analytics: dict, market: Optional[dict], system: dict) -> str:
    """Combined read-only card: pings stats + market + system status."""
    db_str = f"{system['db_mb']:.1f} MB"
    accounts = f"{system['accounts_online']}/{system['accounts_total']}"
    lines = [
        header("📊", "Сводка"),
        f"{kv('📨', 'Записей', analytics['total_pings'])}   "
        f"{kv('🆕', 'Новых', analytics['new_pings'])}   "
        f"{kv('⭐', 'Избр', analytics['favorites'])}",
        DIV,
        "💹 **Курсы**",
    ]
    if market:
        lines.append(f"🟠 BTC `${market['btc']:,}`   🔷 ETH `${market['eth']:,}`")
        lines.append(f"💎 TON `${market['ton']:.3f}`   🟣 SOL `${market['sol']:.2f}`")
    else:
        lines.append(empty("Курсы пока недоступны."))
    lines += [
        DIV,
        f"🛰 **Система** · `v{system['version']}`",
        f"{kv('⏱', 'Uptime', system['uptime'])}   {kv('💾', 'База', db_str)}",
        f"{kv('🛰', 'Аккаунты', accounts)}   {kv('🔄', 'Скан', system['last_scan'])}",
    ]
    return "\n".join(lines)


_ANALYTICS_TAB_LABELS = dict(ANALYTICS_TABS)
_SPARK_BLOCKS = "▁▂▃▄▅▆▇█"


def _pct(part: float, total: float) -> str:
    return f"{round(100 * part / total)}%" if total else "—"


def spark(values: list[float]) -> str:
    """Inline block-character histogram; flat/empty input renders as a floor."""
    if not values:
        return ""
    peak = max(values)
    if peak <= 0:
        return _SPARK_BLOCKS[0] * len(values)
    return "".join(_SPARK_BLOCKS[min(len(_SPARK_BLOCKS) - 1, int(v / peak * (len(_SPARK_BLOCKS) - 1) + 0.5))] for v in values)


def _rank_lines(rows: list[dict], *, title, value, meta=None, limit: int = 8) -> list[str]:
    """Numbered `n. title  ▰▱ value` block with an optional second meta line."""
    rows = list(rows or [])[:limit]
    if not rows:
        return [empty("Данных пока нет.")]
    peak = max((float(value(r) or 0) for r in rows), default=0)
    out: list[str] = []
    for i, row in enumerate(rows, 1):
        val = float(value(row) or 0)
        name = str(title(row) or "неизвестно")[:28]
        out.append(f"`{i}.` {bar(val, peak, 4)} **{name}** · `{_fmt_num(val)}`")
        if meta:
            hint = meta(row)
            if hint:
                out.append(f"      __{hint}__")
    return out


def _fmt_num(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.1f}"


def latency_lines(latency: dict) -> list[str]:
    """«⏱ Задержка»: how long posts and wins waited before the app noticed them."""
    from ..latency import SLOW_MINUTES, fmt_minutes

    def block(title: str, stats: dict, hint: str) -> list[str]:
        if not stats or not stats.get("count"):
            return [title, empty(hint)]
        return [
            title,
            f"{kv('⏱', 'Медиана', fmt_minutes(stats['median']))}   {kv('📈', '90%', fmt_minutes(stats['p90']))}",
            kv("⚠️", f"Дольше {SLOW_MINUTES} мин", f"{stats.get('slow', 0)} из {stats['count']}"),
        ]

    out = block("📨 **Пост → обнаружение** __(30 дней)__", latency.get("posts") or {},
                "Данных пока нет.")
    out += [DIV]
    out += block("🏆 **Победа (правка поста) → флаг**", latency.get("wins") or {},
                 "Считается для побед, найденных после обновления — накопится со временем.")
    channels = latency.get("channels") or []
    out += [DIV, "🐢 **Самые медленные каналы**"]
    out += [
        f"`{i}.` **{str(row.get('chat') or '?')[:28]}** · `{fmt_minutes(float(row.get('median') or 0))}` · {row.get('count', 0)} зап"
        for i, row in enumerate(channels, 1)
    ] or [empty("Мало данных по каналам.")]
    out.append("\n__Долгие задержки обычно — ночь с выключенным ПК.__")
    return out


def analytics_card(tab: str, *, analytics: dict, detailed: dict) -> str:
    """One page of the analytics report. `tab` is a code from ANALYTICS_TABS."""
    tab = tab if tab in _ANALYTICS_TAB_LABELS else "sum"
    out = [header("📈", "Аналитика", f"Домой › Аналитика › {_ANALYTICS_TAB_LABELS[tab]}")]
    total = int(analytics.get("total_pings") or 0)

    if tab == "sum":
        wins = int(analytics.get("wins") or 0)
        giveaways = int(analytics.get("giveaways") or 0)
        resolved = int(analytics.get("resolved") or 0)
        out += [
            f"{kv('📨', 'Записей', total)}   {kv('🆕', 'Новых', analytics.get('new_pings', 0))}",
            f"{kv('🏆', 'Победы', f'{wins} · {_pct(wins, total)}')}   {kv('🎁', 'Розыгрыши', f'{giveaways} · {_pct(giveaways, total)}')}",
            f"{kv('🔥', 'Важных', analytics.get('important', 0))}   {kv('✅', 'Решено', f'{resolved} · {_pct(resolved, total)}')}",
            f"{kv('🕐', 'За 24ч', analytics.get('last_24h', 0))}   {kv('📅', 'За 7 дней', analytics.get('last_7d', 0))}",
            f"{kv('🎯', 'Ср. приоритет', analytics.get('avg_priority', 0))}   {kv('🔇', 'Шум', analytics.get('noise', 0))}",
            DIV,
            f"📡 **Покрытие** · каналов `{analytics.get('total_channels', 0)}` · аккаунтов онлайн `{analytics.get('accounts_online', 0)}`",
        ]
        accounts = detailed.get("channels_by_account") or []
        out += [
            f"{'🟢' if a.get('status') == 'online' else '🔴'} {str(a.get('display') or a.get('session_name') or '?')[:22]} · `{a.get('channels', 0)}` каналов"
            for a in accounts[:8]
        ] or [empty("Каналы посчитаются после ближайшего скана.")]

    elif tab == "src":
        out += ["💎 **Ценные чаты** __(по среднему приоритету)__"]
        out += _rank_lines(
            detailed.get("chats"),
            title=lambda r: r.get("chat"),
            value=lambda r: r.get("avg_priority") or 0,
            meta=lambda r: f"{r.get('count', 0)} упом · {r.get('wins', 0)} побед · {r.get('giveaways', 0)} розыгр",
            limit=6,
        )
        out += [DIV, "🛰 **Репутация источников**"]
        out += _rank_lines(
            detailed.get("sources"),
            title=lambda r: r.get("chat"),
            value=lambda r: r.get("score") or 0,
            meta=lambda r: f"{r.get('total_pings', 0)} всего · {r.get('wins', 0)} побед · {r.get('noise', 0)} шум",
            limit=6,
        )

    elif tab == "who":
        out += ["✍️ **Авторы** __(кто приносит победы)__"]
        out += _rank_lines(
            detailed.get("senders"),
            title=lambda r: r.get("sender"),
            value=lambda r: r.get("count") or 0,
            meta=lambda r: f"{r.get('wins', 0)} побед · {_pct(int(r.get('wins') or 0), int(r.get('count') or 0))} результативность",
            limit=6,
        )
        out += [DIV, "🏷 **Трекинг юзернеймов**"]
        out += _rank_lines(
            detailed.get("top_mentions"),
            title=lambda r: f"@{r.get('username') or '?'}",
            value=lambda r: r.get("count") or 0,
            limit=8,
        )

    elif tab == "time":
        hourly = analytics.get("hourly") or {}
        hours = [float(hourly.get(f"{h:02d}") or 0) for h in range(24)]
        peak_hour = max(range(24), key=lambda h: hours[h]) if any(hours) else None
        out += [
            "🕓 **Активность по часам**",
            f"`{spark(hours)}`",
            "`00      06      12      18   `",
        ]
        if peak_hour is not None:
            out.append(kv("⏰", "Пик", f"{peak_hour:02d}:00 · {int(hours[peak_hour])} записей"))
        out += [DIV, "📅 **По дням**"]
        out += _rank_lines(
            analytics.get("daily"),
            title=lambda r: r.get("day"),
            value=lambda r: r.get("count") or 0,
            limit=7,
        )

    elif tab == "lat":
        out += latency_lines(detailed.get("latency") or {})

    else:  # flow
        out += ["📈 **Качество по дням**"]
        for row in (detailed.get("daily_quality") or [])[:7]:
            out.append(
                f"`{row.get('day')}` · всего `{row.get('total', 0)}` · 🏆 `{row.get('wins', 0)}` · "
                f"🎁 `{row.get('giveaways', 0)}` · ✅ `{row.get('resolved', 0)}`"
            )
        if not (detailed.get("daily_quality") or []):
            out.append(empty("История ещё не накопилась."))
        out += [DIV, "🎚 **Приоритеты**"]
        out += _rank_lines(
            detailed.get("priorities"),
            title=lambda r: r.get("priority_label") or "normal",
            value=lambda r: r.get("count") or 0,
            limit=5,
        )
        out += [DIV, "🔀 **Статусы обработки**"]
        flow = (detailed.get("status_flow") or [])[:6]
        out += [
            f"• `{r.get('status') or 'unknown'}` → `{r.get('action_status') or 'new'}` · `{r.get('count', 0)}`"
            for r in flow
        ] or [empty("Статусов пока нет.")]

    return "\n".join(out)


def member_summary_card(report: dict, accounts: Sequence[str] = ()) -> str:
    """📊 Сводка держателя ключа: цифры только по его аккаунтам.

    Владельцу этот же экран рисует пульт всей системы — скан, каналы, проблемы
    аккаунтов. Держателю ключа там нечего смотреть и незачем это видеть, поэтому
    у него своя карточка поверх `analytics.build_panel_report`, ровно того же
    отчёта, по которому считает Mini App.
    """
    summary = report.get("summary") or {}
    total = int(summary.get("total") or 0)
    wins = int(summary.get("wins") or 0)
    names = ", ".join(f"@{name}" for name in accounts) if accounts else "все аккаунты"
    out = [
        header("📊", "Сводка", names[:60]),
        f"{kv('📨', 'Упоминаний', total)}   {kv('🏆', 'Победы', f'{wins} · {_pct(wins, total)}')}",
        f"{kv('🎁', 'Розыгрыши', summary.get('giveaways', 0))}   {kv('🕐', 'За 24ч', summary.get('last_24h', 0))}",
        kv("📅", "За 7 дней", summary.get("last_7d", 0)),
    ]
    rows = report.get("accounts") or []
    if rows:
        out += [DIV, "🛰 **По аккаунтам**"]
        out += _rank_lines(
            rows,
            title=lambda r: f"@{r.get('name') or '?'}",
            value=lambda r: r.get("mentions") or 0,
            meta=lambda r: f"{r.get('wins', 0)} побед",
            limit=8,
        )
    chats = report.get("chats") or []
    if chats:
        out += [DIV, f"💬 **Чаты** __(за {int(report.get('window_days') or 30)} дней)__"]
        out += _rank_lines(
            chats,
            title=lambda r: r.get("chat"),
            value=lambda r: r.get("count") or 0,
            meta=lambda r: f"{r.get('wins', 0)} побед · {r.get('giveaways', 0)} розыгр",
            limit=5,
        )
    if total == 0:
        out.append(empty("Упоминаний ваших аккаунтов пока нет."))
    return "\n".join(out)


def member_analytics_card(tab: str, report: dict, accounts: Sequence[str] = ()) -> str:
    """Страница отчёта для держателя ключа — тот же `build_panel_report`."""
    labels = dict(MEMBER_ANALYTICS_TABS)
    tab = tab if tab in labels else "sum"
    names = ", ".join(f"@{name}" for name in accounts) if accounts else "все аккаунты"
    out = [header("📈", "Аналитика", f"{labels[tab]} · {names[:40]}")]
    summary = report.get("summary") or {}
    total = int(summary.get("total") or 0)

    if tab == "sum":
        wins = int(summary.get("wins") or 0)
        giveaways = int(summary.get("giveaways") or 0)
        out += [
            f"{kv('📨', 'Упоминаний', total)}   {kv('🏆', 'Победы', f'{wins} · {_pct(wins, total)}')}",
            f"{kv('🎁', 'Розыгрыши', f'{giveaways} · {_pct(giveaways, total)}')}   {kv('🎯', 'Win rate', f"{summary.get('win_rate', 0)}%")}",
            f"{kv('🕐', 'За 24ч', summary.get('last_24h', 0))}   {kv('📅', 'За 7 дней', summary.get('last_7d', 0))}",
            DIV,
            "📅 **По дням**",
        ]
        out += _rank_lines(
            list(reversed(report.get("daily") or []))[:7],
            title=lambda r: r.get("day"),
            value=lambda r: r.get("total") or 0,
            meta=lambda r: f"{r.get('wins', 0)} побед · {r.get('giveaways', 0)} розыгр",
            limit=7,
        )

    elif tab == "src":
        out += [f"💬 **Чаты** __(за {int(report.get('window_days') or 30)} дней)__"]
        out += _rank_lines(
            report.get("chats"),
            title=lambda r: r.get("chat"),
            value=lambda r: r.get("count") or 0,
            meta=lambda r: f"{r.get('wins', 0)} побед · {r.get('giveaways', 0)} розыгр",
            limit=8,
        )

    elif tab == "who":
        out += ["✍️ **Авторы** __(кто приносит ваши упоминания)__"]
        out += _rank_lines(
            report.get("senders"),
            title=lambda r: r.get("sender"),
            value=lambda r: r.get("count") or 0,
            meta=lambda r: f"{r.get('wins', 0)} побед",
            limit=8,
        )

    elif tab == "time":
        hours = [float(v or 0) for v in (report.get("hours") or [0] * 24)]
        peak_hour = max(range(24), key=lambda h: hours[h]) if any(hours) else None
        out += [
            "🕓 **Активность по часам**",
            f"`{spark(hours)}`",
            "`00      06      12      18   `",
        ]
        if peak_hour is not None:
            out.append(kv("⏰", "Пик", f"{peak_hour:02d}:00 · {int(hours[peak_hour])} записей"))
        else:
            out.append(empty("Данных за период нет."))

    else:  # lat
        out += latency_lines(report.get("latency") or {})

    return "\n".join(out)


_BADGES = {"critical": "🔥", "high": "⚡"}
_PING_TEXT_CAP = 3500


def feed_badge(priority_label: Optional[str]) -> str:
    return _BADGES.get(priority_label or "", "•")


def feed_header(active_label: str, count: int) -> str:
    out = header("📡", "Мониторинг", f"Домой › Мониторинг › {active_label}")
    if count == 0:
        return out + "\n" + empty("Пока ничего.")
    return out + f"\nЗаписей: `{count}` · нажми на строку 👇"


def ping_card(ping: dict) -> str:
    badge = feed_badge(ping.get("priority_label"))
    crumb = f"Домой › Мониторинг › #{ping.get('id')}"
    tags = [chip(f"🏷 {ping.get('priority_label') or 'normal'}")]
    if ping.get("is_giveaway"):
        tags.append(chip("🎁 розыгрыш"))
    if ping.get("is_win"):
        tags.append(chip("🏆 победа"))
    text = (ping.get("text") or "—")[:_PING_TEXT_CAP]
    lines = [
        header(badge, f"Пинг #{ping.get('id')}", crumb),
        f"📅 `{fmt_dt(ping.get('detected_at'))}`  ·  {ping.get('chat') or '?'}",
        " ".join(tags),
        DIV,
        text,
    ]
    if ping.get("link"):
        lines.append(f"🔗 {ping['link']}")
    return "\n".join(lines)


def giveaways_header(
    stats: dict,
    need_count: int,
    state: Optional[GiveawayFilter] = None,
    account: str = "Все",
) -> str:
    out = header("🎁", "Розыгрыши", "Домой › Розыгрыши")
    line1 = f"{kv('🟢', 'К действию', need_count)}   {kv('🏆', 'Призы', stats.get('claim_prize', 0))}"
    line2 = f"{kv('⏳', 'Ждут', stats.get('waiting_result', 0))}   {kv('✅', 'Закрыто', stats.get('done', 0))}"
    body = f"{line1}\n{line2}"
    state = state or GiveawayFilter()
    scope = "🏆 только победы" if state.wins else "🎁 все"
    body += f"\n🔽 __Сортировка: {state.sort_label} · {scope} · 👤 {account}__"
    if need_count == 0:
        hint = "Нет записей под этот фильтр." if (state.wins or account != "Все") else "Срочных нет — всё под контролем."
        body += "\n" + empty(hint)
    return f"{out}\n{body}"


def giveaway_accounts_card(counts: dict, accounts: list[str]) -> str:
    """Picker screen: every tracked account with its open wins / giveaways."""
    out = [header("👤", "По аккаунтам", "Домой › Розыгрыши › Аккаунты")]
    if not accounts:
        return out[0] + "\n" + empty("Отслеживаемых аккаунтов нет.")
    out.append("Выберите аккаунт — покажу его записи 👇")
    out.append(DIV)
    for name in accounts:
        row = counts.get(name.lower()) or {}
        out.append(f"`@{name}` — 🏆 `{int(row.get('wins', 0))}`  ·  🎁 `{int(row.get('giveaways', 0))}`")
    return "\n".join(out)


def giveaway_card(ping: dict) -> str:
    badge = feed_badge(ping.get("priority_label"))
    crumb = f"Домой › Розыгрыши › #{ping.get('id')}"
    lines = [
        header(badge, f"Розыгрыш #{ping.get('id')}", crumb),
        f"📅 `{fmt_dt(ping.get('date'))}`  ·  🕐 `{fmt_dt(ping.get('detected_at'))}`  ·  {ping.get('chat') or '?'}",
    ]
    if ping.get("deleted_at"):
        lines.append("🗑 __Пост удалён из канала__")
    lines += [
        DIV,
        (ping.get("text") or "—")[:_PING_TEXT_CAP],
    ]
    if ping.get("link"):
        lines.append(f"🔗 {ping['link']}")
    return "\n".join(lines)


def management_card() -> str:
    return header("⚙️", "Управление", "Домой › Управление") + "\nВыберите раздел 👇"


def scan_card(status: dict, last_scan: str) -> str:
    """Scan control panel: live progress while running, last result when idle."""
    running = bool(status.get("running"))
    out = [header("🔄", "Скан", "Домой › Управление › Скан")]
    if running:
        done = int(status.get("processed_accounts") or 0)
        total = int(status.get("total_accounts") or 0)
        out.append(f"🟡 **Идёт сканирование** {bar(done, total)} `{done}/{total}`")
        current = status.get("current_channel") or status.get("current_account")
        if current:
            out.append(kv("📡", "Сейчас", current))
        out.append(kv("🆕", "Найдено", status.get("found") or 0))
    else:
        out.append("⚪️ Скан не запущен.")
        out.append(kv("🕐", "Последний", last_scan))
        if status.get("last_error"):
            out.append(kv("⚠️", "Ошибка", status["last_error"]))
    return "\n".join(out)


def restart_confirm_card() -> str:
    return "\n".join([
        header("♻️", "Перезапуск", "Домой › Управление › Рестарт"),
        "Переподключить все аккаунты мониторинга?",
        "__Активный скан будет прерван.__",
    ])


def key_state_badge(key: dict) -> str:
    """Whether the invite still opens: revoked and expired both close it."""
    if key.get("revoked"):
        return "🚫 отозван"
    if key_expired(key.get("expires_at")):
        return "⌛ истёк"
    max_uses = int(key.get("max_uses") or 0)
    if max_uses and int(key.get("member_count") or 0) >= max_uses:
        return "🎟 использован"
    return "🟢 активен"


def keys_card(keys: list[dict]) -> str:
    """Keys panel text; one button per key opens its panel in `keys_keyboard`."""
    out = [header("🔑", "Ключи доступа", "Домой › Управление › Ключи")]
    if not keys:
        out.append(empty("Ключей нет — создайте кнопкой ниже."))
        return "\n".join(out)
    for k in keys:
        badge = "⚡ " if (k.get("role") or "viewer") == "premium" else ""
        perms = parse_permissions(k.get("permissions"))
        accounts = perms.get("accounts") or []
        scope = "все аккаунты" if not accounts else ", ".join(f"@{a}" for a in accounts)
        out.append(
            f"`#{k['id']}` {badge}**{k.get('label') or '—'}** · {key_state_badge(k)}\n"
            f"   👥 {k.get('member_count', 0)} · ⏳ {format_expiry(k.get('expires_at'))}\n"
            f"   📂 {len(perms['features'])}/{len(ALL_FEATURES)} · 🔔 {len(perms['notify'])}/{len(ALL_NOTIFY)} · 👤 {scope}"
        )
    out.append("\n__Нажмите на ключ, чтобы открыть его панель: метка, срок, права, ссылка, удаление.__")
    return "\n".join(out)


def key_panel_card(key: dict, perms: dict, accounts: list[str]) -> str:
    """Root of the key control panel: everything the grant decides, at a glance."""
    badge = "⚡ премиум" if (key.get("role") or "viewer") == "premium" else "👁 просмотр"
    out = [
        header("🔑", f"Ключ #{key.get('id')}", "Домой › Управление › Ключи › Настройка"),
        f"🏷 **{key.get('label') or 'без метки'}** · {badge} · {key_state_badge(key)}",
        f"👥 Вошли: `{key.get('member_count', 0)}` · ⏳ {format_expiry(key.get('expires_at'))}",
        DIV,
        render_permissions_summary(perms),
    ]
    max_uses = int(key.get("max_uses") or 0)
    if max_uses:
        out.insert(3, f"🎟 Активаций: `{min(int(key.get('member_count') or 0), max_uses)}/{max_uses}`")
    if key.get("revoked"):
        out.append("\n🚫 __Ссылка отозвана — новые люди войти не смогут. Можно вернуть кнопкой ♻️.__")
    elif key_expired(key.get("expires_at")):
        out.append("\n⌛ __Срок истёк — ссылка больше не открывает доступ. Продлите его в «Срок».__")
    out.append("\n__Правки действуют на тех, кто войдёт позже; уже вошедшие — в «Люди».__")
    return "\n".join(out)


def key_expiry_card(key: dict) -> str:
    return "\n".join([
        header("⏳", "Срок ключа", "Ключи › Настройка › Срок"),
        f"Сейчас: **{format_expiry(key.get('expires_at'))}**",
        DIV,
        "После этого момента ссылка перестаёт открывать доступ.",
        "Уже вошедшие сохраняют доступ — отключить их можно в «Люди».",
    ])


def key_members_card(key: dict, members: list[dict]) -> str:
    """Who came in through this key — the panel's 👥 screen."""
    out = [header("👥", f"Вошли по ключу #{key.get('id')}", "Ключи › Настройка › Вошли")]
    if not members:
        out.append(empty("По этому ключу ещё никто не вошёл."))
        return "\n".join(out)
    for m in members:
        uname = f"@{m['tg_username']}" if m.get("tg_username") else str(m.get("tg_id"))
        dot = "🚫" if m.get("blocked") else "🟢"
        out.append(f"{dot} **{m.get('name') or uname}** · {uname} · 🕐 {fmt_dt(m.get('joined_at'))}")
    out.append("\n__Нажмите на человека, чтобы открыть его карточку.__")
    return "\n".join(out)


def key_delete_card(key: dict) -> str:
    joined = int(key.get("member_count") or 0)
    out = [
        header("🗑", f"Удалить ключ #{key.get('id')}", "Ключи › Настройка › Удаление"),
        f"🏷 **{key.get('label') or 'без метки'}**",
        DIV,
        "Ключ и его ссылка исчезнут навсегда.",
    ]
    if joined:
        out.append(f"👥 Вошедшие (`{joined}`) сохранят доступ — отключить их можно в «Люди».")
    out.append("\n__Нужно просто закрыть вход — отзовите ключ (🚫), тогда его можно вернуть.__")
    return "\n".join(out)


def key_features_card(perms: dict) -> str:
    granted = set(perms.get("features") or [])
    out = [header("📂", "Разделы", "Ключи › Настройка › Разделы"), "Что держатель ключа может открыть:", ""]
    out += [f"{'✅' if code in granted else '🔒'} {label} — __{hint}__" for code, (label, hint) in FEATURES.items()]
    return "\n".join(out)


def key_notify_card(perms: dict) -> str:
    granted = set(perms.get("notify") or [])
    scoped = bool(perms.get("accounts"))
    out = [header("🔔", "Уведомления", "Ключи › Настройка › Уведомления"), "Что ему будет приходить:", ""]
    out += [f"{'✅' if code in granted else '🔕'} {label} — __{hint}__" for code, (label, hint) in NOTIFY_TYPES.items()]
    if scoped:
        out.append("\n⚠️ __Ключ ограничен аккаунтами — дайджест не приходит: он охватывает все аккаунты сразу.__")
    return "\n".join(out)


def key_accounts_card(perms: dict, accounts: list[str]) -> str:
    granted = perms.get("accounts") or []
    out = [header("👤", "Аккаунты", "Ключи › Настройка › Аккаунты")]
    if not accounts:
        out.append(empty("Отслеживаемых юзернеймов нет — добавьте их в настройках."))
        return "\n".join(out)
    if granted:
        out.append(f"Уведомления только про **{len(granted)}** из {len(accounts)}.")
    else:
        out.append("Выбраны **все** — уведомления про любой отслеживаемый аккаунт.")
    out.append("")
    lowered = {name.lower() for name in granted}
    out += [f"{'✅' if (not granted or name.lower() in lowered) else '⬜'} @{name}" for name in accounts]
    return "\n".join(out)


def key_delay_card(perms: dict) -> str:
    return "\n".join([
        header("⏱", "Задержка отправки", "Ключи › Настройка › Задержка"),
        f"Сейчас: **{format_delay(permission_delay_minutes(perms))}**",
        DIV,
        "Уведомления держателю ключа уходят с этой задержкой.",
        "Вы получаете свои сразу — на владельца задержка не действует.",
        "",
        "__Пока задержка не истекла, кнопка «Скрыть у друзей» отменяет отправку полностью.__",
    ])


def members_header(count: int) -> str:
    out = header("👥", "Люди", "Домой › Управление › Люди")
    if count == 0:
        return out + "\n" + empty("Пока никого.")
    return out + f"\nУчастников: `{count}` · нажми на запись 👇"


def member_card(member: dict, access_open: bool, engagement: dict | None = None) -> str:
    tg = member.get("tg_id")
    uname = f"@{member['tg_username']}" if member.get("tg_username") else "—"
    blocked = bool(member.get("blocked"))
    crumb = f"Домой › Управление › Люди › {member.get('name') or tg}"
    state_line = (
        f"{'🚫 заблокирован' if blocked else '🟢 активен'}  ·  "
        f"⏰ {'🟢 открыт' if access_open else '🔴 закрыт'}"
    )
    role_value = "⚡ премиум" if (member.get("role") or "viewer") == "premium" else "👁 просмотр"
    lines = [
        header("👤", str(member.get("name") or tg), crumb),
        uname,
        kv("🔑", "Ключ", member.get("key_label") or "—"),
        kv("🎫", "Тариф", role_value),
        state_line,
        kv("🕐", "Был", fmt_dt(member.get("last_seen_at"))),
    ]
    if engagement:
        joined = int(engagement.get("joined") or 0)
        skipped = int(engagement.get("skipped") or 0)
        total = joined + skipped
        rate = f" · Claim Rate {round(100 * joined / total)}%" if total else ""
        lines.append(kv("🎯", "Розыгрыши", f"участвует {joined} · пропустил {skipped}{rate}"))
    return "\n".join(lines)


# ---- roulette reminder (owner only) ----------------------------------------

def _click_label(value: object) -> str:
    """ISO click timestamp -> `DD.MM HH:MM`."""
    if not value:
        return "—"
    try:
        return datetime.fromisoformat(str(value)).strftime("%d.%m %H:%M")
    except ValueError:
        return fmt_dt(str(value))


def _next_label(now: datetime, next_at: Optional[datetime]) -> str:
    if next_at is None:
        return "выключено"
    if next_at <= now:
        return "просрочено — придёт сейчас"
    if next_at.date() == now.date():
        return f"сегодня {next_at.strftime('%H:%M')}"
    if next_at.date() == (now + timedelta(days=1)).date():
        return f"завтра {next_at.strftime('%H:%M')}"
    return next_at.strftime("%d.%m %H:%M")


def roulette_card(cfg: dict, now: datetime) -> str:
    """Owner panel for the daily yobo-roulette reminder."""
    lines = [
        header("🎰", "Рулетка йобо", "Домой › Управление › Рулетка"),
        kv("🔔", "Напоминание", "вкл" if cfg.get("enabled") else "выкл"),
        kv("⏰", "Время", cfg.get("time") or "—"),
        kv("✅", "Последний клик", _click_label(cfg.get("last_click"))),
        kv("📅", "Следующее", _next_label(now, next_fire_at(now, cfg))),
    ]
    if cfg.get("awaiting"):
        lines += [DIV, "⏳ __Жду время проклика последнего аккаунта — кнопкой или текстом `21:47`.__"]
    return "\n".join(lines)


def roulette_reminder_card(cfg: dict, now: datetime) -> str:
    """The daily nudge itself."""
    return "\n".join([
        header("🎰", "Рулетка йобо", now.strftime("%d.%m")),
        "Пора прокликать рулетку со **всех** аккаунтов.",
        kv("⏰", "Прошлый клик", _click_label(cfg.get("last_click"))),
        DIV,
        "__Закончишь — жми кнопку или пришли время последнего аккаунта:__ `21:47`",
    ])


def _share(part: int, whole: int) -> str:
    return f"{round(100 * part / whole)}%" if whole else "—"


def member_stats_card(total: dict, month: dict, wins: Optional[dict] = None,
                      accounts: Optional[list[str]] = None) -> str:
    """A member's own numbers: engagement with broadcast giveaways and, for a key
    tied to accounts, how many of those accounts' wins were claimed."""
    lines = [
        header("📊", "Моя статистика"),
        "🎁 **Розыгрыши, которые вам присылали**",
        kv("✅", "Участвую", f"{total.get('joined', 0)} (30 дн: {month.get('joined', 0)})"),
        kv("⏭", "Пропустил", f"{total.get('skipped', 0)} (30 дн: {month.get('skipped', 0)})"),
        kv("🎯", "Участие", _share(int(total.get("joined", 0)),
                                   int(total.get("joined", 0)) + int(total.get("skipped", 0)))),
    ]
    if wins is not None and accounts:
        lines += [
            DIV,
            "🏆 **Победы ваших аккаунтов за 30 дней**",
            "👤 " + ", ".join(f"@{name.lstrip('@')}" for name in accounts),
            kv("🏆", "Побед", wins.get("wins", 0)),
            kv("✅", "Забрано", f"{wins.get('claimed', 0)} · {_share(int(wins.get('claimed', 0)), int(wins.get('wins', 0)))}"),
        ]
    if not total.get("joined") and not total.get("skipped"):
        lines += [DIV, empty("Жмите «✅ Участвую» / «⏭ Пропустил» под розыгрышами — здесь появится статистика.")]
    return "\n".join(lines)


def downtime_catchup_card(gap_from: datetime, gap_to: datetime, pings: list[dict]) -> str:
    """What the first sweep after a long downtime (the nightly shutdown) found.

    ``pings`` are the rows detected since the app came back up; wins and
    giveaways are counted separately, mentions are the rest.
    """
    from ..housekeeping import format_duration

    wins = sum(1 for p in pings if p.get("is_win"))
    giveaways = sum(1 for p in pings if p.get("is_giveaway") and not p.get("is_win"))
    mentions = len(pings) - wins - giveaways
    same_day = gap_from.date() == gap_to.date()
    span = (f"{gap_from:%H:%M}–{gap_to:%H:%M}" if same_day
            else f"{gap_from:%d.%m %H:%M} – {gap_to:%d.%m %H:%M}")
    lines = [
        header("⏳", "Пропуск наверстан", gap_to.strftime("%d.%m")),
        f"Приложение не работало **{span}** ({format_duration(gap_to - gap_from)}).",
        "Первый проход скана после запуска закончен.",
        DIV,
        kv("🏆", "Победы", wins),
        kv("🎁", "Розыгрыши", giveaways),
        kv("📌", "Упоминания", mentions),
    ]
    if not pings:
        lines += [DIV, empty("За время простоя ничего не нашлось.")]
    return "\n".join(lines)


# --- зарплаты из книги «Учет розыгрышей» ---------------------------------
# Эмодзи здесь берутся только из пака Aperture (assets/bot/emoji): 💵 🪙 💎 💸
# 🏆 ⏳ ✅ 📅 🧾 🧮 📈 ⭐ 🎁 ⚠️. Символа 💰 в паке нет — у Premium он выпал бы из
# общего стиля карточки.

def money(value: float) -> str:
    """Суммы книги — всегда в долларах и всегда с двумя знаками."""
    return f"{value:.2f}$"


def _signed(value: float) -> str:
    return f"{value:+.2f}$"


def _day(value) -> str:
    return value.strftime("%d.%m.%Y") if value else "—"


def salary_status_label(row) -> str:
    """Человеческий статус строки месяца — то самое «забрал или нет»."""
    if row is None or row.status == salary.STATUS_NONE:
        return "нет выигрышей"
    if row.status == salary.STATUS_PAID:
        return f"выплачено {_day(row.paid_at)}"
    return "ожидает выплаты"


def salary_status_icon(row) -> str:
    if row is None or row.status == salary.STATUS_NONE:
        return "▫️"
    return "✅" if row.status == salary.STATUS_PAID else "⏳"


def salary_card(data: dict) -> str:
    """Личная карточка: сколько человеку причитается за месяц и забрал ли он."""
    row = data.get("row")
    label = salary.month_label(data["month"])
    lines = [
        header("💵", "Зарплата", f"{data['account']} · {label}"),
        kv("💸", "К выплате", money(row.total if row else 0.0)),
        f"{salary_status_icon(row)} Статус: `{salary_status_label(row)}`",
    ]
    if row is None:
        return "\n".join(lines[:1] + [empty("За этот месяц в книге нет строки.")])
    lines += [
        DIV,
        kv("🪙", "Крипта", f"{money(row.crypto)} → {money(row.pay_money)}"),
        kv("💎", "Скины", f"{money(row.skins)} → {money(row.pay_skins)}"),
        kv("🎰", "йобо", f"{money(row.yobo)} → {money(row.pay_yobo)}"),
        kv("🧮", "Доля", f"{row.share * 100:.0f}%"),
    ]
    # Расходы месяца делятся в той же пропорции, что и приз, поэтому показываем
    # и сам расход, и что из него легло на долю, и итоговую арифметику: вычет,
    # который не видно, — это просто другой процент.
    if row.expenses:
        lines += [
            kv("🧾", "Расходы", f"{money(row.expenses)} → −{money(row.withheld)}"),
            f"`{money(row.gross_pay)}` − `{money(row.withheld)}` = `{money(row.total)}`",
        ]
    lines += [
        DIV,
        kv("🏆", "Место", f"{data['rank']} из {data['of']}"),
        kv("📈", "К прошлому месяцу", _signed(data["delta"])),
    ]
    return "\n".join(lines)


def salary_analytics_card(data: dict) -> str:
    """Разбор месяца: из чего сложилась сумма и что накопилось за всё время."""
    label = salary.month_label(data["month"])
    lines = [
        header("📈", "Аналитика зарплаты", f"{data['account']} · {label}"),
        f"{kv('🎁', 'Выигрышей', data['wins'])}   {kv('💵', 'Выиграно', money(data['won']))}",
        kv("🧮", "Средний приз", money(data["avg"])),
    ]
    best = data.get("best")
    if best is not None:
        title = (best.title or best.kind or "приз")[:40]
        lines.append(kv("⭐", "Лучший", f"{money(best.value)} — {title} ({best.day.strftime('%d.%m')})"))
    by_kind = data.get("by_kind") or {}
    if by_kind:
        top = max(by_kind.values())
        lines.append(DIV)
        for kind, value in sorted(by_kind.items(), key=lambda item: -item[1]):
            lines.append(f"{kind}: `{money(value)}` {bar(value, top)}")
    lines += [
        DIV,
        "🧾 **За всё время**",
        kv("💸", "Начислено", money(data["all_time"])),
        kv("✅", "Получено", money(data["all_time_paid"])),
        kv("⏳", "Ждёт выплаты", money(data["all_time_pending"])),
        kv("🎁", "Выигрышей", data["wins_all_time"]),
    ]
    if data.get("all_time_withheld"):
        lines.append(kv("✂️", "Удержано за расходы", money(data["all_time_withheld"])))
    return "\n".join(lines)


def salary_top_card(rows, month: str, mine: Optional[str] = None) -> str:
    """Открытый топ месяца: имена, суммы, статус. Своя строка выделена."""
    lines = [header("🏆", "Топ по зарплатам", salary.month_label(month))]
    if not rows:
        return "\n".join(lines + [empty("За этот месяц в книге нет строк.")])
    width = max(len(row.account) for row in rows)
    for place, row in enumerate(rows, start=1):
        name = row.account.ljust(width)
        line = f"`{place}.` `{name}` `{money(row.total)}` {salary_status_icon(row)}"
        if mine and row.account.casefold() == mine.casefold():
            line = f"{line} {chip('ты')}"
        lines.append(line)
    lines += [DIV, kv("💸", "Всего к выплате", money(sum(row.total for row in rows)))]
    return "\n".join(lines)


def salary_owner_card(data: dict) -> str:
    """Обзор месяца для владельца: кому сколько и что ещё не отдано."""
    lines = [
        header("💵", "Зарплаты", f"{salary.month_label(data['month'])} · владелец"),
        kv("💸", "К выплате", money(data["payout"])),
        f"{kv('✅', 'Выплачено', money(data['paid']))}   {kv('⏳', 'Ждут', money(data['pending']))}",
        DIV,
        f"{kv('🎁', 'Выиграно', money(data['won']))}   {kv('🧾', 'Организатору', money(data['profit']))}",
    ]
    # «Организатору» уже за вычетом расходов, поэтому сами расходы показываем
    # рядом — иначе цифра выглядит просевшей без причины.
    if data.get("expenses"):
        lines.append(
            f"{kv('💳', 'Расходы', money(data['expenses']))}   "
            f"{kv('✂️', 'Удержано с долей', money(data['withheld']))}"
        )
    rows = data.get("rows") or []
    if rows:
        width = max(len(row.account) for row in rows)
        lines.append(DIV)
        for place, row in enumerate(rows, start=1):
            lines.append(
                f"`{place}.` `{row.account.ljust(width)}` `{money(row.total)}` {salary_status_icon(row)}"
            )
    else:
        lines += [DIV, empty("За этот месяц в книге нет строк.")]
    for issue in data.get("issues") or []:
        lines.append(f"⚠️ __{issue}__")
    return "\n".join(lines)


def salary_paid_notice(row) -> str:
    """Разовое уведомление: владелец проставил дату выплаты в книге."""
    return "\n".join([
        header("✅", "Зарплата выплачена", salary.month_label(row.month)),
        kv("💸", "Сумма", money(row.total)),
        kv("📅", "Дата выплаты", _day(row.paid_at)),
    ])


def salary_off_card() -> str:
    """Книга не прочитана — раздел жив, но показывать нечего."""
    return "\n".join([
        header("💵", "Зарплата"),
        empty("Книга учёта сейчас недоступна — попробуйте позже."),
    ])
