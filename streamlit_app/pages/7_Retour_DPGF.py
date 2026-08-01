"""Retour DPGF — reverse-ingestion of validated client prices.

The "validation loop" feature: when a customer accepts a quoted DPGF,
Vincent re-uploads the filled xlsx here. The system parses each line,
matches it back to an existing product, and logs the quoted PU as a
`price_history` row with `source='dpgf_return'` — capturing what the
market accepted, on a real project.

3-step wizard (driven by session_state.dpgf_step):
  1. Dépôt    — file_uploader for the .xlsx
  2. Matching — per-row review: confirm or correct the matched product
                (top-5 fuzzy candidates + "+ créer un produit")
  3. Valider  — single transaction → price_history rows
"""

from __future__ import annotations

import hashlib
import io
import json
import os
from collections import defaultdict
from datetime import datetime
from typing import Any

import streamlit as st
from sqlalchemy import text

from lib.auth import require_login
from lib.branding import (
    apply_branding,
    hf_chip,
    hf_dot,
    hf_kpi,
    hf_stepper,
    render_footer,
    render_header,
    render_sidebar_brand,
)
from lib.db import fetch_all, fetch_one, transaction
from lib.dpgf import (
    DpgfFormatError,
    DpgfLine,
    parse_dpgf,
    parse_project_meta,
    stats as dpgf_stats,
)
from lib.matcher import find_similar_products
from lib.pickers import (
    FAMILY_NEW_ID,
    LABOR_NEW_ID,
    SUPPLIER_NEW_ID,
    ensure_taxonomy,
    quick_create_labor_norm,
    render_labor_norm_picker,
    render_supplier_picker,
    render_taxonomy_picker,
    resolve_family,
    resolve_supplier,
)

st.set_page_config(page_title="Retour DPGF — Merci Raymond", page_icon="🌳", layout="wide")
require_login()
apply_branding()
render_sidebar_brand()


# ============================================================================
#  Session state
# ============================================================================
S = st.session_state
S.setdefault("dpgf_step", 0)              # 0 = dépôt, 1 = matching, 2 = valider
S.setdefault("dpgf_lines", [])             # list[DpgfLine]
S.setdefault("dpgf_filename", None)
S.setdefault("dpgf_project_name", "")
S.setdefault("dpgf_matches", {})           # row_index -> {candidates, selected_id, method}
S.setdefault("dpgf_clarify", {})           # row_index -> clarification dict (create_new lines)
S.setdefault("dpgf_raw_bytes", None)       # the uploaded xlsx, kept for persistence
S.setdefault("dpgf_file_sha", None)
S.setdefault("dpgf_meta", {})              # parse_project_meta() result


def _reset() -> None:
    for k in (
        "dpgf_step", "dpgf_lines", "dpgf_filename", "dpgf_project_name",
        "dpgf_matches", "dpgf_clarify", "dpgf_raw_bytes", "dpgf_file_sha",
        "dpgf_meta",
    ):
        S.pop(k, None)


# ============================================================================
#  Lookups + clarification helpers (Phase 4)
# ============================================================================
def _build_lookups() -> dict[str, Any]:
    """One round-trip to build every dropdown source the clarification block
    needs (taxonomy cascade, labor norms, suppliers), plus name→id reverse
    maps so a parsed DPGF line can pre-select its famille / fournisseur."""
    fam_rows = fetch_all("SELECT id, name FROM product_families ORDER BY name")
    tax_rows = fetch_all(
        "SELECT family_id, subcategory, packaging FROM product_taxonomy "
        "ORDER BY family_id, subcategory, packaging"
    )
    lab_rows = fetch_all(
        "SELECT id, task_name, unit_type FROM labor_norms ORDER BY task_name"
    )
    sup_rows = fetch_all("SELECT id, name FROM suppliers ORDER BY name")

    family_by_id = {r["id"]: r["name"] for r in fam_rows}
    family_id_by_name = {r["name"].strip().lower(): r["id"] for r in fam_rows}
    subs_lookup: dict[int, list[str]] = defaultdict(list)
    packs_lookup: dict[tuple[int, str], list[str]] = defaultdict(list)
    for r in tax_rows:
        fid, sub, pack = r["family_id"], r["subcategory"], r["packaging"]
        if sub and sub not in subs_lookup[fid]:
            subs_lookup[fid].append(sub)
        if pack and pack not in packs_lookup[(fid, sub)]:
            packs_lookup[(fid, sub)].append(pack)

    return {
        "families": [{"id": r["id"], "name": r["name"]} for r in fam_rows],
        "family_by_id": family_by_id,
        "family_id_by_name": family_id_by_name,
        "subs_lookup": subs_lookup,
        "packs_lookup": packs_lookup,
        "labor_norms": [{"id": r["id"], "task_name": r["task_name"]} for r in lab_rows],
        "labor_by_id": {
            r["id"]: f"{r['task_name']} · {r['unit_type']}" for r in lab_rows
        },
        "suppliers": [{"id": r["id"], "name": r["name"]} for r in sup_rows],
        "supplier_by_id": {r["id"]: r["name"] for r in sup_rows},
        "supplier_id_by_name": {r["name"].strip().lower(): r["id"] for r in sup_rows},
    }


def _auto_creatable(line: DpgfLine) -> bool:
    """A create_new line we can build with NO clarification: the picker gave
    a complete triplet (famille / sous-cat / conditionnement) + a name."""
    return bool(
        line.famille and line.sous_cat and line.conditionnement
        and (line.reference_name or line.client_designation)
    )


def _is_product_line(line: DpgfLine) -> bool:
    """A real line to review/ingest — Vincent picked a product (AG) and/or the
    row carries a price (AQ cost or BC client PU). Filters out the hundreds of
    empty template rows + section-header rows so the matching table only shows
    actual products."""
    return bool(
        line.picker
        or (line.pu_client and line.pu_client > 0)
        or (line.pu_fourniture and line.pu_fourniture > 0)
    )


def _render_clarify_block(ri: int, line: DpgfLine, clar: dict, lk: dict) -> None:
    """Render the inline clarification pickers for one create_new line and
    write their current values back into `clar` (session-state dict)."""
    tx = render_taxonomy_picker(
        key_prefix=f"clar_{ri}",
        families=lk["families"],
        family_by_id=lk["family_by_id"],
        subs_lookup=lk["subs_lookup"],
        packs_lookup=lk["packs_lookup"],
        initial_family_id=lk["family_id_by_name"].get((line.famille or "").strip().lower()),
        initial_subcategory=line.sous_cat,
        initial_packaging=line.conditionnement,
    )
    sp_c, lb_c = st.columns(2)
    with sp_c:
        sp = render_supplier_picker(
            key_prefix=f"clar_{ri}",
            suppliers=lk["suppliers"],
            supplier_by_id=lk["supplier_by_id"],
            initial_supplier_id=lk["supplier_id_by_name"].get((line.fournisseur or "").strip().lower()),
            initial_name=line.fournisseur,
        )
    with lb_c:
        nb = render_labor_norm_picker(
            key_prefix=f"clar_{ri}",
            labor_norms=lk["labor_norms"],
            labor_by_id=lk["labor_by_id"],
            default_unit=(line.unit or "u"),
            label="Norme de pose *",
        )
    default_cost = float(clar.get("cost") or line.pu_fourniture or line.pu_client or 0.0)
    cost = st.number_input(
        "Coût HT fournisseur / unité (col. AQ) *",
        min_value=0.0, value=default_cost, step=0.01, format="%.2f",
        key=f"clar_{ri}_cost",
        help="Notre coût d'achat réel — devient le cost_ht du produit créé.",
    )
    clar.update({
        "family_id": tx["family_id"],
        "new_family_name": tx["new_family_name"],
        "subcategory": tx["subcategory"],
        "packaging": tx["packaging"],
        "supplier_id": sp["supplier_id"],
        "supplier_new_name": sp["new_name"],
        "labor_norm_id": nb["labor_norm_id"],
        "labor_new_name": nb["new_name"],
        "labor_new_unit": nb["new_unit"],
        "labor_new_pose_hours": nb["new_pose_hours"],
        "cost": cost,
    })


def _clarify_ready(clar: dict) -> bool:
    """True when a clarification dict has everything needed to create a clean
    product (taxonomy + supplier + norme + a positive cost)."""
    if not clar:
        return False
    fam_ok = clar.get("family_id") != FAMILY_NEW_ID or bool((clar.get("new_family_name") or "").strip())
    sub_ok = bool((clar.get("subcategory") or "").strip())
    pack_ok = bool((clar.get("packaging") or "").strip())
    sup_ok = clar.get("supplier_id") != SUPPLIER_NEW_ID or bool((clar.get("supplier_new_name") or "").strip())
    lab_ok = clar.get("labor_norm_id") != LABOR_NEW_ID or bool((clar.get("labor_new_name") or "").strip())
    cost_ok = float(clar.get("cost") or 0) > 0
    return fam_ok and sub_ok and pack_ok and sup_ok and lab_ok and cost_ok


