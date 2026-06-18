"""Iterate the data rows of a priced sheet into RawLine records.

Section-header rows update the PathTracker (and are skipped). The multiplier row is
already above data_start_row so it is never reached; cost is read ONLY from the
resolved Fourniture/U column, so a coefficient can never become a cost.
"""

from __future__ import annotations

import re

from .config import normalize
from .frnum import parse_cost, parse_number
from .models import BandLayout, RawLine
from .taxonomy_path import PathTracker, is_section_header, numbering_depth
from .units import normalize_unit
from .workbook import Grid

# Footer / total rows to skip even if they carry a number.
_FOOTER_HINTS = (
    "sous total", "sous-total", "total ht", "total h.t", "total hors taxes",
    "total ttc", "total tva", "tva ", "tva(", "remise", "total general",
    "montant ht", "total espaces", "total variante", "total ftm", "total lot",
)
_NUMBERISH = re.compile(r"^[\s\d.,%€/+\-]*$")


def _is_footer(designation_norm: str) -> bool:
    return any(h in designation_norm for h in _FOOTER_HINTS)


_FORME = {"tige", "cepee", "buisson", "touffe", "demi-tige", "demi tige", "baliveau",
          "brin", "cone", "boule", "parasol", "plateau", "multi-tiges", "tiges"}
_SIZECODE = re.compile(r"^(c\s?\d+|g\s?\d+|\d{1,3}\s*/\s*\d{1,3}|\d{1,3}\s*l\b|motte|rac\.?\s?nue)", re.I)


def _primary_designation_col(grid: Grid, cand_cols: list[int], start: int) -> int | None:
    """The left column carrying the most long alphabetic text = the designation col.
    (In files where the genus name is column B and Forme/Force are C/D, this picks B.)"""
    best_cnt, best_c = -1, None
    end = min(start + 70, grid.nrows + 1)
    for c in cand_cols:
        cnt = 0
        for r in range(start, end):
            v = grid.cell(r, c)
            if isinstance(v, str):
                s = v.strip()
                if len(s) >= 6 and sum(ch.isalpha() for ch in s) >= 4 and not _SIZECODE.match(s):
                    cnt += 1
        if cnt > best_cnt:
            best_cnt, best_c = cnt, c
    return best_c


def _row_designation(grid: Grid, r: int, primary_col: int | None,
                     fallback_cols: list[int]) -> tuple[str, dict]:
    """Designation = primary col if filled; else the most-indented (rightmost) text
    cell. Forme/Force in the other left columns are captured as attributes."""
    attrs: dict[str, str] = {}
    for c in fallback_cols:
        if c == primary_col:
            continue
        v = grid.cell(r, c)
        if isinstance(v, str) and v.strip():
            s = v.strip()
            sn = normalize(s)
            if sn in _FORME:
                attrs.setdefault("forme", s)
            elif _SIZECODE.match(s) and len(s) <= 14:
                attrs.setdefault("taille", s)
    designation = ""
    if primary_col:
        v = grid.cell(r, primary_col)
        if isinstance(v, str) and v.strip() and not _NUMBERISH.match(v.strip()):
            designation = v.strip()
    if not designation:
        for c in fallback_cols:  # ordered right→left = most indented first
            v = grid.cell(r, c)
            if isinstance(v, str):
                s = v.strip()
                if s and not _NUMBERISH.match(s):
                    designation = s
                    break
    return designation, attrs


def _excluded_left_cols(grid: Grid, layout: BandLayout, first_band: int) -> set[int]:
    """Left columns that are NOT the designation: unit / qty / client-price / comment
    columns, identified by their header text (handles unlabeled designation cols and
    a SECOND 'Commentaires CCTP' column that isn't the bound comment col)."""
    from .anchors import _matches
    from .config import LEFT_ROLES
    roles = ("comment", "client_pu", "unit", "quantite")
    header_rows = {layout.header_row, layout.sub_row, layout.data_start_row - 1}
    excl: set[int] = set()
    for c in range(1, first_band):
        for hr in header_rows:
            if hr < 1:
                continue
            n = grid.norm(hr, c)
            if n and any(_matches(n, LEFT_ROLES[role]) for role in roles):
                excl.add(c)
                break
    return excl


