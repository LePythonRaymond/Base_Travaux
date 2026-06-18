"""Header-band detection + column-role mapping by *meaning* (never by letter).

The stable anchors across the whole corpus are the band labels (COÛT HUMAIN /
FOURNITURE / ... / PRIX CLIENT) and the sub-headers (Fourniture/U, Heure/U appro,
Heure/U pose, nb pers.). We locate those by text, expand merged band cells so a
band aligns over its sub-columns, then bind each role to a column. The cost column
may bind ONLY under the FOURNITURE band — a hard guard against grabbing a
post-margin client/total column.
"""

from __future__ import annotations

from difflib import SequenceMatcher

from .config import (
    ANCHOR_MIN_CONFIDENCE,
    BAND_LABELS,
    COST_BAND,
    DENY_BANDS,
    DENY_SUBHEADERS,
    FUZZY_RATIO,
    HEADER_SCAN_ROWS,
    LEFT_ROLES,
    SUBHEADER_ROLES,
)
from .models import BandLayout
from .workbook import Grid, expand_merges


def _ratio(a: str, b: str) -> float:
    return SequenceMatcher(None, a, b).ratio()


def _matches(norm: str, patterns: list[str]) -> bool:
    if not norm:
        return False
    for p in patterns:
        if len(p) <= 2:
            if norm == p:
                return True
        elif norm == p or p in norm or _ratio(norm, p) >= FUZZY_RATIO:
            return True
    return False


def _band_label_at(norm: str) -> str | None:
    for lbl in BAND_LABELS:
        if norm == lbl or lbl in norm or _ratio(norm, lbl) >= 0.9:
            return lbl
    return None


def _find_band_row(grid: Grid) -> tuple[int, int, dict[int, str]]:
    """Return (band_row, first_band_col, {col: band_label}) maximizing band hits."""
    best_row, best_hits, best_map, best_first = 0, 0, {}, 0
    cover = expand_merges(grid)
    upto = min(HEADER_SCAN_ROWS, grid.nrows)
    for r in range(1, upto + 1):
        col_band: dict[int, str] = {}
        labels: set[str] = set()
        for c in range(1, grid.ncols + 1):
            lbl = _band_label_at(grid.norm(r, c))
            if lbl:
                col_band[c] = lbl
                labels.add(lbl)
        if len(labels) > best_hits and len(labels) >= 2:
            best_row, best_hits = r, len(labels)
            # Expand each band label across its merged columns for alignment.
            expanded: dict[int, str] = {}
            for c, lbl in col_band.items():
                expanded[c] = lbl
                for (rr, cc), (r0, c0) in cover.items():
                    if rr == r and (r0, c0) == (r, c):
                        expanded[cc] = lbl
            best_map = expanded
            best_first = min(col_band) if col_band else 0
    return best_row, best_first, best_map


def _band_for_col(col: int, band_map: dict[int, str]) -> str | None:
    """Nearest band label at or to the left of this column (bands span rightward)."""
    best = None
    best_c = -1
    for c, lbl in band_map.items():
        if c <= col and c > best_c:
            best, best_c = lbl, c
    return best


def _sniff_unit_col(grid: Grid, data_rows, left_limit: int) -> int | None:
    """The column (left of the bands) whose data cells are mostly unit tokens."""
    from .units import normalize_unit
    best_c, best_hits = None, 0
    for c in range(1, left_limit):
        hits = nonempty = 0
        for r in data_rows:
            v = grid.cell(r, c)
            if v is None or (isinstance(v, str) and not v.strip()):
                continue
            nonempty += 1
            canon, _, _ = normalize_unit(v)
            if canon:
                hits += 1
        if nonempty >= 3 and hits >= 3 and hits >= 0.5 * nonempty and hits > best_hits:
            best_c, best_hits = c, hits
    return best_c


def _sniff_designation_col(grid: Grid, data_rows, left_limit: int) -> int | None:
    """The left column with the longest average free text (the designation)."""
    best_c, best_len = None, 6.0
    for c in range(1, left_limit):
        lengths = []
        for r in data_rows:
            v = grid.cell(r, c)
            if isinstance(v, str) and v.strip():
                lengths.append(len(v.strip()))
        if lengths:
            avg = sum(lengths) / len(lengths)
            if avg > best_len:
                best_c, best_len = c, avg
    return best_c


