"""Render a markdown dossier as a clean PDF (fpdf2), so the UI hands the
researcher a document, never markdown. Author set, core fonts, unicode
punctuation sanitized to latin-1, long unbreakable tokens (URLs) chunked, and
the cursor reset to the left margin on every line so widths never collapse.
"""
from __future__ import annotations

from fpdf import FPDF
from fpdf.enums import XPos, YPos

_REPL = {
    "–": "-", "—": ", ", "‘": "'", "’": "'",
    "“": '"', "”": '"', "…": "...", " ": " ",
    "·": "-", "•": "-", "→": "->",
}


def _break_long(text: str, n: int = 40) -> str:
    """Insert spaces into any token longer than n chars so it can wrap."""
    out = []
    for w in text.split(" "):
        while len(w) > n:
            out.append(w[:n])
            w = w[n:]
        out.append(w)
    return " ".join(out)


def _san(text: str) -> str:
    text = str(text)
    for k, v in _REPL.items():
        text = text.replace(k, v)
    text = text.replace("**", "")
    text = _break_long(text)
    return text.encode("latin-1", "replace").decode("latin-1")


def md_to_pdf(markdown: str, title: str = "Dossier", author: str = "Kayode Adeniyi") -> bytes:
    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=16)
    pdf.set_margins(18, 16, 18)
    pdf.add_page()
    pdf.set_author(author)
    pdf.set_title(_san(title)[:80])

    def cell(text: str, h: float = 5) -> None:
        pdf.set_x(pdf.l_margin)  # always start at the left margin
        try:
            pdf.multi_cell(0, h, _san(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        except Exception:
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(0, h, _san(text)[:180], new_x=XPos.LMARGIN, new_y=YPos.NEXT)

    for line in markdown.splitlines():
        s = line.rstrip()
        if not s:
            pdf.ln(2)
            continue
        if s.startswith("# "):
            pdf.set_font("Helvetica", "B", 15); cell(s[2:], 8); pdf.ln(1)
        elif s.startswith("## "):
            pdf.set_font("Helvetica", "B", 12); pdf.ln(1); cell(s[3:], 6); pdf.ln(0.5)
        elif s.startswith("### "):
            pdf.set_font("Helvetica", "B", 11); cell(s[4:], 6)
        elif s.startswith("> "):
            pdf.set_font("Helvetica", "I", 10.5); cell("    " + s[2:])
        elif s.startswith("- ") or s.startswith("* "):
            pdf.set_font("Helvetica", "", 11); cell("  -  " + s[2:])
        else:
            pdf.set_font("Helvetica", "", 11); cell(s)
    return bytes(pdf.output())
