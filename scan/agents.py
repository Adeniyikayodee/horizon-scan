"""The agents. Each assembles a frozen per-stage frame from the context files
(cached) and makes one structured call. Every stage records provenance (where,
what, how) and a self-check; the Auditor cross-checks the whole chain.
"""
from __future__ import annotations

import asyncio
from typing import Any

from . import config, schemas, sources
from .client import structured_call


def _frame(ctx: dict[str, str], parts: list[str], instructions: str) -> str:
    blocks = [ctx[p] for p in parts if ctx.get(p)]
    blocks.append("# Your task\n\n" + instructions.strip())
    return "\n\n---\n\n".join(blocks)


# --- defensive parsing: normalize whatever a model returns so no single bad
#     field crashes a run. Enums lowercased and defaulted, types coerced.
#     Anthropic's strict schema makes this a no-op; weaker models need it. ---
_TAGS = {"existing", "adjacent", "new"}
_POST = {"aligned", "impact", "watch"}
_MARKS = {"strong", "partial", "weak"}
_OVERALL = {"high", "medium", "low"}
_CONSIST = {"consistent", "overstated", "understated"}


def _enum(v, allowed, default):
    s = str(v).lower().strip()
    return s if s in allowed else default


def _s(v):
    return v if isinstance(v, str) else ("" if v is None else str(v))


def _b(v):
    return v if isinstance(v, bool) else str(v).lower().strip() in ("true", "yes", "1")


def _list(v):
    return v if isinstance(v, list) else ([] if v in (None, "") else [v])


def _coerce_candidate(c: dict) -> dict:
    return {"name": _s(c.get("name")), "one_liner": _s(c.get("one_liner")),
            "year": _s(c.get("year")), "url": _s(c.get("url")),
            "source_type": _enum(c.get("source_type"),
                                 {"report", "webpage", "dataset", "press", "other"}, "other")}


def _coerce_reading(o: dict) -> dict:
    return {"keep": _b(o.get("keep")),
            "band": _enum(o.get("band"), {"frontier", "emerging", "maturing"}, "emerging"),
            "what": _s(o.get("what")), "evidence": _s(o.get("evidence")),
            "uptake": _s(o.get("uptake")), "quotes": [_s(q) for q in _list(o.get("quotes"))],
            "locator": _s(o.get("locator")), "verbatim": _b(o.get("verbatim")),
            "access_note": _s(o.get("access_note"))}


def _coerce_score(o: dict) -> dict:
    return {"mandate_fit": _enum(o.get("mandate_fit"), _MARKS, "partial"),
            "research_to_policy": _enum(o.get("research_to_policy"), _MARKS, "partial"),
            "african_traction": _enum(o.get("african_traction"), _MARKS, "partial"),
            "white_space": _enum(o.get("white_space"), _MARKS, "partial"),
            "reason_mandate": _s(o.get("reason_mandate")), "reason_rtp": _s(o.get("reason_rtp")),
            "reason_traction": _s(o.get("reason_traction")), "reason_whitespace": _s(o.get("reason_whitespace")),
            "overall": _enum(o.get("overall"), _OVERALL, "medium"),
            "evidence_basis": _s(o.get("evidence_basis")),
            "self_check": _enum(o.get("self_check"), _CONSIST, "consistent"),
            "self_check_note": _s(o.get("self_check_note"))}


def _coerce_verdict(o: dict) -> dict:
    return {"status": _enum(o.get("status"), {"verified", "partial"}, "partial"),
            "confirming_quote": _s(o.get("confirming_quote")), "note": _s(o.get("note")),
            "primary_url": _s(o.get("primary_url")), "claim_supported": _b(o.get("claim_supported")),
            "figure_check": _s(o.get("figure_check")),
            "discrepancies": [_s(d) for d in _list(o.get("discrepancies"))]}


def _coerce_audit(o: dict) -> dict:
    return {"quote_supports_claim": _b(o.get("quote_supports_claim")),
            "score_matches_evidence": _enum(o.get("score_matches_evidence"), _CONSIST, "consistent"),
            "source_is_primary": _b(o.get("source_is_primary")),
            "verdict": _enum(o.get("verdict"), {"pass", "flag"}, "flag"), "notes": _s(o.get("notes"))}


