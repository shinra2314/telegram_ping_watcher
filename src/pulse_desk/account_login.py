"""Вход в Telegram-аккаунт: номер → код → пароль 2FA.

Живёт в панели (Mini App), а не в боте: Telegram аннулирует код подтверждения,
отправленный сообщением в любой Telegram-чат, а поле формы на странице — не
сообщение. Логика — бывший ``routers/auth.py`` веб-дашборда, вынутая из FastAPI,
чтобы её можно было проверить без HTTP и без живого Telegram.

Незавершённый вход держит подключённый клиент в ``state.pending_auths`` (ключ —
номер телефона), пока не истечёт ``PENDING_AUTH_TTL_SECONDS``. Номер, код и
пароль не попадают ни в лог, ни в журнал событий — только имя сессии.
"""
from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Optional

from telethon.errors import (
    FloodWaitError,
    PasswordHashInvalidError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    PhoneNumberInvalidError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)

from .app_ctx import API_HASH, API_ID, logger, state
from .common import now_iso, record_app_event, start_background_task
from .telegram_accounts import (
    describe_sent_code_type,
    disconnect_account,
    is_pending_auth_expired,
    mark_auth_key_duplicated,
    normalize_auth_session_name,
    start_client,
    telegram_client_for_session,
)
from .telegram_errors import auth_key_duplicated_message, is_auth_key_duplicated

# Повторный запрос кода на тот же номер не чаще этого. Telegram сам режет частые
# запросы FloodWait'ом на часы — лучше не доводить.
RESEND_COOLDOWN_SECONDS = 60

_PHONE = re.compile(r"^\+?\d{7,15}$")

# Когда по номеру последний раз просили код: phone -> monotonic seconds.
_last_request: dict[str, float] = {}


@dataclass(frozen=True)
class LoginResult:
    status: str                     # "code_sent" | "password_needed" | "ok" | "error"
    message: str
    session_name: str = ""
    delivery: str = ""
    user: str = ""

    def as_dict(self) -> dict[str, str]:
        return {
            "status": self.status,
            "message": self.message,
            "session_name": self.session_name,
            "delivery": self.delivery,
            "user": self.user,
        }


def clean_phone(raw: Any) -> str:
    """``+380 (67) 123-45-67`` → ``+380671234567``; пусто, если это не номер."""
    text = str(raw or "").strip()
    digits = re.sub(r"[\s()\-.]", "", text)
    if not _PHONE.fullmatch(digits):
        return ""
    return digits if digits.startswith("+") else f"+{digits}"


def clean_code(raw: Any) -> str:
    """Только цифры: код копируют с пробелами и дефисами («12 345»)."""
    return re.sub(r"\D", "", str(raw or ""))


def _error(message: str, session_name: str = "") -> LoginResult:
    return LoginResult("error", message, session_name)


async def _close(auth: Optional[dict[str, Any]]) -> None:
    client = (auth or {}).get("client")
    if client is None:
        return
    try:
        await client.disconnect()
    except Exception:
        logger.debug("Could not close a pending auth client", exc_info=True)


async def sweep_expired() -> int:
    """Закрыть брошенные входы: клиент висит подключённым, пока его не закроют.

    Раньше это случалось только при следующем запросе кода, так что брошенный
    на полпути вход держал соединение с Telegram часами. Теперь зовётся и из
    джоба ``bot-janitor`` раз в минуту. Возвращает, сколько входов закрыто.
    """
    closed = 0
    for phone, auth in list(state.pending_auths.items()):
        if is_pending_auth_expired(auth):
            state.pending_auths.pop(phone, None)
            await _close(auth)
            closed += 1
            session_name = str(auth.get("session_name") or "")
            account = state.accounts_state.get(session_name)
            if account and str(account.get("status") or "").startswith("auth_"):
                account.update({"status": "auth_expired",
                                "last_error": "Вход не завершён вовремя — запросите код заново."})
            await record_app_event("INFO", "auth", "Abandoned Telegram login closed",
                                   {"session_name": session_name})
    return closed


