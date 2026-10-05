"""CUID-compatible id generator.

Prisma generates `@default(cuid())` ids in the client, not in the database, so
rows inserted by this service must supply their own ids. These ids only need to
be collision-free and string-shaped; we emit a cuid-v1-style value ("c" + a
timestamp + counter + fingerprint + randomness in base36) so they blend in with
Prisma-created rows.
"""

from __future__ import annotations

import os
import secrets
import threading
import time

_BLOCK = 4
_counter = secrets.randbelow(36**_BLOCK)
_counter_lock = threading.Lock()
_fingerprint = None


def _to_base36(n: int) -> str:
    if n == 0:
        return "0"
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    out = []
    while n:
        n, r = divmod(n, 36)
        out.append(digits[r])
    return "".join(reversed(out))


def _pad(s: str, size: int) -> str:
    return s[-size:].rjust(size, "0")


def _get_fingerprint() -> str:
    global _fingerprint
    if _fingerprint is None:
        pid = os.getpid() % (36**2)
        host = sum(bytearray(secrets.token_bytes(2))) % (36**2)
        _fingerprint = _pad(_to_base36(pid), 2) + _pad(_to_base36(host), 2)
    return _fingerprint


def _next_counter() -> int:
    global _counter
    with _counter_lock:
        _counter = (_counter + 1) % (36**_BLOCK)
        return _counter


def cuid() -> str:
    timestamp = _pad(_to_base36(int(time.time() * 1000)), 8)
    counter = _pad(_to_base36(_next_counter()), _BLOCK)
    fingerprint = _get_fingerprint()
    random = _pad(_to_base36(secrets.randbelow(36**8)), 8)
    return "c" + timestamp + counter + fingerprint + random
