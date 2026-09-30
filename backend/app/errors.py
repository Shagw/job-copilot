"""One place that turns known failures into (status, body, headers), for JSON handlers and SSE error events."""
import math

from fastapi import HTTPException

from app.agents.base import AgentError
from app.llm.groq_client import LLMNotConfigured, LLMRequestTooLarge
from app.llm.key_pool import AllSlotsBusy

TOO_LARGE = "This job posting or resume is too long for the AI to process. Please shorten it and try again."


def describe(exc: Exception) -> tuple[int, dict, dict] | None:
    """None for unexpected errors (the caller logs them and answers with a generic 500)."""
    if isinstance(exc, HTTPException):
        return exc.status_code, {"detail": exc.detail}, dict(exc.headers or {})
    if isinstance(exc, AgentError):
        return exc.status_code, {"detail": str(exc)}, {}
    if isinstance(exc, AllSlotsBusy):
        if exc.retry_after is None:
            return 503, {"detail": "AI service is unavailable. Please contact the admin."}, {}
        seconds = max(1, math.ceil(exc.retry_after))
        return (503, {"detail": f"AI is busy, try again in {seconds} seconds", "retry_after": seconds},
                {"Retry-After": str(seconds)})
    if isinstance(exc, LLMNotConfigured):
        return 503, {"detail": "AI service is not configured."}, {}
    if isinstance(exc, LLMRequestTooLarge):
        return 413, {"detail": TOO_LARGE}, {}
    return None
