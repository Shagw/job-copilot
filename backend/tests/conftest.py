import hashlib
import math
import os
import re
import tempfile

# Point the app at throwaway storage and fake keys *before* importing it.
# (Env vars override values in .env, so real keys are never used by tests.)
_tmpdir = tempfile.mkdtemp()
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["EMAIL_MODE"] = "console"
os.environ["GROQ_API_KEYS"] = "test-key-AAAA,test-key-BBBB"
os.environ["GROQ_MODELS"] = "big-model,small-model"
os.environ["CHROMA_PATH"] = f"{_tmpdir}/chroma"
os.environ["ADMIN_EMAILS"] = "admin@example.com"
os.environ["JWT_SECRET"] = "test-secret-" + "x" * 40

import chromadb  # noqa: E402
import pytest  # noqa: E402
from chromadb.config import Settings as ChromaSettings  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.auth import rate_limit  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.main import app  # noqa: E402
from app.rag.store import ResumeIndex, get_resume_index  # noqa: E402
from app.routers import auth as auth_router  # noqa: E402


class FakeEmbedder:
    """Deterministic bag-of-words embedding: fast, offline, and similar texts score higher."""

    DIM = 256

    def embed(self, texts):
        out = []
        for t in texts:
            v = [0.0] * self.DIM
            for word in re.findall(r"[a-z0-9]+", t.lower()):
                v[int(hashlib.md5(word.encode()).hexdigest(), 16) % self.DIM] += 1.0
            norm = math.sqrt(sum(x * x for x in v)) or 1.0
            out.append([x / norm for x in v])
        return out


@pytest.fixture(autouse=True)
def fresh_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    rate_limit.login_limiter.reset()
    rate_limit.otp_limiter.reset()
    from app.routers.sessions import agent_limiter

    agent_limiter.reset()
    yield


@pytest.fixture
def resume_index(tmp_path):
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"), settings=ChromaSettings(anonymized_telemetry=False))
    index = ResumeIndex(client, FakeEmbedder())
    app.dependency_overrides[get_resume_index] = lambda: index
    yield index
    app.dependency_overrides.pop(get_resume_index, None)


@pytest.fixture
def db():
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def outbox(monkeypatch):
    """Captures OTP emails instead of sending them: list of (to, code, purpose)."""
    sent: list[tuple[str, str, str]] = []
    monkeypatch.setattr(auth_router, "send_otp_email", lambda to, code, purpose: sent.append((to, code, purpose)))
    return sent


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


EMAIL = "alice@example.com"
PASSWORD = "Secret123"


def make_user(client, outbox, email=EMAIL, password=PASSWORD, stay_logged_in=False):
    client.post("/auth/signup", json={"email": email, "password": password})
    code = [c for to, c, _ in outbox if to == email][-1]
    client.post("/auth/verify-otp", json={"email": email, "code": code})
    if not stay_logged_in:
        client.post("/auth/logout")
    return email


@pytest.fixture
def verified_user(client, outbox):
    return make_user(client, outbox)


@pytest.fixture
def logged_in(client, outbox):
    """A client logged in as alice."""
    make_user(client, outbox, stay_logged_in=True)
    return client
