"""The Scan Spec: the framework as editable data, not hardcoded rules.

A spec holds the research question, the lenses, the criteria (name + definition,
add/remove/rename), and the context. Agent frames and the Scorer/Themer JSON
schemas are generated from it at runtime, so criteria flow through scoring,
the scorecard, the map, the cards, and the charts. The default spec reproduces
today's ACET DEPTH frame, so behavior is unchanged until a researcher edits it.
"""
from __future__ import annotations

import json
import re
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
        "You are a research scout for a pan-African economic transformation institute that is building "
        "a hub to find and shape new areas of focus, and you read every candidate as an economic "
        "development expert whose concern is human development and lasting economic prosperity in "
        "Africa. You are looking for new, over-the-horizon approaches in international development the "
        "institute could take forward.\n\n"
        "## What economic transformation means here\n\n"
        "Economic transformation is not growth alone, it is the structural change that makes growth "
        "durable and shared, an economy moving into more productive and more diverse activities, "
        "adding value to what it produces rather than exporting it raw, upgrading its technology, "
        "competing in export markets, and turning all of that into jobs and rising living standards. "
        "When you judge an approach, ask what it does for that structural change, and say so plainly.\n\n"
        "## The two lenses\n\n"
        "Every approach must earn its place on two tests at once:\n"
        "1. Economic transformation in Africa, read through the DEPTH framework, the working definition "
        "of transformation: Diversification into new and more complex sectors, Export competitiveness "
        "in what the economy makes, Productivity across firms and value chains, Technology upgrading in "
        "how things are produced, and Human well-being in incomes, jobs, and security. Name the DEPTH "
        "dimensions an approach advances, and how.\n"
        "2. Research to policy, does it move real evidence into real policy and practice, the "
        "translation the institute exists to do, not a paper that sits on a shelf.\n\n"
        "## The value-capture spine, and who owns it\n\n"
        "Lean hard toward approaches that help Africa keep more of the value it creates in a new "
        "sector, the processed product rather than the raw export, the active ingredient rather than "
        "the imported one, the royalty rather than the unpriced right. This is the spine of the "
        "search, because across most of these sectors the capital and the technical knowledge already "
        "sit with the multilateral financiers and the specialist providers, and the one question none "
        "of them is built to ask is who captures the value. That question is the institute's to own, "
        "so frame it as the body that defines and carries the value-capture agenda, not as a service "
        "downstream of the financiers. Where a theme is about fragility or the delivery of development "
        "rather than a sector, the spine is instead the institutional and delivery question, so name "
        "whichever genuinely fits.\n\n"
        "## Keep the bar high\n\n"
        "We are after the few approaches that matter, not a long list. An approach earns a place only "
        "when it is real, recent, and evidenced, when it advances transformation or the move from "
        "evidence to policy, and when it opens ground the institute could lead rather than crowded "
        "ground or its own existing work."),
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
        "- Favor funders, practitioners, and policy labs over the academic frontier."),
    # The institute's existing portfolio, as DATA. This one list has two consumers:
    # excluded_text() renders it into every agent frame, and screen_existing()
    # enforces it in code after theming. The prompt and the gate therefore read the
    # same source and cannot drift, the pattern config.window_rule() already uses
    # for the recency window.
    "excluded_areas": [
        {"name": "Industrial policy and productive transformation",
         "terms": ["industrial policy", "industrial strategy", "productive transformation",
                   "manufacturing policy", "structural transformation policy"]},
        {"name": "Green industrialization and the just transition",
         "terms": ["green industrialization", "green industrialisation", "just transition",
                   "climate industrial policy", "green growth strategy"]},
        {"name": "Financing Africa's future",
         "terms": ["blended finance", "development finance", "domestic resource mobilization",
                   "domestic resource mobilisation", "debt sustainability", "sovereign debt",
                   "illicit financial flows", "adaptation finance", "natural capital finance",
                   "tax policy", "compact with africa"]},
        {"name": "Digital economy, DPI, and AI",
         "terms": ["digital economy", "digital public infrastructure", "dpi", "artificial intelligence",
                   "ai", "machine learning", "data governance", "digital policy", "digitalization",
                   "digitalisation", "digital transformation", "synthetic data"]},
        {"name": "Jobs, skills, and education",
         "terms": ["tvet", "skills development", "education quality", "youth employment",
                   "jobs and skills", "vocational training", "workforce development",
                   "labour market policy", "labor market policy"]},
        {"name": "Regional value chains and regional integration",
         "terms": ["regional value chain", "regional integration", "cotton value chain",
                   "cocoa value chain", "afcfta", "continental free trade"]},
        {"name": "Health finance",
         "terms": ["health financing", "health finance", "universal health coverage"]},
        {"name": "Care economy",
         "terms": ["care economy", "unpaid care", "care work"]},
    ],
    # The memo's shape, as DATA. One definition with two consumers: the Synthesizer's
    # frame renders the heading list from it, and memo_shortfall() checks the delivered
    # memo against it. Before this the length lived in two places, agents.SYNTH_I asked
    # for eight to twelve pages while context/output_spec.md asked for two to three, and
    # the model followed the shorter. Length is stated ONCE, in words, and the page
    # figure shown to the model is derived from it, so the two cannot disagree.
    "memo": {
        "min_words": 4000,
        "words_per_page": 450,
        "sections": [
            {"heading": "Executive summary",
             "guidance": "Open with the recommendation itself, then the shape of the set, in three "
                         "or four substantial paragraphs. Lead hard on the two cleanest areas to "
                         "enter first, name them plainly, and frame the remaining entry themes as a "
                         "second tier, each a tightly scoped brief rather than a full program, so "
                         "the recommendation reads as genuinely concentrated."},
            {"heading": "Purpose and scope",
             "guidance": "A paragraph on the question the scan answers and what counts as in scope "
                         "or out."},
            {"heading": "Method in brief",
             "guidance": "A short paragraph on how the scan worked, the criteria, and the postures "
                         "(enter, watch, and deepen)."},
            {"heading": "The map at a glance",
             "guidance": "Describe the shape, how many themes are deepen (the existing work), how "
                         "many carry the enter posture, how many sit at watch, and the logic that "
                         "runs through the entry set."},
            {"heading": "The leading approaches",
             "guidance": "This is the heart of the memo. Lead with the two cleanest entry themes and "
                         "give each the fullest treatment, then take the remaining entry themes in "
                         "turn and treat each as a second-tier, tightly scoped brief. Give each theme "
                         "SEVERAL developed paragraphs that lay out where the opening lies and why it "
                         "stands open, the evidence with the named institutions and initiatives behind "
                         "it drawn from the member details, who already holds the capital and the "
                         "technical layer, how it advances the mandate read through the DEPTH lens "
                         "(Diversification, Export competitiveness, Productivity, Technology "
                         "upgrading, and Human well-being), the institute's distinctive contribution "
                         "as the one defining the value-capture or institutional agenda, and a "
                         "concrete first step. Do not reduce a theme to a single paragraph."},
            {"heading": "What the institute already runs",
             "guidance": "The existing, deepen themes, and for each the leading methods worth keeping "
                         "current. Where a theme was held back to existing work by the portfolio "
                         "screen, treat it here as current work to keep sharp, not as an opportunity."},
            {"heading": "What to watch",
             "guidance": "The watch themes, each with a clear line on what keeps it a step below the "
                         "entry tier."},
            {"heading": "Cross-cutting findings",
             "guidance": "Develop the logic that ties the entry set together, and be honest that it "
                         "is not a single idea: value capture is the spine of the sector themes, "
                         "where Africa can retain more of the value it creates, while the themes "
                         "about fragility and the delivery of development rest on a distinct "
                         "institutional and delivery logic, so present these as two clear threads "
                         "rather than forcing one over all of them. Then develop the pattern in where "
                         "the capital and the technical capacity sit, and how the entry set maps onto "
                         "the mandate."},
            {"heading": "Risks and sequencing",
             "guidance": "Name plainly what could go wrong across the entry set, that value-capture "
                         "policy is politically demanding, that several of these sectors are crowded "
                         "with funders, and that the institute's leverage is a convening and "
                         "agenda-setting one in some themes, then set out a realistic sequence "
                         "against a small team, what moves in the first quarter, what follows, and "
                         "what waits."},
            {"heading": "Conclusion and recommendations",
             "guidance": "A sequenced set of concrete moves, ordered by readiness and effort."},
        ],
    },
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


