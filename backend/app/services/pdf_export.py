"""Plain-text resume / cover letter -> clean, ATS-friendly PDF (fpdf2).

Uses the same structure rules as the DOCX export (ALL-CAPS headings, "-" bullets), plus:
the first line is the name, lines before the first heading are contact details, and lines with a
year that aren't bullets are role/date lines (bold). Text stays real, selectable text so ATS
parsers can read it.

The built-in PDF fonts only cover Latin-1, so typographic characters are mapped to plain
equivalents (– -> -, ’ -> ', → -> ->). Bullets are drawn as shapes, not characters.
"""
from __future__ import annotations

import re
import unicodedata

from fpdf import FPDF

from app.services.docx_export import HEADING

FONT = "helvetica"
ACCENT = (31, 78, 140)
MUTED = (90, 90, 90)
_YEAR = re.compile(r"\b(19|20)\d{2}\b")
_BULLET = ("- ", "• ", "* ", "– ", "· ")
_REPLACE = {
    "\u2013": "-", "\u2014": "-", "\u2012": "-", "\u2212": "-", "\u2010": "-", "\u2011": "-",
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201c": '"', "\u201d": '"', "\u201e": '"',
    "\u2022": "-", "\u25cf": "-", "\u25aa": "-", "\u2023": "-", "\u2043": "-",
    "\u2026": "...", "\u2192": "->", "\u2190": "<-", "\u2264": "<=", "\u2265": ">=", "\u2248": "~",
    "\u20ac": "EUR", "\u20b9": "Rs.", "\u2122": "(TM)", "\u2713": "", "\u2714": "",
    "\u00a0": " ", "\u2009": " ", "\u200b": "", "\ufeff": "",
}


def to_latin1(text: str) -> str:
    """Map text to what the core PDF fonts can draw; drop what has no plain equivalent."""
    out = []
    for ch in text:
        if ch in _REPLACE:
            out.append(_REPLACE[ch])
            continue
        cat = unicodedata.category(ch)
        if ch in "\t\n":
            out.append(ch)
        elif cat == "Zs" or ch.isspace():
            out.append(" ")  # all the Unicode spaces (narrow no-break, figure, en/em, ideographic...)
        elif cat == "Cf" or ch == "\u00ad":
            continue  # invisible: zero-width space/joiner, word joiner, soft hyphen, BOM
        elif cat == "Pd":
            out.append("-")  # every kind of dash/hyphen
        elif ord(ch) < 256:
            out.append(ch)
        else:
            plain = unicodedata.normalize("NFKD", ch).encode("latin-1", "ignore").decode("latin-1")
            out.append(plain if plain else "?")  # "?" only when there is truly no plain equivalent
    return "".join(out)


class _Doc(FPDF):
    def __init__(self) -> None:
        super().__init__(format="A4", unit="mm")
        self.set_margins(18, 16, 18)
        self.set_auto_page_break(auto=True, margin=16)
        self.add_page()

    def para(self, text: str, size: float = 10.5, style: str = "", color=(0, 0, 0), align: str = "L",
             line: float = 5.0) -> None:
        self.set_font(FONT, style, size)
        self.set_text_color(*color)
        self.multi_cell(0, line, to_latin1(text), align=align, new_x="LMARGIN", new_y="NEXT")

    def heading(self, text: str) -> None:
        self.ln(2.5)
        self.para(text.upper(), size=11, style="B", color=ACCENT, line=5.5)
        self.set_draw_color(*ACCENT)
        self.set_line_width(0.3)
        y = self.get_y() + 0.5
        self.line(self.l_margin, y, self.w - self.r_margin, y)
        self.ln(2)

    def bullet(self, text: str) -> None:
        indent = 5.0
        self.set_fill_color(40, 40, 40)
        self.circle(self.l_margin + 1.8, self.get_y() + 2.5, 0.65, style="F")  # centre x, y, radius
        left = self.l_margin
        self.set_left_margin(left + indent)
        self.set_x(left + indent)
        self.para(text)
        self.set_left_margin(left)
        self.ln(0.6)


def _is_heading(line: str) -> bool:
    return bool(HEADING.match(line))


def resume_to_pdf(text: str) -> bytes:
    doc = _Doc()
    lines = [ln.strip() for ln in text.splitlines()]
    body_started = False
    name_done = False
    for line in lines:
        if not line:
            continue
        if not name_done and not _is_heading(line):
            doc.para(line, size=18, style="B", align="C", line=8)
            name_done = True
            continue
        if _is_heading(line):
            body_started = True
            doc.heading(line)
        elif not body_started:
            doc.para(line, size=9.5, color=MUTED, align="C", line=4.6)  # contact / headline
        elif line.startswith(_BULLET):
            doc.bullet(line[2:].strip())
        elif _YEAR.search(line) and len(line) <= 140:
            doc.ln(1)
            doc.para(line, style="B")  # role, company and dates
        else:
            doc.para(line)
    return bytes(doc.output())


def letter_to_pdf(text: str) -> bytes:
    doc = _Doc()
    doc.set_margins(25, 22, 25)
    doc.set_x(25)
    paragraphs = re.split(r"\n\s*\n", text.strip())
    for p in paragraphs:
        doc.para(" ".join(ln.strip() for ln in p.splitlines()) if not _is_list(p) else p.strip(),
                 size=11, line=5.8)
        doc.ln(3.5)
    return bytes(doc.output())


def _is_list(paragraph: str) -> bool:
    """Keep line breaks for sign-offs / addresses ("Sincerely,\\nPriya") instead of joining them."""
    lines = [ln for ln in paragraph.splitlines() if ln.strip()]
    return len(lines) > 1 and all(len(ln) < 60 for ln in lines)
