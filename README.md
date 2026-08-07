# scan-agents

A portable research pipeline that runs a horizon scan for an economic
transformation hub. Point it at a list of organizations and a set of context
files, and it fans out one agent per organization, reads each organization's own
reports, scores and verifies what it finds against primary sources, pauses for a
human review, then produces a themed decision map and a memo.

The engine is generic. The `context/*.md` files and the organization sheet make
it a specific scan, so editing `mission.md` retargets it to another institute
without touching the code.

## What it produces

- A theme map that marks each area enter, watch, or deepen, with the evidence
  behind the call.
- A longlist of approaches, each tied to a primary source with a title, a link,
  and a date.
- A memo that reads as one argument, grounded in that evidence.

## How it works

Per organization, a fixed sequence runs, one agent per step. The Scout finds the
organization's candidate programs. The Librarian finds the organization's own
reports and briefs. The Reader reads the opening sections of the report itself,
rather than the landing page, and extracts a single approach with exact quotes. A
long report runs past the reading window, so each row records how much of its
source was actually read and says so when it saw only part. The Scorer marks it on the
criteria. The Verifier opens the same document and tries to disconfirm the claim.
An Auditor then checks the whole chain for consistency.

Deterministic checks run alongside the agents and cannot be talked past. A dead,
redirected, or out-of-window source is dropped before it is read, the confirming
quote is checked against the source, and a single policy layer keeps every output
clean. A person reviews the longlist between stage one and stage two, then the
Themer clusters the kept approaches and the Synthesizer writes the memo.

The governing principle is that the models propose and read while the code
verifies and enforces. A claim reads as verified only when its quote is found in
the cited source.

## Setup

```
cd scan-agents
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env          # add ANTHROPIC_API_KEY
python -m scan init           # writes a sample input/organizations.xlsx
```

## Run from the command line

```
python -m scan run --stage 1     # scan, score, verify  -> review/
#   open review/longlist.xlsx, set keep = Y or N, correct any cell
#   write your own hunches into review/hunches.md
python -m scan run --stage 2     # themes and memo       -> out/
python -m scan status            # per-organization progress and errors
python -m scan eval              # retrieval quality on the golden set
python -m scan prune --keep 20   # delete all but the newest run folders

python -m scan run --stage 1 --dry-run        # test mode, no key and no cost
python -m scan run --stage 1 --scope global   # any region, not only Africa
```

Stage one is resumable. Each organization is cached in
`work/orgs/<name>-<hash>.jsonl`, keyed on the organization's name, so a re-run only
scans what it has not done. Reordering, inserting, or deleting rows in the sheet is
safe, the cache follows the organization rather than the row. Renaming an
organization forces a re-scan, as does deleting its file.

## The web app

`app.py` is a four-step web app (build the roster, run the scan, review, generate
the report) so the team never touches the terminal, the key, or the files. The key
lives in the host's secrets, each person gets their own run folder, and a test mode
runs the whole flow at zero cost.

Run it locally:

```
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add key and password
streamlit run app.py
```

Deploy privately so only the team can see it:

1. Push this folder to a private GitHub repo.
2. On share.streamlit.io, create a new app pointed at `scan-agents/app.py`.
3. In Settings, set the app to private and invite the team by email. Add the
   secrets `ANTHROPIC_API_KEY` and `APP_PASSWORD`, and `OPENROUTER_API_KEY` if you
   use the compare-models path.
4. Share the URL and the password with the invited team.

## Building the organization roster

In the app you can discover organizations from the research question, add your own
list by pasting names or uploading a sheet, or both. The two merge into one
deduplicated roster, each row marked by where it came from. Added names can be
framed automatically so they sit alongside the discovered ones.

## Configuration

- Models. On the OpenRouter path the finding and web stages run on `OR_MODEL` (a
  cheap model, gpt-4o-mini), while reading the reports and every writing and
  judgment stage run on `OR_MODEL_STRONG` (a strong model, `anthropic/claude-sonnet-4`
  by default). On the native Anthropic path every stage runs on Opus 4.8 unless you
  set `MODEL_SONNET` and `MODEL_HAIKU` to cheaper tiers. Temperature is 0 everywhere
  so the model does not invent URLs or facts.
- Reader proxy. Bot-protected or JavaScript-rendered pages (afdb.org, unctad.org)
  are read through a proxy, `READER_PROXY` (`https://r.jina.ai/` by default), only
  when a direct fetch fails. Set it to "" to disable.
