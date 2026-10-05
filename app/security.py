"""Password hashing and opaque-token helpers.

Mirrors src/lib/auth/crypto.ts in the web app so credentials and session tokens
are byte-for-byte compatible:
  * passwords: bcrypt, cost 12 (verifies the existing bcryptjs $2a$/$2b$ hashes)
  * tokens: random 32 bytes, base64url; stored only as a SHA-256 hex digest
"""

from __future__ import annotations

import hashlib
import secrets

import bcrypt

_BCRYPT_ROUNDS = 12
# bcrypt silently truncates at 72 bytes; the web app enforces the same limit.
_BCRYPT_MAX_BYTES = 72


def hash_password(password: str) -> str:
    pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
    return bcrypt.hashpw(pw, bcrypt.gensalt(_BCRYPT_ROUNDS)).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        pw = password.encode("utf-8")[:_BCRYPT_MAX_BYTES]
        return bcrypt.checkpw(pw, password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


# A fixed hash used to keep login timing similar whether or not the account
# exists (matches burnPasswordCheck in the web app).
_DUMMY_HASH = "$2b$12$C6UzMDM.H6dfI/f/IKcEeO5m5jXQ5bGvO6v6qbbQe4rS3wK5c4F2e"


def burn_password_check(password: str) -> None:
    verify_password(password, _DUMMY_HASH)


def generate_token(nbytes: int = 32) -> str:
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
