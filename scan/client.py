"""Claude wrapper: cached system frame, tool loop, structured output.

One entry point, `structured_call`, used by every stage. It forces a single
`record` tool call and returns its validated input dict. When `web=True` it
also hands the model Anthropic's server-side web_search tool and runs the
short pause_turn / search loop until the model records its answer.

Best-practice choices baked in:
  - prompt caching: the frozen per-stage frame sits in a cache_control system
    block, so every org leg reuses it at ~0.1x input cost (Anthropic CE docs).
  - structured output via a strict `record` tool schema (universal, portable).
  - stop_reason handled explicitly: pause_turn, tool_use, end_turn, max_tokens.

In dry-run (config.DRY_RUN) the call is short-circuited to scan.mock, so the
whole pipeline flows with no key and no network.
"""
from __future__ import annotations

import json
from typing import Any

from anthropic import AsyncAnthropic

from . import config, mock

_client: AsyncAnthropic | None = None
_or_client: Any = None

# running token account for the process, so a run's spend is visible instead of blind
USAGE = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0, "calls": 0}


class TruncatedOutput(RuntimeError):
    """The model hit its output cap before it finished writing.

    Raised on BOTH providers, so the caller has one retry path rather than two. The
    OpenRouter path used to surface this as a JSONDecodeError from a half-written
    tool-call argument, which killed stage two instead of retrying with headroom.
    """

    def __init__(self, model: str, max_tokens: int) -> None:
        super().__init__(f"{model}: hit the {max_tokens}-token output cap before finishing")
        self.model = model
        self.max_tokens = max_tokens


# per-model account, so spend can be priced rather than only counted
BY_MODEL: dict[str, dict[str, int]] = {}


