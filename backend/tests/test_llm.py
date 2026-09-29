import threading
from types import SimpleNamespace

import groq
import httpx
import pytest

from app.llm.groq_client import LLMClient, parse_retry_after
from app.llm.key_pool import AllSlotsBusy, KeyPool


class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def sleep(self, seconds):
        self.t += seconds


def api_error(cls, status, message="error", headers=None):
    req = httpx.Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    return cls(message, response=httpx.Response(status, headers=headers or {}, request=req), body=None)


def ok_response(text="hello"):
    msg = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(message=msg)], usage=None)


class FakeGroq:
    """Scripted Groq client. `script[(key, model)]` is a list of results/exceptions consumed in order."""

    def __init__(self, key, script, calls):
        self.key, self.script, self.calls = key, script, calls
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, model, messages, **params):
        self.calls.append((self.key, model))
        queue = self.script.get((self.key, model), [])
        result = queue.pop(0) if queue else ok_response(f"{self.key}/{model}")
        if isinstance(result, Exception):
            raise result
        return result


def make_llm(script=None, keys=("A", "B"), models=("big", "small"), max_wait=10):
    clock = FakeClock()
    calls = []
    pool = KeyPool(list(keys), list(models), clock=clock)
    llm = LLMClient(
        pool,
        default_cooldown=60,
        max_wait=max_wait,
        client_factory=lambda key: FakeGroq(key, script or {}, calls),
        sleep=clock.sleep,
        clock=clock,
    )
    return llm, pool, clock, calls


MSG = [{"role": "user", "content": "hi"}]


# ---------- KeyPool ----------

def test_slot_order_is_best_model_on_every_key_first():
    pool = KeyPool(["A", "B"], ["big", "small"])
    assert [(s.api_key, s.model) for s in pool.slots] == [("A", "big"), ("B", "big"), ("A", "small"), ("B", "small")]


def test_cooling_slot_is_skipped_and_recovers():
    clock = FakeClock()
    pool = KeyPool(["A", "B"], ["big"], clock=clock)
    first = pool.acquire()
    pool.mark_rate_limited(first, 30)
    assert pool.acquire().api_key == "B"
    clock.t += 31
    assert pool.acquire().api_key == "A"


def test_all_cooling_reports_soonest_recovery():
    clock = FakeClock()
    pool = KeyPool(["A", "B"], ["big"], clock=clock)
    pool.mark_rate_limited(pool.slots[0], 40)
    pool.mark_rate_limited(pool.slots[1], 15)
    with pytest.raises(AllSlotsBusy) as e:
        pool.acquire()
    assert e.value.retry_after == pytest.approx(15)


def test_all_disabled_has_no_retry_after():
    pool = KeyPool(["A"], ["big"])
    pool.mark_invalid(pool.slots[0])
    with pytest.raises(AllSlotsBusy) as e:
        pool.acquire()
    assert e.value.retry_after is None


def test_status_never_exposes_full_key():
    pool = KeyPool(["gsk_supersecretvalue1234"], ["big"])
    status = pool.status()
    assert status[0]["key"] == "…1234"
    assert "supersecret" not in str(status)


