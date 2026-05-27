"""Three-stage matcher for ingestion: deciding whether an extracted line
corresponds to an existing product.

Stage A — Postgres trigram candidate retrieval
Stage B — Python composite scoring (unit hard-disqualify + weighted blend)
Stage C — Gemini LLM disambiguation when composite is between LOW and HIGH
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any

from . import gemini
from .db import fetch_all, get_setting

log = logging.getLogger(__name__)

# Weights — tunable; named constants per PRD §12.2.
W_NAME = 0.40
W_PACKAGING = 0.20
W_BRAND = 0.10
W_ATTR_OVERLAP = 0.15
W_ATTR_VALUE_MATCH = 0.15

DEFAULT_THRESHOLD_HIGH = 0.90
DEFAULT_THRESHOLD_LOW = 0.50
TOP_K = 10
LLM_TOP_K = 5

# Below this packaging-string similarity, the matcher hard-disqualifies the
# candidate. Rationale: in landscaping, weight/volume variants of the same
# product (e.g. "Sac 25kg" vs "Sac 50kg") have different prices and MUST be
# kept as distinct SKUs — never auto-merged. 0.6 is permissive enough to
# let "BigBag" match "BigBag 2m³" but tight enough to reject "Sac 25kg"
# vs "Sac 50kg".
PACKAGING_HARD_DISQUALIFY_THRESHOLD = 0.6


def _normalize_unit(u: str | None) -> str:
    """Map free-form unit strings to the schema enum used by labor_norms.

    The matcher's hard-disqualify rule depends on this — keep generous.
    """
    if not u:
        return ""
    s = u.strip().lower()
    aliases = {
        "u": "u", "un": "u", "unite": "u", "unité": "u", "pce": "u", "piece": "u", "pièce": "u",
        "m3": "m3", "m³": "m3",
        "m2": "m2", "m²": "m2",
        "ml": "ml", "metre": "ml", "mètre": "ml",
        "kg": "kg",
        "l": "l", "litre": "l",
        "ft": "Ft", "forfait": "Ft", "fft": "Ft",
    }
    # Try direct lookup, else the canonical-cased form.
    return aliases.get(s, s if s in {"u", "m3", "m2", "ml", "kg", "l", "Ft"} else s)


def _string_similarity(a: str | None, b: str | None) -> float:
    if not a or not b:
        return 0.5 if (not a and not b) else 0.0
    return SequenceMatcher(None, a.lower().strip(), b.lower().strip()).ratio()


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.5  # neutral when neither side has structured attributes
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


@dataclass
class CandidateScore:
    candidate: dict[str, Any]
    composite: float
    name_sim: float
    packaging_sim: float
    brand_match: float
    attr_overlap: float
    attr_value_match: float
    disqualified: bool = False
    reason: str = ""


@dataclass
class MatchResult:
    """The matcher's final say for one extracted line."""

    action: str  # "auto-match" | "match" | "new"
    product_id: int | None
    confidence: float
    reasoning: str
    top_candidates: list[CandidateScore] = field(default_factory=list)


def find_similar_lines(
    target_text: str,
    all_texts: list[str],
    *,
    threshold: float = 0.72,
    exclude_self_index: int | None = None,
) -> list[int]:
    """Return the indices of texts in `all_texts` whose normalized similarity
    to `target_text` is ≥ threshold. Used by the Ingestion review page's
    "Approuver + N similaires" button: given a line the user is approving,
    find other lines in the same invoice that share the extracted text and
    should get the same triplet applied.

    Pure Python (no DB). Operates on `difflib.SequenceMatcher` after
    lowercasing + stripping non-alphanumeric noise — robust to spacing
    and punctuation variation in OCR'd invoice text.
    """
    if not target_text:
        return []

    def _norm(s: str) -> str:
        s = s.lower()
        # Keep alphanumerics + spaces; collapse the rest. Cheap, locale-independent.
        out = []
        prev_space = False
        for ch in s:
            if ch.isalnum():
                out.append(ch)
                prev_space = False
            elif ch.isspace() or ch in "-/.,;:'":
                if not prev_space:
                    out.append(" ")
                    prev_space = True
        return "".join(out).strip()

    target_norm = _norm(target_text)
    if not target_norm:
        return []

    matches: list[int] = []
    for i, t in enumerate(all_texts):
        if i == exclude_self_index:
            continue
        cand = _norm(t)
        if not cand:
            continue
        sim = SequenceMatcher(None, target_norm, cand).ratio()
        if sim >= threshold:
            matches.append(i)
    return matches


