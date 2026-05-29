from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)


def generate_vapid_keys() -> dict[str, str]:
    from py_vapid import Vapid
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    vapid = Vapid()
    vapid.generate_keys()
    pub_bytes = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    import base64
    return {
        "private_key": vapid.private_pem().decode(),
        "public_key": base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode(),
    }


def vapid_public_key_b64(private_pem: str) -> str:
    from py_vapid import Vapid
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    import base64
    vapid = Vapid.from_pem(private_pem.encode())
    pub_bytes = vapid.public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.urlsafe_b64encode(pub_bytes).rstrip(b"=").decode()


async def send_push(
    subscription_info: dict[str, Any],
    payload: dict[str, Any],
    vapid_private_pem: str,
    vapid_claims: dict[str, str],
) -> bool:
    try:
        from pywebpush import webpush
        webpush(
            subscription_info=subscription_info,
            data=json.dumps(payload, ensure_ascii=False),
            vapid_private_key=vapid_private_pem,
            vapid_claims=vapid_claims,
        )
        return True
    except Exception as exc:
        logger.warning("Push send failed: %s", exc)
        return False
