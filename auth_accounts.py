import asyncio
import os
import sys
from getpass import getpass
from pathlib import Path

from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.errors import (
    FloodWaitError,
    PhoneCodeEmptyError,
    PhoneCodeExpiredError,
    PhoneCodeInvalidError,
    SendCodeUnavailableError,
    SessionPasswordNeededError,
)


BASE_DIR = Path(__file__).resolve().parent
SRC_DIR = BASE_DIR / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from pulse_desk.config import get_settings
from pulse_desk.simple_qr import terminal_qr, write_svg_qr
from pulse_desk.telegram_errors import auth_key_duplicated_message, is_auth_key_duplicated


settings = get_settings()
load_dotenv(BASE_DIR / ".env", override=True, encoding="utf-8-sig")

API_ID = os.getenv("TELEGRAM_API_ID")
API_HASH = os.getenv("TELEGRAM_API_HASH")

SESSIONS = settings.discover_sessions() or ["alga_kazakhst2n", "w3v8f0rm", "Fjfjfjfjds"]


class FinishAuthFlow(Exception):
    pass


def describe_sent_code_type(result):
    sent_type = getattr(result, "type", None)
    type_name = type(sent_type).__name__
    labels = {
        "SentCodeTypeApp": ("app", "Code was sent to an already logged-in Telegram app."),
        "SentCodeTypeSms": ("sms", "Code was sent by SMS."),
        "SentCodeTypeCall": ("call", "Code will arrive by phone call."),
        "SentCodeTypeFlashCall": ("flash_call", "Telegram expects a flash-call confirmation."),
        "SentCodeTypeMissedCall": ("missed_call", "Telegram expects a missed-call confirmation."),
        "SentCodeTypeFragmentSms": ("fragment_sms", "Code was sent through Fragment SMS."),
        "SentCodeTypeEmailCode": ("email", "Code was sent to the account email."),
    }
    return labels.get(
        type_name,
        (type_name or "unknown", "Telegram requested a login code; check Telegram, SMS, calls and email."),
    )


def ask_choice() -> str:
    print("Login method:")
    print("  1 - QR/deep-link login (recommended if codes do not arrive)")
    print("  2 - Telegram app code")
    print("  3 - SMS code")
    print("  4 - skip this account")
    print("  5 - finish auth now")
    return input("Choose 1/2/3/4/5 [1]: ").strip().lower() or "1"


async def finish_with_password_if_needed(client: TelegramClient) -> None:
    password = getpass("2FA password: ")
    await client.sign_in(password=password)


async def login_with_qr(client: TelegramClient, session_name: str) -> bool:
    qr_login = await client.qr_login()
    qr_path = settings.data_dir / f"telegram_login_{session_name}.svg"
    write_svg_qr(qr_login.url, qr_path)
    print("\nQR/deep-link login started.")
    print("Open Telegram on a device where this account is already logged in:")
    print("Settings > Devices > Link Desktop Device, then scan this QR.")
    print()
    print(terminal_qr(qr_login.url))
    print()
    print(f"QR was also saved as a local SVG file: {qr_path}")
    print("If the terminal QR is hard to scan, open that SVG file on the screen and scan it.")
    print("Waiting for confirmation for up to 120 seconds...")
    try:
        await qr_login.wait(timeout=120)
        return True
    except asyncio.TimeoutError:
        print("QR/deep-link login timed out. Run auth again and choose another method if needed.")
        return False
    except SessionPasswordNeededError:
        await finish_with_password_if_needed(client)
        return True
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            print(f"Session IP conflict for {session_name}: {auth_key_duplicated_message(session_name)}")
            return False
        print(f"QR login failed for {session_name}: {exc}")
        return False


