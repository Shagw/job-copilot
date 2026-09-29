"""Master resume upload. Uploading again replaces the active resume; old rows are kept."""
import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.deps import get_current_user
from app.config import get_settings
from app.database import get_db
from app.models import Resume, User
from app.rag.store import ResumeIndex, get_resume_index
from app.schemas import ResumeOut
from app.services.file_parser import FileParseError, extract_text

log = logging.getLogger("app.resume")
router = APIRouter(prefix="/resume", tags=["resume"])


def latest_resume(db: Session, user_id: int) -> Resume | None:
    return db.scalar(
        select(Resume).where(Resume.user_id == user_id).order_by(Resume.uploaded_at.desc(), Resume.id.desc()).limit(1)
    )


@router.post("", response_model=ResumeOut, status_code=201)
def upload_resume(
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    index: ResumeIndex = Depends(get_resume_index),
):
    s = get_settings()
    data = file.file.read(s.max_upload_bytes + 1)  # never read more than the limit
    if len(data) > s.max_upload_bytes:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, detail="File too large (max 5 MB)")

    filename = (file.filename or "resume.txt").rsplit("/", 1)[-1][:255]
    try:
        text = extract_text(filename, data)
    except FileParseError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(e))
    if len(text) > s.max_resume_chars:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Resume text is too long (max 50,000 characters)")

    resume = Resume(user_id=user.id, filename=filename, raw_text=text)
    db.add(resume)
    db.flush()  # get resume.id
    try:
        chunks = index.index_resume(user.id, resume.id, text)
    except Exception:
        db.rollback()
        log.exception("Indexing failed for user_id=%s", user.id)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Could not process resume. Try again.")
    db.commit()

    out = ResumeOut.model_validate(resume)
    out.chunks = chunks
    return out


@router.get("", response_model=ResumeOut)
def get_resume(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    resume = latest_resume(db, user.id)
    if resume is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No resume uploaded yet")
    return resume
