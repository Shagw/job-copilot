"""Quantified-result protection, keyword placement and the recruiter check."""
from app.agents.ats import dropped_metrics, keyword_places, metrics_in
from app.agents.recruiter import code_checks, jev_checks
from app.agents.resume_tailor import tailor_resume
from tests.test_agents import FakeLLM
from tests.test_jev import FakeJev
from tests.test_resume import RESUME
from tests.test_tailor_cover import JOB, TAILORED

METRIC_RESUME = """Alice | alice@example.com | +91 98765 43210
SUMMARY
Engineer with 5+ years.
EXPERIENCE
Acme Corp - Backend Engineer (2021-2024)
- Scaled APIs from 1,000 to 10,000 daily requests, serving 1M+ users
- Cut deploy time by 40% and costs by $2M with 3x faster builds"""


def test_metrics_skip_years_and_contact_lines():
    found = metrics_in(METRIC_RESUME)
    assert set(found.values()) == {"5+", "1,000", "10,000", "1M+", "40%", "$2M", "3x"}
    assert "2021" not in " ".join(found) and "98765" not in " ".join(found)


def test_dropped_metrics_are_reported_but_rewording_is_fine():
    assert dropped_metrics(METRIC_RESUME.replace("40%", "40 percent"), METRIC_RESUME) == []
    assert dropped_metrics(METRIC_RESUME.replace(", serving 1M+ users", ""), METRIC_RESUME) == ["1M+"]


def test_keyword_places_name_section_and_role():
    places = keyword_places(METRIC_RESUME + "\nSKILLS\nPython, React", ["APIs", "Python", "Engineer"])
    assert places == {"Engineer": "Summary", "APIs": "Experience: Acme Corp - Backend Engineer (2021-2024)",
                      "Python": "Skills"}


def _by_id(checks):
    return {c.id: c for c in checks}


def test_recruiter_code_checks_pass_for_a_good_resume():
    checks = _by_id(code_checks(TAILORED, JOB, RESUME))
    assert checks["measurable_impact"].ok and checks["length"].ok and checks["no_stuffing"].ok


def test_recruiter_code_checks_catch_buried_skills_lost_impact_and_stuffing():
    buried = "Alice\n\nEXPERIENCE\n- Did backend work\n- Did more backend work\n\n" + "\n" * 5 + \
             "SKILLS\nPython, FastAPI, AWS, React\n" + "Python " * 6
    checks = _by_id(code_checks(buried, JOB, RESUME))
    assert not checks["skills_near_top"].ok and "React" in checks["skills_near_top"].detail
    assert not checks["measurable_impact"].ok  # RESUME has quantified bullets; this has none
    assert not checks["no_stuffing"].ok and "Python" in checks["no_stuffing"].detail


def test_stuffing_counts_whole_keywords_relative_to_the_original():
    from app.agents.job_parser import ParsedJob

    job = ParsedJob(title="Dev", keywords=["Git"])
    text = "- Used Git daily\n- GitHub Actions, GitLab CI, GitHub API, GitHub Pages, GitLab runners"
    assert _by_id(code_checks(text, job, text))["no_stuffing"].ok  # GitHub/GitLab aren't "Git"
    busy = "\n".join(["- Used Git"] * 7)
    assert _by_id(code_checks(busy, job, busy))["no_stuffing"].ok  # no more than the original already had
    assert not _by_id(code_checks(busy, job, "- Used Git"))["no_stuffing"].ok


def test_recruiter_jev_answers_map_to_fixed_feedback():
    def answer(key, q, state):
        return {"type": "noul", "noul": {"relevant_fast": 0.2, "specific_summary": 0.9}.get(key, 0.1)}

    trace = []
    checks = _by_id(jev_checks(FakeJev(answer), TAILORED, JOB, trace))
    assert not checks["relevant_fast"].ok and "summary name the target role" in checks["relevant_fast"].detail
    assert not checks["specific_summary"].ok
    assert checks["natural_keywords"].ok and checks["concise_bullets"].ok and checks["clear_current_role"].ok
    assert all(c.by == "jev" for c in checks.values()) and trace[0]["tool"] == "Jev: recruiter check"


def test_recruiter_jev_down_means_code_checks_only():
    assert jev_checks(FakeJev(fail=True), TAILORED, JOB, []) == []


def test_tailor_puts_lost_metrics_back_and_reports_what_was_added_where(resume_index):
    lost = TAILORED.replace(", cut deploy time by 40%", "")
    llm = FakeLLM(f"<resume>\n{lost}\n</resume>", f"<resume>\n{TAILORED}\n</resume>", f"<resume>\n{TAILORED}\n</resume>")
    text, report, _ = tailor_resume(llm, JOB, RESUME, resume_index, 1)
    assert "quantified results from the resume were lost" in llm.calls[1][0][-1]["content"]
    assert "40%" in llm.calls[1][0][-1]["content"]
    assert text == TAILORED and report.metrics_dropped == []
    added = {k.keyword: k.where for k in report.keywords_added}
    assert all(kw in report.coverage_after.covered for kw in added)
    assert {c.id for c in report.recruiter} >= {"skills_near_top", "measurable_impact", "length", "no_stuffing"}


def test_tailor_prompt_has_writing_rules_and_keyword_places(resume_index):
    llm = FakeLLM(*[f"<resume>\n{TAILORED}\n</resume>"] * 3)
    tailor_resume(llm, JOB, RESUME, resume_index, 1)
    system, user = llm.calls[0][0][0]["content"], llm.calls[0][0][1]["content"]
    assert "KEEP EVERY QUANTIFIED RESULT" in system and "strong action verb" in system
    assert "Skills: group into categories" in system
    assert "Where each is backed in the source" in user and "Python -> " in user
