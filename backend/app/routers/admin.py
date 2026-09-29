"""Ops endpoints. Only emails listed in ADMIN_EMAILS can use them."""
from fastapi import APIRouter, Depends, HTTPException, status

from app.auth.deps import get_current_user
from app.config import get_settings
from app.llm.groq_client import get_llm
from app.models import User
from app.schemas import LlmSlotStatus

router = APIRouter(prefix="/admin", tags=["admin"])


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.email.lower() not in get_settings().admin_email_list:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Admins only")
    return user


@router.get("/llm-status", response_model=list[LlmSlotStatus], dependencies=[Depends(require_admin)])
def llm_status():
    return get_llm().pool.status()
