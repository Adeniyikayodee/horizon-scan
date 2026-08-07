# FIX plan

Nine fixes, in priority order, for the findings in the code review of July 2026.
Internal engineering document, not a deliverable.

**Status: all nine landed.** Test suite went from 55 to 169 passing. Every fix
shipped with a test that fails without it, and F1, F2, F5, and F7 also replay over
the archived runs. The as-built deviations from this plan are recorded at the
bottom, because a plan that quietly stops matching the code is the same drift this
document exists to prevent.

Each fix keeps its review number, so `F1` here is finding 1 there and nothing has
to be re-mapped later. Priority order is the order of this file, which is not the
review's numeric order.

| Order | Fix | Review finding | Cost | Blast radius |
|---|---|---|---|---|
| 1 | Screen themes against the existing portfolio | 1 | half a day | `spec.py`, `pipeline.run_stage2` |
| 2 | Separate the approach-name dedup key | 2 | one hour | `io_xlsx`, `pipeline._dedup` |
| 3 | Stop the guardrail destroying a paid run | 4 | two hours | `guardrail`, `io_xlsx`, `pipeline.run_stage2` |
| 4 | Make the memo shape data, and detect truncation | 3 | one day | `spec.py`, `agents.synthesize`, `client` |
| 5 | Split the verified label | 7 | half a day | `io_xlsx`, `app.py` |
| 6 | Key the resume cache on the organization, not the row number | 5 | two hours | `pipeline`, `config` |
| 7 | Give `keep` a rubric and an audit trail | 6 | half a day | `agents.READER_I`, `schemas`, `pipeline` |
| 8 | Tell the truth about how much of a report is read | 8 | two hours | `config`, `sources`, `agents`, `README` |
| 9 | The small bucket | 9 | one day | several, each isolated |

Total: about four working days, and the first three are the ones that change what
the client receives.

---

## The anti-drift rules

These apply to every fix below. They exist because the review found the same
failure three separate times: a rule stated in prose in one place, enforced in
code in another, and the two no longer agreeing.

1. **One source of truth per rule, and the prompt quotes it.** If a rule is
   enforced in code, the code owns the value and the agent frame renders it. The
   pattern to copy already exists in this repo: `config.window_rule()` builds the
   recency sentence from `YEAR_MIN` and `YEAR_MAX`, the same constants
   `pipeline._out_of_window` checks against, so the window cannot drift between
   what is said and what is enforced. Every fix below either follows that pattern
   or explains why it cannot.
2. **A rule that matters is enforced deterministically, not asked for politely.**
   Prompt text is a hint. If the deliverable is wrong when the rule is broken, the
   rule needs a gate in Python.
3. **Failing test first.** Each fix lands with a test that fails on `main` and
   passes after. No fix is done because it looks right.
4. **Explicit non-goals.** Each fix states what it must not touch. If the work
   starts spreading past that line, stop and open a separate item.
5. **Do not renumber the findings.** The review is the shared reference. New
   problems found while fixing get new numbers, appended, never inserted.
6. **Prove it against the archived runs.** `runs/` holds 35 real runs and 1,071
   cached organization payloads. It is a free regression corpus, no API spend
   required. Every gate added below should be replayed over it before it ships.

---

## F1. Screen themes against the existing portfolio

**Review finding 1.** The exclusion list is prompt text only. The model dodges it
by tagging an existing area `new`, and it already has: run `4be936d649` recommends
"AI-Driven Policy Experimentation Platforms" as a top entry while `spec.py:95`
lists "the digital economy, DPI, and AI" as existing work, and the same memo
reports zero themes in the deepen category.

### Approach

Turn the portfolio from prose into data, screen every theme against it in code,
and make the screen explainable rather than silent.

**Step 1, the list becomes data.** Add to `DEFAULT_SPEC` in `spec.py`:

```python
"excluded_areas": [
    {"name": "Digital economy, DPI, and AI",
     "terms": ["digital", "dpi", "artificial intelligence", "ai ", "machine learning",
               "data governance", "digital public infrastructure"]},
    {"name": "Industrial policy and productive transformation",
     "terms": ["industrial policy", "productive transformation", "manufacturing policy"]},
    # ... one entry per area named in the current context prose
],
```

**Step 2, the prompt renders from the data.** `spec.scope_text()` stops carrying
the hand-written list and builds it from `excluded_areas`, the same way
`scoring_text()` already builds the criteria block. One list, two consumers.

**Step 3, the gate.** New function in `spec.py`:

