"""TypeSafe Jev: HTTP client contract, and the fast paths in Fit / Tailor / Critic (with Groq fallback)."""
import json

import httpx
import pytest

from app.agents.cover_critic import JEV_CHECKS, critique_with_jev, lint_letter
from app.agents.cover_writer import write_cover_letter
from app.agents.fit_scorer import score_fit
from app.agents.resume_tailor import tailor_resume
from app.llm.jev_client import JevClient, JevResult, JevUnavailable, choice, get_jev, noul, score
from app.main import app
from tests.test_agents import FIT_FINAL, JOB_TEXT, FakeLLM, start_session, use_llm  # noqa: F401
from tests.test_resume import RESUME
from tests.test_tailor_cover import JOB, TAILORED, letter


class FakeJev:
    """Answers each question with `answer(key, question, state)`; records every request."""

    model = "jev-test"

    def __init__(self, answer=None, fail=False):
        self.answer, self.fail, self.requests = answer, fail, []

    def ask(self, state, questions):
        self.requests.append((state, questions))
        if self.fail:
            raise JevUnavailable("HTTP 529")
        answers = {k: self.answer(k, q, state) for k, q in questions.items()}
        return JevResult(answers=answers, model="jev-test", input_tokens=123, seconds=0.2, questions=len(questions))


# ---------- HTTP client ----------

def make_client(handler, **kw):
    return JevClient("sk-test", "jev-1.13.0", "https://api.typesafe.test", transport=httpx.MockTransport(handler),
                     sleep=lambda s: None, **kw)


def test_request_matches_the_documented_api():
    seen = {}

    def handler(req: httpx.Request):
        seen["url"], seen["auth"], seen["body"] = str(req.url), req.headers["authorization"], json.loads(req.content)
        return httpx.Response(200, json={
            "model": "jev-1.13.0",
            "answers": {"urgent": {"type": "noul", "noul": 0.9},
                        "team": {"type": "choice", "choice": "billing", "probabilities": {"billing": 0.8, "tech": 0.2},
                                 "confidence": 0.7},
                        "mood": {"type": "score", "score": 1.2, "legend": {}, "probabilities": {}, "confidence": 0.6}},
            "usage": {"input_tokens": 310, "output_tokens": 20}})

    res = make_client(handler).ask("I was charged twice!", {
        "urgent": noul("Is this urgent?"),
        "team": choice("Which team?", {"billing": "Payments", "tech": None}),
        "mood": score("How upset?", ["Calm", "Annoyed", "Angry"]),
    })
    assert seen["url"] == "https://api.typesafe.test/v1/systemone" and seen["auth"] == "Bearer sk-test"
    assert seen["body"]["model"] == "jev-1.13.0" and seen["body"]["state"] == "I was charged twice!"
    assert seen["body"]["questions"]["team"] == {"type": "choice", "instructions": "Which team?",
                                                  "criteria": {"billing": "Payments", "tech": None}}
    assert res.noul("urgent") == 0.9 and res.choice("team") == ("billing", 0.7) and res.score("mood") == (1.2, 0.6)
    assert res.input_tokens == 310


def test_retries_overload_then_succeeds():
    calls = []

    def handler(req):
        calls.append(1)
        if len(calls) == 1:
            return httpx.Response(529, json={"error": "overloaded"})
        return httpx.Response(200, json={"model": "m", "answers": {"a": {"type": "noul", "noul": 0.1}}, "usage": {}})

    assert make_client(handler).ask("s", {"a": noul("q")}).noul("a") == 0.1 and len(calls) == 2


@pytest.mark.parametrize("status", [401, 422])
def test_auth_and_validation_errors_mean_fall_back(status):
    with pytest.raises(JevUnavailable):
        make_client(lambda req: httpx.Response(status, json={"detail": "bad"})).ask("s", {"a": noul("q")})


def test_missing_answers_and_network_errors_mean_fall_back():
    ok_but_empty = lambda req: httpx.Response(200, json={"model": "m", "answers": {}, "usage": {}})  # noqa: E731
    with pytest.raises(JevUnavailable):
        make_client(ok_but_empty).ask("s", {"a": noul("q")})

    def boom(req):
        raise httpx.ConnectError("down")

    with pytest.raises(JevUnavailable):
        make_client(boom).ask("s", {"a": noul("q")})


def test_long_retry_after_falls_back_instead_of_blocking():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(429, headers={"retry-after": "30"})

    with pytest.raises(JevUnavailable):
        make_client(handler).ask("s", {"a": noul("q")})
    assert len(calls) == 1


def test_question_limits():
    with pytest.raises(ValueError):
        choice("q", {"only": None})
    with pytest.raises(ValueError):
        score("q", [str(i) for i in range(11)])


def test_without_a_key_jev_is_disabled():
    get_jev.cache_clear()
    with pytest.raises(JevUnavailable):
        get_jev().ask("s", {"a": noul("q")})
    get_jev.cache_clear()


# ---------- Fit with Jev ----------

