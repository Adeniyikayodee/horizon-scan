"""Orchestration. Stage 1 fans out one leg per org (sectioned parallelization)
and emits stage events so a UI can show each org move through scout, read,
score, and verify live. Resumable per org via work/orgs/<name>-<hash>.jsonl, keyed
on the organization's name so reordering the sheet cannot cross the wires. Stage 2
reads the human-edited review files and runs theming + synthesis.

A `progress` callable, if given, receives a dict per org whenever its state
changes: {id, org, scout, read, score, verify, kept, verified, dropped, note},
each stage being "pending" | "run" | "done".
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections import Counter
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


def _org_key(org: dict[str, str]) -> str:
    """The resume cache key: the organization's normalized NAME, not its row number.

    Ids are positional, io_xlsx.read_orgs numbers by row and merge_orgs renumbers
    contiguously, so keying the cache on the id meant that inserting or deleting a
    row in the sheet silently served one organization's cached result under another
    organization's name. The name is kept in the filename so the directory stays
    readable, and the hash makes the key exact."""
    base = io_xlsx._norm_org(org.get("name", "") or "")
    h = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
    slug = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:40] or "org"
    return f"{slug}-{h}"


def _org_path(org: dict[str, str]) -> Path:
    return config.ORGS_WORK / f"{_org_key(org)}.jsonl"


def _write_org(org: dict[str, str], payload: dict[str, Any]) -> None:
    with _org_path(org).open("w", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _read_org(org: dict[str, str]) -> dict[str, Any] | None:
    p = _org_path(org)
    if p.exists():
        return json.loads(p.read_text())
    # Migrate a cache written before the key changed, but ONLY when the legacy file
    # really does hold this organization. A mismatch is the old bug presenting
    # itself, and ignoring it re-scans the organization rather than inheriting
    # someone else's result. Nothing correct is thrown away.
    legacy = config.ORGS_WORK / f"{org.get('id', '')}.jsonl"
    if org.get("id") and legacy.exists():
        try:
            payload = json.loads(legacy.read_text())
        except Exception:
            return None
        if io_xlsx._norm_org(payload.get("org", "")) == io_xlsx._norm_org(org.get("name", "")):
            _write_org(org, payload)
            return payload
    return None


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
                dropped.append({"org": org["name"], "name": cand.get("name", "?"),
                                "stage": "error", "reason": str(e)[:160], "error": str(e)})
                await tick()
                continue
            # HARD RULE: the source the Reader actually read must fall in the recency window
            yr = _first_year(r.get("access_note", ""), src.get("report_date", ""), src.get("year", ""))
            # reflection gate: drop a thin or empty reading before it costs a scoring
            # call, a substantive reading has a described approach and either a quote
            # or stated evidence
            thin = not (str(r.get("what", "")).strip()
                        and (r.get("quotes") or str(r.get("evidence", "")).strip()))
            # every drop carries a coarse stage (for the rate histogram) and a plain
            # reason (for the analyst), so a run that drops 40 candidates says why 40
            # times instead of once
            if not r.get("keep"):
                dropped.append({"org": org["name"], "name": cand["name"],
                                "stage": "not kept at reading",
                                "reason": r.get("keep_reason") or "no reason given"})
            elif thin:
                dropped.append({"org": org["name"], "name": cand["name"], "stage": "thin reading",
                                "reason": "reading too thin, no substantive content"})
            elif _out_of_window(yr):
                dropped.append({"org": org["name"], "name": cand["name"],
                                "stage": "outside the recency window",
                                "reason": f"source dated {yr}, outside {config.YEAR_MIN}-{config.YEAR_MAX}"})
            elif r.get("band") == "maturing":
                dropped.append({"org": org["name"], "name": cand["name"], "stage": "maturing",
                                "reason": "maturing, now standard practice"})
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
                dropped.append({"org": org["name"], "name": appr.get("name", "?"),
                                "stage": "error", "reason": str(e)[:160], "error": str(e)})
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
                "read_chars": appr.get("read_chars", 0),
                "source_chars": appr.get("source_chars", 0),
                "source_truncated": appr.get("source_truncated", False),
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
            # content relevance: are the Reader's OWN quotes in the source, not just
            # the Verifier's confirming quote? Catches a reading pulled from a real but
            # unrelated page, the deepest remaining hallucination path
            rq = [x for x in (row.get("quotes") or []) if x][:3]
            if config.GROUND_QUOTES and not config.DRY_RUN and rq and row.get("url"):
                checks = [await asyncio.to_thread(sources.quote_grounded, row["url"], x) for x in rq]
                if any(c is True for c in checks):
                    row["reading_grounded"] = True
                elif any(c is False for c in checks):     # checkable quotes, none in the source
                    row["reading_grounded"] = False
                else:
                    row["reading_grounded"] = None
            else:
                row["reading_grounded"] = None
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
        _write_org(org, payload)
        return payload


_MARK_PTS = {"strong": 3, "partial": 2, "weak": 1}


def _apply_top2(themes: list[dict[str, Any]]) -> list[str]:
    """Deterministic top-2: the two cleanest themes to ENTER, by weighted marks
    (mandate fit and research-to-policy count double), respecting any the model
    already flagged. Existing themes are never a 'new area to enter'.

    A theme must carry the enter posture to be eligible. Without that guard a run
    where nothing was worth entering still named two 'cleanest new areas to enter',
    which is an invented recommendation. Fewer than two entry themes is a real
    finding, so it is reported rather than padded. Returns the chosen names."""
    crit = config.active_spec().get("criteria", [])

    def strength(t: dict) -> int:
        return sum((2 if c.get("weight", 1) >= 2 else 1) * _MARK_PTS.get(t.get(c["key"], ""), 0)
                   for c in crit)

    eligible = sorted(
        [t for t in themes if t.get("tag") in ("new", "adjacent") and t.get("posture") == "enter"],
        key=lambda t: (1 if t.get("top2") else 0, strength(t)), reverse=True)
    chosen = {id(t) for t in eligible[:2]}
    for t in themes:
        t["top2"] = id(t) in chosen
    return [t["name"] for t in eligible[:2]]


def _reject_dead_corroboration(corr: dict[str, Any], dead: bool) -> dict[str, Any]:
    """A second source whose link does not resolve corroborates nothing. Pulled out
    of run_stage2 so the rule can be tested without a network or a full stage."""
    if corr.get("corroborated") and corr.get("url") and dead:
        corr["corroborated"] = False
        corr["url"] = ""
        corr["note"] = (corr.get("note", "") + " (second-source link did not resolve)").strip()
    return corr


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


def drop_report(rows: list[dict[str, Any]], dropped: list[dict[str, Any]]) -> str:
    """The reading gate's drop rate, with a histogram of stages, or '' when the rate
    is unremarkable.

    Nearly every drop is one model boolean, and across the archived runs its rate
    swung from 0 to 79 percent on identical code, with two runs returning nothing at
    all. This surfaces that rather than fixing it silently: an automatic retry would
    hide the very signal worth measuring, and the honest response to a run that threw
    away four candidates in five is for a person to look at why."""
    considered = len(rows) + len(dropped)
    if not considered or len(dropped) / considered < config.DROP_RATE_WARN:
        return ""
    hist = Counter(d.get("stage") or "not kept at reading" for d in dropped)
    parts = ", ".join(f"{n} {stage}" for stage, n in hist.most_common())
    return (f"{len(dropped)} of {considered} candidates set aside "
            f"({len(dropped) / considered:.0%}): {parts}")


_VERIF_RANK = {"verified": 2, "partial": 1}


def _dedup(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Merge the same program appearing under different org arms. Conservative: rows
    are treated as one only when their approach names normalize identically under
    _norm_approach, which drops parentheticals and punctuation but KEEPS every
    descriptor word, so 'Blue Economy Program (PROFISHBLUE)' and 'Blue Economy
    Program' collapse while 'Blue Economy Fund' and 'Blue Economy Initiative' stay
    two programs. This used the roster's _norm_org until the July 2026 review, which
    strips 'fund', 'institute', 'initiative', and 'foundation', and so silently
    merged distinct programs. Full acronym aliasing is deliberately NOT applied to
    approach names, where shared words make initials collisions likely, so the rare
    cross-acronym duplicate is left to the human review gate rather than risk merging
    two distinct programs. The strongest (verified over partial) is kept and the
    original order preserved. Returns (kept, dropped_dupes)."""
    def key_of(r: dict) -> str:
        return io_xlsx._norm_approach(r.get("name", "") or "")

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
            dupes.append({"org": r.get("org", ""), "name": r.get("name", ""), "stage": "duplicate",
                          "reason": "duplicate of the same program under another organization"})
    return kept, dupes