```python
def screen_existing(themes: list[dict], spec: dict) -> list[dict]:
    """Force any theme that matches the institute's existing portfolio to
    tag=existing, posture=deepen, and record why. Deterministic and explainable."""
```

Match on the concatenation of `name`, `rationale`, `marquee`, and `members`,
normalized. On a hit set `tag="existing"`, `posture="deepen"`, and
`t["screened"] = f"matched existing portfolio: {area} via '{term}'"`. Record
near-misses (one term matched but below threshold) in `t["screen_note"]` without
changing the posture.

**Step 4, call it.** In `pipeline.run_stage2`, immediately after
`themes = await agents.themes(...)` and **before** `_apply_top2(themes)`, so a
screened theme can never be promoted to a top entry area.

**Step 5, surface it.** Write the screened and near-miss themes into
`review/open_questions.md` so the analyst sees what the gate caught and can
overrule it in the next run by editing the spec, not the code.

### Test

`tests/test_screen.py`: a theme named "AI-Driven Policy Experimentation Platforms"
tagged `new` with posture `enter` comes back `existing` and `deepen`. A theme named
"Blue economy and coastal value addition" is untouched. Replay: load
`runs/4be936d649/out/theme_scorecard.xlsx`, run the screen over its theme names,
assert the AI theme flips.

### Non-goals

No embeddings, no model call in the screen. A term list is auditable by the
analyst and a similarity score is not. If keyword matching proves too blunt after
a few runs, revisit it as a separate item, do not widen it here.

---

## F2. Separate the approach-name dedup key

**Review finding 2.** `pipeline._dedup` reuses `io_xlsx._norm_org`, which strips
organization descriptor words. "Blue Economy Fund" and "Blue Economy Initiative"
both normalize to "blue economy" and one is silently dropped.

### Approach

Two different jobs need two different normalizers. Add to `io_xlsx.py`:

```python
_APPROACH_STOP = {"the", "and", "of", "for", "a", "an"}   # articles only

def _norm_approach(name: str) -> str:
    """Normalization for PROGRAM names. Drops parentheticals and punctuation so
    'Blue Economy Program (PROFISHBLUE)' still collapses onto 'Blue Economy
    Program', but keeps every descriptor word, because 'Fund' and 'Initiative'
    distinguish two real programs even when the rest of the name matches."""
```

Point `pipeline._dedup.key_of` at it and correct the docstring, which currently
claims a conservatism the stop-word list removed.

### Test

Extend `tests/test_gates.py`:

- `Blue Economy Program (PROFISHBLUE)` and `Blue Economy Program` still merge, the
  verified row surviving. This is the existing test and it must keep passing.
- `Blue Economy Fund` and `Blue Economy Initiative` do not merge.
- `Africa Climate Foundation` and `Africa Climate Institute` do not merge.
- `Digital Skills Fund` and `Digital Skills Foundation` do not merge.

Replay: run the new `_dedup` over every `runs/*/work/longlist_full.json` and print
the merges it makes. Any merge that is not a parenthetical or casing variant is a
bug in the fix.

### Non-goals

Do not touch `_norm_org`, `_org_sig`, `_acro_hits`, or `merge_orgs`. Roster
matching is a different problem, it is tested, and it works.

---

## F3. Stop the guardrail destroying a paid run

**Review finding 4.** `io_xlsx.write_memo` calls `assert_clean`, which raises.
`write_map` and `write_scorecard` have already written by then, so a memo that
says "large language model" once aborts stage 2 after full spend and leaves a half
populated `out/`.

### Approach

Check before writing anything, scrub what is mechanically scrubbable, and route
what is not to a human. Never raise after spend.

**Step 1**, in `guardrail.py`:

```python
def finalize(label: str, text: str) -> tuple[str, list[str]]:
    """Scrub what can be safely scrubbed, then report what remains. Returns the
    cleaned text and any violations a machine must not fix on its own."""
    text = scrub(text)                 # mechanical only: the long-dash glyphs
    return text, scan_text(text)
```

**Step 2**, in `pipeline.run_stage2`, run `finalize` on the memo and the scorecard
intro **before** the first write. If violations remain: write every artifact as
normal, additionally write `review/policy_violations.md` naming the file, the
violation, and the surrounding sentence, and print a loud warning. The run
completes, the analyst fixes the sentence.

**Step 3**, `write_memo` and `write_scorecard` stop raising. Keep `assert_clean`
in the module, used only by tests, so the strict check still has a home.

**The design rule, write it in the docstring:** `scrub` only ever fixes mechanical
things, currently the dash glyphs. It must never regex-delete a banned phrase out
of prose, because "the memo no longer says the forbidden thing" and "the memo
still means what it meant" are different properties and only a human can hold
both. Semantic violations are surfaced, not silently rewritten.

