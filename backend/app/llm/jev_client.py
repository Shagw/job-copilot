"""TypeSafe Jev client: fast, typed judgments (no text generation).

Jev answers typed questions about a `state` in one parallel pass (~0.1-0.5s):
  noul   -> probability that a statement is true (0..1)
  choice -> one option from a set (<=255), with probabilities and confidence
  score  -> a position on 2-10 ordered levels, with probabilities and confidence

We use it for the *judgment* parts of the agents (does this resume line show that requirement?
is this letter generic?) and keep Groq for writing. Jev is optional: when TYPESAFE_API_KEY is
unset or Jev fails, callers catch `JevUnavailable` and fall back to Groq, so the app never depends on it.

Plain httpx against the documented HTTP API (POST /v1/systemone) instead of adding another SDK.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import httpx

from app.config import get_settings

log = logging.getLogger("app.llm.jev")

MAX_CHOICE_OPTIONS = 255
MAX_SCORE_LEVELS = 10
_RETRY_STATUSES = {429, 500, 502, 503, 529}


class JevUnavailable(Exception):
    """Jev is not configured, or failed; the caller should use its Groq fallback."""


def noul(instructions: Any, true: str | None = None, false: str | None = None) -> dict:
    q: dict = {"type": "noul", "instructions": instructions}
    if true or false:
        q["criteria"] = {k: v for k, v in (("true", true), ("false", false)) if v}
    return q


def choice(instructions: Any, options: dict[str, str | None]) -> dict:
    if not 2 <= len(options) <= MAX_CHOICE_OPTIONS:
        raise ValueError(f"a choice needs 2-{MAX_CHOICE_OPTIONS} options, got {len(options)}")
    return {"type": "choice", "instructions": instructions, "criteria": options}


def score(instructions: Any, levels: list[str]) -> dict:
    if not 2 <= len(levels) <= MAX_SCORE_LEVELS:
        raise ValueError(f"a score needs 2-{MAX_SCORE_LEVELS} levels, got {len(levels)}")
    return {"type": "score", "instructions": instructions, "criteria": levels}


@dataclass
class JevResult:
    answers: dict[str, dict]
    model: str
    input_tokens: int = 0
    seconds: float = 0.0
    questions: int = field(default=0)

    def noul(self, key: str, default: float = 0.0) -> float:
        a = self.answers.get(key) or {}
        return float(a.get("noul", default))

    def choice(self, key: str) -> tuple[str | None, float]:
        a = self.answers.get(key) or {}
        return a.get("choice"), float(a.get("confidence") or 0.0)

    def score(self, key: str) -> tuple[float | None, float]:
        a = self.answers.get(key) or {}
        s = a.get("score")
        return (float(s) if s is not None else None), float(a.get("confidence") or 0.0)

    def trace(self, label: str) -> dict:
        """A trace step shaped like the agents' other steps, for the "How the agent got here" view."""
        return {"type": "jev", "tool": label, "model": self.model, "tokens": self.input_tokens,
                "questions": self.questions, "seconds": round(self.seconds, 2)}


class JevClient:
    def __init__(self, api_key: str, model: str, base_url: str, timeout: float = 15,
                 transport: httpx.BaseTransport | None = None, max_attempts: int = 3,
                 sleep=time.sleep):
        self.model = model
        self._max_attempts = max_attempts
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            timeout=timeout,
            transport=transport,
        )

    def ask(self, state: Any, questions: dict[str, dict]) -> JevResult:
        if not questions:
            return JevResult(answers={}, model=self.model)
        body = {"state": state, "model": self.model, "questions": questions}
        start = time.monotonic()
        for attempt in range(1, self._max_attempts + 1):
            try:
                r = self._http.post("/v1/systemone", json=body)
            except httpx.HTTPError as e:
                if attempt == self._max_attempts:
                    raise JevUnavailable(f"network error: {type(e).__name__}") from e
                self._sleep(0.5 * attempt)
                continue
            if r.status_code == 200:
                data = r.json()
                answers = data.get("answers") or {}
                missing = set(questions) - set(answers)
                if missing:
                    raise JevUnavailable(f"answers missing for {sorted(missing)[:3]}")
                usage = data.get("usage") or {}
                return JevResult(answers=answers, model=data.get("model", self.model),
                                 input_tokens=int(usage.get("input_tokens") or 0),
                                 seconds=time.monotonic() - start, questions=len(questions))
            if r.status_code in _RETRY_STATUSES and attempt < self._max_attempts:
                wait = _retry_after(r) or 0.5 * 2 ** (attempt - 1)
                if wait > 5:
                    break  # not worth blocking the user: fall back to Groq instead
                self._sleep(wait)
                continue
            break
        # 401/422 are our bug or config; log the status (never the key) and let the caller fall back.
        log.warning("Jev request failed: HTTP %s %s", r.status_code, r.text[:200])
        raise JevUnavailable(f"HTTP {r.status_code}")


def _retry_after(r: httpx.Response) -> float | None:
    try:
        return float(r.headers.get("retry-after", ""))
    except ValueError:
        return None


class _Disabled:
    """Stand-in when no key is configured: every call falls back to Groq."""

    model = None

    def ask(self, state, questions):  # noqa: ARG002
        raise JevUnavailable("TYPESAFE_API_KEY is not set")


@lru_cache
def get_jev() -> JevClient | _Disabled:
    s = get_settings()
    if not s.typesafe_api_key:
        return _Disabled()
    return JevClient(s.typesafe_api_key, s.typesafe_model, s.typesafe_base_url, s.typesafe_timeout_seconds)
