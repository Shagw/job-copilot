"""The API app.

Development: uvicorn app.main:app --reload   (Vite dev server proxies /api/* here)
Production:  uvicorn app.server:site          (serves the built frontend + this app at /api)
"""
import logging
import math
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import models  # noqa: F401  (registers tables on Base.metadata)
from app.agents.base import AgentError
from app.config import get_settings
from app.database import Base, SessionLocal, engine
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


@app.exception_handler(AgentError)
async def _agent_error(_: Request, exc: AgentError):
    return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})


@app.exception_handler(AllSlotsBusy)
async def _llm_busy(_: Request, exc: AllSlotsBusy):
    if exc.retry_after is None:
        return JSONResponse(status_code=503, content={"detail": "AI service is unavailable. Please contact the admin."})
    seconds = max(1, math.ceil(exc.retry_after))
    return JSONResponse(
        status_code=503,
        content={"detail": f"AI is busy, try again in {seconds} seconds", "retry_after": seconds},
        headers={"Retry-After": str(seconds)},
    )


@app.exception_handler(LLMNotConfigured)
async def _llm_not_configured(_: Request, __: LLMNotConfigured):
    return JSONResponse(status_code=503, content={"detail": "AI service is not configured."})


@app.exception_handler(LLMRequestTooLarge)
async def _llm_too_large(_: Request, __: LLMRequestTooLarge):
    return JSONResponse(status_code=413, content={
        "detail": "This job posting or resume is too long for the AI to process. Please shorten it and try again."
    })


@app.get("/health")
def health():
    return {"status": "ok"}