def excluded_areas(spec: dict) -> list[dict]:
    return spec.get("excluded_areas") or DEFAULT_SPEC["excluded_areas"]


def excluded_text(spec: dict) -> str:
    """The existing-portfolio list rendered for the agent frame, built from the SAME
    data screen_existing() enforces, so what the model is told and what the code
    checks are one list."""
    lines = ["## Existing programs, treat as covered ground, never as new (posture deepen)",
             "",
             "The institute already works in these areas. A theme that falls in one of them is "
             "existing and carries the deepen posture, never enter, and it is screened in code "
             "after theming, so tagging it new does not get it past the gate:"]
    for a in excluded_areas(spec):
        lines.append(f"- {a['name']}: {', '.join(a.get('terms', []))}")
    lines += ["",
              "The goal is new areas to enter. An existing theme can never carry the enter "
              "posture, only deepen."]
    return "\n".join(lines)


def scope_text(spec: dict) -> str:
    return spec.get("context", "") + "\n\n" + excluded_text(spec)


# --- the existing-portfolio screen, enforced in code -----------------------------
_WORD = re.compile(r"[a-z0-9]+")


def _singular(w: str) -> str:
    """Crude singularization, applied to BOTH the text and the term, so 'regional
    value chains' matches the term 'regional value chain' and 'policies' matches
    'policy'. Being symmetric is what makes the crudeness safe: both sides land on
    the same wrong stem, so nothing is missed and nothing new is matched."""
    if len(w) > 4 and w.endswith("ies"):
        return w[:-3] + "y"
    if len(w) > 4 and w.endswith(("sses", "shes", "ches", "xes")):
        return w[:-2]
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
        return w[:-1]
    return w