### Test

A memo containing "large language model" produces `synthesis_memo.md`,
`synthesis_memo.docx`, `innovation_map.xlsx`, `theme_scorecard.xlsx`, and
`review/policy_violations.md`, and does not raise. A memo containing an em dash
produces a clean memo with a comma and no violations file.

### Non-goals

Do not change `BANNED_IDENT`, `BANNED_PHRASE`, `MECHANICS`, or the dash set. The
lists are well calibrated and covered by the false-positive tests. This fix is
about when the check runs and what happens on a hit.

---

## F4. Make the memo shape data, and detect truncation

**Review finding 3.** The memo asks for eight to twelve pages and delivers a
median of three. Two causes, plus a contradiction:

- `_truncated` is set only on the native path (`client.py:203`), so
  `synthesize`'s retry loop is dead code on the OpenRouter path the app
  hard-codes. A truncated tool-call argument throws inside `json.loads` and kills
  stage 2 instead of retrying.
- Nothing measures the output.
- `context/output_spec.md` says "two to three pages" while `agents.SYNTH_I` says
  eight to twelve. The model reads both and follows the shorter.

### Approach

**Step 1, truncation is detectable on both paths.** In `_openrouter_call`, read
`resp.choices[0].finish_reason`. On `"length"`, raise a typed
`TruncatedOutput(max_tokens)` rather than letting `json.loads` throw an opaque
error. Wrap the `json.loads` in the same handler. In `agents.synthesize`, catch
`TruncatedOutput` inside the existing budget loop so both providers retry through
one code path.

**Step 2, the memo shape becomes data.** This is the anti-drift move, and it is
the same one `spec.py` already made for the criteria. In `spec.py`:

```python
"memo": {
    "min_words": 4000,                    # the floor a real 8 to 12 page memo clears
    "sections": [
        {"heading": "Executive summary", "guidance": "Open with the recommendation itself ..."},
        {"heading": "Purpose and scope",  "guidance": "..."},
        # ... the full ordered list currently hardcoded in SYNTH_I
    ],
},
```

`agents.SYNTH_I` stops listing headings inline and renders them from
`spec["memo"]["sections"]`, exactly as `scoring_text()` renders the criteria.
`context/output_spec.md` drops its own length statement entirely and says the memo
shape is set by the scan spec. One definition, one consumer chain, no contradiction
possible.

**Step 3, the floor is checked.** After `synthesize`, count words and confirm every
required heading is present. Short or missing a section, retry once with the larger
budget. Still short, ship it and print exactly what fell short. Never silently
deliver a third of the specified document.

### Test

- A stub response with `finish_reason="length"` triggers exactly one retry at the
  larger budget on both providers.
- The heading list rendered into the frame equals `spec["memo"]["sections"]`, so a
  spec edit provably reaches the prompt.
- A memo of 900 words against a 4,000-word floor is reported, not silently
  accepted.

### Non-goals

Do not rewrite the voice guidance in `SYNTH_I`. It is long, it is specific, and the
memos that do reach length read well. This fix is about structure and length, not
prose.

---

## F5. Split the verified label

**Review finding 7.** `reading_grounded` is `None` on 29 of 29 rows in
`a158691ae1`, and `quote_grounded` is `True` on 0 of 4 rows in several runs. A row
reading "verified" may mean the quote was found in the fetched source, or that a
model asserted it and nothing could be checked. `write_map` prints one word for
both.

### Approach

The distinction already exists in the row. Expose it in a **new column**, and leave
the `verification` field alone.

That last point matters. Two consumers parse the existing string:
`io_xlsx.read_kept_longlist` overlays the review sheet's `verification` cell onto
the row's status, and `app.py` counts `df["verification"] == "verified"`. Changing
the string breaks the stage-two rejoin and the app counter at once. So:

```python
def evidence_check(row: dict) -> str:
    """How firm 'verified' is. 'checked' when a quote was found in the fetched
    source, 'unchecked' when the source could not be read, '' for partial rows."""
```

`True` on either `quote_grounded` or `reading_grounded` gives "checked". Verified
with both `None` gives "unchecked". Add an "Evidence check" column to
`write_longlist` and `write_map`, add the same line to the app's row cards and
dossier, and fold the counts into the per-theme `t["evidence"]` string in
`run_stage2` so the memo can state firmness honestly.

### Test

A row verified with `quote_grounded=True` reads "checked". A row verified with both
`None` reads "unchecked". `read_kept_longlist` round-trips a workbook written with
the new column and returns the same statuses as before, which is the regression
that protects the rejoin.

