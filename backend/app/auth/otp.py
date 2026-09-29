"""One-time codes for email verification and password reset.

Rules (ARCHITECTURE.md §2): 6 digits from `secrets`, stored hashed, 10-minute
expiry, single use, locked after 5 wrong attempts, 60s resend cooldown,
max 5 codes per hour per user.
"""
import secrets
from datetime import datetime, timedelta, timezone
from enum import Enum

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth.security import hash_secret, verify_secret
from app.config import get_settings
from app.models import OtpCode, User


class OtpPurpose(str, Enum):
    VERIFY_EMAIL = "verify_email"
    RESET_PASSWORD = "reset_password"


class OtpRateLimited(Exception):
    def __init__(self, retry_after: int):
        self.retry_after = retry_after


class OtpResult(str, Enum):
    OK = "ok"
    INVALID = "invalid"  # wrong code, expired, used, locked or missing — callers show one generic message


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    # SQLite drops tzinfo; treat stored values as UTC.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def issue_otp(db: Session, user: User, purpose: OtpPurpose) -> str:
    """Create a new OTP, invalidating older unused ones. Returns the plain code (to email)."""
    s = get_settings()
    now = _now()

    latest = db.scalar(
        select(OtpCode)
        .where(OtpCode.user_id == user.id, OtpCode.purpose == purpose.value)
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    if latest:
        elapsed = (now - _aware(latest.created_at)).total_seconds()
        if elapsed < s.otp_resend_cooldown_seconds:
            raise OtpRateLimited(int(s.otp_resend_cooldown_seconds - elapsed) + 1)

    sent_last_hour = db.scalar(
        select(func.count(OtpCode.id)).where(
            OtpCode.user_id == user.id,
            OtpCode.purpose == purpose.value,
            OtpCode.created_at > now - timedelta(hours=1),
        )
    )
    if sent_last_hour >= s.otp_max_per_hour:
        raise OtpRateLimited(3600)

    # Only the newest code is ever valid.
    for old in db.scalars(
        select(OtpCode).where(
            OtpCode.user_id == user.id, OtpCode.purpose == purpose.value, OtpCode.used_at.is_(None)
        )
    ):
        old.used_at = now

    code = f"{secrets.randbelow(1_000_000):06d}"
    db.add(
        OtpCode(
            user_id=user.id,
            code_hash=hash_secret(code),
            purpose=purpose.value,
            expires_at=now + timedelta(minutes=s.otp_expire_minutes),
            created_at=now,
        )
    )
    db.commit()
    return code


def check_otp(db: Session, user: User, purpose: OtpPurpose, code: str) -> OtpResult:
    """Validate and consume an OTP."""
    s = get_settings()
    now = _now()
    otp = db.scalar(
        select(OtpCode)
        .where(OtpCode.user_id == user.id, OtpCode.purpose == purpose.value, OtpCode.used_at.is_(None))
        .order_by(OtpCode.created_at.desc())
        .limit(1)
    )
    if otp is None or _aware(otp.expires_at) < now or otp.attempts >= s.otp_max_attempts:
        return OtpResult.INVALID

    if not verify_secret(code, otp.code_hash):
        otp.attempts += 1
        db.commit()
        return OtpResult.INVALID

    otp.used_at = now
    db.commit()
    return OtpResult.OK