def _defaulted(raw: dict, checks: dict) -> list[str]:
    """Which enum fields the model returned off-spec, so silent repair is visible."""
    return [f for f, allowed in checks.items()
            if str(raw.get(f, "")).lower().strip() not in allowed]


SCOUT_I = """
You are the Scout. You are given ONE organization. Search only for that
organization's own recent work (since 2023) on Africa. Record the exact search
queries you ran, so the how is on the record. For each real, named program that
touches economic transformation or research-to-policy, record the name, one
sentence on what it does, the year, a direct link to the organization's own
page, and what kind of source it is.

Favor funders, practitioners, and policy labs over the academic frontier, since
that frontier is mostly working papers not yet operationalized. Skip programs
that are squarely part of the hub's existing portfolio (listed in scope), we are
after genuinely new or adjacent open ground, not familiar territory. Do not
invent programs. If none fit, record an empty candidate list. Search the web as
needed, then call record once.
"""

READER_I = """
You are the Reader. When the full source text is given below, READ INTO it: work
through the document, find the single approach it really describes, and quote the
exact lines from that text. When no full text is provided, open the candidate's
link and read the page instead.

Lay the approach out in three parts: what it is, the evidence, and whether a
government has adopted it. Record WHERE in the document the approach sits (the
section heading, page number, or table), and quote the exact lines that back each
part. Set verbatim=true only if every quote is copied word for word from the
source. If the page states its own publication or update date, put that in
access_note, and never state a date on which you accessed the source, that date
is stamped for you.

Classify the approach into a band: frontier (actively researched and debated but
not yet in mainstream policy), emerging (crossed into policy experimentation, in
pilots, new legislation, or funder priorities, but not broadly adopted), or
maturing (now standard practice, such as green bonds or cash transfers). Keep is
about whether the source is substantive and on-lens, do not set it false only for
the band. If it is thin or off-lens, set keep=false. Call record once.
"""

SCORER_I = """
You are the Scorer. Score the approach on the four criteria using strong,
partial, or weak, weighting mandate fit and research-to-policy most. Give a
one-line reason for each, drawn only from the evidence provided, and state the
single fact or quote the marks rest on. Then run a self-check: do the marks
follow from the evidence, or are they overstated or understated. Set an overall
fit. Call record once.
"""

VERIFIER_I = """
You are the Verifier, working adversarially. Open the primary source yourself
and try to DISCONFIRM the claim. Record the primary URL you actually opened.
Set claim_supported true only if the institution's own page or report directly
supports it, and quote the confirming line. If a figure is claimed, check it
against the source and report the result. List any discrepancies you find.
Mark status verified only when claim_supported is true and a confirming quote
exists; otherwise partial. Fetch the source, then call record once.
"""

AUDIT_I = """
You are the Auditor. You are handed one approach with its full chain: the quoted
lines, the scores, and the verification. Do not re-search. Check the chain for
internal consistency: do the quotes actually support the stated approach, do the
scores follow from the evidence, and is the source the institution's own
document. Return verdict flag if any check fails, so a human looks again,
otherwise pass. Call record once.
"""

THEMER_I = """
You are the Themer. Group the kept approaches into a tight set of forward-looking
themes. Tag each existing, adjacent, or new against what the institute already
runs (white space is the first screen). Score each theme on the four criteria
and give it one posture: aligned, impact, or watch. Existing themes are always
aligned, never impact. Name a marquee approach and the member approaches, and
flag the two cleanest new areas as top2. Weigh the analyst's hunches below as a
first-class input. Call record once.
"""

SYNTH_I = """
You are the Synthesizer. Write a detailed, well-framed, and useful memo, about
four to five pages when the material supports it and tighter when it does not.
Write in full prose paragraphs, clear, plain, and simple, and keep every sentence
tight with no padding. Spell each acronym out in full the first time it appears.

Voice, hold to this closely:
- Write in the affirmative. State what each approach is and what it does in
  positive, direct sentences.
- Avoid negations. Say what is, rather than what is absent.
- Avoid antithesis. Present each point on its own; do not set one thing against
  another with constructions like "not this but that," "rather than," or "instead."
- Keep it simple and clear, one idea per sentence.

Lay the memo out with these markdown headings, in order:
# a plain title
## Executive summary, a short paragraph on what the scan found and the two areas to enter first
## The themes, a full clear paragraph for each theme: what it is, the evidence behind it, how it advances the hub's mandate read through the DEPTH lens, its posture, and the strength of the evidence
## The two cleanest new areas, name each and develop over several sentences why it is worth entering first and what a first step looks like
## Open questions, a short numbered list

Follow the house style in output_spec exactly: US English, active voice, the
serial comma, spell out zero to nine, write in the analyst's own voice, describe
findings, and keep every tool and AI trace out entirely.

Each theme carries an evidence summary with verified and partial counts. State
the strength of the evidence plainly and in the affirmative: where a theme rests
on secondary sources, say that it rests on secondary sources and awaits a primary
source to confirm it. Also write one scorecard intro paragraph. Call record once.
"""


