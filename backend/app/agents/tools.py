"""Tools the agents can call. Tools only ever return the *current user's* data.

- search_my_experience: RAG search over the user's resume (Fit Scorer, Tailor, Writer)
- check_ats_coverage:   deterministic keyword coverage of a draft (Tailor)
- verify_claims:        deterministic check for invented numbers/skills (Tailor)
"""
from __future__ import annotations

import re

from app.agents.ats import keyword_coverage, verify_claims
from app.agents.base import Tool
from app.rag.store import ResumeIndex

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "over", "using", "used", "have", "has",
    "was", "were", "are", "our", "your", "their", "its", "per", "via", "also", "led", "etc",
}


def _words(text: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9+#.]+", text.lower()) if len(w) > 2 and w not in _STOPWORDS]


def is_grounded(evidence: str | None, source: str, threshold: float = 0.75) -> bool:
    """True if most meaningful words of `evidence` appear in the user's resume.

    Forgiving enough for light paraphrasing ("Built APIs in FastAPI" vs the exact bullet),
    strict enough to reject invented experience.
    """
    ev = _words(evidence or "")
    if not ev:
        return False
    src = set(_words(source))
    return sum(w.strip(".") in src or w in src for w in ev) / len(ev) >= threshold


def grounded_evidence(evidence: str | None, source: str) -> str | None:
    """Return the parts of `evidence` that are really in the resume, or None.

    Models often stitch quotes together ("Built X; Y listed in skills"). Each fragment is
    checked on its own and only verified fragments are kept.
    """
    if not evidence:
        return None
    if is_grounded(evidence, source):
        return evidence
    fragments = [f.strip(" .") for f in re.split(r"[;|•\n]|\.\s", evidence) if len(f.strip(" .")) > 3]
    kept = [f for f in fragments if is_grounded(f, source)]
    return " … ".join(kept) if kept else None


def check_ats_coverage_tool(keywords: list[str], original: str) -> Tool:
    def check_ats_coverage(draft: str) -> dict:
        cov = keyword_coverage(str(draft), keywords, original)
        return {
            "supported_coverage_percent": cov.supported_percent,
            "covered": cov.covered,
            "missing_you_should_add": cov.missing_supported,
            "missing_do_not_add": cov.missing_unsupported,
            "note": "Only add keywords from 'missing_you_should_add'. The others are not backed by the resume.",
        }

    return Tool(
        name="check_ats_coverage",
        description=(
            "Check which of the job's ATS keywords appear in a resume draft. Pass the FULL draft text. "
            "Returns keywords the candidate truly has but the draft is missing, and keywords that must not be added."
        ),
        parameters={
            "type": "object",
            "properties": {"draft": {"type": "string", "description": "Full resume draft text"}},
            "required": ["draft"],
        },
        fn=check_ats_coverage,
    )


def verify_claims_tool(keywords: list[str], original: str) -> Tool:
    def verify_claims_fn(draft: str) -> dict:
        report = verify_claims(str(draft), original, keywords)
        return {
            "ok": report.ok,
            "issues": [i.model_dump() for i in report.issues],
            "note": "Fix every issue by removing or correcting the claim. Never invent numbers or skills.",
        }

    return Tool(
        name="verify_claims",
        description=(
            "Check a resume draft for claims the original resume doesn't support (invented numbers, "
            "skills the candidate doesn't have). Pass the FULL draft text."
        ),
        parameters={
            "type": "object",
            "properties": {"draft": {"type": "string", "description": "Full resume draft text"}},
            "required": ["draft"],
        },
        fn=verify_claims_fn,
    )


def search_experience_tool(index: ResumeIndex, user_id: int) -> Tool:
    """Create one per agent run: excerpts already returned in this run are referenced, not resent.

    Agents search many times and hit the same resume chunks repeatedly; resending them grew the
    Fit Scorer's history past Groq's 8K-token request limit on a normal two-page resume.
    """
    seen: dict[str, int] = {}

    def search_my_experience(query: str, k: int = 3) -> dict:
        try:
            k = max(1, min(int(k), 5))
        except (TypeError, ValueError):
            k = 3
        hits = index.search(user_id, str(query)[:300], k)
        if not hits:
            return {"results": [], "note": "No matching experience found in the resume."}
        results = []
        for h in hits:
            if h.text in seen:
                results.append({"excerpt": seen[h.text], "text": "(same excerpt as shown earlier)",
                                "relevance": h.score})
            else:
                seen[h.text] = len(seen) + 1
                results.append({"excerpt": seen[h.text], "text": h.text, "relevance": h.score})
        return {"results": results}

    return Tool(
        name="search_my_experience",
        description=(
            "Semantic search over the candidate's own resume. Returns the most relevant resume excerpts. "
            "Use short, specific queries (e.g. 'Kubernetes container orchestration'). "
            "This is the ONLY source of truth about the candidate."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What experience or skill to look for"},
                "k": {"type": "integer", "description": "Number of excerpts (1-5)", "default": 3},
            },
            "required": ["query"],
        },
        fn=search_my_experience,
    )