async def login_with_code(client: TelegramClient, session_name: str, force_sms: bool = False) -> bool:
    phone = input("Phone number for this account, with country code: ").strip()
    if phone.lower() in {"skip", "s", "4"}:
        print("Skipping this session.")
        return False
    if phone.lower() in {"finish", "quit", "exit", "q", "5"}:
        raise FinishAuthFlow
    if not phone:
        print("Phone is empty; skipping this session.")
        return False
    try:
        result = await client.send_code_request(phone, force_sms=force_sms)
        delivery_type, delivery_message = describe_sent_code_type(result)
        print(f"Delivery: {delivery_type}. {delivery_message}")
        print("Do not press Enter with an empty code; Telegram may treat that as another resend attempt.")
        print("Type 'skip' to skip this account or 'finish' to stop auth.")
        for _ in range(3):
            code = input("Code: ").strip()
            if code.lower() in {"skip", "s", "4"}:
                print("Skipping this session.")
                return False
            if code.lower() in {"finish", "quit", "exit", "q", "5"}:
                raise FinishAuthFlow
            if not code:
                print("Empty code ignored. Wait for the code or restart auth and choose QR/SMS.")
                continue
            try:
                await client.sign_in(phone, code, phone_code_hash=result.phone_code_hash)
                return True
            except SessionPasswordNeededError:
                await finish_with_password_if_needed(client)
                return True
            except PhoneCodeInvalidError:
                print("Invalid code. Check Telegram service chat and enter it again.")
            except PhoneCodeExpiredError:
                print("Code expired. Request a new code later or use QR login.")
                return False
            except PhoneCodeEmptyError:
                print("Empty code rejected.")
        return False
    except FloodWaitError as exc:
        print(f"Telegram rate limited code requests. Wait {exc.seconds} seconds before trying again.")
        return False
    except SendCodeUnavailableError:
        print("Telegram has no resend options left for this number right now. Use QR login or wait before trying again.")
        return False
    except Exception as exc:
        if is_auth_key_duplicated(exc):
            print(f"Session IP conflict for {session_name}: {auth_key_duplicated_message(session_name)}")
            return False
        print(f"Code login failed for {session_name}: {exc}")
        return False


async def ensure_authorized(client: TelegramClient, session_name: str) -> bool:
    await client.connect()
    if await client.is_user_authorized():
        return True
    choice = ask_choice()
    if choice in {"4", "skip", "s"}:
        print("Skipping this session.")
        return False
    if choice in {"5", "finish", "quit", "exit", "q"}:
        raise FinishAuthFlow
    if choice == "2":
        return await login_with_code(client, session_name, force_sms=False)
    if choice == "3":
        return await login_with_code(client, session_name, force_sms=True)
    return await login_with_qr(client, session_name)


async def auth():
    if not API_ID or not API_HASH:
        print("Error: TELEGRAM_API_ID or TELEGRAM_API_HASH is missing in .env")
        return

    print(f"Found sessions: {', '.join(SESSIONS)}")

    for session_name in SESSIONS:
        print(f"\n--- Login account/session: {session_name} ---")
        print("If this session is already logged in, Pulse Desk will only verify it.")

        client = TelegramClient(str(settings.session_path(session_name)), int(API_ID), API_HASH)

        try:
            logged_in = await ensure_authorized(client, session_name)
            if not logged_in:
                continue
            me = await client.get_me()
            if me:
                username = me.username or "no username"
                print(f"Login OK. Account: {me.first_name} (@{username})")
            else:
                print(f"Could not read account info for {session_name}")
        except Exception as exc:
            if isinstance(exc, FinishAuthFlow):
                print("Auth finished by user.")
                break
            if is_auth_key_duplicated(exc):
                print(f"Session IP conflict for {session_name}: {auth_key_duplicated_message(session_name)}")
                print("Fix: stop the app on every other IP, move/delete this broken .session file, then run auth again.")
                print("For another IP: use a different PULSE_SESSION_DIR and different TELEGRAM_SESSIONS names there.")
                continue
            print(f"Error while working with {session_name}: {exc}")
        finally:
            await client.disconnect()

    print("\n" + "=" * 40)
    print("All configured sessions were processed.")
    print("Start Pulse Desk with: .\\.venv\\Scripts\\python.exe main.py")
    print("=" * 40)


if __name__ == "__main__":
    if os.name == "nt":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(auth())
