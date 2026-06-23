"""
Embedding module.

Calls Voyage AI's voyage-3 model to produce 1024-dim semantic embeddings for
products and in-store catalogue items. Pairs naturally with Claude (Anthropic
recommends Voyage). Falls back to None if no key is configured — callers must
handle the None case (downstream similarity queries already filter out NULL
embeddings).

Cost: voyage-3 is $0.06 per 1M input tokens. At ~100 tokens per product, a
full backfill of 200k items costs ~$1.20.
"""
import numpy as np
import structlog
from typing import Optional

from config import settings

log = structlog.get_logger()

EMBEDDING_DIM = settings.voyage_embedding_dim  # 1024 for voyage-3

# Voyage batch limit — empirically the API accepts up to 128 docs per call.
VOYAGE_BATCH_SIZE = 128


def _coerce_str(v) -> str:
    """Normalise a value that *might* be a list into a single string.

    Claude Vision occasionally returns list values for scalar-typed fields
    (e.g. multi-finish products returning ['woodgrain', 'matte']). Without
    this, string concatenation in _build_embedding_text raises TypeError
    and sends the task into a retry loop that burns API credits.
    """
    if v is None:
        return ""
    if isinstance(v, (list, tuple)):
        return ", ".join(str(x).strip() for x in v if x)
    return str(v).strip()


def _coerce_list(v) -> list[str]:
    """Normalise a value that *might* be a single string into a list of strings."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x).strip() for x in v if x]
    s = str(v).strip()
    return [s] if s else []


def _build_embedding_text(
    name: str,
    description: str,
    vision_attrs: Optional[dict],
    nlp_attrs: Optional[dict],
) -> str:
    """Construct a rich text representation of the product for embedding."""
    parts = [name or ""]

    if vision_attrs:
        parts.append("Colours: " + ", ".join(_coerce_list(vision_attrs.get("colours"))))
        parts.append("Style: " + ", ".join(_coerce_list(vision_attrs.get("style_tags"))))
        parts.append("Shape: " + _coerce_str(vision_attrs.get("shape")))
        parts.append("Finish: " + _coerce_str(vision_attrs.get("finish")))

    if nlp_attrs:
        parts.append("Materials: " + ", ".join(_coerce_list(nlp_attrs.get("materials"))))
        parts.append("Patterns: " + ", ".join(_coerce_list(nlp_attrs.get("patterns"))))
        parts.append("Function: " + ", ".join(_coerce_list(nlp_attrs.get("function_tags"))))
        fragrance = _coerce_str(nlp_attrs.get("fragrance"))
        if fragrance:
            parts.append("Fragrance: " + fragrance)
        parts.append("Room: " + _coerce_str(nlp_attrs.get("room")))

    if description:
        parts.append(description[:500])

    return " | ".join(p for p in parts if p.strip())


def _voyage_client_sync():
    """Module-level sync Voyage client. Lazy-initialised so that import-time
    failures (e.g. no API key configured locally) don't break test runs."""
    import voyageai
    return voyageai.Client(api_key=settings.voyage_api_key)


def _voyage_client_async():
    """Module-level async Voyage client (for use inside the analyse pipeline)."""
    import voyageai
    return voyageai.AsyncClient(api_key=settings.voyage_api_key)


def embed_text_sync(text: str) -> Optional[list[float]]:
    """Sync single-text embedding. Used by Celery tasks (catalogue + backfill).
    Returns None when no API key is configured or the call fails."""
    if not settings.voyage_api_key:
        log.warning("voyage_key_missing")
        return None
    text = (text or "").strip()
    if not text:
        return None
    try:
        client = _voyage_client_sync()
        result = client.embed([text], model=settings.voyage_model, input_type="document")
        return result.embeddings[0]
    except Exception as exc:
        log.warning("voyage_embed_sync_failed", error=str(exc), text_preview=text[:80])
        return None


def embed_batch_sync(texts: list[str]) -> list[Optional[list[float]]]:
    """Sync batched embedding. Returns a list parallel to `texts`, with None
    in any slot where the input was empty. All non-empty texts are sent in a
    single Voyage call (up to VOYAGE_BATCH_SIZE)."""
    if not settings.voyage_api_key:
        log.warning("voyage_key_missing")
        return [None] * len(texts)

    # Track which indices have content; only send non-empty ones to Voyage.
    indices = [i for i, t in enumerate(texts) if (t or "").strip()]
    if not indices:
        return [None] * len(texts)

    out: list[Optional[list[float]]] = [None] * len(texts)
    try:
        client = _voyage_client_sync()
        for start in range(0, len(indices), VOYAGE_BATCH_SIZE):
            chunk_idx = indices[start:start + VOYAGE_BATCH_SIZE]
            chunk_texts = [texts[i] for i in chunk_idx]
            result = client.embed(chunk_texts, model=settings.voyage_model,
                                  input_type="document")
            for src_idx, emb in zip(chunk_idx, result.embeddings):
                out[src_idx] = emb
    except Exception as exc:
        log.warning("voyage_embed_batch_failed",
                    error=str(exc), batch_size=len(indices))
    return out


class EmbeddingGenerator:
    """Async generator used by the analyse_product pipeline. Returns a
    1024-dim semantic embedding from Voyage."""

    async def generate(
        self,
        name: str,
        description: str = "",
        vision_attrs: Optional[dict] = None,
        nlp_attrs: Optional[dict] = None,
    ) -> Optional[list[float]]:
        text = _build_embedding_text(name, description, vision_attrs, nlp_attrs)
        if not text.strip():
            return None
        if not settings.voyage_api_key:
            log.warning("voyage_key_missing")
            return None
        try:
            client = _voyage_client_async()
            result = await client.embed(
                [text], model=settings.voyage_model, input_type="document",
            )
            return result.embeddings[0]
        except Exception as exc:
            log.warning("voyage_embed_async_failed",
                        error=str(exc), text_preview=text[:80])
            return None


async def cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)
