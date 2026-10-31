"""ALDI screenshot inventory and session trend digest (Phase 2, 2026-11-01).

Why: the web recommender (Phase 3) must say which screenshot element each
pick echoes, and the UI draws that element's box on the screenshot. The
existing mood-board analysis extracts session-wide themes, colours and
materials but records nothing per screenshot. So:

  * inventory_upload_bytes()  — one vision call per upload: every distinct
    image, swatch strip, text panel or table row on the screenshot, with a
    normalised bounding box, a description and the materials / colours seen.
    Stored on aldi_uploads.elements.

  * build_trend_digest()      — one text-only call per session over all the
    inventories plus the per-upload analyses: the themes as the report names
    them (T1, T2, …), each with a description and the elements that show it.
    Stored on aldi_sessions.trend_digest, built once, reused by both teams
    and every Try Again.

Both are additive: a failure leaves the existing flow untouched.
"""
from __future__ import annotations

import base64
import io
import json
from datetime import datetime
from typing import Optional

import structlog
from anthropic import AsyncAnthropic

from analysis.catalogue_vision import _coerce_bbox, _strip_fences
from analysis.llm_usage import tracked_create
from config import settings

log = structlog.get_logger()

ELEMENT_KINDS = {"image", "swatch", "text", "table_row"}

INVENTORY_PROMPT = """You are cataloguing ONE page of a retail trend report for a home and general merchandise buying team. The page is a screenshot: it may be a mood board of product photos with theme labels, a colour/material swatch strip, a text panel, or a table.

List EVERY distinct element on the page. An element is one of:
- image: one product photo or styled photo (if a photo is a collage of several products, list it once and name the products in the description)
- swatch: a colour or material swatch strip or Pantone chip row (list the strip once; name each colour/material in the description)
- text: a heading, label, callout or paragraph (theme names like "SASSY DINER", "TREND APPLIES TO: …", retailer credits like "ANTHROPOLOGIE")
- table_row: one row of a table (put the row's cells in the description)

For each element return:
- id: "E1", "E2", … in reading order (left to right, top to bottom)
- kind: image | swatch | text | table_row
- bbox_norm: [x, y, w, h], each 0.0–1.0 of the page, origin top-left, fitted tightly to the element
- description: one or two specific sentences a buyer could match a product against — name the product types, shapes, finishes, prints, colours, materials, and any visible brand or retailer label. For text elements quote the text.
- product_types: list of product types visible (e.g. ["latte bowl", "French press"]) — empty for text/swatch
- materials: materials visible or named (e.g. ["glazed stoneware", "ribbed coloured glass"]) — empty if none
- colours: 1–5 dominant colours as plain names (e.g. ["chocolate brown", "blush pink", "cream"])
- theme_label: the theme name this element sits under or next to, exactly as written on the page (e.g. "SASSY DINER"), or null if none applies

Return ONLY a JSON array of element objects, no prose, no markdown fences. Be exhaustive about content — every photo, every swatch strip, every label, every filled table row — but skip page furniture: empty table rows, logos, page numbers, navigation buttons and decorative shapes."""


DIGEST_PROMPT = """You are a senior buying manager reading a retail trend report that was uploaded as {page_count} page screenshot(s). You have, for each page, a machine inventory of its elements (E1, E2, …, each with a bounding box, a description, materials and colours) and an earlier extraction of the page's themes, palette and materials.

Identify the report's key trend themes AS THE REPORT NAMES THEM (use the theme labels written on the pages when present; otherwise name the theme from what the images show). For each theme give:
- id: "T1", "T2", …
- name: the theme name as written on the page where possible
- description: 2–4 sentences on its aesthetic — colours, materials, finishes, prints, forms and functions — specific enough to search for products against
- pages: the page numbers it appears on
- element_refs: the elements that show it, as objects {{"upload_id": <int>, "element_id": "E3"}} — cite ONLY ids that appear in the inventories below; cite the images first, then swatches and text
- colours: 2–6 colour names
- materials: the materials this theme is expressed in
- product_types: product types shown under this theme

Also return:
- season_occasion: the season or occasion the report targets, or null
- applies_to: categories or departments the report says the trend applies to (e.g. "kitchen", "stationery"), or []
- notes: one or two sentences on anything a buyer should know (on-shelf dates, markets, exclusions), or null

Return ONLY a JSON object: {{"themes": [...], "season_occasion": ..., "applies_to": [...], "notes": ...}}. No prose, no markdown fences. 3–8 themes. Do not invent pages, elements or themes that are not in the inventories.

{pages_block}"""