def find_similar_products(
    query_text: str,
    *,
    top_k: int = 5,
    family_id: int | None = None,
    subcategory: str | None = None,
    min_similarity: float = 0.15,
) -> list[dict[str, Any]]:
    """Return top-k existing products whose `reference_name` is most similar
    to `query_text`, using Postgres pg_trgm similarity. Used by:
      - Retour DPGF: matching a DPGF line to a catalog product
      - Future: any "find me products that look like this" call

    Each row: id, reference_name, family_name, subcategory, packaging,
    supplier_name, cost_ht, similarity.
    """
    if not query_text or not query_text.strip():
        return []
    sql = """
        SELECT
            p.id,
            p.reference_name,
            pf.name AS family_name,
            p.subcategory,
            p.packaging,
            s.name AS supplier_name,
            p.cost_ht,
            similarity(unaccent(p.reference_name), unaccent(:q)) AS sim
          FROM products p
          JOIN product_families pf ON pf.id = p.family_id
          JOIN suppliers s         ON s.id = p.supplier_id
         WHERE p.is_active = TRUE
           AND similarity(unaccent(p.reference_name), unaccent(:q)) >= :min_sim
           AND (:family_id IS NULL OR p.family_id = :family_id)
           AND (:subcategory IS NULL OR p.subcategory = :subcategory)
         ORDER BY sim DESC
         LIMIT :k
    """
    return fetch_all(
        sql,
        {
            "q": query_text,
            "k": top_k,
            "family_id": family_id,
            "subcategory": subcategory or None,
            "min_sim": min_similarity,
        },
    )


def _stage_a_candidates(
    query_name: str,
    *,
    family_id: int | None = None,
    subcategory: str | None = None,
    packaging: str | None = None,
) -> list[dict[str, Any]]:
    """Trigram candidate retrieval (Stage A). Returns top TOP_K rows or [].

    Both sides go through unaccent() so 'Chêne' matches 'Chene'.

    When (family_id, subcategory, packaging) are provided, they act as
    HARD pre-filters before the trigram step — products with a different
    triplet are not even considered. Each filter is independent; pass
    None to ignore that dimension (useful when Gemini left it unset).
    """
    if not query_name.strip():
        return []
    # NOTE: pg_trgm's `%` operator is written as a single `%` here on purpose.
    # SQLAlchemy 2.x's `text()` already auto-escapes literal `%` to `%%` so it
    # survives psycopg2's pyformat paramstyle. Doubling the operator manually
    # ends up producing `%%%%` -> `%%` at the Postgres wire level, which is
    # invalid (`operator does not exist: text %% text`). Keep it as a single `%`.
    sql = """
        SELECT
            id, reference_name, family_id, subcategory, brand, material,
            packaging, unit_type, attributes,
            similarity(unaccent(reference_name), unaccent(:q)) AS sim
        FROM products
        WHERE is_active = TRUE
          AND unaccent(reference_name) % unaccent(:q)
          AND (:family_id IS NULL OR family_id = :family_id)
          AND (:subcategory IS NULL OR subcategory = :subcategory)
          AND (:packaging IS NULL OR packaging = :packaging)
        ORDER BY sim DESC
        LIMIT :k
    """
    return fetch_all(
        sql,
        {
            "q": query_name,
            "k": TOP_K,
            "family_id": family_id,
            "subcategory": subcategory or None,
            "packaging": packaging or None,
        },
    )


def _score_candidate(query: dict[str, Any], cand: dict[str, Any]) -> CandidateScore:
    """Stage B: composite score for one candidate."""
    q_unit = _normalize_unit(query.get("unit_type"))
    c_unit = _normalize_unit(cand.get("unit_type"))
    if q_unit and c_unit and q_unit != c_unit:
        return CandidateScore(
            candidate=cand,
            composite=0.0,
            name_sim=float(cand.get("sim") or 0.0),
            packaging_sim=0.0,
            brand_match=0.0,
            attr_overlap=0.0,
            attr_value_match=0.0,
            disqualified=True,
            reason=f"unit mismatch ({c_unit} vs {q_unit})",
        )

    name_sim = float(cand.get("sim") or 0.0)
    packaging_sim = _string_similarity(query.get("packaging"), cand.get("packaging"))

    # Hard rule: clearly-different packaging means different SKU.
    # Skip when either side is empty (we don't penalise missing data,
    # only contradictory data).
    q_pkg = (query.get("packaging") or "").strip()
    c_pkg = (cand.get("packaging") or "").strip()
    if q_pkg and c_pkg and packaging_sim < PACKAGING_HARD_DISQUALIFY_THRESHOLD:
        return CandidateScore(
            candidate=cand,
            composite=0.0,
            name_sim=name_sim,
            packaging_sim=packaging_sim,
            brand_match=0.0,
            attr_overlap=0.0,
            attr_value_match=0.0,
            disqualified=True,
            reason=f"conditionnement différent ({c_pkg!r} vs {q_pkg!r}, sim={packaging_sim:.2f})",
        )

    q_brand = (query.get("brand") or "").strip().lower()
    c_brand = (cand.get("brand") or "").strip().lower()
    if q_brand and c_brand:
        brand_match = 1.0 if q_brand == c_brand else 0.0
    elif not q_brand and not c_brand:
        brand_match = 0.5
    else:
        brand_match = 0.0

    q_attrs = query.get("attributes") or {}
    c_attrs = cand.get("attributes") or {}
    if isinstance(c_attrs, str):
        # In case psycopg2 returned the JSONB column as a string.
        import json as _json

        try:
            c_attrs = _json.loads(c_attrs)
        except _json.JSONDecodeError:
            c_attrs = {}

    attr_overlap = _jaccard(set(q_attrs.keys()), set(c_attrs.keys()))
    shared_keys = set(q_attrs.keys()) & set(c_attrs.keys())
    if shared_keys:
        attr_value_match = sum(
            1.0 if str(q_attrs[k]).strip().lower() == str(c_attrs[k]).strip().lower() else 0.0
            for k in shared_keys
        ) / len(shared_keys)
    else:
        attr_value_match = 0.5  # neutral

    composite = (
        W_NAME * name_sim
        + W_PACKAGING * packaging_sim
        + W_BRAND * brand_match
        + W_ATTR_OVERLAP * attr_overlap
        + W_ATTR_VALUE_MATCH * attr_value_match
    )

    return CandidateScore(
        candidate=cand,
        composite=composite,
        name_sim=name_sim,
        packaging_sim=packaging_sim,
        brand_match=brand_match,
        attr_overlap=attr_overlap,
        attr_value_match=attr_value_match,
        disqualified=False,
    )


