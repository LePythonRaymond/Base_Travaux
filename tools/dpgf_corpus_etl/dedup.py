"""Dedup RawLines into ProductCandidates and attach classification + supplier.

Business key aligns to the DB unique constraint (reference_name, packaging,
supplier_id): we group on canonical(designation)+unit+packaging. Same product seen
in N files keeps ONE product row (median cost) but ALL observed costs are preserved
for price_history.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from decimal import Decimal

from .config import normalize
from .models import ProductCandidate, RawLine


def canonical_name(s: str) -> str:
    return normalize(s)


def _median_cost(costs: list[Decimal]) -> Decimal | None:
    if not costs:
        return None
    vals = sorted(float(c) for c in costs)
    return Decimal(str(round(statistics.median(vals), 2)))


def build_supplier_lookup(consult_costs) -> dict[str, str]:
    """canonical(designation) -> supplier, from consultation rows."""
    lut: dict[str, str] = {}
    for supplier, designation, _cost in consult_costs:
        key = canonical_name(designation)
        if key and key not in lut:
            lut[key] = supplier
    return lut


def _match_supplier(designation: str, lut: dict[str, str]) -> str | None:
    key = canonical_name(designation)
    if key in lut:
        return lut[key]
    # partial: a consultation designation contained in the product designation (or vice-versa)
    for ck, sup in lut.items():
        if len(ck) >= 6 and (ck in key or key in ck):
            return sup
    return None


def group_products(lines: list[RawLine], classify_fn, supplier_lut: dict[str, str]
                   ) -> list[ProductCandidate]:
    groups: dict[tuple, list[RawLine]] = defaultdict(list)
    for ln in lines:
        if not ln.has_cost:
            continue  # precision-first: a product needs a real Fourniture/U cost
        cls = classify_fn(ln)
        attrs = {**cls.get("attributes", {}), **(ln.col_attributes or {})}
        pkg = attrs.get("taille") or attrs.get("conditionnement") or ""
        key = (canonical_name(ln.designation), ln.unit, normalize(pkg))
        ln._cls = cls  # type: ignore[attr-defined]
        ln._attrs = attrs  # type: ignore[attr-defined]
        ln._pkg = pkg  # type: ignore[attr-defined]
        groups[key].append(ln)

    out: list[ProductCandidate] = []
    for (cname, unit, _pkgnorm), grp in groups.items():
        rep = grp[0]
        cls = rep._cls  # type: ignore[attr-defined]
        costs = [ln.cost_ht for ln in grp if ln.cost_ht is not None]
        supplier = _match_supplier(rep.designation, supplier_lut) or "Fournisseur inconnu"
        flags = sorted({f for ln in grp for f in ln.flags})
        merged_attrs: dict = {}
        for ln in grp:
            merged_attrs.update(ln._attrs)  # type: ignore[attr-defined]
        pc = ProductCandidate(
            reference_name=rep.designation,
            unit=unit,
            packaging=rep._pkg or "",  # type: ignore[attr-defined]
            family=cls.get("family"),
            subcategory=cls.get("subcategory") or "À classifier",
            brand=cls.get("brand"),
            material=cls.get("material"),
            attributes=merged_attrs,
            supplier=supplier,
            labor_task=cls.get("labor_task"),
            cost_ht=_median_cost(costs),
            costs=costs,
            n_sources=len({ln.file for ln in grp}),
            source_files={ln.file for ln in grp},
            source_cells=[ln.provenance.get("cost_ht", f"{ln.sheet}!{ln.row}") for ln in grp],
            confidence=float(cls.get("confidence", 0.5)),
            is_option=any(ln.is_option for ln in grp),
            flags=flags,
        )
        out.append(pc)
    return out
