"""Dataclasses passed between stages."""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal


@dataclass
class BandLayout:
    """Resolved column roles for one priced sheet (column indices are 1-based)."""

    sheet: str
    header_row: int            # the band-label row (COÛT HUMAIN / FOURNITURE ...)
    sub_row: int               # the sub-header row (Fourniture/U, Heure/U pose ...)
    data_start_row: int
    cols: dict[str, int] = field(default_factory=dict)  # role -> col index
    first_band_col: int = 0    # leftmost band column (left block is to its left)
    confidence: float = 0.0
    anchor_audit: dict = field(default_factory=dict)

    @property
    def cost_col(self) -> int | None:
        return self.cols.get("cost_ht")


@dataclass
class RawLine:
    """One extracted data row from a priced sheet."""

    file: str
    sheet: str
    row: int
    designation: str
    unit_raw: str
    unit: str | None                 # normalized to UNIT_TYPES, else None
    quantity: Decimal | None
    cost_ht: Decimal | None          # Fourniture/U (raw supplier cost, pre-margin)
    heure_u_decharge: Decimal | None  # Heure/U appro
    heure_u_pose: Decimal | None
    nombre_uth: Decimal | None
    comment: str = ""
    col_attributes: dict = field(default_factory=dict)  # forme/taille captured from labeled-ish left cols
    section_path: tuple[str, ...] = ()
    is_option: bool = False
    flags: list[str] = field(default_factory=list)
    provenance: dict[str, str] = field(default_factory=dict)  # field -> "sheet!A12 raw='..'"

    @property
    def has_cost(self) -> bool:
        return self.cost_ht is not None and self.cost_ht > 0

    @property
    def has_labor(self) -> bool:
        return any(
            v is not None and v > 0
            for v in (self.heure_u_pose, self.heure_u_decharge)
        )


@dataclass
class SupplierCandidate:
    canonical: str
    display: str
    source_files: set[str] = field(default_factory=set)
    aliases: set[str] = field(default_factory=set)


@dataclass
class LaborObservation:
    task_name: str            # semantic identity (LLM-assigned, fallback: section path)
    unit: str | None
    heure_u_pose: Decimal | None
    nombre_uth: Decimal | None
    heure_u_decharge: Decimal | None
    file: str
    designation: str = ""


@dataclass
class ProductCandidate:
    """A deduped product ready for the review sheet (one row per business key)."""

    reference_name: str
    unit: str | None
    packaging: str = ""
    family: str | None = None
    subcategory: str | None = None
    brand: str | None = None
    material: str | None = None
    attributes: dict = field(default_factory=dict)
    supplier: str | None = None
    labor_task: str | None = None
    cost_ht: Decimal | None = None         # chosen canonical cost
    costs: list[Decimal] = field(default_factory=list)  # all observed costs
    n_sources: int = 0
    source_files: set[str] = field(default_factory=set)
    source_cells: list[str] = field(default_factory=list)
    confidence: float = 1.0
    is_option: bool = False
    flags: list[str] = field(default_factory=list)
