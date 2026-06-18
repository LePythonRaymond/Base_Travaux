"""Self-contained tests for the deterministic core (no pytest, no DB needed):

    python -m dpgf_corpus_etl._selftest

Covers French/€/date parsing, unit normalization, and — on a synthetic grid that
reproduces the corpus band structure — anchor column-role mapping and multiplier-row
exclusion. Also runs a quick integration check on a real corpus file if present.
"""

from __future__ import annotations

import os
from decimal import Decimal

from .anchors import detect_layout
from .frnum import parse_cost, parse_number
from .models import BandLayout
from .multiplier import find_multiplier_rows
from .rows import iter_lines
from .units import normalize_unit
from .workbook import Grid

_fail = []


def check(name, cond):
    print(f"  {'PASS' if cond else 'FAIL'}  {name}")
    if not cond:
        _fail.append(name)


def test_frnum():
    print("[frnum]")
    check("'1 662,50 €' -> 1662.50", parse_cost("1 662,50 €").value == Decimal("1662.50"))
    check("'31,00' -> 31", parse_number("31,00").value == Decimal("31.00"))
    check("plain float 25.0 -> 25", parse_cost(25.0).value == Decimal("25.00"))
    check("'-' is empty", parse_number("-").is_empty)
    check("'PM' is empty", parse_number("PM").is_empty)
    check("'#DIV/0!' is error", parse_number("#DIV/0!").is_error)
    import datetime
    pr = parse_cost(datetime.datetime(2026, 10, 2))
    check("datetime price -> date_corruption + None", pr.date_corruption and pr.value is None)
    check("US-trap '1,234.56' -> 1234.56", parse_number("1,234.56").value == Decimal("1234.56"))


def test_units():
    print("[units]")
    check("'m²' -> m2", normalize_unit("m²")[0] == "m2")
    check("'M3' -> m3", normalize_unit("M3")[0] == "m3")
    check("'Ens' -> Ft (lump)", normalize_unit("Ens")[0] == "Ft" and normalize_unit("Ens")[2])
    check("'U' -> u", normalize_unit("U")[0] == "u")
    check("unknown kept None", normalize_unit("zorg")[0] is None)


def _synthetic_grid() -> Grid:
    # Reproduce: multiplier row, band row, sub-header row, then 2 data rows.
    rows = [
        ["", "", "", "", "", "", ""],                                              # 1
        ["", "", "", "", 32, 0.1, 1.8],                                            # 2 multiplier (rate/coeffs)
        ["N°", "Désignation", "Unité", "Qté", "COÛT HUMAIN", "FOURNITURE", "PRIX CLIENT"],  # 3 band
        ["", "", "", "", "Heure/U pose", "Fourniture/U", "Prix client / U"],       # 4 sub-header
        ["1", "PLANTATIONS", "", "", "", "", ""],                                  # 5 section header
        ["1.1", "Quercus robur", "u", "3", 0.5, "120,00 €", "640,00 €"],           # 6 data
        ["1.2", "Terre végétale", "m3", "10", 0.2, "25,00 €", "86,00 €"],          # 7 data
    ]
    return Grid(name="dpgf", rows=rows)


def test_anchors_and_rows():
    print("[anchors + rows]")
    g = _synthetic_grid()
    lay = detect_layout(g)
    aud = lay.anchor_audit.get("roles", {})
    check("cost col bound to F (Fourniture/U)", lay.anchor_audit.get("cost_col_letter") == "F")
    check("pose col bound to E", aud.get("heure_u_pose") == "E")
    check("client PU (G) NOT taken as cost", lay.cost_col != 7)
    mult = find_multiplier_rows(g, lay.header_row, lay.cols.get("designation"))
    check("multiplier row 2 detected", 2 in mult)
    lines = iter_lines(g, lay, "synthetic.xlsx")
    by_name = {l.designation: l for l in lines}
    check("2 data lines (section header excluded)", len(lines) == 2)
    check("Quercus cost = 120", by_name.get("Quercus robur") and by_name["Quercus robur"].cost_ht == Decimal("120.00"))
    check("Terre végétale unit m3", by_name.get("Terre végétale") and by_name["Terre végétale"].unit == "m3")
    check("section path = PLANTATIONS", lines[0].section_path == ("PLANTATIONS",))


def test_integration():
    print("[integration: Arpajon if present]")
    root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
    f = os.path.join(root, "sources", "dpgf", "DPGF Paysage Arpajon Les Belles Vues.xlsx")
    if not os.path.exists(f):
        print("  SKIP (file not found)")
        return
    from .workbook import load_grids
    g = load_grids(f)
    name = "DPGF Paysage Arpajon Les Belles"
    lay = detect_layout(g[name])
    check("Arpajon cost col = Q", lay.anchor_audit.get("cost_col_letter") == "Q")
    lines = iter_lines(g[name], lay, "arpajon")
    terre = [l for l in lines if "terre" in l.designation.lower() and l.unit == "m3" and l.cost_ht]
    check("Arpajon terre végétale ~25 €/m3", any(l.cost_ht == Decimal("25.00") for l in terre))


def main() -> int:
    for t in (test_frnum, test_units, test_anchors_and_rows, test_integration):
        t()
    print(f"\n{'ALL PASS' if not _fail else f'{len(_fail)} FAILED: ' + ', '.join(_fail)}")
    return 1 if _fail else 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
