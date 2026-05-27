"""Products page — two tabs:

  • Catalogue — flat filtered grid + on-select detail strip.
  • Édition   — 2-column staged form (4 sections on left, 3 context cards
                on right). Edit and create modes share the same screen.

Both tabs share the families / suppliers / labor_norms / taxonomy metadata
loaded once at the top of the page.
"""

from __future__ import annotations

import json

import altair as alt
import pandas as pd
import streamlit as st
import streamlit.components.v1 as components
from sqlalchemy.exc import IntegrityError

from lib.auth import require_login
from lib.branding import (
    GREEN,
    LEAF,
    TERRA,
    apply_branding,
    hf_chip,
    hf_dot,
    render_footer,
    render_header,
    render_sidebar_brand,
)
from lib.db import execute, fetch_all, fetch_df, fetch_one

UNIT_TYPES = ["u", "m3", "ml", "m2", "Ft", "kg", "l"]
MATERIAL_OPTIONS = ["", "végétal", "minéral", "bois", "métal", "plastique", "composite", "autre"]
SOURCE_OPTIONS = ["manuel", "supplier_catalog", "historical_devis", "dpgf_return"]
NEW_VALUE_SENTINEL = "+ créer nouveau…"

# Suggested attribute keys per (famille, sous-cat). Used by Section 4's
# "suggéré pour ce triplet" chip row. Coarse but useful — Vincent can
# always type any key.
ATTRIBUTE_HINTS: dict[tuple[str, str], list[str]] = {
    ("Arbre", "Tige"):       ["essence", "circonférence", "hauteur", "origine"],
    ("Arbre", "Cépée"):      ["essence", "hauteur", "nb troncs", "origine"],
    ("Arbuste", "Caduc"):    ["essence", "port", "hauteur", "feuillage"],
    ("Arbuste", "Persistant"): ["essence", "port", "hauteur", "feuillage"],
    ("Vivace", "Persistante"): ["essence", "exposition", "hauteur"],
    ("Vivace", "Caduque"):   ["essence", "exposition", "floraison"],
    ("Graminée", "Persistante"): ["essence", "hauteur", "couleur"],
    ("Graminée", "Caduque"): ["essence", "hauteur", "couleur"],
    ("Couvre-sol", "Persistant"): ["essence", "exposition", "vitesse étalement"],
    ("Couvre-sol", "Caduc"): ["essence", "exposition", "vitesse étalement"],
    ("Bulbe", "Printemps"):  ["essence", "calibre", "couleur"],
    ("Bulbe", "Été/Automne"): ["essence", "calibre", "couleur"],
    ("Terre végétale", "Standard"): ["granulométrie", "norme", "origine"],
    ("Terre végétale", "Drainante"): ["granulométrie", "% sable", "norme"],
    ("Substrat / amendement", "Engrais"): ["NPK", "norme", "format"],
    ("Substrat / amendement", "Amendement organique"): ["matière", "norme"],
    ("Compost", "Végétal"):  ["norme", "âge", "origine"],
    ("Compost", "Mixte"):    ["norme", "ratio v/a"],
    ("Paillage minéral", "Roulé"): ["granulométrie", "couleur"],
    ("Paillage minéral", "Concassé"): ["granulométrie", "couleur"],
    ("Paillage végétal", "Écorces"): ["essence", "granulométrie"],
    ("Paillage végétal", "Broyat"): ["essence", "granulométrie"],
    ("Géotextile", "Tissé"): ["grammage", "norme", "couleur"],
    ("Géotextile", "Non-tissé"): ["grammage", "norme", "couleur"],
    ("Tuteur / piquet", "Bois"): ["essence", "diamètre", "longueur"],
    ("Tuteur / piquet", "Métal"): ["section", "longueur", "traitement"],
    ("Arrosage / irrigation", "Goutte-à-goutte"): ["débit", "écartement"],
    ("Arrosage / irrigation", "Aspersion"): ["débit", "portée"],
    ("Minéral (gravier, pierre)", "Gravier"): ["granulométrie", "couleur"],
    ("Minéral (gravier, pierre)", "Bloc / Pavé"): ["dimensions", "finition"],
    ("Mobilier extérieur", "Assise / Banc"): ["matériau", "dimensions"],
    ("Mobilier extérieur", "Bac / Jardinière"): ["dimensions", "matériau", "couleur"],
}

st.set_page_config(page_title="Produits — Merci Raymond", page_icon="🌳", layout="wide")
require_login()
apply_branding()
render_sidebar_brand()


def _badge(status: str) -> str:
    return {
        "fresh": "🟢 Frais (< 6 mois)",
        "stale_6mo": "🟡 6–9 mois",
        "stale_9mo": "🔴 > 9 mois",
    }.get(status, status)


def _freshness_dot(status: str) -> str:
    return {"fresh": "🟢", "stale_6mo": "🟡", "stale_9mo": "🔴"}.get(status, "⚪")


def _fmt_eur(v) -> str:
    if v is None or pd.isna(v):
        return "—"
    return f"{float(v):,.2f} €".replace(",", " ")


# Metadata loaded once for both tabs.
families = fetch_all("SELECT id, name FROM product_families ORDER BY name")
suppliers = fetch_all("SELECT id, name FROM suppliers ORDER BY name")
labor_norms = fetch_all("SELECT id, task_name, unit_type, heure_u_pose_default, nombre_uth_default FROM labor_norms ORDER BY task_name")
taxonomy = fetch_all(
    """
    SELECT pt.family_id, pf.name AS family_name, pt.subcategory, pt.packaging
      FROM product_taxonomy pt
      JOIN product_families pf ON pf.id = pt.family_id
     ORDER BY pf.name, pt.subcategory, pt.packaging
    """
)

family_lookup = {0: "(toutes)"} | {f["id"]: f["name"] for f in families}
family_by_id = {f["id"]: f["name"] for f in families}
supplier_lookup = {0: "(tous)"} | {s["id"]: s["name"] for s in suppliers}
supplier_by_id = {s["id"]: s["name"] for s in suppliers}
labor_lookup = {ln["id"]: f"{ln['task_name']} ({ln['unit_type']})" for ln in labor_norms}
labor_by_id = {ln["id"]: ln for ln in labor_norms}

# Taxonomy lookups: per-family subcategory list, per-(family, subcat) packaging list.
subcats_by_family: dict[int, list[str]] = {}
packagings_by_pair: dict[tuple[int, str], list[str]] = {}
for t in taxonomy:
    subcats_by_family.setdefault(t["family_id"], [])
    if t["subcategory"] not in subcats_by_family[t["family_id"]]:
        subcats_by_family[t["family_id"]].append(t["subcategory"])
    key = (t["family_id"], t["subcategory"])
    packagings_by_pair.setdefault(key, [])
    if t["packaging"] not in packagings_by_pair[key]:
        packagings_by_pair[key].append(t["packaging"])


# ============================================================================
#  Header — title, sub, right-side action buttons (above the mode switcher)
# ============================================================================
catalog_total = (fetch_one("SELECT count(*) AS c FROM products WHERE is_active") or {"c": 0})["c"]

# If a previous action requested a tab change (e.g. clicking "+ Nouveau
# produit"), apply it BEFORE the radio renders.
if "force_produits_tab" in st.session_state:
    st.session_state["produits_mode"] = st.session_state.pop("force_produits_tab")

hdr_l, hdr_r = st.columns([3, 2])
with hdr_l:
    render_header(
        title="Produits",
        subtitle=f"catalogue · {catalog_total:,} références".replace(",", " "),
    )
with hdr_r:
    b1, b2 = st.columns([1, 1])
    with b1:
        if st.button("⌕ recherche", key="prod_hdr_search", use_container_width=True):
            st.session_state["cat_search_focus"] = True
    with b2:
        # The catalogue now embeds the full edit form inline when a row is
        # selected, so the header button only ever needs to mean "create a
        # new product". Selecting a row in the table is enough to start
        # editing — no extra click required.
        if st.button(
            "+ Nouveau produit",
            key="prod_hdr_new",
            type="primary",
            use_container_width=True,
        ):
            st.session_state.pop("product_edit_id", None)
            # Clear any catalogue selection so the form opens truly blank.
            st.session_state.pop("cat_selected_pid", None)
            st.session_state["force_produits_tab"] = "Nouveau produit"
            st.rerun()


