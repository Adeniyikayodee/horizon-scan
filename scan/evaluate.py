"""Evaluation harness, on Google's two axes.

  1. Trajectory eval  (objective, no judge): run Scout on each golden org and
     measure whether it hit the org's own domain (grounding), how much of the
     expected substance it recovered (recall), and how much of what it returned
     cites the org's own site (primary-source precision).
  2. Final-response eval (LLM judge): score a produced memo on a rubric.

Run:  python -m scan eval [--provider openrouter --model ...] [--judge out/synthesis_memo.md]
"""
from __future__ import annotations

import json

from . import agents, config
from .client import structured_call

GOLDEN = config.ROOT / "eval" / "golden.json"

RUBRIC_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["themes_supported", "sources_primary", "house_style", "actionability", "overall", "notes"],
    "properties": {
        "themes_supported": {"type": "string", "enum": ["strong", "partial", "weak"]},
        "sources_primary": {"type": "string", "enum": ["strong", "partial", "weak"]},
        "house_style": {"type": "string", "enum": ["strong", "partial", "weak"]},
        "actionability": {"type": "string", "enum": ["strong", "partial", "weak"]},
        "overall": {"type": "string", "enum": ["strong", "adequate", "weak"]},
        "notes": {"type": "string"},
    },
}


def _load_golden() -> list[dict]:
    return json.loads(GOLDEN.read_text(encoding="utf-8"))["orgs"]


async def trajectory() -> list[dict]:
    ctx = config.load_context()
    out = []
    for g in _load_golden():
        try:
            scout = await agents.scout(ctx, {"name": g["name"], "type": g.get("type", ""),
                                             "region": g.get("region", "")})
        except Exception as e:
            out.append({"org": g["name"], "error": str(e)[:80]})
            continue
        cands = scout["candidates"]
        blob = " ".join((c.get("name", "") + " " + c.get("one_liner", "")) for c in cands).lower()
        urls = " ".join(c.get("url", "") for c in cands)
        kw = g.get("expect_keywords", [])
        matched = [k for k in kw if k.lower() in blob]
        dom = g.get("expect_domains", [])
        on_domain = sum(1 for c in cands if any(d in c.get("url", "") for d in dom))
        out.append({
            "org": g["name"], "found": len(cands),
            "recall": round(len(matched) / len(kw), 2) if kw else 0.0,
            "domain_hit": any(d in urls for d in dom),
            "precision": round(on_domain / len(cands), 2) if cands else 0.0,
            "matched": matched,
        })
    return out


async def judge_memo(memo_text: str) -> dict:
    rubric = ("Score the memo, being critical. themes_supported: are the themes backed by the "
              "approaches. sources_primary: does it rest on primary sources. house_style: US English, "
              "active voice, serial comma, no em dashes, and no trace of any tool or AI. actionability: "
              "are the recommendations clear and specific. Mark each strong, partial, or weak, then an "
              "overall of strong, adequate, or weak, with notes.")
    return await structured_call(
        model=config.MODEL_OPUS,
        frame="You are an evaluation judge for a research institute. " + rubric,
        user="MEMO TO SCORE:\n\n" + memo_text,
        schema=RUBRIC_SCHEMA,
    )


def print_trajectory(rows: list[dict]) -> None:
    print("\nTRAJECTORY EVAL, retrieval quality on the golden set")
    print("-" * 74)
    recs, precs, hits, n = [], [], 0, 0
    for r in rows:
        if r.get("error"):
            print(f"  {r['org'][:36]:36}  ERROR {r['error']}")
            continue
        n += 1
        recs.append(r["recall"]); precs.append(r["precision"]); hits += int(r["domain_hit"])
        print(f"  {r['org'][:36]:36}  found {r['found']:2}   recall {r['recall']:.2f}   "
              f"domain-hit {'Y' if r['domain_hit'] else 'N'}   primary-precision {r['precision']:.2f}")
    if n:
        print("-" * 74)
        print(f"  {'AGGREGATE':36}  recall {sum(recs)/n:.2f}   domain-hit {hits}/{n}   "
              f"primary-precision {sum(precs)/n:.2f}")


def print_rubric(res: dict) -> None:
    print("\nFINAL-RESPONSE EVAL, memo rubric (LLM judge)")
    print("-" * 74)
    for k in ("themes_supported", "sources_primary", "house_style", "actionability"):
        print(f"  {k:20} {res.get(k, '?')}")
    print(f"  {'OVERALL':20} {res.get('overall', '?')}")
    if res.get("notes"):
        print(f"  notes: {res['notes']}")
