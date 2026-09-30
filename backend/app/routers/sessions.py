"""Job sessions: one per job the user works on. Each AI step is its own endpoint,
so the frontend can show the result for review before calling the next one.

History: only sessions from the last HISTORY_VISIBLE_DAYS (3) are visible. Older ones are
archived (hidden), never deleted, and there is no delete endpoint.
"""
import re
from collections.abc import Callable
from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.cover_writer import write_cover_letter
from app.agents.fit_scorer import FitResult, score_fit
from app.agents.job_parser import ParsedJob, parse_job
from app.agents.resume_tailor import tailor_resume
from app.auth.deps import get_current_user
from app.auth.rate_limit import RateLimiter
from app.config import get_settings
from app.database import get_db
from app.llm.jev_client import get_jev
from app.llm.groq_client import LLMClient, get_llm
from app.models import JobSession, Resume, User, as_utc
from app.rag.store import ResumeIndex, get_resume_index
from app.routers.resume import latest_resume
from app.schemas import (
    CoverLetterRequest,
    FitRequest,
    SessionCreate,
    SessionOut,
    SessionSummary,
    SessionUpdate,
    TailorRequest,
)
from app.services.archive import archive_expired, history_cutoff
from app.services.docx_export import text_to_docx
from app.services.pdf_export import letter_to_pdf, resume_to_pdf
from app.services.url_fetcher import FetchError, fetch_job_text

router = APIRouter(prefix="/sessions", tags=["sessions"])

STEPS = ["created", "parsed", "scored", "tailored", "done"]

# Protects the shared Groq quota from one user hammering the agents: 30 AI runs / 10 min.
agent_limiter = RateLimiter(max_requests=30, window_seconds=600)


def limit_agent_runs(user: User = Depends(get_current_user)) -> User:
    agent_limiter.check(f"user:{user.id}")
    return user


def advance(session: JobSession, step: str) -> None:
    """Move forward only; re-running an earlier step doesn't rewind progress."""
    if STEPS.index(step) > STEPS.index(session.current_step):
        session.current_step = step


def get_visible_session(db: Session, user: User, session_id: int) -> JobSession:
    """Owned by this user, not archived, and inside the history window. Otherwise 404 (never 403,
    so users can't probe which ids exist)."""
    s = db.get(JobSession, session_id)
    if s is None or s.user_id != user.id or s.archived_at is not None or as_utc(s.created_at) < history_cutoff():
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Session not found")
    return s


def _require_resume(db: Session, user: User) -> Resume:
    resume = latest_resume(db, user.id)
    if resume is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Upload your resume first")
    return resume


def get_job_fetcher() -> Callable[[str], str]:
    return fetch_job_text  # overridden in tests


@router.get("", response_model=list[SessionSummary])
def list_sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """History: this user's sessions from the last 3 days, newest first."""
    archive_expired(db, user.id)
    rows = db.scalars(
        select(JobSession)
        .where(JobSession.user_id == user.id, JobSession.archived_at.is_(None),
               JobSession.created_at >= history_cutoff())
        .order_by(JobSession.created_at.desc(), JobSession.id.desc())
        .limit(200)
    ).all()
    window = timedelta(days=get_settings().history_visible_days)
    return [
        SessionSummary(
            id=r.id,
            title=(r.parsed_job or {}).get("title") or None,
            company=(r.parsed_job or {}).get("company"),
            job_url=r.job_url,
            fit_score=(r.fit_result or {}).get("score"),
            current_step=r.current_step,
            status=r.status,
            created_at=as_utc(r.created_at),
            visible_until=as_utc(r.created_at) + window,
        )
        for r in rows
    ]


@router.post("", response_model=SessionOut, status_code=201)
def create_session(
    body: SessionCreate,
    user: User = Depends(limit_agent_runs),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
    fetch: Callable[[str], str] = Depends(get_job_fetcher),
):
    job_text = body.job_text
    if not job_text:
        try:
            job_text = fetch(str(body.job_url))
        except FetchError as e:
            # 422 + message: the UI asks the user to paste the description instead.
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e))

    parsed = parse_job(llm, job_text)  # LLM errors propagate before anything is saved
    session = JobSession(
        user_id=user.id,
        job_url=str(body.job_url) if body.job_url else None,
        job_text=job_text.strip(),
        parsed_job=parsed.model_dump(),
        current_step="parsed",
    )
    db.add(session)
    db.commit()
    return session


@router.post("/{session_id}/fit", response_model=SessionOut)
def run_fit(
    session_id: int,
    body: FitRequest | None = None,
    user: User = Depends(limit_agent_runs),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
    index: ResumeIndex = Depends(get_resume_index),
    jev=Depends(get_jev),
):
    session = get_visible_session(db, user, session_id)
    resume = _require_resume(db, user)

    if body and body.parsed_job:  # the user reviewed and edited the parsed job
        session.parsed_job = body.parsed_job.model_dump()

    job = ParsedJob.model_validate(session.parsed_job or {})
    result, run = score_fit(llm, job, index, user.id, resume.raw_text, jev)

    session.fit_result = result.model_dump()
    # Reassign (not mutate) so SQLAlchemy notices the JSON change.
    session.agent_trace = {**(session.agent_trace or {}), "fit": run.trace}
    advance(session, "scored")
    db.commit()
    return session


