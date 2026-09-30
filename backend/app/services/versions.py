"""Resume version history for a session: every tailored text is kept so it can be compared and restored."""
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import JobSession, ResumeVersion

LIST_LIMIT = 30


def _last(db: Session, session_id: int) -> ResumeVersion | None:
    return db.scalars(select(ResumeVersion).where(ResumeVersion.session_id == session_id)
                      .order_by(ResumeVersion.id.desc()).limit(1)).first()


def ensure_baseline(db: Session, session: JobSession) -> None:
    """Sessions tailored before version history existed: keep their current text as the first version."""
    if not session.tailored_resume:
        return
    exists = db.scalar(select(func.count()).select_from(ResumeVersion).where(ResumeVersion.session_id == session.id))
    if not exists:
        db.add(ResumeVersion(session_id=session.id, text=session.tailored_resume, source="earlier",
                             report=session.tailor_report))
        db.flush()


def record(db: Session, session: JobSession, source: str, note: str | None = None) -> None:
    """Save the session's current tailored text as a new version (skipped if nothing changed)."""
    if not session.tailored_resume:
        return
    last = _last(db, session.id)
    if last is not None and last.text == session.tailored_resume and source in ("edit", "restore"):
        return  # saving the same text again isn't a new version
    db.add(ResumeVersion(session_id=session.id, text=session.tailored_resume, source=source,
                         note=(note or None) and note[:1000], report=session.tailor_report))


def list_versions(db: Session, session: JobSession) -> list[dict]:
    rows = db.scalars(select(ResumeVersion).where(ResumeVersion.session_id == session.id)
                      .order_by(ResumeVersion.id)).all()
    out = []
    for n, v in enumerate(rows, start=1):
        report = v.report or {}
        fit_after = report.get("fit_after") or {}
        out.append({
            "id": v.id, "number": n, "source": v.source, "note": v.note, "text": v.text,
            "created_at": v.created_at,
            "coverage": (report.get("coverage_after") or {}).get("supported_percent"),
            "fit_score": fit_after.get("after"),
            "current": False,
        })
    # Newest first; the latest version with the session's text is "current".
    out = out[::-1][:LIST_LIMIT]
    for item in out:
        if item["text"] == session.tailored_resume:
            item["current"] = True
            break
    return out


def get_version(db: Session, session: JobSession, version_id: int) -> ResumeVersion | None:
    return db.scalars(select(ResumeVersion).where(ResumeVersion.id == version_id,
                                                  ResumeVersion.session_id == session.id)).first()


def number_of(db: Session, version: ResumeVersion) -> int:
    return db.scalar(select(func.count()).select_from(ResumeVersion).where(
        ResumeVersion.session_id == version.session_id, ResumeVersion.id <= version.id))
