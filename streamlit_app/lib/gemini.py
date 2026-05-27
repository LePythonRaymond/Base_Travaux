"""Gemini client + invoice extraction + match disambiguation.

Three calls in the pipeline (per PRD §11.2):
  - Call A+B (combined): extract_invoice() — full invoice → ExtractedInvoice
  - Call C: disambiguate() — when matcher composite is between LOW and HIGH

Resilience:
  - Model fallback: try app_settings.llm_model; if NotFound/404, retry with
    gemini-3-flash-preview (the current Gemini 3 Flash tier; multimodal +
    structured output, much cheaper than Pro).
  - PDF fallback: try direct PDF input; if rejected, rasterize via pypdfium2
    and resend as image parts.
  - JSON parse retry: on parse failure, retry once with the error appended.
"""

from __future__ import annotations

import io
import json
import logging
import os
from typing import Any

import google.generativeai as genai
import pypdfium2 as pdfium
from pydantic import ValidationError

from .prompts import (
    DISAMBIGUATE_USER_PROMPT_TEMPLATE,
    EXTRACT_USER_PROMPT_TEMPLATE,
    SYSTEM_PROMPT,
    build_forbidden_keys_block,
    build_taxonomy_block,
)
from .schemas import ExtractedInvoice, ExtractionError, MatchVerdict

log = logging.getLogger(__name__)

FALLBACK_MODEL = "gemini-3-flash-preview"


def _configure() -> None:
    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not set in the environment.")
    genai.configure(api_key=api_key)


def _resolve_model_name() -> str:
    """Read llm_model from app_settings; fall back gracefully."""
    # Lazy import so this module is testable in isolation.
    from .db import get_setting

    return get_setting("llm_model", default=FALLBACK_MODEL) or FALLBACK_MODEL


def _make_model(model_name: str) -> genai.GenerativeModel:
    return genai.GenerativeModel(
        model_name,
        system_instruction=SYSTEM_PROMPT,
    )


def _rasterize_pdf(pdf_bytes: bytes, max_pages: int = 12, dpi: int = 144) -> list[bytes]:
    """Convert a PDF to a list of PNG byte blobs (one per page).

    Used as a fallback when direct PDF input is rejected by the model.
    Caps at max_pages to keep payloads bounded.
    """
    pages: list[bytes] = []
    doc = pdfium.PdfDocument(pdf_bytes)
    try:
        for i, page in enumerate(doc):
            if i >= max_pages:
                break
            pil_image = page.render(scale=dpi / 72.0).to_pil()
            buf = io.BytesIO()
            pil_image.save(buf, format="PNG")
            pages.append(buf.getvalue())
    finally:
        doc.close()
    return pages


def _generate(
    model: genai.GenerativeModel,
    parts: list[Any],
    *,
    response_mime_type: str = "application/json",
) -> str:
    """Run a generation request and return the text. No streaming."""
    response = model.generate_content(
        parts,
        generation_config={
            "response_mime_type": response_mime_type,
            "temperature": 0.1,
        },
    )
    if not response.candidates:
        raise ExtractionError("Gemini returned no candidates.")
    text = (response.text or "").strip()
    if not text:
        raise ExtractionError("Gemini returned empty text.", raw_response=str(response))
    return text


def _parse_invoice(text: str) -> ExtractedInvoice:
    """Strict parse of model output → ExtractedInvoice. Raises ExtractionError."""
    try:
        return ExtractedInvoice.model_validate_json(text)
    except ValidationError as exc:
        raise ExtractionError(
            f"ExtractedInvoice validation failed: {exc}",
            raw_response=text,
        ) from exc
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"JSON parse failed: {exc}",
            raw_response=text,
        ) from exc


