"""DPGF reverse-ingestion parser.

Reads a filled Merci Raymond DPGF (.xlsx) and yields structured line items
ready for matching back to existing products.

Schema assumptions (the master DPGF template, v2):
  - Working zone starts at column AA (client mirror), AD onwards is the
    Merci Raymond cascade (Famille / Sous-cat / Cond / Produit).
  - AG holds the picker string Vincent selected ("Famille — Sous-cat —
    Reference — Conditionnement").
  - AC holds the (mirrored) quantité.
  - BC holds the final PU client (after coefficients).
  - The "DPGF" tab is the operative one.

We try to be robust to small layout variations: if `Paramètres` exposes
Col_Designation / Col_Quantite mappings, we use those for the
client-designation reading.
"""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from typing import Any, Iterator

try:
    from openpyxl import load_workbook
    from openpyxl.utils import column_index_from_string
except ImportError:  # pragma: no cover
    load_workbook = None  # type: ignore
    column_index_from_string = None  # type: ignore


# Default column positions on the DPGF tab (1-indexed).
COL_AC = 29  # client-mirror Quantité
COL_AD = 30  # Famille
COL_AE = 31  # Sous-catégorie
COL_AF = 32  # Conditionnement
COL_AG = 33  # Produit (picker string)
COL_AI = 35  # Fournisseur (lookup formula)
COL_AQ = 43  # Fourniture / U (after column shift)
COL_BC = 55  # PU client total

DPGF_SHEET_CANDIDATES = ["DPGF", "DPGF Master", "DPGF Template"]
PARAMS_SHEET_CANDIDATES = ["Paramètres", "Parametres", "Settings"]


class DpgfFormatError(ValueError):
    """Raised when the uploaded workbook doesn't match the Merci Raymond
    DPGF template (no DPGF tab, wrong column layout, etc.). The caller
    surfaces the message to the user — these are expected user errors,
    not crashes."""


@dataclass
class DpgfLine:
    """One parseable line of a filled DPGF."""

    row_index: int                       # 1-indexed sheet row
    client_designation: str = ""         # text mirrored from the client zone
    unit: str | None = None              # mirrored client unit
    quantity: float | None = None        # mirrored Col_Quantite cell
    picker: str | None = None            # AG selected picker string
    famille: str | None = None           # parsed from picker
    sous_cat: str | None = None
    reference_name: str | None = None
    conditionnement: str | None = None
    # `pu_client` is the unit price accepted by the client — read from
    # col BC of the signed DPGF that's being re-ingested. Whether the
    # client renegotiated it during the quote phase is not something this
    # ingestion needs to detect: the workbook we receive is already the
    # accepted one.
    pu_client: float | None = None       # BC value (accepted PU)
    pu_fourniture: float | None = None   # AQ value (per-unit fourniture cost the row used)
    fournisseur: str | None = None       # AI value if formula returned one
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def client_price_differs_from_supplier(self) -> bool:
        """True when the client PU (BC) is meaningfully different from the
        supplier cost (AQ) on the same row. When they're equal, logging
        the BC value separately would just duplicate the cost update — so
        the page skips the validation point in that case. 1-cent
        tolerance to ignore spreadsheet rounding noise."""
        if self.pu_client is None or self.pu_fourniture is None:
            return False
        return abs(self.pu_client - self.pu_fourniture) > 0.01


