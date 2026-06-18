"""CLI: extract corpus → review xlsx, plus the resumable classification workflow.

    # 1. export every distinct product that needs classification
    python -m dpgf_corpus_etl.run worklist --all-load --out classification_worklist.json
    # 2. check progress at any time (cache = the checkpoint)
    python -m dpgf_corpus_etl.run status
    # 3. build the review sheet (uses classification_cache.json automatically)
    python -m dpgf_corpus_etl.run extract --all-load --out review.xlsx

`load` lives in run_load.py (imports lib.db); `extract`/`worklist`/`status` need no DB.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

from .anchors import detect_layout
from .classify import CACHE_PATH, cache_key, classify, llm_available, load_cache
from .config import ANCHOR_MIN_CONFIDENCE, normalize
from .consultation import harvest_workbook
from .dedup import build_supplier_lookup, group_products
from .labor import compute_norms
from .models import LaborObservation
from .multiplier import find_multiplier_rows
from .review_sheet import write_review
from .rows import iter_lines
from .workbook import load_grids

_PKG = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(_PKG, "..", "..", ".."))
SRC = os.path.join(ROOT, "sources", "dpgf")
WORKLIST_PATH = os.path.join(_PKG, "classification_worklist.json")

PILOT = [
    os.path.join(SRC, "(sans tableau)_DPGF_FONTENAY-Sous-Bois_LOT 19 - ESPACES VERTS - AMENAGEMENTS EXTERIEURS _DPGF.xlsx"),
    os.path.join(SRC, "Fichier de travail V5 (VALIDE CLIENT)_ FAR_DCE_GAL_17_TN_SOC_TAB_85_0_DPGF LOT 17 - ESPACES VERTS ET BIODIVERSITE.xlsx"),
    os.path.join(SRC, "Fichier de travail Capucines Rooftop DCE_DPGF - Offre Merci Raymond V1.xlsx"),
    os.path.join(SRC, "Fichier de travail Espaces verts Courbevoie V2.xlsx - Feuil2.csv"),
]
# Per-file priced-sheet selection. Some workbooks expose several band-matching
# sheets — near-duplicate versions (51LEM: 'chantier en cours' ≡ 'DPGF Final') or a
# catalogue + a flattened copy (NEX). Pin the canonical one(s) to avoid double-count.
SHEET_OVERRIDES = {
    "NEX-77AF": {"Copie de DPGF"},                                    # complete priced doc-de-travail
    "51LEM": {"TCO Espaces Verts - DPGF Final ", "TSs Client suivi"},  # final + TS extras (not 'chantier en cours')
    "Lot N°17": {"DPGF"},
    "ROOFSCAPES 25": {"V1"},                                          # V1 base (V2 / V2-Variante are reduced/alt versions)
}


def _all_load_files() -> list[str]:
    import glob
    files = glob.glob(os.path.join(SRC, "*.xlsx")) + glob.glob(os.path.join(SRC, "*.csv"))
    files += glob.glob(os.path.join(ROOT, "V4_DOC*LOT18.csv"))
    # the `new/` drop-folder: same extraction, incl. macro-enabled .xlsm
    for ext in ("*.xlsx", "*.xlsm", "*.csv"):
        files += glob.glob(os.path.join(SRC, "new", ext))
    names = {os.path.basename(x) for x in files}
    out = []
    for f in files:
        base = os.path.basename(f)
        if base.endswith(".csv") and " - " in base and base.split(" - ")[0] + ".xlsx" in names:
            if "ESPACE VERTS" in base or "Cartouche" in base:
                continue  # redundant single-sheet export of an xlsx already in the set
        out.append(f)
    return sorted(set(out))


def _sheet_override(base: str) -> set[str] | None:
    for key, sheets in SHEET_OVERRIDES.items():
        if key in base:
            return sheets
    return None


def find_priced_sheets(grids) -> list:
    picks = []
    for name, grid in grids.items():
        if grid.nrows < 3:
            continue
        lay = detect_layout(grid)
        if lay.cost_col and lay.confidence >= ANCHOR_MIN_CONFIDENCE:
            picks.append((name, lay))
    return picks


def _process_files(files: list[str]):
    """Return (all_lines, all_suppliers, all_consult_costs, qa)."""
    all_lines, all_suppliers, all_consult_costs, qa = [], set(), [], []
    for f in files:
        base = os.path.basename(f)
        if not os.path.exists(f):
            print(f"  !! missing: {base}")
            continue
        try:
            grids = load_grids(f)
        except Exception as e:
            print(f"  !! load error {base}: {e}")
            continue
        picks = find_priced_sheets(grids)
        override = _sheet_override(base)
        if override is not None:
            picks = [(n, l) for n, l in picks if n in override]
        priced_names = {n for n, _ in picks}
        lines_this, mult_summary = [], []
        for name, lay in picks:
            is_opt = "option" in normalize(name) or "variante" in normalize(name)
            lines_this.extend(iter_lines(grids[name], lay, base, is_option=is_opt))
            mr = find_multiplier_rows(grids[name], lay.header_row, lay.cols.get("designation"))
            if mr:
                mult_summary.append(f"{name}:{sorted(mr)}")
        sups, costs = harvest_workbook(grids, priced_names)
        all_suppliers |= sups
        all_consult_costs.extend(costs)
        all_lines.extend(lines_this)
        n_cost = sum(1 for x in lines_this if x.has_cost)
        n_labor = sum(1 for x in lines_this if x.has_labor)
        warn = []
        if not picks:
            warn.append("NO_PRICED_SHEET")
        if any("cost_date_corruption" in x.flags for x in lines_this):
            warn.append("date_corruption")
        if any("unit_unmapped" in x.flags for x in lines_this):
            warn.append("unit_unmapped")
        qa.append({"file": base,
                   "priced_sheets": ", ".join(f"{n}({l.anchor_audit['cost_col_letter']})" for n, l in picks),
                   "cost_cols": ", ".join(l.anchor_audit["cost_col_letter"] for _, l in picks),
                   "confidence": ", ".join(str(l.confidence) for _, l in picks),
                   "multiplier_rows": "; ".join(mult_summary),
                   "n_lines": len(lines_this), "n_cost": n_cost, "n_labor": n_labor,
                   "warnings": ", ".join(warn)})
        print(f"  {base[:54]:54} sheets={len(picks)} lines={len(lines_this)} cost={n_cost} labor={n_labor} sup={len(sups)}")
    return all_lines, all_suppliers, all_consult_costs, qa


def _resolve(files_arg, pilot, all_load):
    if pilot:
        return PILOT
    if all_load:
        return _all_load_files()
    return files_arg


# ---------------------------------------------------------------------------
def cmd_worklist(files: list[str], out_path: str) -> None:
    all_lines, _sup, _costs, _qa = _process_files(files)
    work: dict[str, dict] = {}
    for ln in all_lines:
        if not ln.has_cost:
            continue
        k = cache_key(ln.designation)
        if not k:
            continue
        e = work.setdefault(k, {"designation": ln.designation, "units": set(), "sections": set(),
                                "comments": set(), "cost_min": None, "cost_max": None, "n": 0})
        e["n"] += 1
        if ln.unit:
            e["units"].add(ln.unit)
        if ln.section_path:
            e["sections"].add(" > ".join(ln.section_path[-2:]))
        if ln.comment:
            e["comments"].add(ln.comment[:60])
        c = float(ln.cost_ht)
        e["cost_min"] = c if e["cost_min"] is None else min(e["cost_min"], c)
        e["cost_max"] = c if e["cost_max"] is None else max(e["cost_max"], c)
    serializable = {k: {**v, "units": sorted(v["units"]), "sections": sorted(v["sections"])[:3],
                        "comments": sorted(v["comments"])[:2]} for k, v in work.items()}
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(serializable, fh, ensure_ascii=False, indent=0)
    print(f"\nWORKLIST: {len(serializable)} distinct products -> {out_path}")
    cmd_status(out_path)


def cmd_status(worklist_path: str = WORKLIST_PATH) -> None:
    try:
        work = json.load(open(worklist_path, encoding="utf-8"))
    except FileNotFoundError:
        print("no worklist yet (run `worklist` first)")
        return
    cache = load_cache()
    done = sum(1 for k in work if k in cache)
    total = len(work)
    print(f"CLASSIFICATION PROGRESS: {done}/{total} done ({100*done//max(total,1)}%), {total-done} remaining")
    print(f"  cache: {CACHE_PATH}")


def extract(files: list[str], out_path: str) -> None:
    load_cache()
    cache_n = len(load_cache())
    print(f"classification: cache={cache_n} entries | LLM fallback available={llm_available()}\n")
    all_lines, all_suppliers, all_consult_costs, qa = _process_files(files)

    from .classify import task_from_family
    for ln in all_lines:
        cls = classify(ln, backend="auto")
        # labor task = deterministic from family (kills verb/subcat duplicates)
        cls["labor_task"] = task_from_family(cls.get("family"), cls.get("subcategory"))
        ln._cls = cls  # type: ignore[attr-defined]
    supplier_lut = build_supplier_lookup(all_consult_costs)
    products = group_products(all_lines, lambda ln: ln._cls, supplier_lut)  # type: ignore[attr-defined]

    obs = [LaborObservation(
        task_name=ln._cls.get("labor_task") or "Norme par défaut (à classifier)",  # type: ignore[attr-defined]
        unit=ln.unit, heure_u_pose=ln.heure_u_pose, nombre_uth=ln.nombre_uth,
        heure_u_decharge=ln.heure_u_decharge, file=ln.file, designation=ln.designation)
        for ln in all_lines if ln.has_labor]
    norms = compute_norms(obs)
    # prune to sure norms, assign IDs, split multi-unit tasks, repoint products
    from .labor import DEFAULT_TASK, finalize_norms
    norms, task_map = finalize_norms(norms)
    for p in products:
        p.labor_task = task_map.get((p.labor_task, p.unit)) or DEFAULT_TASK

    suppliers = set(all_suppliers)
    for p in products:
        if p.supplier and p.supplier != "Fournisseur inconnu":
            suppliers.add(p.supplier)
    suppliers.add("Fournisseur inconnu")

    taxonomy: dict[tuple, int] = {}
    for p in products:
        if p.family:
            key = (p.family, p.subcategory or "À classifier", p.packaging or "Standard")
            taxonomy[key] = taxonomy.get(key, 0) + 1

    price_history = []
    for p in products:
        for c, fcell in zip(p.costs, sorted(p.source_files)):
            price_history.append({"ref": p.reference_name, "unit": p.unit or "u",
                                  "cost": float(c), "file": fcell, "flags": ",".join(p.flags)})

    write_review(out_path, products=products, labor_norms=norms, suppliers=suppliers,
                 taxonomy=taxonomy, qa=qa, price_history=price_history)
    classified = sum(1 for p in products if p.family)
    via_cache = sum(1 for ln in all_lines if ln._cls.get("method") == "claude_cache")  # type: ignore[attr-defined]
    print(f"\n=== EXTRACT SUMMARY ===")
    print(f"  products (deduped, w/ cost): {len(products)}  | classified to a family: {classified}")
    print(f"  lines classified via cache:  {via_cache}/{len(all_lines)}")
    print(f"  labor norms (tasks):         {len(norms)}")
    print(f"  suppliers:                   {len(suppliers)}")
    print(f"  taxonomy triplets:           {len(taxonomy)}")
    print(f"  review workbook ->           {out_path}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="dpgf_corpus_etl")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("extract", "worklist"):
        p = sub.add_parser(name)
        p.add_argument("--pilot", action="store_true")
        p.add_argument("--all-load", action="store_true")
        p.add_argument("--files", nargs="*", default=[])
        p.add_argument("--out", required=(name == "extract"), default=WORKLIST_PATH)
    sub.add_parser("status")
    args = ap.parse_args(argv)

    if args.cmd == "status":
        cmd_status()
        return 0
    files = _resolve(args.files, args.pilot, args.all_load)
    if not files:
        print("no files given (use --pilot / --all-load / --files)")
        return 2
    if args.cmd == "extract":
        extract(files, args.out)
    elif args.cmd == "worklist":
        cmd_worklist(files, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
