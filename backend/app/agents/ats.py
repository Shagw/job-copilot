"""Deterministic checks used by the Resume Tailor and the Cover Letter critic.

These run in plain Python, not in the LLM, so they are fast, free and cannot be
talked out of their verdict:

- keyword_coverage: which job keywords a text contains (ATS-style matching).
- verify_claims:    numbers or job skills in a draft that the original resume
                    doesn't support (i.e. likely invented).
"""
from __future__ import annotations

import re

from pydantic import BaseModel

# Common spellings ATS systems treat as the same skill.
ALIASES: dict[str, list[str]] = {
    "kubernetes": ["k8s"],
    "postgresql": ["postgres"],
    "javascript": ["js"],
    "typescript": ["ts"],
    "ci cd": ["cicd", "continuous integration"],
    "aws": ["amazon web services"],
    "gcp": ["google cloud"],
    "machine learning": ["ml"],
    "rest": ["restful"],
}


def _norm(text: str) -> str:
    """Lowercase, treat - _ / as spaces, collapse whitespace. Keeps + # . (C++, C#, Node.js)."""
    text = re.sub(r"[\-_/–—]", " ", text.lower())
    return re.sub(r"\s+", " ", text).strip()


def _pattern(term: str) -> re.Pattern:
    body = r"\s*".join(re.escape(part) for part in term.split(" "))
    # Word-ish boundaries that respect +/# ("c" must not match "c++"); allow plural s/es.
    return re.compile(rf"(?<![a-z0-9+#]){body}(?:e?s)?(?![a-z0-9+#])")


def has_keyword(text: str, keyword: str) -> bool:
    norm_text, norm_kw = _norm(text), _norm(keyword)
    if not norm_kw:
        return False
    variants = {norm_kw, *ALIASES.get(norm_kw, [])}
    for canonical, alts in ALIASES.items():  # "k8s" in the job should also match "Kubernetes"
        if norm_kw in alts:
            variants |= {canonical, *alts}
    return any(_pattern(v).search(norm_text) for v in variants)


class Coverage(BaseModel):
    total: int
    covered: list[str]
    missing_supported: list[str]    # candidate has it (in original resume) but the draft doesn't show it
    missing_unsupported: list[str]  # candidate has no evidence: must NOT be added
    percent: int                    # covered / all keywords
    supported_percent: int          # covered / keywords the candidate can truthfully claim


def keyword_coverage(text: str, keywords: list[str], original: str) -> Coverage:
    covered, missing_sup, missing_unsup = [], [], []
    supported_total = 0
    for kw in keywords:
        supported = has_keyword(original, kw)
        supported_total += supported
        if has_keyword(text, kw):
            covered.append(kw)
        elif supported:
            missing_sup.append(kw)
        else:
            missing_unsup.append(kw)
    supported_covered = sum(1 for kw in covered if has_keyword(original, kw))
    return Coverage(
        total=len(keywords),
        covered=covered,
        missing_supported=missing_sup,
        missing_unsupported=missing_unsup,
        percent=round(100 * len(covered) / len(keywords)) if keywords else 100,
        supported_percent=round(100 * supported_covered / supported_total) if supported_total else 100,
    )


class ClaimIssue(BaseModel):
    type: str  # unsupported_number | unsupported_skill
    detail: str
    line: str


class ClaimReport(BaseModel):
    ok: bool
    issues: list[ClaimIssue]


_NUMBER = re.compile(r"(?<![\w.])\d+(?:[.,]\d+)?")
# Spelled-out numbers ("five years", "three million") must be checked too, or a model can dodge the
# digit check. "one" is left out: it's mostly a pronoun ("one of the ...").
_NUMBER_WORDS = {
    w: str(n) for n, w in enumerate(
        "zero _ two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen "
        "sixteen seventeen eighteen nineteen twenty".split()
    ) if w != "_"
} | {"thirty": "30", "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70", "eighty": "80",
     "ninety": "90", "hundred": "100", "dozen": "12"}
_WORD = re.compile(r"\b(" + "|".join(_NUMBER_WORDS) + r")\b", re.I)


def numbers_in(text: str) -> set[str]:
    found = {n.replace(",", "") for n in _NUMBER.findall(text)}
    return found | {_NUMBER_WORDS[w.lower()] for w in _WORD.findall(text)}