@router.post("/{session_id}/tailor", response_model=SessionOut)
def run_tailor(
    session_id: int,
    body: TailorRequest | None = None,
    user: User = Depends(limit_agent_runs),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
    index: ResumeIndex = Depends(get_resume_index),
    jev=Depends(get_jev),
):
    session = get_visible_session(db, user, session_id)
    resume = _require_resume(db, user)
    job = ParsedJob.model_validate(session.parsed_job or {})
    fit = FitResult.model_validate(session.fit_result) if session.fit_result else None

    current = body.current_resume.strip() if body and body.current_resume else None
    previous = session.tailor_report or {}
    text, report, run = tailor_resume(
        llm, job, resume.raw_text, index, user.id, fit, body.instructions if body else None, jev=jev,
        current=current,
        # Notes from earlier rounds stay true facts when revising; a fresh tailoring starts over.
        prior_notes=previous.get("notes", []) if current else None,
        revision=previous.get("revision", 0) + 1 if current else 0,
    )
    session.tailored_resume = text
    session.tailor_report = report.model_dump()
    session.agent_trace = {**(session.agent_trace or {}), "tailor": run.trace}
    advance(session, "tailored")
    db.commit()
    return session


@router.post("/{session_id}/cover-letter", response_model=SessionOut)
def run_cover_letter(
    session_id: int,
    body: CoverLetterRequest | None = None,
    user: User = Depends(limit_agent_runs),
    db: Session = Depends(get_db),
    llm: LLMClient = Depends(get_llm),
    index: ResumeIndex = Depends(get_resume_index),
    jev=Depends(get_jev),
):
    session = get_visible_session(db, user, session_id)
    resume = _require_resume(db, user)
    if body and body.tailored_resume:  # the user reviewed and edited the tailored resume
        session.tailored_resume = body.tailored_resume.strip()
    job = ParsedJob.model_validate(session.parsed_job or {})
    fit = FitResult.model_validate(session.fit_result) if session.fit_result else None

    letter, report, trace = write_cover_letter(
        llm, job, session.job_text, session.tailored_resume or resume.raw_text, resume.raw_text,
        index, user.id, fit, body.instructions if body else None, jev=jev,
    )
    session.cover_letter = letter
    session.cover_letter_report = report.model_dump()
    session.agent_trace = {**(session.agent_trace or {}), "cover_letter": trace}
    advance(session, "done")
    db.commit()
    return session


@router.get("/{session_id}", response_model=SessionOut)
def get_session(session_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return get_visible_session(db, user, session_id)


@router.patch("/{session_id}", response_model=SessionOut)
def update_session(
    session_id: int, body: SessionUpdate, user: User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Save the user's edits and application status. No AI runs here."""
    session = get_visible_session(db, user, session_id)
    changes = body.model_dump(exclude_unset=True, exclude_none=True)
    if "parsed_job" in changes:
        session.parsed_job = body.parsed_job.model_dump()
    if "tailored_resume" in changes:
        session.tailored_resume = body.tailored_resume.strip()
    if "cover_letter" in changes:
        session.cover_letter = body.cover_letter.strip()
    if "status" in changes:
        session.status = body.status
    db.commit()
    return session


_DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _safe_filename(*parts: str | None) -> str:
    name = "-".join(p for p in parts if p)
    return re.sub(r"[^A-Za-z0-9]+", "-", name).strip("-")[:80] or "document"


@router.get("/{session_id}/export/{kind}")
def export_document(
    session_id: int,
    kind: Literal["resume", "cover-letter"],
    format: Literal["docx", "pdf"] = "docx",
    inline: bool = False,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Download the saved resume / letter as DOCX or PDF. `inline=true` (PDF only) is for the in-app
    preview: shown in the browser's PDF viewer inside our own page instead of downloaded."""
    session = get_visible_session(db, user, session_id)
    text = session.tailored_resume if kind == "resume" else session.cover_letter
    if not text:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"No {kind.replace('-', ' ')} generated yet")
    job = session.parsed_job or {}
    filename = _safe_filename(kind, job.get("company"), job.get("title")) + f".{format}"
    headers = {"Cache-Control": "private, no-store"}  # personal data; always the latest saved version
    if format == "pdf":
        content = resume_to_pdf(text) if kind == "resume" else letter_to_pdf(text)
        disposition = "inline" if inline else "attachment"
        if inline:
            # Only our own pages may embed it (the site default is "never framed").
            headers |= {"X-Frame-Options": "SAMEORIGIN",
                        "Content-Security-Policy": "frame-ancestors 'self'"}
        media_type = "application/pdf"
    else:
        content, disposition, media_type = text_to_docx(text), "attachment", _DOCX
    headers["Content-Disposition"] = f'{disposition}; filename="{filename}"'
    return Response(content=content, media_type=media_type, headers=headers)