def parse_dpgf(xlsx_bytes: bytes) -> list[DpgfLine]:
    """Parse a filled DPGF xlsx. Returns rows with at least quantity OR
    picker OR client_designation populated. Empty header / footer rows are
    skipped.

    `data_only=True` so we read FORMULA RESULTS, not the formulas themselves
    (the xlsx must have been opened in Excel/Sheets once for cached values
    to exist — Google Sheets stores them on download).
    """
    if load_workbook is None:
        raise RuntimeError("openpyxl is required for DPGF parsing")

    wb = load_workbook(filename=io.BytesIO(xlsx_bytes), data_only=True)
    sheet = None
    for name in DPGF_SHEET_CANDIDATES:
        if name in wb.sheetnames:
            sheet = wb[name]
            break
    if sheet is None:
        # No "DPGF" tab present → this is almost certainly not a Merci
        # Raymond DPGF workbook. Refuse rather than silently parse the
        # wrong sheet (which would produce noise but no error).
        raise DpgfFormatError(
            "Onglet « DPGF » introuvable dans le classeur. "
            "Le fichier doit être un classeur Merci Raymond DPGF contenant "
            "un onglet nommé « DPGF » (ou « DPGF Master » / « DPGF Template »). "
            f"Onglets trouvés : {', '.join(wb.sheetnames) or '(aucun)'}."
        )

    # Try to read the client-zone column mapping from Paramètres if available.
    col_desig = _resolve_client_column(wb, "Col_Designation", default_col=2)  # B
    col_unite = _resolve_client_column(wb, "Col_Unite", default_col=3)         # C
    col_qte = _resolve_client_column(wb, "Col_Quantite", default_col=5)        # E

    lines: list[DpgfLine] = []
    # Data rows are typically 3..502 per template.
    for r in range(3, min(sheet.max_row + 1, 600)):
        qty_val = sheet.cell(row=r, column=COL_AC).value
        picker_val = sheet.cell(row=r, column=COL_AG).value
        # Read client-zone via mapped columns, then fall back to AA-AB if blank
        client_desig = (
            sheet.cell(row=r, column=col_desig).value
            or sheet.cell(row=r, column=27).value   # AA fallback
        )
        unit_val = (
            sheet.cell(row=r, column=col_unite).value
            or sheet.cell(row=r, column=28).value   # AB fallback
        )
        client_qty = sheet.cell(row=r, column=col_qte).value

        # Use client_qty if AC mirror was empty (happens when formula failed)
        if qty_val is None and isinstance(client_qty, (int, float)):
            qty_val = client_qty

        # Skip rows with nothing to ingest
        if not qty_val and not picker_val and not client_desig:
            continue
        # Skip rows whose qty is a string (header/section row)
        if qty_val is not None and not isinstance(qty_val, (int, float)):
            qty_val = None

        line = DpgfLine(
            row_index=r,
            client_designation=str(client_desig or "").strip(),
            unit=(str(unit_val).strip() if unit_val else None),
            quantity=(float(qty_val) if qty_val is not None else None),
        )

        if picker_val and isinstance(picker_val, str) and picker_val.strip():
            line.picker = picker_val.strip()
            parts = [p.strip() for p in line.picker.split("—")]
            if len(parts) == 4:
                line.famille, line.sous_cat, line.reference_name, line.conditionnement = parts
            else:
                # Try old 3-part picker as a fallback
                if len(parts) == 3:
                    line.famille, line.reference_name, line.conditionnement = parts

        pu_client = sheet.cell(row=r, column=COL_BC).value
        if isinstance(pu_client, (int, float)):
            line.pu_client = float(pu_client)

        pu_fourn = sheet.cell(row=r, column=COL_AQ).value
        if isinstance(pu_fourn, (int, float)):
            line.pu_fourniture = float(pu_fourn)

        fournisseur = sheet.cell(row=r, column=COL_AI).value
        if isinstance(fournisseur, str) and fournisseur.strip():
            line.fournisseur = fournisseur.strip()

        line.raw = {
            "AC": qty_val,
            "AG": picker_val,
            "AI": fournisseur,
            "AQ": pu_fourn,
            "BC": pu_client,
        }
        lines.append(line)

    return lines


def _resolve_client_column(wb, named_range: str, default_col: int) -> int:
    """Look up a Paramètres-cell name (Col_Designation etc.) and return its
    1-indexed column position. Returns `default_col` if not found.
    """
    if column_index_from_string is None:
        return default_col

    # Find Paramètres sheet
    params_sheet = None
    for name in PARAMS_SHEET_CANDIDATES:
        if name in wb.sheetnames:
            params_sheet = wb[name]
            break
    if params_sheet is None:
        return default_col

    # The template stores Identifiant in column A, Valeur in column B
    # (rows 16-18 hold Col_Designation, Col_Unite, Col_Quantite).
    for row in range(15, 25):
        identifier = params_sheet.cell(row=row, column=1).value
        if identifier and str(identifier).strip() == named_range:
            value = params_sheet.cell(row=row, column=2).value
            if value and isinstance(value, str):
                try:
                    return column_index_from_string(value.strip().upper())
                except Exception:  # noqa: BLE001
                    return default_col
            break
    return default_col


def stats(lines: list[DpgfLine]) -> dict[str, int]:
    """Return rough counts: total / with_picker / with_qty / matched / unmatched."""
    total = len(lines)
    with_picker = sum(1 for l in lines if l.picker)
    with_qty = sum(1 for l in lines if l.quantity is not None)
    return {"total": total, "with_picker": with_picker, "with_qty": with_qty}
