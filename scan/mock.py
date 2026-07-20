"""Dry-run mocks. Schema-shaped canned data so the whole pipeline flows with
no key and no network. `mock_response` detects the stage from the schema's
fields and returns one branch's data; each branch prints a trace line so you
can watch the flow. The data carries the same provenance and self-check fields
the real agents produce.
"""
from __future__ import annotations

from typing import Any


def _subject(user: str) -> str:
    return user.splitlines()[0] if user else ""


def _scout(subj: str, h: int) -> dict[str, Any]:
    org = subj.replace("Organization:", "").strip() or "the organization"
    print(f"  scout    {org[:48]}")
    return {"queries": [f"{org} Africa economic transformation since 2023",
                        f"{org} value addition policy program"],
            "candidates": [
                {"name": f"{org} value-addition initiative",
                 "one_liner": "A recent program on keeping more value onshore.",
                 "year": "2024", "url": f"https://example.org/a{h % 997}.pdf", "source_type": "report"},
                {"name": f"{org} evidence-to-policy lab",
                 "one_liner": "A pilot moving research into practice.",
                 "year": "2025", "url": f"https://example.org/b{h % 991}", "source_type": "webpage"}]}


def _read(subj: str, h: int) -> dict[str, Any]:
    keep = h % 5 != 0
    band = ["emerging", "frontier", "maturing"][h % 3]
    print(f"  read     {subj[:48]}  keep={keep} band={band}")
    return {"keep": keep, "band": band,
            "what": "An approach that adds value in a new sector.",
            "evidence": "A pilot showed measurable gains in output and jobs.",
            "uptake": "One government has begun to adopt it.",
            "quotes": ["The program raised local value retention by a measurable margin."],
            "locator": "Section 3, Results, page 14",
            "verbatim": True,
            "access_note": "Published 2024, reached from the organization's reports page."}


def _score(subj: str, h: int) -> dict[str, Any]:
    print(f"  score    {subj[:48]}")
    marks = ["strong", "partial", "weak"]
    return {"mandate_fit": marks[h % 2], "research_to_policy": "strong",
            "african_traction": marks[(h + 1) % 3], "white_space": "strong",
            "reason_mandate": "Advances diversification and productivity.",
            "reason_rtp": "Portable into policy advice.",
            "reason_traction": "Real demand on the continent.",
            "reason_whitespace": "Open ground for the institute to lead.",
            "overall": ["high", "medium"][h % 2],
            "evidence_basis": "The pilot's measured gain in local value retention.",
            "self_check": "consistent",
            "self_check_note": "The marks follow from the evidence on record."}


def _verify(subj: str, h: int) -> dict[str, Any]:
    if h % 3 == 0:
        print(f"  verify   {subj[:48]}  partial")
        return {"status": "partial", "confirming_quote": "", "note": "Rests on secondary sources.",
                "primary_url": "", "claim_supported": False, "figure_check": "n/a",
                "discrepancies": ["Figure could not be traced to the primary."]}
    print(f"  verify   {subj[:48]}  verified")
    return {"status": "verified",
            "confirming_quote": "The institution's own page states the reported gain.",
            "note": "Confirmed on the primary source.",
            "primary_url": "https://example.org/primary", "claim_supported": True,
            "figure_check": "Figure matches the source table.", "discrepancies": []}


def _audit(subj: str, h: int) -> dict[str, Any]:
    flag = h % 7 == 0
    print(f"  audit    {subj[:48]}  {'flag' if flag else 'pass'}")
    return {"quote_supports_claim": not flag, "score_matches_evidence": "overstated" if flag else "consistent",
            "source_is_primary": True, "verdict": "flag" if flag else "pass",
            "notes": "Score looks high for the evidence." if flag else "Chain is internally consistent."}


def _themes(user: str) -> dict[str, Any]:
    members = [l[2:].split(":")[0].strip() for l in user.splitlines() if l.startswith("- ")]
    print(f"  theme    clustering {len(members)} approaches")
    marks = lambda a, b, c, d: {"mandate_fit": a, "research_to_policy": b,
                                "african_traction": c, "white_space": d}
    return {"themes": [
        {"name": "Blue economy and coastal value addition", "tag": "new",
         **marks("strong", "strong", "strong", "strong"), "posture": "impact",
         "rationale": "Open coastal sector, strong value capture.",
         "marquee": members[0] if members else "", "members": members[:3], "top2": True},
        {"name": "Sovereign and strategic investment funds", "tag": "adjacent",
         **marks("strong", "partial", "strong", "partial"), "posture": "impact",
         "rationale": "A real extension into value retention.",
         "marquee": members[3] if len(members) > 3 else "", "members": members[3:5], "top2": True},
        {"name": "Financing Africa's future", "tag": "existing",
         **marks("strong", "strong", "strong", "weak"), "posture": "aligned",
         "rationale": "The institute already works here.",
         "marquee": "", "members": members[5:7], "top2": False},
    ]}


def _synth() -> dict[str, Any]:
    print("  synth    writing memo and scorecard intro")
    memo = ("# Global scan, wrap-up\n\n"
            "The scan points to two clean new areas, the blue economy and coastal value "
            "addition, and sovereign and strategic investment funds. Both let Africa keep "
            "more of the value it creates in a new sector. Financing Africa's future stays "
            "aligned, since the institute already works there.\n\n"
            "## Open questions\n\n"
            "Several rows rest on secondary sources and need a primary before they harden.")
    return {"memo_markdown": memo,
            "scorecard_intro": "Themes scored on four criteria, with two clean new areas to enter first."}


def _hunches() -> dict[str, Any]:
    print("  hunches  seeding cross-org patterns")
    return {"patterns": [
        {"name": "Value capture recurs", "note": "Several approaches turn on keeping value onshore."},
        {"name": "Coastal sectors look open", "note": "The blue economy appears underserved."}]}


def mock_response(schema: dict[str, Any], user: str) -> dict[str, Any]:
    """Return schema-shaped canned data for the stage this schema belongs to."""
    keys = set(schema.get("properties", {}).keys())
    subj = _subject(user)
    h = sum(ord(c) for c in user)

    if "candidates" in keys:
        return _scout(subj, h)
    if "keep" in keys:
        return _read(subj, h)
    if "mandate_fit" in keys:
        return _score(subj, h)
    if "quote_supports_claim" in keys:
        return _audit(subj, h)
    if "status" in keys:
        return _verify(subj, h)
    if "themes" in keys:
        return _themes(user)
    if "memo_markdown" in keys:
        return _synth()
    if "patterns" in keys:
        return _hunches()
    return {}
