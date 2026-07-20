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

# Anthropic server-side web search tool.
WEB_SEARCH_TYPE = os.environ.get("WEB_SEARCH_TYPE", "web_search_20250305")
WEB_MAX_USES = int(os.environ.get("WEB_MAX_USES", "6"))

# Provider: "anthropic" (native web_search + prompt caching) or "openrouter"
# (OpenAI-compatible, to run the same agents on other models and compare).
PROVIDER = os.environ.get("SCAN_PROVIDER", "anthropic")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
# When PROVIDER=openrouter this single model runs every stage, so you can A/B a
# whole scan on one model, then swap and compare. Set it, e.g. openai/gpt-5,
# google/gemini-2.5-pro, anthropic/claude-opus-4.1.
OR_MODEL = os.environ.get("OR_MODEL", "")
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


def load_context() -> dict[str, str]:
    """Read every context/*.md into a dict keyed by stem. In global scope, the
    mission is swapped for mission_global.md."""
    ctx: dict[str, str] = {}
    for name in CONTEXT_FILES:
        p = CONTEXT_DIR / f"{name}.md"
        ctx[name] = p.read_text(encoding="utf-8") if p.exists() else ""
    if SCAN_MODE == "global":
        gp = CONTEXT_DIR / "mission_global.md"
        if gp.exists():
            ctx["mission"] = gp.read_text(encoding="utf-8")
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