class AldiInventory:
    def __init__(self):
        self.client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self.model = settings.aldi_inventory_model

    async def aclose(self):
        try:
            await self.client.close()
        except Exception:  # noqa: BLE001
            pass

    async def inventory_upload_bytes(self, data: bytes, file_type: str, *, ref_id: Optional[int] = None) -> list[dict]:
        """Elements for one uploaded page. Empty list on any failure (logged)."""
        blocks = _image_blocks(data, file_type)
        if not blocks:
            log.warning("aldi_inventory_no_image", ref_id=ref_id, file_type=file_type)
            return []
        raw = ""
        try:
            response = await tracked_create(
                self.client, "aldi_inventory", ref_type="aldi_upload", ref_id=ref_id,
                model=self.model,
                # Adaptive thinking (on by default for Sonnet 5.5) spends
                # from this same budget; 6000 truncated a 21-element page.
                max_tokens=16000,
                messages=[{"role": "user", "content": blocks + [{"type": "text", "text": INVENTORY_PROMPT}]}],
            )
            _warn_if_truncated(response, "aldi_inventory", ref_id)
            raw = _strip_fences(_response_text(response))
            parsed = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log.error("aldi_inventory_failed", ref_id=ref_id, error=str(exc), raw_preview=raw[:300])
            return []
        if not isinstance(parsed, list):
            log.warning("aldi_inventory_not_a_list", ref_id=ref_id)
            return []
        return sanitise_elements(parsed)

    async def build_trend_digest(self, pages: list[dict], *, ref_id: Optional[int] = None) -> Optional[dict]:
        """`pages` = [{upload_id, page, filename, analysis: {...}, elements: [...]}, …]
        in page order. Returns the digest dict or None on failure."""
        pages_block = "\n\n".join(_page_block(p) for p in pages)
        prompt = DIGEST_PROMPT.format(page_count=len(pages), pages_block=pages_block)
        raw = ""
        try:
            response = await tracked_create(
                self.client, "aldi_digest", ref_type="aldi_session", ref_id=ref_id,
                model=self.model,
                max_tokens=16000,   # thinking tokens come out of this too
                messages=[{"role": "user", "content": prompt}],
            )
            _warn_if_truncated(response, "aldi_digest", ref_id)
            raw = _strip_fences(_response_text(response))
            parsed = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            log.error("aldi_digest_failed", ref_id=ref_id, error=str(exc), raw_preview=raw[:300])
            return None
        if not isinstance(parsed, dict):
            return None
        return sanitise_digest(parsed, pages, model=self.model)


def _warn_if_truncated(response, feature: str, ref_id) -> None:
    if getattr(response, "stop_reason", None) == "max_tokens":
        usage = getattr(response, "usage", None)
        log.error(f"{feature}_truncated", ref_id=ref_id,
                  output_tokens=getattr(usage, "output_tokens", None),
                  hint="JSON cut off at max_tokens — raise the limit")


def _response_text(response) -> str:
    """The last text block's text. With adaptive thinking (Sonnet 5.5 /
    Opus 5.5) content[0] can be a thinking block whose .text is None, so
    never index the first block blindly — that cost page 1 of the first
    inventory run (2026-11-01)."""
    for block in reversed(getattr(response, "content", None) or []):
        if getattr(block, "type", None) == "text" and getattr(block, "text", None):
            return block.text.strip()
    return ""


# ── Sanitisers (pure; unit-testable) ─────────────────────────────────────────

def sanitise_elements(items: list) -> list[dict]:
    out: list[dict] = []
    n = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        desc = (item.get("description") or "").strip()
        if not desc:
            continue
        n += 1
        kind = (item.get("kind") or "image").strip().lower()
        if kind not in ELEMENT_KINDS:
            kind = "image"
        out.append({
            "id": f"E{n}",                       # renumber: ids must be dense and unique
            "kind": kind,
            "bbox": _coerce_bbox(item.get("bbox_norm") or item.get("bbox")),
            "description": desc[:600],
            "product_types": _str_list(item.get("product_types"), 12),
            "materials": _str_list(item.get("materials"), 8),
            "colours": _str_list(item.get("colours"), 6),
            "theme_label": (str(item.get("theme_label")).strip()[:80] if item.get("theme_label") else None),
        })
    return out


