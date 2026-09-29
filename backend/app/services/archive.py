"""3-day history window. Old sessions are *archived* (hidden from the user), never deleted."""
from datetime import datetime, timedelta, timezone

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import JobSession


def history_cutoff(now: datetime | None = None) -> datetime:
    now = now or datetime.now(timezone.utc)
    return now - timedelta(days=get_settings().history_visible_days)


def archive_expired(db: Session, user_id: int | None = None) -> int:
    """Set archived_at on sessions older than the window. Returns how many were archived.

    Visibility never depends on this sweep having run: every read also filters on
    created_at, so this only records *when* something was archived.
    """
    now = datetime.now(timezone.utc)
    stmt = (
        update(JobSession)
        .where(JobSession.archived_at.is_(None), JobSession.created_at < history_cutoff(now))
        .values(archived_at=now)
    )
    if user_id is not None:
        stmt = stmt.where(JobSession.user_id == user_id)
    count = db.execute(stmt).rowcount or 0
    db.commit()
    return count
