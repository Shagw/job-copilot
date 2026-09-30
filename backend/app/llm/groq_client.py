"""Groq chat client with automatic failover through the KeyPool.

Every LLM call in the app goes through `LLMClient.chat`. On failure it decides:
  429 rate limit           -> cool the slot down (Groq's retry-after), retry on next slot
  401 invalid key          -> disable every slot using that key
  403 permission denied    -> disable that slot
  404 model not found      -> disable that model on every key
  5xx / network / timeout  -> short cooldown, retry on next slot
  413 request too large    -> shrink max_tokens by the overflow and retry
  400 bad request          -> our bug, raise immediately (retrying won't help)
Before every call, max_tokens is clamped so prompt + output fits LLM_MAX_REQUEST_TOKENS.
If all slots are cooling it waits up to LLM_MAX_WAIT_SECONDS, then raises AllSlotsBusy.
"""
from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

import groq

from app.config import get_settings
from app.llm.key_pool import AllSlotsBusy, KeyPool, Slot

log = logging.getLogger("app.llm")

TRANSIENT_COOLDOWN_SECONDS = 5.0
# Groq error text looks like: "... Please try again in 1m2.5s." / "in 7.66s" / "in 350ms"
_RETRY_IN = re.compile(r"try again in\s+(?:(\d+)h)?(?:(\d+)m(?!s))?(?:([\d.]+)s)?(?:([\d.]+)ms)?", re.I)


class LLMNotConfigured(Exception):
    pass


class LLMRequestTooLarge(Exception):
    """The prompt alone doesn't fit the per-request token budget."""


MIN_OUTPUT_TOKENS = 1024
_TOO_LARGE = re.compile(r"Limit\s+(\d+),\s*Requested\s+(\d+)", re.I)


def estimate_tokens(messages: list[dict], tools: list | None = None) -> int:
    """Rough, deliberately pessimistic token count (~3 chars/token for JSON-heavy prompts)."""
    size = len(json.dumps(messages, ensure_ascii=False, default=str))
    if tools:
        size += len(json.dumps(tools))
    return size // 3 + 50


def fit_max_tokens(requested: int | None, prompt_tokens: int, budget: int) -> int | None:
    """Shrink max_tokens so prompt + reserved output stays inside the per-request budget."""
    if requested is None:
        return None
    room = budget - prompt_tokens - 100
    if room < MIN_OUTPUT_TOKENS:
        raise LLMRequestTooLarge(f"Prompt (~{prompt_tokens} tokens) is too large for the {budget}-token budget")
    return min(requested, room)


@dataclass
class ChatResult:
    message: Any  # groq ChatCompletionMessage: .content, .tool_calls
    model: str
    slot: int
    usage: Any = None


_GENERATION_FAILURES = ("json_validate_failed", "tool_use_failed")


def _is_generation_failure(err: groq.APIStatusError) -> bool:
    body = err.body if isinstance(err.body, dict) else {}
    code = (body.get("error") or body).get("code") if isinstance(body.get("error", body), dict) else None
    # json_validate_failed: bad JSON in JSON mode. tool_use_failed: the model called a tool when tools were
    # off, or wrote a malformed call. Both are the model misbehaving on this attempt, not a bad request.
    return err.status_code == 400 and any(c == code or c in str(err) for c in _GENERATION_FAILURES)


def parse_retry_after(err: groq.APIStatusError, default: float) -> float:
    """Seconds to cool down, from the retry-after header, then the error text, then a default."""
    header = err.response.headers.get("retry-after") if err.response is not None else None
    if header:
        try:
            return float(header)
        except ValueError:
            pass
    m = _RETRY_IN.search(str(err))
    if m and any(m.groups()):
        h, mins, s, ms = (float(g) if g else 0.0 for g in m.groups())
        return h * 3600 + mins * 60 + s + ms / 1000
    return default


def reasoning_params(model: str, effort: str) -> dict:
    """Keep hidden reasoning short: it counts against Groq's per-minute token limit and adds latency.

    gpt-oss accepts low/medium/high; qwen3 also accepts "none" (no reasoning at all). Other models
    get nothing. effort="" disables this entirely.
    """
    if not effort:
        return {}
    if model.startswith("openai/gpt-oss"):
        return {"reasoning_effort": "low" if effort == "none" else effort}
    if model.startswith("qwen/qwen3"):
        # "low" = as cheap as possible: qwen3 can switch reasoning off completely.
        return {"reasoning_effort": "none" if effort == "low" else effort, "reasoning_format": "hidden"}
    return {}


