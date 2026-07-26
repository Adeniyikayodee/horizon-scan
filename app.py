"""Web app for non-technical users, styled to the Horizon Scan identity.

Four plain steps: upload a list, run the scan, review, generate the report.
No terminal, no key on the user's side. The key lives in server secrets.

Run locally:   streamlit run app.py
Deploy:        Streamlit Community Cloud or Hugging Face Spaces, set secrets.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid

import pandas as pd
import streamlit as st

from scan import agents, client, config, io_xlsx, pdf_out, pipeline, sources, spec


def _secret(key: str, default: str = "") -> str:
    try:
        return st.secrets[key]
    except Exception:
        return os.environ.get(key, default)


# keys come from server secrets, never from the user
config.API_KEY = _secret("ANTHROPIC_API_KEY", config.API_KEY)
config.OPENROUTER_API_KEY = _secret("OPENROUTER_API_KEY", config.OPENROUTER_API_KEY)
APP_PASSWORD = _secret("APP_PASSWORD", "")

st.set_page_config(page_title="Horizon Scan", page_icon="🧭", layout="wide")

# --- identity: a survey-instrument look, single committed light theme ---
CSS = """
<style>
:root{
  --bg:#EFF1EB; --panel:#F4F6F0; --panel-2:#EDF0E8;
  --ink:#16201C; --ink-soft:#3B463F; --muted:#6C766C;
  --line:#D3D8CC; --line-soft:#E0E4DA;
  --accent:#12605A; --accent-2:#1C7E72; --accent-ghost:rgba(18,96,90,.10);
  --amber:#A96A22; --ok:#2C8A69; --ok-bg:rgba(44,138,105,.13);
  --warn:#B4842A; --warn-bg:rgba(180,132,42,.15); --bad:#A5564A; --bad-bg:rgba(165,86,74,.12);
  --serif:'Iowan Old Style','Palatino Linotype',Palatino,'Book Antiqua',Georgia,serif;
  --sans:'Avenir Next','Avenir','Segoe UI',system-ui,-apple-system,sans-serif;
  --mono:'SF Mono','JetBrains Mono',ui-monospace,Menlo,monospace;
}
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"], header{display:none!important}
.stApp{background:var(--bg)}
html, body, [class*="css"]{font-family:var(--sans);color:var(--ink)}
.block-container{padding-top:1.4rem;max-width:1080px}
h1,h2,h3{font-family:var(--serif);color:var(--ink);letter-spacing:.01em}
[data-testid="stSidebar"]{background:var(--panel-2);border-right:1px solid var(--line)}
.stButton>button{font-family:var(--sans);font-weight:600;border-radius:9px;border:1px solid var(--line);
  background:var(--panel);color:var(--ink);padding:.5rem 1rem;transition:.15s}
