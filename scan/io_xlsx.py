"""Excel and markdown I/O: read the org sheet, write the review longlist and
the stage-2 deliverables. Uses openpyxl directly to control author metadata.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook

from . import config, guardrail

AUTHOR = "Kayode Adeniyi"


def _stamp(wb: Workbook) -> None:
    wb.properties.creator = AUTHOR
    wb.properties.lastModifiedBy = AUTHOR


# --- input ---
def read_orgs(path: Path | None = None) -> list[dict[str, str]]:
    path = path or config.ORG_SHEET
    wb = load_workbook(path, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip().lower() if c else "" for c in rows[0]]
    orgs = []
    for i, r in enumerate(rows[1:], start=1):
        d = {header[j]: ("" if v is None else str(v)) for j, v in enumerate(r) if j < len(header)}
        if not d.get("name"):
            continue
        d.setdefault("id", f"O{i:03d}")
        orgs.append(d)
    return orgs


def write_sample_orgs(path: Path | None = None) -> Path:
    path = path or config.ORG_SHEET
    path.parent.mkdir(parents=True, exist_ok=True)
    wb = Workbook()
    ws = wb.active
    ws.title = "organizations"
    ws.append(["id", "name", "type", "region", "status"])
    sample = [
        ("O001", "African Economic Research Consortium (AERC)", "African policy institute", "Africa", ""),
        ("O002", "Policy Center for the New South", "African policy institute", "Africa", ""),
        ("O003", "UN Trade and Development (UNCTAD)", "Multilateral", "Global", ""),
    ]
    for row in sample:
        ws.append(row)
    _stamp(wb)
    wb.save(path)
    return path


# --- review (stage 1 out) ---
def write_longlist(rows: list[dict[str, Any]]) -> Path:
    path = config.REVIEW_DIR / "longlist.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "longlist"
    cols = ["keep", "org", "approach", "band", "what", "year", "mandate_fit",
            "research_to_policy", "african_traction", "white_space", "overall",
            "verification", "source"]
    ws.append(cols)
    for r in rows:
        s = r.get("score", {})
        v = r.get("verification", {})
        ws.append([
            "Y", r.get("org", ""), r.get("name", ""), r.get("band", ""), r.get("what", ""), r.get("year", ""),
            s.get("mandate_fit", ""), s.get("research_to_policy", ""),
            s.get("african_traction", ""), s.get("white_space", ""), s.get("overall", ""),
            v.get("status", ""), r.get("url", ""),
        ])
    _stamp(wb)
    wb.save(path)
    return path


def read_kept_longlist() -> list[dict[str, Any]]:
    path = config.REVIEW_DIR / "longlist.xlsx"
    wb = load_workbook(path, read_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    header = [str(c).strip().lower() for c in rows[0]]
    kept = []
    for r in rows[1:]:
        d = {header[j]: v for j, v in enumerate(r) if j < len(header)}
        if str(d.get("keep", "")).strip().upper() == "Y":
            kept.append({
                "org": d.get("org", ""), "name": d.get("approach", ""),
                "band": d.get("band", ""), "what": d.get("what", ""),
                "overall": d.get("overall", ""), "year": d.get("year", ""),
                "url": d.get("source", ""),
                "verification": {"status": d.get("verification", "")},
            })
    return kept


def write_open_questions(rows: list[dict[str, Any]], dropped: list[dict[str, Any]]) -> Path:
    path = config.REVIEW_DIR / "open_questions.md"
    lines = ["# Open questions\n"]
    partial = [r for r in rows if (r.get("verification", {}) or {}).get("status") == "partial"]
    lines.append("## Partial, needs a primary\n")
    for r in partial:
        note = (r.get("verification", {}) or {}).get("note", "")
        lines.append(f"- {r.get('org','')}: {r.get('name','')} — {note}")
    lines.append("\n## Dropped at reading\n")
    for d in dropped:
        lines.append(f"- {d.get('org','')}: {d.get('name','')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def write_hunches(patterns: list[dict[str, str]]) -> Path:
    path = config.REVIEW_DIR / "hunches.md"
    lines = [
        "# Hunches\n",
        "Seeded from cross-org patterns. Overwrite with your own reading, this",
        "is the part a tool misses and it feeds the theming in stage 2.\n",
    ]
    for p in patterns:
        lines.append(f"- {p.get('name','')}: {p.get('note','')}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


# --- deliverables (stage 2 out) ---
def write_scorecard(themes: list[dict[str, Any]], intro: str) -> Path:
    guardrail.assert_clean("scorecard_intro", intro)
    path = config.OUT_DIR / "theme_scorecard.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Theme scorecard"
    ws.append([intro])
    ws.append([])
    cols = ["Theme", "Standing", "Mandate fit", "Research to policy",
            "African traction", "White space", "Posture", "Evidence", "Top area", "Marquee"]
    ws.append(cols)
    for t in themes:
        ws.append([
            t["name"], t["tag"], t["mandate_fit"], t["research_to_policy"],
            t["african_traction"], t["white_space"], t["posture"], t.get("evidence", ""),
            "yes" if t.get("top2") else "", t.get("marquee", ""),
        ])
    _stamp(wb)
    wb.save(path)
    return path


def write_map(rows: list[dict[str, Any]], themes: list[dict[str, Any]]) -> Path:
    path = config.OUT_DIR / "innovation_map.xlsx"
    member_theme = {}
    for t in themes:
        for m in t.get("members", []):
            member_theme[m] = t["name"]
    wb = Workbook()
    ws = wb.active
    ws.title = "Scan"
    ws.append(["Theme", "Approach", "Band", "What", "Year", "Overall", "Verification", "Source"])
    for r in rows:
        ws.append([
            member_theme.get(r.get("name", ""), ""), r.get("name", ""), r.get("band", ""),
            r.get("what", ""), r.get("year", ""), r.get("overall", ""),
            (r.get("verification", {}) or {}).get("status", ""), r.get("url", ""),
        ])
    _stamp(wb)
    wb.save(path)
    return path


def write_memo(markdown: str) -> Path:
    guardrail.assert_clean("synthesis_memo", markdown)
    path = config.OUT_DIR / "synthesis_memo.md"
    path.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return path
