from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.models import OtpCode, User
from tests.conftest import EMAIL, PASSWORD


def signup(client, email=EMAIL, password=PASSWORD):
    return client.post("/auth/signup", json={"email": email, "password": password})


def wrong(code: str) -> str:
    return f"{(int(code) + 1) % 1_000_000:06d}"


# ---------- signup ----------

def test_signup_creates_unverified_user_and_sends_code(client, outbox, db):
    r = signup(client)
    assert r.status_code == 201
    assert len(outbox) == 1 and outbox[0][0] == EMAIL and outbox[0][2] == "verify_email"
    user = db.scalar(select(User).where(User.email == EMAIL))
    assert user and not user.is_verified


def test_password_and_otp_are_stored_hashed(client, outbox, db):
    signup(client)
    user = db.scalar(select(User))
    otp = db.scalar(select(OtpCode))
    assert PASSWORD not in user.password_hash and user.password_hash.startswith("$2")
    assert outbox[0][1] not in otp.code_hash


def test_email_is_normalized(client, outbox):
    signup(client, email="  Alice@Example.COM ")
    r = client.post("/auth/verify-otp", json={"email": EMAIL, "code": outbox[0][1]})
    assert r.status_code == 200


def test_weak_password_rejected(client, outbox):
    assert signup(client, password="short1").status_code == 422
    assert signup(client, password="onlyletters").status_code == 422
    assert signup(client, password="a1" * 40).status_code == 422  # > 72 bytes
    assert outbox == []


def test_duplicate_verified_email_rejected(client, verified_user):
    assert signup(client).status_code == 409


# ---------- verify OTP ----------

def test_verify_logs_user_in(client, outbox):
    signup(client)
    r = client.post("/auth/verify-otp", json={"email": EMAIL, "code": outbox[0][1]})
    assert r.status_code == 200 and r.json()["is_verified"] is True
    assert client.get("/auth/me").json()["email"] == EMAIL


def test_wrong_code_rejected(client, outbox):
    signup(client)
    r = client.post("/auth/verify-otp", json={"email": EMAIL, "code": wrong(outbox[0][1])})
    assert r.status_code == 400
    assert client.get("/auth/me").status_code == 401


def test_code_is_single_use(client, outbox):
    signup(client)
    code = outbox[0][1]
    assert client.post("/auth/verify-otp", json={"email": EMAIL, "code": code}).status_code == 200
    assert client.post("/auth/verify-otp", json={"email": EMAIL, "code": code}).status_code == 400


def test_code_locks_after_5_wrong_attempts(client, outbox):
    signup(client)
    code = outbox[0][1]
    for _ in range(5):
        client.post("/auth/verify-otp", json={"email": EMAIL, "code": wrong(code)})
    # Even the correct code no longer works.
    assert client.post("/auth/verify-otp", json={"email": EMAIL, "code": code}).status_code == 400


def test_expired_code_rejected(client, outbox, db):
    signup(client)
    otp = db.scalar(select(OtpCode))
    otp.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    assert client.post("/auth/verify-otp", json={"email": EMAIL, "code": outbox[0][1]}).status_code == 400


def test_malformed_code_rejected(client, outbox):
    signup(client)
    assert client.post("/auth/verify-otp", json={"email": EMAIL, "code": "12ab56"}).status_code == 422


def test_verify_unknown_email_gives_generic_error(client):
    r = client.post("/auth/verify-otp", json={"email": "nobody@example.com", "code": "123456"})
    assert r.status_code == 400 and r.json()["detail"] == "Invalid or expired code"


# ---------- resend OTP ----------

def test_resend_respects_cooldown(client, outbox):
    signup(client)
    r = client.post("/auth/resend-otp", json={"email": EMAIL})
    assert r.status_code == 200
    assert len(outbox) == 1  # no new code within 60s


def test_resend_after_cooldown_invalidates_old_code(client, outbox, db):
    signup(client)
    old_code = outbox[0][1]
    # Pretend the first code was sent 2 minutes ago.
    otp = db.scalar(select(OtpCode))
    otp.created_at = datetime.now(timezone.utc) - timedelta(minutes=2)
    db.commit()

    client.post("/auth/resend-otp", json={"email": EMAIL})
    assert len(outbox) == 2
    new_code = outbox[1][1]
    if old_code != new_code:
        assert client.post("/auth/verify-otp", json={"email": EMAIL, "code": old_code}).status_code == 400
    assert client.post("/auth/verify-otp", json={"email": EMAIL, "code": new_code}).status_code == 200


def test_resend_hourly_cap(client, outbox, db):
    signup(client)
    user = db.scalar(select(User))
    # 5 codes already sent within the last hour (oldest > 60s ago so cooldown isn't the blocker).
    base = datetime.now(timezone.utc) - timedelta(minutes=50)
    for i in range(4):
        db.add(OtpCode(user_id=user.id, code_hash="x", purpose="verify_email",
                       expires_at=base, created_at=base + timedelta(minutes=i), used_at=base))
    db.scalar(select(OtpCode).order_by(OtpCode.id)).created_at = base - timedelta(minutes=1)
    db.commit()
    client.post("/auth/resend-otp", json={"email": EMAIL})
    assert len(outbox) == 1  # capped


def test_resend_unknown_email_is_generic(client, outbox):
    r = client.post("/auth/resend-otp", json={"email": "nobody@example.com"})
    assert r.status_code == 200 and outbox == []


# ---------- login / logout ----------

def test_login_before_verification_forbidden(client, outbox):
    signup(client)
    r = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 403


def test_login_success_sets_httponly_cookie(client, verified_user):
    r = client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 200
    cookie = r.headers["set-cookie"].lower()
    assert "access_token=" in cookie and "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/auth/me").status_code == 200


def test_login_wrong_password_and_unknown_email_look_the_same(client, verified_user):
    a = client.post("/auth/login", json={"email": EMAIL, "password": "Wrong1234"})
    b = client.post("/auth/login", json={"email": "nobody@example.com", "password": "Wrong1234"})
    assert a.status_code == b.status_code == 401
    assert a.json() == b.json()


def test_logout_clears_session(client, verified_user):
    client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    client.post("/auth/logout")
    assert client.get("/auth/me").status_code == 401


def test_tampered_token_rejected(client, verified_user):
    client.post("/auth/login", json={"email": EMAIL, "password": PASSWORD})
    token = client.cookies.get("access_token")
    client.cookies.set("access_token", token[:-2] + ("aa" if token[-2:] != "aa" else "bb"))
    assert client.get("/auth/me").status_code == 401


def test_me_requires_auth(client):
    assert client.get("/auth/me").status_code == 401


def test_login_rate_limited(client, verified_user):
    codes = [client.post("/auth/login", json={"email": EMAIL, "password": "Wrong1234"}).status_code
             for _ in range(11)]
    assert codes[:10] == [401] * 10
    assert codes[10] == 429
