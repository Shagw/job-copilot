"""Sending OTP emails. `console` mode logs the code (dev); `smtp` mode uses Gmail SMTP."""
import logging
import smtplib
import ssl
from email.message import EmailMessage

from app.config import get_settings

log = logging.getLogger("app.email")

_SUBJECTS = {
    "verify_email": "Your Job Copilot verification code",
    "reset_password": "Your Job Copilot password reset code",
}


def send_otp_email(to: str, code: str, purpose: str) -> None:
    s = get_settings()
    subject = _SUBJECTS.get(purpose, "Your Job Copilot code")
    body = (
        f"Your code is: {code}\n\n"
        f"It expires in {s.otp_expire_minutes} minutes. If you didn't request this, ignore this email."
    )

    if s.email_mode == "console":
        # Dev only: never enable console mode in production.
        log.warning("[DEV EMAIL] to=%s purpose=%s code=%s", to, purpose, code)
        return

    msg = EmailMessage()
    msg["From"] = s.email_from or s.smtp_user
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)

    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as smtp:
        smtp.starttls(context=ssl.create_default_context())
        smtp.login(s.smtp_user, s.smtp_password)
        smtp.send_message(msg)
