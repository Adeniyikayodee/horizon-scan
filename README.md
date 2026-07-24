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
reports and briefs. The Reader reads the actual report rather than the landing
page and extracts a single approach with exact quotes. The Scorer marks it on the
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

python -m scan run --stage 1 --dry-run        # test mode, no key and no cost
python -m scan run --stage 1 --scope global   # any region, not only Africa
```

Stage one is resumable. Each organization is cached in `work/orgs/<id>.jsonl`, so
a re-run only scans what it has not done. Delete an organization's file to force a
re-scan.

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

- Models. Every stage runs on Opus 4.8 by default. Set `MODEL_SONNET` and
  `MODEL_HAIKU` in `.env` to run the judgment and the mechanical stages on cheaper
  tiers.
- Recency window. The scan keeps only sources published in a window, 2023 to 2026
  by default. Change it in one place with `SCAN_YEAR_MIN` and `SCAN_YEAR_MAX`, and
  both the enforcement and the agent instructions follow.
- Provider. Anthropic is the default, with native web search and prompt caching.
  Set `SCAN_PROVIDER=openrouter` and `OR_MODEL`, or pass `--provider openrouter
  --model <id>`, to run the same agents on another model and compare the results.
- Scope. The scan targets Africa by default. Pass `--scope global`, or set
  `SCAN_MODE=global`, to scan any region and note the transfer to Africa.
- Test mode. `--dry-run` (or `SCAN_DRY_RUN=1`) runs the whole pipeline on canned
  data with no key and no network, so the flow can be checked at zero cost.

## The organization sheet

`input/organizations.xlsx`, one row per organization. Columns: `id`, `name`,
`type`, `region`. Only `name` is required.

## The context files

Everything scan-specific lives in `context/`. Edit these, not the code.

| File | Controls |
|---|---|
| `mission.md` | the two lenses, the DEPTH frame, and the over-the-horizon definition |
| `scope.md` | the standing rules and the existing-programs exclusion list |
| `scoring.md` | the criteria, the marks, and the posture rules |
| `themes.md` | how themes are tagged and kept tight |
| `output_spec.md` | the row and scorecard schema, and the house style |
| `policy.md` | the non-negotiables the guardrail enforces |

## Tests

```
python -m pytest -q
```

The suite covers the deterministic gates, the policy guardrail, link health, the
recency window, and the roster and row de-duplication.