- Recency window. The scan keeps only sources published in a window, 2023 to 2026
  by default. Change it in one place with `SCAN_YEAR_MIN` and `SCAN_YEAR_MAX`, and
  both the enforcement and the agent instructions follow.
- Provider. Set `SCAN_PROVIDER=openrouter` with `OR_MODEL` and `OR_MODEL_STRONG`, or
  `SCAN_PROVIDER=anthropic` with an `ANTHROPIC_API_KEY` for native web search and
  prompt caching.
- Scope. The scan targets Africa by default. Pass `--scope global`, or set
  `SCAN_MODE=global`, to scan any region and note the transfer to Africa.
- Test mode. `--dry-run` (or `SCAN_DRY_RUN=1`) runs the whole pipeline on canned
  data with no key and no network, so the flow can be checked at zero cost.

## Cost

The tool pays per unit of text the models read and write, so cost tracks how much
reading and writing a scan does. OpenRouter passes the model prices through with no
markup on usage, and adds about 5.5 percent only when you top up credits.

- Cheap model, gpt-4o-mini, does the searching and discovery, about $0.15 and $0.60
  per million tokens in and out.
- Strong model, Claude Sonnet 4, reads the reports and writes, about $3 and $15 per
  million, and is the main cost.

Each stage prints its own spend, priced per model from the table in `config.PRICES`
and including the tokens written to and read from the cache. A model with no price
on file is named rather than guessed at, so the figure is never quietly wrong; add
one with `SCAN_PRICES={"model-id": [input, output]}`.

A measured run of 20 self-discovered organizations cost $2.52 ($2.24 on Sonnet 4,
$0.28 on gpt-4o-mini), about $0.13 per organization. As a guide, and these are
estimates on current prices:

| Organizations | Cost per scan |
|---|---|
| 10 | about $1.30 |
| 20 | about $2.50 (measured) |
| 30 | about $3.80 |
| 50 | about $6.30 |
| dry-run (test) | free |

At 50 organizations, a monthly scan is about $6 a month (about $75 a year) and a
weekly scan is about $25 a month (about $300 a year).

## Caching and resume

Two different things, and they behave differently across runs:

- Instruction (prompt) caching runs only on the native Anthropic path, not through
  OpenRouter, and it is short-lived (about 5 minutes), so it saves cost within a
  single run, not across separate runs.
- Result caching (resume) works across runs: stage 1 skips organizations already
  scanned (cached in `work/orgs/<id>.jsonl`), so re-running the same scan is cheap
  and only pays for new organizations. Stage 2 (theming and the memo) re-runs each
  time, about 40 to 50 cents. Delete an organization's file to force a re-scan, or
  start a fresh scan for full cost.

## The organization sheet

`input/organizations.xlsx`, one row per organization. Columns: `id`, `name`,
`type`, `region`. Only `name` is required.

## Where the scan-specific content lives

Two places, and it matters which. Anything the code also enforces lives in the scan
spec, `scan/spec.py`, as data, so the instruction the model reads and the check the
code runs are generated from one definition and cannot drift apart. Everything else
is a markdown file in `context/`.

In the spec (`DEFAULT_SPEC`, editable per run and saved to `work/spec.json`):

| Key | Controls | Also enforced by |
|---|---|---|
| `research_question`, `lenses` | the mission, the two lenses, the DEPTH frame | |
| `criteria` | the criteria, their weights, the marks | the scoring and theming schemas |
| `context` | the standing rules | |
| `excluded_areas` | the institute's existing portfolio | `spec.screen_existing`, after theming |
| `memo` | the memo's length and its section list | `spec.memo_shortfall`, after synthesis |

In `context/`:

| File | Controls |
|---|---|
| `themes.md` | how themes are tagged and kept tight |
| `output_spec.md` | the row and scorecard schema, the voice, and the house style |
| `policy.md` | the non-negotiables the guardrail enforces |
| `exemplar.md` | the gold-standard register and depth to match |
| `mission_global.md` | the mission text used when the scope is global |

`mission.md`, `scope.md`, and `scoring.md` are historical: their content moved into
the spec and they are no longer read. Editing them changes nothing.

## Tests

```
python -m pytest -q
```

The suite covers the deterministic gates, the policy guardrail, link health, the
recency window, and the roster and row de-duplication.
