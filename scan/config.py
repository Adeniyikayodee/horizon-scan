"""Runtime config: env, model tiers, paths, and the cached context frame.

Everything client-specific lives in context/*.md and input/organizations.xlsx.
Point those at a different institute and the same engine scans for them.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# --- API ---
API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

# Every stage runs on Opus 4.8 by default. To save cost, downgrade individual
# tiers via env (e.g. MODEL_SONNET=claude-sonnet-5 for the parallel legs,
# MODEL_HAIKU=claude-haiku-4-5-20251001 for the mechanical step).
MODEL_OPUS = os.environ.get("MODEL_OPUS", "claude-opus-4-8")
MODEL_SONNET = os.environ.get("MODEL_SONNET", MODEL_OPUS)
MODEL_HAIKU = os.environ.get("MODEL_HAIKU", MODEL_OPUS)

# Deterministic decoding: temperature 0 everywhere, so the model does not invent
# URLs, figures, or facts. This is a hard default; override only for experiments.
TEMPERATURE = float(os.environ.get("SCAN_TEMPERATURE", "0"))

# Anthropic server-side web search tool.
WEB_SEARCH_TYPE = os.environ.get("WEB_SEARCH_TYPE", "web_search_20250305")
WEB_MAX_USES = int(os.environ.get("WEB_MAX_USES", "6"))

# Reader proxy: a fetch-and-render service used ONLY as a fallback when a direct
# fetch is blocked or empty (bot-protected sites like afdb.org and unctad.org, and
# JavaScript-rendered pages). It returns clean readable text, so the Reader reads
# the real document instead of search snippets. Set to "" to disable.
READER_PROXY = os.environ.get("READER_PROXY", "https://r.jina.ai/")

# Provider: "anthropic" (native web_search + prompt caching) or "openrouter"
# (OpenAI-compatible, to run the same agents on other models and compare).
PROVIDER = os.environ.get("SCAN_PROVIDER", "anthropic")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
# When PROVIDER=openrouter this model runs the finding, reading, and web stages,
# where a cheap model is enough. Set it, e.g. openai/gpt-4o-mini.
OR_MODEL = os.environ.get("OR_MODEL", "")
# The stronger model for the writing and judgment stages (scoring, auditing,
# theming, and the memo), where prose quality and reasoning matter. It runs only on
# stages that do NOT use the web plugin, since Claude underperforms through it, so
# the cheap model keeps the search while this model writes. Empty means use OR_MODEL
# for everything.
OR_MODEL_STRONG = os.environ.get("OR_MODEL_STRONG", "anthropic/claude-sonnet-4")
OR_REFERER = os.environ.get("OR_REFERER", "https://acet-horizon-scan.local")
OR_TITLE = os.environ.get("OR_TITLE", "ACET Horizon Scan")

# Concurrency for the per-org fan-out.
MAX_CONCURRENCY = int(os.environ.get("MAX_CONCURRENCY", "8"))
MAX_TOOL_TURNS = int(os.environ.get("MAX_TOOL_TURNS", "8"))
REQUEST_TIMEOUT = float(os.environ.get("REQUEST_TIMEOUT", "300"))
MAX_RETRIES = int(os.environ.get("MAX_RETRIES", "4"))

# Dry run: mock every model call, no key or network needed. Set via env or CLI.
DRY_RUN = os.environ.get("SCAN_DRY_RUN", "").lower() in ("1", "true", "yes")

# Ground the verifier: fetch the source and check the confirming quote is in it.
GROUND_QUOTES = os.environ.get("GROUND_QUOTES", "1").lower() in ("1", "true", "yes")

# Scan scope: "africa" (default) or "global" (any region/actor, transfer-to-Africa noted).
SCAN_MODE = os.environ.get("SCAN_MODE", "africa")

# --- The recency window, ONE source of truth ---
# The enforcement in pipeline.py and the rule text in every agent frame both read
# these, so the window never drifts between what is said and what is enforced.
# Change it here (or via env) and it changes everywhere at once.
YEAR_MIN = int(os.environ.get("SCAN_YEAR_MIN", "2023"))
YEAR_MAX = int(os.environ.get("SCAN_YEAR_MAX", "2026"))


def window_years() -> list[int]:
    return list(range(YEAR_MIN, YEAR_MAX + 1))


def window_rule() -> str:
    """The canonical recency rule, injected verbatim into every agent's frame so
    all stages carry the identical hard rule. The years come only from here."""
    ys = ", ".join(str(y) for y in window_years())
    return (f"# Standing hard rule, applies to every stage\n\n"
            f"The recency window is {YEAR_MIN} to {YEAR_MAX} ({ys}), and it is a hard rule, not a "
            f"preference. Every report read and every source cited must be published or updated within "
            f"it, nothing before {YEAR_MIN}. Sweep the whole span, do not stop at {YEAR_MIN}, and actively "
            f"include the most recent {YEAR_MAX} and {YEAR_MAX - 1} work. Always record each source's "
            f"publication or update date, a source that cannot be dated to this window is set aside.")

# --- Paths ---
ROOT = Path(__file__).resolve().parent.parent
CONTEXT_DIR = ROOT / "context"
INPUT_DIR = ROOT / "input"
WORK_DIR = ROOT / "work"
ORGS_WORK = WORK_DIR / "orgs"
REVIEW_DIR = ROOT / "review"
OUT_DIR = ROOT / "out"
MANIFEST = WORK_DIR / "manifest.json"

ORG_SHEET = INPUT_DIR / "organizations.xlsx"

for d in (WORK_DIR, ORGS_WORK, REVIEW_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

CONTEXT_FILES = ["mission", "scope", "scoring", "themes", "output_spec", "policy"]


# The active Scan Spec (research question, lenses, criteria, context). The app
# sets this per session; None means use the default.
SPEC = None


def active_spec() -> dict:
    global SPEC
    if SPEC is None:
        from . import spec as _sm
        SPEC = dict(_sm.DEFAULT_SPEC)
    return SPEC


def load_context() -> dict[str, str]:
    """Build the agent frame. Mission, scope, and scoring come from the active
    Scan Spec (so criteria are editable); themes, output_spec, and policy stay
    as files. Global scope swaps in mission_global.md."""
    from . import spec as _sm
    sp = active_spec()
    ctx: dict[str, str] = {
        "mission": _sm.mission_text(sp),
        "scope": _sm.scope_text(sp),
        "scoring": _sm.scoring_text(sp),
    }
    if SCAN_MODE == "global":
        gp = CONTEXT_DIR / "mission_global.md"
        if gp.exists():
            ctx["mission"] = gp.read_text(encoding="utf-8")
    for name in ("themes", "output_spec", "policy", "exemplar"):
        p = CONTEXT_DIR / f"{name}.md"
        ctx[name] = p.read_text(encoding="utf-8") if p.exists() else ""
    return ctx


# Anthropic models run best on the native path (iterative web_search + caching).
# Map their OpenRouter slugs to native ids so a picked Anthropic model can route home.
_NATIVE_ANTHROPIC = {
    "anthropic/claude-opus-4.8": "claude-opus-4-8",
    "anthropic/claude-opus-4.8-fast": "claude-opus-4-8",
    "anthropic/claude-sonnet-5": "claude-sonnet-5",
    "anthropic/claude-haiku-4.5": "claude-haiku-4-5-20251001",
}


def native_anthropic_id(or_slug: str) -> str:
    if or_slug in _NATIVE_ANTHROPIC:
        return _NATIVE_ANTHROPIC[or_slug]
    name = or_slug.split("/", 1)[-1]
    return name.replace("-fast", "").replace("-latest", "").replace(".", "-")


def anthropic_via_openrouter() -> bool:
    """True when an Anthropic model was chosen on the OpenRouter provider."""
    return PROVIDER == "openrouter" and OR_MODEL.startswith("anthropic/")


def require_key() -> None:
    if DRY_RUN:
        return
    if PROVIDER == "openrouter":
        if not OPENROUTER_API_KEY:
            raise SystemExit("OPENROUTER_API_KEY is not set (provider is openrouter).")
        if not OR_MODEL:
            raise SystemExit("OR_MODEL is not set. Pick an OpenRouter model id, e.g. openai/gpt-5.")
        return
    if not API_KEY:
        raise SystemExit(
            "ANTHROPIC_API_KEY is not set. Copy .env.example to .env and add your key."
        )
