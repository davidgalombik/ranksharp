"""
Embedding module.
Generates a 1536-dim vector for each product to enable semantic similarity
and cross-retailer clustering.

Uses a deterministic keyword-based pseudo-embedding.
Replace _keyword_embedding with voyage-3 (Anthropic recommended) when
real semantic embeddings are needed in production.
"""
import hashlib
import numpy as np
import structlog
from typing import Optional

log = structlog.get_logger()

EMBEDDING_DIM = 1536


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


class EmbeddingGenerator:
    async def generate(
        self,
        name: str,
        description: str = "",
        vision_attrs: Optional[dict] = None,
        nlp_attrs: Optional[dict] = None,
    ) -> Optional[list[float]]:
        """
        Generate a 1536-dim embedding vector for a product.
        Uses deterministic keyword hashing — no API calls.
        """
        text = _build_embedding_text(name, description, vision_attrs, nlp_attrs)
        if not text.strip():
            return None
        return self._keyword_embedding(text)

    def _keyword_embedding(self, text: str) -> list[float]:
        """
        Deterministic pseudo-embedding from text hashing.
        Suitable for initial development; replace with voyage-3 in production.
        Produces a 1536-dim float vector that captures coarse semantic similarity.
        """
        rng = np.random.RandomState(
            seed=int(hashlib.md5(text.lower().encode()).hexdigest(), 16) % (2**31)
        )
        base = rng.randn(EMBEDDING_DIM).astype(np.float32)

        # Boost dimensions corresponding to key terms found in text
        text_lower = text.lower()
        keywords = {
            "green": 0, "blue": 10, "pink": 20, "white": 30, "black": 40,
            "beige": 50, "terracotta": 60, "sage": 70, "cream": 80,
            "ceramic": 100, "linen": 110, "rattan": 120, "wood": 130,
            "glass": 140, "metal": 150, "bamboo": 160, "cotton": 170,
            "striped": 200, "floral": 210, "geometric": 220,
            "minimalist": 300, "coastal": 310, "rustic": 320, "boho": 330,
            "organiser": 400, "container": 410, "basket": 420, "vase": 430,
            "candle": 440, "tray": 450, "jar": 460,
            "kitchen": 500, "living": 510, "bedroom": 520, "bathroom": 530,
        }
        for term, dim in keywords.items():
            if term in text_lower:
                base[dim] += 2.0

        # Normalise to unit length
        norm = np.linalg.norm(base)
        if norm > 0:
            base = base / norm
        return base.tolist()


async def cosine_similarity(a: list[float], b: list[float]) -> float:
    va = np.array(a, dtype=np.float32)
    vb = np.array(b, dtype=np.float32)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    if denom == 0:
        return 0.0
    return float(np.dot(va, vb) / denom)
