"""Material families — a buyer-facing grouping over the raw material tags
Claude attaches to products.

Why (2026-10-23): a fabrics-and-metals buyer couldn't find her products
in ALDI trend output. The raw tags are too fragmented to filter on
("stainless steel", "stainless-steel", "steel", "enameled cast iron" are
four tags for one remit), and nothing in the UI exposed them anyway.
Families collapse ~hundreds of tags into nine buyer remits.

Two consumers must agree exactly:
  * Python — families_for(tags) labels products in API responses.
  * SQL     — sql_regex(family) filters/aggregates in Postgres, applied to
              lower(product_attributes.materials::text), i.e. the JSON
              array rendered as text, e.g. '["stainless steel", "glass"]'.
Both are generated from the same PATTERNS table, so a tag is in a family
in SQL iff it is in Python.

Patterns are matched FIRST-WIN in FAMILY_ORDER, so a composite tag like
"enameled cast iron" lands in metal (listed before ceramic) and "faux
leather" in fabric. Keep new patterns specific; a loose one pollutes a
family the way 'holder' polluted ALDI keyword matches.
"""
from __future__ import annotations

import re

# family key → (label, [regex fragments]). Fragments are joined with | and
# wrapped in word boundaries. They must be valid in BOTH Python re and
# Postgres ARE (we only use literals, (?:…) groups, ? and |).
# Calibrated against the full catalogue on 2026-10-23 (451,700 products,
# 7,368 distinct tags): the original table covered 88% of tag occurrences;
# the additions below came from the unmapped tags with >=200 products,
# each placed by its co-occurring tags and sample product names —
#   mesh → metal (wire baskets/strainers 5:1 over nylon mesh)
#   enamel/enamelware → metal (on cast iron / steel)
#   lacquer → wood (rubberwood boxes, trays)   wheat straw → plastic (PP)
#   natural fiber → woven (baskets)            cloth/plush/flannel → fabric
# Deliberately NOT mapped (not materials, or too mixed): nonstick, insulated,
# magnetic, led, reed (reed diffusers), rope (clotheslines/planters/lights),
# platinum (a dinnerware rim finish), sponge, oil / essential oil / liquid.
PATTERNS: dict[str, tuple[str, list[str]]] = {
    "fabric": ("Fabric", [
        "fabric", "textile", "cotton", "linen", "canvas", "felt", "wool", "velvet",
        "polyester", "knit(?:ted)?", "burlap", "hessian", "fleece", "faux fur", "fur",
        "sherpa", "chenille", "microfib(?:er|re)", "nylon", "silk", "suede", "leather",
        "denim", "twill", "terry(?:cloth)?", "muslin", "gauze", "lace", "tweed", "boucl[eé]",
        "cashmere", "yarn", "upholster(?:y|ed)", "jacquard", "satin", "cord(?:uroy)?",
        "cloth", "plush", "flannel", "oxford (?:cloth|fabric)", "cheesecloth",
    ]),
    "metal": ("Metal", [
        "metal(?:lic)?", "stainless[ -]?steel", "steel", "iron", "brass", "alumin(?:i)?um",
        "copper", "tin", "zinc", "gold", "silver", "chrome", "pewter", "bronze", "nickel",
        "wire", "titanium", "galvani[sz]ed", "enamel(?:ware|ed|led)?", "carbon steel",
        "mesh", "foil",
    ]),
    "ceramic": ("Ceramic", [
        "ceramic", "porcelain", "stoneware", "earthenware", "terra ?cotta", "clay",
        "bone china", "china", "dolomite", "majolica", "faience", "pottery",
    ]),
    "glass": ("Glass", ["glass", "crystal", "borosilicate", "mirror(?:ed)?"]),
    "wood": ("Wood", [
        "wood(?:en)?", "oak", "walnut", "acacia", "teak", "olive ?wood", "mango ?wood",
        "pine", "birch", "beech", "maple", "mdf", "plywood", "veneer", "cork", "bamboo",
        "rubberwood", "sheesham", "paulownia", "cedar", "mahogany", "timber",
        "lacquer(?:ed)?",
    ]),
    "woven": ("Woven / natural fibre", [
        "rattan", "wicker", "seagrass", "jute", "sisal", "raffia", "water hyacinth",
        "cane", "abaca", "paper rope", "woven", "hyacinth", "banana leaf", "palm leaf",
        "natural fib(?:er|re)",
    ]),
    "plastic": ("Plastic / resin", [
        "plastic", "acrylic", "resin", "silicone", "melamine", "polypropylene", "pp",
        "pet", "vinyl", "pvc", "rubber", "foam", "tritan", "polyresin", "abs",
        "pe", "hdpe", "ldpe", "polyethylene", "polycarbonate", "polystyrene", "wheat straw",
    ]),
    "stone": ("Stone / concrete", [
        "marble", "stone", "granite", "concrete", "cement", "slate", "travertine",
        "soapstone", "quartz", "terrazzo", "onyx", "agate", "alabaster",
    ]),
    "paper": ("Paper / card", [
        "paper", "cardboard", "kraft", "card ?stock", "papier[- ]m[aâ]ch[eé]",
    ]),
    "wax": ("Wax", ["wax", "soy", "paraffin", "beeswax"]),
}

FAMILY_ORDER: list[str] = list(PATTERNS.keys())
FAMILY_LABELS: dict[str, str] = {k: v[0] for k, v in PATTERNS.items()}


def _alternation(family: str) -> str:
    return "(?:" + "|".join(PATTERNS[family][1]) + ")"


# Python-side compiled matchers (word boundaries via \b).
_PY: dict[str, re.Pattern[str]] = {
    f: re.compile(r"\b" + _alternation(f) + r"\b", re.IGNORECASE) for f in FAMILY_ORDER
}


def family_of(tag: str) -> str | None:
    """First family (in FAMILY_ORDER) whose pattern matches the tag, or None."""
    t = (tag or "").strip().lower()
    if not t:
        return None
    for f in FAMILY_ORDER:
        if _PY[f].search(t):
            return f
    return None


def families_for(tags: list[str] | None) -> list[str]:
    """Ordered, de-duplicated families for a product's raw material tags."""
    out: list[str] = []
    for tag in tags or []:
        f = family_of(tag)
        if f and f not in out:
            out.append(f)
    return out


def sql_regex(family: str) -> str:
    """Postgres ARE regex for `lower(materials::text) ~ :pat`. Word
    boundaries are \\y in Postgres; otherwise identical to the Python
    pattern, so SQL and Python agree on membership."""
    if family not in PATTERNS:
        raise KeyError(family)
    return r"\y" + _alternation(family) + r"\y"


def is_family(value: str | None) -> bool:
    return bool(value) and value in PATTERNS
