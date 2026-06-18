"""Detect the coefficient/multiplier row ('random numbers' above the band headers).

Because data extraction starts strictly below the band+sub-header rows, and cost is
read only from the resolved Fourniture/U column, a multiplier row can never become a
product line OR leak into cost. This module exists for the QA report (so a reviewer
can confirm nothing real was dropped) and as a belt-and-suspenders flag.
"""

from __future__ import annotations

from .config import (
    MARGIN_RANGE,
    MULTIPLIER_LOOKBACK,
    RATE_RANGE,
    SECURITY_RANGE,
)
from .frnum import parse_number
from .workbook import Grid

_LABEL_HINTS = ("taux", "securite", "marge", "coef", "coefficient", "install",
                "gestion", "horaire", "ouvrier", "archi", "terre")


def _row_score(grid: Grid, r: int, designation_col: int | None) -> tuple[float, list]:
    numeric, values, label_hits = 0, [], 0
    populated = 0
    for c in range(1, grid.ncols + 1):
        v = grid.cell(r, c)
        if v is None or (isinstance(v, str) and not v.strip()):
            continue
        populated += 1
        norm = grid.norm(r, c)
        if any(h in norm for h in _LABEL_HINTS):
            label_hits += 1
        pr = parse_number(v)
        if pr.value is not None:
            numeric += 1
            values.append(float(pr.value))
    if populated == 0:
        return 0.0, []
    # fingerprint: values cluster in coefficient ranges, not realistic € costs.
    in_coef = sum(
        1 for x in values
        if RATE_RANGE[0] <= x <= RATE_RANGE[1]
        or MARGIN_RANGE[0] <= x <= MARGIN_RANGE[1]
        or SECURITY_RANGE[0] <= x <= SECURITY_RANGE[1]
    )
    has_designation = bool(
        designation_col and str(grid.cell(r, designation_col) or "").strip()
    )
    score = 0.0
    score += 0.4 * (numeric / max(populated, 1))                  # numeric density
    score += 0.4 * (in_coef / max(len(values), 1) if values else 0)  # coef fingerprint
    score += 0.2 if label_hits else 0.0                          # label sniff
    if has_designation:
        score -= 0.5                                             # real product text → not a coeff row
    return score, values


def find_multiplier_rows(grid: Grid, header_row: int, designation_col: int | None,
                         threshold: float = 0.6) -> dict[int, list]:
    """Return {row: coefficient_values} for rows just above the band that fingerprint
    as the multiplier/coefficient row."""
    out: dict[int, list] = {}
    lo = max(1, header_row - MULTIPLIER_LOOKBACK)
    for r in range(lo, header_row):
        score, values = _row_score(grid, r, designation_col)
        if score >= threshold and values:
            out[r] = values
    return out