def verify_claims(draft: str, original: str, keywords: list[str], max_issues: int = 20) -> ClaimReport:
    """Flag numbers and job skills in `draft` that `original` doesn't contain.

    Rewording is fine; new facts are not. Numbers (metrics, years of experience,
    team sizes) are the most common thing LLMs invent in resumes.
    """
    source_numbers = numbers_in(original)
    unsupported_skills = [kw for kw in keywords if not has_keyword(original, kw)]
    issues: list[ClaimIssue] = []
    for line in (ln.strip() for ln in draft.splitlines()):
        if not line:
            continue
        for n in sorted(numbers_in(line) - source_numbers):
            issues.append(ClaimIssue(type="unsupported_number", detail=f"'{n}' does not appear in the original resume",
                                     line=line[:200]))
        for kw in unsupported_skills:
            if has_keyword(line, kw):
                issues.append(ClaimIssue(type="unsupported_skill",
                                         detail=f"'{kw}' is not in the original resume", line=line[:200]))
    return ClaimReport(ok=not issues, issues=issues[:max_issues])


# --- Quantified results -----------------------------------------------------------------------------------
# A "metric" is a number that shows impact: 40%, $2M, 3x, 10,000, 1M+. Years (2021), phone numbers and dates
# aren't metrics. Tailoring should never lose one: they are what recruiters look for first.
_METRIC = re.compile(
    r"(?<![\w.])(?:[$₹€£]\s?)?\d[\d,]*(?:\.\d+)?\s?(?:%|x\b|[kKMB]\+?(?![a-zA-Z])|\+|(?:million|billion|lakh|crore)s?\b)?",
)
_YEAR = re.compile(r"^(?:19|20)\d{2}$")
_UNIT = {"%": "%", "x": "x", "k": "k", "m": "m", "b": "b", "million": "m", "billion": "b", "lakh": "lakh", "crore": "crore"}


def _metric_key(token: str) -> tuple[str, str]:
    """("40", "%") for "40%", ("2", "m") for "$2M", ("10000", "") for "10,000"."""
    m = re.match(r"[$₹€£]?\s?([\d,]+(?:\.\d+)?)\s?([a-zA-Z%]*)", token)
    if not m:
        return "", ""
    unit = m.group(2).lower().rstrip("s")
    return m.group(1).replace(",", ""), _UNIT.get(unit, "")


def metrics_in(text: str) -> dict[str, str]:
    """{key: as written} for impact numbers, e.g. {"40%": "40%", "2m": "$2M", "10000": "10,000"}."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if "@" in line or "http" in line or re.search(r"\+?\d[\d\s-]{8,}\d", line):
            continue  # contact lines: phone numbers, emails, links
        for m in _METRIC.finditer(line):
            token = m.group(0).strip()
            value, unit = _metric_key(token)
            if not value or (_YEAR.match(value) and not unit):
                continue
            has_unit = bool(unit) or token.endswith("+") or token[0] in "$₹€£"
            if has_unit or (value.isdigit() and int(value) >= 10):
                out.setdefault(value + unit, token)
    return out


def dropped_metrics(draft: str, reference: str) -> list[str]:
    """Quantified results in `reference` that no longer appear anywhere in `draft`.

    "40%" also counts as kept if "40" is still there (e.g. "40 percent"); small numbers need their unit
    ("1M" isn't kept by a stray "1").
    """
    kept = set(metrics_in(draft))
    plain = {n for n in numbers_in(draft) if n.replace(".", "").isdigit() and float(n) >= 10}
    return [shown for key, shown in metrics_in(reference).items()
            if key not in kept and re.sub(r"[^\d.]", "", key) not in plain]


# --- Where keywords live ----------------------------------------------------------------------------------
_DATED = re.compile(r"(?:19|20)\d{2}")


def _is_section(line: str) -> bool:
    t = line.strip().rstrip(":")
    return bool(t) and t.isupper() and len(t.split()) <= 5 and not t.startswith(("-", "•"))


def keyword_places(text: str, keywords: list[str]) -> dict[str, str]:
    """Where each keyword first appears: "SUMMARY", "SKILLS", "EXPERIENCE: Acme Corp - Backend Engineer"."""
    places: dict[str, str] = {}
    section, role = "TOP", ""
    for line in text.splitlines():
        t = line.strip()
        if not t:
            continue
        if _is_section(t):
            section, role = t.rstrip(":").title(), ""
            continue
        if _DATED.search(t) and not t.startswith(("-", "•")) and len(t) < 120:
            role = t.split("|")[0].strip()[:60]
        for kw in keywords:
            if kw not in places and has_keyword(t, kw):
                places[kw] = f"{section}: {role}" if role and section.lower().startswith(("experience", "work", "employment", "project")) else section
    return places
