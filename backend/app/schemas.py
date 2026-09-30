"""Pydantic request/response schemas."""
from datetime import datetime, timezone
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    field_validator,
    model_validator,
)

from app.agents.cover_writer import CoverLetterReport
from app.agents.fit_scorer import FitResult
from app.agents.job_parser import ParsedJob
from app.agents.resume_tailor import TailorReport


def _utc(dt: datetime) -> datetime:
    # SQLite returns naive datetimes; they are stored as UTC. Emit "+00:00" so browsers parse them correctly.
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


UtcDatetime = Annotated[datetime, AfterValidator(_utc)]


def _check_password(v: str) -> str:
    # bcrypt only uses the first 72 bytes and bcrypt>=5 rejects longer input.
    if len(v.encode("utf-8")) > 72:
        raise ValueError("Password must be at most 72 bytes")
    if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
        raise ValueError("Password must contain at least one letter and one digit")
    return v


class SignupRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)

    @field_validator("password")
    @classmethod
    def _password_rules(cls, v: str) -> str:
        return _check_password(v)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")
    new_password: str = Field(min_length=8, max_length=128)

    @field_validator("new_password")
    @classmethod
    def _password_rules(cls, v: str) -> str:
        return _check_password(v)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class VerifyOtpRequest(BaseModel):
    email: EmailStr
    code: str = Field(pattern=r"^\d{6}$")


class ResendOtpRequest(BaseModel):
    email: EmailStr


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    email: EmailStr
    is_verified: bool
    created_at: UtcDatetime
    is_admin: bool = False


class MessageOut(BaseModel):
    message: str


class ResumeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    filename: str
    raw_text: str
    uploaded_at: UtcDatetime
    chunks: int | None = None  # only set right after upload


class LlmSlotStatus(BaseModel):
    slot: int
    key: str  # last 4 chars only
    model: str
    state: str  # active | cooling | disabled
    cooldown_remaining_seconds: float
    last_error: str | None


# ---------- job sessions ----------

class SessionCreate(BaseModel):
    """Paste the job text, or give a public job URL and the server fetches it."""

    job_text: str | None = Field(None, min_length=50, max_length=100_000)
    job_url: HttpUrl | None = None

    @model_validator(mode="after")
    def _need_one(self):
        if not self.job_text and not self.job_url:
            raise ValueError("Provide job_text or job_url")
        return self


Status = Literal["draft", "applied", "interview", "rejected", "offer"]


class SessionUpdate(BaseModel):
    """User edits after review. Only the fields sent are changed."""

    parsed_job: ParsedJob | None = None
    tailored_resume: str | None = Field(None, min_length=50, max_length=20_000)
    cover_letter: str | None = Field(None, min_length=50, max_length=10_000)
    status: Status | None = None


class SessionSummary(BaseModel):
    id: int
    title: str | None
    company: str | None
    job_url: str | None
    fit_score: int | None
    current_step: str
    status: str
    created_at: UtcDatetime
    visible_until: UtcDatetime  # when it moves to the archive


class FitRequest(BaseModel):
    # The user's reviewed/edited version of the parsed job. If omitted, the saved one is used.
    parsed_job: ParsedJob | None = None


class TailorRequest(BaseModel):
    instructions: str | None = Field(None, max_length=1000)  # e.g. "emphasise leadership"
    # Re-tailor: the current tailored resume (possibly edited by the user) to revise with `instructions`.
    current_resume: str | None = Field(None, min_length=50, max_length=20_000)


class CoverLetterRequest(BaseModel):
    # The user's reviewed/edited tailored resume. If omitted, the saved one (or the original) is used.
    tailored_resume: str | None = Field(None, min_length=50, max_length=20_000)
    instructions: str | None = Field(None, max_length=1000)  # tone, why this company, etc.


class SessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    job_url: str | None
    job_text: str
    parsed_job: ParsedJob | None
    fit_result: FitResult | None
    tailored_resume: str | None
    tailor_report: TailorReport | None
    cover_letter: str | None
    cover_letter_report: CoverLetterReport | None
    agent_trace: dict | None
    current_step: str
    status: str
    created_at: UtcDatetime
