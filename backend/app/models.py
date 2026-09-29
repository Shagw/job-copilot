"""ORM models. See ARCHITECTURE.md §6."""
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def as_utc(dt: datetime) -> datetime:
    """SQLite drops tzinfo on read; stored values are always UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(100))
    is_verified: Mapped[bool] = mapped_column(Boolean, default=False)
    # Bumped on password reset; JWTs carrying an older version are rejected (logs out every device).
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    otp_codes: Mapped[list["OtpCode"]] = relationship(back_populates="user")


class OtpCode(Base):
    __tablename__ = "otp_codes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    code_hash: Mapped[str] = mapped_column(String(100))
    purpose: Mapped[str] = mapped_column(String(32))  # verify_email | reset_password
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    user: Mapped[User] = relationship(back_populates="otp_codes")


class Resume(Base):
    __tablename__ = "resumes"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    raw_text: Mapped[str] = mapped_column(Text)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class JobSession(Base):
    """One history entry = one job the user worked on."""

    __tablename__ = "job_sessions"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    job_url: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    job_text: Mapped[str] = mapped_column(Text)
    parsed_job: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    fit_result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    tailored_resume: Mapped[str | None] = mapped_column(Text, nullable=True)
    tailor_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    cover_letter: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_letter_report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    agent_trace: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    current_step: Mapped[str] = mapped_column(String(20), default="created")
    status: Mapped[str] = mapped_column(String(20), default="draft")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
