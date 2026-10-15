"""Thin wrapper around the Claude API: one shared client, usage capture and cost.

Adapted from Margin Guard's llm.py. Adds cache-aware usage (multi-turn calls re-send
the whole conversation, so cache reads are most of the input), a tool-use create(),
and an optional batch transport (agentgate/batch.py) that the same calls route through.
"""

import time
from typing import TypeVar

import anthropic
from anthropic.lib._parse._transform import transform_schema   # what messages.parse uses
from pydantic import BaseModel, TypeAdapter

T = TypeVar("T", bound=BaseModel)

# $ per million tokens: (input, output). Cache reads bill at 0.1x input, cache writes at
# 1.25x (5-minute TTL) or 2x (1-hour TTL); the Batch API halves everything.
PRICES = {
    "claude-sonnet-5-5": (2.00, 10.00),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-opus-5-5": (4.00, 20.00),
}
BATCH_DISCOUNT = 0.5

_client: anthropic.Anthropic | None = None
_transport = None          # agentgate.batch.BatchTransport while a batch run is active


class LLMRefusal(RuntimeError):
    pass


def client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(max_retries=4)
    return _client


def use_transport(transport) -> None:
    """Route create()/parse() through a batch transport (None = live calls)."""
    global _transport
    _transport = transport


def fallback_args(model: str, enabled: bool) -> dict:
    """Server-side refusal fallback ("default" routing). Haiku has none; batches can't carry it."""
    if not enabled or model.startswith("claude-haiku") or _transport is not None:
        return {}
    return {"extra_headers": {"anthropic-beta": "server-side-fallback-2026-07-01"},
            "extra_body": {"fallbacks": "default"}}


def usage_entry(model: str, usage, started: float, role: str) -> dict:
    created = getattr(usage, "cache_creation", None)
    return {
        "role": role,
        "model": model,
        "mode": "batch" if _transport is not None else "live",
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
        "cache_write": getattr(usage, "cache_creation_input_tokens", 0) or 0,
        "cache_write_1h": (getattr(created, "ephemeral_1h_input_tokens", 0) or 0) if created else 0,
        "seconds": round(time.perf_counter() - started, 2),
    }


def parse(system: str | list, user: str, schema: type[T], *, model: str, effort: str = "low",
          max_tokens: int = 4000, role: str = "", log: list | None = None) -> T:
    """One call whose response is validated against `schema` (simulator, judge)."""
    started = time.perf_counter()
    base = {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": user}]}
    if _transport is None:
        response = client().messages.parse(**base, output_format=schema, output_config={"effort": effort})
        parsed = response.parsed_output
    else:
        fmt = {"type": "json_schema", "schema": transform_schema(TypeAdapter(schema).json_schema())}
        response = _transport.send({**base, "output_config": {"effort": effort, "format": fmt}})
        text = next((b.text for b in response.content if b.type == "text"), None)
        parsed = schema.model_validate_json(text) if text and response.stop_reason != "refusal" else None
    if log is not None:
        log.append(usage_entry(model, response.usage, started, role))
    if response.stop_reason == "refusal":
        raise LLMRefusal(f"model declined: {getattr(response, 'stop_details', None)}")
    if parsed is None:
        raise ValueError(f"no structured output (stop_reason={response.stop_reason})")
    return parsed


def create(*, model: str, system: list, messages: list, tools: list, effort: str, max_tokens: int = 4000,
           fallbacks: bool = False, role: str = "", log: list | None = None):
    """One tool-use turn. Top-level cache_control caches the conversation so far, so each
    turn of a call reads the previous turns from cache and writes only the new ones."""
    started = time.perf_counter()
    params = {"model": model, "max_tokens": max_tokens, "system": system, "messages": messages, "tools": tools,
              "output_config": {"effort": effort}, "cache_control": {"type": "ephemeral"}}
    if _transport is None:
        response = client().messages.create(**params, **fallback_args(model, fallbacks))
    else:
        response = _transport.send(params)
    if log is not None:
        log.append(usage_entry(model, response.usage, started, role))
    return response


def cost_usd(entries: list[dict]) -> float:
    total = 0.0
    for e in entries:
        p_in, p_out = PRICES.get(e["model"], (0.0, 0.0))
        w1h = e.get("cache_write_1h", 0)
        w5m = e.get("cache_write", 0) - w1h
        cost = (e["input_tokens"] * p_in + e["output_tokens"] * p_out + e.get("cache_read", 0) * p_in * 0.1
                + w5m * p_in * 1.25 + w1h * p_in * 2.0) / 1e6
        total += cost * (BATCH_DISCOUNT if e.get("mode") == "batch" else 1.0)
    return total
