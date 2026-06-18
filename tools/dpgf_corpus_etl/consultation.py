"""Harvest real supplier identity + net unit costs from consultation / DC sheets.

These sheets carry a supplier name (often a 'Prix <NAME>' header or a merged top
cell) and per-line (designation, net price), sometimes with several suppliers in
side-by-side column blocks. We extract supplier NAMES (canonicalised via an alias
map) for the suppliers tab, and a (supplier, designation, net_cost) list used to
attach suppliers to products and cross-check the doc-de-travail Fourniture/U.

Sheets that are supplier-status *logs* (envoyé/RECU, no per-line price) yield
supplier names only — never products.
"""

from __future__ import annotations

from decimal import Decimal

from .config import normalize
from .frnum import parse_cost
from .workbook import Grid

_GENERIC = {
    "nom", "designation", "prix", "quantite", "qte", "unite", "total", "tva", "montant",
    "reference", "note", "notes", "taille", "dimension", "pu", "ht", "ttc", "sous total",
    "nom vegetaux", "nom de plante", "prix net ht", "prix brut ht", "valeur totale ht",
    "prix unitaire", "force", "forme", "type", "presentation", "pres. taille", "vegetaux",
    "date", "statut", "envoye", "recu", "reponse", "consultation", "fournisseur",
}
# normalized token -> canonical supplier name
_ALIAS_CANON = {
    "allavoine": "Pépinières Allavoine", "allavvoine": "Pépinières Allavoine",
    "plateau de versailles": "Pépinières du Plateau de Versailles",
    "pepinieres du plateau de versailles": "Pépinières du Plateau de Versailles",
    "versailles": "Pépinières du Plateau de Versailles", "versaille": "Pépinières du Plateau de Versailles",
    "poulain": "Pépinière Poulain", "pepiniere poulain": "Pépinière Poulain",
    "adezz": "ADEZZ", "mysteel": "MySteel", "atech": "ATECH", "carrez": "Carrez",
    "terralgreen": "Terralgreen", "lpo": "LPO", "nieuwkoop": "Nieuwkoop", "tfb": "TFB",
    "point p": "Point P", "raboni": "Raboni", "hydralians": "Hydralians", "chausson": "Chausson",
    "eurovia": "Eurovia", "colas": "Colas", "ecovegetal": "Ecovegetal", "vivara": "Vivara",
}

_PRICE_NET = ("prix net", "net ht", "prix net ht")
_PRICE_ANY = ("prix", "pu", "p.u")
_NAME_HDR = ("nom de plante", "nom vegetaux", "designation", "nom", "plante", "produit", "vegetaux")


# Tokens that, on their own, are price-header noise — never a supplier name.
_REJECT = {"ht", "ttc", "tva", "brut", "net", "unitaire", "total", "prix", "montant",
           "valeur", "quantite", "qte", "reference", "note", "euros", "euro", "eur", "pu", "u",
           "designation", "dimension", "taille", "force", "forme", "remise",
           # doc-de-travail band / column header words that must never be a supplier
           "client", "fourni", "pose", "posee", "fourniture", "livraison", "location", "loc",
           "marge", "marges", "humain", "install", "installation", "gestion", "depenses", "sup",
           "cout", "mo", "temps", "heure", "securite", "uth", "pers", "coefficient", "coef"}


def canonical_supplier(raw: str) -> str | None:
    norm = normalize(raw)
    if not norm or norm in _GENERIC:
        return None
    if norm in _ALIAS_CANON:
        return _ALIAS_CANON[norm]
    for tok, canon in _ALIAS_CANON.items():
        if tok in norm:
            return canon
    # strip a leading 'prix '
    cleaned = raw.strip()
    if normalize(cleaned).startswith("prix "):
        parts = cleaned.split(None, 1)
        cleaned = parts[1] if len(parts) > 1 else ""
    cnorm = normalize(cleaned)
    toks = [t for t in cnorm.replace("(", " ").replace(")", " ").split() if t]
    meaningful = [t for t in toks if t not in _REJECT and not t.isdigit() and len(t) > 1]
    if not meaningful:
        return None  # rejects '(€)', 'Brut HT (€)', 'Net HT (€)', 'Unitaire (€)'
    if ("€" in cleaned or "(e)" in cnorm) and not any(t in _ALIAS_CANON for t in toks):
        return None  # a price header that slipped through
    return cleaned.strip().title() if cleaned.islower() else cleaned.strip()