async def request_code(
    phone_raw: Any,
    session_name_raw: Any = "",
    force_sms: bool = False,
    *,
    client_factory: Callable[[str], Any] = telegram_client_for_session,
    clock: Callable[[], float] = time.monotonic,
) -> LoginResult:
    """Шаг 1: запросить код у Telegram и запомнить незавершённый вход."""
    if not API_ID or not API_HASH:
        return _error("В .env нет TELEGRAM_API_ID / TELEGRAM_API_HASH.")
    phone = clean_phone(phone_raw)
    if not phone:
        return _error("Номер в международном формате: +380…, 7–15 цифр.")
    try:
        session_name = normalize_auth_session_name(str(session_name_raw or ""), phone)
    except Exception as exc:  # HTTPException из общего помощника
        return _error(str(getattr(exc, "detail", exc)))
    last = _last_request.get(phone)
    if last is not None and clock() - last < RESEND_COOLDOWN_SECONDS:
        wait = int(RESEND_COOLDOWN_SECONDS - (clock() - last)) + 1
        return _error(f"Код уже запрошен. Повторить можно через {wait} с.", session_name)

    await sweep_expired()
    await _close(state.pending_auths.pop(phone, None))
    await disconnect_account(session_name)
    account = state.accounts_state.setdefault(session_name, {"session_name": session_name})
    account.update({"status": "auth_requesting", "last_error": None, "manual_disconnect": False})

    client = client_factory(session_name)
    try:
        await client.connect()
        sent = await client.send_code_request(phone, force_sms=force_sms)
    except FloodWaitError as exc:
        await _close({"client": client})
        message = f"Telegram ограничил запросы кода. Подождите {exc.seconds} с."
        account.update({"status": "rate_limited", "last_error": message})
        await record_app_event("WARNING", "auth", "Telegram auth code flood wait",
                               {"session_name": session_name, "seconds": exc.seconds})
        return _error(message, session_name)
    except PhoneNumberInvalidError:
        await _close({"client": client})
        account.update({"status": "auth_error", "last_error": "Номер не принят Telegram"})
        return _error("Telegram не принял этот номер.", session_name)
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            await mark_auth_key_duplicated(session_name, client, exc)
            return _error(auth_key_duplicated_message(session_name), session_name)
        await _close({"client": client})
        account.update({"status": "auth_error", "last_error": str(exc)})
        logger.warning("Auth code request failed for %s: %s", session_name, type(exc).__name__)
        return _error(f"Не удалось запросить код: {exc}", session_name)

    _last_request[phone] = clock()
    delivery, delivery_text = describe_sent_code_type(sent)
    state.pending_auths[phone] = {
        "client": client,
        "phone_code_hash": sent.phone_code_hash,
        "session_name": session_name,
        "created_at": datetime.now(),
        "awaiting_password": False,
    }
    account.update({"status": "auth_code_sent", "auth_delivery_type": delivery,
                    "last_error": None, "auth_requested_at": now_iso()})
    await record_app_event("INFO", "auth", "Telegram auth code requested",
                           {"session_name": session_name, "delivery_type": delivery,
                            "force_sms": force_sms})
    return LoginResult("code_sent", delivery_text, session_name, delivery)


async def _pending(phone: str) -> tuple[Optional[dict[str, Any]], Optional[LoginResult]]:
    auth = state.pending_auths.get(phone)
    if not auth:
        return None, _error("Вход не начат или уже завершён. Запросите код заново.")
    if is_pending_auth_expired(auth):
        state.pending_auths.pop(phone, None)
        await _close(auth)
        return None, _error("Время на вход истекло. Запросите код заново.", auth.get("session_name", ""))
    return auth, None