def sanitise_digest(parsed: dict, pages: list[dict], *, model: str) -> dict:
    valid: dict[int, set[str]] = {
        int(p["upload_id"]): {e["id"] for e in (p.get("elements") or [])} for p in pages
    }
    page_of = {int(p["upload_id"]): int(p["page"]) for p in pages}
    themes: list[dict] = []
    for i, t in enumerate(parsed.get("themes") or [], 1):
        if not isinstance(t, dict):
            continue
        name = (t.get("name") or "").strip()
        if not name:
            continue
        refs: list[dict] = []
        seen: set[tuple[int, str]] = set()
        for r in t.get("element_refs") or []:
            try:
                uid = int(r.get("upload_id"))
                eid = str(r.get("element_id") or "").strip().upper()
            except Exception:  # noqa: BLE001
                continue
            if eid in valid.get(uid, set()) and (uid, eid) not in seen:
                seen.add((uid, eid))
                refs.append({"upload_id": uid, "element_id": eid, "page": page_of.get(uid)})
        pages_cited = sorted({r["page"] for r in refs if r.get("page")} | {
            int(p) for p in (t.get("pages") or []) if str(p).isdigit() and int(p) in page_of.values()
        })
        themes.append({
            "id": f"T{i}",
            "name": name[:120],
            "description": (t.get("description") or "").strip()[:1200],
            "pages": pages_cited,
            "element_refs": refs,
            "colours": _str_list(t.get("colours"), 8),
            "materials": _str_list(t.get("materials"), 10),
            "product_types": _str_list(t.get("product_types"), 15),
        })
    return {
        "themes": themes,
        "season_occasion": (str(parsed.get("season_occasion")).strip()[:120] if parsed.get("season_occasion") else None),
        "applies_to": _str_list(parsed.get("applies_to"), 10),
        "notes": (str(parsed.get("notes")).strip()[:600] if parsed.get("notes") else None),
        "pages": [{"upload_id": int(p["upload_id"]), "page": int(p["page"]), "filename": p.get("filename")} for p in pages],
        "built_at": datetime.utcnow().isoformat(timespec="seconds"),
        "model": model,
        "version": 1,
    }


def _page_block(p: dict) -> str:
    a = p.get("analysis") or {}
    lines = [
        f"=== PAGE {p['page']} (upload_id {p['upload_id']}, file: {p.get('filename') or '?'}) ===",
        f"Earlier extraction — themes: {a.get('themes') or []}; colours: {a.get('colour_palette') or []}; "
        f"materials: {a.get('key_materials') or []}; prints: {a.get('key_prints') or []}; "
        f"product categories: {a.get('product_categories') or []}; season: {a.get('season_occasion')}",
        "Elements:",
    ]
    for e in p.get("elements") or []:
        bits = [f"{e['id']} [{e['kind']}]"]
        if e.get("theme_label"):
            bits.append(f"under '{e['theme_label']}'")
        bits.append(e["description"])
        if e.get("product_types"):
            bits.append(f"products: {', '.join(e['product_types'])}")
        if e.get("materials"):
            bits.append(f"materials: {', '.join(e['materials'])}")
        if e.get("colours"):
            bits.append(f"colours: {', '.join(e['colours'])}")
        lines.append("  - " + " | ".join(bits))
    if not p.get("elements"):
        lines.append("  (no elements inventoried for this page)")
    return "\n".join(lines)


def _str_list(val, cap: int) -> list[str]:
    if not val:
        return []
    if isinstance(val, str):
        val = [val]
    if not isinstance(val, list):
        return []
    return [str(v).strip()[:80] for v in val if v and str(v).strip()][:cap]


def _image_blocks(data: bytes, file_type: str) -> list[dict]:
    """Screenshot → one downsized JPEG block (PDFs: first 2 pages)."""
    ft = (file_type or "").lower().lstrip(".")
    try:
        if ft == "pdf":
            from pdf2image import convert_from_bytes
            pages = convert_from_bytes(data, first_page=1, last_page=2, dpi=150)
            blocks = []
            for img in pages:
                buf = io.BytesIO()
                img.save(buf, format="JPEG", quality=85)
                blocks.append(_b64_block(buf.getvalue()))
            return blocks
        from analysis.catalogue_payload import prepare_vision_payload
        jpg, _ = prepare_vision_payload(data, ft or "png")
        return [_b64_block(jpg)]
    except Exception as exc:  # noqa: BLE001
        log.warning("aldi_inventory_image_failed", error=str(exc))
        return []


def _b64_block(jpg: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                        "data": base64.standard_b64encode(jpg).decode("utf-8")}}
