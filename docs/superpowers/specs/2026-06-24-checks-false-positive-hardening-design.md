# Чеки: жёстче против ложных срабатываний

**Дата:** 2026-06-24
**Статус:** утверждён

## Проблема

`is_check_text` (`giveaways.py`) помечает сообщение чеком по одному
существительному «чек/мультичек». Этого мало: ловятся не-чеки —
«кассовый чек», «скинь чек оплаты», «два чека для вас». Плюс реальные чеки,
адресованные другому юзеру («для @IvanLydhii777»), и уже забранные
(«✅ Получено») не должны попадать в выдачу для владельца.

Примеры реальных чеков (для калибровки, должны остаться True):
- `@send`: «Чек на 1.413019 USDT (105 RUB).»
- `@xrocket`: «Чек на 5 USDT (5.0$) для @IvanLydhii777», кнопка «Получить 5 USDT».
- CryptoBot-ссылка: «💸 Чек: t.me/CryptoBot?start=CQ».
- Мультичек: «раздаю мультичек на 20 человек».

## Требования

- F1. «чек» без сигнала ценности — **не чек**. Реальный крипто-чек содержит
  хотя бы один сильный сигнал:
  - сумма + валюта (`5 USDT`, `105 RUB`, `5 TON`, `$5`, `грн`, `₽`, `₴`, …); ИЛИ
  - кошелёк-сигнал: `t.me/CryptoBot|send|xrocket|wallet|tonkeeper`,
    `@cryptobot/@xrocket/@send/@wallet/@tonkeeper`, `?start=`; ИЛИ
  - слово «мультичек» (однозначно крипто).
- F2. Чек, адресованный **другому** (`для @username`, username ∉
  `state.ping_usernames`), — игнор. Если список владельца пуст — фильтр **не**
  применяется (не теряем чеки вслепую).
- F3. Уже забранный/мёртвый чек — игнор. Маркеры: `получено/получен(а|ы)`
  (но не призыв «Получить»), «активаций больше нет», «чек недействителен»,
  «истёк».
- F4. Глагол «чекать/чекни/чекаю» — уже не чек (R5, regex именных окончаний).
  Сохранить; добавить регресс-тесты.

## Дизайн

### Чистый слой — `giveaways.py`

Существующий лексический матчер существительного (`_check_regex`, R5-safe)
остаётся как первый гейт. Поверх — сигналы.

Новые модульные регексы:

```python
# F1: сумма + крипто/фиат валюта рядом с числом
CHECK_AMOUNT_RE = re.compile(
    r"(?:\d+(?:[.,]\d+)?\s*(?:usdt|usdc|usd|ton|trx|btc|eth|ltc|bnb|sol|"
    r"not|dogs|rub|руб|грн|uah|eur|\$|₽|₴|€))"
    r"|(?:(?:\$|usd|usdt|₽|₴|€)\s*\d+(?:[.,]\d+)?)",
    re.IGNORECASE,
)
# F1: ссылка/упоминание кошелёк-бота или redeem-параметр
CHECK_WALLET_RE = re.compile(
    r"(?:t\.me/(?:cryptobot|send|xrocket|wallet|tonkeeper))"
    r"|(?:@(?:cryptobot|xrocket|send|wallet|tonkeeper))"
    r"|(?:\bcryptobot\b|\bxrocket\b)"
    r"|(?:\?start=)",
    re.IGNORECASE,
)
# F3: маркеры уже забранного/мёртвого
CHECK_CLAIMED_RE = re.compile(
    r"получен(?:о|а|ы)?\b|активаций\s+больше\s+нет|"
    r"чек\s+недействител|истёк|истек\b|разобран",
    re.IGNORECASE,
)
# F2: адресность «для @username»
CHECK_FOR_OTHER_RE = re.compile(r"(?:для|for)\s+@([A-Za-z0-9_]{4,32})", re.IGNORECASE)
```

`is_check_text(text, keywords)` — новая логика (F1 + F3):

```python
def is_check_text(text, keywords):
    if not text:
        return False
    regex = _check_regex(keywords)
    if not (regex and regex.search(text)):   # F4/лексика: существительное «чек»
        return False
    if CHECK_CLAIMED_RE.search(text):         # F3: уже забран
        return False
    if "мультичек" in text.lower():           # F1: мультичек — сильный сам по себе
        return True
    return bool(CHECK_AMOUNT_RE.search(text) or CHECK_WALLET_RE.search(text))  # F1
```

Новая чистая функция (F2):

```python
def check_addressed_to_other(text, owner_usernames):
    """True, если чек явно адресован НЕ владельцу."""
    owners = {u.lstrip("@").lower() for u in (owner_usernames or []) if u}
    if not owners:
        return False  # владелец неизвестен — не фильтруем
    return any(
        m.group(1).lower() not in owners
        for m in CHECK_FOR_OTHER_RE.finditer(text or "")
    )
```

### Интеграция — `ping_pipeline.py`

```python
def check_is_check(text):
    return is_check_text(text, state.check_keywords) and not check_addressed_to_other(
        text, state.ping_usernames
    )
```

(`check_addressed_to_other` импортируется из `giveaways`.)

## Поток данных

```
скан → process_ping_message → check_is_check(raw_text)
   = is_check_text (F1 существительное+сигнал, F3 не забран)
     AND not check_addressed_to_other (F2 не «для @чужого»)
```

## Тестирование (`tests/test_checks.py`)

True (реальные):
- «чек на 5 TON, забирай»; «💸 Чек: t.me/CryptoBot?start=CQ»;
  «раздаю мультичек на 20 человек»; «ещё мультичеки»;
  «Чек на 1.413019 USDT (105 RUB).»; «Чек на 5 USDT (5.0$)» (без «для»).

False (ложные, F1):
- «кассовый чек»; «скинь чек оплаты на карту»; «два чека для вас»;
  «пачка чеков»; «приз в чеке»; «обычное сообщение».

False (F3 забран): «Чек на 5 USDT — получено»; «чек на 5 TON, активаций больше нет».

F2 `check_addressed_to_other`:
- «Чек на 5 USDT для @IvanLydhii777», owners=`{"myhandle"}` → True (чужой).
- тот же текст, owners=`{"ivanlydhii777"}` → False (мой).
- owners=`[]` → False (не фильтруем).

F4 (регресс): «го чекать профиль», «чекни личку», «зачекать» → False.

## Вне рамок

- Live-проверка «забран ли чек» дёрганьем ссылки — не делаем (как в прошлом спеке).
- Авто-redeem — не делаем.
- Обновление существующих тестов, кодирующих «голое существительное = чек»
  («два чека для вас», «пачка чеков», «приз в чеке» → теперь False) — это
  намеренное ужесточение, тесты приводятся к новому поведению.
