"""Auth endpoints: signup + email OTP verification, login/logout, forgot/reset password."""
import logging

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.auth.otp import OtpPurpose, OtpRateLimited, OtpResult, check_otp, issue_otp
from app.auth.rate_limit import limit_login, limit_otp
from app.auth.security import create_access_token, hash_secret, verify_secret
from app.config import get_settings
from app.database import get_db
from app.models import User
from app.schemas import (
    ForgotPasswordRequest,
    LoginRequest,
    MessageOut,
    ResendOtpRequest,
    ResetPasswordRequest,
    SignupRequest,
    UserOut,
    VerifyOtpRequest,
)
from app.services.archive import archive_expired
from app.services.email import send_otp_email

log = logging.getLogger("app.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

INVALID_CODE = "Invalid or expired code"


def _normalize(email: str) -> str:
    return email.strip().lower()


def _set_auth_cookie(response: Response, user: User) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.cookie_name,
        value=create_access_token(user.id, user.token_version),
        max_age=s.jwt_expire_minutes * 60,
        httponly=True,  # JS can't read it (XSS protection)
        secure=s.is_production,  # HTTPS-only in production
        samesite="lax",  # not sent on cross-site POSTs (CSRF protection)
        path="/",
    )


def _send_code(db: Session, user: User, purpose: OtpPurpose, *, quiet: bool = False) -> None:
    """Issue + email a code. `quiet=True` swallows send failures, for endpoints whose response
    must not reveal whether an account exists."""
    code = issue_otp(db, user, purpose)  # may raise OtpRateLimited
    try:
        send_otp_email(user.email, code, purpose.value)
    except Exception:
        log.exception("Failed to send %s email to user_id=%s", purpose.value, user.id)
        if not quiet:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="Could not send email. Try again shortly.")


def _on_login(db: Session, response: Response, user: User) -> None:
    archive_expired(db, user.id)  # record which history entries passed the 3-day window
    _set_auth_cookie(response, user)


@router.post("/signup", response_model=MessageOut, status_code=201, dependencies=[Depends(limit_otp)])
def signup(body: SignupRequest, db: Session = Depends(get_db)):
    email = _normalize(body.email)
    user = db.scalar(select(User).where(User.email == email))

    if user and user.is_verified:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")

    if user is None:
        user = User(email=email, password_hash=hash_secret(body.password))
        db.add(user)
    else:
        # Unverified account: whoever proves ownership of the inbox via OTP owns it.
        user.password_hash = hash_secret(body.password)
    db.commit()

    try:
        _send_code(db, user, OtpPurpose.VERIFY_EMAIL)
    except OtpRateLimited as e:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"Please wait {e.retry_after}s before requesting another code",
            headers={"Retry-After": str(e.retry_after)},
        )
    return MessageOut(message="Account created. Check your email for a 6-digit code.")


@router.post("/verify-otp", response_model=UserOut, dependencies=[Depends(limit_otp)])
def verify_otp(body: VerifyOtpRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == _normalize(body.email)))
    if user is None or user.is_verified:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=INVALID_CODE)

    if check_otp(db, user, OtpPurpose.VERIFY_EMAIL, body.code) is not OtpResult.OK:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=INVALID_CODE)

    user.is_verified = True
    db.commit()
    _on_login(db, response, user)
    return user


@router.post("/resend-otp", response_model=MessageOut, dependencies=[Depends(limit_otp)])
def resend_otp(body: ResendOtpRequest, db: Session = Depends(get_db)):
    # Same response whether or not the email exists, so accounts can't be discovered.
    generic = MessageOut(message="If this account needs verification, a new code has been sent.")
    user = db.scalar(select(User).where(User.email == _normalize(body.email)))
    if user and not user.is_verified:
        try:
            _send_code(db, user, OtpPurpose.VERIFY_EMAIL, quiet=True)
        except OtpRateLimited:
            pass  # silently ignore; the UI enforces the 60s resend timer
    return generic


@router.post("/login", response_model=UserOut, dependencies=[Depends(limit_login)])
def login(body: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == _normalize(body.email)))
    # Always run bcrypt, even for unknown emails, so response time doesn't leak which emails exist.
    if not verify_secret(body.password, user.password_hash if user else None):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password")
    if not user.is_verified:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Email not verified")

    _on_login(db, response, user)
    return user


@router.post("/logout", response_model=MessageOut)
def logout(response: Response):
    s = get_settings()
    response.delete_cookie(s.cookie_name, path="/", httponly=True, secure=s.is_production, samesite="lax")
    return MessageOut(message="Logged out")


@router.post("/forgot-password", response_model=MessageOut, dependencies=[Depends(limit_otp)])
def forgot_password(body: ForgotPasswordRequest, db: Session = Depends(get_db)):
    # Identical response (and no error on send failure) whether or not the account exists.
    generic = MessageOut(message="If an account exists for this email, we've sent a reset code.")
    user = db.scalar(select(User).where(User.email == _normalize(body.email)))
    if user:
        try:
            _send_code(db, user, OtpPurpose.RESET_PASSWORD, quiet=True)
        except OtpRateLimited:
            pass
    return generic


@router.post("/reset-password", response_model=MessageOut, dependencies=[Depends(limit_otp)])
def reset_password(body: ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.scalar(select(User).where(User.email == _normalize(body.email)))
    if user is None or check_otp(db, user, OtpPurpose.RESET_PASSWORD, body.code) is not OtpResult.OK:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=INVALID_CODE)

    user.password_hash = hash_secret(body.new_password)
    user.token_version += 1  # log out every existing session
    user.is_verified = True  # they just proved they own the inbox
    db.commit()
    log.info("Password reset for user_id=%s", user.id)
    return MessageOut(message="Password updated. Please log in with your new password.")


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    out = UserOut.model_validate(user)
    out.is_admin = user.email.lower() in get_settings().admin_email_list  # UI shows the AI status page
    return out
