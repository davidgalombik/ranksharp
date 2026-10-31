"""Per-call Claude usage and cost ledger (2026-11-01).

Every Claude call in the catalogue, ALDI and in-store code goes through
`tracked_create()`, a drop-in for `client.messages.create(**kwargs)`. It
records tokens, server-tool counts (web search / fetch), duration and a
USD cost on the `llm_calls` table, together with the per-million rates it
was costed with, so a later price change never rewrites history. The
Costs page reads that table for the per-call list and the aggregates.

Not instrumented on purpose: product trends and fragrance trends (David's
standing instruction, 2026-10-29), and the Voyage embedding calls.

Rates live in config.py (`llm_rates`, `llm_web_search_rate_usd`).
"""
from __future__ import annotations

import asyncio
import functools
import time
from datetime import datetime
from typing import Any, Optional

import structlog

from config import settings

log = structlog.get_logger()


def rates_for(model: str) -> dict:
    """Per-MTok USD rates for a model id, with a `source` marker: exact,
    prefix (e.g. a dated snapshot of a known id), family (opus / sonnet /
    haiku fallback) or unknown (zero rates — the row still records tokens)."""
    table: dict = settings.llm_rates or {}
    if model in table:
        return {**table[model], "source": "exact"}
    for key, rates in table.items():
        if model.startswith(key):
            return {**rates, "source": "prefix"}
    family = next((f for f in ("opus", "sonnet", "haiku") if f in model), None)
    fallback = (settings.llm_rate_fallbacks or {}).get(family) if family else None
    if fallback:
        return {**fallback, "source": "family"}
    return {"input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0, "source": "unknown"}


def usage_dict(usage: Any) -> dict:
    """Normalise an SDK Usage object (any SDK version) or a raw dict."""
    if usage is None:
        return {}
    if isinstance(usage, dict):
        return usage
    if hasattr(usage, "model_dump"):
        try:
            return usage.model_dump()
        except Exception:  # noqa: BLE001
            pass
    try:
        return dict(vars(usage))
    except Exception:  # noqa: BLE001
        return {}


def compute_cost(model: str, usage: dict) -> tuple[float, dict]:
    """USD cost for one call and the rates used (stored on the row)."""
    rates = rates_for(model)
    it = usage.get("input_tokens") or 0
    ot = usage.get("output_tokens") or 0
    cr = usage.get("cache_read_input_tokens") or 0
    cw = usage.get("cache_creation_input_tokens") or 0
    server = usage.get("server_tool_use") or {}
    searches = server.get("web_search_requests") or 0
    per_search = float(settings.llm_web_search_rate_usd or 0.0)
    cost = (
        it * rates["input"] + ot * rates["output"]
        + cr * rates["cache_read"] + cw * rates["cache_write"]
    ) / 1_000_000 + searches * per_search
    return cost, {**rates, "web_search_per_request": per_search}


def record_call(
    *,
    feature: str,
    model: str,
    usage: Any = None,
    ref_type: Optional[str] = None,
    ref_id: Optional[int] = None,
    duration_ms: Optional[int] = None,
    status: str = "ok",
    error: Optional[str] = None,
) -> Optional[float]:
    """Insert one llm_calls row through the sync engine. Never raises —
    a ledger failure must not fail the call it describes. Returns the cost."""
    try:
        from sqlalchemy import insert
        from database.db import sync_engine
        from database.models import LlmCall

        u = usage_dict(usage)
        cost, rates = compute_cost(model, u)
        server = u.get("server_tool_use") or {}
        with sync_engine.begin() as conn:
            conn.execute(insert(LlmCall).values(
                created_at=datetime.utcnow(),
                feature=feature[:60],
                model=(model or "?")[:80],
                ref_type=ref_type[:40] if ref_type else None,
                ref_id=ref_id,
                input_tokens=int(u.get("input_tokens") or 0),
                output_tokens=int(u.get("output_tokens") or 0),
                cache_read_tokens=int(u.get("cache_read_input_tokens") or 0),
                cache_write_tokens=int(u.get("cache_creation_input_tokens") or 0),
                web_searches=int(server.get("web_search_requests") or 0),
                web_fetches=int(server.get("web_fetch_requests") or 0),
                duration_ms=duration_ms,
                status=status[:10],
                error=str(error)[:500] if error else None,
                cost_usd=round(cost, 6),
                rates=rates,
            ))
        return cost
    except Exception as exc:  # noqa: BLE001
        log.warning("llm_usage_record_failed", feature=feature, error=str(exc))
        return None


async def _record_off_loop(**kwargs) -> None:
    """The ledger insert is a sync Postgres round trip; keep it off the
    event loop (API) — and the worker's asyncio.run() loops are fine with
    the default executor too."""
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, functools.partial(record_call, **kwargs))


async def tracked_create(
    client: Any,
    feature: str,
    *,
    ref_type: Optional[str] = None,
    ref_id: Optional[int] = None,
    **kwargs,
):
    """`await client.messages.create(**kwargs)` plus a ledger row.

    `feature` names the caller (catalogue_vision, aldi_cluster, …);
    `ref_type` / `ref_id` point at the row the call was for (an image, an
    upload, a session) so the Costs page can link back to it. A failed call
    is recorded with status=error and zero tokens, then re-raised.
    """
    model = kwargs.get("model") or "?"
    t0 = time.perf_counter()
    try:
        response = await client.messages.create(**kwargs)
    except Exception as exc:
        await _record_off_loop(
            feature=feature, model=model, usage=None, ref_type=ref_type, ref_id=ref_id,
            duration_ms=int((time.perf_counter() - t0) * 1000), status="error", error=str(exc),
        )
        raise
    await _record_off_loop(
        feature=feature, model=model, usage=getattr(response, "usage", None),
        ref_type=ref_type, ref_id=ref_id,
        duration_ms=int((time.perf_counter() - t0) * 1000), status="ok",
    )
    return response