def _line_will_create(line: DpgfLine, clar: dict) -> bool:
    """Whether a create_new line is ready to be written: either auto-creatable
    from the parsed picker, or fully clarified."""
    if clar.get("override"):
        return _clarify_ready(clar)
    if _auto_creatable(line):
        return True
    return _clarify_ready(clar)


# ============================================================================
#  Header + stepper
# ============================================================================
hdr_l, hdr_r = st.columns([3, 2])
with hdr_l:
    sub = "ingestion inverse · validation des prix"
    breadcrumb = None
    if S.get("dpgf_filename"):
        breadcrumb = (
            f"{S.get('dpgf_project_name') or 'Projet sans nom'} "
            f"<span class='sep'>·</span> {S['dpgf_filename']} "
            f"<span class='sep'>·</span> {len(S.get('dpgf_lines') or [])} lignes"
        )
    render_header(title="Retour DPGF", subtitle=sub, breadcrumb=breadcrumb)
with hdr_r:
    hf_stepper(["Dépôt", "Matching", "Valider"], current_idx=S["dpgf_step"])


# ============================================================================
#  Step 1 — Dépôt
# ============================================================================
if S["dpgf_step"] == 0:
    # Hard requirement banner, one line: the parser only understands the Merci
    # Raymond DPGF template. The detail (expected columns + what the commit
    # actually does) lives in the expander below, so the landing screen is calm.
    st.markdown(
        '<div class="hf-card danger" style="margin:4px 0 10px 0;padding:10px 14px">'
        '<div class="hf-row" style="gap:9px;align-items:center">'
        '<span style="font-size:16px;line-height:1">⚠</span>'
        '<span style="font-weight:600;font-size:13px;color:var(--hf-ink)">'
        "Format requis · MR DPGF Template (Master)</span>"
        "</div></div>",
        unsafe_allow_html=True,
    )

    with st.expander("Comment le fichier est traité", expanded=False):
        st.markdown(
            '<p class="hf-muted" style="margin:2px 0 8px 0;font-size:12.5px">'
            "À l'enregistrement, pour chaque ligne le système :</p>"
            '<ul class="hf-muted" style="font-size:12.5px;line-height:1.7;margin:0 0 12px 18px;padding:0">'
            "<li>identifie le <b>produit existant</b> correspondant (ou propose d'en créer un nouveau),</li>"
            "<li>met à jour le <b>coût HT</b> du produit avec le prix fournisseur du modèle (col. AQ) — c'est notre coût d'achat réel,</li>"
            "<li>enregistre le <b>PU client accepté</b> (col. BC) dans l'historique du produit avec un chip rouge "
            "<code>dpgf_return</code> — <b>uniquement si</b> ce PU diffère du PU fournisseur de la ligne. "
            "Quand AQ = BC, la mise à jour de coût suffit ; sinon, on garde trace du prix de vente distinct.</li>"
            "<li>pour les lignes sans correspondance : crée le produit avec les pièces extraites "
            "de la chaîne col. AG — si le triplet est incomplet, le produit part en <b>À classifier</b>.</li>"
            "</ul>"
            '<p class="hf-muted" style="margin:0 0 6px 0;font-size:12.5px">'
            "Le classeur doit contenir un onglet <b>« DPGF »</b> (ou « DPGF Master » / "
            "« DPGF Template ») ; tu peux téléverser le classeur complet, les autres "
            "onglets sont ignorés. Colonnes lues :</p>"
            '<ul class="hf-muted" style="font-size:12px;line-height:1.6;margin:0 0 4px 18px;padding:0">'
            "<li><b>Zone client (A–Z)</b> : <code>B</code>/<code>C</code>/<code>E</code> par défaut "
            "(désignation / unité / quantité) — re-mappables via <code>Col_Designation</code>, "
            "<code>Col_Unite</code>, <code>Col_Quantite</code>.</li>"
            "<li><b>Notre zone (AA+)</b> : <code>AC</code> quantité mirroir · <code>AG</code> chaîne produit "
            "(Famille — Sous-cat — Référence — Cond.)</li>"
            "<li><code>AI</code> fournisseur · <code>AQ</code> prix fourniture / unité · "
            "<code>BC</code> PU client accepté</li>"
            "</ul>",
            unsafe_allow_html=True,
        )

    project_name = st.text_input(
        "Nom du projet",
        value=S.get("dpgf_project_name", ""),
        placeholder="Villa Picpus, Hôtel Paradis, …",
        key="dpgf_project_input",
    )
    uploaded = st.file_uploader("DPGF remplie (.xlsx)", type=["xlsx"], key="dpgf_upload")
    if uploaded is not None:
        S["dpgf_filename"] = uploaded.name
        S["dpgf_project_name"] = project_name.strip()
        raw = uploaded.getvalue()
        S["dpgf_raw_bytes"] = raw
        S["dpgf_file_sha"] = hashlib.sha256(raw).hexdigest()
        try:
            lines = parse_dpgf(raw)
        except DpgfFormatError as exc:
            # Expected-user-error: the workbook isn't a Merci Raymond DPGF.
            # Surface as a clean red callout, not a stack-trace-flavoured
            # st.error.
            st.markdown(
                f"""
                <div class="hf-card danger" style="margin:8px 0 12px 0">
                  <div class="hf-row" style="gap:10px;align-items:flex-start">
                    <span style="font-size:18px;line-height:1">⛔</span>
                    <div>
                      <div style="font-weight:600;font-size:13.5px;color:var(--hf-ink)">
                        Fichier refusé · format DPGF Merci Raymond non détecté
                      </div>
                      <div class="hf-muted" style="font-size:12px;margin-top:4px;line-height:1.55">
                        {exc}
                      </div>
                    </div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            # Clear the upload so the user can drop a new file without
            # reloading the page.
            S["dpgf_filename"] = None
            st.stop()
        except Exception as exc:
            st.error(f"Impossible de parser le fichier : {exc}")
            st.stop()
        if not lines:
            st.warning(
                "Aucune ligne exploitable trouvée. Vérifiez que le fichier est bien "
                "un DPGF Merci Raymond rempli (la colonne AG « Produit » doit contenir "
                "des références sélectionnées par Vincent)."
            )
            st.stop()

        S["dpgf_lines"] = lines

        # Project-level rentability + coefficient snapshot (never raises).
        try:
            S["dpgf_meta"] = parse_project_meta(raw)
        except Exception:  # noqa: BLE001
            S["dpgf_meta"] = {"coefficients": {}, "computed": {}, "recap": {}}
        S["dpgf_clarify"] = {}

        # Pre-compute matching candidates
        st.markdown(
            '<div class="hf-card" style="padding:14px">'
            f'<b>{len(lines)} ligne(s)</b> détectée(s) — calcul des correspondances…</div>',
            unsafe_allow_html=True,
        )
        _PROD_COLS = (
            "SELECT p.id, p.reference_name, pf.name AS family_name, "
            "p.subcategory, p.packaging, p.cost_ht, s.name AS supplier_name "
            "FROM products p "
            "JOIN product_families pf ON pf.id = p.family_id "
            "JOIN suppliers s         ON s.id = p.supplier_id "
        )
        matches: dict[int, dict[str, Any]] = {}
        progress = st.progress(0.0)
        for i, line in enumerate(lines):
            progress.progress((i + 1) / max(1, len(lines)))
            # Only real product lines enter the matching table — skip the
            # hundreds of empty / section-header rows.
            if not _is_product_line(line):
                continue

            cands: list[dict[str, Any]] = []
            method: str | None = None

            # ── 1. EXACT by hidden product id (col BE) — strongest signal.
            if line.product_id:
                byid = fetch_one(
                    _PROD_COLS + "WHERE p.is_active AND p.id = :id LIMIT 1",
                    {"id": line.product_id},
                )
                if byid:
                    cands.append(dict(byid))
                    method = "id"

            # ── 2a. TAXONOMY exact — full identity from the picker:
            #         famille + sous-catégorie + conditionnement + référence,
            #         narrowed by FOURNISSEUR when the 5-part chaîne carries
            #         one (two suppliers can sell the same 4-tuple: the
            #         supplier is what picks the right row). Falls back to the
            #         supplier-less match for 4-part legacy strings.
            if not cands and line.reference_name and line.famille and line.sous_cat:
                _tax_sql = (
                    _PROD_COLS
                    + "WHERE p.is_active "
                    "  AND lower(pf.name) = lower(:fam) "
                    "  AND lower(p.subcategory) = lower(:sub) "
                    "  AND p.packaging = :pkg "
                    "  AND p.reference_name = :ref "
                )
                _tax_params = {
                    "fam": line.famille,
                    "sub": line.sous_cat,
                    "pkg": line.conditionnement or "",
                    "ref": line.reference_name,
                }
                tax = None
                if line.fournisseur:
                    tax = fetch_one(
                        _tax_sql + "  AND lower(s.name) = lower(:sup) LIMIT 1",
                        {**_tax_params, "sup": line.fournisseur.strip()},
                    )
                if tax is None:
                    tax = fetch_one(_tax_sql + "LIMIT 1", _tax_params)
                if tax:
                    cands.append(dict(tax))
                    method = "taxonomy"

            # ── 2b. TAXONOMY fallback — référence + conditionnement only
            #         (picker without famille/sous-cat). Still an exact-ish
            #         taxonomic match, just less specific.
            if not cands and line.reference_name:
                exact = fetch_one(
                    _PROD_COLS
                    + "WHERE p.is_active AND p.reference_name = :ref "
                    "  AND p.packaging = :pkg LIMIT 1",
                    {"ref": line.reference_name, "pkg": line.conditionnement or ""},
                )
                if exact:
                    cands.append(dict(exact))
                    method = "taxonomy"

            # ── 3. FUZZY — only a SUGGESTION to verify, never auto-confident.
            fuzzy_text = line.client_designation or line.reference_name or ""
            fuzzy = find_similar_products(fuzzy_text, top_k=5)
            seen = {c["id"] for c in cands}
            for c in fuzzy:
                if c["id"] not in seen:
                    cands.append(c)
                    seen.add(c["id"])
            if method is None and cands:
                method = "fuzzy"

            # Default selection:
            #   • id / taxonomy  → confident existing product, auto-selected.
            #   • fuzzy only     → top suggestion pre-selected but flagged
            #                      "à vérifier" (user confirms before write).
            #   • nothing + a PU → "create_new" (surfaces clarification).
            if method in ("id", "taxonomy"):
                selected: Any = cands[0]["id"]
            elif cands:
                selected = cands[0]["id"]
            elif line.pu_client and line.pu_client > 0:
                selected = "create_new"
            else:
                selected = "create_new" if line.picker else None

            matches[line.row_index] = {
                "candidates": cands[:5],
                "selected_id": selected,
                "method": method,
            }
        S["dpgf_matches"] = matches
        progress.empty()
        S["dpgf_step"] = 1
        st.rerun()


# ============================================================================
#  Step 2 — Matching review
# ============================================================================
elif S["dpgf_step"] == 1:
    lines: list[DpgfLine] = S["dpgf_lines"]
    matches = S["dpgf_matches"]
    line_by_ri = {ln.row_index: ln for ln in lines}

    # Dropdown sources for the inline clarification blocks (one round-trip).
    lk = _build_lookups()

    _METHOD_BADGE = {
        "id": '<span class="hf-chip ok" style="font-size:9.5px;padding:1px 6px">🔗 id</span>',
        "taxonomy": '<span class="hf-chip ok" style="font-size:9.5px;padding:1px 6px">≈ taxonomie</span>',
        "fuzzy": '<span class="hf-chip warn" style="font-size:9.5px;padding:1px 6px">≈ proche</span>',
    }
    # State → (accent colour, faint tint, dot, label). Drives the per-row
    # colour band so existing / à-vérifier / nouveau / ignorée read instantly.
    _CAT_STYLE = {
        "existing": ("#2e7d52", "rgba(46,125,82,.06)", "ok",   "produit existant"),
        "verify":   ("#2f6f9f", "rgba(47,111,159,.08)", "warn", "à vérifier"),
        "new":      ("#c4623d", "rgba(196,98,61,.08)",  "warn", "nouveau produit"),
        "skip":     ("#9a9a9a", "transparent",          "bad",  "ignorée"),
    }

    def _classify(mm: dict) -> str:
        sel, meth = mm.get("selected_id"), mm.get("method")
        if sel is None:
            return "skip"
        if sel == "create_new":
            return "new"
        if meth in ("id", "taxonomy"):
            return "existing"
        return "verify"  # an int chosen from a fuzzy suggestion

    product_lines = [ln for ln in lines if ln.row_index in matches]

    st.markdown(
        '<p class="hf-muted" style="font-size:12.5px;margin:2px 0 8px 0;max-width:780px">'
        "Chaque ligne du DPGF a été rapprochée d'un produit du catalogue : "
        "<span style='color:#2e7d52;font-weight:600'>vert = reconnu</span> · "
        "<span style='color:#2f6f9f;font-weight:600'>bleu = à confirmer</span> (nom proche, "
        "vérifie que c'est le bon produit) · "
        "<span style='color:#c4623d;font-weight:600'>orange = sera créé</span>.</p>",
        unsafe_allow_html=True,
    )

    # Per-category counts (drive the view filter labels).
    cat_counts = {"existing": 0, "verify": 0, "new": 0, "skip": 0}
    for mm in matches.values():
        cat_counts[_classify(mm)] += 1

    view_defs = [
        ("Tous", None), ("Existants", "existing"), ("À vérifier", "verify"),
        ("À créer", "new"), ("Ignorées", "skip"),
    ]
    opts = [
        f"{lbl} ({len(product_lines) if cat is None else cat_counts[cat]})"
        for lbl, cat in view_defs
    ]
    fcol1, fcol2 = st.columns([4, 1])
    with fcol1:
        view = st.radio(
            "Afficher", options=opts, horizontal=True,
            label_visibility="collapsed", key="dpgf_view",
        )
    with fcol2:
        if st.button("↺ recommencer", key="dpgf_reset_top", use_container_width=True):
            _reset()
            st.rerun()
    flt = next((cat for (lbl, cat), o in zip(view_defs, opts) if o == view), None)

    st.markdown(
        '<h2 class="hf-h2" style="margin-top:6px">Tableau de rapprochement</h2>',
        unsafe_allow_html=True,
    )

    def _eur(v):
        return f"{v:,.2f} €".replace(",", " ") if v else "—"

    shown = 0
    for line in product_lines:
        ri = line.row_index
        m = matches[ri]
        if flt and _classify(m) != flt:
            continue
        shown += 1
        cands: list[dict[str, Any]] = m.get("candidates") or []

        # Options: top-5 candidates + "create_new" + "(skip)"
        option_ids: list[Any] = [c["id"] for c in cands]
        if "create_new" not in option_ids:
            option_ids = option_ids + ["create_new"]
        option_ids = [None] + option_ids
        sel_idx = option_ids.index(m.get("selected_id")) if m.get("selected_id") in option_ids else 0

        def _label_for(cid, _cands=cands):
            if cid is None:
                return "(ignorer cette ligne)"
            if cid == "create_new":
                return "+ créer un produit"
            c = next((x for x in _cands if x["id"] == cid), None)
            if not c:
                return f"produit #{cid}"
            return f"#{c['id']} {c['reference_name']} · {c['family_name']} · {c['packaging']}"

        cont = st.container(border=True)
        with cont:
            row_l, row_r, row_btn = st.columns([5, 2, 1.5])
            # Render the selector FIRST so the colour band reflects the
            # current choice on the same run (no one-rerun lag).
            with row_r:
                new_sel = st.selectbox(
                    "Produit correspondant",
                    options=option_ids,
                    index=sel_idx,
                    format_func=_label_for,
                    key=f"dpgf_match_{ri}",
                    label_visibility="collapsed",
                )
                m["selected_id"] = new_sel
            with row_btn:
                if st.button("↻ re-matcher", key=f"dpgf_redo_{ri}", use_container_width=True):
                    fuzzy_text = line.client_designation or line.reference_name or ""
                    fuzzy = find_similar_products(fuzzy_text, top_k=10)
                    m["candidates"] = fuzzy[:5]
                    m["selected_id"] = fuzzy[0]["id"] if fuzzy else None
                    st.rerun()

            cat = _classify(m)
            border, tint, dot_state, cat_label = _CAT_STYLE[cat]
            method = m.get("method")
            badge = _METHOD_BADGE.get(method, "") if cat in ("existing", "verify") else ""

            # Money facts, COST FIRST: the primary event of a re-ingestion is
            # "does the catalogue cost change?". Show current DB cost → new AQ
            # with an explicit update signal. The PU client (BC — recomputed by
            # formula from AQ, so it virtually always differs) is secondary
            # bookkeeping and reads last, muted. Only the zero-margin anomaly
            # keeps a loud warning.
            qty = (
                f"{line.quantity:,.2f}".rstrip("0").rstrip(",").replace(",", " ")
                if line.quantity is not None else "—"
            )
            sel_c = next((c for c in cands if c["id"] == new_sel), None)

            if line.pu_fourniture:
                _old = sel_c.get("cost_ht") if sel_c else None
                if _old is not None and abs(float(_old) - float(line.pu_fourniture)) > 0.005:
                    cost_txt = (
                        f'coût <span style="text-decoration:line-through">{_eur(_old)}</span> '
                        f'→ <b style="color:#2e7d52">{_eur(line.pu_fourniture)}</b> '
                        f'<span style="color:#2e7d52;font-weight:600">✎ sera mis à jour</span>'
                    )
                elif _old is not None:
                    cost_txt = f'coût <b style="color:var(--hf-ink)">{_eur(line.pu_fourniture)}</b> (inchangé)'
                else:
                    cost_txt = f'coût <b style="color:var(--hf-ink)">{_eur(line.pu_fourniture)}</b>'
            else:
                cost_txt = 'coût — <span style="color:var(--hf-muted)">(pas de Fourniture/U → catalogue intact)</span>'

            warn_txt = ""
            if line.pu_client and line.pu_fourniture and not line.client_price_differs_from_supplier:
                warn_txt = '<span style="color:#c4623d">⚠ PU client = coût (marge nulle)</span>'
            if new_sel == "create_new":
                target = (
                    f'→ <b>nouveau</b> · {line.famille or "?"} · '
                    f'{line.sous_cat or "?"} · {line.conditionnement or "?"}'
                )
            elif sel_c:
                target = (
                    f'→ #{sel_c["id"]} <b>{sel_c["reference_name"]}</b> · '
                    f'{sel_c["family_name"]} · {sel_c["packaging"]}'
                )
            elif new_sel is None:
                target = "→ <i>ligne ignorée</i>"
            else:
                target = f"→ produit #{new_sel}"

            with row_l:
                st.markdown(
                    f"""
                    <div style="border-left:3px solid {border};background:{tint};
                                padding:6px 10px;border-radius:4px">
                      <div class="hf-row" style="gap:8px;align-items:center">
                        {hf_dot(dot_state)}
                        <span style="font-family:JetBrains Mono,monospace;font-size:10px;color:var(--hf-muted)">L{ri}</span>
                        <span style="font-size:13px;color:var(--hf-ink);font-weight:600;min-width:0">{line.client_designation or '(sans désignation)'}</span>
                        {badge}
                        <span class="hf-chip" style="font-size:9px;padding:1px 6px;background:{tint};color:{border};border:1px solid {border}">{cat_label}</span>
                      </div>
                      <div class="hf-muted" style="font-size:11px;margin-top:3px">
                        {cost_txt}{(' · ' + warn_txt) if warn_txt else ''}
                      </div>
                      <div style="font-size:11px;color:var(--hf-body);margin-top:2px">{target}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
                # Proposition A : tout le secondaire vit ici, à la demande.
                with st.expander("détails", expanded=False):
                    _mg_txt = "—"
                    if line.pu_client and line.pu_fourniture:
                        _mg = line.pu_client - line.pu_fourniture
                        _mg_pct = (_mg / line.pu_fourniture * 100) if line.pu_fourniture else 0
                        _mg_txt = f"+{_eur(_mg)} ({_mg_pct:.0f} %)"
                    _bd = line.breakdown or {}
                    _hours_bits = []
                    if _bd.get("h_appro_u"):
                        _hours_bits.append(f"appro {_bd['h_appro_u']:.2f} h/u")
                    if _bd.get("h_pose_u"):
                        _hours_bits.append(f"pose {_bd['h_pose_u']:.2f} h/u")
                    if _bd.get("nb_uth"):
                        _hours_bits.append(f"{_bd['nb_uth']:.0f} pers.")
                    _rows_html = "".join(
                        f'<div class="hf-row hf-between" style="font-size:11.5px;padding:2px 0">'
                        f'<span class="hf-muted">{_lbl}</span><span>{_val}</span></div>'
                        for _lbl, _val in [
                            ("Quantité", qty),
                            ("PU client (prix de vente)", _eur(line.pu_client)),
                            ("Marge (PU client − coût)", _mg_txt),
                            ("Temps de pose", " · ".join(_hours_bits) if _hours_bits else "—"),
                            ("Fournisseur (feuille)", line.fournisseur or "—"),
                        ]
                    )
                    st.markdown(_rows_html, unsafe_allow_html=True)

            # ── Inline clarification for create_new lines ──────────────
            if new_sel == "create_new":
                clar = S["dpgf_clarify"].setdefault(ri, {})
                auto = _auto_creatable(line)
                if auto:
                    # Surface any SILENT taxonomy creation: if the famille (or
                    # its sous-cat / conditionnement) doesn't exist in the DB
                    # yet, the commit will create it — say so up front instead
                    # of letting a typo silently become a new family.
                    _new_bits = []
                    _fid_known = lk["family_id_by_name"].get((line.famille or "").strip().lower())
                    if not _fid_known:
                        _new_bits.append(f"famille « {line.famille} »")
                    else:
                        if line.sous_cat and line.sous_cat not in lk["subs_lookup"].get(_fid_known, []):
                            _new_bits.append(f"sous-catégorie « {line.sous_cat} »")
                        elif line.conditionnement and line.conditionnement not in lk["packs_lookup"].get(
                            (_fid_known, line.sous_cat), []
                        ):
                            _new_bits.append(f"conditionnement « {line.conditionnement} »")
                    if _new_bits:
                        st.markdown(
                            '<div class="hf-row" style="gap:6px;margin:2px 0 4px 0">'
                            + hf_chip("création : " + " + ".join(_new_bits), "warn")
                            + '<span class="hf-muted" style="font-size:10.5px">sera ajouté au '
                            "référentiel à l'enregistrement — vérifie l'orthographe</span></div>",
                            unsafe_allow_html=True,
                        )
                    exp_label = (
                        f"➕ création auto · {line.famille} · {line.sous_cat} · "
                        f"{line.conditionnement} — corriger ?"
                    )
                    with st.expander(exp_label, expanded=bool(clar.get("override"))):
                        override = st.checkbox(
                            "Corriger la taxonomie / fournisseur / norme",
                            value=bool(clar.get("override")),
                            key=f"clar_ovr_{ri}",
                        )
                        clar["override"] = override
                        if override:
                            _render_clarify_block(ri, line, clar, lk)
                else:
                    clar["override"] = True
                    ready = _clarify_ready(clar)
                    st.markdown(
                        '<div class="hf-muted" style="font-size:11px;margin:2px 0 4px 0">'
                        + ("✅ ligne complétée" if ready else
                           "⚠ ligne incomplète — précisez pour créer le produit :")
                        + "</div>",
                        unsafe_allow_html=True,
                    )
                    _render_clarify_block(ri, line, clar, lk)

    if shown == 0:
        st.markdown(
            '<div class="hf-muted" style="font-size:12px;padding:16px 4px">'
            "Aucune ligne dans ce filtre.</div>",
            unsafe_allow_html=True,
        )

    # ── Bottom action bar (fresh counts after the clarify widgets ran) ──
    create_ris = [ri for ri, mm in matches.items() if mm.get("selected_id") == "create_new"]
    n_existing = sum(1 for mm in matches.values() if _classify(mm) == "existing")
    n_verify = sum(1 for mm in matches.values() if _classify(mm) == "verify")
    n_matched = n_existing + n_verify
    n_create_ready = sum(
        1 for ri in create_ris
        if _line_will_create(line_by_ri[ri], S["dpgf_clarify"].get(ri, {}))
    )
    n_blocking = len(create_ris) - n_create_ready
    n_skip = sum(1 for mm in matches.values() if mm.get("selected_id") is None)
    n_writable = n_matched + n_create_ready

    st.markdown(
        f'<div class="hf-row" style="gap:8px;margin:12px 0 8px 0;flex-wrap:wrap">'
        f'{hf_chip(f"✅ {n_existing} existants confirmés", "ok")}'
        + (hf_chip(f"🔵 {n_verify} à vérifier", "warn") if n_verify else "")
        + f'{hf_chip(f"➕ {n_create_ready} à créer", "warn")}'
        f'{hf_chip(f"⏭ {n_skip} ignorées", "ghost")}'
        + (hf_chip(f"⛔ {n_blocking} à compléter", "danger") if n_blocking else "")
        + "</div>",
        unsafe_allow_html=True,
    )
    if n_verify:
        st.markdown(
            '<p class="hf-muted" style="font-size:11.5px;margin:0 0 6px 0">'
            f"🔵 {n_verify} ligne(s) reposent sur un rapprochement <b>approché</b> (nom proche, "
            "pas d'id ni de taxonomie exacte) — vérifiez le produit cible avant de valider, "
            "ou repassez-les en « + créer un produit ».</p>",
            unsafe_allow_html=True,
        )
    if n_blocking:
        st.markdown(
            '<p class="hf-muted" style="font-size:11.5px;margin:0 0 8px 0">'
            f"{n_blocking} ligne(s) « + créer un produit » sont incomplètes — "
            "complétez la taxonomie / fournisseur / norme ci-dessus, ou repassez-les "
            "sur « (ignorer cette ligne) » pour continuer.</p>",
            unsafe_allow_html=True,
        )

    bn1, bn2 = st.columns([3, 2])
    with bn1:
        if st.button("↺ recommencer", key="dpgf_reset_bottom", use_container_width=True):
            _reset()
            st.rerun()
    with bn2:
        if st.button(
            f"→ Étape suivante : valider ({n_writable})",
            key="dpgf_to_step3",
            type="primary",
            disabled=(n_writable == 0 or n_blocking > 0),
            use_container_width=True,
        ):
            S["dpgf_step"] = 2
            st.rerun()


# ============================================================================
#  Step 3 — Valider
# ============================================================================
elif S["dpgf_step"] == 2:
    lines: list[DpgfLine] = S["dpgf_lines"]
    matches = S["dpgf_matches"]

    meta = S.get("dpgf_meta") or {}
    coef_snapshot = meta.get("coefficients") or {}
    computed = meta.get("computed") or {}
    recap = meta.get("recap") or {}

    def _should_log_client(line: DpgfLine) -> bool:
        """Log the accepted client PU whenever it carries signal: a positive
        BC that either has no AQ to compare against, or differs from it. Only
        the degenerate BC==AQ case is skipped (it would just duplicate the
        cost update)."""
        if not (line.pu_client and line.pu_client > 0):
            return False
        if line.pu_fourniture is None:
            return True
        return line.client_price_differs_from_supplier

    def _client_breakdown_json(line: DpgfLine) -> str:
        """Full per-line coefficient breakdown stored on the dpgf_client_price
        row, enriched with the project coefficient snapshot + quantity so the
        product card can explain the price without a join."""
        bd = dict(line.breakdown or {})
        bd["coefficients"] = coef_snapshot
        bd["quantity"] = line.quantity
        bd["pu_client"] = line.pu_client
        bd["pu_fourniture"] = line.pu_fourniture
        return json.dumps(bd, ensure_ascii=False)

    # Categorise: matched lines update an existing product, create_new lines
    # spawn a fresh product (clarified, or auto-created from the parsed picker).
    match_lines: list[tuple[DpgfLine, int]] = []
    create_lines: list[DpgfLine] = []
    for line in lines:
        m = matches.get(line.row_index, {})
        sel = m.get("selected_id")
        has_value = bool(
            (line.pu_client and line.pu_client > 0)
            or (line.pu_fourniture and line.pu_fourniture > 0)
        )
        if isinstance(sel, int):
            if has_value:
                match_lines.append((line, sel))
        elif sel == "create_new":
            clar = S["dpgf_clarify"].get(line.row_index, {})
            if _line_will_create(line, clar):
                create_lines.append(line)

    n_match = len(match_lines)
    n_create = len(create_lines)
    n_ready = n_match + n_create
    # Lines that get an extra client-price validation point (red).
    n_distinct_client = sum(
        1 for line, _ in match_lines if _should_log_client(line)
    ) + sum(
        1 for line in create_lines if _should_log_client(line)
    )

    # Resolve placeholders (used for create-new fallback paths)
    _placeholder_supplier = fetch_one(
        "SELECT id FROM suppliers WHERE name = 'Fournisseur inconnu' LIMIT 1"
    )
    _placeholder_supplier_id = (
        int(_placeholder_supplier["id"]) if _placeholder_supplier else None
    )
    _default_labor = fetch_one(
        "SELECT id FROM labor_norms "
        "WHERE task_name ILIKE '%défaut%' OR task_name ILIKE '%classifier%' "
        "ORDER BY id LIMIT 1"
    )
    _default_labor_id = int(_default_labor["id"]) if _default_labor else None

    st.markdown(
        f"""
        <div class="hf-card" style="margin:8px 0 12px 0">
          <div class="hf-row hf-between">
            <div class="hf-row" style="gap:10px;flex-wrap:wrap">
              <h2 class="hf-h2" style="margin:0">Ce qui sera enregistré</h2>
              {hf_chip(f"{n_match} coûts fournisseur mis à jour", "ok")}
              {hf_chip(f"{n_create} nouveaux produits", "warn")}
              {hf_chip(f"{n_distinct_client} prix de vente historisés", "ghost")}
            </div>
            <div class="hf-mono" style="font-size:12px;color:var(--hf-ink);font-weight:600">
              {n_ready} ligne(s) prête(s)
            </div>
          </div>
          <div class="hf-muted" style="font-size:12px;margin-top:8px;line-height:1.6">
            Le <b>coût fournisseur</b> (AQ) met à jour le catalogue ; le <b>prix de vente client</b>
            est seulement archivé dans l'historique — il ne modifie jamais le prix catalogue.
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    # ── Project rentability snapshot ─────────────────────────────────
    # The SHEET recap is ground truth; the line-sum (`computed`) is only a
    # silent cross-check shown via a "≠ calcul" flag when they diverge.
    def _fmt_money(v: Any) -> str:
        try:
            return f"{float(v):,.0f} €".replace(",", " ")
        except (TypeError, ValueError):
            return "—"

    def _f(v):
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    canon = recap if (recap and recap.get("prix_vente") is not None) else computed
    if canon:
        from_sheet = canon is recap and bool(recap)
        kv = canon.get("kv")
        marge_pct = canon.get("marge_pct")
        # Cross-check the displayed (sheet) figure against the line-sum.
        cross = ""
        rec_pv, com_pv = _f((recap or {}).get("prix_vente")), _f((computed or {}).get("prix_vente"))
        if from_sheet and rec_pv and com_pv and abs(rec_pv - com_pv) > max(1.0, 0.01 * rec_pv):
            cross = hf_chip("≠ calcul", "warn")
        src_label = "feuille" if from_sheet else "calculée"
        hors = canon.get("hors_sst") if isinstance(canon.get("hors_sst"), dict) else None
        hors_html = ""
        if hors:
            hp = hors.get("marge_pct")
            hk = hors.get("kv")
            hors_html = (
                '<div class="hf-muted" style="font-size:11px;margin-top:8px;padding-top:8px;'
                'border-top:1px dashed var(--hf-border-soft)">Hors-SST · '
                f'PV {_fmt_money(hors.get("prix_vente"))} · PR {_fmt_money(hors.get("prix_revient"))} · '
                f'marge {_fmt_money(hors.get("marge_eur"))}'
                + (f' · {hp:.1f}%' if isinstance(hp, (int, float)) else "")
                + (f' · KV {hk:.3f}' if isinstance(hk, (int, float)) else "")
                + "</div>"
            )
        plan_bits = []
        for k, lbl, fmt in [("tps_chantier", "Tps chantier", "{:.0f} h"), ("personnes", "Pers.", "{:.0f}"),
                            ("jours", "Jours", "{:.0f}"), ("semaines", "Sem.", "{:.1f}"), ("mois", "Mois", "{:.1f}")]:
            v = canon.get(k)
            if isinstance(v, (int, float)):
                plan_bits.append(f"{lbl} {fmt.format(v)}")
        plan_html = (
            '<div class="hf-muted" style="font-size:10.5px;margin-top:6px">Planning · '
            + " · ".join(plan_bits) + "</div>"
        ) if plan_bits else ""
        st.markdown(
            f"""
            <div class="hf-card" style="margin:0 0 12px 0;padding:14px 18px">
              <div class="hf-row" style="gap:8px;align-items:center;margin-bottom:8px">
                <span class="hf-muted" style="font-size:11px;text-transform:uppercase;letter-spacing:.04em">
                  Rentabilité du projet ({src_label})</span>{cross}
              </div>
              <div class="hf-row" style="gap:26px;flex-wrap:wrap">
                <div><div class="hf-muted" style="font-size:10.5px">Prix de vente</div>
                  <div style="font-size:18px;font-weight:700;color:var(--hf-ink)">{_fmt_money(canon.get('prix_vente'))}</div></div>
                <div><div class="hf-muted" style="font-size:10.5px">Prix de revient</div>
                  <div style="font-size:18px;font-weight:700;color:var(--hf-ink)">{_fmt_money(canon.get('prix_revient'))}</div></div>
                <div><div class="hf-muted" style="font-size:10.5px">Marge</div>
                  <div style="font-size:18px;font-weight:700;color:var(--hf-ink)">{_fmt_money(canon.get('marge_eur'))}
                    <span style="font-size:12px;font-weight:600;color:var(--hf-muted)">{f"· {marge_pct:.1f}%" if isinstance(marge_pct, (int, float)) else ""}</span></div></div>
                <div><div class="hf-muted" style="font-size:10.5px">KV (vente / revient)</div>
                  <div style="font-size:18px;font-weight:700;color:var(--hf-leaf,#3a7d52)">{f"{kv:.3f}" if isinstance(kv, (int, float)) else "—"}</div></div>
              </div>
              {hors_html}
              {plan_html}
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ── Summary table (matched then to-create) ──────────────────────
    def _fmt_eur(v: float | None) -> str:
        return f"{v:,.2f} €".replace(",", " ") if v else "—"

    summary_rows: list[dict[str, Any]] = []
    for line, pid in match_lines:
        prod = fetch_one(
            """
            SELECT p.reference_name, pf.name AS family_name, p.packaging
              FROM products p JOIN product_families pf ON pf.id = p.family_id
             WHERE p.id = :pid
            """,
            {"pid": pid},
        )
        if not prod:
            continue
        cost_used = line.pu_fourniture if line.pu_fourniture else line.pu_client
        summary_rows.append({
            "L": line.row_index,
            "Action": "↻ MAJ",
            "DPGF": (line.client_designation or "")[:48],
            "→ Produit": f"{prod['reference_name']} · {prod['family_name']} · {prod['packaging']}",
            "Coût HT (AQ)": _fmt_eur(cost_used),
            "PU client (BC)": _fmt_eur(line.pu_client),
            "Prix de vente historisé": ("oui" if _should_log_client(line) else "—"),
        })
    for line in create_lines:
        clar = S["dpgf_clarify"].get(line.row_index, {})
        if clar.get("override") or not _auto_creatable(line):
            # Clarified: reflect what the user actually picked.
            new_label = (
                f"{clar.get('new_family_name') or line.famille or '?'} · "
                f"{clar.get('subcategory') or 'À classifier'} · "
                f"{line.reference_name or (line.client_designation or '')[:24]} · "
                f"{clar.get('packaging') or 'À classifier'}"
            )
            cost_used = clar.get("cost") or line.pu_fourniture or line.pu_client
        else:
            new_label = (
                f"{line.famille or '?'} · {line.sous_cat or 'À classifier'} · "
                f"{line.reference_name or (line.client_designation or '')[:24]} · "
                f"{line.conditionnement or 'À classifier'}"
            )
            cost_used = line.pu_fourniture if line.pu_fourniture else line.pu_client
        summary_rows.append({
            "L": line.row_index,
            "Action": "+ CRÉER",
            "DPGF": (line.client_designation or "")[:48],
            "→ Produit": new_label,
            "Coût HT (AQ)": _fmt_eur(cost_used),
            "PU client (BC)": _fmt_eur(line.pu_client),
            "Prix de vente historisé": ("oui" if _should_log_client(line) else "—"),
        })

    if summary_rows:
        st.dataframe(summary_rows, hide_index=True, use_container_width=True, height=380)
    else:
        st.warning("Aucune ligne prête à valider.")

    bb1, bb2, bb3 = st.columns([1, 1, 2])
    with bb1:
        if st.button("← retour matching", key="dpgf_back_to_step2", use_container_width=True):
            S["dpgf_step"] = 1
            st.rerun()
    with bb2:
        if st.button("↺ recommencer", key="dpgf_reset_step3", use_container_width=True):
            _reset()
            st.rerun()
    with bb3:
        if st.button(
            f"✓ enregistrer {n_ready} ligne(s)",
            key="dpgf_commit",
            type="primary",
            disabled=(n_ready == 0),
            use_container_width=True,
        ):
            try:
                actor = os.environ.get("STREAMLIT_AUTH_USER", "system")
                source_ref = (
                    f"{S.get('dpgf_project_name') or 'sans-projet'} :: "
                    f"{S.get('dpgf_filename') or 'sans-fichier'}"
                )
                n_updated = 0
                n_created = 0
                n_validated = 0
                n_cost_skipped = 0   # matched lines with no AQ → cost untouched
                with transaction(ingestion_source="dpgf_return", ingestion_actor=actor) as conn:

                    # ── 0. Persist the project (xlsx + stats), get its id ──
                    proj = conn.execute(
                        text(
                            """
                            INSERT INTO dpgf_projects
                                (project_name, filename, file_bytes, file_sha256,
                                 imported_by, n_lines, n_matched, n_created,
                                 coefficients, stats, recap, notes)
                            VALUES
                                (:pn, :fn, :fb, :sha,
                                 :by, :nl, :nm, :nc,
                                 CAST(:coef AS jsonb), CAST(:stats AS jsonb),
                                 CAST(:recap AS jsonb), :notes)
                            RETURNING id
                            """
                        ),
                        {
                            "pn": S.get("dpgf_project_name") or None,
                            "fn": S.get("dpgf_filename") or None,
                            "fb": S.get("dpgf_raw_bytes"),
                            "sha": S.get("dpgf_file_sha"),
                            "by": actor,
                            "nl": len(lines),
                            "nm": n_match,
                            "nc": n_create,
                            "coef": json.dumps(coef_snapshot, ensure_ascii=False),
                            "stats": json.dumps(computed, ensure_ascii=False),
                            "recap": json.dumps(recap, ensure_ascii=False),
                            "notes": None,
                        },
                    ).mappings().first()
                    project_id = int(proj["id"])

                    # Thread project + reference to the audit trigger so the
                    # supplier-cost (black) rows it auto-inserts on a cost_ht
                    # UPDATE carry this project too. Transaction-local settings.
                    conn.execute(
                        text("SELECT set_config('app.ingestion_project_id', :p, true)"),
                        {"p": str(project_id)},
                    )
                    conn.execute(
                        text("SELECT set_config('app.ingestion_reference', :r, true)"),
                        {"r": source_ref},
                    )

                    # ── 1. Update existing matched products ──────────
                    for line, pid in match_lines:
                        # Cost = supplier price (AQ) ONLY. The DB stores costs,
                        # never selling prices: a line without AQ leaves the
                        # product's cost untouched (the BC selling price is
                        # still historised below). Letting BC fall through here
                        # would poison cost_ht with a margined client price.
                        if line.pu_fourniture:
                            # The trigger logs a 'dpgf_return' (black) row
                            # stamped with project_id — only if cost changed.
                            conn.execute(
                                text(
                                    "UPDATE products SET cost_ht = :cost, "
                                    "last_price_update = now() "
                                    "WHERE id = :pid"
                                ),
                                {"cost": float(line.pu_fourniture), "pid": pid},
                            )
                            n_updated += 1
                        else:
                            n_cost_skipped += 1

                        # Client PU (BC) → 'dpgf_client_price' (red) row with the
                        # full coefficient breakdown + project link.
                        if _should_log_client(line):
                            conn.execute(
                                text(
                                    "INSERT INTO price_history "
                                    "(product_id, cost_ht, source, source_reference, "
                                    " recorded_by, project_id, breakdown) "
                                    "VALUES (:pid, :cost, 'dpgf_client_price', :ref, "
                                    " :by, :proj, CAST(:bd AS jsonb))"
                                ),
                                {
                                    "pid": pid,
                                    "cost": float(line.pu_client),
                                    "ref": source_ref + f" :: PU client {line.pu_client:.2f}",
                                    "by": actor,
                                    "proj": project_id,
                                    "bd": _client_breakdown_json(line),
                                },
                            )
                            n_validated += 1

                    # ── 2. Create products (clarified or auto from picker) ──
                    for line in create_lines:
                        clar = S["dpgf_clarify"].get(line.row_index, {})
                        use_clar = clar.get("override") or not _auto_creatable(line)

                        if use_clar:
                            family_id = resolve_family(
                                conn, clar.get("family_id"), clar.get("new_family_name", "")
                            )
                            subcategory = (clar.get("subcategory") or "À classifier").strip() or "À classifier"
                            packaging = (clar.get("packaging") or "À classifier").strip() or "À classifier"
                            supplier_id = resolve_supplier(
                                conn, clar.get("supplier_id"), clar.get("supplier_new_name", "")
                            )
                            labor_id = clar.get("labor_norm_id")
                            if labor_id == LABOR_NEW_ID:
                                labor_id = quick_create_labor_norm(
                                    conn,
                                    clar.get("labor_new_name", ""),
                                    clar.get("labor_new_unit", "u"),
                                    clar.get("labor_new_pose_hours") or 0,
                                )
                            # Cost = clarified value or AQ. NEVER the client PU
                            # (BC is a margined selling price, not a cost).
                            new_cost = float(clar.get("cost") or line.pu_fourniture or 0)
                            unit = line.unit or clar.get("labor_new_unit") or "u"
                        else:
                            # Auto from the parsed picker (complete triplet).
                            family_id = resolve_family(conn, FAMILY_NEW_ID, line.famille)
                            subcategory = line.sous_cat or "À classifier"
                            packaging = line.conditionnement or "À classifier"
                            supplier_id = _placeholder_supplier_id
                            if line.fournisseur:
                                sup_row = conn.execute(
                                    text("SELECT id FROM suppliers WHERE lower(name)=lower(:n) LIMIT 1"),
                                    {"n": line.fournisseur.strip()},
                                ).mappings().first()
                                if sup_row:
                                    supplier_id = int(sup_row["id"])
                            labor_id = _default_labor_id
                            new_cost = float(line.pu_fourniture or 0)  # AQ only, never BC
                            unit = line.unit or "u"

                        # Guarantee the NOT NULL FKs (supplier + labor norm).
                        if not supplier_id:
                            supplier_id = resolve_supplier(conn, SUPPLIER_NEW_ID, "Fournisseur inconnu")
                        if not labor_id:
                            labor_id = quick_create_labor_norm(
                                conn, "Norme par défaut (à classifier)", unit, 0.0
                            )

                        ensure_taxonomy(conn, family_id, subcategory, packaging, created_by="dpgf_return")

                        ref_name = (
                            (line.reference_name or "").strip()
                            or (line.client_designation or "")[:60].strip()
                            or f"DPGF L{line.row_index}"
                        )

                        ins = conn.execute(
                            text(
                                """
                                INSERT INTO products
                                    (reference_name, family_id, subcategory,
                                     supplier_id, labor_norm_id,
                                     packaging, unit_type,
                                     cost_ht, attributes, notes, is_active)
                                VALUES
                                    (:ref, :fid, :sub,
                                     :sid, :lid,
                                     :pkg, :unit,
                                     :cost, '{}'::jsonb, :notes, TRUE)
                                ON CONFLICT (reference_name, packaging, supplier_id)
                                  DO UPDATE SET cost_ht = EXCLUDED.cost_ht,
                                                last_price_update = now()
                                RETURNING id, (xmax = 0) AS inserted
                                """
                            ),
                            {
                                "ref": ref_name,
                                "fid": family_id,
                                "sub": subcategory,
                                "sid": supplier_id,
                                "lid": labor_id,
                                "pkg": packaging,
                                "unit": unit,
                                "cost": new_cost,
                                "notes": f"Créé via Retour DPGF ({source_ref})",
                            },
                        ).mappings().first()
                        new_pid = int(ins["id"])
                        was_insert = bool(ins["inserted"])
                        n_created += 1

                        # Supplier-cost (black) row: the trigger only fires on
                        # UPDATE, so for a genuinely new product we log it by
                        # hand. On a conflict-UPDATE the trigger already did.
                        if was_insert and new_cost > 0:
                            conn.execute(
                                text(
                                    "INSERT INTO price_history "
                                    "(product_id, cost_ht, source, source_reference, "
                                    " recorded_by, project_id) "
                                    "VALUES (:pid, :cost, 'dpgf_return', :ref, :by, :proj)"
                                ),
                                {
                                    "pid": new_pid,
                                    "cost": new_cost,
                                    "ref": source_ref + " :: création produit",
                                    "by": actor,
                                    "proj": project_id,
                                },
                            )

                        # Client PU (red) row with breakdown + project link.
                        if _should_log_client(line):
                            conn.execute(
                                text(
                                    "INSERT INTO price_history "
                                    "(product_id, cost_ht, source, source_reference, "
                                    " recorded_by, project_id, breakdown) "
                                    "VALUES (:pid, :cost, 'dpgf_client_price', :ref, "
                                    " :by, :proj, CAST(:bd AS jsonb))"
                                ),
                                {
                                    "pid": new_pid,
                                    "cost": float(line.pu_client),
                                    "ref": source_ref + f" :: PU client {line.pu_client:.2f}",
                                    "by": actor,
                                    "proj": project_id,
                                    "bd": _client_breakdown_json(line),
                                },
                            )
                            n_validated += 1

                msg = (
                    f"✓ Projet enregistré (#{project_id}) · "
                    f"{n_updated} coût(s) fournisseur mis à jour · "
                    f"{n_created} produit(s) créé(s) · "
                    f"{n_validated} prix de vente historisé(s). "
                    f"Le fichier .xlsx et la rentabilité sont conservés (en bas de cette page)."
                )
                st.success(msg)
                if n_cost_skipped:
                    st.info(
                        f"ℹ️ {n_cost_skipped} ligne(s) sans coût fournisseur (col. AQ vide) : "
                        "le coût du produit n'a pas été modifié — seul le prix de vente a été historisé."
                    )
                _reset()
                st.balloons()
            except Exception as exc:
                st.error(f"Échec de l'enregistrement : {exc}")


# ============================================================================
#  Pilotage de rentabilité — history of every re-ingested project
# ============================================================================
# Lives here (not on Paramètres) because it's the natural read-side of this
# page: you drop a signed DPGF above, and every project you've already sent
# through is listed below. Only rendered on the landing step so it never
# clutters the wizard mid-flow.
_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def _fmt_money(v, decimals: int = 0) -> str:
    """FR money format: 1 234,56 €. Python emits en-US (1,234.56), so swap both
    separators — with decimals=0 the '.' branch is simply a no-op."""
    try:
        s = f"{float(v):,.{decimals}f}"
    except (TypeError, ValueError):
        return "—"
    return s.replace(",", " ").replace(".", ",") + " €"


def _num(v):
    return float(v) if isinstance(v, (int, float)) else None


def _canon(p) -> dict:
    """Authoritative rentability for a project: the SHEET recap when present
    (ground truth), else the app's computed line-sum (older imports). recap
    may also carry hors_sst + the Tps-chantier planning fields."""
    recap = p["recap"] or {}
    if _num(recap.get("prix_vente")) is not None:
        return recap
    return p["stats"] or {}


@st.cache_data(show_spinner=False, max_entries=20, ttl=3600)
def _project_xlsx(pid: int) -> bytes | None:
    """Lazily load (and cache) the stored .xlsx for one project.

    Bounded cache: these are raw workbook blobs and the list renders up to 100
    projects per rerun — unbounded caching would pin every file in memory."""
    row = fetch_one("SELECT file_bytes FROM dpgf_projects WHERE id = :id", {"id": pid})
    if not row or row["file_bytes"] is None:
        return None
    return bytes(row["file_bytes"])


def _detail_fmt(value, kind: str) -> str:
    """Format one recap value the way the sheet does (FR decimal comma)."""
    v = _num(value)
    if v is None:
        return "—"
    if kind == "€":
        return _fmt_money(v, 2)
    if kind == "%":
        return f"{v:.2f} %".replace(".", ",")
    if kind == "kv":
        return f"{v:.3f}".replace(".", ",")
    if kind == "h":
        return f"{v:.0f} h"
    # plain number: drop the decimals when it's whole (5 not 5,0)
    return (f"{v:.0f}" if abs(v - round(v)) < 1e-9 else f"{v:.2f}".replace(".", ","))


def _detail_block(title: str, rows: list) -> str:
    """One green-banded label/value block, mirroring the sheet's recap cards."""
    body = "".join(
        '<div class="hf-row" style="justify-content:space-between;gap:10px;'
        'padding:3px 8px;border-bottom:1px solid var(--hf-border-soft)">'
        f'<span class="hf-muted" style="font-size:11px">{label}</span>'
        f'<span style="font-size:11.5px;font-weight:600;color:var(--hf-ink)">'
        f'{_detail_fmt(value, kind)}</span></div>'
        for label, value, kind in rows
    )
    return (
        '<div style="border:1px solid var(--hf-border-soft);border-radius:6px;overflow:hidden">'
        '<div style="background:var(--hf-green,#1d3a2a);color:#fff;font-weight:700;'
        'font-size:10.5px;padding:5px 8px;letter-spacing:.02em">' + title + "</div>"
        + body + "</div>"
    )


def _render_rentabilite() -> None:
    st.markdown('<div style="height:22px"></div>', unsafe_allow_html=True)
    st.markdown(
        '<h2 class="hf-h2" style="margin:6px 0 2px 0">Pilotage de rentabilité</h2>',
        unsafe_allow_html=True,
    )
    st.markdown(
        '<p class="hf-muted" style="font-size:12.5px;margin:0 0 12px 0">'
        "L'historique des projets ré-ingérés, avec leur rentabilité.</p>",
        unsafe_allow_html=True,
    )

    projects = fetch_all(
        """
        SELECT id, project_name, filename, imported_at, imported_by,
               n_lines, n_matched, n_created,
               stats, recap, coefficients,
               octet_length(file_bytes) AS file_size
          FROM dpgf_projects
         ORDER BY imported_at DESC
         LIMIT 100
        """
    )

    if not projects:
        st.markdown(
            '<div class="hf-card" style="padding:18px 20px">'
            '<div class="hf-muted" style="font-size:12.5px">'
            "Aucun projet ingéré pour l'instant. Dépose un DPGF signé ci-dessus — "
            "il apparaîtra ici avec ses statistiques de rentabilité "
            "et le fichier téléchargeable.</div></div>",
            unsafe_allow_html=True,
        )
        return

    # ── Aggregate KPI strip (driven by the authoritative sheet recap) ──
    _canons = [_canon(p) for p in projects]
    tot_pv = sum(_num(c.get("prix_vente")) or 0 for c in _canons)
    tot_pr = sum(_num(c.get("prix_revient")) or 0 for c in _canons)
    tot_marge = tot_pv - tot_pr
    marge_pct = (tot_marge / tot_pv * 100) if tot_pv else None
    kv_moyen = (tot_pv / tot_pr) if tot_pr else None

    k1, k2, k3, k4, k5 = st.columns(5)
    with k1:
        hf_kpi("Projets", len(projects))
    with k2:
        hf_kpi("Prix de vente cumulé", _fmt_money(tot_pv))
    with k3:
        hf_kpi("Prix de revient cumulé", _fmt_money(tot_pr))
    with k4:
        hf_kpi("Marge moyenne", f"{marge_pct:.1f}".replace(".", ",") if marge_pct is not None else "—", unit="%")
    with k5:
        hf_kpi("KV moyen", f"{kv_moyen:.3f}".replace(".", ",") if kv_moyen is not None else "—", accent=True)

    st.markdown('<div style="height:10px"></div>', unsafe_allow_html=True)

    # ── Per-project cards ──
    for p in projects:
        stats = p["stats"] or {}
        recap = p["recap"] or {}
        canon = _canon(p)
        from_sheet = canon is recap and bool(recap)
        pv = _num(canon.get("prix_vente"))
        pr = _num(canon.get("prix_revient"))
        marge_e = _num(canon.get("marge_eur"))
        marge_p = _num(canon.get("marge_pct"))
        kv = _num(canon.get("kv"))

        # Provenance + cross-check: the sheet figure is shown; flag when the
        # app's line-sum (stats) disagrees with it by >1 %.
        src_chip = hf_chip("feuille", "ok") if from_sheet else hf_chip("calculé", "ghost")
        cross_flag = ""
        if from_sheet:
            sheet_pv, comp_pv = _num(recap.get("prix_vente")), _num(stats.get("prix_vente"))
            if sheet_pv and comp_pv and abs(sheet_pv - comp_pv) > max(1.0, 0.01 * sheet_pv):
                cross_flag = hf_chip("≠ calcul", "warn")

        # Hors-SST variant (full block from the sheet recap).
        hors = canon.get("hors_sst") if isinstance(canon.get("hors_sst"), dict) else None
        hors_html = ""
        if hors:
            bits = []
            h_pv, h_pr, h_marge = _num(hors.get("prix_vente")), _num(hors.get("prix_revient")), _num(hors.get("marge_eur"))
            h_pct, h_kv = _num(hors.get("marge_pct")), _num(hors.get("kv"))
            if h_pv is not None:
                bits.append(f"PV {_fmt_money(h_pv)}")
            if h_pr is not None:
                bits.append(f"PR {_fmt_money(h_pr)}")
            if h_marge is not None:
                bits.append(f"marge {_fmt_money(h_marge)}")
            if h_pct is not None:
                bits.append(f"{h_pct:.1f}%".replace(".", ","))
            if h_kv is not None:
                bits.append(f"KV {h_kv:.3f}".replace(".", ","))
            if bits:
                hors_html = (
                    '<div class="hf-muted" style="font-size:10.5px;margin-top:6px;'
                    'padding-top:6px;border-top:1px dashed var(--hf-border-soft)">'
                    "Hors-SST · " + " · ".join(bits) + "</div>"
                )

        # Tps-chantier planning line (only present on sheet-recap projects).
        plan_bits = []
        for key, label, fmt in [
            ("tps_chantier", "Tps chantier", "{:.0f} h"), ("personnes", "Pers.", "{:.0f}"),
            ("jours", "Jours", "{:.0f}"), ("semaines", "Sem.", "{:.1f}"), ("mois", "Mois", "{:.1f}"),
        ]:
            v = _num(canon.get(key))
            if v is not None:
                plan_bits.append(f"{label} {fmt.format(v)}".replace(".", ","))
        plan_html = (
            '<div class="hf-muted" style="font-size:10.5px;margin-top:4px">'
            "Planning · " + " · ".join(plan_bits) + "</div>"
        ) if plan_bits else ""

        when = ""
        try:
            when = p["imported_at"].strftime("%d/%m/%Y")
        except Exception:  # noqa: BLE001
            pass
        size_kb = f"{(p['file_size'] or 0) / 1024:.0f} Ko"

        with st.container(border=True):
            c_meta, c_pv, c_pr, c_marge, c_kv, c_dl = st.columns([3, 1.5, 1.5, 1.7, 1, 1.4])
            with c_meta:
                st.markdown(
                    f'<div style="font-weight:600;font-size:13.5px;color:var(--hf-ink)">'
                    f'{(p["project_name"] or "Projet sans nom")}</div>'
                    f'<div class="hf-row" style="gap:5px;align-items:center;margin-top:3px">'
                    f'<span class="hf-muted" style="font-size:10.5px">'
                    f'{when} · {p["n_lines"]} lignes · {p["n_matched"]}✓ / {p["n_created"]}＋</span>'
                    f'{src_chip}{cross_flag}</div>'
                    f'{hors_html}{plan_html}',
                    unsafe_allow_html=True,
                )
            with c_pv:
                st.markdown(
                    '<div class="hf-muted" style="font-size:9.5px">Prix de vente</div>'
                    f'<div style="font-weight:600;font-size:15px;color:var(--hf-ink)">{_fmt_money(pv)}</div>',
                    unsafe_allow_html=True,
                )
            with c_pr:
                st.markdown(
                    '<div class="hf-muted" style="font-size:9.5px">Prix de revient</div>'
                    f'<div style="font-weight:600;font-size:15px;color:var(--hf-ink)">{_fmt_money(pr)}</div>',
                    unsafe_allow_html=True,
                )
            with c_marge:
                pct_txt = f' · {marge_p:.1f}%'.replace(".", ",") if marge_p is not None else ""
                st.markdown(
                    '<div class="hf-muted" style="font-size:9.5px">Marge</div>'
                    f'<div style="font-weight:600;font-size:15px;color:var(--hf-ink)">{_fmt_money(marge_e)}'
                    f'<span style="font-size:11px;color:var(--hf-muted);font-weight:500">{pct_txt}</span></div>',
                    unsafe_allow_html=True,
                )
            with c_kv:
                st.markdown(
                    '<div class="hf-muted" style="font-size:9.5px">KV</div>'
                    f'<div style="font-weight:700;font-size:15px;color:var(--hf-leaf,#3a7d52)">'
                    f'{f"{kv:.3f}".replace(".", ",") if kv is not None else "—"}</div>',
                    unsafe_allow_html=True,
                )
            with c_dl:
                data = _project_xlsx(p["id"])
                if data:
                    st.download_button(
                        "⬇ .xlsx",
                        data=data,
                        file_name=p["filename"] or f"projet_{p['id']}.xlsx",
                        mime=_XLSX_MIME,
                        key=f"rent_dl_{p['id']}",
                        use_container_width=True,
                        help=f"{size_kb} · fichier d'origine conservé",
                    )
                else:
                    st.markdown(
                        '<span class="hf-muted" style="font-size:10px">fichier absent</span>',
                        unsafe_allow_html=True,
                    )

            # ── Full detail — mirrors the three blocks of the sheet's
            #    « Pilotage de rentabilité » tab, plus the coefficient snapshot.
            with st.expander("Détail du projet", expanded=False):
                d1, d2, d3 = st.columns(3)
                with d1:
                    st.markdown(
                        _detail_block("TEMPS CHANTIER", [
                            ("Tps chantier", canon.get("tps_chantier"), "h"),
                            ("Personnes (équipe)", canon.get("personnes"), ""),
                            ("Heures / jour", canon.get("heures_par_jour"), ""),
                            ("Jours / semaine", canon.get("jours_par_semaine"), ""),
                            ("Semaines / mois", canon.get("semaines_par_mois"), ""),
                            ("Jours", canon.get("jours"), ""),
                            ("Semaines", canon.get("semaines"), ""),
                            ("Mois", canon.get("mois"), ""),
                        ]),
                        unsafe_allow_html=True,
                    )
                with d2:
                    st.markdown(
                        _detail_block("RENTABILITÉ — GLOBAL", [
                            ("Prix de vente", canon.get("prix_vente"), "€"),
                            ("Prix de revient", canon.get("prix_revient"), "€"),
                            ("Marge €", canon.get("marge_eur"), "€"),
                            ("Marge %", canon.get("marge_pct"), "%"),
                            ("KV (vente / revient)", canon.get("kv"), "kv"),
                        ]),
                        unsafe_allow_html=True,
                    )
                with d3:
                    hs = hors or {}
                    st.markdown(
                        _detail_block("RENTABILITÉ — HORS SST", [
                            ("Prix de vente", hs.get("prix_vente"), "€"),
                            ("Prix de revient", hs.get("prix_revient"), "€"),
                            ("Marge €", hs.get("marge_eur"), "€"),
                            ("Marge %", hs.get("marge_pct"), "%"),
                            ("KV (vente / revient)", hs.get("kv"), "kv"),
                        ]),
                        unsafe_allow_html=True,
                    )

                # Ingestion counters + coefficient snapshot used for this quote.
                coefs = p["coefficients"] or {}
                meta_bits = [
                    f"{p['n_lines']} lignes",
                    f"{p['n_matched']} produits mis à jour",
                    f"{p['n_created']} produits créés",
                ]
                if p.get("imported_by"):
                    meta_bits.append(f"par {p['imported_by']}")
                if p.get("filename"):
                    meta_bits.append(str(p["filename"]))
                st.markdown(
                    '<div class="hf-muted" style="font-size:11px;margin-top:10px">'
                    + " · ".join(meta_bits) + "</div>",
                    unsafe_allow_html=True,
                )
                if coefs:
                    coef_bits = [
                        f"<code>{k}</code> {str(v).replace('.', ',')}"
                        for k, v in sorted(coefs.items())
                    ]
                    st.markdown(
                        '<div class="hf-muted" style="font-size:11px;margin-top:6px;line-height:1.8">'
                        "<b>Coefficients appliqués</b> · " + " · ".join(coef_bits) + "</div>",
                        unsafe_allow_html=True,
                    )


# S.get: after a successful commit, _reset() pops dpgf_step within this same
# script run, so a bare S["dpgf_step"] here raises KeyError (seen in prod logs).
if S.get("dpgf_step", 0) == 0:
    _render_rentabilite()


render_footer()
