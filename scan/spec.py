"""The Scan Spec: the framework as editable data, not hardcoded rules.

A spec holds the research question, the lenses, the criteria (name + definition,
add/remove/rename), and the context. Agent frames and the Scorer/Themer JSON
schemas are generated from it at runtime, so criteria flow through scoring,
the scorecard, the map, the cards, and the charts. The default spec reproduces
today's ACET DEPTH frame, so behavior is unchanged until a researcher edits it.
"""
from __future__ import annotations

import json
from pathlib import Path

_MARKS = {"strong", "partial", "weak"}
_OVERALL = {"high", "medium", "low"}
_CONSIST = {"consistent", "overstated", "understated"}
_TAGS = {"existing", "adjacent", "new"}
_POST = {"deepen", "enter", "watch"}

BANDS = """## What "over the horizon" means

An over-the-horizon approach is either genuinely new, or an uncommon and better
way of doing something, that has moved out of pure research into early policy use
but is not yet mainstream. Sort candidates into three bands and keep the first two:

- Frontier: actively researched and debated, not yet in mainstream policy.
- Emerging: crossed into policy experimentation (pilots, new legislation, funder priorities), not yet broadly adopted.
- Maturing: now standard practice (green bonds, special economic zones, cash transfers). Screen these out."""

DEFAULT_SPEC = {
    "research_question": (
        "Which emerging or leading-edge approaches in international development should the hub "
        "enter, judged on their power to advance economic transformation and to carry evidence "
        "into policy?"),
    "lenses": (
        "# Mission\n\n"
        "You are a research scout for an African economic transformation hub, looking for new, "
        "over-the-horizon approaches in international development.\n\n"
        "## The two lenses\n\n"
        "Every approach must earn its place on two tests at once:\n"
        "1. Economic transformation in Africa, read through the DEPTH framework: Diversification, "
        "Export competitiveness, Productivity, Technology upgrading, and Human well-being.\n"
        "2. Research to policy: does it move evidence into real policy and practice?\n\n"
        "## The value-capture tilt\n\n"
        "Lean toward approaches that help Africa keep more of the value it creates in a new sector. "
        "Keep the bar high, we are after the few that matter."),
    "criteria": [
        {"key": "mandate_fit", "name": "Mandate fit",
         "definition": "Advances economic transformation as the hub defines it, GDP growth with gains in DEPTH.",
         "weight": 2},
        {"key": "research_to_policy", "name": "Research to policy",
         "definition": "The hub could turn it into policy advice that moves.", "weight": 2},
        {"key": "african_traction", "name": "African traction",
         "definition": "The continent shows real demand and uptake.", "weight": 1},
        {"key": "white_space", "name": "White space",
         "definition": "Open ground for the hub to lead. This is the primary screen.", "weight": 1},
    ],
    "context": (
        "# Scope\n\n"
        "## Standing rules\n"
        "- The recency window is a hard rule, stated in the standing rule that leads every frame: every "
        "report read and source cited must fall within it, and each source's date is always recorded. "
        "Primary sources only, the organization's own page, report, or database.\n"
        "- Existing programs stay out of scope. Keep looking past familiar ground toward open areas.\n"
        "- Favor funders, practitioners, and policy labs over the academic frontier.\n\n"
        "## Existing programs, treat as covered ground, never as new (posture deepen)\n"
        "Industrial policy; green industrialization and the just transition; financing Africa's future "
        "(development and blended finance, debt, domestic resource mobilization, adaptation finance); the "
        "digital economy, DPI, and AI; jobs, skills, and education including TVET; regional value chains "
        "including cotton and cocoa; health finance; the care economy.\n\n"
        "The goal is new areas to enter. An existing theme can never carry the enter posture, only deepen."),
}


# --- frame text built from the spec ---
def mission_text(spec: dict) -> str:
    return spec.get("lenses", "") + "\n\n" + BANDS


def scoring_text(spec: dict) -> str:
    lines = ["# Scoring\n",
             "Score each approach, and each theme, on these criteria using strong, partial, or weak:"]
    for c in spec.get("criteria", []):
        w = " (weighted most)" if c.get("weight", 1) >= 2 else ""
        lines.append(f"- {c['name']}{w}: {c.get('definition','')}")
    lines += ["",
              "Anchor the marks the same way for every organization, so scores compare across them: "
              "strong means a clear, direct fit carried by specific evidence in the source; partial "
              "means a plausible but indirect or thinly evidenced fit; weak means tangential, generic, "
              "or unsupported. When unsure between two marks, take the lower one.",
              "",
              "Give a one-line reason for every mark, drawn only from the evidence. Resolve the marks "
              "into an overall fit, high, medium, or low, leaning on the weighted criteria.",
              "",
              "Posture (themes only): enter, watch, or deepen. Enter marks a new or adjacent area worth "
              "entering or piloting now, watch marks one to monitor and revisit, and deepen marks existing "
              "work to keep current. Every existing theme is deepen; no existing theme may carry enter."]
    return "\n".join(lines)