async def submit_code(phone_raw: Any, code_raw: Any, *,
                      launch: Optional[Callable[[str], Any]] = None) -> LoginResult:
    """Шаг 2: код из Telegram. Неверный код оставляет вход открытым."""
    phone = clean_phone(phone_raw)
    code = clean_code(code_raw)
    if not code:
        # Пустой sign_in тратит попытку у Telegram — не отправляем вовсе.
        return _error("Введите код из Telegram.")
    auth, problem = await _pending(phone)
    if problem:
        return problem
    session_name = auth["session_name"]
    client = auth["client"]
    try:
        await client.sign_in(phone, code, phone_code_hash=auth["phone_code_hash"])
    except SessionPasswordNeededError:
        auth["awaiting_password"] = True
        return LoginResult("password_needed", "На аккаунте включён облачный пароль (2FA).", session_name)
    except (PhoneCodeInvalidError, PhoneCodeEmptyError):
        return _error("Код неверный. Проверьте чат «Telegram» и введите заново.", session_name)
    except PhoneCodeExpiredError:
        state.pending_auths.pop(phone, None)
        await _close(auth)
        return _error("Код устарел. Запросите новый.", session_name)
    except SendCodeUnavailableError:
        state.pending_auths.pop(phone, None)
        await _close(auth)
        return _error("Telegram исчерпал способы отправки кода для номера. Попробуйте позже.", session_name)
    except Exception as exc:
        return await _fail(phone, auth, exc)
    return await _finish(phone, auth, launch)


async def submit_password(phone_raw: Any, password: Any, *,
                          launch: Optional[Callable[[str], Any]] = None) -> LoginResult:
    """Шаг 3: облачный пароль. Неверный пароль оставляет вход открытым."""
    phone = clean_phone(phone_raw)
    secret = str(password or "")
    if not secret:
        return _error("Введите пароль 2FA.")
    auth, problem = await _pending(phone)
    if problem:
        return problem
    if not auth.get("awaiting_password"):
        return _error("Сначала введите код из Telegram.", auth["session_name"])
    try:
        await auth["client"].sign_in(password=secret)
    except PasswordHashInvalidError:
        return _error("Пароль неверный.", auth["session_name"])
    except Exception as exc:
        return await _fail(phone, auth, exc)
    return await _finish(phone, auth, launch)


async def cancel(phone_raw: Any) -> LoginResult:
    phone = clean_phone(phone_raw)
    auth = state.pending_auths.pop(phone, None)
    await _close(auth)
    session_name = (auth or {}).get("session_name", "")
    if session_name:
        account = state.accounts_state.get(session_name)
        if account and str(account.get("status", "")).startswith("auth_"):
            account["status"] = "offline"
    return LoginResult("cancelled", "Вход отменён.", session_name)


async def _finish(phone: str, auth: dict[str, Any],
                  launch: Optional[Callable[[str], Any]]) -> LoginResult:
    client = auth["client"]
    session_name = auth["session_name"]
    me = await client.get_me()
    state.pending_auths.pop(phone, None)
    _last_request.pop(phone, None)
    await _close(auth)
    state.accounts_state.setdefault(session_name, {"session_name": session_name}).update({
        "status": "connecting",
        "last_error": None,
        "username": getattr(me, "username", None),
        "user_id": getattr(me, "id", None),
        "manual_disconnect": False,
    })
    if session_name not in state.session_names:
        state.session_names.append(session_name)
    (launch or _launch)(session_name)
    user = f"@{me.username}" if getattr(me, "username", None) else str(getattr(me, "id", ""))
    await record_app_event("INFO", "auth", "Telegram account connected",
                           {"session_name": session_name})
    return LoginResult("ok", f"Подключено: {user}", session_name, user=user)


def _launch(session_name: str) -> None:
    start_background_task(f"telegram-start:{session_name}", start_client(session_name))


async def _fail(phone: str, auth: dict[str, Any], exc: BaseException) -> LoginResult:
    session_name = auth.get("session_name", "")
    state.pending_auths.pop(phone, None)
    if session_name and is_auth_key_duplicated(exc):
        await mark_auth_key_duplicated(session_name, auth.get("client"), exc)
        return _error(auth_key_duplicated_message(session_name), session_name)
    await _close(auth)
    logger.warning("Auth sign-in failed for %s: %s", session_name, type(exc).__name__)
    return _error(f"Вход не удался: {exc}", session_name)
