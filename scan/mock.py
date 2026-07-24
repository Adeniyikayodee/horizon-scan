"""Dry-run mocks. Schema-shaped canned data so the whole pipeline flows with no
key and no network. Score and theme marks follow the active spec's criteria, so
custom criteria flow through dry runs too.
"""
from __future__ import annotations

from typing import Any

from . import config, spec as spec_mod

_MARKS = ["strong", "partial", "weak"]


def _subject(user: str) -> str:
    return user.splitlines()[0] if user else ""


def _scout(subj: str, h: int) -> dict[str, Any]:
    org = subj.replace("Organization:", "").strip() or "the organization"
    print(f"  scout    {org[:48]}")
    return {"queries": [f"{org} Africa economic transformation recent"],
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
            "locator": "Section 3, Results, page 14", "verbatim": True,
            "access_note": "Published 2024."}


def _score(subj: str, h: int) -> dict[str, Any]:
    print(f"  score    {subj[:48]}")
    out: dict[str, Any] = {}
    for i, c in enumerate(spec_mod.criteria(config.active_spec())):
        out[c["key"]] = _MARKS[(h + i) % 2]
        out["reason_" + c["key"]] = "Grounded in the evidence on record."
    out["overall"] = ["high", "medium"][h % 2]
    out["evidence_basis"] = "The pilot's measured gain in local value retention."
    out["self_check"] = "consistent"
    out["self_check_note"] = "The marks follow from the evidence."
    return out


def _verify(subj: str, h: int) -> dict[str, Any]:
    if h % 3 == 0:
        print(f"  verify   {subj[:48]}  partial")
        return {"status": "partial", "confirming_quote": "", "note": "Rests on secondary sources.",
                "primary_url": "", "claim_supported": False, "figure_check": "n/a", "discrepancies": []}
    print(f"  verify   {subj[:48]}  verified")
    return {"status": "verified", "confirming_quote": "The institution's own page states the gain.",
            "note": "Confirmed on the primary source.", "primary_url": "https://example.org/primary",
            "claim_supported": True, "figure_check": "Figure matches the source.", "discrepancies": []}


def _audit(subj: str, h: int) -> dict[str, Any]:
    flag = h % 7 == 0
    print(f"  audit    {subj[:48]}  {'flag' if flag else 'pass'}")
    return {"quote_supports_claim": not flag, "score_matches_evidence": "overstated" if flag else "consistent",
            "source_is_primary": True, "verdict": "flag" if flag else "pass",
            "notes": "Chain is internally consistent." if not flag else "Score looks high."}


def _librarian(subj: str) -> dict[str, Any]:
    org = subj.replace("Organization:", "").strip() or "the organization"
    print(f"  library  finding reports for {org[:40]}")
    return {"reports": [
        {"title": f"{org} value-addition working paper", "date": "2024",
         "url": "https://example.org/report-value-addition.pdf", "type": "working paper"},
        {"title": f"{org} annual report 2024", "date": "2024",
         "url": "https://example.org/annual-2024.pdf", "type": "annual report"}]}


def _frame_orgs(user: str) -> dict[str, Any]:
    names = [l[2:].strip() for l in user.splitlines() if l.startswith("- ")]
    print(f"  frame    {len(names)} added organizations")
    return {"organizations": [
        {"name": n, "type": "Analyst-added", "region": "",
         "why": "Added by the analyst, framed for the roster."} for n in names]}


def _discover() -> dict[str, Any]:
    print("  discover organizations")
    return {"organizations": [
        {"name": "African Economic Research Consortium (AERC)", "type": "African policy institute",
         "region": "Africa", "why": "Strong research-to-policy track record."},
        {"name": "Policy Center for the New South", "type": "African policy institute",
         "region": "Africa", "why": "Atlantic and Africa economic dialogue."},
        {"name": "UN Trade and Development (UNCTAD)", "type": "Multilateral", "region": "Global",
         "why": "Trade and productive-capacity work."}]}


def _themes(user: str) -> dict[str, Any]:
    crit = spec_mod.criteria(config.active_spec())
    members = [l[2:].split(":")[0].strip() for l in user.splitlines() if l.startswith("- ")]
    print(f"  theme    clustering {len(members)} approaches")

    def theme(name, tag, posture, mark, mem, top2):
        d = {"name": name, "tag": tag, "posture": posture, "rationale": "A clear rationale for this theme.",
             "marquee": mem[0] if mem else "", "members": mem, "top2": top2}
        for c in crit:
            d[c["key"]] = mark
        return d

    return {"themes": [
        theme("Blue economy and coastal value addition", "new", "enter", "strong", members[:3], True),
        theme("Sovereign and strategic investment funds", "adjacent", "enter", "partial", members[3:5], True),
        theme("Financing Africa's future", "existing", "deepen", "strong", members[5:7], False)]}


def _synth() -> dict[str, Any]:
    print("  synth    writing memo and scorecard intro")
    memo = ("# Global scan, wrap-up\n\n"
            "The scan points to two clean new areas, the blue economy and coastal value addition, and "
            "sovereign and strategic investment funds. Both let Africa keep more of the value it creates.\n\n"
            "## Open questions\n\nSeveral rows rest on secondary sources and need a primary to harden.")
    return {"memo_markdown": memo,
            "scorecard_intro": "Themes scored on the criteria, with two clean new areas to enter first."}


def _hunches() -> dict[str, Any]:
    print("  hunches  seeding cross-org patterns")
    return {"patterns": [
        {"name": "Value capture recurs", "note": "Several approaches turn on keeping value onshore."},
        {"name": "Coastal sectors look open", "note": "The blue economy appears underserved."}]}


def mock_response(schema: dict[str, Any], user: str) -> dict[str, Any]:
    keys = set(schema.get("properties", {}).keys())
    subj = _subject(user)
    h = sum(ord(c) for c in user)

    if "candidates" in keys:
        return _scout(subj, h)
    if "reports" in keys:
        return _librarian(subj)
    if "keep" in keys:
        return _read(subj, h)
    if "organizations" in keys:
        return _frame_orgs(user) if "to frame" in user.lower() else _discover()
    if "quote_supports_claim" in keys:
        return _audit(subj, h)
    if "overall" in keys and "evidence_basis" in keys:
        return _score(subj, h)
    if "status" in keys:
        return _verify(subj, h)
    if "themes" in keys:
        return _themes(user)
    if "memo_markdown" in keys:
        return _synth()
    if "patterns" in keys:
        return _hunches()
    return {}