def scope_text(spec: dict) -> str:
    return spec.get("context", "")


def criteria(spec: dict) -> list[dict]:
    return spec.get("criteria", []) or DEFAULT_SPEC["criteria"]


def criteria_keys(spec: dict) -> list[str]:
    return [c["key"] for c in criteria(spec)]


# --- dynamic JSON schemas generated from the criteria ---
def _mark_prop(desc: str = "") -> dict:
    return {"type": "string", "enum": ["strong", "partial", "weak"], "description": desc}


def score_schema(spec: dict) -> dict:
    props, req = {}, []
    for c in criteria(spec):
        k = c["key"]
        props[k] = _mark_prop(c.get("definition", ""))
        props["reason_" + k] = {"type": "string"}
        req += [k, "reason_" + k]
    props["overall"] = {"type": "string", "enum": ["high", "medium", "low"]}
    props["evidence_basis"] = {"type": "string", "description": "The fact or quote the marks rest on."}
    props["self_check"] = {"type": "string", "enum": ["consistent", "overstated", "understated"]}
    props["self_check_note"] = {"type": "string"}
    req += ["overall", "evidence_basis", "self_check", "self_check_note"]
    return {"type": "object", "additionalProperties": False, "required": req, "properties": props}


def themes_schema(spec: dict) -> dict:
    tprops = {
        "name": {"type": "string"},
        "tag": {"type": "string", "enum": ["existing", "adjacent", "new"]},
        "posture": {"type": "string", "enum": ["enter", "watch", "deepen"]},
        "rationale": {"type": "string"},
        "marquee": {"type": "string", "description": "One leading approach."},
        "members": {"type": "array", "items": {"type": "string"}},
        "top2": {"type": "boolean"},
    }
    treq = ["name", "tag", "posture", "rationale", "marquee", "members", "top2"]
    for c in criteria(spec):
        tprops[c["key"]] = _mark_prop()
        treq.append(c["key"])
    return {"type": "object", "additionalProperties": False, "required": ["themes"],
            "properties": {"themes": {"type": "array", "items": {
                "type": "object", "additionalProperties": False, "required": treq, "properties": tprops}}}}


DISCOVER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["organizations"],
    "properties": {"organizations": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["name", "type", "region", "why"],
        "properties": {
            "name": {"type": "string"},
            "type": {"type": "string", "description": "What kind of organization it is."},
            "region": {"type": "string"},
            "why": {"type": "string", "description": "One line on why it fits the research question."},
        }}}},
}


# --- dynamic coercion (defensive parsing over the criteria) ---
def _enum(v, allowed, default):
    s = str(v).lower().strip()
    return s if s in allowed else default


def _s(v):
    return v if isinstance(v, str) else ("" if v is None else str(v))


def coerce_score(out: dict, spec: dict) -> tuple[dict, list[str]]:
    r, coerced = {}, []
    for c in criteria(spec):
        k = c["key"]
        m = _enum(out.get(k), _MARKS, "partial")
        if str(out.get(k, "")).lower().strip() not in _MARKS:
            coerced.append("score." + k)
        r[k] = m
        r["reason_" + k] = _s(out.get("reason_" + k))
    r["overall"] = _enum(out.get("overall"), _OVERALL, "medium")
    r["evidence_basis"] = _s(out.get("evidence_basis"))
    r["self_check"] = _enum(out.get("self_check"), _CONSIST, "consistent")
    r["self_check_note"] = _s(out.get("self_check_note"))
    return r, coerced


def coerce_theme(t: dict, spec: dict) -> dict:
    t = dict(t)
    tag = str(t.get("tag", "")).lower().strip()
    posture = str(t.get("posture", "")).lower().strip()
    if tag in _POST and posture in _TAGS:
        tag, posture = posture, tag
    if tag not in _TAGS:
        tag = {"enter": "new", "deepen": "existing", "watch": "adjacent"}.get(tag, "new")
    if posture not in _POST:
        posture = {"existing": "deepen", "new": "enter", "adjacent": "enter"}.get(posture, "watch")
    if tag == "existing":
        posture = "deepen"
    t["tag"], t["posture"] = tag, posture
    for c in criteria(spec):
        t[c["key"]] = _enum(t.get(c["key"]), _MARKS, "partial")
    if not isinstance(t.get("members"), list):
        t["members"] = []
    for k, d in (("rationale", ""), ("marquee", ""), ("name", "Untitled theme"), ("top2", False)):
        t.setdefault(k, d)
    return t


# --- persistence per run ---
def save_spec(spec: dict, path) -> None:
    Path(path).write_text(json.dumps(spec, indent=2), encoding="utf-8")


def load_spec(path) -> dict:
    p = Path(path)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else dict(DEFAULT_SPEC)