def fit_answers(key, q, state):
    """Python/FastAPI/AWS: strong with a real line; Kubernetes/Terraform missing; React: no line shows it."""
    rid, kind = key.split("_", 1)
    req = q["instructions"]["requirement"]
    if kind == "match":
        verdict = "missing" if req in ("Kubernetes", "Terraform") else "strong"
        return {"type": "choice", "choice": verdict, "confidence": 0.9, "probabilities": {}}
    line = q["instructions"]["line"]
    shows = req != "React" and any(w.lower() in line.lower() for w in req.replace("or", " ").split() if len(w) > 2)
    return {"type": "noul", "noul": 0.9 if shows else 0.05}


def test_fit_with_jev_uses_no_groq_tokens(resume_index):
    resume_index.index_resume(1, 1, RESUME)
    jev = FakeJev(fit_answers)
    llm = FakeLLM()  # would raise if called
    result, run = score_fit(llm, JOB, resume_index, 1, RESUME, jev)
    by_req = {r.requirement: r for r in result.requirements}

    assert llm.calls == [] and len(jev.requests) == 1  # one parallel Jev call for every requirement
    questions = jev.requests[0][1]
    assert {k for k in questions if k.endswith("_match")} == {f"{r}_match" for r in ["M1", "M2", "M3", "M4", "N1", "N2"]}
    assert all(q["type"] == "noul" for k, q in questions.items() if not k.endswith("_match"))  # one per req-line pair
    assert questions["M1_match"]["instructions"]["requirement"] == "Python"  # years judged by code, not Jev
    assert by_req["AWS"].match == "strong" and "AWS" in by_req["AWS"].evidence
    assert by_req["AWS"].evidence in RESUME  # always a verbatim resume line
    assert by_req["5+ years Python"].match == "partial"  # code years check still applies
    assert by_req["React"].match == "missing"  # "strong" overall, but no line shows it -> not trusted
    assert by_req["Kubernetes"].match == "missing"
    assert result.summary.startswith("Moderate fit") and result.strengths and result.advice  # written by code
    assert run.trace[1]["tool"] == "Jev: judged every requirement" and run.trace[1]["tokens"] == 123


def test_fit_falls_back_to_one_groq_call_when_jev_fails(resume_index):
    llm = FakeLLM(FIT_FINAL)
    result, run = score_fit(llm, JOB, resume_index, 1, RESUME, FakeJev(fail=True))
    assert len(llm.calls) == 1 and result.score == 70
    assert run.trace[1]["tool"] == "LLM: judged every requirement"


def test_fit_endpoint_uses_injected_jev(logged_in, use_llm, resume_index):  # noqa: F811
    logged_in.post("/resume", files={"file": ("cv.txt", RESUME.encode(), "text/plain")})
    s = start_session(logged_in, use_llm)
    use_llm()  # no Groq replies scripted: the fit must not need any
    app.dependency_overrides[get_jev] = lambda: FakeJev(fit_answers)
    try:
        r = logged_in.post(f"/sessions/{s['id']}/fit")
    finally:
        app.dependency_overrides.pop(get_jev, None)
    assert r.status_code == 200, r.text
    assert r.json()["fit_result"]["verdict"] == "Moderate fit"


# ---------- Tailor: Jev flags changed lines the resume doesn't support ----------

def test_tailor_jev_flags_unsupported_changed_line_and_triggers_fix(resume_index):
    invented_duty = TAILORED.replace("- Set up CI/CD pipelines with GitHub Actions",
                                     "- Owned the company-wide incident response process")

    def answer(key, q, state):
        if "line" not in q["instructions"]:  # recruiter check: all fine here
            return {"type": "noul", "noul": 0.9 if key == "relevant_fast" else 0.1}
        return {"type": "noul", "noul": 0.05 if "incident response" in q["instructions"]["line"] else 0.95}

    jev = FakeJev(answer)
    llm = FakeLLM(f"<resume>{invented_duty}</resume>", f"<resume>{TAILORED}</resume>")
    text, report, run = tailor_resume(llm, JOB, RESUME, resume_index, 1, jev=jev)
    assert text == TAILORED and report.claims.ok
    assert "incident response" in llm.calls[1][0][-1]["content"]  # the Jev finding went into the one fix call
    assert jev.requests[0][0] == {"original_resume": RESUME}
    unchanged = "Built REST APIs in Python with FastAPI serving 2M requests per day"
    assert all(q["instructions"]["line"] != unchanged for q in jev.requests[0][1].values())  # only changed lines
    assert "relevant_fast" in jev.requests[1][1]  # then the recruiter check


def test_tailor_keeps_working_when_jev_is_down(resume_index):
    llm = FakeLLM(f"<resume>{TAILORED}</resume>")
    text, report, _ = tailor_resume(llm, JOB, RESUME, resume_index, 1, jev=FakeJev(fail=True))
    assert text == TAILORED and report.target_met


# ---------- Critic with Jev ----------