### Non-goals

Do not change the `verification.status` vocabulary, `guardrail.settle_row`, or the
downgrade rules. Those are correct. This is a reporting fix.

---

## F6. Key the resume cache on the organization, not the row number

**Review finding 5.** `_org_path` writes `work/orgs/O001.jsonl`, and ids are
positional. Insert a row in the sheet and every later organization loads a
different one's cached result. All 1,071 archived payloads check out, because the
app isolates each run in its own folder, but the CLI path documented in the README
is exposed.

### Approach

```python
def _org_path(org: dict) -> Path:
    key = hashlib.sha1(io_xlsx._norm_org(org["name"]).encode()).hexdigest()[:10]
    return config.ORGS_WORK / f"{_slug(org['name'])[:40]}-{key}.jsonl"
```

Name plus hash, so the directory stays readable and the key is content addressed.

**Migrate rather than discard.** On a cache miss, look for the legacy
`O###.jsonl`, and accept it only when its stored `payload["org"]` matches the
organization's name. A matching legacy file is rewritten under the new name, a
mismatching one is ignored, which is precisely the bug closing itself. No existing
work is thrown away and no wrong result is inherited.

Update the README resume section: the cache follows the organization's name, so
reordering the sheet is safe and renaming an organization forces a re-scan.

### Test

Two rosters that differ only by an inserted first row produce the same cache path
for a shared organization. A legacy `O005.jsonl` whose `org` field disagrees with
row five is not used.

### Non-goals

Do not change `manifest.json`, which is keyed by id for display only, and do not
touch the app's per-run folder isolation.

---

## F7. Give `keep` a rubric and an audit trail

**Review finding 6.** Drop rates swing from 0 to 79 percent on identical code, and
nearly every drop is one unrubricked boolean in `READER_I`. Two runs returned zero
rows. `pipeline.py:483` already ships a printed apology telling the user to try a
steadier model.

### Approach

**Step 1, rubric.** Rewrite the `keep` clause in `READER_I` with an explicit
threshold and one example each way. It currently says only "keep is about whether
the source is substantive and on-lens". State what substantive means: a named
program or approach, at least one concrete piece of evidence, and a link to
transformation or to the move from evidence into policy. State plainly that a
document being broad, or the approach being early, is not a reason to drop.

**Step 2, auditability.** Add `keep_reason: str` to `READER_SCHEMA`,
`schemas.Reading`, and `_coerce_reading`. Carry it into the `dropped` entries in
`_process_org` and into `write_open_questions`, so a run that drops 40 candidates
says why 40 times.

**Step 3, observability.** At the end of stage 1, when the drop rate exceeds a
threshold, print the rate with a histogram of reasons. Surface, do not auto-retry:
a silent retry would hide exactly the signal being measured.

### Test

`keep_reason` survives coercion, reaches the dropped list, and appears in
`open_questions.md`. Replay the drop-rate reporting over the archived runs and
confirm it flags `3983b64d16` (19 of 31 dropped) and `4be936d649` (24 dropped, 8
organizations empty).

### Non-goals

Do not change the band filter, the recency gate, or the thin-reading gate. Those
are deterministic, they work, and their drops are already labelled.

---

## F8. Tell the truth about how much of a report is read

**Review finding 8.** The Reader sees 60,000 characters, the Verifier 40,000, and
`_pdf_to_text` stops at 80 pages. That is roughly the opening 15 to 20 pages of a
flagship report, while the README claims the Reader "reads the actual report".

### Approach

Honesty first, coverage second.

**Step 1**, the three magic numbers become named constants in `config.py`:
`READ_MAX_CHARS`, `VERIFY_MAX_CHARS`, `PDF_MAX_PAGES`. Three call sites read them.
Tunable in one place, and visible.

**Step 2**, record what was actually read. `sources.fetch_text` reports the full
extracted length alongside the truncated text, and the row carries `read_chars`
and `source_truncated`. The Reader's frame states the window it was given, so the
model does not claim document-wide coverage from a partial read.

**Step 3**, correct the README. "Reads the opening sections of the report rather
than the landing page" is both true and still a strong claim.

**Step 4, optional, measure before building.** Only if the recorded data shows
truncation is losing real findings, add a second pass over the later sections of a
truncated document. Do not build it on the assumption.

### Test

A 200,000-character document sets `source_truncated=True` and a correct
`read_chars`. Constants are read from config, so changing `READ_MAX_CHARS` provably
changes the fetch.

### Non-goals

