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
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

Progress = Callable[[dict], None] | None

from . import agents, client, config, docx_out, guardrail, io_xlsx, sources, spec


def _today() -> str:
    """The real access date, US style, stamped by the system (never the model)."""
    n = datetime.now()
    return n.strftime("%B ") + str(n.day) + ", " + str(n.year)


# --- the recency window, enforced in code, reading the SAME source of truth
#     (config.YEAR_MIN/MAX) that every agent frame quotes, so the two never drift ---
WINDOW = (config.YEAR_MIN, config.YEAR_MAX)
_YEAR_RE = re.compile(r"(?:19|20)\d{2}")


def _year_of(*texts) -> int | None:
    """The publication year across the given date/title strings, or None when
    nothing datable is present. A publication year cannot be in the future, so
    years beyond the current calendar year are ignored: they are target or vision
    years ('Strategy 2024-2029', 'Agenda 2063', 'targets by 2035'), not the date
    the document was published. Among the plausible years we take the most recent,
    so an 'updated 2024' beats an original 2019 date."""
    now_year = datetime.now().year
    yrs = [int(y) for t in texts if t for y in _YEAR_RE.findall(str(t))]
    yrs = [y for y in yrs if 1990 <= y <= now_year]
    return max(yrs) if yrs else None


def _first_year(*fields) -> int | None:
    """The publication year from the FIRST field that carries one, in order of
    authority, rather than the most recent year found anywhere. This stops a stray
    recent year in a low-authority field (a title, a cited study) from letting an
    out-of-window source pass on the strength of a year it only mentions."""
    for f in fields:
        y = _year_of(f)
        if y is not None:
            return y
    return None


def _out_of_window(year: int | None) -> bool:
    """True only when a source carries a CONFIRMED year outside the window. An
    unknown year is not out of window, it is undated, we cannot drop what we
    cannot date, so it is flagged instead."""
    return year is not None and not (WINDOW[0] <= year <= WINDOW[1])