async def run_stage1(only: Path | None = None, progress: Progress = None) -> None:
    config.WORK_DIR.mkdir(parents=True, exist_ok=True)
    spec.save_spec(config.active_spec(), config.WORK_DIR / "spec.json")
    ctx = config.load_context()
    orgs = io_xlsx.read_orgs(only) if only else io_xlsx.read_orgs()
    manifest = _load_manifest()
    sem = asyncio.Semaphore(config.MAX_CONCURRENCY)

    todo = [o for o in orgs if _read_org(o) is None]
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
        payload = _read_org(o)
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
    ungrounded = sum(1 for r in all_rows if r.get("reading_grounded") is False)
    read_live = len(all_rows) - unreachable
    if not config.DRY_RUN:
        print(f"  spend: {client.usage_line()}")
    print(f"stage 1 done: {len(all_rows)} rows ({verified} verified), "
          f"{len(all_dropped)} dropped. Review review/ then run --stage 2.")
    print(f"  coverage: {orgs_with_rows} of {orgs_scanned} organizations yielded approaches, "
          f"{orgs_with_reports} had reports found, {read_live} of {len(all_rows)} rows read from a live source"
          + (f", {len(dupes)} duplicates merged" if dupes else "")
          + (f", {unreachable} rest on an unreadable source" if unreachable else "")
          + (f", {ungrounded} flagged where the reading was not found in the source" if ungrounded else "")
          + ".")
    report = drop_report(all_rows, all_dropped)
    if report:
        print(f"  ! {report}")
        print("    A high rate is usually the reading gate over-dropping rather than an error. "
              "Every candidate and its reason is listed in review/open_questions.md.")
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

    # HARD RULE, enforced in code and not merely asked for: a theme that lands in the
    # institute's existing portfolio is existing work and carries deepen, whatever the
    # model tagged it. This runs BEFORE _apply_top2, so a screened theme can never be
    # promoted to one of the two cleanest new areas.
    themes = spec.screen_existing(themes, config.active_spec())
    screened = [t for t in themes if t.get("screened")]
    if screened:
        print(f"stage 2: {len(screened)} theme(s) held back to existing work by the portfolio screen")
        for t in screened:
            print(f"    {t.get('name','')}: {t['screened']}")
    io_xlsx.write_theme_screen(themes)

    # carry the scan's verification through into the report: per-theme evidence
    row_by_name = {r["name"]: r for r in kept}
    for t in themes:
        members = [row_by_name.get(m) for m in t.get("members", [])]
        found = [r for r in members if r]
        ver = [r for r in found if (r.get("verification") or {}).get("status") == "verified"]
        par = sum(1 for r in found if (r.get("verification") or {}).get("status") == "partial")
        unk = max(0, len(t.get("members", [])) - len(ver) - par)
        # how many of the verified rows were actually checked against their source,
        # so the memo can state firmness rather than repeat the word "verified"
        chk = sum(1 for r in ver if guardrail.evidence_check(r) == "checked")
        t["verified_count"], t["partial_count"], t["checked_count"] = len(ver), par, chk
        vtext = (f"{len(ver)} verified ({chk} confirmed against the source, "
                 f"{len(ver) - chk} on the reading alone)" if ver else "")
        parts = ([vtext] if vtext else []) + ([f"{par} partial"] if par else []) \
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

    top2 = _apply_top2(themes)   # the two cleanest areas to enter, or fewer, honestly
    if len(top2) < 2:
        print(f"  ! only {len(top2)} theme carries the enter posture, so the memo names "
              f"{len(top2)} area(s) to enter rather than an invented pair")

    # two-source corroboration for the entry themes: confirm each one's central
    # finding on an INDEPENDENT source, so the recommendations do not rest on one
    entry = [t for t in themes if t.get("posture") == "enter"]
    if entry:
        print(f"stage 2: corroborating {len(entry)} entry themes on a second source")
        detail = {r["name"]: r for r in kept}
        for t in entry:
            m = t.get("marquee", "") or (t.get("members") or [""])[0]
            r = detail.get(m, {})
            claim = f"{t.get('name','')}: {m}. {r.get('what','')} {r.get('evidence','')}".strip()
            try:
                corr = await agents.corroborate(ctx, claim)
            except Exception:
                corr = {"corroborated": False, "source": "", "url": "", "quote": "", "note": "check failed"}
            # a dead or unresolved second-source link does not corroborate anything
            cu = corr.get("url", "")
            dead = bool(cu) and not config.DRY_RUN and await asyncio.to_thread(sources.link_dead, cu)
            t["corroboration"] = _reject_dead_corroboration(corr, dead)

    synth = await agents.synthesize(ctx, themes)

    # The policy check runs BEFORE the first write, and never raises. By this point
    # the run is paid for, so a banned phrase becomes a note beside the deliverables
    # rather than an exception that leaves a half-written out/ and nothing to show.
    memo, memo_hits = guardrail.finalize("synthesis_memo", synth.get("memo_markdown", ""))
    intro, intro_hits = guardrail.finalize("scorecard_intro", synth.get("scorecard_intro", ""))
    io_xlsx.write_map(kept, themes)
    io_xlsx.write_scorecard(themes, intro)
    io_xlsx.write_memo(memo)
    docx_out.write_memo_docx(memo, config.OUT_DIR / "synthesis_memo.docx")
    style = guardrail.title_notes(memo)
    vpath = io_xlsx.write_policy_violations(
        [("synthesis_memo.md", memo, memo_hits), ("theme_scorecard.xlsx intro", intro, intro_hits),
         ("synthesis_memo.md, house style", memo, style)])
    if vpath:
        n = len(memo_hits) + len(intro_hits) + len(style)
        print(f"  ! {n} policy or style note(s) left after the scrub. "
              f"Every file was written. Fix by hand, see {vpath}")
    if not config.DRY_RUN:
        print(f"  spend: {client.usage_line()}")
    print(f"stage 2 done: {len(themes)} themes -> out/. "
          + (f"Cleanest new areas: {', '.join(top2)}" if top2
             else "No theme carries the enter posture, nothing is recommended for entry."))


def prune_runs(keep: int = 20, dry: bool = False) -> list[str]:
    """Delete all but the newest `keep` run folders under runs/.

    The app gives each run its own folder and nothing ever removed them, so the
    directory grew without bound inside the repo. Newest by modification time, and
    the caller is told exactly what went."""
    root = config.ROOT / "runs"
    if not root.is_dir():
        return []
    dirs = sorted([d for d in root.iterdir() if d.is_dir()],
                  key=lambda d: d.stat().st_mtime, reverse=True)
    doomed = dirs[max(0, keep):]
    if not dry:
        import shutil
        for d in doomed:
            shutil.rmtree(d, ignore_errors=True)
    return [d.name for d in doomed]


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
