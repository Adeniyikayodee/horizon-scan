# scan-agents

A portable Claude-API pipeline that runs the follow-up scan. Add your API key,
point it at an Excel org list and the `context/` files, and it fans out one
agent per organization, scores and verifies what it finds, pauses for your
review, then wraps up into a themed map and a memo. The engine is generic; the
`context/*.md` files and the org sheet make it the ACET scan, so swapping
`mission.md` scans for any institute. Design notes in `../agent_scan_design.md`.

## Setup

```
cd scan-agents
python -m venv .venv && source .venv/bin/activate
pip install -e .
cp .env.example .env          # add ANTHROPIC_API_KEY
python -m scan init           # writes a 3-org sample input/organizations.xlsx
```

## Run

```
python -m scan run --stage 1              # scan + score  -> review/   (fan-out)
#   open review/longlist.xlsx, set keep = Y/N, fix facts
#   write your own hunches into review/hunches.md
python -m scan run --stage 2              # themes + memo -> out/
python -m scan status                     # per-org progress and errors
python -m scan run --stage 1 --only new_orgs.xlsx   # incremental round
```

Stage 1 is resumable: each org is cached in `work/orgs/<id>.jsonl`, so a re-run
only scans orgs it has not done. Delete an org's file to force a re-scan.

## For a non-technical team: the web app

`app.py` is a four-step web app (upload a list, run the scan, review, generate
the report) so the team never touches the terminal, the key, or the files. The
key lives in the host's secrets, each person gets their own run folder, and a
test mode runs the whole flow at zero cost.

Try it locally:

```
pip install -r requirements.txt
cp .streamlit/secrets.toml.example .streamlit/secrets.toml   # add key + password
streamlit run app.py
```

Deploy privately (only the team can see it), the least-setup path:

1. Push this folder to a **private** GitHub repo.
2. On share.streamlit.io, New app, point it at `scan-agents/app.py`.
3. In the app's Settings, set it to **private** and invite the team by email, so
   only those accounts can open it. Then add Secrets: `ANTHROPIC_API_KEY`,
   `APP_PASSWORD`, and optionally `OPENROUTER_API_KEY`.
4. Share the URL and the password with the invited team.

A Hugging Face **private Space** works the same way and is visible only to org
members. For SSO gating, put a small Render or Railway host behind Cloudflare
Access.

## Compare models (OpenRouter)

To run the same agents on other models and compare the scans, switch the
provider. In the app, open "Compare models (advanced)" in the sidebar, pick
OpenRouter, and enter a model id. From the CLI:

```
python -m scan run --stage 1 --provider openrouter --model openai/gpt-5
python -m scan run --stage 1 --provider openrouter --model google/gemini-2.5-pro
```

Anthropic (Opus 4.8) stays the default and keeps native web search and prompt
caching; the OpenRouter path uses OpenRouter's web plugin for the browsing
stages. Set `OPENROUTER_API_KEY` in `.env` or the host secrets.

## The org sheet

`input/organizations.xlsx`, one row per organization. Columns: `id`, `name`,
`type`, `region`, `status`. Only `name` is required.

## The brain

Everything client-specific is in `context/`. Edit these, not the code.

| File | Controls |
|---|---|
| `mission.md` | the two lenses, DEPTH, value capture, the over-the-horizon definition |
| `scope.md` | standing rules and the existing-programs exclusion list |
| `scoring.md` | the four criteria, the marks, the posture rules |
| `themes.md` | how themes are tagged and kept tight |
| `output_spec.md` | row and scorecard schema, and the house style |
| `policy.md` | the non-negotiables the guardrail enforces |

## How it works

Six agents: Scout (search one org), Reader (extract one approach), Scorer (four
marks), Verifier (adversarial primary-source check), Themer, Synthesizer. Every
stage runs on Opus 4.8 by default; set `MODEL_SONNET` and `MODEL_HAIKU` in
`.env` to downgrade the parallel legs and the mechanical step and save cost. The
frozen per-stage frame is cached across every org leg, each leg sees only its
one organization, and one guardrail enforces primary-source-only and no tool
trace over every output.
