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
    # Hard requirement banner — the parser only understands the
    # Merci Raymond DPGF template v2. Any other workbook (BTP / Excelya /
    # ad-hoc client format) will be rejected at upload time.
    st.markdown(
        """
        <div class="hf-card danger" style="margin:4px 0 16px 0">
          <div class="hf-row" style="gap:10px;align-items:flex-start">
            <span style="font-size:18px;line-height:1">⚠</span>
            <div>
              <div style="font-weight:600;font-size:13.5px;color:var(--hf-ink)">
                Format requis · classeur DPGF Merci Raymond v2
              </div>
              <div class="hf-muted" style="font-size:11.5px;margin-top:4px;line-height:1.55">
                Le classeur doit contenir un onglet nommé <b>« DPGF »</b>
                (ou « DPGF Master » / « DPGF Template »). Tu peux téléverser
                le <b>classeur complet</b> — la page ignorera les autres
                onglets et lira uniquement le DPGF. Les colonnes attendues
                sur l'onglet DPGF :
                <ul style="margin:6px 0 0 18px;padding:0;line-height:1.6">
                  <li><b>Zone client (A–Z)</b> : <code>B</code>/<code>C</code>/<code>E</code> par défaut pour désignation/unité/quantité — re-mappables via les cellules <code>Col_Designation</code>, <code>Col_Unite</code>, <code>Col_Quantite</code> de l'onglet Paramètres.</li>
                  <li><b>Notre zone (AA+)</b> : <code>AC</code> = quantité mirroir — <code>AG</code> = chaîne produit (Famille — Sous-cat — Référence — Cond.)</li>
                  <li><code>AI</code> = fournisseur (résolu par formule) — <code>AQ</code> = prix fourniture / unité (notre coût d'achat)</li>
                  <li><code>BC</code> = PU unitaire client accepté (ce que le marché a validé)</li>
                </ul>
                Un classeur d'un autre format (ex. DPGF client externe non
                Merci Raymond) sera <b>refusé</b>.
              </div>
            </div>
          </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    st.markdown(
        '<p class="hf-muted" style="margin:6px 0 10px 0;font-size:13px;max-width:740px">'
        "À l'enregistrement, pour chaque ligne le système :"
        "</p>"
        '<ul class="hf-muted" style="font-size:12.5px;line-height:1.7;max-width:740px;margin:0 0 14px 18px;padding:0">'
        "<li>identifie le <b>produit existant</b> correspondant (ou propose d'en créer un nouveau),</li>"
        "<li>met à jour le <b>coût HT</b> du produit avec le prix fournisseur du modèle (col. AQ) — c'est notre coût d'achat réel,</li>"
        "<li>enregistre le <b>PU client accepté</b> (col. BC) dans l'historique du produit avec un chip rouge "
        "<code>dpgf_return</code> — <b>uniquement si</b> ce PU diffère du PU fournisseur de la ligne. "
        "Quand AQ = BC, la mise à jour de coût suffit ; sinon, on garde trace du prix de vente distinct.</li>"
        "<li>pour les lignes sans correspondance : crée le produit avec les pièces extraites "
        "de la chaîne col. AG — si le triplet est incomplet, le produit part en <b>À classifier</b>.</li>"
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
            #         famille + sous-catégorie + conditionnement + référence.
            #         This is what makes "existing vs new" 100% reliable.
            if not cands and line.reference_name and line.famille and line.sous_cat:
                tax = fetch_one(
                    _PROD_COLS
                    + "WHERE p.is_active "
                    "  AND lower(pf.name) = lower(:fam) "
                    "  AND lower(p.subcategory) = lower(:sub) "
                    "  AND p.packaging = :pkg "
                    "  AND p.reference_name = :ref "
                    "LIMIT 1",
                    {
                        "fam": line.famille,
                        "sub": line.sous_cat,
                        "pkg": line.conditionnement or "",
                        "ref": line.reference_name,
                    },
                )
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
        '<p class="hf-muted" style="font-size:12px;margin:2px 0 8px 0;max-width:780px">'
        "Une ligne = un produit. Le rapprochement est <b>fiable</b> quand il vient de "
        "l'<b>identifiant caché</b> (🔗 id) ou de la <b>taxonomie exacte</b> "
        "(famille · sous-cat · conditionnement · référence) — "
        "<span style='color:#2e7d52;font-weight:600'>vert = produit existant</span>. "
        "Un rapprochement <b>approché</b> est en "
        "<span style='color:#2f6f9f;font-weight:600'>bleu (à vérifier)</span>, "
        "un <span style='color:#c4623d;font-weight:600'>nouveau produit en orange</span>. "
        "On ne donne un nouveau prix à un produit existant que sur du vert ou un choix confirmé.</p>",
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

            # Money facts to verify: qty · PU client (what we record) · our cost
            # (AQ) · the margin between them (the whole point of the check).
            qty = (
                f"{line.quantity:,.2f}".rstrip("0").rstrip(",").replace(",", " ")
                if line.quantity is not None else "—"
            )
            marge_txt = ""
            if line.pu_client and line.pu_fourniture:
                if line.client_price_differs_from_supplier:
                    mg = line.pu_client - line.pu_fourniture
                    mg_pct = (mg / line.pu_fourniture * 100) if line.pu_fourniture else 0
                    marge_txt = (
                        f'<span style="color:#2e7d52">marge +{_eur(mg)} '
                        f'({mg_pct:.0f}%)</span>'
                    )
                else:
                    marge_txt = '<span style="color:#c4623d">⚠ PU = coût (marge nulle)</span>'

            # The → target: the matched product, or what we'll create.
            sel_c = next((c for c in cands if c["id"] == new_sel), None)
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
                        qté {qty} · PU client <b style="color:var(--hf-ink)">{_eur(line.pu_client)}</b>
                        · coût {_eur(line.pu_fourniture)} {('· ' + marge_txt) if marge_txt else ''}
                      </div>
                      <div style="font-size:11px;color:var(--hf-body);margin-top:2px">{target}</div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )

            # ── Inline clarification for create_new lines ──────────────
            if new_sel == "create_new":
                clar = S["dpgf_clarify"].setdefault(ri, {})
                auto = _auto_creatable(line)
                if auto:
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
              <h2 class="hf-h2" style="margin:0">Récap de l'écriture</h2>
              {hf_chip(f"{n_match} produits à mettre à jour", "ok")}
              {hf_chip(f"{n_create} produits à créer", "warn")}
              {hf_chip(f"{n_distinct_client} PU client (rouge)", "danger" if n_distinct_client else "ghost")}
            </div>
            <div class="hf-mono" style="font-size:12px;color:var(--hf-ink);font-weight:600">
              {n_ready} ligne(s) prête(s)
            </div>
          </div>
          <div class="hf-muted" style="font-size:11.5px;margin-top:8px;line-height:1.55">
            ↳ <b>Coût HT</b> du produit mis à jour avec le coût fournisseur (col. AQ) — tracé en
            <b>noir</b> <code style="font-family:JetBrains Mono,monospace;background:var(--hf-cream);padding:1px 5px;border-radius:3px;font-size:10.5px">dpgf_return</code>.<br>
            ↳ <b>PU client accepté</b> (col. BC) enregistré en <b>rouge</b>
            <code style="font-family:JetBrains Mono,monospace;background:var(--hf-cream);padding:1px 5px;border-radius:3px;font-size:10.5px">dpgf_client_price</code>
            avec le <b>détail des coefficients</b>, rattaché au projet.<br>
            ↳ Le <b>fichier .xlsx</b> et les <b>stats de rentabilité</b> du projet sont conservés (page Paramètres → Pilotage).<br>
            ↳ Les <b>nouveaux produits</b> utilisent la taxonomie / fournisseur / norme précisés à l'étape précédente.
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
                  <div style="font-size:18px;font-weight:700;color:var(--hf-accent)">{f"{kv:.3f}" if isinstance(kv, (int, float)) else "—"}</div></div>
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
            "PU client (rouge)": ("🔴 oui" if _should_log_client(line) else "—"),
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
            "PU client (rouge)": ("🔴 oui" if _should_log_client(line) else "—"),
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
                        # Supplier cost (AQ) → falls back to client PU.
                        new_cost = (
                            float(line.pu_fourniture)
                            if line.pu_fourniture else float(line.pu_client)
                        )
                        # The trigger logs a 'dpgf_return' (black) row stamped
                        # with project_id from set_config — only if cost changed.
                        conn.execute(
                            text(
                                "UPDATE products SET cost_ht = :cost, "
                                "last_price_update = now() "
                                "WHERE id = :pid"
                            ),
                            {"cost": new_cost, "pid": pid},
                        )
                        n_updated += 1

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
                            new_cost = float(
                                clar.get("cost") or line.pu_fourniture or line.pu_client or 0
                            )
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
                            new_cost = float(line.pu_fourniture or line.pu_client or 0)
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
                    f"{n_updated} produit(s) mis à jour · "
                    f"{n_created} produit(s) créé(s) · "
                    f"{n_validated} prix client (rouge) enregistré(s). "
                    f"Le fichier .xlsx et les stats sont conservés (Paramètres → Pilotage)."
                )
                st.success(msg)
                _reset()
                st.balloons()
            except Exception as exc:
                st.error(f"Échec de l'enregistrement : {exc}")


render_footer()