async def scout(ctx: dict[str, str], org: dict[str, str]) -> dict[str, Any]:
    user = f"Organization: {org['name']}\nType: {org.get('type','')}\nRegion: {org.get('region','')}"
    out = await structured_call(
        model=config.MODEL_SONNET, frame=_frame(ctx, ["mission", "scope"], SCOUT_I),
        user=user, schema=schemas.SCOUT_SCHEMA, web=True, effort="medium",
    )
    cands = []
    for c in out.get("candidates", []):
        try:
            cands.append(schemas.Candidate(**_coerce_candidate(c)).model_dump())
        except Exception:
            continue
    return {"candidates": cands, "queries": [_s(q) for q in _list(out.get("queries", []))]}


async def read(ctx: dict[str, str], cand: dict[str, str]) -> dict[str, Any]:
    frame = _frame(ctx, ["mission"], READER_I)
    # actually read the document: fetch its full text (HTML or extracted PDF)
    doc = "" if config.DRY_RUN else await asyncio.to_thread(sources.fetch_text, cand.get("url", ""))
    if doc:
        user = (f"Candidate: {cand['name']}\nWhat: {cand.get('one_liner','')}\n"
                f"Source: {cand.get('url','')}\n\n=== FULL SOURCE TEXT, read into this ===\n{doc}")
        out = await structured_call(model=config.MODEL_SONNET, frame=frame, user=user,
                                    schema=schemas.READER_SCHEMA, web=False, effort="medium")
    else:  # fetch failed (bot-protected, binary), fall back to search
        user = f"Candidate: {cand['name']}\nWhat: {cand.get('one_liner','')}\nLink: {cand.get('url','')}"
        out = await structured_call(model=config.MODEL_SONNET, frame=frame, user=user,
                                    schema=schemas.READER_SCHEMA, web=True, effort="medium")
    return schemas.Reading(**_coerce_reading(out)).model_dump()


async def score(ctx: dict[str, str], approach: dict[str, Any]) -> dict[str, Any]:
    user = (f"Approach: {approach['name']}\nWhat: {approach.get('what','')}\n"
            f"Evidence: {approach.get('evidence','')}\nUptake: {approach.get('uptake','')}\n"
            f"Quoted lines: {approach.get('quotes', [])}")
    out = await structured_call(
        model=config.MODEL_SONNET, frame=_frame(ctx, ["mission", "scope", "scoring"], SCORER_I),
        user=user, schema=schemas.SCORE_SCHEMA, effort="low",
    )
    dumped = schemas.Score(**_coerce_score(out)).model_dump()
    dumped["_coerced"] = _defaulted(out, {"mandate_fit": _MARKS, "research_to_policy": _MARKS,
        "african_traction": _MARKS, "white_space": _MARKS, "overall": _OVERALL, "self_check": _CONSIST})
    return dumped


async def verify(ctx: dict[str, str], approach: dict[str, Any]) -> dict[str, Any]:
    user = (f"Claim to check: {approach['name']} — {approach.get('what','')}\n"
            f"Evidence stated: {approach.get('evidence','')}\n"
            f"Quoted lines: {approach.get('quotes', [])}\nSource: {approach.get('url','')}")
    out = await structured_call(
        model=config.MODEL_SONNET, frame=_frame(ctx, ["mission"], VERIFIER_I),
        user=user, schema=schemas.VERIFY_SCHEMA, web=True, effort="medium",
    )
    dumped = schemas.Verdict(**_coerce_verdict(out)).model_dump()
    dumped["_coerced"] = _defaulted(out, {"status": {"verified", "partial"}})
    return dumped


