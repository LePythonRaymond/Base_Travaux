"""Pydantic models for LLM I/O.

Per PRD §11.4. These shapes are used both as the contract Gemini must
follow (we serialize them to JSON-Schema in the prompt) and as the
landing schema for ingestion_queue.raw_payload.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ExtractedAttribute(BaseModel):
    key: str
    value: str


class ExtractedLineItem(BaseModel):
    """One line of the invoice. Some lines (labor charges, totals, freight)
    are NOT product lines — Gemini flags those with is_product_line=false."""

    model_config = ConfigDict(extra="ignore")

    is_product_line: bool
    designation_raw: str

    quantity: Decimal | None = None
    unit_invoice: str | None = None
    unit_price_ht: Decimal | None = None
    total_ht: Decimal | None = None

    # Enrichment (call B in PRD §11.2 — combined with extraction in our pipeline):
    reference_name: str | None = None
    family_hint: str | None = None
    brand: str | None = None
    material: str | None = None
    packaging: str | None = None
    unit_type_normalized: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)
    suggested_labor_task: str | None = None
    # The middle tier of the (Famille, Sous-catégorie, Conditionnement) triplet.
    # Must be picked from the live taxonomy passed in the prompt (or left null —
    # in which case the line lands in `ingestion_queue.status='needs_info'` and
    # a human completes it in the À classifier Streamlit page).
    subcategory: str | None = None


class ExtractedSupplier(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str | None = None
    address: str | None = None
    siret: str | None = None
    contact_email: str | None = None
    contact_phone: str | None = None


class ExtractedInvoice(BaseModel):
    model_config = ConfigDict(extra="ignore")

    supplier: ExtractedSupplier = Field(default_factory=ExtractedSupplier)
    invoice_date: date | None = None
    invoice_number: str | None = None
    currency: str = "EUR"
    line_items: list[ExtractedLineItem] = Field(default_factory=list)


class MatchVerdict(BaseModel):
    """Stage C output — Gemini's call on which existing product (if any) the
    incoming line matches."""

    model_config = ConfigDict(extra="ignore")

    chosen_product_id: int | None
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str


class ExtractionError(Exception):
    """Raised when Gemini's response cannot be parsed into ExtractedInvoice."""

    def __init__(self, message: str, raw_response: str | None = None) -> None:
        super().__init__(message)
        self.raw_response = raw_response