def iter_lines(grid: Grid, layout: BandLayout, file: str,
               is_option: bool = False) -> list[RawLine]:
    cols = layout.cols
    c_des = cols.get("designation")
    c_unit = cols.get("unit")
    c_qty = cols.get("quantite")
    c_cost = cols.get("cost_ht")
    c_dech = cols.get("heure_u_decharge")
    c_pose = cols.get("heure_u_pose")
    c_uth = cols.get("nombre_uth")
    c_com = cols.get("comment")
    tracker = PathTracker()
    out: list[RawLine] = []

    # Designation may appear in column A/B/C/D (multi-level indentation). Exclude the
    # unit/qty/price/comment columns by header, then scan the rest right→left so the
    # most-indented (most specific) item name wins.
    first_band = layout.first_band_col or (c_cost or grid.ncols + 1)
    excluded = _excluded_left_cols(grid, layout, first_band)
    excluded |= {c for c in (c_unit, c_qty, cols.get("client_pu"), c_com) if c}
    cand_cols = [c for c in range(1, first_band) if c not in excluded]
    primary_col = _primary_designation_col(grid, cand_cols, layout.data_start_row)
    fallback_cols = list(reversed(cand_cols))  # right→left = most-indented first

    for r in range(layout.data_start_row, grid.nrows + 1):
        designation, col_attrs = _row_designation(grid, r, primary_col, fallback_cols)
        des_norm = normalize(designation)
        if not designation:
            continue
        # junk / placeholder designations are not products
        if des_norm in {"pm", "idem", "cf", "voir", "ditto", "id"} or len(des_norm) < 3 \
                or not any(ch.isalpha() for ch in designation):
            continue
        if _is_footer(des_norm):
            continue

        cost_pr = parse_cost(grid.cell(r, c_cost)) if c_cost else None
        unit_raw = str(grid.cell(r, c_unit) or "").strip() if c_unit else ""
        qty_pr = parse_number(grid.cell(r, c_qty)) if c_qty else None
        dech_pr = parse_number(grid.cell(r, c_dech)) if c_dech else None
        pose_pr = parse_number(grid.cell(r, c_pose)) if c_pose else None
        uth_pr = parse_number(grid.cell(r, c_uth)) if c_uth else None

        has_unit = bool(unit_raw)
        has_qty = bool(qty_pr and qty_pr.value is not None)
        has_cost = bool(cost_pr and cost_pr.value is not None)
        has_labor = bool(
            (pose_pr and pose_pr.value) or (dech_pr and dech_pr.value)
        )

        # Section header → update path, skip as a data row.
        if is_section_header(designation, has_unit, has_qty, has_cost, has_labor):
            depth = numbering_depth(str(grid.cell(r, 1) or "")) or numbering_depth(designation)
            tracker.update_header(designation, depth)
            continue

        unit, unit_text, is_lump = normalize_unit(unit_raw)
        flags: list[str] = []
        if cost_pr and cost_pr.date_corruption:
            flags.append("cost_date_corruption")
        if cost_pr and cost_pr.is_error:
            flags.append("cost_formula_error")
        if unit is None and unit_raw:
            flags.append("unit_unmapped")
        if is_lump:
            flags.append("lump_unit")

        prov = {}
        if c_cost:
            prov["cost_ht"] = f"{grid.name}!{grid.col_letter(c_cost)}{r} raw={cost_pr.raw!r}"

        line = RawLine(
            file=file, sheet=grid.name, row=r,
            designation=designation,
            unit_raw=unit_text,
            unit=unit,
            quantity=qty_pr.value if qty_pr else None,
            cost_ht=cost_pr.value if cost_pr else None,
            heure_u_decharge=dech_pr.value if dech_pr else None,
            heure_u_pose=pose_pr.value if pose_pr else None,
            nombre_uth=uth_pr.value if uth_pr else None,
            comment=str(grid.cell(r, c_com) or "").strip() if c_com else "",
            col_attributes=col_attrs,
            section_path=tracker.path,
            is_option=is_option,
            flags=flags,
            provenance=prov,
        )
        out.append(line)
    return out