No chunk-and-map rewrite of the Reader in this fix. It is a real project and it
needs the measurement from step 2 to justify itself.

---

## F9. The small bucket

**Review finding 9.** Six independent items, each an hour or two. Land them
separately, not as one commit.

**a. Cost in currency.** `client.USAGE` ignores `cache_creation_input_tokens`, and
`usage_line` reports tokens while the README quotes dollars. Add a `PRICES` table
in `config.py` keyed by model id, count cache creation, and have `usage_line`
report both tokens and an estimated cost. The README then quotes a number the tool
can actually produce.

**b. `_apply_top2` respects posture.** `pipeline.py:339` picks the two strongest
new or adjacent themes regardless of posture, so a run with no entry themes still
names two "cleanest new areas to enter". Restrict the eligible set to
`posture == "enter"`, and when none qualify say so rather than inventing a pair.

**c. Title style.** `output_spec.md` asks for plain, specific names and the memos
come back as "AI-Driven Policy Platforms and Market Shaping: New Frontiers for
African Economic Transformation". Add a soft check after synthesis, warn on a colon
or heavy title case in the H1, and report it alongside the F3 policy violations.
Warn only, never rewrite a title automatically.

**d. Eval judge.** `evaluate.judge_memo` passes no `tier`, so on the OpenRouter
path the cheap model grades the strong model's prose. Pass `tier="strong"`. Note in
the docstring that a same-family judge is a known weakness and the trajectory eval
is the objective half.

**e. Test the untested joins.** `read_kept_longlist`'s rid rejoin,
`_best_report`'s matching threshold, and the stage-2 corroboration path have no
tests, and they are where silent data loss lives. Note also that
`tests/test_smoke.py`'s existing-theme assertion runs against `mock.py`, which
hard-codes a compliant theme set, so it cannot currently fail. Point it at a
deliberately non-compliant fixture.

**f. `runs/` retention.** 46 MB across 98 directories inside the repo. Add a
`python -m scan prune --keep 20` subcommand and a line in the README. On Streamlit
Community Cloud the filesystem is ephemeral anyway, which is worth stating so
nobody treats a run folder as storage.

---

## Verification protocol

Before any fix ships:

1. `python -m pytest -q` passes, including the new failing-first test.
2. `python -m scan run --stage 1 --dry-run` then `--stage 2` completes and writes
   all four artifacts. Free, no key.
3. The relevant replay over `runs/` produces no regression. F1, F2, F5, and F7 all
   have a replay defined above, and they cost nothing.
4. One paid end-to-end run on a small roster, five to eight organizations, roughly
   a dollar, before calling the batch done.

## As built: where the code differs from this plan, and why

Five deviations. Each is a deliberate call made while implementing, recorded here
so the plan and the code stay one description of the same system.

1. **F1 surfacing goes to its own file.** The plan said write the screen's decisions
   into `review/open_questions.md`. That file is written by stage one, so appending
   to it from stage two either clobbers it or duplicates on every re-run. The screen
   writes `review/theme_screen.md` instead, listing what was held back and what sat
   near the line.
2. **F4 renamed one heading.** "What ACET already runs" became "What the institute
   already runs" when the heading list moved into the spec. The engine is supposed to
   be client-neutral, with everything client-specific in `context/` and the spec, and
   a client's name hardcoded in `agents.py` was a leak. Any scan can now set its own
   wording in `spec["memo"]["sections"]`.
3. **F9c writes title notes into the F3 violations file** rather than a separate one.
   Both are "things a person needs to fix by hand before this goes out", and one file
   to check beats two.
4. **F7 added a coarse `stage` field to every dropped row**, alongside the plain
   reason. The reason is free text and cannot be counted, so the drop-rate histogram
   needed a small fixed vocabulary: not kept at reading, thin reading, outside the
   recency window, maturing, duplicate, error.
5. **F9e extracted `_reject_dead_corroboration` out of `run_stage2`.** The rule that
   a dead second-source link corroborates nothing was inline inside an async stage and
   could not be tested without a network. It is now a two-line pure function, called
   from the same place.

## What this plan deliberately does not do

- No refactor of the agent sequence. Scout, Librarian, Reader, Scorer, Verifier,
  Auditor is the right shape and it is not the problem.
- No provider abstraction rewrite. The dual path is awkward, and it is not
  currently costing correctness anywhere except F4, which is fixed directly.
- No change to the human review gate between stages. It is the best structural
  decision in the system.
- No new agents. Every fix above is a gate, a constant, or a label. If a fix starts
  wanting a model call, that is the signal it has drifted.
