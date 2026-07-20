"""Orchestration. Stage 1 fans out one leg per org (sectioned parallelization)
and emits stage events so a UI can show each org move through scout, read,
score, and verify live. Resumable per org via work/orgs/<id>.jsonl. Stage 2
reads the human-edited review files and runs theming + synthesis.

A `progress` callable, if given, receives a dict per org whenever its state
changes: {id, org, scout, read, score, verify, kept, verified, dropped, note},
each stage being "pending" | "run" | "done".
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

Progress = Callable[[dict], None] | None

from . import agents, config, docx_out, guardrail, io_xlsx, sources


def _today() -> str:
    """The real access date, US style, stamped by the system (never the model)."""
    n = datetime.now()
    return n.strftime("%B ") + str(n.day) + ", " + str(n.year)


def _load_manifest() -> dict[str, Any]:
    if config.MANIFEST.exists():
        return json.loads(config.MANIFEST.read_text())
    return {}


def _save_manifest(m: dict[str, Any]) -> None:
    config.MANIFEST.write_text(json.dumps(m, indent=2))


def _org_path(org_id: str) -> Path:
    return config.ORGS_WORK / f"{org_id}.jsonl"


def _write_org(org_id: str, payload: dict[str, Any]) -> None:
    with _org_path(org_id).open("w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _read_org(org_id: str) -> dict[str, Any] | None:
    p = _org_path(org_id)
    if not p.exists():
        return None
    return json.loads(p.read_text())


async def _process_org(ctx, org, sem, progress: Progress = None) -> dict[str, Any]:
    async with sem:
        state = {"id": org["id"], "org": org["name"], "scout": "pending", "read": "pending",
                 "score": "pending", "verify": "pending", "audit": "pending", "kept": 0,
                 "verified": 0, "flagged": 0, "dropped": 0, "note": "queued"}

        def emit(**kw) -> None:
            state.update(kw)
            if progress:
                progress(dict(state))

        async def tick() -> None:
            if config.DRY_RUN:  # pace the demo so the flow is visible in test mode
                await asyncio.sleep(0.25)

        rows: list[dict[str, Any]] = []
        dropped: list[dict[str, Any]] = []

        # 1. scout, records its search queries (the how)
        emit(scout="run", note="searching")
        await tick()
        try:
            scout_out = await agents.scout(ctx, org)
        except Exception as e:
            emit(scout="done", note="nothing usable")
            return {"org": org["name"], "id": org["id"], "error": str(e), "rows": [], "dropped": []}
        candidates = scout_out["candidates"]
        queries = scout_out.get("queries", [])
        emit(scout="done", read="run", note=f"{len(candidates)} found")
        await tick()

        # 2. read: extract the approach and record WHERE it sits + verbatim quotes
        readings: list[dict[str, Any]] = []
        for i, cand in enumerate(candidates, 1):
            emit(read="run", note=f"reading {i} of {len(candidates)}")
            try:
                r = await agents.read(ctx, cand)
            except Exception as e:
                dropped.append({"org": org["name"], "name": cand.get("name", "?"), "error": str(e)})
                await tick()
                continue
            if not r.get("keep"):
                dropped.append({"org": org["name"], "name": cand["name"]})
            elif r.get("band") == "maturing":
                # screen out the third band: standard practice, not over-the-horizon
                dropped.append({"org": org["name"], "name": cand["name"], "reason": "maturing (standard practice)"})
            else:
                readings.append({**cand, **r})
            await tick()

        # 3. score, with the evidence basis and a self-check
        emit(read="done", score="run", note=f"scoring {len(readings)}")
        scored: list[dict[str, Any]] = []
        for appr in readings:
            try:
                sc = await agents.score(ctx, appr)
                scored.append({**appr, "score": sc, "overall": sc.get("overall", "")})
            except Exception as e:
                dropped.append({"org": org["name"], "name": appr.get("name", "?"), "error": str(e)})
            await tick()

        # 4. verify each claim against its primary, adversarially
        emit(score="done", verify="run", note=f"verifying {len(scored)}")
        for appr in scored:
            try:
                vd = await agents.verify(ctx, appr)
            except Exception:
                vd = {"status": "partial", "confirming_quote": "", "note": "verify failed",
                      "primary_url": "", "claim_supported": False, "figure_check": "", "discrepancies": []}
            rows.append({
                "org": org["name"], "name": appr["name"], "year": appr.get("year", ""),
                "url": appr.get("url", ""), "source_type": appr.get("source_type", ""),
                "band": appr.get("band", ""), "accessed": _today(),
                "what": appr.get("what", ""), "evidence": appr.get("evidence", ""),
                "uptake": appr.get("uptake", ""), "quotes": appr.get("quotes", []),
                "locator": appr.get("locator", ""), "verbatim": appr.get("verbatim", False),
                "access_note": appr.get("access_note", ""),
                "score": appr.get("score", {}), "overall": appr.get("overall", ""),
                "verification": vd, "queries": queries,
            })
            await tick()

        # 5. audit the whole chain for consistency, then settle each row
        emit(verify="done", audit="run", note=f"auditing {len(rows)}")
        verified = flagged = 0
        for row in rows:
            try:
                au = await agents.audit(ctx, row)
            except Exception:
                au = {"quote_supports_claim": False, "score_matches_evidence": "consistent",
                      "source_is_primary": False, "verdict": "flag", "notes": "audit failed"}
            row["audit"] = au
            # fix 1: check the confirming quote is actually in the source (policy-trust)
            q = (row.get("verification") or {}).get("confirming_quote", "")
            if config.GROUND_QUOTES and not config.DRY_RUN and q:
                row["quote_grounded"] = await asyncio.to_thread(
                    sources.quote_grounded, row.get("url", ""), q)
            else:
                row["quote_grounded"] = None
            # fix 3: surface every field a model returned off-spec that we had to repair
            row["coerced_fields"] = (
                ["score." + f for f in (row.get("score") or {}).get("_coerced", [])]
                + ["verify." + f for f in (row.get("verification") or {}).get("_coerced", [])]
                + ["audit." + f for f in au.get("_coerced", [])])
            row["trail"] = _trail(row)
            guardrail.settle_row(row)
            if row["verification"]["status"] == "verified":
                verified += 1
            if row.get("flagged"):
                flagged += 1
            await tick()

        emit(audit="done", kept=len(rows), verified=verified, flagged=flagged,
             dropped=len(dropped), note="complete")
        payload = {"org": org["name"], "id": org["id"], "rows": rows, "dropped": dropped}
        _write_org(org["id"], payload)
        return payload


_MARK_PTS = {"strong": 3, "partial": 2, "weak": 1}


def _apply_top2(themes: list[dict[str, Any]]) -> None:
    """Deterministic top-2: the two cleanest NEW or ADJACENT themes by weighted
    marks (mandate fit and research-to-policy count double), respecting any the
    model already flagged. Existing themes are never a 'new area to enter'."""
    def strength(t: dict) -> int:
        m = lambda f: _MARK_PTS.get(t.get(f, ""), 0)
        return (2 * m("mandate_fit") + 2 * m("research_to_policy")
                + m("african_traction") + m("white_space")
                + (1 if t.get("posture") == "impact" else 0))

    eligible = sorted(
        [t for t in themes if t.get("tag") in ("new", "adjacent")],
        key=lambda t: (1 if t.get("top2") else 0, strength(t)), reverse=True)
    chosen = {id(t) for t in eligible[:2]}
    for t in themes:
        t["top2"] = id(t) in chosen


def _trail(row: dict[str, Any]) -> dict[str, Any]:
    """A compact per-stage provenance record: where, what, how, and the checks."""
    s = row.get("score", {}) or {}
    v = row.get("verification", {}) or {}
    a = row.get("audit", {}) or {}
    return {
        "scout": {"queries": row.get("queries", []), "source_type": row.get("source_type", "")},
        "reader": {"source_url": row.get("url", ""), "band": row.get("band", ""),
                   "locator": row.get("locator", ""), "verbatim": row.get("verbatim", False),
                   "accessed": row.get("accessed", ""), "access_note": row.get("access_note", ""),
                   "quotes": row.get("quotes", [])},
        "scorer": {"evidence_basis": s.get("evidence_basis", ""),
                   "self_check": s.get("self_check", ""), "self_check_note": s.get("self_check_note", "")},
        "verifier": {"primary_url": v.get("primary_url", ""), "claim_supported": v.get("claim_supported", False),
                     "figure_check": v.get("figure_check", ""), "discrepancies": v.get("discrepancies", []),
                     "quote_grounded": row.get("quote_grounded")},
        "auditor": {"verdict": a.get("verdict", ""), "quote_supports_claim": a.get("quote_supports_claim", False),
                    "score_matches_evidence": a.get("score_matches_evidence", ""),
                    "source_is_primary": a.get("source_is_primary", False), "notes": a.get("notes", "")},
    }


async def run_stage1(only: Path | None = None, progress: Progress = None) -> None:
    ctx = config.load_context()
    orgs = io_xlsx.read_orgs(only) if only else io_xlsx.read_orgs()
    manifest = _load_manifest()
    sem = asyncio.Semaphore(config.MAX_CONCURRENCY)

    todo = [o for o in orgs if _read_org(o["id"]) is None]
    done = len(orgs) - len(todo)
    print(f"stage 1: {len(orgs)} orgs, {done} cached, {len(todo)} to scan, "
          f"concurrency {config.MAX_CONCURRENCY}")

    results = await asyncio.gather(*[_process_org(ctx, o, sem, progress) for o in todo])
    for r in results:
        manifest[r["id"]] = {"org": r["org"], "rows": len(r.get("rows", [])),
                             "error": r.get("error", "")}
    _save_manifest(manifest)

    all_rows: list[dict[str, Any]] = []
    all_dropped: list[dict[str, Any]] = []
    for o in orgs:
        payload = _read_org(o["id"])
        if not payload:
            continue
        all_rows.extend(payload.get("rows", []))
        all_dropped.extend(payload.get("dropped", []))

    io_xlsx.write_longlist(all_rows)
    io_xlsx.write_open_questions(all_rows, all_dropped)
    try:
        patterns = await agents.seed_hunches(ctx, [r["name"] for r in all_rows][:80])
    except Exception:
        patterns = []
    io_xlsx.write_hunches(patterns)

    verified = sum(1 for r in all_rows if (r.get("verification", {}) or {}).get("status") == "verified")
    print(f"stage 1 done: {len(all_rows)} rows ({verified} verified), "
          f"{len(all_dropped)} dropped. Review review/ then run --stage 2.")
    errs = [r.get("error", "") for r in results if r.get("error")]
    if not all_rows:
        if errs:
            print(f"  {len(errs)} of {len(results)} organizations errored. First error: {errs[0][:160]}")
            print("  (a free model's web search still needs OpenRouter credits, a 402 means the balance is empty.)")
        elif all_dropped:
            print("  note: every candidate was dropped at the reading stage. This is usually the model "
                  "over-dropping, not an error. Try a steadier model (e.g. openai/gpt-4o-mini) and re-run.")


async def run_stage2() -> None:
    ctx = config.load_context()
    kept = io_xlsx.read_kept_longlist()
    if not kept:
        raise SystemExit("No kept rows in review/longlist.xlsx (keep column = Y).")
    hunches_path = config.REVIEW_DIR / "hunches.md"
    hunches = hunches_path.read_text(encoding="utf-8") if hunches_path.exists() else ""

    print(f"stage 2: theming {len(kept)} kept approaches")
    themes = await agents.themes(ctx, kept, hunches)

    # carry the scan's verification through into the report: per-theme evidence
    status_by_name = {r["name"]: (r.get("verification", {}) or {}).get("status", "") for r in kept}
    for t in themes:
        vs = [status_by_name.get(m, "") for m in t.get("members", [])]
        ver = sum(1 for s in vs if s == "verified")
        par = sum(1 for s in vs if s == "partial")
        unk = max(0, len(t.get("members", [])) - ver - par)
        t["verified_count"], t["partial_count"] = ver, par
        parts = ([f"{ver} verified"] if ver else []) + ([f"{par} partial"] if par else []) \
            + ([f"{unk} unconfirmed"] if unk else [])
        t["evidence"] = ", ".join(parts) if parts else "no members matched"

    _apply_top2(themes)  # guarantee the two cleanest new areas are named
    synth = await agents.synthesize(ctx, themes)

    memo = guardrail.scrub(synth.get("memo_markdown", ""))
    intro = guardrail.scrub(synth.get("scorecard_intro", ""))
    io_xlsx.write_map(kept, themes)
    io_xlsx.write_scorecard(themes, intro)
    io_xlsx.write_memo(memo)
    docx_out.write_memo_docx(memo, config.OUT_DIR / "synthesis_memo.docx")
    top2 = [t["name"] for t in themes if t.get("top2")]
    print(f"stage 2 done: {len(themes)} themes -> out/. Cleanest new areas: {', '.join(top2)}")


def status() -> None:
    manifest = _load_manifest()
    if not manifest:
        print("no runs yet")
        return
    rows = sum(m.get("rows", 0) for m in manifest.values())
    errs = [k for k, m in manifest.items() if m.get("error")]
    print(f"orgs scanned: {len(manifest)}, rows: {rows}, errors: {len(errs)}")
    for k in errs:
        print(f"  ! {k}: {manifest[k]['error']}")