def _read_thresholds() -> tuple[float, float]:
    high = float(get_setting("matching_threshold_high", str(DEFAULT_THRESHOLD_HIGH)) or DEFAULT_THRESHOLD_HIGH)
    low = float(get_setting("matching_threshold_low", str(DEFAULT_THRESHOLD_LOW)) or DEFAULT_THRESHOLD_LOW)
    return high, low


def match(query: dict[str, Any]) -> MatchResult:
    """Run the three-stage match for one extracted line.

    `query` keys (all optional except reference_name):
        reference_name, packaging, unit_type, brand, material, attributes (dict),
        family_id, subcategory

    When the triplet (family_id, subcategory, packaging) is present, Stage A
    pre-filters candidates on exact match — a product in a different family
    or sub-category will never be considered a match.
    """
    name = (query.get("reference_name") or "").strip()
    if not name:
        return MatchResult(
            action="new", product_id=None, confidence=0.0,
            reasoning="Aucun nom de référence sur lequel chercher",
        )

    candidates = _stage_a_candidates(
        name,
        family_id=query.get("family_id"),
        subcategory=query.get("subcategory"),
        packaging=query.get("packaging"),
    )
    if not candidates:
        return MatchResult(
            action="new", product_id=None, confidence=0.0,
            reasoning="Aucun candidat trouvé dans la base (recherche par trigramme)",
        )

    scored = [_score_candidate(query, c) for c in candidates]
    # Disqualified at the bottom but kept for transparency.
    scored.sort(key=lambda s: (s.disqualified, -s.composite))

    qualified = [s for s in scored if not s.disqualified]
    if not qualified:
        return MatchResult(
            action="new", product_id=None, confidence=0.0,
            reasoning="Tous les candidats ont été disqualifiés (unité différente)",
            top_candidates=scored[:LLM_TOP_K],
        )

    high, low = _read_thresholds()
    top = qualified[0]

    if top.composite >= high:
        return MatchResult(
            action="auto-match",
            product_id=int(top.candidate["id"]),
            confidence=top.composite,
            reasoning=f"Score composite élevé {top.composite:.3f} ≥ {high}",
            top_candidates=qualified[:LLM_TOP_K],
        )

    if top.composite >= low:
        # Stage C — let Gemini choose
        try:
            cands_for_llm = [
                {
                    "id": c.candidate["id"],
                    "reference_name": c.candidate.get("reference_name"),
                    "family_id": c.candidate.get("family_id"),
                    "subcategory": c.candidate.get("subcategory"),
                    "packaging": c.candidate.get("packaging"),
                    "unit_type": c.candidate.get("unit_type"),
                    "brand": c.candidate.get("brand"),
                    "material": c.candidate.get("material"),
                    "attributes": c.candidate.get("attributes") or {},
                }
                for c in qualified[:LLM_TOP_K]
            ]
            verdict = gemini.disambiguate(query=query, candidates=cands_for_llm)
        except Exception as exc:
            log.warning("Stage C LLM call failed; falling back to top-composite. %s", exc)
            return MatchResult(
                action="match",
                product_id=int(top.candidate["id"]),
                confidence=top.composite,
                reasoning=(
                    f"Score composite {top.composite:.3f} entre {low} et {high} ; "
                    f"appel LLM échoué ({exc}) ; meilleur candidat retenu"
                ),
                top_candidates=qualified[:LLM_TOP_K],
            )

        if verdict.chosen_product_id is None:
            return MatchResult(
                action="new",
                product_id=None,
                confidence=verdict.confidence,
                reasoning=f"Verdict LLM : nouveau produit. {verdict.reasoning}",
                top_candidates=qualified[:LLM_TOP_K],
            )
        return MatchResult(
            action="match",
            product_id=int(verdict.chosen_product_id),
            confidence=verdict.confidence,
            reasoning=f"Verdict LLM. {verdict.reasoning}",
            top_candidates=qualified[:LLM_TOP_K],
        )

    return MatchResult(
        action="new",
        product_id=None,
        confidence=top.composite,
        reasoning=f"Score composite {top.composite:.3f} < seuil bas {low} — nouveau produit",
        top_candidates=qualified[:LLM_TOP_K],
    )