def _account(resp: Any, model: str = "") -> None:
    u = getattr(resp, "usage", None)
    if not u:
        return
    # Anthropic fields; OpenRouter (OpenAI shape) uses prompt_/completion_tokens
    got = {
        "calls": 1,
        "input": getattr(u, "input_tokens", None) or getattr(u, "prompt_tokens", 0) or 0,
        "output": getattr(u, "output_tokens", None) or getattr(u, "completion_tokens", 0) or 0,
        "cache_read": getattr(u, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(u, "cache_creation_input_tokens", 0) or 0,
    }
    for k, v in got.items():
        USAGE[k] += v
    per = BY_MODEL.setdefault(model or "unknown", dict.fromkeys(got, 0))
    for k, v in got.items():
        per[k] += v


def cost_usd() -> tuple[float, list[str]]:
    """(priced spend in dollars, the models with no price on file). Reported per
    model, because a run mixes a cheap search model with an expensive writing one and
    a single blended rate would be fiction. Anything unpriced is named rather than
    guessed at."""
    total, unpriced = 0.0, []
    for model, u in BY_MODEL.items():
        price = config.PRICES.get(model)
        if not price:
            unpriced.append(model)
            continue
        pin, pout = price
        billed_in = max(0, u["input"] - u["cache_read"] - u["cache_write"])
        total += (billed_in * pin
                  + u["cache_read"] * pin * config.CACHE_READ_MULT
                  + u["cache_write"] * pin * config.CACHE_WRITE_MULT
                  + u["output"] * pout) / 1_000_000
    return round(total, 4), sorted(unpriced)


def usage_line() -> str:
    line = (f"{USAGE['calls']} model calls, {USAGE['input']:,} input tokens "
            f"({USAGE['cache_read']:,} from cache, {USAGE['cache_write']:,} written to cache), "
            f"{USAGE['output']:,} output tokens")
    priced, unpriced = cost_usd()
    if priced:
        line += f", about ${priced:,.2f}"
    if unpriced:
        line += f" (no price on file for {', '.join(unpriced)}, so its cost is not counted)"
    return line


def client() -> AsyncAnthropic:
    global _client
    if _client is None:
        config.require_key()
        _client = AsyncAnthropic(
            api_key=config.API_KEY,
            max_retries=config.MAX_RETRIES,
            timeout=config.REQUEST_TIMEOUT,
        )
    return _client


def or_client() -> Any:
    """OpenAI-compatible client pointed at OpenRouter, built lazily."""
    global _or_client
    if _or_client is None:
        from openai import AsyncOpenAI  # lazy: only needed for the openrouter path

        config.require_key()
        _or_client = AsyncOpenAI(
            base_url=config.OPENROUTER_BASE_URL,
            api_key=config.OPENROUTER_API_KEY,
            default_headers={"HTTP-Referer": config.OR_REFERER, "X-Title": config.OR_TITLE},
            max_retries=config.MAX_RETRIES,
            timeout=config.REQUEST_TIMEOUT,
        )
    return _or_client


async def _openrouter_call(
    frame: str, user: str, schema: dict[str, Any], web: bool, max_tokens: int,
    or_model: str | None = None,
) -> dict[str, Any]:
    """Run one stage on OpenRouter. Structured output via a forced `record`
    function; web via OpenRouter's web plugin so browsing stages still work."""
    tool = {
        "type": "function",
        "function": {
            "name": "record",
            "description": "Report your final structured result. Call this exactly once.",
            "parameters": schema,
        },
    }
    body: dict[str, Any] = {}
    if web:
        body["plugins"] = [{"id": "web", "max_results": config.WEB_MAX_USES}]
    resp = await or_client().chat.completions.create(
        model=or_model or config.OR_MODEL,
        max_tokens=max_tokens,
        temperature=config.TEMPERATURE,
        messages=[
            {"role": "system", "content": frame},
            {"role": "user", "content": user},
        ],
        tools=[tool],
        tool_choice={"type": "function", "function": {"name": "record"}},
        extra_body=body or None,
    )
    _account(resp, or_model or config.OR_MODEL)
    name = or_model or config.OR_MODEL
    if not getattr(resp, "choices", None):
        raise RuntimeError(f"openrouter {name}: empty response (no choices)")
    choice = resp.choices[0]
    # the model ran out of room mid-write: tell the caller so, rather than letting a
    # half-written tool-call argument surface as an opaque JSON error
    if getattr(choice, "finish_reason", "") == "length":
        raise TruncatedOutput(f"openrouter {name}", max_tokens)
    msg = choice.message
    if getattr(msg, "tool_calls", None):
        args = msg.tool_calls[0].function.arguments
        if args:
            try:
                return json.loads(args)
            except json.JSONDecodeError:
                # a truncated argument string is the usual cause, and it is recoverable
                raise TruncatedOutput(f"openrouter {name}", max_tokens) from None
    if getattr(msg, "content", None):
        try:
            return json.loads(msg.content)
        except Exception:
            pass
    raise RuntimeError(f"openrouter {name}: no valid structured output returned")


def record_tool(schema: dict[str, Any]) -> dict[str, Any]:
    """A client tool whose only job is to carry the structured result out."""
    return {
        "name": "record",
        "description": "Report your final structured result. Call this exactly once.",
        "input_schema": schema,
    }


def _web_tool() -> dict[str, Any]:
    return {
        "type": config.WEB_SEARCH_TYPE,
        "name": "web_search",
        "max_uses": config.WEB_MAX_USES,
    }


def _system_blocks(frame: str) -> list[dict[str, Any]]:
    # single cached block; the breakpoint caches tools + system as one prefix.
    return [{"type": "text", "text": frame, "cache_control": {"type": "ephemeral"}}]


def _find_record(content: list[Any]) -> dict[str, Any] | None:
    for block in content:
        if getattr(block, "type", None) == "tool_use" and block.name == "record":
            return dict(block.input)
    return None


async def structured_call(
    *,
    model: str,
    frame: str,
    user: str,
    schema: dict[str, Any],
    web: bool = False,
    max_tokens: int = 4096,
    effort: str | None = None,
    tier: str = "base",
) -> dict[str, Any]:
    """Run a stage and return the validated `record` input as a dict. `tier`
    is "strong" for the writing and judgment stages; on the OpenRouter path a
    strong non-web stage routes to OR_MODEL_STRONG so a better model writes the
    memo and scores while the cheap model keeps the web search."""
    if config.DRY_RUN:
        return mock.mock_response(schema, user)
    if config.PROVIDER == "openrouter":
        # auto-route: an Anthropic model runs best natively (iterative web_search +
        # caching); only fall back to OpenRouter's plugin when there is no Anthropic key.
        if config.anthropic_via_openrouter() and config.API_KEY:
            model = config.native_anthropic_id(config.OR_MODEL)
        else:
            or_model = config.OR_MODEL
            if tier == "strong" and not web and config.OR_MODEL_STRONG:
                or_model = config.OR_MODEL_STRONG    # a strong model for the writing stages
            return await _openrouter_call(frame, user, schema, web, max_tokens, or_model)

    rec = record_tool(schema)
    system = _system_blocks(frame)
    messages: list[dict[str, Any]] = [{"role": "user", "content": user}]

    if not web:
        # No browsing: force the record tool in a single call.
        resp = await client().messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=config.TEMPERATURE,
            system=system,
            messages=messages,
            tools=[rec],
            tool_choice={"type": "tool", "name": "record"},
        )
        _account(resp, model)
        out = _find_record(resp.content)
        if out is None:
            if resp.stop_reason == "max_tokens":
                raise TruncatedOutput(model, max_tokens)
            raise RuntimeError(f"{model}: model did not call record ({resp.stop_reason})")
        if resp.stop_reason == "max_tokens":
            out["_truncated"] = True     # the caller can retry with more headroom
        return out

    # Browsing: web_search (server tool) + record, auto choice, loop on pauses.
    tools = [_web_tool(), rec]
    for _ in range(config.MAX_TOOL_TURNS):
        resp = await client().messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=config.TEMPERATURE,
            system=system,
            messages=messages,
            tools=tools,
            tool_choice={"type": "auto"},
        )
        _account(resp, model)
        out = _find_record(resp.content)
        if out is not None:
            return out
        if resp.stop_reason in ("pause_turn", "tool_use"):
            # server tool ran (results already attached) — resume the turn.
            messages.append({"role": "assistant", "content": resp.content})
            continue
        # end_turn / max_tokens without a record: force one final record call.
        messages.append({"role": "assistant", "content": resp.content})
        messages.append(
            {"role": "user", "content": "Now call the record tool with your final result."}
        )
        forced = await client().messages.create(
            model=model,
            max_tokens=max_tokens,
            temperature=config.TEMPERATURE,
            system=system,
            messages=messages,
            tools=[rec],
            tool_choice={"type": "tool", "name": "record"},
        )
        _account(forced, model)
        out = _find_record(forced.content)
        if out is None:
            raise RuntimeError(f"{model}: could not force a record call")
        return out
    raise RuntimeError(f"{model}: exceeded {config.MAX_TOOL_TURNS} tool turns")
