"""Fit long job postings / resumes into the LLM's per-request token budget, automatically.

Deterministic and order-preserving, in three passes (each only if still too long):
1. Clean: collapse whitespace, drop blank runs and exact duplicate lines (common in scraped pages).
2. Drop boilerplate: EEO/legal text, cookie/privacy banners, "share this job", footers.
3. Keep the most useful lines: headings, dated role lines, requirement-style lines and lines that
   mention the job's keywords score highest; the rest are dropped. Original order is kept.

The caller reports what was dropped, so the user always knows the AI saw a shortened version.
Code-side checks (grounding, claims, ATS coverage) still use the full original text.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.agents.ats import has_keyword

MAX_LINE_CHARS = 1000

_BOILERPLATE = re.compile(
    r"equal (employment )?opportunit|\beeo\b|affirmative action|without regard to|protected veteran|"
    r"reasonable accommodation|e-verify|\bcookies?\b|privacy (policy|notice)|terms of (use|service)|"
    r"all rights reserved|©|share (this )?job|follow us|subscribe|sign in|log in|create alert|"
    r"similar jobs|back to (search|jobs)|apply (now|for this job)|powered by|report (this )?job",
    re.I,
)
_CUES = re.compile(
    r"requir|qualif|experience|skills?|must|proficien|knowledge|familiar|years?|degree|responsib|"
    r"you will|you'll|what you|nice to have|preferred|bonus|plus|about (the )?(role|job|team)|"
    r"built|designed|led|developed|implemented|managed|improved|reduced|increased",
    re.I,
)
_YEARS = re.compile(r"\b(19|20)\d{2}\b")


@dataclass
class Shortened:
    text: str
    original_chars: int
    removed_lines: int = 0

    @property
    def was_shortened(self) -> bool:
        return self.removed_lines > 0 or len(self.text) < self.original_chars


def _clean(text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = " ".join(raw.split())[:MAX_LINE_CHARS]
        if not line:
            if out and out[-1]:
                out.append("")
            continue
        key = line.lower()
        if key in seen and len(line) > 3:
            continue
        seen.add(key)
        out.append(line)
    return out


def _is_heading(line: str) -> bool:
    return len(line) <= 60 and (line.isupper() or line.endswith(":") or line.startswith("#"))


def _score(i: int, line: str, keywords: list[str]) -> float:
    if not line:
        return -1  # blank lines are re-added between kept lines as needed
    s = 0.0
    if i < 8:
        s += 4  # title, company, name, contact
    if _is_heading(line):
        s += 3
    if _YEARS.search(line):
        s += 2  # dated role lines anchor years-of-experience checks
    if _CUES.search(line):
        s += 2
    if line.lstrip().startswith(("-", "•", "*", "–")):
        s += 1
    s += min(3, sum(1 for kw in keywords if has_keyword(line, kw)))
    if _BOILERPLATE.search(line):
        s -= 6
    return s


def _join(lines: list[str]) -> str:
    return "\n".join(lines).strip()


def shorten(text: str, max_chars: int, keywords: list[str] | None = None) -> Shortened:
    original = text or ""
    lines = _clean(original)
    total = len([ln for ln in original.splitlines() if ln.strip()])

    def result(kept: list[str]) -> Shortened:
        kept_count = len([ln for ln in kept if ln])
        return Shortened(_join(kept), len(original.strip()), max(0, total - kept_count))

    if len(_join(lines)) <= max_chars:
        return result(lines)

    no_boiler = [ln for ln in lines if not (ln and _BOILERPLATE.search(ln) and len(ln) < 400)]
    if len(_join(no_boiler)) <= max_chars:
        return result(no_boiler)

    kws = keywords or []
    ranked = sorted((i for i, ln in enumerate(no_boiler) if ln),
                    key=lambda i: (-_score(i, no_boiler[i], kws), i))
    keep: set[int] = set()
    size = 0
    for i in ranked:
        cost = len(no_boiler[i]) + 1
        if size + cost > max_chars:
            continue  # a shorter, lower-ranked line may still fit
        keep.add(i)
        size += cost
    kept = [no_boiler[i] for i in sorted(keep)]
    return result(kept)


def char_budget(llm, output_tokens: int, overhead_chars: int, copies: float = 1) -> int:
    """Characters of input text that fit one request.

    Mirrors groq_client.estimate_tokens (~3 chars/token, pessimistic) with a 10% margin for JSON
    escaping. `copies`: how many times the text appears in one request (the Tailor's prompt holds
    the original, its draft in a tool call, and the output is about as long again).
    """
    budget = getattr(llm, "max_request_tokens", None) or 8000
    room_chars = (budget - output_tokens - 250) * 3 * 0.9 - overhead_chars
    return max(2000, int(room_chars / copies))