def detect_layout(grid: Grid) -> BandLayout:
    band_row, first_band_col, band_map = _find_band_row(grid)
    layout = BandLayout(sheet=grid.name, header_row=band_row, sub_row=band_row,
                        data_start_row=band_row + 1)
    if not band_row:
        layout.anchor_audit = {"reason": "no_band_row"}
        return layout

    # Sub-header row: the row near the band row with the most sub-role hits.
    best_sub, best_sub_hits, best_sub_cols = band_row, -1, {}
    for cand in (band_row, band_row + 1, band_row + 2):
        cols: dict[str, int] = {}
        for c in range(1, grid.ncols + 1):
            norm = grid.norm(cand, c)
            for role, pats in SUBHEADER_ROLES.items():
                if role in cols:
                    continue
                if _matches(norm, pats):
                    cols[role] = c
        hits = len(cols)
        if hits > best_sub_hits:
            best_sub, best_sub_hits, best_sub_cols = cand, hits, cols
    sub_row = best_sub
    cols = dict(best_sub_cols)

    # Cost column hard guard: must sit under the FOURNITURE band, never a deny band,
    # and its own sub-header must not be a derived/client label.
    cost_candidates: list[int] = []
    for c in range(1, grid.ncols + 1):
        if _matches(grid.norm(sub_row, c), SUBHEADER_ROLES["cost_ht"]):
            cost_candidates.append(c)
    chosen_cost = None
    for c in cost_candidates:
        band = _band_for_col(c, band_map)
        sub_norm = grid.norm(sub_row, c)
        if any(d in sub_norm for d in DENY_SUBHEADERS):
            continue
        if band == COST_BAND:
            chosen_cost = c
            break
        if band not in DENY_BANDS and chosen_cost is None:
            chosen_cost = c  # tentative; keep looking for a FOURNITURE-band match
    if chosen_cost is not None:
        cols["cost_ht"] = chosen_cost
    else:
        cols.pop("cost_ht", None)

    # Left-block roles live left of the first band column; their header may sit on
    # the band row, the sub row, or one row below the sub row.
    left_limit = first_band_col if first_band_col else grid.ncols + 1
    left_rows = [r for r in (band_row - 1, band_row, sub_row, sub_row + 1, sub_row + 2)
                 if 1 <= r <= grid.nrows]
    left_header_row = sub_row
    best_left_hits = -1
    for r in left_rows:
        found: dict[str, int] = {}
        for c in range(1, left_limit):
            norm = grid.norm(r, c)
            for role, pats in LEFT_ROLES.items():
                if role in found:
                    continue
                if _matches(norm, pats):
                    found[role] = c
        if len(found) > best_left_hits:
            best_left_hits = len(found)
            left_header_row = r
            for role, c in found.items():
                cols.setdefault(role, c)

    layout.sub_row = sub_row
    layout.cols = cols
    layout.first_band_col = first_band_col
    layout.data_start_row = max(sub_row, left_header_row) + 1

    # Data-sniff fallbacks when a header label was missing (e.g. a bare 'u' unit
    # header, or a designation column with no header text).
    data_rows = range(layout.data_start_row, min(layout.data_start_row + 40, grid.nrows + 1))
    if "unit" not in cols:
        c = _sniff_unit_col(grid, data_rows, left_limit)
        if c:
            cols["unit"] = c
    if "designation" not in cols:
        c = _sniff_designation_col(grid, data_rows, left_limit)
        if c:
            cols["designation"] = c

    # Confidence: how many of the 4 core roles bound, with cost_ht required.
    core = ["cost_ht", "heure_u_pose", "heure_u_decharge", "nombre_uth"]
    matched = sum(1 for r in core if r in cols)
    conf = matched / len(core)
    if "cost_ht" not in cols:
        conf *= 0.3
    layout.confidence = round(conf, 3)
    layout.anchor_audit = {
        "band_row": band_row,
        "first_band_col": first_band_col,
        "cost_col_letter": grid.col_letter(cols["cost_ht"]) if "cost_ht" in cols else None,
        "roles": {r: grid.col_letter(c) for r, c in cols.items()},
        "band_map": {grid.col_letter(c): lbl for c, lbl in sorted(band_map.items())},
    }
    return layout


def find_priced_sheet(grids: dict[str, Grid]) -> tuple[str | None, BandLayout | None]:
    """Pick the sheet with the highest-confidence priced layout (must have a cost col)."""
    best_name, best_layout, best_conf = None, None, -1.0
    for name, grid in grids.items():
        if grid.nrows < 3:
            continue
        layout = detect_layout(grid)
        if layout.cost_col and layout.confidence > best_conf:
            best_name, best_layout, best_conf = name, layout, layout.confidence
    if best_layout and best_layout.confidence >= ANCHOR_MIN_CONFIDENCE:
        return best_name, best_layout
    return best_name, best_layout  # caller checks confidence; may still inspect
