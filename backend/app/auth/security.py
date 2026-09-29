"""Password hashing (bcrypt) and JWT creation/verification."""
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from app.config import get_settings

# A real bcrypt hash used to keep login timing constant when the email doesn't exist.
_DUMMY_HASH = bcrypt.hashpw(b"dummy-password-for-timing", bcrypt.gensalt()).decode()


def hash_secret(plain: str) -> str:
    """Hash a password or OTP with a fresh random salt."""
    return bcrypt.hashpw(plain.encode("utf-8"), bcrypt.gensalt()).decode()


def verify_secret(plain: str, hashed: str | None) -> bool:
    """Constant-time compare. Pass hashed=None to burn equal time for unknown users."""
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), (hashed or _DUMMY_HASH).encode()) and hashed is not None
    except ValueError:  # e.g. input longer than 72 bytes
        return False


def create_access_token(user_id: int, token_version: int = 0) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "ver": token_version, "iat": now,
               "exp": now + timedelta(minutes=s.jwt_expire_minutes)}
    return jwt.encode(payload, s.jwt_secret, algorithm=s.jwt_algorithm)


def decode_access_token(token: str) -> tuple[int, int] | None:
    """Return (user_id, token_version), or None if the token is invalid/expired."""
    s = get_settings()
    try:
        payload = jwt.decode(token, s.jwt_secret, algorithms=[s.jwt_algorithm])
        return int(payload["sub"]), int(payload.get("ver", 0))
    except (jwt.PyJWTError, KeyError, ValueError, TypeError):
        return None