# ============================================================================
#  Mode switcher (replaces st.tabs to allow programmatic switching)
#
# `st.tabs(...)` can't be flipped from code in Streamlit ≤1.39, so the
# header "+ Nouveau produit" / "Éditer" buttons can't activate the Édition
# view. Using `st.radio` with a stable session_state key gives us full
# control: setting `st.session_state["produits_mode"]` before the radio
# renders selects the corresponding option. CSS in branding.py styles
# this radio to read like the underlined tabs it replaces.
# ============================================================================
_mode = st.radio(
    "Mode",
    options=["Catalogue", "Nouveau produit"],
    horizontal=True,
    label_visibility="collapsed",
    key="produits_mode",
)
tab_catalogue = st.container()
tab_edition = st.container()


# ============================================================================
#  Catalogue tab — flat filtered grid + on-select detail strip
# ============================================================================
def _render_product_form(edit_id_arg):
    """Render the staged product edit/create form + right-side context.

    Used in two places:
      • Catalogue mode, inline below the table when a row is selected
        (``edit_id_arg`` = the row's product id).
      • Nouveau produit mode (``edit_id_arg=None`` → blank create form).

    All widget keys are suffixed with the edit_id (or ``'new'``) so the form
    state stays isolated per product context: switching between catalogue
    rows correctly resets every field, and the inline-edit form doesn't
    bleed state into the standalone create form.
    """
    edit_id = edit_id_arg
    sfx = "_new" if edit_id is None else f"_{edit_id}"
    prefill: dict = {}
    history_df = pd.DataFrame()
    if edit_id:
        row = fetch_one("SELECT * FROM products WHERE id = :id", {"id": edit_id})
        if row:
            prefill = row
            history_df = fetch_df(
                """
                SELECT recorded_at, cost_ht, source, recorded_by
                  FROM price_history
                 WHERE product_id = :pid
                 ORDER BY recorded_at ASC
                """,
                {"pid": edit_id},
            )
        else:
            edit_id = None

    is_edit = bool(edit_id)

    # ---- Sub-header (mode-aware) ----
    if is_edit:
        title = "Modifier un produit"
        sub_meta = f"id {edit_id} · sku {(prefill.get('reference_name') or '?')[:24].lower().replace(' ', '-')}"
        bc_pieces = [
            "Produits", "Catalogue",
            f"{prefill.get('reference_name') or '?'} · "
            f"{prefill.get('packaging') or '?'}",
        ]
    else:
        title = "Nouveau produit"
        sub_meta = "création"
        bc_pieces = ["Produits", "Nouveau produit"]

    edh_l, edh_r = st.columns([3, 2])
    with edh_l:
        bc_separator = " <span class='sep'>›</span> "
        bc_html = bc_separator.join(bc_pieces)
        st.markdown(
            f'<div class="hf-bc">{bc_html}</div>',
            unsafe_allow_html=True,
        )
        st.markdown(
            f'<h1 class="hf-h1" style="margin:0 0 4px 0">{title}'
            f' <small style="font-weight:400;color:var(--hf-muted);font-size:12px;margin-left:8px">{sub_meta}</small></h1>',
            unsafe_allow_html=True,
        )
    with edh_r:
        ab1, ab2, ab3 = st.columns([1, 1, 1])
        with ab1:
            # Label depends on context: in inline-edit (catalogue mode with
            # a selected row), the button "fermes" the form by clearing the
            # selection. In Nouveau produit mode, it switches back to
            # Catalogue.
            _back_label = "× fermer" if is_edit else "← retour catalogue"
            if st.button(_back_label, key=f"ed_back{sfx}", use_container_width=True):
                st.session_state.pop("product_edit_id", None)
                # Clear catalogue selection (closes the inline form) and
                # also clear the dataframe widget state so the row deselects.
                st.session_state.pop("cat_selected_pid", None)
                st.session_state.pop("catalog_grid", None)
                st.session_state["force_produits_tab"] = "Catalogue"
                st.rerun()
        with ab2:
            if is_edit and st.button(
                "archiver" if prefill.get("is_active") else "réactiver",
                key=f"ed_archive{sfx}", use_container_width=True,
            ):
                new_active = not bool(prefill.get("is_active"))
                try:
                    execute(
                        "UPDATE products SET is_active = :a WHERE id = :id",
                        {"a": new_active, "id": edit_id},
                    )
                    st.toast(
                        "Produit " + ("archivé" if not new_active else "réactivé"),
                        icon="🗂",
                    )
                    st.rerun()
                except IntegrityError as exc:
                    st.error(f"Erreur : {exc.orig}")
        with ab3:
            save_clicked = st.button(
                "✓ enregistrer",
                key=f"ed_save_top{sfx}",
                type="primary",
                use_container_width=True,
            )

    # ----- Two-column body -----
    col_form, col_ctx = st.columns([1.5, 1], gap="medium")

    # ════════════════════════════════════════════════════════════
    #  LEFT — staged form (4 sections)
    # ════════════════════════════════════════════════════════════
    with col_form:
        # -----------------------------------------------------------------
        # SECTION 1 — Identité du produit
        # -----------------------------------------------------------------
        with st.container(border=True):
            # Will compute triplet-valide chip after the fields are filled.
            sec1_header_placeholder = st.empty()

            # Reference name (required, full width).
            reference_name = st.text_input(
                "Nom de référence *",
                value=prefill.get("reference_name", ""),
                key=f"ed_reference_name{sfx}",
                placeholder="Buxus sempervirens · boule Ø40",
            )

            # Cascade: family → subcat → packaging → unité.
            # Unité (unit_type) lives in Section 1 with the rest of the
            # product identity — it's the billing unit (per piece, per
            # m3…) which is a product-scale attribute, not a
            # supplier-scale one. Placed next to Conditionnement so the
            # pair (packaging text + billing unit) stays visible together.
            c1, c2, c3, c4 = st.columns([1, 1.2, 1.3, 0.7])

            with c1:
                # Family options include a sentinel `-1` for "+ créer
                # nouveau…" so the user can grow the reference table
                # in-place without leaving the form. Same pattern as the
                # sous-catégorie / conditionnement selects below. The
                # actual INSERT into product_families happens in the save
                # block at the bottom of the function.
                FAMILY_NEW_ID = -1
                family_options = [f["id"] for f in families] + [FAMILY_NEW_ID]
                family_default_idx = (
                    family_options.index(prefill["family_id"])
                    if prefill.get("family_id") in family_options else 0
                )
                family_id = st.selectbox(
                    "Famille *",
                    options=family_options,
                    index=family_default_idx if family_options else 0,
                    format_func=lambda i: (
                        NEW_VALUE_SENTINEL if i == FAMILY_NEW_ID
                        else family_by_id.get(i, str(i))
                    ),
                    key=f"ed_family{sfx}",
                )
                new_family_name = ""
                if family_id == FAMILY_NEW_ID:
                    new_family_name = st.text_input(
                        "Nouvelle famille",
                        value="",
                        key=f"ed_family_new{sfx}",
                        placeholder="ex. Mobilier outdoor, Arrosage automatique…",
                    ).strip()

            with c2:
                existing_subs = subcats_by_family.get(family_id, [])
                sub_options = existing_subs + [NEW_VALUE_SENTINEL]
                sub_default = (
                    existing_subs.index(prefill["subcategory"])
                    if prefill.get("subcategory") in existing_subs else 0
                )
                chosen_sub = st.selectbox(
                    "Sous-catégorie *",
                    options=sub_options,
                    index=sub_default if existing_subs else 0,
                    key=f"ed_subcat{sfx}",
                )
                if chosen_sub == NEW_VALUE_SENTINEL:
                    chosen_sub = st.text_input(
                        "Nouvelle sous-catégorie",
                        value="",
                        key=f"ed_subcat_new{sfx}",
                        placeholder="Conifère, Topiaire, Aromatique…",
                    ).strip()
                subcategory = chosen_sub

            with c3:
                existing_packs = packagings_by_pair.get((family_id, subcategory), [])
                pack_options = existing_packs + [NEW_VALUE_SENTINEL]
                pack_default = (
                    existing_packs.index(prefill["packaging"])
                    if prefill.get("packaging") in existing_packs else 0
                )
                chosen_pack = st.selectbox(
                    "Conditionnement *",
                    options=pack_options,
                    index=pack_default if existing_packs else 0,
                    key=f"ed_pack{sfx}",
                )
                if chosen_pack == NEW_VALUE_SENTINEL:
                    chosen_pack = st.text_input(
                        "Nouveau conditionnement",
                        value="",
                        key=f"ed_pack_new{sfx}",
                        placeholder="Conteneur 7L, Sac 25kg…",
                    ).strip()
                packaging = chosen_pack

            with c4:
                unit_type = st.selectbox(
                    "Unité *",
                    options=UNIT_TYPES,
                    index=UNIT_TYPES.index(prefill.get("unit_type"))
                    if prefill.get("unit_type") in UNIT_TYPES else 0,
                    key=f"ed_unit{sfx}",
                    help=(
                        "Unité de facturation (u = pièce, m3, ml, m2, kg, l). "
                        "Doit correspondre à l'unité de la Norme de pose choisie."
                    ),
                )

            # Secondary identity row: brand + material
            cm1, cm2 = st.columns(2)
            with cm1:
                brand = st.text_input(
                    "Marque", value=prefill.get("brand") or "", key=f"ed_brand{sfx}"
                )
            with cm2:
                material = st.selectbox(
                    "Matériau",
                    options=MATERIAL_OPTIONS,
                    index=MATERIAL_OPTIONS.index(prefill.get("material"))
                    if prefill.get("material") in MATERIAL_OPTIONS else 0,
                    key=f"ed_material{sfx}",
                )

            # Triplet validity chip (computed AFTER fields above)
            triplet_complete = bool(family_id and subcategory and packaging)
            triplet_exists = (
                triplet_complete
                and subcategory in subcats_by_family.get(family_id, [])
                and packaging in packagings_by_pair.get((family_id, subcategory), [])
            )
            if not triplet_complete:
                triplet_chip = hf_chip("triplet incomplet", "ghost")
            elif triplet_exists:
                triplet_chip = hf_chip("triplet valide", "ok")
            else:
                triplet_chip = hf_chip("triplet à créer", "warn")

            sec1_header_placeholder.markdown(
                f'<div class="hf-row hf-between" style="margin-bottom:8px">'
                f'<h2 class="hf-h2" style="margin:0">1 · Identité du produit</h2>'
                f'{triplet_chip}</div>',
                unsafe_allow_html=True,
            )

            st.markdown(
                '<div class="hf-muted" style="font-size:11px;margin-top:4px">'
                "↳ chaque liste propose <code style='font-family:JetBrains Mono,monospace;"
                "background:var(--hf-cream);padding:1px 5px;border-radius:3px;font-size:10.5px'>"
                "+ créer nouveau…</code> pour ajouter un triplet manquant au référentiel."
                "</div>",
                unsafe_allow_html=True,
            )

        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

        # -----------------------------------------------------------------
        # SECTION 2 — Prix & fournisseur
        # -----------------------------------------------------------------
        with st.container(border=True):
            st.markdown(
                '<div class="hf-row hf-between" style="margin-bottom:8px">'
                '<h2 class="hf-h2" style="margin:0">2 · Prix &amp; fournisseur</h2>'
                '<span class="hf-muted" style="font-size:11px">coûts HT uniquement · pas de marge ici</span>'
                '</div>',
                unsafe_allow_html=True,
            )

            # Unité moved up to Section 1 — it's a product-scale property,
            # not a supplier-scale one, and was a duplicate next to
            # Conditionnement. Section 2 keeps only the supplier-scale
            # fields: Fournisseur, Coût HT, Source.
            ps1, ps2, ps3 = st.columns([2, 1, 1.3])
            with ps1:
                supplier_id_options = [s["id"] for s in suppliers]
                sup_default = (
                    supplier_id_options.index(prefill["supplier_id"])
                    if prefill.get("supplier_id") in supplier_id_options else 0
                )
                supplier_id = st.selectbox(
                    "Fournisseur *",
                    options=supplier_id_options,
                    index=sup_default if supplier_id_options else 0,
                    format_func=lambda i: supplier_by_id.get(i, str(i)),
                    key=f"ed_supplier{sfx}",
                )
            with ps2:
                cost_ht_default = float(prefill["cost_ht"]) if prefill.get("cost_ht") is not None else 0.0
                cost_ht = st.number_input(
                    "Coût HT (€) *",
                    min_value=0.0,
                    value=cost_ht_default,
                    step=0.01,
                    format="%.2f",
                    key=f"ed_cost_ht{sfx}",
                )
            with ps3:
                source = st.selectbox(
                    "Source",
                    options=SOURCE_OPTIONS,
                    index=0,  # 'manuel' default for hand edits
                    key=f"ed_source{sfx}",
                    help=(
                        "Étiquette de la source pour l'audit. 'manuel' par défaut "
                        "pour les éditions à la main. Les valeurs supplier_catalog "
                        "etc. viennent automatiquement de la page Ingestion."
                    ),
                )

            # Cost-diff chip — only meaningful in edit mode
            if is_edit:
                db_cost = float(prefill.get("cost_ht") or 0)
                if abs(cost_ht - db_cost) > 0.001:
                    prev_date = "—"
                    if not history_df.empty:
                        prev_date = pd.to_datetime(history_df.iloc[-1]["recorded_at"]).strftime("%d/%m/%Y")
                    chip = hf_chip("🟢 ce prix sera enregistré dans l'historique", "ok")
                    st.markdown(
                        f'<div class="hf-row" style="gap:10px;margin-top:8px;align-items:center;font-size:11.5px">'
                        f'{chip}'
                        f'<span class="hf-muted">précédent : {_fmt_eur(db_cost)} · {prev_date}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )

        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

        # -----------------------------------------------------------------
        # SECTION 3 — Pose
        # -----------------------------------------------------------------
        with st.container(border=True):
            st.markdown(
                '<h2 class="hf-h2" style="margin:0 0 8px 0">3 · Pose</h2>',
                unsafe_allow_html=True,
            )
            # Sentinel for inline "+ créer nouveau…" — INSERT into
            # labor_norms happens in the save block once the user submits
            # the whole form.
            LABOR_NEW_ID = -1
            labor_norm_options = [ln["id"] for ln in labor_norms] + [LABOR_NEW_ID]
            ln_default = (
                labor_norm_options.index(prefill["labor_norm_id"])
                if prefill.get("labor_norm_id") in labor_norm_options else 0
            )

            # When the user picks "+ créer nouveau…", the right-side
            # readouts (heure_u_pose / nombre_uth) lose meaning — the
            # values are still being typed in the inline form below.
            # Render only the select at full width in that case.
            _is_new_labor = (
                st.session_state.get(f"ed_labor{sfx}") == LABOR_NEW_ID
                or (
                    f"ed_labor{sfx}" not in st.session_state
                    and labor_norm_options[ln_default] == LABOR_NEW_ID
                )
            )

            if _is_new_labor:
                labor_norm_id = st.selectbox(
                    "Norme de pose",
                    options=labor_norm_options,
                    index=ln_default if labor_norm_options else 0,
                    format_func=lambda i: (
                        NEW_VALUE_SENTINEL if i == LABOR_NEW_ID
                        else labor_lookup.get(i, str(i))
                    ),
                    key=f"ed_labor{sfx}",
                )
            else:
                pose1, pose2, pose3 = st.columns([2, 1, 1])
                with pose1:
                    labor_norm_id = st.selectbox(
                        "Norme de pose",
                        options=labor_norm_options,
                        index=ln_default if labor_norm_options else 0,
                        format_func=lambda i: (
                            NEW_VALUE_SENTINEL if i == LABOR_NEW_ID
                            else labor_lookup.get(i, str(i))
                        ),
                        key=f"ed_labor{sfx}",
                    )
                ln = labor_by_id.get(labor_norm_id, {})
                # Read-only metric values shown next to the Norme select.
                # The label margin-bottom + value min-height match the
                # Streamlit widget label margin (6px) + the input
                # min-height (34px) so these line up vertically with the
                # select control to the left of them.
                _ro_label_css = (
                    "font-size:10.5px;color:var(--hf-muted);"
                    "letter-spacing:0.04em;text-transform:uppercase;"
                    "font-weight:600;margin:0 0 6px 0;line-height:1.45"
                )
                _ro_value_css = (
                    "font-weight:600;font-size:14px;color:var(--hf-ink);"
                    "min-height:34px;display:flex;align-items:center;"
                    "border-bottom:1px solid var(--hf-border-soft)"
                )
                with pose2:
                    st.markdown(
                        f'<div style="{_ro_label_css}">heure_u_pose</div>'
                        f'<div class="hf-mono" style="{_ro_value_css}">'
                        f'{float(ln.get("heure_u_pose_default") or 0):.3f} h</div>',
                        unsafe_allow_html=True,
                    )
                with pose3:
                    st.markdown(
                        f'<div style="{_ro_label_css}">nombre_uth</div>'
                        f'<div class="hf-mono" style="{_ro_value_css}">'
                        f'{float(ln.get("nombre_uth_default") or 0):.2f}</div>',
                        unsafe_allow_html=True,
                    )

            # Inline new-norm sub-form (full parity with the Normes de
            # pose dialog). All values are captured in local variables
            # and submitted to labor_norms in the save block below.
            new_norm_task_name = ""
            new_norm_unit_type = unit_type  # default to product's unit
            new_norm_uth = 1.0
            new_norm_heure = 0.0
            new_norm_t1_label = "facile"
            new_norm_t1_h = 0.0
            new_norm_t2_label = "moyen"
            new_norm_t2_h = 0.0
            new_norm_t3_label = "difficile"
            new_norm_t3_h = 0.0
            new_norm_notes = ""

            if labor_norm_id == LABOR_NEW_ID:
                with st.container(border=True):
                    st.markdown(
                        '<div class="hf-row hf-between" style="margin-bottom:6px">'
                        '<h3 class="hf-h2" style="margin:0;font-size:13px">'
                        '↳ Nouvelle norme de pose</h3>'
                        '<span class="hf-muted" style="font-size:11px">'
                        'sera créée à l\'enregistrement</span>'
                        '</div>',
                        unsafe_allow_html=True,
                    )
                    new_norm_task_name = st.text_input(
                        "Nom de la tâche *",
                        value="",
                        key=f"ed_new_norm_task{sfx}",
                        placeholder="ex. Plantation arbre 10/12, Pose paillage…",
                    ).strip()

                    nn_a, nn_b = st.columns(2)
                    with nn_a:
                        new_norm_unit_type = st.selectbox(
                            "Unité *",
                            options=UNIT_TYPES,
                            index=UNIT_TYPES.index(unit_type)
                            if unit_type in UNIT_TYPES else 0,
                            key=f"ed_new_norm_unit{sfx}",
                            help="Doit correspondre à l'unité du produit ci-dessus.",
                        )
                        new_norm_uth = st.number_input(
                            "UTH par défaut",
                            min_value=0.0, max_value=99.0,
                            value=1.0,
                            step=0.25,
                            key=f"ed_new_norm_uth{sfx}",
                        )
                        new_norm_heure = st.number_input(
                            "Heure pose / unité *",
                            min_value=0.0,
                            value=0.0,
                            step=0.001, format="%.3f",
                            key=f"ed_new_norm_heure{sfx}",
                        )
                    with nn_b:
                        new_norm_t1_label = st.text_input(
                            "Tier 1 — label",
                            value="facile",
                            key=f"ed_new_norm_t1_label{sfx}",
                        )
                        new_norm_t1_h = st.number_input(
                            "Tier 1 — heure",
                            min_value=0.0,
                            value=st.session_state.pop(
                                f"_pending_new_t1{sfx}", 0.0
                            ),
                            step=0.001, format="%.3f",
                            key=f"ed_new_norm_t1_h{sfx}",
                        )
                        new_norm_t2_label = st.text_input(
                            "Tier 2 — label",
                            value="moyen",
                            key=f"ed_new_norm_t2_label{sfx}",
                        )
                        new_norm_t2_h = st.number_input(
                            "Tier 2 — heure",
                            min_value=0.0,
                            value=st.session_state.pop(
                                f"_pending_new_t2{sfx}", 0.0
                            ),
                            step=0.001, format="%.3f",
                            key=f"ed_new_norm_t2_h{sfx}",
                        )
                        new_norm_t3_label = st.text_input(
                            "Tier 3 — label",
                            value="difficile",
                            key=f"ed_new_norm_t3_label{sfx}",
                        )
                        new_norm_t3_h = st.number_input(
                            "Tier 3 — heure",
                            min_value=0.0,
                            value=st.session_state.pop(
                                f"_pending_new_t3{sfx}", 0.0
                            ),
                            step=0.001, format="%.3f",
                            key=f"ed_new_norm_t3_h{sfx}",
                        )
                    new_norm_notes = st.text_area(
                        "Notes",
                        value="",
                        height=60,
                        key=f"ed_new_norm_notes{sfx}",
                    )

                    # Auto-fill button: rewrites the 3 tier number_inputs
                    # using the ×1 / ×2 / ×3 default scheme from the
                    # Normes de pose page. Same session_state baton
                    # pattern: stash the target values under `_pending_…`
                    # keys, drop the widget keys, rerun so the next
                    # render reads our `value=` instead of the user's
                    # last entry.
                    if st.button(
                        "↻ Auto-remplir décharges (×1 / ×2 / ×3)",
                        key=f"ed_new_norm_autofill{sfx}",
                        use_container_width=True,
                    ):
                        if new_norm_heure and new_norm_heure > 0:
                            st.session_state[f"_pending_new_t1{sfx}"] = float(new_norm_heure) * 1.0
                            st.session_state[f"_pending_new_t2{sfx}"] = float(new_norm_heure) * 2.0
                            st.session_state[f"_pending_new_t3{sfx}"] = float(new_norm_heure) * 3.0
                            for _k in (
                                f"ed_new_norm_t1_h{sfx}",
                                f"ed_new_norm_t2_h{sfx}",
                                f"ed_new_norm_t3_h{sfx}",
                            ):
                                st.session_state.pop(_k, None)
                            st.rerun()
                        else:
                            st.warning(
                                "Renseignez d'abord « Heure pose / unité » avant "
                                "l'auto-remplissage."
                            )

            st.markdown(
                '<div class="hf-muted" style="font-size:11px;margin-top:8px">'
                "↳ valeurs reprises de la norme. Pour les modifier en détail, "
                "passez par la page « Normes de pose »."
                "</div>",
                unsafe_allow_html=True,
            )

        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

        # -----------------------------------------------------------------
        # SECTION 4 — Attributs
        # -----------------------------------------------------------------
        with st.container(border=True):
            st.markdown(
                '<div class="hf-row hf-between" style="margin-bottom:6px;align-items:baseline">'
                '<div class="hf-row" style="gap:8px;align-items:baseline">'
                '<h2 class="hf-h2" style="margin:0">4 · Attributs</h2>'
                '<span class="hf-muted" style="font-size:11px">propriétés libres (clé en français → valeur)</span>'
                '</div>'
                '</div>',
                unsafe_allow_html=True,
            )

            # Initial attributes from DB (or session_state if we've been mutating)
            session_attrs_key = f"ed_attrs_{edit_id or 'new'}"
            if session_attrs_key not in st.session_state:
                raw_attrs = prefill.get("attributes") or {}
                if isinstance(raw_attrs, str):
                    try:
                        raw_attrs = json.loads(raw_attrs)
                    except json.JSONDecodeError:
                        raw_attrs = {}
                st.session_state[session_attrs_key] = (
                    [{"clé": k, "valeur": v} for k, v in raw_attrs.items()]
                    or [{"clé": "", "valeur": ""}]
                )

            attr_df = pd.DataFrame(st.session_state[session_attrs_key])
            edited_attrs = st.data_editor(
                attr_df,
                num_rows="dynamic",
                use_container_width=True,
                key=f"ed_attr_editor_{edit_id or 'new'}",
                column_config={
                    "clé": st.column_config.TextColumn(width="small"),
                    "valeur": st.column_config.TextColumn(width="large"),
                },
            )

            # (Removed: per-triplet "suggéré pour ce triplet" hint chips.
            # The data_editor's `num_rows="dynamic"` row already lets the
            # user add any key/value freely, so the hint chips were noise
            # without strong UX value.)

    # ════════════════════════════════════════════════════════════
    #  RIGHT — context panel
    # ════════════════════════════════════════════════════════════
    with col_ctx:
        # ----- Card 1 — Historique de coût (edit-only) -----
        # No prior history exists for a brand-new product, so we skip the
        # card entirely in Nouveau produit mode rather than show an "Aucun
        # historique" placeholder. The 2-space `if`/`with` step keeps the
        # card body's indentation at 12 (no re-indent of ~100 lines below).
        if is_edit:
          with st.container(border=True):
            n_changes = len(history_df) if not history_df.empty else 0
            st.markdown(
                f'<div class="hf-row hf-between" style="margin-bottom:6px">'
                f'<h2 class="hf-h2" style="margin:0">Historique de coût</h2>'
                f'<span class="hf-muted" style="font-size:11px">{n_changes} changement(s)</span>'
                f'</div>',
                unsafe_allow_html=True,
            )
            if n_changes >= 2:
                line = (
                    alt.Chart(history_df)
                    .mark_line(color=GREEN, strokeWidth=1.8)
                    .encode(
                        x=alt.X("recorded_at:T", axis=None),
                        y=alt.Y("cost_ht:Q", axis=None, scale=alt.Scale(zero=False)),
                    )
                )
                # DPGF-sourced points (negotiated client prices) get their own
                # TERRA dots all along the line so Vincent spots them at a
                # glance without reading the list below.
                dpgf_pts = (
                    alt.Chart(
                        history_df[history_df["source"].astype(str).str.lower() == "dpgf_return"]
                    )
                    .mark_point(color=TERRA, size=55, filled=True, opacity=0.9)
                    .encode(x="recorded_at:T", y="cost_ht:Q")
                )
                end_dot = (
                    alt.Chart(history_df.iloc[-1:])
                    .mark_point(color=TERRA, size=80, filled=True)
                    .encode(x="recorded_at:T", y="cost_ht:Q")
                )
                chart = (
                    (line + dpgf_pts + end_dot)
                    .properties(height=64)
                    .configure_view(strokeWidth=0)
                )
                st.altair_chart(chart, use_container_width=True)

                latest_price = float(history_df.iloc[-1]["cost_ht"])
                prev_price = float(history_df.iloc[-2]["cost_ht"])
                delta = latest_price - prev_price
                pct = (delta / prev_price * 100) if prev_price else 0.0
                delta_color = LEAF if delta > 0 else (TERRA if delta < 0 else "var(--hf-muted)")
                delta_sign = "+" if delta > 0 else ""
                prev_date = pd.to_datetime(history_df.iloc[-2]["recorded_at"]).strftime("%d/%m/%Y")
                st.markdown(
                    f'<div class="hf-row hf-between" style="align-items:baseline;margin-top:8px">'
                    f'<div>'
                    f'<div class="hf-mono hf-muted" style="font-size:10px">aujourd\'hui</div>'
                    f'<div style="font-weight:600;font-size:22px;color:var(--hf-ink);line-height:1">{_fmt_eur(latest_price)}</div>'
                    f'</div>'
                    f'<div style="text-align:right">'
                    f'<div style="font-size:11.5px;color:{delta_color}">'
                    f'{delta_sign}{f"{delta:.2f}".replace(".", ",")} € ({delta_sign}{f"{pct:.1f}".replace(".", ",")} %)</div>'
                    f'<div class="hf-muted" style="font-size:11px">vs {prev_date}</div>'
                    f'</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            elif is_edit and n_changes == 1:
                p0 = float(history_df.iloc[0]["cost_ht"])
                st.markdown(
                    f'<div class="hf-row hf-between" style="align-items:baseline;margin-top:8px">'
                    f'<div>'
                    f'<div class="hf-mono hf-muted" style="font-size:10px">aujourd\'hui</div>'
                    f'<div style="font-weight:600;font-size:22px;color:var(--hf-ink);line-height:1">{_fmt_eur(p0)}</div>'
                    f'</div>'
                    f'<div class="hf-muted" style="font-size:11px">création</div>'
                    f'</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.markdown(
                    '<div class="hf-muted" style="font-size:11px;padding:8px 0">'
                    "Aucun historique pour ce produit (création en cours)."
                    "</div>",
                    unsafe_allow_html=True,
                )

            if n_changes > 0:
                st.markdown(
                    '<hr style="border:none;border-top:1px solid var(--hf-border-soft);margin:10px 0 8px">',
                    unsafe_allow_html=True,
                )
                moves = history_df.iloc[::-1].head(5)
                rows_html = []
                last_idx = len(moves) - 1
                for j, (_, h) in enumerate(moves.iterrows()):
                    d = pd.to_datetime(h["recorded_at"]).strftime("%d/%m/%y")
                    v = f"{float(h['cost_ht']):,.2f} €".replace(",", " ")
                    # Source-aware styling: DPGF returns (negotiated client
                    # prices or supplier costs ingested from a signed DPGF)
                    # get a TERRA-tinted background + a small "DPGF" tag so
                    # they read distinctly from regular supplier prices in
                    # the cost timeline.
                    is_dpgf = (str(h.get("source") or "")).lower() == "dpgf_return"
                    row_style = "font-size:10.5px;line-height:1.65;padding:2px 6px;border-radius:3px;"
                    if is_dpgf:
                        row_style += (
                            "background:var(--hf-terra-soft);"
                            "border-left:2px solid var(--hf-terra);"
                            "padding-left:6px;"
                        )
                    suffix_html = ""
                    if is_dpgf:
                        suffix_html = (
                            ' <span style="font-size:9px;color:var(--hf-terra);'
                            'font-weight:600;letter-spacing:0.06em;'
                            'text-transform:uppercase;margin-left:4px">dpgf</span>'
                        )
                    if j == 0:
                        rows_html.append(
                            f'<div class="hf-row hf-between" style="{row_style}">'
                            f'<span class="hf-mono" style="color:var(--hf-body)">{d}</span>'
                            f'<span style="font-weight:600">{v}{suffix_html}</span></div>'
                        )
                    elif j == last_idx:
                        rows_html.append(
                            f'<div class="hf-row hf-between" style="{row_style}">'
                            f'<span class="hf-mono hf-muted">{d}</span>'
                            f'<span class="hf-muted">{v} · création{suffix_html}</span></div>'
                        )
                    else:
                        rows_html.append(
                            f'<div class="hf-row hf-between" style="{row_style}">'
                            f'<span class="hf-mono hf-muted">{d}</span>'
                            f'<span>{v}{suffix_html}</span></div>'
                        )
                st.markdown(
                    '<div style="font-family:JetBrains Mono,monospace;'
                    'display:flex;flex-direction:column;gap:2px">'
                    + "".join(rows_html) + "</div>",
                    unsafe_allow_html=True,
                )

        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

        # ----- Card 2 — Aussi disponible chez -----
        with st.container(border=True):
            st.markdown(
                '<div class="hf-row hf-between" style="margin-bottom:6px">'
                '<h2 class="hf-h2" style="margin:0">Aussi disponible chez</h2>'
                '<span class="hf-muted" style="font-size:11px">même triplet</span>'
                '</div>',
                unsafe_allow_html=True,
            )
            if triplet_complete:
                others = fetch_all(
                    """
                    SELECT s.name AS supplier_name, p.cost_ht, p.last_price_update,
                           CASE
                             WHEN p.last_price_update < now() - INTERVAL '9 months' THEN 'stale_9mo'
                             WHEN p.last_price_update < now() - INTERVAL '6 months' THEN 'stale_6mo'
                             ELSE 'fresh'
                           END AS freshness_status,
                           EXTRACT(DAY FROM AGE(now(), p.last_price_update))::int AS days_old
                      FROM products p
                      JOIN suppliers s ON s.id = p.supplier_id
                     WHERE p.is_active = TRUE
                       AND p.family_id = :fid
                       AND p.subcategory = :sub
                       AND p.packaging = :pkg
                       AND p.id <> COALESCE(:eid, -1)
                     ORDER BY p.cost_ht ASC
                     LIMIT 6
                    """,
                    {
                        "fid": family_id,
                        "sub": subcategory,
                        "pkg": packaging,
                        "eid": edit_id,
                    },
                )
                if not others:
                    st.markdown(
                        '<div class="hf-muted" style="font-size:11.5px;padding:4px 0">'
                        "Aucun autre fournisseur ne référence ce triplet pour l'instant."
                        "</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    items_html = []
                    for o in others:
                        dot = _freshness_dot(o.get("freshness_status") or "fresh")
                        days = int(o.get("days_old") or 0)
                        items_html.append(
                            f'<div class="hf-row hf-between" style="font-size:12px;padding:3px 0">'
                            f'<span>{o["supplier_name"]}</span>'
                            f'<span><span style="font-weight:600">{_fmt_eur(o["cost_ht"])}</span> '
                            f'<span class="hf-muted" style="font-size:10.5px">· {dot} {days} j</span></span>'
                            f'</div>'
                        )
                    st.markdown(
                        '<div class="hf-col">' + "".join(items_html) + "</div>",
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    '<div class="hf-muted" style="font-size:11.5px;padding:4px 0">'
                    "Renseignez le triplet pour voir les autres fournisseurs."
                    "</div>",
                    unsafe_allow_html=True,
                )

        st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

        # ----- Card 3 — Chaîne pour la DPGF (dark, one-click copy) -----
        #
        # Renders inside an HTML component so the Copier button can call
        # `navigator.clipboard.writeText()` directly — one click writes the
        # chain into the user's clipboard with a brief "✓ Copié !" toast
        # (handled by inline JS, no Streamlit rerun needed). Without this
        # we'd have a 2-click path: Streamlit button → `st.code` block →
        # the code-block's hover-revealed copy icon.
        picker_str = (
            f"{family_by_id.get(family_id, '?')} — {subcategory or '?'} — "
            f"{reference_name or '?'} — {packaging or '?'}"
        )
        # Stash the chain in a data-attribute (HTML-escape it) and read it
        # from JS via `this.dataset.chain` — that way we never embed quotes
        # or `=>` arrows inside an `onclick="…"` attribute (which would
        # break the HTML parser). The JS function itself is defined once
        # in a <script> block at the top so the onclick handler stays
        # short and quote-safe.
        from html import escape as _html_escape
        picker_attr = _html_escape(picker_str, quote=True)
        components.html(
            f"""
            <html><head><style>
              body {{
                margin: 0; padding: 0;
                background: transparent;
                font-family: 'Inter', system-ui, -apple-system, sans-serif;
              }}
              .card {{
                background: #1d3a2a;
                border: 1px solid #244a36;
                border-radius: 6px;
                padding: 12px 14px;
                color: #faf5e6;
                box-shadow: 0 1px 0 rgba(17,22,19,0.04), 0 2px 6px rgba(17,22,19,0.06);
              }}
              .head {{
                display: flex; justify-content: space-between; align-items: baseline;
                margin-bottom: 6px;
              }}
              .title {{
                margin: 0; font-size: 11.5px; text-transform: uppercase;
                letter-spacing: 0.06em; font-weight: 600; color: #b6c5b3;
              }}
              .sub {{ font-size: 11px; color: #8aa088; }}
              .chain {{
                font-family: 'JetBrains Mono', monospace;
                font-size: 11px; color: #faf5e6;
                line-height: 1.45;
                padding: 8px 10px;
                background: rgba(0,0,0,0.25);
                border-radius: 4px;
                word-break: break-all;
                margin-bottom: 10px;
              }}
              .btn {{
                background: transparent; color: #faf5e6;
                border: 1px solid rgba(250,245,230,0.3);
                border-radius: 4px;
                padding: 8px 14px;
                width: 100%;
                font: 500 12px 'Inter', sans-serif;
                cursor: pointer;
                transition: background 0.12s ease, border-color 0.12s ease;
              }}
              .btn:hover {{ background: rgba(255,255,255,0.06); border-color: rgba(250,245,230,0.5); }}
              .btn.copied {{ background: rgba(58, 125, 82, 0.35); border-color: rgba(58, 125, 82, 0.6); }}
            </style>
            <script>
              function copyChain(btn) {{
                var txt = btn.getAttribute('data-chain');
                navigator.clipboard.writeText(txt).then(function() {{
                  btn.textContent = '\\u2713 Copié dans le presse-papiers';
                  btn.classList.add('copied');
                  setTimeout(function() {{
                    btn.textContent = '\\u2398 Copier la chaîne';
                    btn.classList.remove('copied');
                  }}, 1800);
                }}).catch(function(err) {{
                  btn.textContent = '\\u2717 Erreur — copier manuellement';
                  console.error('clipboard write failed', err);
                }});
              }}
            </script>
            </head><body>
              <div class="card">
                <div class="head">
                  <h2 class="title">Chaîne pour la DPGF (col. AG)</h2>
                  <span class="sub">copiée dans la colonne AG</span>
                </div>
                <div class="chain">{picker_str}</div>
                <button class="btn" data-chain="{picker_attr}" onclick="copyChain(this)">⎘ Copier la chaîne</button>
              </div>
            </body></html>
            """,
            height=160,
        )

        # ----- Footer — audit -----
        created_str = ""
        last_edit_str = ""
        if is_edit:
            created_at = prefill.get("created_at")
            if created_at:
                created_str = pd.to_datetime(created_at).strftime("%d/%m/%Y")
            updated_at = prefill.get("updated_at")
            if updated_at:
                last_edit_str = pd.to_datetime(updated_at).strftime("%d/%m/%Y %H:%M")
            last_recorder = (
                history_df.iloc[-1]["recorded_by"] if not history_df.empty else None
            )
            audit_lines = [
                f"créé · {created_str or '?'}",
                f"dernière édition · {last_recorder or '—'} · {last_edit_str or '?'}",
            ]
            st.markdown(
                '<div style="font-size:10.5px;color:var(--hf-muted);line-height:1.5;padding:6px 4px 0">'
                + "<br>".join(audit_lines) +
                "</div>",
                unsafe_allow_html=True,
            )

    # ════════════════════════════════════════════════════════════
    #  SAVE  (handled via the top-right ✓ Enregistrer button)
    # ════════════════════════════════════════════════════════════
    if save_clicked:
        # Validate
        missing = []
        if not reference_name.strip():
            missing.append("Nom de référence")
        if family_id is None or (family_id == FAMILY_NEW_ID and not new_family_name):
            missing.append("Famille (choisir ou nommer la nouvelle)")
        if not subcategory:
            missing.append("Sous-catégorie")
        if not packaging:
            missing.append("Conditionnement")
        if cost_ht <= 0:
            missing.append("Coût HT (> 0)")
        if labor_norm_id == LABOR_NEW_ID:
            if not new_norm_task_name:
                missing.append("Nom de la tâche (nouvelle norme)")
            if not new_norm_heure or new_norm_heure <= 0:
                missing.append("Heure pose / unité (nouvelle norme, > 0)")
        if missing:
            st.error("Champs obligatoires manquants : " + ", ".join(missing))
        else:
            # Build attrs dict from data_editor
            attrs: dict[str, str] = {}
            for _, row_a in edited_attrs.iterrows():
                k = (str(row_a.get("clé") or "")).strip()
                v = (str(row_a.get("valeur") or "")).strip()
                if k:
                    attrs[k] = v

            actor_source = source if source else "manuel"
            try:
                # If the user chose "+ créer nouveau…" for the famille,
                # INSERT the family FIRST and resolve to its new id so the
                # FK target exists before we touch product_taxonomy and
                # products.
                if family_id == FAMILY_NEW_ID:
                    new_fam_row = fetch_one(
                        """
                        INSERT INTO product_families (name)
                        VALUES (:name)
                        ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name
                        RETURNING id
                        """,
                        {"name": new_family_name},
                    )
                    family_id = int(new_fam_row["id"])
                    st.toast(
                        f"✓ Famille « {new_family_name} » créée",
                        icon="🌿",
                    )

                # Same pattern for the labor_norm sentinel — INSERT first
                # so the FK target exists when we touch products. If the
                # user left tier values at 0 (i.e. didn't run
                # auto-remplir), fall back to ×1 / ×2 / ×3 of the pose
                # time so the NOT NULL constraints on tier_*_decharge
                # are satisfied.
                if labor_norm_id == LABOR_NEW_ID:
                    _t1 = float(new_norm_t1_h) if new_norm_t1_h > 0 else float(new_norm_heure) * 1.0
                    _t2 = float(new_norm_t2_h) if new_norm_t2_h > 0 else float(new_norm_heure) * 2.0
                    _t3 = float(new_norm_t3_h) if new_norm_t3_h > 0 else float(new_norm_heure) * 3.0
                    new_ln_row = fetch_one(
                        """
                        INSERT INTO labor_norms (
                            task_name, unit_type, nombre_uth_default, heure_u_pose_default,
                            tier_1_label, tier_1_heure_u_decharge,
                            tier_2_label, tier_2_heure_u_decharge,
                            tier_3_label, tier_3_heure_u_decharge,
                            notes
                        ) VALUES (
                            :task_name, :unit_type, :uth, :heure,
                            :t1_label, :t1_h,
                            :t2_label, :t2_h,
                            :t3_label, :t3_h,
                            :notes
                        )
                        RETURNING id
                        """,
                        {
                            "task_name": new_norm_task_name,
                            "unit_type": new_norm_unit_type,
                            "uth": float(new_norm_uth),
                            "heure": float(new_norm_heure),
                            "t1_label": new_norm_t1_label.strip() or "facile",
                            "t1_h": _t1,
                            "t2_label": new_norm_t2_label.strip() or "moyen",
                            "t2_h": _t2,
                            "t3_label": new_norm_t3_label.strip() or "difficile",
                            "t3_h": _t3,
                            "notes": new_norm_notes.strip() or None,
                        },
                    )
                    labor_norm_id = int(new_ln_row["id"])
                    st.toast(
                        f"✓ Norme « {new_norm_task_name} » créée",
                        icon="🌿",
                    )

                params = {
                    "reference_name": reference_name.strip(),
                    "family_id": family_id,
                    "subcategory": subcategory,
                    "supplier_id": supplier_id,
                    "labor_norm_id": labor_norm_id,
                    "brand": brand.strip() or None,
                    "material": material or None,
                    "packaging": packaging,
                    "unit_type": unit_type,
                    "cost_ht": float(cost_ht),
                    "attributes": json.dumps(attrs, ensure_ascii=False),
                    "notes": None,
                    "is_active": prefill.get("is_active", True) if is_edit else True,
                }
                # Ensure the taxonomy row exists FIRST (FK target). The
                # composite FK on products would otherwise reject the insert.
                if not triplet_exists:
                    execute(
                        """
                        INSERT INTO product_taxonomy
                            (family_id, subcategory, packaging, created_by, notes)
                        VALUES (:fid, :sub, :pkg, :by, 'Créé depuis Produits · Édition')
                        ON CONFLICT (family_id, subcategory, packaging) DO NOTHING
                        """,
                        {
                            "fid": family_id, "sub": subcategory, "pkg": packaging,
                            "by": "produits_edition",
                        },
                    )

                if is_edit:
                    params["id"] = edit_id
                    execute(
                        """
                        UPDATE products SET
                            reference_name = :reference_name,
                            family_id      = :family_id,
                            subcategory    = :subcategory,
                            supplier_id    = :supplier_id,
                            labor_norm_id  = :labor_norm_id,
                            brand          = :brand,
                            material       = :material,
                            packaging      = :packaging,
                            unit_type      = :unit_type,
                            cost_ht        = :cost_ht,
                            attributes     = CAST(:attributes AS jsonb),
                            notes          = :notes,
                            is_active      = :is_active
                        WHERE id = :id
                        """,
                        params,
                        ingestion_source=actor_source if actor_source in (
                            "supplier_catalog", "historical_devis", "dpgf_return"
                        ) else "admin_streamlit",
                    )
                    st.toast(f"✓ Produit #{edit_id} mis à jour", icon="🌿")
                else:
                    execute(
                        """
                        INSERT INTO products
                            (reference_name, family_id, subcategory, supplier_id,
                             labor_norm_id, brand, material, packaging, unit_type,
                             cost_ht, attributes, notes, is_active)
                        VALUES
                            (:reference_name, :family_id, :subcategory, :supplier_id,
                             :labor_norm_id, :brand, :material, :packaging, :unit_type,
                             :cost_ht, CAST(:attributes AS jsonb), :notes, :is_active)
                        """,
                        params,
                        ingestion_source=actor_source if actor_source in (
                            "supplier_catalog", "historical_devis", "dpgf_return"
                        ) else "admin_streamlit",
                    )
                    st.toast("✓ Nouveau produit créé", icon="🌿")
                # Reset all edit state so the user lands clean on the
                # catalogue: drop product_edit_id, the row selection, the
                # dataframe widget state, and our attribute scratch.
                st.session_state.pop("product_edit_id", None)
                st.session_state.pop("cat_selected_pid", None)
                st.session_state.pop("catalog_grid", None)
                st.session_state.pop(f"ed_attrs_{edit_id or 'new'}", None)
                st.session_state["force_produits_tab"] = "Catalogue"
                st.rerun()
            except IntegrityError as exc:
                msg = str(exc.orig)
                if "products_reference_name_packaging_supplier_id_key" in msg:
                    st.error(
                        "Un produit avec ce nom, ce conditionnement et ce fournisseur "
                        "existe déjà."
                    )
                elif "fk_products_taxonomy" in msg:
                    st.error(
                        "Le triplet (Famille, Sous-catégorie, Conditionnement) n'existe "
                        "pas dans le référentiel. Ajoutez-le d'abord, puis réessayez."
                    )
                elif "labor_norms_task_name_key" in msg:
                    st.error(
                        f"Une norme nommée « {new_norm_task_name} » existe déjà. "
                        "Choisissez-la dans la liste ou renommez la nouvelle norme."
                    )
                elif "products_cost_ht_check" in msg:
                    st.error("Le coût HT doit être ≥ 0.")
                else:
                    st.error(f"Erreur d'intégrité : {msg}")
            except Exception as exc:
                st.error(f"Erreur : {exc}")



# ============================================================================
#  Mount the form for the standalone create flow.
#
# In Catalogue mode, the form is rendered inline below the dataframe when a
# row is selected (so the user edits a product without leaving the table).
# In Nouveau produit mode, we mount it here as the page's main body.
# ============================================================================


with tab_catalogue:
  if _mode == "Catalogue":
    cat_sql = """
        SELECT
            p.id, p.reference_name,
            pf.name AS family_name,
            p.subcategory,
            p.brand, p.material, p.packaging, p.unit_type,
            p.cost_ht,
            s.name AS supplier_name,
            ln.task_name AS labor_task,
            p.last_price_update,
            CASE
                WHEN p.last_price_update < now() - INTERVAL '9 months' THEN 'stale_9mo'
                WHEN p.last_price_update < now() - INTERVAL '6 months' THEN 'stale_6mo'
                ELSE 'fresh'
            END AS freshness_status
        FROM products p
        JOIN product_families pf ON pf.id = p.family_id
        JOIN suppliers s         ON s.id = p.supplier_id
        JOIN labor_norms ln      ON ln.id = p.labor_norm_id
        WHERE p.is_active = TRUE
        ORDER BY pf.name, p.subcategory, p.reference_name
    """
    cat_df = fetch_df(cat_sql)

    if cat_df.empty:
        st.info(
            "Le catalogue est vide. Ajoutez des produits via la page **Ingestion facture** "
            "ou l'onglet **Édition** ci-contre."
        )
    else:
        # Cascading filter values from the live catalog data.
        suppliers_in_catalog = ["Tous"] + sorted(cat_df["supplier_name"].dropna().unique().tolist())
        families_in_catalog = ["Toutes"] + sorted(cat_df["family_name"].dropna().unique().tolist())
        all_subcats = ["—"] + sorted(cat_df["subcategory"].dropna().unique().tolist())
        all_packagings = ["—"] + sorted(cat_df["packaging"].dropna().unique().tolist())

        # DPGF-style cascade: Famille → Sous-catégorie → Conditionnement,
        # then Fournisseur / Fraîcheur / Recherche as orthogonal filters.
        f1, f2, f3, f4, f5, f6, f7 = st.columns([1, 1.2, 1.5, 1.4, 1, 1.5, 0.7])

        with f1:
            f_family = st.selectbox("Famille", options=families_in_catalog, key="cat_family")
        # Sous-catégorie options cascade from Famille
        if f_family != "Toutes":
            subcat_choices = ["—"] + sorted(
                cat_df.loc[cat_df["family_name"] == f_family, "subcategory"]
                .dropna().unique().tolist()
            )
        else:
            subcat_choices = all_subcats
        with f2:
            f_subcat = st.selectbox("Sous-catégorie", options=subcat_choices, key="cat_subcat")
        # Conditionnement options cascade from (Famille, Sous-catégorie)
        pack_mask = pd.Series(True, index=cat_df.index)
        if f_family != "Toutes":
            pack_mask &= cat_df["family_name"] == f_family
        if f_subcat != "—":
            pack_mask &= cat_df["subcategory"] == f_subcat
        packaging_choices = ["—"] + sorted(
            cat_df.loc[pack_mask, "packaging"].dropna().unique().tolist()
        )
        with f3:
            f_packaging = st.selectbox(
                "Conditionnement", options=packaging_choices, key="cat_packaging"
            )
        with f4:
            f_supplier = st.selectbox(
                "Fournisseur",
                options=suppliers_in_catalog,
                index=(
                    suppliers_in_catalog.index(st.session_state.get("supplier_filter_name", "Tous"))
                    if st.session_state.get("supplier_filter_name") in suppliers_in_catalog
                    else 0
                ),
                key="cat_supplier",
            )
        with f5:
            f_fresh = st.selectbox(
                "Fraîcheur",
                options=["Toutes", "🟢 Frais", "🟡 6–9 mois", "🔴 > 9 mois"],
                index=(
                    ["Toutes", "🟢 Frais", "🟡 6–9 mois", "🔴 > 9 mois"].index(
                        st.session_state.get("catalog_fresh_filter", "Toutes")
                    )
                    if st.session_state.get("catalog_fresh_filter") in
                    ["Toutes", "🟢 Frais", "🟡 6–9 mois", "🔴 > 9 mois"]
                    else 0
                ),
                key="cat_fresh",
            )
        with f6:
            f_search = st.text_input(
                "⌕ recherche",
                placeholder="lavande, teralt, bigbag…",
                key="cat_search",
            )

        filtered = cat_df.copy()
        if f_family != "Toutes":
            filtered = filtered[filtered["family_name"] == f_family]
        if f_subcat != "—":
            filtered = filtered[filtered["subcategory"] == f_subcat]
        if f_supplier != "Tous":
            filtered = filtered[filtered["supplier_name"] == f_supplier]
        if f_packaging != "—":
            filtered = filtered[filtered["packaging"] == f_packaging]
        if f_fresh != "Toutes":
            fresh_map = {"🟢 Frais": "fresh", "🟡 6–9 mois": "stale_6mo", "🔴 > 9 mois": "stale_9mo"}
            filtered = filtered[filtered["freshness_status"] == fresh_map[f_fresh]]
        if f_search.strip():
            q = f_search.strip().lower()
            mask = (
                filtered["reference_name"].fillna("").str.lower().str.contains(q, na=False)
                | filtered["family_name"].fillna("").str.lower().str.contains(q, na=False)
                | filtered["subcategory"].fillna("").str.lower().str.contains(q, na=False)
                | filtered["brand"].fillna("").str.lower().str.contains(q, na=False)
                | filtered["packaging"].fillna("").str.lower().str.contains(q, na=False)
            )
            filtered = filtered[mask]

        with f7:
            st.markdown(
                f'<div style="text-align:right;padding-top:24px;font-size:11.5px;color:var(--hf-muted);font-variant-numeric:tabular-nums">'
                f'{len(filtered):,} résultats</div>'.replace(",", " "),
                unsafe_allow_html=True,
            )

        if filtered.empty:
            st.info("Aucun produit ne correspond aux filtres.")
        else:
            view = filtered[[
                "id", "reference_name", "family_name", "subcategory", "packaging",
                "labor_task", "supplier_name", "cost_ht", "freshness_status",
                "last_price_update",
            ]].copy()
            view["Coût HT"] = view["cost_ht"].apply(lambda x: f"{x:,.2f} €".replace(",", " "))
            view["Fraîs."] = view["freshness_status"].map(_freshness_dot)
            view["M. à j."] = pd.to_datetime(view["last_price_update"]).dt.strftime("%d/%m/%Y")
            # Chaîne pour la DPGF (col AG) — surfaced as the first data
            # column so Vincent can preview it at a glance from the table.
            view["Chaîne DPGF (col. AG)"] = (
                view["family_name"].astype(str) + " — "
                + view["subcategory"].astype(str) + " — "
                + view["reference_name"].astype(str) + " — "
                + view["packaging"].astype(str)
            )
            view = view[[
                "Chaîne DPGF (col. AG)",
                "reference_name", "family_name", "subcategory", "packaging",
                "labor_task",
                "supplier_name", "Coût HT", "Fraîs.", "M. à j.",
            ]]
            view.columns = [
                "Chaîne DPGF (col. AG)",
                "Produit", "Famille", "Sous-catégorie", "Conditionnement",
                "Norme de pose",
                "Fournisseur", "Coût HT", "Fraîs.", "M. à j.",
            ]

            # Hint above the dataframe — Streamlit's selection column has no
            # header label so users don't always realise that's the click
            # target. The arrow + green wording call it out clearly.
            st.markdown(
                '<div class="hf-muted" style="font-size:12px;margin:6px 0 4px">'
                '↓ <span style="color:var(--hf-green);font-weight:600">'
                'Cliquez sur la pastille à gauche d\'une ligne</span> '
                'pour ouvrir le produit en édition (le formulaire complet '
                's\'affichera sous le tableau).'
                '</div>',
                unsafe_allow_html=True,
            )

            event = st.dataframe(
                view,
                hide_index=True,
                use_container_width=True,
                on_select="rerun",
                selection_mode="single-row",
                key="catalog_grid",
                column_config={
                    "Chaîne DPGF (col. AG)": st.column_config.TextColumn(
                        width="large",
                        help=(
                            "Chaîne à coller dans la colonne AG du tableur "
                            "DPGF — c'est ce que les listes déroulantes "
                            "en cascade utilisent pour résoudre le produit."
                        ),
                    ),
                    "Produit": st.column_config.TextColumn(width="medium"),
                    "Famille": st.column_config.TextColumn(width="small"),
                    "Sous-catégorie": st.column_config.TextColumn(width="small"),
                    "Conditionnement": st.column_config.TextColumn(width="small"),
                    "Norme de pose": st.column_config.TextColumn(
                        width="medium",
                        help="Tâche de pose liée au produit (table labor_norms). "
                             "Détermine la formule de temps-homme appliquée au DPGF.",
                    ),
                    "Fournisseur": st.column_config.TextColumn(width="medium"),
                    "Coût HT": st.column_config.TextColumn(width="small"),
                    "Fraîs.": st.column_config.TextColumn(width=64),
                    "M. à j.": st.column_config.TextColumn(width=96),
                },
                height=420,
            )

            sel_rows = (
                event.selection.rows if hasattr(event, "selection") and event.selection else []
            )

            # Surface the selected product's PID up to session_state so the
            # page header can morph "+ Nouveau produit" → "✎ Éditer ce
            # produit". One explicit rerun on change keeps the header in
            # sync without an infinite loop (we only rerun when the value
            # actually changes).
            if sel_rows:
                _new_pid = int(filtered.iloc[sel_rows[0]]["id"])
            else:
                _new_pid = None
            if st.session_state.get("cat_selected_pid") != _new_pid:
                st.session_state["cat_selected_pid"] = _new_pid
                st.rerun()

            if sel_rows:
                # User picked a row → render the full edit form (staged
                # sections + right-side context panel) inline below the
                # table. The function handles everything: prefill from DB,
                # history sparkline, "Aussi disponible chez", DPGF chain,
                # and the save flow.
                _selected_pid = int(filtered.iloc[sel_rows[0]]["id"])
                st.markdown(
                    '<div style="height:18px"></div>'
                    '<div style="border-top:1px solid var(--hf-border);'
                    'padding-top:14px"></div>',
                    unsafe_allow_html=True,
                )
                _render_product_form(_selected_pid)


# ============================================================================
#  Édition tab — 2-column staged form (informed editing)
# ============================================================================
with tab_edition:
    if _mode == "Nouveau produit":
        _render_product_form(None)


render_footer()
