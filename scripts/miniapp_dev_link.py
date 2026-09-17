"""Print a local link that opens the panel in a normal browser, signed as the owner.

Outside Telegram the page has no initData, and the server accepts nothing
else — there is deliberately no development bypass on a port that the tunnel
publishes. This script signs initData with the real bot token, exactly as
Telegram would, and puts it in the URL hash where telegram-web-app.js looks.

    .\\.venv\\Scripts\\python.exe scripts\\miniapp_dev_link.py [--screen converter] [--tg-id 123]

The link carries a working credential for ~1 hour of actions (24 h of reading):
keep it on this machine.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote, urlencode

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from pulse_desk.config import Settings  # noqa: E402
from pulse_desk.miniapp_auth import sign  # noqa: E402


def main() -> int:
    settings = Settings()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--tg-id", type=int, default=settings.admin_id)
    parser.add_argument("--screen", default="")
    args = parser.parse_args()
    if not settings.bot_token or not args.tg_id:
        print("TELEGRAM_BOT_TOKEN and ADMIN_ID must be set in .env", file=sys.stderr)
        return 1
    payload = {
        "auth_date": str(int(time.time())),
        "user": json.dumps({"id": args.tg_id, "first_name": "Dev"}, separators=(",", ":")),
    }
    init_data = urlencode({**payload, "hash": sign(urlencode(payload), settings.bot_token)})
    query = f"?s={args.screen}" if args.screen else ""
    print(f"http://127.0.0.1:{settings.miniapp_port}/app{query}#tgWebAppData={quote(init_data)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
