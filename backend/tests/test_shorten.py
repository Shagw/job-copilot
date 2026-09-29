"""Long inputs are fitted to the LLM budget automatically instead of failing with "too long"."""
import json
from types import SimpleNamespace as NS

from app.agents.base import _fit_budget
from app.agents.shorten import char_budget, shorten
from app.llm.groq_client import estimate_tokens
from tests.test_agents import PARSED, use_llm  # noqa: F401  (fixture)

JOB = """Senior Backend Engineer
Northwind

Requirements:
- 4+ years of Python
- Experience with FastAPI
- Kubernetes in production
"""
FILLER = "\n".join(f"Our company history paragraph number {i} about culture and values." for i in range(400))
LEGAL = "\n".join([
    "Northwind is an equal opportunity employer and considers applicants without regard to race.",
    "We use cookies to improve your experience. Read our privacy policy.",
    "Share this job", "Apply now", "© 2026 Northwind. All rights reserved.",
] * 50)


def test_short_text_is_untouched_except_whitespace():
    s = shorten("  Python   dev \n\n\n\nFastAPI  ", 1000)
    assert s.text == "Python dev\n\nFastAPI" and s.removed_lines == 0


def test_duplicates_and_boilerplate_go_first():
    s = shorten(JOB + LEGAL, 250)  # repeats collapse to 5 legal lines; those must go next
    assert "4+ years of Python" in s.text and "Kubernetes" in s.text
    assert "cookies" not in s.text and "equal opportunity" not in s.text
    assert s.was_shortened


def test_requirements_survive_long_filler_in_order():
    s = shorten(FILLER[:3000] + "\n" + JOB + "\n" + FILLER, 1500, keywords=["Python", "FastAPI", "Kubernetes"])
    assert len(s.text) <= 1500
    for req in ["4+ years of Python", "Experience with FastAPI", "Kubernetes in production"]:
        assert req in s.text
    assert s.text.index("Python") < s.text.index("Kubernetes")  # original order kept
    assert s.removed_lines > 300


def test_char_budget_follows_configured_token_limit():
    small, big = NS(max_request_tokens=8000), NS(max_request_tokens=30000)
    assert char_budget(big, 2000, 1000) > char_budget(small, 2000, 1000) > 10_000
    assert char_budget(small, 2000, 1000, copies=3) < char_budget(small, 2000, 1000)
    assert char_budget(NS(max_request_tokens=100), 2000, 1000) == 2000  # floor


def test_long_job_posting_is_parsed_not_rejected(logged_in, use_llm):

    fake = use_llm(json.dumps(PARSED))
    fake.max_request_tokens = 8000
    r = logged_in.post("/sessions", json={"job_text": JOB + FILLER * 3 + LEGAL})
    assert r.status_code == 201, r.text
    prompt = fake.calls[0][0][1]["content"]
    assert "Kubernetes in production" in prompt
    assert estimate_tokens(fake.calls[0][0]) < 8000 - 2000


def test_agent_history_is_trimmed_to_fit_budget():
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    for i in range(10):
        messages.append({"role": "assistant", "content": "", "tool_calls": []})
        messages.append({"role": "tool", "tool_call_id": str(i), "content": "x" * 5000})
    llm = NS(max_request_tokens=8000)
    _fit_budget(llm, messages, [], 2650)
    assert estimate_tokens(messages) <= 8000 - 2650
    assert messages[-1]["content"] == "x" * 5000  # newest result kept whole
    assert "trimmed" in messages[3]["content"]