.stButton>button:hover{border-color:var(--accent);color:var(--accent)}
.stButton>button[kind="primary"]{background:var(--accent);border-color:var(--accent);color:#fff}
.stButton>button[kind="primary"]:hover{background:var(--accent-2);border-color:var(--accent-2);color:#fff}
[data-testid="stDataFrame"],[data-testid="stDataEditor"]{border:1px solid var(--line);border-radius:11px}

.hs-mast{display:flex;align-items:center;gap:13px;margin:0 0 2px}
.hs-word{font-family:var(--serif);font-size:22px;font-weight:600;letter-spacing:.02em}
.hs-word b{color:var(--accent)}
.hs-eyebrow{font-family:var(--mono);font-size:11px;letter-spacing:.2em;text-transform:uppercase;
  color:var(--accent);font-weight:600;margin:22px 0 4px}
.hs-tag{font-family:var(--mono);font-size:11px;color:var(--muted);letter-spacing:.04em}
.hs-rule{height:1px;background:linear-gradient(90deg,var(--accent),transparent);margin:10px 0 6px}

.hs-step{display:flex;gap:11px;align-items:flex-start;padding:9px 0}
.hs-step .m{width:24px;height:24px;border-radius:50%;flex:none;display:grid;place-items:center;
  font-family:var(--mono);font-size:12px;border:1.5px solid var(--line);color:var(--muted);background:var(--panel)}
.hs-step.done .m{background:var(--accent);border-color:var(--accent);color:#fff}
.hs-step.active .m{border-color:var(--accent);color:var(--accent)}
.hs-step .l{font-size:13.5px;font-weight:600;padding-top:2px}
.hs-step.upcoming .l{color:var(--muted);font-weight:500}
.hs-step .s{font-size:11px;color:var(--muted);font-family:var(--mono);font-weight:500}

.hs-tiles{display:grid;grid-template-columns:repeat(auto-fit,minmax(118px,1fr));gap:12px;margin:6px 0 16px}
.hs-tile{border:1px solid var(--line);border-radius:11px;padding:12px 14px;background:var(--panel)}
.hs-tile .k{font-family:var(--mono);font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:var(--muted)}
.hs-tile .v{font-family:var(--serif);font-size:25px;font-weight:600;font-variant-numeric:tabular-nums;line-height:1.1;margin-top:4px}
.hs-lane{display:flex;align-items:center;gap:12px;border:1px solid var(--line);border-radius:10px;
  padding:10px 14px;background:var(--panel);margin-bottom:8px}
.hs-lane .nm{font-size:13.5px;font-weight:600;flex:1;min-width:0;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.hs-pipe{display:flex;gap:5px;flex:none}
.hs-st{font-family:var(--mono);font-size:10px;padding:3px 8px;border-radius:20px;border:1px solid var(--line);
  color:var(--muted);background:var(--panel);letter-spacing:.02em;transition:.2s}
.hs-st.done{color:#fff;background:var(--accent);border-color:var(--accent)}
.hs-st.run{color:var(--accent);border-color:var(--accent);background:var(--accent-ghost);
  position:relative;padding-left:17px}
.hs-st.run::before{content:"";position:absolute;left:6px;top:50%;width:5px;height:5px;border-radius:50%;
  background:var(--accent);transform:translateY(-50%);animation:hspulse 1s infinite}
@keyframes hspulse{0%,100%{opacity:.25}50%{opacity:1}}
.hs-note{font-family:var(--mono);font-size:10.5px;color:var(--muted);flex:none;
  min-width:90px;text-align:right;letter-spacing:.02em}
.hs-chip{font-family:var(--mono);font-size:10px;padding:3px 9px;border-radius:20px;flex:none}
.hs-chip.ok{color:var(--ok);background:var(--ok-bg)}
.hs-chip.mut{color:var(--muted);background:var(--line-soft)}
.hs-chip.flag{color:var(--bad);background:var(--bad-bg)}
.hs-chip.partial{color:var(--warn);background:var(--warn-bg)}
.hs-doc{display:flex;justify-content:space-between;align-items:center;border:1px solid var(--line);
  border-radius:10px;padding:12px 15px;background:var(--panel);margin-bottom:8px}
.hs-doc .t{font-size:14px;font-weight:600}
.hs-doc .s{font-family:var(--mono);font-size:11px;color:var(--muted)}

.hs-card{border:1px solid var(--line);border-radius:10px;padding:13px 15px;background:var(--panel);margin:10px 0}
.hs-card .an{font-size:14.5px;font-weight:700;margin-bottom:2px}
.hs-trail{display:flex;gap:6px;flex-wrap:wrap;margin:8px 0 10px}
.hs-trail .t{font-family:var(--mono);font-size:10px;color:var(--muted);border:1px solid var(--line);
  border-radius:20px;padding:2px 9px}
.hs-trail .t b{color:var(--accent)}
.hs-trail .t.arw{border:none;color:var(--accent);opacity:.6;padding:2px 1px}
.hs-kv{font-size:12.5px;color:var(--ink-soft);margin:4px 0;line-height:1.55}
.hs-kv b{color:var(--ink)}
.hs-quote{border-left:2px solid var(--accent);padding:3px 0 3px 11px;margin:5px 0;
  color:var(--ink-soft);font-size:12.5px;font-style:italic}
.hs-mark{font-family:var(--mono);font-size:11px;font-weight:700}
.hs-mark.strong{color:var(--ok)}.hs-mark.partial{color:var(--warn)}.hs-mark.weak{color:var(--bad)}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)

COMPASS = ('<svg width="30" height="30" viewBox="0 0 40 40" fill="none">'
           '<circle cx="20" cy="20" r="18" stroke="#12605A" stroke-width="1.5"/>'
           '<circle cx="20" cy="20" r="2.2" fill="#12605A"/>'
           '<path d="M20 5 L23 20 L20 35 L17 20 Z" fill="#12605A" opacity=".9"/>'
           '<path d="M5 20 L20 17 L35 20 L20 23 Z" fill="#12605A" opacity=".35"/></svg>')


# The one model the app runs on: reliable and cheap on OpenRouter's web plugin.
# If you add an ANTHROPIC_API_KEY, set this to "anthropic/claude-opus-4.8" and it
# auto-routes to the native Anthropic path (best quality).
DEFAULT_MODEL = "openai/gpt-4o-mini"


def masthead() -> None:
    st.markdown('<div class="hs-rule" style="margin-top:6px"></div>', unsafe_allow_html=True)


def stepper(step: int) -> None:
    labels = [("Organization list", "your targets"), ("Run the scan", "agents fan out"),
              ("Review findings", "you decide"), ("Generate report", "the deliverables")]
    html = ""
    for i, (lab, sub) in enumerate(labels, start=1):
        state = "done" if step > i else "active" if step == i else "upcoming"
        mark = "✓" if step > i else str(i)
        html += (f'<div class="hs-step {state}"><div class="m">{mark}</div>'
                 f'<div><div class="l">{lab}</div><div class="s">{sub}</div></div></div>')
    st.markdown(html, unsafe_allow_html=True)


def eyebrow(kicker: str, heading: str) -> None:
    st.markdown(f'<div class="hs-eyebrow">{kicker}</div>', unsafe_allow_html=True)
    st.markdown(f"### {heading}")


def read_payloads() -> list[dict]:
    out = []
    for p in sorted(config.ORGS_WORK.glob("*.jsonl")):
        try:
            out.append(json.loads(p.read_text()))
        except Exception:
            pass
    return out


def tiles_html(pairs: list[tuple[str, int]]) -> str:
    cells = ""
    for k, v in pairs:
        col = ('style="color:var(--ok)"' if k == "Verified"
               else 'style="color:var(--muted)"' if k == "Set aside" else "")
        cells += f'<div class="hs-tile"><div class="k">{k}</div><div class="v" {col}>{v}</div></div>'
    return f'<div class="hs-tiles">{cells}</div>'


def lanes_html(states: list[dict]) -> str:
    html = ""
    for s in states:
        pipe = "".join(
            f'<span class="hs-st {s.get(stage, "pending")}">{stage}</span>'
            for stage in ("scout", "read", "score", "verify", "audit"))
        complete = s.get("note") == "complete"
        if complete:
            kept = s.get("kept", 0)
            right = (f'<span class="hs-chip ok">{kept} kept</span>' if kept
                     else '<span class="hs-chip mut">none</span>')
            note = ""
        else:
            right = ""
            note = f'<span class="hs-note">{s.get("note", "")}</span>'
        html += (f'<div class="hs-lane"><span class="nm">{s.get("org","")}</span>'
                 f'<span class="hs-pipe">{pipe}</span>{note}{right}</div>')
    return html


def _slug(s: str) -> str:
    return (re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")[:60]) or "item"


def provenance_html(r: dict) -> str:
    v = r.get("verification", {}) or {}
    a = r.get("audit", {}) or {}
    nq = len(r.get("quotes", []))
    chain = [f'Scout <b>found</b>', f'Reader <b>{nq} quotes</b>',
             f'Scorer <b>{r.get("overall","")}</b>', f'Verifier <b>{v.get("status","")}</b>',
             f'Auditor <b>{a.get("verdict","")}</b>']
    inner = '<span class="t arw">→</span>'.join(f'<span class="t">{c}</span>' for c in chain)
    return f'<div class="hs-trail">{inner}</div>'


def card_html(r: dict) -> str:
    s = r.get("score", {}) or {}
    v = r.get("verification", {}) or {}
    a = r.get("audit", {}) or {}
    status = v.get("status", "")
    chip = ('<span class="hs-chip ok">verified</span>' if status == "verified"
            else '<span class="hs-chip partial">partial</span>')
    flagchip = '<span class="hs-chip flag">flagged</span>' if r.get("flagged") else ""
    band = r.get("band", "")
    bandchip = f'<span class="hs-tag" style="text-transform:uppercase;letter-spacing:.1em">{band}</span>' if band else ""

    def mk(f: str) -> str:
        m = s.get(f, "")
        return f'<span class="hs-mark {m}">{m}</span>'

    marks = " &nbsp;·&nbsp; ".join(f'{c["name"].lower()} {mk(c["key"])}'
                                   for c in config.active_spec().get("criteria", []))
    quotes = "".join(f'<div class="hs-quote">"{q}"</div>' for q in r.get("quotes", []))
    url = r.get("url", "")
    src_label = "Source PDF" if url.lower().endswith(".pdf") else "Source page"

    # where + a system-stamped access date (never the model's guess)
    where = f'<div class="hs-kv"><b>Where.</b> {r.get("locator","")}</div>' if r.get("locator") else ""
    acc, src_dated = r.get("accessed", ""), r.get("access_note", "")
    yr = r.get("source_year")
    win = f"{config.YEAR_MIN}&ndash;{config.YEAR_MAX}"
    # date signal: confirmed in the recency window, or undated (flagged, not silent)
    if yr:
        datechip = f' &nbsp;·&nbsp; <span style="color:var(--good)">source dated {yr}, in {win}</span>'
    elif r.get("date_status") == "undated":
        datechip = f' &nbsp;·&nbsp; <span style="color:var(--bad)">date not confirmed in {win}</span>'
    else:
        datechip = (f' &nbsp;·&nbsp; source dated {src_dated}' if src_dated else "")
    accessed = (f'<div class="hs-kv"><b>Accessed.</b> {acc}' + datechip + "</div>") if acc else ""
    # how it searched
    q = r.get("queries", []) or (r.get("trail", {}).get("scout", {}) or {}).get("queries", [])
    how = (f'<div class="hs-kv"><b>How it searched.</b> ' + "; ".join(q) + '</div>') if q else ""
    # scorer basis + self-check
    basis = (f'<div class="hs-kv"><b>Marks rest on.</b> {s.get("evidence_basis","")} '
             f'<span class="hs-tag">(self-check: {s.get("self_check","")})</span></div>'
             if s.get("evidence_basis") else "")
    # verifier detail
    conf = v.get("confirming_quote", "")
    conf_html = (f'<div class="hs-kv" style="margin-top:6px"><b>Verifier confirmed on the primary:</b></div>'
                 f'<div class="hs-quote">"{conf}"</div>' if conf
                 else (f'<div class="hs-kv"><b>Verifier note.</b> {v.get("note","")}</div>' if v.get("note") else ""))
    g = r.get("quote_grounded")
    ground_html = ('<div class="hs-kv"><b>Quote check.</b> found in the source</div>' if g is True
                   else '<div class="hs-kv" style="color:var(--bad)"><b>Quote check.</b> not found in the source</div>'
                   if g is False else "")
    disc = v.get("discrepancies", [])
    disc_html = (f'<div class="hs-kv"><b>Discrepancies.</b> ' + "; ".join(disc) + '</div>') if disc else ""
    fig = (f'<div class="hs-kv"><b>Figure check.</b> {v.get("figure_check","")}</div>'
           if v.get("figure_check") and v.get("figure_check") != "n/a" else "")
    # consistency (auditor)
    checks = (f'quote supports claim: {"yes" if a.get("quote_supports_claim") else "no"} &nbsp;·&nbsp; '
              f'score vs evidence: {a.get("score_matches_evidence","")} &nbsp;·&nbsp; '
              f'primary source: {"yes" if a.get("source_is_primary") else "no"}')
    audit_html = (f'<div class="hs-kv" style="margin-top:6px"><b>Consistency check.</b> '
                  f'{a.get("verdict","")} &nbsp; <span class="hs-tag">{checks}</span></div>') if a else ""
    flags = r.get("flags", [])
    flags_html = (f'<div class="hs-kv" style="color:var(--bad)"><b>Needs a look.</b> ' + "; ".join(flags) + '</div>') if flags else ""

    if r.get("report_title"):
        rt = r.get("report_title", "") + (f' ({r.get("report_date","")})' if r.get("report_date") else "")
        if r.get("source_reachable") is False:  # matched a report but could not open it
            report_html = (f'<div class="hs-kv" style="color:var(--muted)"><b>Report link.</b> {rt} '
                           "&mdash; could not be opened directly, read from search results instead</div>")
        else:
            report_html = f'<div class="hs-kv"><b>Read from report.</b> {rt}</div>'
    else:
        report_html = ""
    src = (f'<div class="hs-kv" style="margin-top:6px"><b>{src_label}.</b> '
           f'<a href="{url}" target="_blank">{url}</a></div>' if url else "")
    if r.get("dead_url") and not url:
        reach_html = ('<div class="hs-kv" style="color:var(--bad)"><b>No confirmed source.</b> '
                      'the proposed link did not resolve and was removed, treat this row as unconfirmed</div>')
    elif r.get("source_reachable") is False:
        reach_html = ('<div class="hs-kv" style="color:var(--bad)"><b>Coverage note.</b> the source '
                      'could not be read directly, this row rests on search results</div>')
    else:
        reach_html = ""
    return (f'<div class="hs-card"><div class="an">{r.get("name","")} &nbsp;{chip}{flagchip} {bandchip}</div>'
            + provenance_html(r)
            + f'<div class="hs-kv"><b>What it is.</b> {r.get("what","")}</div>'
            + f'<div class="hs-kv"><b>Evidence.</b> {r.get("evidence","")}</div>'
            + f'<div class="hs-kv">{marks}</div>' + basis + where + accessed + how
            + (f'<div class="hs-kv" style="margin-top:6px"><b>Lines it took, straight from the source:</b></div>{quotes}'
               if quotes else "")
            + conf_html + ground_html + fig + disc_html + audit_html + flags_html + report_html + src + reach_html + '</div>')


def dossier_md(r: dict) -> str:
    s = r.get("score", {}) or {}
    v = r.get("verification", {}) or {}
    a = r.get("audit", {}) or {}
    L = [f"# {r.get('name','')}", "",
         f"- Organization: {r.get('org','')}", f"- Year: {r.get('year','')}",
         f"- Band: {r.get('band','')}",
         f"- Overall fit: {r.get('overall','')}", f"- Verification: {v.get('status','')}",
         f"- Flagged for review: {'yes' if r.get('flagged') else 'no'}",
         f"- Source: {r.get('url','')}  ({r.get('source_type','')})", "",
         "## What it is", r.get("what", ""), "",
         "## Evidence", r.get("evidence", ""),
         f"Government uptake: {r.get('uptake','')}", "",
         "## Where, on the source",
         f"- Locator: {r.get('locator','')}",
         f"- Accessed: {r.get('accessed','')}",
         (f"- Source dated: {r.get('source_year')}, within the {config.YEAR_MIN}-{config.YEAR_MAX} window"
          if r.get("source_year") else
          f"- Source date: not confirmed within the {config.YEAR_MIN}-{config.YEAR_MAX} window"
          + (f" (stated: {r.get('access_note','')})" if r.get("access_note") else "")),
         f"- Quotes verbatim: {'yes' if r.get('verbatim') else 'not confirmed'}", "",
         "## How it searched",
         *[f"- {q}" for q in (r.get('queries', []) or [])], "",
         "## Scores, with reasons",
         f"- Mandate fit: {s.get('mandate_fit','')} — {s.get('reason_mandate','')}",
         f"- Research to policy: {s.get('research_to_policy','')} — {s.get('reason_rtp','')}",
         f"- African traction: {s.get('african_traction','')} — {s.get('reason_traction','')}",
         f"- White space: {s.get('white_space','')} — {s.get('reason_whitespace','')}",
         f"- Marks rest on: {s.get('evidence_basis','')}",
         f"- Scorer self-check: {s.get('self_check','')} ({s.get('self_check_note','')})", ""]
    if r.get("quotes"):
        L += ["## Lines taken from the source"] + [f"> {q}" for q in r["quotes"]] + [""]
    L += ["## Verification (adversarial)",
          f"- Primary opened: {v.get('primary_url','')}",
          f"- Claim supported: {'yes' if v.get('claim_supported') else 'no'}",
          f"- Figure check: {v.get('figure_check','')}"]
    if v.get("confirming_quote"):
        L += [f"- Confirming line: \"{v['confirming_quote']}\""]
    if v.get("discrepancies"):
        L += [f"- Discrepancy: {d}" for d in v["discrepancies"]]
    if v.get("note"):
        L += [f"- Note: {v['note']}"]
    L += ["", "## Consistency check (auditor)",
          f"- Verdict: {a.get('verdict','')}",
          f"- Quote supports claim: {'yes' if a.get('quote_supports_claim') else 'no'}",
          f"- Score vs evidence: {a.get('score_matches_evidence','')}",
          f"- Source is primary: {'yes' if a.get('source_is_primary') else 'no'}",
          f"- Notes: {a.get('notes','')}"]
    if r.get("flags"):
        L += ["", "## Needs a look"] + [f"- {f}" for f in r["flags"]]
    return "\n".join(L)


# posture -> (color, the decision it implies), the recommendation layer
_POSTURE = {"enter": ("#12605A", "Enter or pilot"),
            "watch": ("#C0862B", "Watch"),
            "deepen": ("#8A938C", "Deepen, existing")}
# the ordinal marks -> (numeric, color on a single-hue intensity ramp, text color)
# weak is a clearly-present light sage, not near-white, so it never reads as missing data
_MARKV = {"weak": (1, "#CAD7D1", "#4C5A55"), "partial": (2, "#7FB0A6", "#0C3E39"),
          "strong": (3, "#12605A", "#FFFFFF")}


def render_theme_charts(scorecard_path) -> None:
    """Two coordinated, overlap-free views built to drive the decision: a ranked
    'where to enter' bar (themes by weighted fit, colored by posture) and a
    decision-matrix heatmap (themes x criteria, each cell colored by its mark).
    Both scale to many themes and follow whatever criteria the spec defines."""
    import plotly.graph_objects as go

    crit = config.active_spec().get("criteria", [])
    names = [c["name"] for c in crit]
    if len(names) < 2:
        return
    df = pd.read_excel(scorecard_path, skiprows=2)
    if df.empty or "Theme" not in df.columns:
        return
    weights = {c["name"]: (2 if c.get("weight", 1) >= 2 else 1) for c in crit}
    post_rank = {"enter": 0, "watch": 1, "deepen": 2}

    def mark_num(v):
        return _MARKV.get(str(v).strip().lower(), (None, None, None))[0]

    # per-theme composite: weighted marks as a 0-100 fit score
    recs = []
    for _, row in df.iterrows():
        nums = {n: mark_num(row.get(n)) for n in names}
        got = sum(weights[n] * (nums[n] or 0) for n in names)
        cap = sum(weights[n] * 3 for n in names) or 1
        post = str(row.get("Posture", "")).strip().lower()
        recs.append({
            "theme": str(row.get("Theme", "")), "tag": str(row.get("Standing", "")).strip(),
            "posture": post, "score": round(got / cap * 100), "nums": nums,
            "prank": post_rank.get(post, 3),
        })
    # sort so the recommended entries (enter) rise to the top, then by fit
    recs.sort(key=lambda r: (r["prank"], -r["score"]))
    order = [r["theme"] for r in recs]              # best-first (top of chart)
    h = max(300, 34 * len(recs) + 90)

    # posture group blocks, built by walking the actual sorted sequence bottom-to-top,
    # so the grouping is visible AND any unexpected posture value still forms its own
    # labeled band rather than vanishing from the chart
    _plabel = {"enter": "Enter", "watch": "Watch", "deepen": "Deepen, existing"}
    by_theme = {r["theme"]: r for r in recs}
    seq = [by_theme[t]["posture"] for t in reversed(order)]   # bottom-to-top
    blocks, i = [], 0
    while i < len(seq):
        p, j = seq[i], i
        while j < len(seq) and seq[j] == p:
            j += 1
        blocks.append((p, _plabel.get(p, (p or "other").title()), i, j))
        i = j
    divider_ys = [e - 0.5 for _, _, _, e in blocks[:-1]]
    # distinct postures top-to-bottom, so every theme's posture gets a bar
    postures_top = list(dict.fromkeys(r["posture"] for r in recs))

    def _add_dividers(fig):
        for y in divider_ys:
            fig.add_shape(type="line", xref="paper", x0=0, x1=1, yref="y", y0=y, y1=y,
                          line=dict(color="#9AA39A", width=1.2, dash="dot"), layer="above")

    st.markdown('<div class="hs-eyebrow" style="margin-top:10px">Where to enter, '
                'themes ranked by weighted fit and colored by posture</div>', unsafe_allow_html=True)

    # --- View 1: ranked posture bar (the "so what") ---
    figbar = go.Figure()
    for p in postures_top:
        grp = [r for r in recs if r["posture"] == p]
        if not grp:
            continue
        color, label = _POSTURE.get(p, ("#8A938C", (p or "Other").title()))
        figbar.add_trace(go.Bar(
            y=[r["theme"] for r in grp], x=[r["score"] for r in grp], orientation="h",
            name=f"{label}", marker_color=color,
            text=[r["tag"].title() for r in grp], textposition="outside", cliponaxis=False,
            customdata=[[r["posture"].title(), r["tag"].title()] for r in grp],
            hovertemplate="<b>%{y}</b><br>Weighted fit: %{x}<br>Posture: %{customdata[0]}"
                          "<br>Standing: %{customdata[1]}<extra></extra>"))
    figbar.update_layout(
        barmode="stack", height=h, margin=dict(t=8, b=36, l=8, r=90),
        xaxis=dict(title="Weighted fit score", range=[0, 112], showgrid=True, gridcolor="#E4E6E1"),
        yaxis=dict(categoryorder="array", categoryarray=list(reversed(order)),
                   autorange=True, automargin=True),
        legend=dict(orientation="h", y=1.06, x=0, title=""),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        font=dict(size=13), bargap=0.35)
    _add_dividers(figbar)
    st.plotly_chart(figbar, use_container_width=True)
    st.caption("Grouped by posture, enter first, then watch, then deepen, and ranked by weighted "
               "fit within each group, so bar length compares within a group, not across the dividers.")

    # --- View 2: decision-matrix heatmap (the "why") ---
    st.markdown('<div class="hs-eyebrow" style="margin-top:14px">The scorecard behind it, '
                'each theme across your criteria</div>', unsafe_allow_html=True)
    ys = list(reversed(order))                       # bottom-to-top so best sits on top
    z, txt, tcol = [], [], []
    by_theme = {r["theme"]: r for r in recs}
    for th in ys:
        r = by_theme[th]
        z.append([r["nums"][n] for n in names])
        txt.append([("" if r["nums"][n] is None else
                     {1: "Weak", 2: "Partial", 3: "Strong"}[r["nums"][n]]) for n in names])
    heat = go.Figure(go.Heatmap(
        z=z, x=names, y=ys, zmin=1, zmax=3, xgap=3, ygap=3, showscale=False,
        colorscale=[[0.0, _MARKV["weak"][1]], [0.5, _MARKV["partial"][1]], [1.0, _MARKV["strong"][1]]],
        hovertemplate="<b>%{y}</b><br>%{x}: %{text}<extra></extra>", text=txt))
    for i, th in enumerate(ys):
        for j, n in enumerate(names):
            v = by_theme[th]["nums"][n]
            if v is None:
                continue
            heat.add_annotation(x=n, y=th, text={1: "Weak", 2: "Partial", 3: "Strong"}[v],
                                showarrow=False, font=dict(size=12, color=_MARKV[
                                    {1: "weak", 2: "partial", 3: "strong"}[v]][2]))
    # carry the posture dimension into the heatmap: dividers plus a labeled band on the right
    _add_dividers(heat)
    for pk, plabel, s, e in blocks:
        heat.add_annotation(xref="paper", x=1.012, yref="y", y=(s + e - 1) / 2,
                            text=plabel, showarrow=False, textangle=90,
                            xanchor="left", yanchor="middle",
                            font=dict(size=12, color=_POSTURE.get(pk, ("#8A938C",))[0]))
    heat.update_layout(
        height=h + 20, margin=dict(t=12, b=10, l=8, r=104),
        xaxis=dict(side="top", tickfont=dict(size=13, color="#12605A"), automargin=True,
                   showgrid=False, ticks=""),
        yaxis=dict(autorange=True, automargin=True, showgrid=False, ticks=""),
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)", font=dict(size=13))
    st.plotly_chart(heat, use_container_width=True)
    st.caption("Read the bar for the call and the ranking, the matrix for the evidence "
               "behind each theme. Weighted criteria count double in the fit score.")


# --- per-user run folder, so two people never clobber each other ---
def apply_run_dir(run_id: str) -> None:
    base = config.ROOT / "runs" / run_id
    config.WORK_DIR = base / "work"
    config.ORGS_WORK = config.WORK_DIR / "orgs"
    config.REVIEW_DIR = base / "review"
    config.OUT_DIR = base / "out"
    config.INPUT_DIR = base / "input"
    config.MANIFEST = config.WORK_DIR / "manifest.json"
    config.ORG_SHEET = config.INPUT_DIR / "organizations.xlsx"
    for d in (config.ORGS_WORK, config.REVIEW_DIR, config.OUT_DIR, config.INPUT_DIR):
        d.mkdir(parents=True, exist_ok=True)


def gate() -> bool:
    if not APP_PASSWORD:
        return True
    if st.session_state.get("auth"):
        return True
    masthead()
    st.write("")
    pw = st.text_input("Team password", type="password")
    if st.button("Enter", type="primary") and pw:
        if pw == APP_PASSWORD:
            st.session_state["auth"] = True
            st.rerun()
        st.error("Wrong password.")
    return False


def run_async(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


# --- session setup ---
if "run_id" not in st.session_state:
    st.session_state.run_id = uuid.uuid4().hex[:10]
    st.session_state.step = 1
apply_run_dir(st.session_state.run_id)

if not gate():
    st.stop()

# --- sidebar: the stepper rail, then settings ---
with st.sidebar:
    st.markdown('<div class="hs-eyebrow" style="margin-top:2px">Progress</div>', unsafe_allow_html=True)
    stepper(st.session_state.step)
    st.markdown('<div style="height:1px;background:var(--line);margin:14px 0"></div>', unsafe_allow_html=True)
    config.PROVIDER = "openrouter"
    config.OR_MODEL = DEFAULT_MODEL
    scope = st.radio(
        "Scope", ["Africa focus", "Global"], horizontal=True,
        help="Africa focus: search each organization's Africa work and judge it for Africa, the "
             "tighter, more reliable scan. Global: look for approaches anywhere in the world and from "
             "any actor (civil society, foundations, and the private sector too), noting for each how "
             "it could transfer to an African context.")
    config.SCAN_MODE = "global" if scope == "Global" else "africa"
    if not config.OPENROUTER_API_KEY:
        st.error("No API key on the server. Add OPENROUTER_API_KEY to .streamlit/secrets.toml.")
    if st.button("Start over"):
        for k in ("run_id", "step", "generated"):
            st.session_state.pop(k, None)
        st.rerun()

masthead()
step = st.session_state.step

# --- the editable framework (Scan Spec), read by every agent ---
if "spec" not in st.session_state:
    st.session_state.spec = json.loads(json.dumps(spec.DEFAULT_SPEC))
config.SPEC = st.session_state.spec

with st.expander("Frame the scan  ·  research question, criteria, context", expanded=(step == 1)):
    sp = st.session_state.spec
    sp["research_question"] = st.text_area("Research question", value=sp.get("research_question", ""), height=80)
    st.caption("The criteria the Scorer and Themer use. Rename, redefine, add, or remove rows.")
    cdf = pd.DataFrame(sp.get("criteria", []))
    for col in ("name", "definition", "weight"):
        if col not in cdf.columns:
            cdf[col] = "" if col != "weight" else 1
    edited_crit = st.data_editor(cdf[["name", "definition", "weight"]], num_rows="dynamic",
                                 use_container_width=True, key="crit_editor")
    sp["context"] = st.text_area("Context and existing programs to screen out",
                                 value=sp.get("context", ""), height=140)
    if st.button("Save frame"):
        crit = []
        for _, row in edited_crit.iterrows():
            name = str(row.get("name", "") or "").strip()
            if not name:
                continue
            key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or f"c{len(crit)}"
            try:
                w = int(row.get("weight", 1))
            except Exception:
                w = 1
            crit.append({"key": key, "name": name, "definition": str(row.get("definition", "") or ""), "weight": w})
        if crit:
            sp["criteria"] = crit
        st.session_state.spec = sp
        config.SPEC = sp
        st.success(f"Frame saved. {len(sp['criteria'])} criteria in play.")

# --- Step 1: build the organization roster (discover, add your own, or both) ---
eyebrow("Step one", "Your organizations")
st.caption("Discover organizations from your research question, add your own, or both. "
           "They merge into one roster, de-duplicated, with each row marked by where it came from.")
roster = st.session_state.setdefault("roster", [])

col_a, col_b = st.columns(2)
with col_a:
    st.markdown("**Discover from the research question**")
    if st.button("Discover organizations"):
        with st.spinner("Proposing organizations from your research question..."):
            disc = run_async(agents.discover(config.load_context(), 20))
        disc = [{**o, "source": "discovered"} for o in disc]   # tag before merge
        st.session_state.roster = io_xlsx.merge_orgs(st.session_state.roster, disc)
        st.rerun()
with col_b:
    st.markdown("**Add your own**")
    pasted = st.text_area("One organization per line", key="paste_orgs", height=110,
                          placeholder="Tony Blair Institute for Global Change\nODI\nAfDB")
    frame_it = st.checkbox("Frame my additions (fill type, region, and fit)", value=True,
                           help="A quick pass that describes each added organization so it "
                                "sits alongside the discovered ones in the same shape.")
    up = st.file_uploader("or upload an Excel list (name, type, region)", type=["xlsx"], key="up_orgs")
    if st.button("Add to roster"):
        additions: list[dict] = [{"name": ln.strip()} for ln in pasted.splitlines() if ln.strip()]
        if frame_it and additions:
            with st.spinner("Framing your additions..."):
                framed = run_async(agents.frame_orgs(config.load_context(), [a["name"] for a in additions]))
            if framed:
                additions = framed
        if up is not None:
            tmp = config.WORK_DIR / "_uploaded_orgs.xlsx"
            tmp.write_bytes(up.getbuffer())
            try:
                additions += io_xlsx.read_orgs(tmp)
            except Exception as e:
                st.error(f"Could not read that file: {e}")
        st.session_state.roster = io_xlsx.merge_orgs(st.session_state.roster, additions)
        st.rerun()

if roster:
    counts = {}
    for o in roster:
        counts[o.get("source", "")] = counts.get(o.get("source", ""), 0) + 1
    tally = ", ".join(f"{n} {s}" for s, n in counts.items() if s)
    st.caption(f"{len(roster)} organizations in the roster ({tally}). Edit, remove, or add rows, then use it.")
    rdf = pd.DataFrame(roster)
    for col in ("name", "type", "region", "why", "source"):
        if col not in rdf.columns:
            rdf[col] = ""
    edited_orgs = st.data_editor(rdf[["name", "type", "region", "why", "source"]], num_rows="dynamic",
                                 use_container_width=True, hide_index=True, key="roster_editor")
    cc1, cc2 = st.columns([1, 3])
    if cc1.button("Use this roster", type="primary"):
        rows = [{"name": str(r.get("name", "") or "").strip(), "type": str(r.get("type", "") or ""),
                 "region": str(r.get("region", "") or ""), "why": str(r.get("why", "") or ""),
                 "source": str(r.get("source", "") or "").strip() or "analyst"}
                for _, r in edited_orgs.iterrows() if str(r.get("name", "") or "").strip()]
        rows = io_xlsx.merge_orgs(rows, [])          # final de-dup and contiguous ids
        if not rows:
            cc2.error("Add at least one organization to begin.")
        else:
            io_xlsx.write_orgs_list(rows)
            st.session_state.roster = rows
            st.session_state.n_orgs = len(rows)
            st.session_state.step = 2
            st.rerun()

if step >= 2:
    st.success(f"{st.session_state.n_orgs} organizations loaded.")

# --- Step 2: run ---
if step >= 2:
    eyebrow("Step two", "Run the scan")
    st.markdown(f'One agent per organization, bounded to that organization alone, '
                f'**{st.session_state.n_orgs}** in all.')
    if step == 2 and st.button("Run the scan", type="primary"):
        board = st.empty()
        bstate: dict[str, dict] = {
            o["id"]: {"id": o["id"], "org": o["name"], "scout": "pending", "read": "pending",
                      "score": "pending", "verify": "pending", "audit": "pending", "kept": 0,
                      "verified": 0, "flagged": 0, "dropped": 0, "note": "queued"}
            for o in io_xlsx.read_orgs()}

        def render() -> None:
            states = list(bstate.values())
            done = sum(1 for s in states if s.get("note") == "complete")
            appr = sum(s.get("kept", 0) for s in states)
            ver = sum(s.get("verified", 0) for s in states)
            flg = sum(s.get("flagged", 0) for s in states)
            aside = sum(s.get("dropped", 0) for s in states)
            head = (f'<div class="hs-eyebrow" style="margin-top:6px">Scanning the field</div>'
                    f'<div class="hs-tag">{done} of {len(states)} organizations complete, '
                    f'each moving through scout, read, score, verify, audit</div>')
            board.markdown(head + tiles_html([("Organizations", len(states)), ("Approaches", appr),
                           ("Verified", ver), ("Flagged", flg), ("Set aside", aside)]) + lanes_html(states),
                           unsafe_allow_html=True)

        def progress(s: dict) -> None:
            bstate[s["id"]] = s
            render()

        render()
        run_async(pipeline.run_stage1(progress=progress))
        df = pd.read_excel(config.REVIEW_DIR / "longlist.xlsx")
        st.session_state.n_rows = len(df)
        st.session_state.n_verified = int((df["verification"] == "verified").sum())
        st.session_state.step = 3
        st.rerun()

# --- scan summary: tiles + explorable evidence per organization ---
if step >= 3:
    if st.session_state.get("n_rows", 0) == 0:
        errmsg = ""
        if config.MANIFEST.exists():
            m = json.loads(config.MANIFEST.read_text())
            errs = [v.get("error") for v in m.values() if v.get("error")]
            errmsg = errs[0] if errs else ""
        if errmsg:
            st.error("This run failed, every organization errored. First error: " + errmsg[:220]
                     + "  —  If it mentions credits, even a free model's web search needs a small "
                       "OpenRouter balance; the free tier covers the model, not the web access.")
        else:
            st.warning("This run returned no usable approaches, the model dropped every candidate at the "
                       "reading stage. That is usually the model over-dropping, not an error. Pick a steadier "
                       "model in the sidebar (for example openai/gpt-4o-mini), then Start over and run again.")
    payloads = read_payloads()
    dropped = sum(len(p.get("dropped", [])) for p in payloads)
    flagged_total = sum(1 for p in payloads for r in p.get("rows", []) if r.get("flagged"))
    st.markdown(tiles_html([("Organizations", st.session_state.n_orgs),
                ("Approaches", st.session_state.n_rows),
                ("Verified", st.session_state.n_verified),
                ("Flagged", flagged_total), ("Set aside", dropped)]),
                unsafe_allow_html=True)
    # coverage honesty: how much of the scan rests on reports read vs unreachable sources
    orgs_with_reports = sum(1 for p in payloads if p.get("reports_found"))
    unreachable = sum(1 for p in payloads for r in p.get("rows", []) if r.get("source_reachable") is False)
    cov = f"Coverage · {orgs_with_reports} of {len(payloads)} organizations had reports found and read"
    if unreachable:
        cov += f" · {unreachable} approach{'es' if unreachable != 1 else ''} rest on a source that could not be read directly"
    st.caption(cov + ".")
    if client.USAGE["calls"]:
        st.caption("Spend · " + client.usage_line() + ".")
    srcdir = config.ROOT / "runs" / st.session_state.run_id / "sources"
    manifest = st.session_state.get("sources")
    if manifest is None:
        manifest = sources.load_manifest(srcdir)
        st.session_state.sources = manifest

    st.markdown('<div class="hs-eyebrow" style="margin-top:8px">The trail · '
                'click any organization to see what it read, where, how, and every check</div>',
                unsafe_allow_html=True)
    ca, cb = st.columns([1.5, 3])
    if ca.button("Capture source files"):
        urls = [r.get("url") for p in payloads for r in p.get("rows", []) if r.get("url")]
        with st.spinner(f"Fetching {len(set(urls))} source documents into the run..."):
            manifest = sources.capture(urls, srcdir)
        st.session_state.sources = manifest
        saved = sum(1 for v in manifest.values() if v.get("status") == "saved")
        skipped = sum(1 for v in manifest.values() if v.get("status") == "skipped")
        cb.success(f"Saved {saved} of {len(manifest)} source files into the run bundle"
                   + (f" ({skipped} placeholders skipped)" if skipped else "") + ".")
    st.caption("Downloads the actual page or PDF each approach rests on, so the run bundle "
               "holds the primary documents, not just links.")

    for pidx, p in enumerate(payloads):
        rows = p.get("rows", [])
        verified = sum(1 for r in rows if (r.get("verification", {}) or {}).get("status") == "verified")
        flagged = sum(1 for r in rows if r.get("flagged"))
        head = f"{p.get('org','')}      ·   {len(rows)} kept, {verified} verified"
        if p.get("reports_found"):
            head += f", {p['reports_found']} reports found"
        if flagged:
            head += f", {flagged} flagged"
        with st.expander(head):
            st.markdown('<span class="hs-st done">scout</span> <span class="hs-st done">read</span> '
                        '<span class="hs-st done">score</span> <span class="hs-st done">verify</span> '
                        '<span class="hs-st done">audit</span>', unsafe_allow_html=True)
            for ri, r in enumerate(rows):
                st.markdown(card_html(r), unsafe_allow_html=True)
                b1, b2, b3, _ = st.columns([1.1, 1.1, 1.1, 1.7])
                key = f"{pidx}-{ri}"
                if r.get("url"):
                    b1.link_button("Open ↗", r["url"], use_container_width=True)
                else:
                    b1.button("No source", disabled=True, key="ns" + key, use_container_width=True)
                m = (manifest or {}).get(r.get("url", ""))
                if m and m.get("status") == "saved" and (srcdir / m["file"]).exists():
                    b2.download_button("Source file", (srcdir / m["file"]).read_bytes(),
                                       file_name=m["file"], key="fz" + key, use_container_width=True)
                else:
                    b2.button("Not captured", disabled=True, key="nc" + key, use_container_width=True)
                b3.download_button("Dossier", pdf_out.md_to_pdf(dossier_md(r), r.get("name", "Dossier")),
                                   file_name=f"{_slug(r.get('name',''))}.pdf", mime="application/pdf",
                                   key="dz" + key, use_container_width=True)
            drp = p.get("dropped", [])
            if drp:
                st.caption("Set aside at reading: " + ", ".join(d.get("name", "") for d in drp))

# --- Step 3: review gate ---
if step >= 3:
    eyebrow("Step three", "Where you decide")
    st.markdown("Tick to keep, fix any cell, then add your own hunches, the reading a tool misses.")
    df = pd.read_excel(config.REVIEW_DIR / "longlist.xlsx")
    df["keep"] = df["keep"].astype(str).str.upper().eq("Y")
    edited = st.data_editor(df, use_container_width=True, hide_index=True,
                            column_config={"keep": st.column_config.CheckboxColumn("keep"),
                                           "rid": st.column_config.TextColumn("rid", disabled=True)})
    hunch_path = config.REVIEW_DIR / "hunches.md"
    hunches = st.text_area("Your hunches",
                           value=hunch_path.read_text(encoding="utf-8") if hunch_path.exists() else "",
                           height=150)
    if st.button("Save and continue", type="primary"):
        out = edited.copy()
        out["keep"] = out["keep"].map(lambda b: "Y" if b else "N")
        out.to_excel(config.REVIEW_DIR / "longlist.xlsx", index=False)
        hunch_path.write_text(hunches, encoding="utf-8")
        st.session_state.step = 4
        st.rerun()

# --- Step 4: generate ---
if step >= 4:
    eyebrow("Step four", "Generate the report")
    sc = config.OUT_DIR / "theme_scorecard.xlsx"
    out_ready = sc.exists()
    if st.button("Regenerate the report" if out_ready else "Generate", type="primary"):
        with st.status("Clustering into themes and writing the memo..."):
            run_async(pipeline.run_stage2())
        st.session_state.generated = True
        st.rerun()
    if out_ready or st.session_state.get("generated"):
        if sc.exists():
            st.dataframe(pd.read_excel(sc, skiprows=2), use_container_width=True, hide_index=True)
            try:
                render_theme_charts(sc)
            except Exception as e:
                st.caption(f"(charts unavailable: {str(e)[:80]})")
        docs = [("Theme scorecard", "the decision layer", config.OUT_DIR / "theme_scorecard.xlsx"),
                ("Innovation map", "the evidence, by theme", config.OUT_DIR / "innovation_map.xlsx"),
                ("Synthesis memo", "Word document, house style", config.OUT_DIR / "synthesis_memo.docx")]
        for label, sub, path in docs:
            if path.exists():
                dc1, dc2 = st.columns([3, 1])
                dc1.markdown(f'<div class="hs-doc"><div><div class="t">{label}</div>'
                             f'<div class="s">{sub}</div></div></div>', unsafe_allow_html=True)
                dc2.download_button(f"Download", path.read_bytes(), file_name=path.name, key=label)