class LLMClient:
    def __init__(
        self,
        pool: KeyPool,
        default_cooldown: float = 60,
        max_wait: float = 10,
        timeout: float = 60,
        max_request_tokens: int | None = None,
        reasoning_effort: str = "",
        client_factory: Callable[[str], Any] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.pool = pool
        self.default_cooldown = default_cooldown
        self.max_wait = max_wait
        self.max_request_tokens = max_request_tokens
        self.reasoning_effort = reasoning_effort
        self._sleep = sleep
        self._clock = clock
        # max_retries=0: the SDK must NOT retry on its own; the pool decides.
        self._factory = client_factory or (lambda key: groq.Groq(api_key=key, max_retries=0, timeout=timeout))
        self._clients: dict[str, Any] = {}

    def _client_for(self, slot: Slot):
        if slot.api_key not in self._clients:
            self._clients[slot.api_key] = self._factory(slot.api_key)
        return self._clients[slot.api_key]

    def _acquire_with_wait(self, deadline: float) -> Slot:
        while True:
            try:
                return self.pool.acquire()
            except AllSlotsBusy as busy:
                remaining = deadline - self._clock()
                if busy.retry_after is None or busy.retry_after > remaining:
                    raise
                self._sleep(busy.retry_after + 0.05)

    def chat(self, messages: list[dict], **params) -> ChatResult:
        """Run a chat completion. `params` go straight to Groq (tools, response_format, temperature...).

        Retries are bounded by *time* (max_wait), not by attempt count: per-minute token limits
        produce many short 429s, and waiting a few seconds for a slot is the right behaviour.
        """
        deadline = self._clock() + self.max_wait
        max_attempts = max(20, len(self.pool.slots) * 4)  # safety net only
        params = dict(params)
        effort = params.pop("reasoning_effort", self.reasoning_effort)  # per-call override, e.g. "medium"
        if self.max_request_tokens:
            params["max_tokens"] = fit_max_tokens(
                params.get("max_tokens"), estimate_tokens(messages, params.get("tools")), self.max_request_tokens
            )

        for _ in range(max_attempts):
            slot = self._acquire_with_wait(deadline)
            try:
                call = {**reasoning_params(slot.model, effort), **params}
                resp = self._client_for(slot).chat.completions.create(model=slot.model, messages=messages, **call)
                return ChatResult(message=resp.choices[0].message, model=slot.model, slot=slot.index, usage=resp.usage)
            except groq.RateLimitError as e:
                self.pool.mark_rate_limited(slot, parse_retry_after(e, self.default_cooldown), "429 rate limited")
            except groq.AuthenticationError:
                self.pool.disable_key(slot.api_key, "401 invalid API key")
            except groq.PermissionDeniedError:
                self.pool.mark_invalid(slot, "403 permission denied")  # may be model-specific access
            except groq.NotFoundError:
                self.pool.disable_model(slot.model, "404 model not found")
            except (groq.InternalServerError, groq.APIConnectionError) as e:  # APITimeoutError is a subclass
                self.pool.mark_rate_limited(slot, TRANSIENT_COOLDOWN_SECONDS, f"transient: {type(e).__name__}")
            except groq.APIStatusError as e:
                if e.status_code == 413:
                    # Our estimate was too low: shrink the output reservation by the overflow and retry.
                    m = _TOO_LARGE.search(str(e))
                    current = params.get("max_tokens")
                    if not (m and current):
                        raise LLMRequestTooLarge(str(e)[:200]) from e
                    limit, requested = int(m.group(1)), int(m.group(2))
                    new_max = current - (requested - limit) - 200
                    if new_max < MIN_OUTPUT_TOKENS:
                        raise LLMRequestTooLarge("Prompt too large for this model's token limit") from e
                    log.info("LLM request too large on %s; max_tokens %s -> %s", slot.label, current, new_max)
                    params["max_tokens"] = new_max
                    continue
                if e.status_code >= 500:
                    self.pool.mark_rate_limited(slot, TRANSIENT_COOLDOWN_SECONDS, f"{e.status_code} server error")
                elif _is_generation_failure(e):
                    # The *model* produced invalid output (bad JSON, unwanted tool call).
                    # That's a generation hiccup, not a bad request: try another slot.
                    self.pool.mark_rate_limited(slot, 1.0, "generation failed")
                else:
                    raise  # 400/413/422: request problem, not a slot problem
            log.info("LLM failing over from %s", slot.label)

        raise AllSlotsBusy(TRANSIENT_COOLDOWN_SECONDS)


@lru_cache
def get_llm() -> LLMClient:
    """Process-wide client, so every request shares the same KeyPool state."""
    s = get_settings()
    if not s.groq_key_list:
        raise LLMNotConfigured("GROQ_API_KEYS is not set")
    pool = KeyPool(s.groq_key_list, s.groq_model_list)
    return LLMClient(
        pool,
        default_cooldown=s.llm_default_cooldown_seconds,
        max_wait=s.llm_max_wait_seconds,
        timeout=s.llm_request_timeout_seconds,
        max_request_tokens=s.llm_max_request_tokens,
        reasoning_effort=s.llm_reasoning_effort,
    )