def critic_answers(quality_level, failing=()):
    def answer(key, q, state):
        if key == "quality":
            return {"type": "score", "score": quality_level, "confidence": 0.8}
        good = JEV_CHECKS[key][1]
        return {"type": "noul", "noul": (0.1 if good else 0.9) if key in failing else (0.9 if good else 0.1)}
    return answer


def test_jev_critic_maps_answers_to_feedback():
    review, step = critique_with_jev(FakeJev(critic_answers(3.4)), letter(), JOB, RESUME)
    assert review.score == 9 and review.approved and review.issues == []
    review, _ = critique_with_jev(FakeJev(critic_answers(3.0, ["grounded", "cliches"])), letter(), JOB, RESUME)
    assert not review.approved
    assert review.issues == ["Some claims aren't supported by the resume.", "Relies on generic, clichéd phrasing."]
    assert len(review.suggestions) == 2 and step["tool"] == "Jev: reviewed the letter"


def test_writer_critic_loop_with_jev_uses_one_groq_call_per_round(resume_index):
    rounds = iter([critic_answers(1.0, ["specific_evidence"]), critic_answers(3.5)])
    current = {"fn": next(rounds)}

    class SwitchingJev(FakeJev):
        def ask(self, state, questions):
            res = super().ask(state, questions)
            current["fn"] = next(rounds, current["fn"])
            return res

    jev = SwitchingJev(lambda k, q, s: current["fn"](k, q, s))
    llm = FakeLLM(f"<letter>{letter()}</letter>", f"<letter>{letter(extra='Cut deploy time by 40%.')}</letter>")
    text, report, _ = write_cover_letter(llm, JOB, JOB_TEXT, RESUME, RESUME, resume_index, 1, jev=jev)
    assert len(llm.calls) == 2  # the writer only; no Groq critic calls
    assert report.approved and report.rounds == 2 and [h.score for h in report.history] == [4, 9]
    assert "Cite 2-3 specific achievements" in llm.calls[1][0][1]["content"]  # Jev feedback drove the revision
    assert "tools" not in llm.calls[0][1]


def test_hard_lint_still_vetoes_jev_approval(resume_index):
    invented = letter(extra="I grew revenue by 300%.")
    llm = FakeLLM(f"<letter>{invented}</letter>", f"<letter>{letter()}</letter>")
    _, report, _ = write_cover_letter(llm, JOB, JOB_TEXT, RESUME, RESUME, resume_index, 1,
                                      jev=FakeJev(critic_answers(4.0)))
    assert report.history[0].approved is False and "300" in " ".join(report.history[0].issues)
    assert lint_letter(invented, RESUME, JOB_TEXT, JOB.keywords).hard


def test_requirement_with_no_candidate_lines_is_still_judged(resume_index):
    """Regression: a lone "none" option made an invalid Choice and crashed the Jev fit."""
    from app.agents.job_parser import ParsedJob
    job = ParsedJob(title="Chef", must_have=["Sous-vide cooking"], keywords=[])
    jev = FakeJev(lambda k, q, s: {"type": "choice", "choice": "missing", "confidence": 0.9})
    result, _ = score_fit(FakeLLM(), job, resume_index, 1, RESUME, jev)
    assert set(jev.requests[0][1]) == {"M1_match"} and result.requirements[0].match == "missing"


def test_jev_line_probabilities_decide_strong_vs_partial(resume_index):
    from app.agents.job_parser import ParsedJob
    job = ParsedJob(title="Dev", must_have=["Docker", "AWS"], keywords=["Docker", "AWS"])

    def answer(key, q, state):
        if key.endswith("_match"):
            return {"type": "choice", "choice": "strong" if "AWS" in q["instructions"]["requirement"] else "missing",
                    "confidence": 0.9}
        # a line mentions Docker only weakly; AWS clearly
        return {"type": "noul", "noul": 0.45 if q["instructions"]["requirement"] == "Docker" else 0.55}

    result, _ = score_fit(FakeLLM(), job, resume_index, 1, RESUME, FakeJev(answer))
    by_req = {r.requirement: r.match for r in result.requirements}
    assert by_req == {"Docker": "missing",  # overall "missing" is never upgraded by line answers
                      "AWS": "partial"}     # overall "strong", but no line above 0.6 -> partial


def test_fallback_asks_again_only_for_skipped_requirements(resume_index):
    first = json.dumps({"requirements": [{"id": r, "match": "missing"} for r in ["M1", "M2", "M3", "M4"]]})
    second = json.dumps({"requirements": [{"id": "N1", "match": "strong",
                                           "evidence": "Python, FastAPI, PostgreSQL, Docker, AWS, React"},
                                          {"id": "N2", "match": "missing"}]})
    llm = FakeLLM(first, second)
    result, _ = score_fit(llm, JOB, resume_index, 1, RESUME, None)
    followup = llm.calls[1][0][1]["content"]
    assert "N1 (nice): React" in followup and "M1 (must)" not in followup and "ids: N1, N2" in followup
    assert {r.requirement: r.match for r in result.requirements}["React"] == "strong"
