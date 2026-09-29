"""FastAPI dependencies for authentication."""
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.auth.security import decode_access_token
from app.config import get_settings
from app.database import get_db
from app.models import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Resolve the logged-in, verified user from the JWT cookie, or 401."""
    token = request.cookies.get(get_settings().cookie_name)
    decoded = decode_access_token(token) if token else None
    user = db.get(User, decoded[0]) if decoded else None
    if user is None or not user.is_verified or decoded[1] != user.token_version:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user
