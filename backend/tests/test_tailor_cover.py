import json

import pytest

from app.agents.ats import has_keyword, keyword_coverage, verify_claims
from app.agents.base import AgentError, extract_tagged
from app.agents.cover_critic import lint_letter
from app.agents.cover_writer import MAX_ROUNDS, write_cover_letter
from app.agents.job_parser import ParsedJob
from app.agents.resume_tailor import tailor_resume
from tests.test_agents import FIT_FINAL, JOB_TEXT, PARSED, FakeLLM, call, start_session, use_llm  # noqa: F401
from tests.test_resume import RESUME

JOB = ParsedJob(**PARSED)  # keywords: Python FastAPI Django Kubernetes AWS React Terraform

TAILORED = """Alice Example
Software Engineer | alice@example.com

SUMMARY
Backend engineer building Python and FastAPI services on AWS.

EXPERIENCE
Acme Corp - Backend Engineer (2021-2024)
- Built REST APIs in Python with FastAPI serving 2M requests per day
- Deployed services on Docker and AWS ECS, cut deploy time by 40%
- Set up CI/CD pipelines with GitHub Actions

SKILLS
Python, FastAPI, AWS, Docker, PostgreSQL, React"""

BAD_DRAFT = TAILORED.replace("serving 2M", "serving 9M") + "\n- Ran Kubernetes clusters in production"


