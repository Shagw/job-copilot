"""Recruiter check: would a human screening this resume for 15 seconds keep reading?

Code checks always run (free, deterministic): top skills near the top, measurable impact, length,
keyword stuffing. With Jev, one extra call adds judgment questions code can't answer: is the fit obvious
from the top, is the summary specific, does anything look like a rejection risk. Failed checks go into the
Tailor's fix round and into the report. No Groq tokens are used here.
"""
from __future__ import annotations

import logging

from pydantic import BaseModel

from app.agents.ats import _norm, _pattern, has_keyword, metrics_in
from app.agents.job_parser import ParsedJob
from app.llm.jev_client import JevUnavailable, noul

log = logging.getLogger("app.agents.recruiter")

TOP_FRACTION = 1 / 3        # "near the top" = first third of the resume
TOP_KEYWORDS = 8            # the job's first keywords are the ones that matter most
MIN_IMPACT_BULLETS = 3      # bullets with a quantified result
MAX_KEYWORD_REPEATS = 5     # more than this reads as stuffing
JEV_FAIL_ABOVE = 0.6        # P(problem) above this -> failed check


class RecruiterCheck(BaseModel):
    id: str
    label: str
    ok: bool
    detail: str | None = None
    by: str = "code"  # "code" or "jev"


# id -> (question for Jev, True means a problem?, label, what to do when it fails)
JEV_CHECKS = {
    "relevant_fast": ("Reading only `top` (the first lines of the resume), would a recruiter hiring for `job` see "
                      "within 15 seconds that this candidate is relevant?", False,
                      "Relevant within 15 seconds",
                      "Make the summary name the target role and the 2-3 most important job skills you have"),
    "specific_summary": ("Does the summary in `top` rely on generic claims or buzzwords (e.g. 'passionate', "
                         "'results-driven', 'team player') instead of specific skills and results?", True,
                         "Specific, not generic",
                         "Replace generic phrases in the summary with specific skills and a quantified result"),
    "natural_keywords": ("Are keywords in `resume` crammed in unnaturally, such as long lists repeated across "
                         "sections or keywords that don't fit the sentence they're in?", True,
                         "Keywords read naturally",
                         "Remove repeated or forced keywords; keep each where it describes real work"),
    # Specific risks instead of one vague "would it be rejected?" (which Jev answers ~0.8 for most resumes).
    "clear_current_role": ("Is the candidate's current or most recent job title and employer unclear in `resume`?",
                           True, "Current role is clear",
                           "Make the most recent title, employer and dates easy to spot at the top of experience"),
    "concise_bullets": ("Does `resume` contain bullets that are walls of text (about 3+ lines, or several ideas "
                        "in one bullet)?", True, "Bullets are concise",
                        "Split long bullets: one idea each, action verb first, result at the end"),
    "clean_formatting": ("Does `resume` have formatting problems a recruiter would notice, such as broken lines, "
                         "stray symbols or inconsistent headings?", True, "Clean formatting",
                         "Fix broken lines, stray symbols and inconsistent headings"),
}


def _count(text: str, keyword: str) -> int:
    """Whole-keyword occurrences ("Git" doesn't count "GitHub" or "GitLab")."""
    return len(_pattern(_norm(keyword)).findall(_norm(text)))


def _bullets(text: str) -> list[str]:
    return [ln.strip() for ln in text.splitlines() if ln.strip().startswith(("-", "•", "*"))]


def code_checks(resume: str, job: ParsedJob, original: str) -> list[RecruiterCheck]:
    out: list[RecruiterCheck] = []
    keywords = (job.keywords or job.must_have)[:TOP_KEYWORDS]
    have = [kw for kw in keywords if has_keyword(resume, kw)]
    if have:
        top = resume[: max(1, int(len(resume) * TOP_FRACTION))]
        low = [kw for kw in have if not has_keyword(top, kw)]
        ok = len(low) <= len(have) // 3
        out.append(RecruiterCheck(
            id="skills_near_top", label="Top job skills visible near the top", ok=ok,
            detail=None if ok else "Move these into the summary or skills near the top: " + ", ".join(low)))

    impact = [b for b in _bullets(resume) if metrics_in(b)]
    need = min(MIN_IMPACT_BULLETS, len([b for b in _bullets(original) if metrics_in(b)]))  # can't beat the original
    ok = len(impact) >= need
    out.append(RecruiterCheck(
        id="measurable_impact", label="Shows measurable impact", ok=ok,
        detail=None if ok else f"Only {len(impact)} bullet(s) show a quantified result; lead bullets with your numbers"))

    words, limit = len(resume.split()), max(700, int(len(original.split()) * 1.2))
    ok = words <= limit
    out.append(RecruiterCheck(id="length", label="Concise (about the original length)", ok=ok,
                              detail=None if ok else f"{words} words; trim to under {limit}"))

    stuffed = [kw for kw in (job.keywords or [])
               if _count(resume, kw) > max(MAX_KEYWORD_REPEATS, int(_count(original, kw) * 1.5))]
    out.append(RecruiterCheck(id="no_stuffing", label="No keyword stuffing", ok=not stuffed,
                              detail=None if not stuffed else "Repeated too often: " + ", ".join(stuffed)))
    return out


def jev_checks(jev, resume: str, job: ParsedJob, trace: list[dict]) -> list[RecruiterCheck]:
    if jev is None:
        return []
    top = "\n".join([ln for ln in resume.splitlines() if ln.strip()][:15])
    job_text = f"{job.title or ''} {('at ' + job.company) if job.company else ''}\nMust have: " + "; ".join(job.must_have)
    questions = {cid: noul({"question": q}) for cid, (q, _, _, _) in JEV_CHECKS.items()}
    try:
        res = jev.ask({"job": job_text[:4000], "top": top, "resume": resume[:20_000]}, questions)
    except JevUnavailable as e:
        log.info("Recruiter check: Jev skipped (%s)", e)
        return []
    trace.append({**res.trace("Jev: recruiter check"), "step": len(trace) + 1, "type": "tool"})
    out = []
    for cid, (_, true_is_bad, label, fix) in JEV_CHECKS.items():
        p = res.noul(cid, 0.0 if true_is_bad else 1.0)
        bad = p > JEV_FAIL_ABOVE if true_is_bad else p < 1 - JEV_FAIL_ABOVE
        out.append(RecruiterCheck(id=cid, label=label, ok=not bad, detail=fix if bad else None, by="jev"))
    return out


def recruiter_check(jev, resume: str, job: ParsedJob, original: str, trace: list[dict]) -> list[RecruiterCheck]:
    return code_checks(resume, job, original) + jev_checks(jev, resume, job, trace)