def extract_invoice(
    pdf_bytes: bytes,
    *,
    labor_norm_names: list[str],
    family_names: list[str],
) -> ExtractedInvoice:
    """Run extraction on a supplier invoice PDF.

    1. Try the model from app_settings.llm_model. On NotFound, fall back to
       gemini-3-flash-preview (FALLBACK_MODEL).
    2. Try direct PDF input. On rejection, rasterize via pypdfium2 and retry.
    3. On parse failure, retry once with the error appended to the prompt.
    """
    _configure()
    # Pull the live flat columns + taxonomy from the DB so the prompt adapts
    # whenever someone alters the products schema or extends the taxonomy.
    from .db import fetch_all, get_product_flat_columns

    flat_columns = get_product_flat_columns()
    taxonomy_rows = fetch_all(
        """
        SELECT pt.family_id, pf.name AS family_name,
               pt.subcategory, pt.packaging
          FROM product_taxonomy pt
          JOIN product_families pf ON pf.id = pt.family_id
         WHERE pt.subcategory <> 'À classifier'   -- hide the triage bucket from Gemini
         ORDER BY pf.name, pt.subcategory, pt.packaging
        """
    )
    sample_rows = fetch_all(
        """
        SELECT family_id, subcategory, packaging, reference_name
          FROM (
            SELECT family_id, subcategory, packaging, reference_name,
                   row_number() OVER (
                       PARTITION BY family_id, subcategory, packaging
                       ORDER BY last_price_update DESC
                   ) AS rn
              FROM products
             WHERE is_active = TRUE
               AND subcategory <> 'À classifier'
          ) t
         WHERE rn <= 3
        """
    )
    samples_by_triplet: dict[tuple[int, str, str], list[str]] = {}
    for s in sample_rows:
        key = (s["family_id"], s["subcategory"], s["packaging"])
        samples_by_triplet.setdefault(key, []).append(s["reference_name"])

    schema_json = json.dumps(ExtractedInvoice.model_json_schema(), ensure_ascii=False, indent=2)
    user_prompt = EXTRACT_USER_PROMPT_TEMPLATE.format(
        schema_json=schema_json,
        labor_norms_list="\n".join(f"- {n}" for n in labor_norm_names),
        family_names_list="\n".join(f"- {n}" for n in family_names),
        taxonomy_block=build_taxonomy_block(taxonomy_rows, samples_by_triplet),
        flat_columns_list="\n".join(f"- {c}" for c in flat_columns) or "(aucune)",
        forbidden_keys_list=build_forbidden_keys_block(flat_columns),
    )

    primary_model_name = _resolve_model_name()
    model_names_to_try: list[str] = [primary_model_name]
    if primary_model_name != FALLBACK_MODEL:
        model_names_to_try.append(FALLBACK_MODEL)

    last_exc: Exception | None = None
    for model_name in model_names_to_try:
        try:
            model = _make_model(model_name)
        except Exception as exc:  # pragma: no cover — defensive
            log.warning("Could not instantiate model %s: %s", model_name, exc)
            last_exc = exc
            continue

        # Attempt 1: direct PDF input
        try:
            pdf_part = {"mime_type": "application/pdf", "data": pdf_bytes}
            text = _generate(model, [pdf_part, user_prompt])
        except Exception as exc:
            log.warning(
                "Direct PDF input failed for model %s (%s). Falling back to rasterized images.",
                model_name, exc,
            )
            try:
                images = _rasterize_pdf(pdf_bytes)
            except Exception as render_exc:
                log.error("PDF rasterization failed: %s", render_exc)
                last_exc = render_exc
                continue
            try:
                image_parts = [{"mime_type": "image/png", "data": b} for b in images]
                text = _generate(model, [*image_parts, user_prompt])
            except Exception as img_exc:
                log.warning("Image fallback also failed for model %s: %s", model_name, img_exc)
                last_exc = img_exc
                # If this looks like a model-not-found, try the fallback model.
                if "not found" in str(img_exc).lower() or "404" in str(img_exc):
                    continue
                raise

        # Attempt to parse; one retry with the error appended.
        try:
            return _parse_invoice(text)
        except ExtractionError as parse_exc:
            log.info("First parse failed; retrying with error context. (%s)", parse_exc)
            try:
                retry_prompt = (
                    user_prompt
                    + "\n\n# CORRECTION\nLa réponse précédente n'était pas un JSON valide. "
                    + f"Erreur : {parse_exc}\nRenvoie un JSON strict, sans texte autour."
                )
                text2 = _generate(model, [retry_prompt])
                return _parse_invoice(text2)
            except Exception as retry_exc:
                last_exc = retry_exc
                # Don't try a different model just for parse failures — try the
                # next one only if this looks like a transport/auth issue.
                continue

    raise ExtractionError(
        f"Gemini extraction failed across all attempts. Last error: {last_exc}",
    )


def disambiguate(
    *,
    query: dict[str, Any],
    candidates: list[dict[str, Any]],
) -> MatchVerdict:
    """Stage C: ask Gemini to choose between top candidates.

    `query` is a dict-like view of the extracted line (reference_name, packaging,
    unit_type, brand, material, attributes, cost_ht). `candidates` is a list of
    similar dicts with an extra `id` key.
    """
    _configure()
    primary_model_name = _resolve_model_name()
    model = _make_model(primary_model_name)

    user_prompt = DISAMBIGUATE_USER_PROMPT_TEMPLATE.format(
        query_json=json.dumps(query, ensure_ascii=False, default=str),
        candidates_json=json.dumps(candidates, ensure_ascii=False, default=str, indent=2),
        n=len(candidates),
    )
    text = _generate(model, [user_prompt])
    try:
        return MatchVerdict.model_validate_json(text)
    except ValidationError as exc:
        log.warning("Disambiguate output invalid; treating as 'no match'. %s. Raw: %s", exc, text)
        return MatchVerdict(chosen_product_id=None, confidence=0.0, reasoning=f"parse_error: {exc}")
