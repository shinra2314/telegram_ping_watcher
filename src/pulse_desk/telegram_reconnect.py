from __future__ import annotations

import hashlib


def reconnect_delay_seconds(
    session_name: str,
    attempt: int,
    *,
    base_seconds: int,
    max_seconds: int,
    jitter_seconds: int,
) -> int:
    capped_attempt = max(1, min(attempt, 8))
    exponential = base_seconds * (2 ** (capped_attempt - 1))
    delay = min(exponential, max_seconds)
    if jitter_seconds > 0:
        seed = int(hashlib.sha1(f"{session_name}:{attempt}".encode("utf-8")).hexdigest()[:8], 16)
        delay += seed % (jitter_seconds + 1)
    return int(min(delay, max_seconds + max(0, jitter_seconds)))