def _best_report(cand: dict, reports: list[dict]) -> dict | None:
    """Pick the report whose title/URL best matches this candidate, so the Reader
    reads the actual report rather than the landing page. None if no good match."""
    text = (cand.get("name", "") + " " + cand.get("one_liner", "")).lower()
    toks = set(re.findall(r"[a-z]{4,}", text))
    if not toks:
        return None

    def overlap(r: dict) -> int:
        blob = (str(r.get("title", "")) + " " + str(r.get("url", ""))).lower()
        return sum(1 for w in toks if w in blob)

    ranked = sorted([r for r in reports if r.get("url")], key=overlap, reverse=True)
    return ranked[0] if ranked and overlap(ranked[0]) >= 2 else None


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
            # ReAct-lite: a thin first pass gets one broader, retargeted retry
            if not scout_out["candidates"]:
                emit(scout="run", note="thin, broadening search")
                scout_out = await agents.scout(ctx, org, hint=(
                    "The first search was thin. Broaden it: try the organization's "
                    "publications, research, news, and annual-report pages, and vary the terms."))
        except Exception as e:
            emit(scout="done", note="nothing usable")
            return {"org": org["name"], "id": org["id"], "error": str(e), "rows": [], "dropped": []}
        candidates = scout_out["candidates"]
        queries = scout_out.get("queries", [])

        # 1b. Librarian: find the org's own recent reports and briefs to read from
        emit(scout="done", read="run", note="finding reports")
        try:
            reports = await agents.librarian(ctx, org)
            if not reports:  # retry with site-scoped, PDF-targeted queries
                reports = await agents.librarian(ctx, org, hint=(
                    "None found on the first pass. Try site-scoped queries for PDFs and the "
                    "/publications, /research, and /reports sections of the organization's site."))
        except Exception:
            reports = []

        # HARD RULE: drop any report dated outside the recency window before it is matched or read
        if reports:
            in_window = [rp for rp in reports
                         if not _out_of_window(_first_year(rp.get("date", ""), rp.get("title", "")))]
            if len(in_window) != len(reports):
                emit(read="run", note=f"{len(reports) - len(in_window)} reports outside "
                     f"{config.YEAR_MIN}-{config.YEAR_MAX} dropped")
            reports = in_window

        # drop dead report links (404/410) so a stale or hallucinated URL is never
        # matched to a candidate or shown as a source
        if reports and not config.DRY_RUN:
            dead = await asyncio.gather(
                *[asyncio.to_thread(sources.link_dead, rp.get("url", "")) for rp in reports])
            live = [rp for rp, d in zip(reports, dead) if not d]
            if len(live) != len(reports):
                emit(read="run", note=f"{len(reports) - len(live)} dead report links dropped")
            reports = live
        emit(read="run", note=f"{len(candidates)} found, {len(reports)} reports")
        await tick()

        # 2. read: read the matched report (not the landing page), extract the approach
        readings: list[dict[str, Any]] = []
        for i, cand in enumerate(candidates, 1):
            rep = _best_report(cand, reports)
            src = dict(cand)
            if rep:
                src["url"] = rep["url"]
                src["report_title"] = rep.get("title", "")
                src["report_date"] = rep.get("date", "")
            emit(read="run", note=f"reading {i} of {len(candidates)}"
                 + (" (report)" if rep else " (page)"))
            try:
                r = await agents.read(ctx, src)
            except Exception as e:
                dropped.append({"org": org["name"], "name": cand.get("name", "?"), "error": str(e)})
                await tick()
                continue
            # HARD RULE: the source the Reader actually read must fall in the recency window
            yr = _first_year(r.get("access_note", ""), src.get("report_date", ""), src.get("year", ""))
            if not r.get("keep"):
                dropped.append({"org": org["name"], "name": cand["name"]})
            elif _out_of_window(yr):
                dropped.append({"org": org["name"], "name": cand["name"],
                                "reason": f"source dated {yr}, outside {config.YEAR_MIN}-{config.YEAR_MAX}"})
            elif r.get("band") == "maturing":
                dropped.append({"org": org["name"], "name": cand["name"], "reason": "maturing (standard practice)"})
            else:
                readings.append({**src, **r, "source_year": yr})
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
                "report_title": appr.get("report_title", ""), "report_date": appr.get("report_date", ""),
                "what": appr.get("what", ""), "evidence": appr.get("evidence", ""),
                "uptake": appr.get("uptake", ""), "quotes": appr.get("quotes", []),
                "locator": appr.get("locator", ""), "verbatim": appr.get("verbatim", False),
                "access_note": appr.get("access_note", ""),
                "source_year": appr.get("source_year"),
                "date_status": "in window" if appr.get("source_year") else "undated",
                "source_reachable": appr.get("source_reachable", True),
                "score": appr.get("score", {}), "overall": appr.get("overall", ""),
                "verification": vd, "queries": queries,
            })
            await tick()

        # 4b. link health: a dead, fabricated, or soft-404 URL must never be shown.
        # Check every row's URL, and the Verifier's primary as a possible fallback.
        # If neither resolves, the link is REMOVED, not just flagged, so no dead or
        # invented URL ever reaches a card, a dossier, or the map.
        if rows and not config.DRY_RUN:
            emit(verify="run", note=f"checking {len(rows)} source links")
            statuses = await asyncio.gather(
                *[asyncio.to_thread(sources.link_status, r.get("url", "")) for r in rows])
            for row, stt in zip(rows, statuses):
                row["link_status"] = stt
                if stt not in ("dead", "empty"):
                    continue
                vurl = (row.get("verification") or {}).get("primary_url", "")
                vlive = bool(vurl) and vurl != row.get("url") and not await asyncio.to_thread(
                    sources.link_dead, vurl)
                if vlive:
                    row["url"] = vurl                    # swap to the live primary
                    row["link_repointed"] = True
                    row["report_title"] = ""             # it is no longer that report
                else:
                    # no live source exists: strip the dead link and hold the row to
                    # partial, so it is kept for the analyst but never cites a bad URL
                    row["dead_url"] = row.get("url", "")  # kept internally for audit only
                    row["url"] = ""
                    row["report_title"] = ""
                    row["source_reachable"] = False
                    v = row.get("verification") or {}
                    if v.get("status") == "verified":
                        v["status"] = "partial"
                    v["note"] = (v.get("note", "") + " (source link did not resolve, removed)").strip()
                    row["verification"] = v
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
        payload = {"org": org["name"], "id": org["id"], "rows": rows, "dropped": dropped,
                   "reports_found": len(reports), "reports": reports[:20]}
        _write_org(org["id"], payload)
        return payload


_MARK_PTS = {"strong": 3, "partial": 2, "weak": 1}


def _apply_top2(themes: list[dict[str, Any]]) -> None:
    """Deterministic top-2: the two cleanest NEW or ADJACENT themes by weighted
    marks (mandate fit and research-to-policy count double), respecting any the
    model already flagged. Existing themes are never a 'new area to enter'."""
    crit = config.active_spec().get("criteria", [])

    def strength(t: dict) -> int:
        s = sum((2 if c.get("weight", 1) >= 2 else 1) * _MARK_PTS.get(t.get(c["key"], ""), 0)
                for c in crit)
        return s + (1 if t.get("posture") == "enter" else 0)

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


_VERIF_RANK = {"verified": 2, "partial": 1}


