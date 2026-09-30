"""The API app.

Development: uvicorn app.main:app --reload   (Vite dev server proxies /api/* here)
Production:  uvicorn app.server:site          (serves the built frontend + this app at /api)
"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.agents.base import AgentError
from app.config import get_settings
from app.database import Base, SessionLocal, engine
from app.errors import describe
from app.llm.groq_client import LLMNotConfigured, LLMRequestTooLarge
from app.llm.key_pool import AllSlotsBusy
from app.routers import admin, auth, resume, sessions
from app.services.archive import archive_expired

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")


def startup() -> None:
    # Simple table creation for now; Alembic migrations can replace this later.
    Base.metadata.create_all(bind=engine)
    with SessionLocal() as db:
        archive_expired(db)  # all users; per-user sweeps also run at login and on history reads


@asynccontextmanager
async def lifespan(_: FastAPI):
    startup()
    yield


app = FastAPI(title="Job Application Copilot", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[get_settings().frontend_origin],  # only our React app
    allow_credentials=True,  # needed for the auth cookie
    allow_methods=["GET", "POST", "PATCH"],
    allow_headers=["Content-Type"],
)

app.include_router(auth.router)
app.include_router(resume.router)
app.include_router(sessions.router)
app.include_router(admin.router)


def _handle(_: Request, exc: Exception):
    status, body, headers = describe(exc)
    return JSONResponse(status_code=status, content=body, headers=headers or None)


for _exc in (AgentError, AllSlotsBusy, LLMNotConfigured, LLMRequestTooLarge):
    app.add_exception_handler(_exc, _handle)


@app.get("/health")
def health():
    return {"status": "ok"}