def letter(words=300, extra=""):
    body = " ".join(["I built REST APIs in Python with FastAPI at Acme Corp."] * (words // 11))
    return f"Dear Initech team,\n\n{body}\n{extra}\n\nAlice Example"


def critic(score=9, approved=True, issues=(), suggestions=()):
    return json.dumps({"score": score, "approved": approved, "issues": list(issues), "suggestions": list(suggestions)})


# ---------- ATS keyword matching ----------

@pytest.mark.parametrize(
    "text,kw,expected",
    [
        ("Built CI/CD pipelines", "CI/CD", True),
        ("ci-cd", "CI/CD", True),
        ("Wrote C++ code", "C", False),
        ("Wrote C++ code", "C++", True),
        ("C# developer", "C#", True),
        ("Node.js services.", "Node.js", True),
        ("REST APIs", "API", True),
        ("Ran k8s clusters", "Kubernetes", True),
        ("Kubernetes", "K8s", True),
        ("Postgres DB", "PostgreSQL", True),
        ("JavaScript", "Java", False),
        ("rapid growth", "API", False),
    ],
)
def test_has_keyword(text, kw, expected):
    assert has_keyword(text, kw) is expected


def test_coverage_separates_supported_from_unsupported():
    draft = "Python developer"  # dropped FastAPI/AWS/React which the candidate does have
    cov = keyword_coverage(draft, JOB.keywords, RESUME)
    assert cov.covered == ["Python"]
    assert cov.missing_supported == ["FastAPI", "AWS", "React"]
    assert cov.missing_unsupported == ["Django", "Kubernetes", "Terraform"]
    assert cov.supported_percent == 25 and cov.percent == round(100 / 7)


def test_verify_claims_passes_honest_rewrite():
    assert verify_claims(TAILORED, RESUME, JOB.keywords).ok


def test_verify_claims_flags_invented_numbers_and_skills():
    report = verify_claims(BAD_DRAFT, RESUME, JOB.keywords)
    kinds = {(i.type, i.detail.split("'")[1]) for i in report.issues}
    assert not report.ok
    assert ("unsupported_number", "9") in kinds
    assert ("unsupported_skill", "Kubernetes") in kinds


def test_extract_tagged():
    assert extract_tagged("<think>x</think><resume>\nA\n</resume>", "resume") == "A"
    assert extract_tagged("<resume>no closing tag", "resume") == "no closing tag"
    assert extract_tagged("nothing here", "resume") is None


# ---------- Resume Tailor agent ----------

def test_tailor_agent_uses_tools_and_reports(resume_index):
    resume_index.index_resume(1, 1, RESUME)
    llm = FakeLLM(
        [call("check_ats_coverage", {"draft": BAD_DRAFT}, "c1"), call("verify_claims", {"draft": BAD_DRAFT}, "c2")],
        f"<resume>\n{TAILORED}\n</resume>\n<changes>\n- Added summary\n- Moved AWS up\n</changes>",
    )
    text, report, run = tailor_resume(llm, JOB, RESUME, resume_index, 1)

    assert text == TAILORED
    assert report.changes == ["Added summary", "Moved AWS up"]
    assert report.claims.ok and report.coverage_after.supported_percent == 100 and report.target_met
    # The agent saw the real tool results for its bad draft.
    observations = [json.loads(m["content"]) for m in llm.calls[1][0] if m["role"] == "tool"]
    assert observations[0]["missing_do_not_add"] == ["Django", "Terraform"]
    assert "Kubernetes" in observations[0]["covered"]  # present in the bad draft...
    assert observations[1]["ok"] is False  # ...and flagged as unsupported by verify_claims
    # Full drafts are trimmed in the stored trace.
    assert all(len(t["args"].get("draft", "")) <= 301 for t in run.trace if t["type"] == "tool")


def test_tailor_report_catches_what_the_agent_missed(resume_index):
    llm = FakeLLM(f"<resume>\n{BAD_DRAFT}\n</resume>")
    _, report, _ = tailor_resume(llm, JOB, RESUME, resume_index, 1)
    assert not report.claims.ok and not report.target_met


def test_tailor_prompt_contains_gaps_and_user_notes(resume_index):
    from app.agents.fit_scorer import FitResult

    fit = FitResult(score=50, verdict="Moderate fit", requirements=[], gaps=["Kubernetes"], advice=["Lead with APIs"])
    llm = FakeLLM(f"<resume>\n{TAILORED}\n</resume>")
    tailor_resume(llm, JOB, RESUME, resume_index, 1, fit, "Emphasise leadership")
    user_msg = llm.calls[0][0][1]["content"]
    assert "do NOT claim these): Kubernetes" in user_msg
    assert "Emphasise leadership" in user_msg and "<original_resume>" in user_msg


def test_tailor_without_tags_falls_back_to_content(resume_index):
    text, _, _ = tailor_resume(FakeLLM(TAILORED), JOB, RESUME, resume_index, 1)
    assert text == TAILORED


def test_empty_final_answer_gets_one_nudge(resume_index):
    llm = FakeLLM("", f"<resume>\n{TAILORED}\n</resume>")
    text, _, run = tailor_resume(llm, JOB, RESUME, resume_index, 1)
    assert text == TAILORED and run.trace[-1]["retry"] is True
    assert llm.calls[1][1]["tool_choice"] == "none" and "required format" in llm.calls[1][0][-1]["content"]


def test_tailor_empty_output_raises(resume_index):
    with pytest.raises(AgentError):
        tailor_resume(FakeLLM("sorry", "still sorry", "sorry again"), JOB, RESUME, resume_index, 1)


# ---------- Cover letter lint ----------

def test_lint_hard_failures():
    lint = lint_letter("Dear [Hiring Manager],\nI grew revenue 300%.", RESUME, JOB_TEXT, JOB.keywords)
    joined = " ".join(lint.hard)
    assert "Too short" in joined and "[Hiring Manager]" in joined and "300" in joined


def test_lint_allows_numbers_from_resume_or_job():
    lint = lint_letter(letter(extra="Serving 2M requests; your 5+ years requirement."), RESUME, JOB_TEXT, JOB.keywords)
    assert lint.hard == []


def test_lint_soft_hints():
    lint = lint_letter(letter(extra="I am a team player eager to learn Kubernetes."), RESUME, JOB_TEXT, JOB.keywords)
    assert lint.hard == []
    assert any("team player" in s for s in lint.soft) and any("Kubernetes" in s for s in lint.soft)


# ---------- Writer ⇄ Critic loop ----------

def run_letter(llm, resume_index, **kw):
    return write_cover_letter(llm, JOB, JOB_TEXT, RESUME, RESUME, resume_index, 1, **kw)


def test_approved_first_round(resume_index):
    llm = FakeLLM(f"<letter>\n{letter()}\n</letter>", critic(9, True))
    text, report, trace = run_letter(llm, resume_index)
    assert report.approved and report.rounds == 1 and report.score == 9 and report.remaining_issues == []
    assert text.startswith("Dear Initech team") and len(trace) == 1


def test_critic_feedback_drives_revision(resume_index):
    llm = FakeLLM(
        f"<letter>\n{letter()}\n</letter>",
        critic(5, False, ["Too generic"], ["Mention the 40% deploy-time cut"]),
        f"<letter>\n{letter(extra='Cut deploy time by 40% on AWS ECS.')}\n</letter>",
        critic(9, True),
    )
    text, report, trace = run_letter(llm, resume_index)
    assert report.approved and report.rounds == 2 and "40%" in text
    revision_prompt = llm.calls[2][0][1]["content"]
    assert "Your previous draft" in revision_prompt
    assert "Too generic" in revision_prompt and "40% deploy-time cut" in revision_prompt
    assert [h.score for h in report.history] == [5, 9]


def test_code_checks_override_critic_approval(resume_index):
    invented = letter(extra="I grew revenue by 300%.")
    llm = FakeLLM(f"<letter>{invented}</letter>", critic(9, True), f"<letter>{letter()}</letter>", critic(9, True))
    _, report, _ = run_letter(llm, resume_index)
    assert report.history[0].approved is False and "300" in " ".join(report.history[0].issues)
    assert report.approved and report.rounds == 2


def test_stops_after_max_rounds_and_returns_last_draft(resume_index):
    replies = []
    for i in range(MAX_ROUNDS):
        replies += [f"<letter>{letter()} draft{i}</letter>", critic(4, False, ["Still weak"])]
    text, report, _ = run_letter(FakeLLM(*replies), resume_index)
    assert not report.approved and report.rounds == MAX_ROUNDS
    assert text.endswith(f"draft{MAX_ROUNDS - 1}") and report.remaining_issues == ["Still weak"]


def test_writer_gets_candidate_notes(resume_index):
    llm = FakeLLM(f"<letter>{letter()}</letter>", critic())
    run_letter(llm, resume_index, instructions="I love Initech's open-source work")
    assert "I love Initech's open-source work" in llm.calls[0][0][1]["content"]


def test_empty_first_draft_raises(resume_index):
    with pytest.raises(AgentError):
        run_letter(FakeLLM("<letter>hi</letter>", "<letter>hi</letter>", "<letter>hi</letter>"), resume_index)


# ---------- endpoints ----------

def upload_resume(client):
    client.post("/resume", files={"file": ("cv.txt", RESUME.encode(), "text/plain")})


def test_full_flow_parse_fit_tailor_letter(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm(FIT_FINAL)
    assert logged_in.post(f"/sessions/{s['id']}/fit").json()["current_step"] == "scored"

    use_llm(f"<resume>\n{TAILORED}\n</resume>\n<changes>\n- Added summary\n</changes>")
    r = logged_in.post(f"/sessions/{s['id']}/tailor", json={"instructions": "Keep it to one page"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_step"] == "tailored" and body["tailored_resume"] == TAILORED
    assert body["tailor_report"]["target_met"] is True and "tailor" in body["agent_trace"]

    edited = TAILORED + "\n\nINTERESTS\nOpen-source contributor"
    llm = use_llm(f"<letter>{letter()}</letter>", critic())
    r = logged_in.post(f"/sessions/{s['id']}/cover-letter", json={"tailored_resume": edited})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current_step"] == "done" and body["cover_letter"].startswith("Dear")
    assert body["tailored_resume"] == edited  # the user's edit was saved
    assert "Open-source contributor" in llm.calls[0][0][1]["content"]  # and used by the writer
    assert body["cover_letter_report"]["approved"] is True
    assert set(body["agent_trace"]) == {"fit", "tailor", "cover_letter"}


def test_tailor_requires_resume(logged_in, use_llm, resume_index):  # noqa: F811
    s = start_session(logged_in, use_llm)
    use_llm()
    assert logged_in.post(f"/sessions/{s['id']}/tailor").status_code == 400
    assert logged_in.post(f"/sessions/{s['id']}/cover-letter").status_code == 400


def test_rerunning_tailor_does_not_rewind_step(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm(f"<letter>{letter()}</letter>", critic())
    assert logged_in.post(f"/sessions/{s['id']}/cover-letter").json()["current_step"] == "done"
    use_llm(f"<resume>{TAILORED}</resume>")
    assert logged_in.post(f"/sessions/{s['id']}/tailor").json()["current_step"] == "done"


def test_cannot_tailor_someone_elses_session(client, outbox, use_llm, resume_index):  # noqa: F811
    from tests.conftest import make_user

    make_user(client, outbox, stay_logged_in=True)
    s = start_session(client, use_llm)
    client.post("/auth/logout")
    make_user(client, outbox, email="bob@example.com", stay_logged_in=True)
    upload_resume(client)
    use_llm()
    assert client.post(f"/sessions/{s['id']}/tailor").status_code == 404
    assert client.post(f"/sessions/{s['id']}/cover-letter").status_code == 404


def test_request_validation(logged_in, use_llm, resume_index):  # noqa: F811
    upload_resume(logged_in)
    s = start_session(logged_in, use_llm)
    use_llm()
    assert logged_in.post(f"/sessions/{s['id']}/tailor", json={"instructions": "x" * 1001}).status_code == 422
    assert logged_in.post(f"/sessions/{s['id']}/cover-letter", json={"tailored_resume": "short"}).status_code == 422


# ---------- found in the real-browser E2E run ----------

def test_spelled_out_numbers_are_checked():
    from app.agents.ats import numbers_in

    assert numbers_in("five years of Python and three million requests") == {"5", "3"}
    assert numbers_in("one of the team") == set()  # "one" is usually a pronoun
    lint = lint_letter(letter(extra="I bring seven years of Python experience."), RESUME, JOB_TEXT, JOB.keywords)
    assert any("7" in h for h in lint.hard)
    report = verify_claims(TAILORED + "\n- Nine years of backend work", RESUME, JOB.keywords)
    assert any(i.detail.startswith("'9'") for i in report.issues)


def test_fit_drops_strengths_with_invented_totals():
    from app.agents.fit_scorer import _LlmFit, build_result

    llm_fit = _LlmFit.model_validate({
        "requirements": [],
        "strengths": ["7+ years of Python development", "FastAPI APIs serving 2M requests per day"],
        "advice": ["State your total experience (e.g., 7 years)", "Lead with the 5+ years requirement"],
    })
    result = build_result(JOB, llm_fit, RESUME)
    assert result.strengths == ["FastAPI APIs serving 2M requests per day"]  # 2M is in the resume
    assert result.advice == ["Lead with the 5+ years requirement"]  # 5 is in the job posting


# ---------- tool calls written as text (seen live: qwen3 on Groq) ----------

QWEN_CALL = ("<tool_call>\n<function=verify_claims>\n<parameter=draft>\nAlice Example\n- Built REST APIs in Python\n"
             "</parameter>\n</function>\n</tool_call>")


def test_text_tool_calls_are_parsed():
    from app.agents.base import strip_tool_markup, text_tool_calls

    assert text_tool_calls(QWEN_CALL) == [("verify_claims", json.dumps({"draft": "Alice Example\n- Built REST APIs in Python"}))]
    hermes = '<tool_call>{"name": "search_my_experience", "arguments": {"query": "AWS"}}</tool_call>'
    assert text_tool_calls(hermes) == [("search_my_experience", '{"query": "AWS"}')]
    two = QWEN_CALL + "\n" + QWEN_CALL.replace("verify_claims", "check_ats_coverage")
    assert [n for n, _ in text_tool_calls(two)] == ["verify_claims", "check_ats_coverage"]
    assert text_tool_calls("A normal resume with <b>no</b> calls") == []
    assert strip_tool_markup("Thinking.\n" + QWEN_CALL) == "Thinking."


def test_agent_runs_text_tool_calls_instead_of_returning_them(resume_index):
    llm = FakeLLM(QWEN_CALL, f"<resume>\n{TAILORED}\n</resume>\n<changes>\n- Reordered\n</changes>")
    text, report, run = tailor_resume(llm, JOB, RESUME, resume_index, 1)
    assert text == TAILORED
    tool_steps = [s for s in run.trace if s["type"] == "tool"]
    assert tool_steps[0]["tool"] == "verify_claims" and tool_steps[0]["text_call"] is True
    tool_msg = next(m for m in llm.calls[1][0] if m["role"] == "tool")
    assert '"ok"' in tool_msg["content"]  # the tool really ran and its result went back to the model


def test_final_answer_that_is_only_tool_markup_is_retried_then_rejected(resume_index):
    """The live bug: the forced final answer was tool-call text and got saved as the resume."""
    with pytest.raises(AgentError):
        tailor_resume(FakeLLM("sorry", QWEN_CALL, QWEN_CALL, QWEN_CALL), JOB, RESUME, resume_index, 1)
    llm = FakeLLM("sorry", QWEN_CALL, f"<resume>\n{TAILORED}\n</resume>")
    text, _, _ = tailor_resume(llm, JOB, RESUME, resume_index, 1)
    assert text == TAILORED and "<tool_call>" not in text