def _find_header_row(grid: Grid) -> int | None:
    for r in range(1, min(15, grid.nrows) + 1):
        norms = grid.row_norms(r)
        has_name = any(any(h in n for h in _NAME_HDR) for n in norms)
        has_price = any(any(h in n for h in _PRICE_ANY) for n in norms)
        if has_name and has_price:
            return r
    return None


def harvest_sheet(grid: Grid) -> tuple[set[str], list[tuple[str, str, Decimal]]]:
    """Return (supplier_names, [(supplier, designation, net_cost)]) for one sheet."""
    suppliers: set[str] = set()
    costs: list[tuple[str, str, Decimal]] = []

    # 1) supplier names: any 'Prix <NAME>' header or known token anywhere near the top.
    for r in range(1, min(8, grid.nrows) + 1):
        for c in range(1, grid.ncols + 1):
            txt = str(grid.cell(r, c) or "").strip()
            norm = normalize(txt)
            if not norm:
                continue
            if norm.startswith("prix ") and norm[5:] not in _GENERIC:
                s = canonical_supplier(txt)
                if s:
                    suppliers.add(s)
            elif norm in _ALIAS_CANON or any(tok in norm for tok in _ALIAS_CANON):
                s = canonical_supplier(txt)
                if s:
                    suppliers.add(s)

    # 2) per-line net costs (best-effort, single dominant block).
    hr = _find_header_row(grid)
    if hr:
        norms = grid.row_norms(hr)
        name_col = price_col = None
        for c, n in enumerate(norms, start=1):
            if name_col is None and any(h in n for h in _NAME_HDR) and n not in ("prix",):
                name_col = c
        # prefer an explicit NET column, else first generic price col
        for c, n in enumerate(norms, start=1):
            if any(h in n for h in _PRICE_NET):
                price_col = c
                break
        if price_col is None:
            for c, n in enumerate(norms, start=1):
                if any(h in n for h in _PRICE_ANY) and "total" not in n and "brut" not in n and "montant" not in n:
                    price_col = c
                    break
        sup = next(iter(suppliers), None) or "Fournisseur inconnu"
        if name_col and price_col:
            for r in range(hr + 1, grid.nrows + 1):
                des = str(grid.cell(r, name_col) or "").strip()
                if not des or normalize(des) in _GENERIC or "total" in normalize(des):
                    continue
                pr = parse_cost(grid.cell(r, price_col))
                if pr.value and pr.value > 0:
                    costs.append((sup, des, pr.value))
    return suppliers, costs


# Sheet-name hints for consultation/price sheets (vs supplier-status logs).
_CONSULT_HINTS = ("consultation", "dc ", "dc végétaux", "dc vegetaux", "prix veget", "récap véget",
                  "recap veget", "palette", "consult", "catalogue", "costiere", "costière", "plantes")


def is_consultation_sheet(name: str) -> bool:
    n = normalize(name)
    return any(h.strip() in n for h in _CONSULT_HINTS)


def harvest_workbook(grids: dict[str, Grid], priced_names: set[str]):
    """Harvest suppliers + costs across all non-priced consultation sheets."""
    all_suppliers: set[str] = set()
    all_costs: list[tuple[str, str, Decimal]] = []
    for name, grid in grids.items():
        if name in priced_names or grid.nrows < 2:
            continue
        if not is_consultation_sheet(name):
            continue
        sups, costs = harvest_sheet(grid)
        all_suppliers |= sups
        all_costs.extend(costs)
    return all_suppliers, all_costs
