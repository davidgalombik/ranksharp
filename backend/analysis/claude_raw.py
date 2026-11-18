"""Raw Messages API client for calls that use server tools (web search,
web fetch).

Why not the SDK: the pinned anthropic SDK (0.40.0) predates the web
search / fetch result block types, and upgrading it touches every Claude
call on the platform. The recommender's tool calls are short, single-use
requests (one search or one fetch each), so a plain non-streaming POST
with httpx is enough — the same transport the Phase 0 spike used.

Every call is recorded in the cost ledger (analysis/llm_usage.py) with
the feature and reference given, including failures.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from typing import Any, Optional

import httpx
import structlog

from analysis.llm_usage import record_call_async
from config import settings

log = structlog.get_logger()

_API = "https://api.anthropic.com/v1/messages"
_RETRY_STATUSES = {429, 500, 502, 503, 529}


class ClaudeRawError(RuntimeError):
    def __init__(self, status: int, body: str):
        super().__init__(f"Messages API HTTP {status}: {body[:300]}")
        self.status = status
        self.body = body


async def messages_create(
    body: dict,
    *,
    feature: str,
    ref_type: Optional[str] = None,
    ref_id: Optional[int] = None,
    timeout: float = 240.0,
    retries: int = 2,
) -> dict:
    """POST /v1/messages (non-streaming) and return the parsed response.
    Retries 429/5xx with backoff. Records the call in the ledger."""
    headers = {
        "x-api-key": settings.anthropic_api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    model = body.get("model") or "?"
    t0 = time.perf_counter()
    last_exc: Optional[Exception] = None
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=30.0)) as client:
        for attempt in range(retries + 1):
            try:
                r = await client.post(_API, headers=headers, json=body)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                if attempt < retries:
                    await asyncio.sleep(2 * (attempt + 1))
                    continue
                break
            if r.status_code == 200:
                data = r.json()
                await record_call_async(
                    feature=feature, model=model, usage=data.get("usage"),
                    ref_type=ref_type, ref_id=ref_id,
                    duration_ms=int((time.perf_counter() - t0) * 1000), status="ok",
                )
                return data
            last_exc = ClaudeRawError(r.status_code, r.text)
            if r.status_code in _RETRY_STATUSES and attempt < retries:
                await asyncio.sleep(3 * (attempt + 1))
                continue
            break
    await record_call_async(
        feature=feature, model=model, usage=None, ref_type=ref_type, ref_id=ref_id,
        duration_ms=int((time.perf_counter() - t0) * 1000), status="error", error=str(last_exc),
    )
    raise last_exc  # type: ignore[misc]


# ── Response helpers ─────────────────────────────────────────────────────────

def response_text(data: dict) -> str:
    """The last text block's text ('' if none). Thinking blocks are skipped."""
    for block in reversed(data.get("content") or []):
        if block.get("type") == "text" and block.get("text"):
            return block["text"].strip()
    return ""


def strip_fences(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        inner = lines[1:-1] if lines[-1].strip() == "```" else lines[1:]
        return "\n".join(inner).strip()
    return text


def parse_json(text: str) -> Any:
    """Tolerant JSON parse: strips fences and falls back to the first
    top-level {...} or [...] in prose."""
    text = strip_fences(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    values = _json_values(text)
    if not values:
        raise ValueError("no JSON in response")
    # Several top-level values (2026-11-18): a model sometimes writes one
    # array per theme, or one record per line, instead of a single array —
    # the old greedy regex then failed with "Extra data". Arrays are joined;
    # records that share the same keys become a list; otherwise the first
    # value wins and trailing prose or examples are ignored.
    if len(values) > 1:
        if all(isinstance(v, list) for v in values):
            return [x for v in values for x in v]
        if all(isinstance(v, dict) for v in values) and len({frozenset(v) for v in values}) == 1:
            return values
    return values[0]


def _json_values(text: str) -> list:
    """Every top-level JSON array or object in `text`, in order, skipping
    the prose between them."""
    decoder = json.JSONDecoder()
    out: list = []
    i = 0
    while True:
        starts = [p for p in (text.find("[", i), text.find("{", i)) if p != -1]
        if not starts:
            return out
        start = min(starts)
        try:
            value, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            i = start + 1        # a stray bracket in prose — keep looking
            continue
        out.append(value)
        i = end


def search_results(data: dict) -> list[dict]:
    """Every web search result the server returned, in order:
    [{url, title, page_age}]. Independent of what the model wrote."""
    out: list[dict] = []
    for block in data.get("content") or []:
        if block.get("type") != "web_search_tool_result":
            continue
        content = block.get("content")
        if not isinstance(content, list):
            continue
        for item in content:
            if item.get("type") == "web_search_result" and item.get("url"):
                out.append({"url": item["url"], "title": item.get("title") or "", "page_age": item.get("page_age")})
    return out


def fetch_results(data: dict) -> list[dict]:
    """Every web fetch outcome: [{url, ok, error_code, text}]. `text` is the
    page as the tool returned it (meta tags first, then the body text)."""
    out: list[dict] = []
    for block in data.get("content") or []:
        if block.get("type") != "web_fetch_tool_result":
            continue
        content = block.get("content") or {}
        if content.get("type") == "web_fetch_result":
            doc = content.get("content") or {}
            src = doc.get("source") or {}
            out.append({"url": content.get("url"), "ok": True, "error_code": None,
                        "text": src.get("data") or "" if src.get("type") == "text" else ""})
        else:
            out.append({"url": None, "ok": False, "error_code": content.get("error_code") or content.get("type"),
                        "text": ""})
    return out


_META_RE = re.compile(r"^meta-(og:image|og:title|og:price:amount|og:price:currency|og:site_name|og:availability|product:price:amount|product:price:currency|product:availability)\s*:\s*(.+)$", re.M)


def page_meta(text: str) -> dict:
    """og:/product: meta tags from a fetched page's text header."""
    meta: dict = {}
    for key, val in _META_RE.findall(text[:6000]):
        meta.setdefault(key, val.strip())
    return meta