async def audit(ctx: dict[str, str], row: dict[str, Any]) -> dict[str, Any]:
    s = row.get("score", {})
    v = row.get("verification", {})
    user = (f"Approach: {row.get('name','')}\nWhat: {row.get('what','')}\n"
            f"Evidence: {row.get('evidence','')}\nQuoted lines: {row.get('quotes', [])}\n"
            f"Scores: mandate {s.get('mandate_fit','')}, policy {s.get('research_to_policy','')}, "
            f"traction {s.get('african_traction','')}, white space {s.get('white_space','')}, "
            f"overall {s.get('overall','')}\n"
            f"Verification: status {v.get('status','')}, claim_supported {v.get('claim_supported','')}, "
            f"confirming quote: {v.get('confirming_quote','')}\nSource: {row.get('url','')}")
    out = await structured_call(
        model=config.MODEL_SONNET, frame=_frame(ctx, ["mission", "scoring"], AUDIT_I),
        user=user, schema=schemas.AUDIT_SCHEMA, effort="low",
    )
    dumped = schemas.Audit(**_coerce_audit(out)).model_dump()
    dumped["_coerced"] = _defaulted(out, {"score_matches_evidence": _CONSIST, "verdict": {"pass", "flag"}})
    return dumped


async def seed_hunches(ctx: dict[str, str], titles: list[str]) -> list[dict[str, str]]:
    user = "Kept approaches:\n" + "\n".join(f"- {t}" for t in titles)
    out = await structured_call(
        model=config.MODEL_SONNET,
        frame=_frame(ctx, ["mission"], "List a few cross-org patterns worth a human second look. Seed only, label each as a hunch."),
        user=user, schema=schemas.HUNCH_SCHEMA, effort="low",
    )
    return out.get("patterns", [])


def _coerce_theme(t: dict[str, Any]) -> dict[str, Any]:
    """Repair a theme a weaker model may have malformed: swapped tag/posture,
    off-enum marks, wrong types. Keeps the run alive across any model."""
    t = dict(t)
    tag = str(t.get("tag", "")).lower().strip()
    posture = str(t.get("posture", "")).lower().strip()
    if tag in _POST and posture in _TAGS:            # fields swapped
        tag, posture = posture, tag
    if tag not in _TAGS:
        tag = {"impact": "new", "aligned": "existing", "watch": "adjacent"}.get(tag, "new")
    if posture not in _POST:
        posture = {"existing": "aligned", "new": "impact", "adjacent": "impact"}.get(posture, "watch")
    if tag == "existing":                            # the hard rule: existing stays aligned
        posture = "aligned"
    t["tag"], t["posture"] = tag, posture
    for f in ("mandate_fit", "research_to_policy", "african_traction", "white_space"):
        m = str(t.get(f, "")).lower().strip()
        t[f] = m if m in _MARKS else "partial"
    if not isinstance(t.get("members"), list):
        t["members"] = []
    for k, d in (("rationale", ""), ("marquee", ""), ("name", "Untitled theme"), ("top2", False)):
        t.setdefault(k, d)
    return t


async def themes(ctx: dict[str, str], rows: list[dict[str, Any]], hunches: str) -> list[dict[str, Any]]:
    lines = [f"- {r['name']}: {r.get('what','')} [{r.get('overall','')}]" for r in rows]
    user = "Kept approaches:\n" + "\n".join(lines) + f"\n\nAnalyst hunches:\n{hunches}"
    out = await structured_call(
        model=config.MODEL_OPUS,
        frame=_frame(ctx, ["mission", "scope", "scoring", "themes"], THEMER_I),
        user=user, schema=schemas.THEMES_SCHEMA, max_tokens=8192, effort="high",
    )
    result = []
    for t in out.get("themes", []):
        try:
            result.append(schemas.Theme(**_coerce_theme(t)).model_dump())
        except Exception:
            continue  # skip a theme too broken to repair, rather than crash the run
    return result


async def synthesize(ctx: dict[str, str], themes_list: list[dict[str, Any]]) -> dict[str, str]:
    import json
    user = "Themes and scores:\n" + json.dumps(themes_list, ensure_ascii=False, indent=2)
    out = await structured_call(
        model=config.MODEL_OPUS,
        frame=_frame(ctx, ["mission", "output_spec"], SYNTH_I),
        user=user, schema=schemas.SYNTH_SCHEMA, max_tokens=12000, effort="high",
    )
    return out
