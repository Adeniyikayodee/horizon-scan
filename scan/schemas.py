"""Pydantic models (client-side validation) and the JSON tool schemas the API
enforces. Every stage carries provenance (where, what, how) and a self-check,
and a dedicated Auditor cross-checks the whole chain for consistency.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Mark = Literal["strong", "partial", "weak"]
Overall = Literal["high", "medium", "low"]
Posture = Literal["enter", "watch", "deepen"]
Tag = Literal["existing", "adjacent", "new"]
VStatus = Literal["verified", "partial"]
Consistency = Literal["consistent", "overstated", "understated"]
Verdict_ = Literal["pass", "flag"]


# --- models used to validate tool output ---
class Candidate(BaseModel):
    name: str
    one_liner: str
    year: str = ""
    url: str = ""
    source_type: str = ""


class Reading(BaseModel):
    keep: bool
    keep_reason: str = ""      # why kept or dropped, so every drop is auditable
    band: Literal["frontier", "emerging", "maturing"] = "emerging"  # over-the-horizon band
    what: str = ""
    evidence: str = ""
    uptake: str = ""
    quotes: list[str] = Field(default_factory=list)
    locator: str = ""          # where on the page/report the approach sits
    verbatim: bool = False      # self-check: quotes copied exactly
    access_note: str = ""       # recency / how the page was reached


class Score(BaseModel):
    mandate_fit: Mark
    research_to_policy: Mark
    african_traction: Mark
    white_space: Mark
    reason_mandate: str
    reason_rtp: str
    reason_traction: str
    reason_whitespace: str
    overall: Overall
    evidence_basis: str = ""    # how: which fact/quote drives the marks
    self_check: Consistency = "consistent"
    self_check_note: str = ""


class Verdict(BaseModel):
    status: VStatus
    confirming_quote: str = ""
    note: str = ""
    primary_url: str = ""
    claim_supported: bool = False
    figure_check: str = ""      # any number checked against the source, or n/a
    discrepancies: list[str] = Field(default_factory=list)


class Audit(BaseModel):
    quote_supports_claim: bool
    score_matches_evidence: Consistency
    source_is_primary: bool
    verdict: Verdict_
    notes: str = ""


class Theme(BaseModel):
    name: str
    tag: Tag
    mandate_fit: Mark
    research_to_policy: Mark
    african_traction: Mark
    white_space: Mark
    posture: Posture
    rationale: str
    marquee: str = ""
    members: list[str] = Field(default_factory=list)
    top2: bool = False


# --- JSON schemas passed to the API as the `record` tool input_schema ---
def _mark(desc: str) -> dict:
    return {"type": "string", "enum": ["strong", "partial", "weak"], "description": desc}


_consistency = {"type": "string", "enum": ["consistent", "overstated", "understated"]}

SCOUT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["queries", "candidates"],
    "properties": {
        "queries": {"type": "array", "items": {"type": "string"},
                    "description": "The exact searches you ran, so the how is on record."},
        "candidates": {
            "type": "array",
            "description": "Named programs from THIS organization only. Empty if none fit.",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "one_liner", "year", "url", "source_type"],
                "properties": {
                    "name": {"type": "string"},
                    "one_liner": {"type": "string", "description": "One sentence on what it does."},
                    "year": {"type": "string", "description": "Year it appeared, within the recency window."},
                    "url": {"type": "string", "description": "Direct link to the org's own page."},
                    "source_type": {"type": "string",
                                    "enum": ["report", "webpage", "dataset", "press", "other"]},
                },
            },
        },
    },
}

READER_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["keep", "keep_reason", "band", "what", "evidence", "uptake", "quotes",
                 "locator", "verbatim", "access_note"],
    "properties": {
        "keep": {"type": "boolean",
                 "description": "True unless the document names no program, carries no concrete "
                                "evidence, or is off-lens. Earliness is not a reason to drop."},
        "keep_reason": {"type": "string",
                        "description": "One plain line on why, required either way, because every "
                                       "drop is reviewed."},
        "band": {"type": "string", "enum": ["frontier", "emerging", "maturing"],
                 "description": "frontier: researched/debated, not yet in mainstream policy. "
                                "emerging: in policy experimentation (pilots, new legislation, funder "
                                "priorities), not broadly adopted. maturing: now standard practice."},
        "what": {"type": "string", "description": "The single approach, one plain sentence."},
        "evidence": {"type": "string", "description": "What has been tested or shown."},
        "uptake": {"type": "string", "description": "Whether a government has adopted it."},
        "quotes": {"type": "array", "items": {"type": "string"},
                   "description": "Exact lines from the page backing the fields above."},
        "locator": {"type": "string",
                    "description": "Where on the page: the section heading, page number, or table."},
        "verbatim": {"type": "boolean",
                     "description": "True only if every quote is copied word for word from the source."},
        "access_note": {"type": "string",
                        "description": "How recent the page is, and anything about how it was reached."},
    },
}

SCORE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["mandate_fit", "research_to_policy", "african_traction", "white_space",
                 "reason_mandate", "reason_rtp", "reason_traction", "reason_whitespace",
                 "overall", "evidence_basis", "self_check", "self_check_note"],
    "properties": {
        "mandate_fit": _mark("Advances economic transformation through DEPTH."),
        "research_to_policy": _mark("Could be carried into real policy."),
        "african_traction": _mark("Genuine demand and uptake on the continent."),
        "white_space": _mark("Open ground for the institute to lead."),
        "reason_mandate": {"type": "string"},
        "reason_rtp": {"type": "string"},
        "reason_traction": {"type": "string"},
        "reason_whitespace": {"type": "string"},
        "overall": {"type": "string", "enum": ["high", "medium", "low"]},
        "evidence_basis": {"type": "string",
                           "description": "Which fact or quote from the reading drives these marks."},
        "self_check": {**_consistency,
                       "description": "Do the marks follow from the evidence, or are they over or understated."},
        "self_check_note": {"type": "string"},
    },
}

VERIFY_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["status", "confirming_quote", "note", "primary_url",
                 "claim_supported", "figure_check", "discrepancies"],
    "properties": {
        "status": {"type": "string", "enum": ["verified", "partial"],
                   "description": "verified only if the primary confirms the claim."},
        "confirming_quote": {"type": "string", "description": "The exact confirming line, or empty."},
        "note": {"type": "string", "description": "What was checked, or why it stays partial."},
        "primary_url": {"type": "string", "description": "The primary source you actually opened."},
        "claim_supported": {"type": "boolean",
                            "description": "True only if the primary directly supports the claim."},
        "figure_check": {"type": "string",
                         "description": "Any number checked against the source and the result, or n/a."},
        "discrepancies": {"type": "array", "items": {"type": "string"},
                          "description": "Anything in the claim the source did not support."},
    },
}

AUDIT_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["quote_supports_claim", "score_matches_evidence", "source_is_primary", "verdict", "notes"],
    "properties": {
        "quote_supports_claim": {"type": "boolean",
                                 "description": "Do the quoted lines actually support the stated approach."},
        "score_matches_evidence": {**_consistency,
                                    "description": "Do the scores follow from the evidence on record."},
        "source_is_primary": {"type": "boolean",
                              "description": "Is the source the institution's own document or domain."},
        "verdict": {"type": "string", "enum": ["pass", "flag"],
                    "description": "flag if any check fails, so a human looks again."},
        "notes": {"type": "string"},
    },
}

THEMES_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["themes"],
    "properties": {
        "themes": {
            "type": "array",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "tag", "mandate_fit", "research_to_policy",
                             "african_traction", "white_space", "posture", "rationale",
                             "marquee", "members", "top2"],
                "properties": {
                    "name": {"type": "string"},
                    "tag": {"type": "string", "enum": ["existing", "adjacent", "new"]},
                    "mandate_fit": _mark(""), "research_to_policy": _mark(""),
                    "african_traction": _mark(""), "white_space": _mark(""),
                    "posture": {"type": "string", "enum": ["enter", "watch", "deepen"]},
                    "rationale": {"type": "string"},
                    "marquee": {"type": "string", "description": "One leading approach."},
                    "members": {"type": "array", "items": {"type": "string"}},
                    "top2": {"type": "boolean", "description": "One of the two cleanest new areas."},
                },
            },
        }
    },
}

SYNTH_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["memo_markdown", "scorecard_intro"],
    "properties": {
        "memo_markdown": {"type": "string", "description": "The full short memo, markdown, house style."},
        "scorecard_intro": {"type": "string", "description": "One paragraph atop the scorecard."},
    },
}

LIBRARIAN_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["reports"],
    "properties": {"reports": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["title", "date", "url", "type"],
        "properties": {
            "title": {"type": "string"},
            "date": {"type": "string", "description": "Publication date or year."},
            "url": {"type": "string", "description": "Direct link, a PDF where one exists."},
            "type": {"type": "string",
                     "enum": ["report", "brief", "working paper", "annual report", "dataset", "other"]},
        }}}},
}

HUNCH_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["patterns"],
    "properties": {
        "patterns": {
            "type": "array",
            "description": "Cross-org patterns worth a human second look. Seed only.",
            "items": {
                "type": "object", "additionalProperties": False,
                "required": ["name", "note"],
                "properties": {"name": {"type": "string"}, "note": {"type": "string"}},
            },
        }
    },
}
