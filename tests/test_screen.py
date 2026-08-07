"""F1: the existing-portfolio screen.

The one rule the whole scan exists to serve, never recommend work the institute
already runs, was prompt text only until now, and the model routed around it by
tagging an existing area "new". These tests lock the gate that stops that.
"""
from __future__ import annotations

import glob

import pytest

from scan import spec as S

SPEC = dict(S.DEFAULT_SPEC)


def _theme(name, tag="new", posture="enter", rationale="", marquee="", members=None, top2=True):
    return {"name": name, "tag": tag, "posture": posture, "rationale": rationale,
            "marquee": marquee, "members": members or [], "top2": top2}


# --- the case that actually shipped ------------------------------------------------
def test_ai_theme_is_screened_back_to_existing():
    """Run 4be936d649 recommended this as a top area to enter while the spec listed
    the digital economy, DPI, and AI as existing work."""
    t = _theme("AI-Driven Policy Experimentation Platforms")
    S.screen_existing([t], SPEC)
    assert t["tag"] == "existing"
    assert t["posture"] == "deepen"
    assert t["top2"] is False
    assert "Digital economy" in t["screened"]


@pytest.mark.parametrize("name", [
    "AI-Driven Policy Experimentation Platforms",
    "Digital economy and DPI",
    "Green industrialization and the just transition",
    "Regional value chains",
    "Health financing for transformation",
    "The care economy",
    "TVET and workforce development",
    "Blended finance for adaptation",
    "Industrial policy for the next decade",
])
def test_existing_areas_are_screened(name):
    t = _theme(name)
    S.screen_existing([t], SPEC)
    assert t["tag"] == "existing" and t["posture"] == "deepen", f"not screened: {name}"


# --- and the genuinely new ground must survive -------------------------------------
@pytest.mark.parametrize("name", [
    "Blue economy and coastal value addition",
    "Sovereign and strategic investment funds",
    "Creative economy and IP value capture",
    "Economic transformation in fragile states",
    "Earth observation and the space-data economy",
    "Urban and spatial economic transformation",
    "Diaspora and remittances",
])
def test_new_areas_are_untouched(name):
    t = _theme(name)
    S.screen_existing([t], SPEC)
    assert t["tag"] == "new" and t["posture"] == "enter", f"wrongly screened: {name}"


# --- word boundaries: the 'ai' term is the sharp edge -------------------------------
def test_ai_term_does_not_match_ordinary_words():
    for name in ("Aid effectiveness and delivery", "Chain of custody in minerals",
                 "Maritime and coastal trade"):
        t = _theme(name)
        S.screen_existing([t], SPEC)
        assert t["tag"] == "new", f"false positive on {name!r}"


# --- confidence rule: one body mention is a near miss, two is a screen --------------
def test_single_body_mention_is_a_near_miss_not_a_screen():
    t = _theme("Blue economy and coastal value addition",
               rationale="Coastal processing needs capital, and development finance is "
                         "one route among several to reach it.")
    S.screen_existing([t], SPEC)
    assert t["tag"] == "new", "a passing mention should not kill a new theme"
    assert "near the existing portfolio" in t.get("screen_note", "")


def test_two_body_mentions_screen():
    t = _theme("Coastal capital markets",
               rationale="The work turns on blended finance and domestic resource "
                         "mobilization for coastal states.")
    S.screen_existing([t], SPEC)
    assert t["tag"] == "existing" and t["posture"] == "deepen"


def test_member_names_count_as_body():
    t = _theme("A new area",
               members=["National TVET reform pilot", "Skills development compact"])
    S.screen_existing([t], SPEC)
    assert t["tag"] == "existing"


# --- the prompt renders from the same list the gate enforces ------------------------
def test_prompt_and_gate_read_one_list():
    text = S.scope_text(SPEC)
    for area in S.excluded_areas(SPEC):
        assert area["name"] in text, f"{area['name']} missing from the agent frame"
        for term in area["terms"]:
            assert term in text, f"term {term!r} enforced but never stated to the model"


def test_editing_the_spec_moves_both_the_prompt_and_the_gate():
    custom = dict(SPEC)
    custom["excluded_areas"] = [{"name": "Widget policy", "terms": ["widget policy"]}]
    assert "Widget policy" in S.scope_text(custom)
    t = _theme("Widget policy for Africa")
    S.screen_existing([t], custom)
    assert t["tag"] == "existing"
    # and an area dropped from the spec is no longer screened
    t2 = _theme("AI-Driven Policy Experimentation Platforms")
    S.screen_existing([t2], custom)
    assert t2["tag"] == "new"


# --- replay over the archived runs, free, no API spend -------------------------------
def test_replay_over_archived_scorecards():
    """Every archived theme name is run through the screen. The AI theme from
    4be936d649 must flip, and nothing may crash on real data."""
    openpyxl = pytest.importorskip("openpyxl")
    seen = flipped = 0
    found_the_ai_theme = False
    for f in sorted(glob.glob("runs/*/out/theme_scorecard.xlsx")):
        wb = openpyxl.load_workbook(f, read_only=True)
        rows = list(wb.active.iter_rows(values_only=True))
        for r in rows[3:]:
            if not r or not r[0]:
                continue
            t = _theme(str(r[0]), marquee=str(r[-1] or ""))
            S.screen_existing([t], SPEC)
            seen += 1
            if t.get("screened"):
                flipped += 1
                if "ai-driven" in str(r[0]).lower():
                    found_the_ai_theme = True
    if seen:
        assert found_the_ai_theme, "the theme that motivated this gate is no longer caught"
        assert flipped < seen, "the screen is catching everything, it is too broad"