def test_cooldown_is_shared_across_threads():
    clock = FakeClock()
    pool = KeyPool(["A", "B"], ["big"], clock=clock)
    pool.mark_rate_limited(pool.slots[0], 60)
    seen = []
    threads = [threading.Thread(target=lambda: seen.append(pool.acquire().api_key)) for _ in range(50)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert set(seen) == {"B"}  # no thread picked the cooling slot


# ---------- LLMClient failover ----------

def test_happy_path_uses_first_slot():
    llm, _, _, calls = make_llm()
    r = llm.chat(MSG)
    assert r.message.content == "A/big" and calls == [("A", "big")]


def test_429_fails_over_to_next_key_and_cools_down():
    script = {("A", "big"): [api_error(groq.RateLimitError, 429, headers={"retry-after": "30"})]}
    llm, pool, clock, calls = make_llm(script)
    r = llm.chat(MSG)
    assert r.message.content == "B/big"
    assert calls == [("A", "big"), ("B", "big")]
    assert pool.status()[0]["state"] == "cooling"

    # While A is cooling, every new request goes straight to B.
    llm.chat(MSG)
    assert calls[-1] == ("B", "big")

    # After the cooldown, A is used again.
    clock.t += 31
    llm.chat(MSG)
    assert calls[-1] == ("A", "big")


def test_both_keys_limited_falls_back_to_smaller_model():
    rl = lambda: api_error(groq.RateLimitError, 429, headers={"retry-after": "60"})  # noqa: E731
    llm, _, _, calls = make_llm({("A", "big"): [rl()], ("B", "big"): [rl()]})
    r = llm.chat(MSG)
    assert r.model == "small" and calls[-1] == ("A", "small")


def test_invalid_key_disables_that_key_on_every_model():
    llm, pool, clock, calls = make_llm({("A", "big"): [api_error(groq.AuthenticationError, 401)]})
    llm.chat(MSG)
    clock.t += 10_000
    llm.chat(MSG)
    assert all(key != "A" for key, _ in calls[1:])
    assert [s["state"] for s in pool.status()] == ["disabled", "active", "disabled", "active"]


def test_decommissioned_model_is_disabled_on_every_key():
    llm, pool, _, calls = make_llm({("A", "big"): [api_error(groq.NotFoundError, 404, "model_not_found")]})
    r = llm.chat(MSG)
    assert r.model == "small" and calls == [("A", "big"), ("A", "small")]
    assert [s["state"] for s in pool.status()] == ["disabled", "disabled", "active", "active"]


def test_server_error_short_cooldown_then_next_slot():
    llm, pool, _, _ = make_llm({("A", "big"): [api_error(groq.InternalServerError, 503)]})
    assert llm.chat(MSG).message.content == "B/big"
    assert 0 < pool.status()[0]["cooldown_remaining_seconds"] <= 5


def test_bad_request_is_not_retried():
    llm, _, _, calls = make_llm({("A", "big"): [api_error(groq.BadRequestError, 400)]})
    with pytest.raises(groq.BadRequestError):
        llm.chat(MSG)
    assert len(calls) == 1


def test_waits_briefly_when_every_slot_is_cooling():
    llm, pool, clock, calls = make_llm(keys=("A",), models=("big",), max_wait=10)
    pool.mark_rate_limited(pool.slots[0], 4)
    start = clock.t
    assert llm.chat(MSG).message.content == "A/big"
    assert 4 <= clock.t - start < 5


def test_many_short_rate_limits_are_waited_out():
    """Per-minute token limits give repeated ~1s 429s; keep retrying while inside max_wait."""
    rl = [api_error(groq.RateLimitError, 429, headers={"retry-after": "1"}) for _ in range(6)]
    llm, _, clock, calls = make_llm({("A", "big"): rl}, keys=("A",), models=("big",), max_wait=10)
    assert llm.chat(MSG).message.content == "A/big"
    assert len(calls) == 7 and clock.t - 1000 < 10


def test_gives_up_when_cooldown_longer_than_max_wait():
    llm, pool, _, calls = make_llm(keys=("A",), models=("big",), max_wait=10)
    pool.mark_rate_limited(pool.slots[0], 120)
    with pytest.raises(AllSlotsBusy) as e:
        llm.chat(MSG)
    assert e.value.retry_after == pytest.approx(120) and calls == []


@pytest.mark.parametrize(
    "headers,message,expected",
    [
        ({"retry-after": "12"}, "", 12),
        ({}, "Rate limit reached ... Please try again in 1m2.5s.", 62.5),
        ({}, "Please try again in 7.66s.", 7.66),
        ({}, "Please try again in 350ms.", 0.35),
        ({}, "Please try again in 2h3m.", 7380),
        ({}, "no hint here", 60),
    ],
)
def test_parse_retry_after(headers, message, expected):
    err = api_error(groq.RateLimitError, 429, message, headers)
    assert parse_retry_after(err, 60) == pytest.approx(expected)


# ---------- API mapping ----------

def test_all_busy_maps_to_503_with_retry_after(client):
    from fastapi import APIRouter

    from app.main import app

    router = APIRouter()

    @router.get("/_test/busy")
    def busy():
        raise AllSlotsBusy(7.2)

    app.include_router(router)
    r = client.get("/_test/busy")
    assert r.status_code == 503 and r.headers["retry-after"] == "8"
    assert "try again in 8 seconds" in r.json()["detail"]


def test_llm_status_admin_only(client, outbox):
    from tests.conftest import make_user

    make_user(client, outbox, stay_logged_in=True)
    assert client.get("/admin/llm-status").status_code == 403

    client.post("/auth/logout")
    make_user(client, outbox, email="admin@example.com", stay_logged_in=True)
    r = client.get("/admin/llm-status")
    assert r.status_code == 200
    assert [s["key"] for s in r.json()] == ["…AAAA", "…BBBB", "…AAAA", "…BBBB"]
    assert "test-key" not in r.text


# ---------- token budget (Groq counts prompt + max_tokens against the per-minute limit) ----------

class RecordingGroq(FakeGroq):
    def _create(self, model, messages, **params):
        self.calls.append((self.key, model, params.get("max_tokens")))
        queue = self.script.get((self.key, model), [])
        result = queue.pop(0) if queue else ok_response("ok")
        if isinstance(result, Exception):
            raise result
        return result


def budget_llm(script=None, budget=8000):
    calls = []
    pool = KeyPool(["A"], ["big"])
    llm = LLMClient(pool, max_request_tokens=budget, client_factory=lambda k: RecordingGroq(k, script or {}, calls))
    return llm, calls


def test_max_tokens_is_clamped_to_fit_budget():
    from app.llm.groq_client import estimate_tokens

    llm, calls = budget_llm()
    msgs = [{"role": "user", "content": "x" * 9000}]  # ~3000 tokens
    llm.chat(msgs, max_tokens=8000)
    assert calls[0][2] == 8000 - estimate_tokens(msgs) - 100


def test_prompt_too_large_for_budget_fails_fast():
    from app.llm.groq_client import LLMRequestTooLarge

    llm, calls = budget_llm()
    with pytest.raises(LLMRequestTooLarge):
        llm.chat([{"role": "user", "content": "x" * 30000}], max_tokens=4000)
    assert calls == []


def test_413_shrinks_max_tokens_and_retries():
    msg = "Request too large ... tokens per minute (TPM): Limit 8000, Requested 8216, please reduce your message size"
    llm, calls = budget_llm({("A", "big"): [api_error(groq.APIStatusError, 413, msg)]})
    llm.chat(MSG, max_tokens=4000)
    assert calls[0][2] == 4000 and calls[1][2] == 4000 - 216 - 200


def test_413_maps_to_friendly_error(client):
    from fastapi import APIRouter

    from app.llm.groq_client import LLMRequestTooLarge
    from app.main import app

    router = APIRouter()

    @router.get("/_test/too-large")
    def too_large():
        raise LLMRequestTooLarge("x")

    app.include_router(router)
    r = client.get("/_test/too-large")
    assert r.status_code == 413 and "too long" in r.json()["detail"]


def test_json_generation_failure_tries_next_slot():
    """Groq returns 400 json_validate_failed when the model emits bad JSON in JSON mode (seen live in E2E)."""
    err = api_error(groq.BadRequestError, 400,
                    "Error code: 400 - {'error': {'code': 'json_validate_failed', 'failed_generation': ''}}")
    llm, _, _, calls = make_llm({("A", "big"): [err]})
    assert llm.chat(MSG, response_format={"type": "json_object"}).message.content == "B/big"
    assert calls == [("A", "big"), ("B", "big")]
