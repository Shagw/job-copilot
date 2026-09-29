"""KeyPool: failover across (API key, model) slots with cooldowns.

ARCHITECTURE.md §4:
- Slots are tried in priority order (model-major: best model on every key first).
- 429 -> slot cools down for `retry_after` seconds; nobody can use it meanwhile.
- 401 -> slot disabled for the life of the process.
- All slots cooling -> caller may wait up to `max_wait`; otherwise AllSlotsBusy.

State is in memory and guarded by one lock, so it is shared by every request in
this process. Multi-worker deployments would move this state to Redis.
"""
from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger("app.llm.pool")


class AllSlotsBusy(Exception):
    """Every usable slot is cooling down (or disabled)."""

    def __init__(self, retry_after: float | None):
        self.retry_after = retry_after  # None = no slot will ever recover (all disabled)
        super().__init__(f"All LLM slots busy; retry after {retry_after}s")


@dataclass
class Slot:
    index: int
    api_key: str
    model: str
    cooldown_until: float = 0.0  # monotonic seconds
    disabled: bool = False
    last_error: str | None = None

    @property
    def key_hint(self) -> str:
        # Only ever expose the last 4 characters.
        return f"…{self.api_key[-4:]}" if len(self.api_key) >= 4 else "…"

    @property
    def label(self) -> str:
        return f"slot{self.index}({self.key_hint}, {self.model})"


class KeyPool:
    def __init__(self, api_keys: list[str], models: list[str], clock: Callable[[], float] = time.monotonic):
        if not api_keys or not models:
            raise ValueError("KeyPool needs at least one API key and one model")
        self._clock = clock
        self._lock = threading.Lock()
        self.slots = [
            Slot(index=i, api_key=key, model=model)
            for i, (model, key) in enumerate((m, k) for m in models for k in api_keys)
        ]

    # ---- selection ----

    def acquire(self, exclude: set[int] | None = None) -> Slot:
        """Return the highest-priority slot that is usable right now, or raise AllSlotsBusy."""
        exclude = exclude or set()
        with self._lock:
            now = self._clock()
            soonest: float | None = None
            for slot in self.slots:
                if slot.disabled or slot.index in exclude:
                    continue
                if slot.cooldown_until <= now:
                    return slot
                wait = slot.cooldown_until - now
                soonest = wait if soonest is None else min(soonest, wait)
            raise AllSlotsBusy(soonest)

    # ---- state changes ----

    def mark_rate_limited(self, slot: Slot, retry_after: float, reason: str = "rate limited") -> None:
        with self._lock:
            until = self._clock() + max(retry_after, 1.0)
            slot.cooldown_until = max(slot.cooldown_until, until)
            slot.last_error = reason
        log.warning("LLM %s cooling down for %.0fs (%s)", slot.label, retry_after, reason)

    def mark_invalid(self, slot: Slot, reason: str = "invalid API key") -> None:
        with self._lock:
            slot.disabled = True
            slot.last_error = reason
        log.error("LLM %s disabled (%s)", slot.label, reason)

    def disable_key(self, api_key: str, reason: str = "invalid API key") -> None:
        """A bad key is bad for every model, so disable all of its slots."""
        with self._lock:
            hit = [s for s in self.slots if s.api_key == api_key]
            for s in hit:
                s.disabled, s.last_error = True, reason
        log.error("LLM key %s disabled on %d slot(s) (%s)", hit[0].key_hint if hit else "?", len(hit), reason)

    def disable_model(self, model: str, reason: str = "model not found") -> None:
        """A removed model is gone on every key, so disable all of its slots."""
        with self._lock:
            for s in self.slots:
                if s.model == model:
                    s.disabled, s.last_error = True, reason
        log.error("LLM model %s disabled on all keys (%s)", model, reason)

    # ---- introspection ----

    def status(self) -> list[dict]:
        with self._lock:
            now = self._clock()
            out = []
            for s in self.slots:
                remaining = max(0.0, s.cooldown_until - now)
                state = "disabled" if s.disabled else ("cooling" if remaining > 0 else "active")
                out.append({
                    "slot": s.index,
                    "key": s.key_hint,
                    "model": s.model,
                    "state": state,
                    "cooldown_remaining_seconds": round(remaining, 1),
                    "last_error": s.last_error,
                })
            return out