def _norm_text(*parts) -> str:
    """Lowercased, singularized word stream, space padded at both ends, so a plain
    `in` test on a space-padded needle matches whole words only ('ai' hits
    'AI-driven', never 'aid')."""
    words = []
    for p in parts:
        if isinstance(p, (list, tuple)):
            p = " ".join(str(x) for x in p)
        words += [_singular(w) for w in _WORD.findall(str(p or "").lower())]
    return " " + " ".join(words) + " "


def _term_hits(text: str, terms: list[str]) -> list[str]:
    hits = []
    for t in terms:
        needle = _norm_text(t)
        if needle.strip() and needle in text:
            hits.append(t)
    return hits


def screen_existing(themes: list[dict], spec: dict) -> list[dict]:
    """Force any theme that lands in the institute's existing portfolio to
    tag=existing, posture=deepen, and record why.

    The model is asked to tag existing areas honestly and mostly does not, so this
    is the gate rather than the request. Deliberately keyword based and not a model
    call: an analyst can read the term list, argue with it, and edit it in the spec,
    which is not true of a similarity score.

    Confidence rule. A term in the theme's NAME is decisive on its own, because a
    theme named for an existing area is that area. A term only in the rationale,
    marquee, or member names screens only when two distinct terms hit, so one
    passing mention of 'development finance' inside a long rationale does not kill a
    genuinely new theme. A single body hit is recorded as a near miss instead, for
    the analyst to look at.
    """
    for t in themes:
        name = _norm_text(t.get("name", ""))
        body = _norm_text(t.get("name", ""), t.get("rationale", ""), t.get("marquee", ""),
                          t.get("members") or [])
        matched, near = None, []
        for a in excluded_areas(spec):
            terms = a.get("terms", [])
            in_name = _term_hits(name, terms)
            in_body = _term_hits(body, terms)
            if in_name:
                matched = (a["name"], in_name[0], "name")
                break
            if len(in_body) >= 2:
                matched = (a["name"], in_body[0], "rationale")
                break
            if in_body:
                near.append(f"{a['name']} (\"{in_body[0]}\")")
        if matched:
            t["tag"], t["posture"], t["top2"] = "existing", "deepen", False
            t["screened"] = (f"matched the existing portfolio: {matched[0]}, "
                             f"on \"{matched[1]}\" in the theme {matched[2]}")
        elif near:
            t["screen_note"] = "sits near the existing portfolio: " + "; ".join(near[:3])
    return themes


# --- the memo's shape, one definition, two consumers -----------------------------
def memo_spec(spec: dict) -> dict:
    return spec.get("memo") or DEFAULT_SPEC["memo"]


def memo_target_text(spec: dict) -> str:
    """The length instruction shown to the model, DERIVED from the same word count
    memo_shortfall() checks against, so the ask and the check cannot disagree."""
    m = memo_spec(spec)
    words = int(m.get("min_words", 4000))
    pages = max(1, round(words / max(1, int(m.get("words_per_page", 450)))))
    return f"at least about {pages} pages, roughly {words:,} words"


def memo_sections_text(spec: dict) -> str:
    lines = []
    for s in memo_spec(spec).get("sections", []):
        lines.append(f"## {s['heading']}")
        if s.get("guidance"):
            lines.append(s["guidance"])
    return "\n".join(lines)


def memo_shortfall(markdown: str, spec: dict) -> str:
    """What the delivered memo is missing against the spec, or '' when it is whole.
    The memos before this check ran a median of about three pages against a stated
    eight to twelve, and nothing noticed."""
    m = memo_spec(spec)
    words = len(re.findall(r"\S+", markdown or ""))
    floor = int(m.get("min_words", 4000))
    headings = {h.strip().lower() for h in re.findall(r"^##\s+(.+?)\s*$", markdown or "", re.M)}
    missing = [s["heading"] for s in m.get("sections", [])
               if s["heading"].strip().lower() not in headings]
    problems = []
    if words < floor:
        problems.append(f"{words:,} words against a floor of {floor:,}")
    if missing:
        problems.append("missing section(s): " + ", ".join(missing))
    return "; ".join(problems)


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