def _dedup(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge the same program appearing under different org arms. Conservative: rows
    are treated as one only when their approach names normalize identically, using the
    same normalization the roster uses (parentheticals and descriptor words removed),
    so 'Blue Economy Program (PROFISHBLUE)' and 'Blue Economy Program' collapse while
    genuinely different programs do not. Full acronym aliasing is deliberately NOT
    applied to approach names, where shared words make initials collisions likely, so
    the rare cross-acronym duplicate is left to the human review gate rather than
    risk merging two distinct programs. The strongest (verified over partial) is kept
    and the original order preserved. Returns (kept, dropped_dupes)."""
    def key_of(r: dict) -> str:
        return io_xlsx._norm_org(r.get("name", "") or "")

    def rank(r: dict) -> int:
        return _VERIF_RANK.get((r.get("verification", {}) or {}).get("status", ""), 0)

    best: dict[str, int] = {}
    for i, r in enumerate(rows):
        k = key_of(r)
        if k and (k not in best or rank(r) > rank(rows[best[k]])):
            best[k] = i

    kept: list[dict[str, Any]] = []
    dupes: list[dict[str, Any]] = []
    for i, r in enumerate(rows):
        k = key_of(r)
        if not k or best.get(k) == i:
            kept.append(r)
        else:
            dupes.append({"org": r.get("org", ""), "name": r.get("name", ""),
                          "reason": "duplicate of the same program under another organization"})
    return kept, dupes


async def run_stage1(only: Path | None = None, progress: Progress = None) -> None:
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    spec.save_spec(config.active_spec(), config.WORK_DIR / "spec.json")
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
    orgs_scanned = orgs_with_rows = orgs_with_reports = 0
    for o in orgs:
        payload = _read_org(o["id"])
        if not payload:
            continue
        orgs_scanned += 1
        rws = payload.get("rows", [])
        if rws:
            orgs_with_rows += 1
        if payload.get("reports_found"):
            orgs_with_reports += 1
        all_rows.extend(rws)
        all_dropped.extend(payload.get("dropped", []))

    # 4. dedup the same program appearing under different org arms, before review
    all_rows, dupes = _dedup(all_rows)
    all_dropped.extend(dupes)

    io_xlsx.write_longlist(all_rows)
    io_xlsx.write_open_questions(all_rows, all_dropped)
    try:
        patterns = await agents.seed_hunches(ctx, [r["name"] for r in all_rows][:80])
    except Exception:
        patterns = []
    io_xlsx.write_hunches(patterns)

    verified = sum(1 for r in all_rows if (r.get("verification", {}) or {}).get("status") == "verified")
    unreachable = sum(1 for r in all_rows if r.get("source_reachable") is False)
    if not config.DRY_RUN:
        print(f"  spend: {client.usage_line()}")
    print(f"stage 1 done: {len(all_rows)} rows ({verified} verified), "
          f"{len(all_dropped)} dropped. Review review/ then run --stage 2.")
    print(f"  coverage: {orgs_with_rows} of {orgs_scanned} organizations yielded approaches, "
          f"{orgs_with_reports} had reports found"
          + (f", {len(dupes)} duplicates merged" if dupes else "")
          + (f", {unreachable} rows rest on a source that could not be read directly" if unreachable else "")
          + ".")
    errs = [r.get("error", "") for r in results if r.get("error")]
    if not all_rows:
        if errs:
            print(f"  {len(errs)} of {len(results)} organizations errored. First error: {errs[0][:160]}")
            print("  (a free model's web search still needs OpenRouter credits, a 402 means the balance is empty.)")
        elif all_dropped:
            print("  note: every candidate was dropped at the reading stage. This is usually the model "
                  "over-dropping, not an error. Try a steadier model (e.g. openai/gpt-4o-mini) and re-run.")


async def run_stage2() -> None:
    sp_path = config.WORK_DIR / "spec.json"
    if sp_path.exists():
        config.SPEC = spec.load_spec(sp_path)
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

    # give the synthesizer the actual evidence per theme, so a fuller memo is
    # built from real material rather than padded out
    detail_by_name = {r["name"]: r for r in kept}
    for t in themes:
        md = []
        for m in t.get("members", []):
            r = detail_by_name.get(m)
            if r:
                md.append({"name": m, "org": r.get("org", ""), "year": r.get("year", ""),
                           "what": r.get("what", ""), "evidence": r.get("evidence", ""),
                           "uptake": r.get("uptake", ""), "overall": r.get("overall", ""),
                           "source": r.get("url", "")})
        t["member_details"] = md

    _apply_top2(themes)  # guarantee the two cleanest new areas are named
    synth = await agents.synthesize(ctx, themes)

    memo = guardrail.scrub(synth.get("memo_markdown", ""))
    intro = guardrail.scrub(synth.get("scorecard_intro", ""))
    io_xlsx.write_map(kept, themes)
    io_xlsx.write_scorecard(themes, intro)
    io_xlsx.write_memo(memo)
    docx_out.write_memo_docx(memo, config.OUT_DIR / "synthesis_memo.docx")
    top2 = [t["name"] for t in themes if t.get("top2")]
    if not config.DRY_RUN:
        print(f"  spend: {client.usage_line()}")
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
