"""Fournisseurs — hi-fi card grid + dialog edit/add.

Each supplier appears as a card showing name, star rating, category/city,
product count, last-activity freshness. "→ Voir produits" pre-fills the
Catalogue filter and switches to the Produits page.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy.exc import IntegrityError

from lib.auth import require_login
from lib.branding import (
    apply_branding,
    hf_chip,
    render_footer,
    render_header,
    render_sidebar_brand,
)
from lib.db import execute, fetch_all, fetch_df, fetch_one

st.set_page_config(page_title="Fournisseurs — Merci Raymond", page_icon="🌳", layout="wide")
require_login()
apply_branding()
render_sidebar_brand()


def _safe_int(v, default: int = 0) -> int:
    """Coerce a possibly-NaN / None / numeric.NaN value to int safely."""
    try:
        import math
        if v is None:
            return default
        if isinstance(v, float) and math.isnan(v):
            return default
        return int(v)
    except (TypeError, ValueError):
        return default


def _stars(n) -> str:
    n = max(0, min(5, _safe_int(n)))
    return (
        '<span style="color:var(--hf-amber);font-size:12px">' + ("★" * n) + "</span>"
        + '<span style="color:var(--hf-soft);font-size:12px">' + ("★" * (5 - n)) + "</span>"
    )


def _freshness_chip(days_since_update) -> str:
    if days_since_update is None:
        return hf_chip("aucun produit", "ghost")
    try:
        import math
        if isinstance(days_since_update, float) and math.isnan(days_since_update):
            return hf_chip("aucun produit", "ghost")
        d = int(days_since_update)
    except (TypeError, ValueError):
        return hf_chip("aucun produit", "ghost")
    if d <= 30:
        return hf_chip(f"🟢 {d} j", "ok")
    if d <= 180:
        return hf_chip(f"🟡 {d} j", "warn")
    return hf_chip(f"🔴 {d} j", "danger")


# ============================================================================
#  Aggregates + header
# ============================================================================
n_suppliers = (fetch_one("SELECT count(*) AS c FROM suppliers") or {"c": 0})["c"]

hdr_l, hdr_r = st.columns([3, 2])
with hdr_l:
    render_header(title="Fournisseurs", subtitle=f"{n_suppliers} partenaires")
with hdr_r:
    b1, b2 = st.columns([1, 1])
    with b1:
        if st.button("⌕ recherche", key="sup_hdr_search", use_container_width=True):
            st.session_state["sup_focus_search"] = True
    with b2:
        if st.button("+ Nouveau fournisseur", key="sup_hdr_add", type="primary", use_container_width=True):
            st.session_state["supplier_add_open"] = True


# ============================================================================
#  Read all suppliers + computed metadata
# ============================================================================
suppliers = fetch_df(
    """
    SELECT s.id, s.name, s.category, s.contact_name, s.contact_email,
           s.contact_phone, s.address, s.payment_terms, s.rating, s.notes,
           (SELECT count(*) FROM products p WHERE p.supplier_id = s.id) AS product_count,
           (SELECT EXTRACT(DAY FROM AGE(now(), MAX(p.last_price_update)))::int
              FROM products p WHERE p.supplier_id = s.id) AS days_since_update
      FROM suppliers s
     ORDER BY s.name
    """
)


# ============================================================================
#  Filter row
# ============================================================================
all_categories = ["Toutes"] + sorted(
    suppliers["category"].dropna().unique().tolist()
)
f1, f2, f3, f4 = st.columns([1.6, 1.2, 2.0, 0.8])
with f1:
    f_category = st.selectbox("Catégorie", options=all_categories, key="sup_category")
with f2:
    f_min_rating = st.selectbox(
        "Note minimale",
        options=["—", "★★+", "★★★+", "★★★★+", "★★★★★"],
        key="sup_min_rating",
    )
with f3:
    f_search = st.text_input(
        "⌕ nom, catégorie…",
        value=st.session_state.get("supplier_search", ""),
        key="sup_search",
        placeholder="lavoisier, pépiniériste…",
    )
with f4:
    pass


# ============================================================================
#  Apply filters
# ============================================================================
filtered = suppliers.copy()
if f_category != "Toutes":
    filtered = filtered[filtered["category"] == f_category]
if f_min_rating != "—":
    min_n = {"★★+": 2, "★★★+": 3, "★★★★+": 4, "★★★★★": 5}[f_min_rating]
    filtered = filtered[filtered["rating"].fillna(0).astype(int) >= min_n]
if f_search.strip():
    q = f_search.strip().lower()
    mask = (
        filtered["name"].fillna("").str.lower().str.contains(q, na=False)
        | filtered["category"].fillna("").str.lower().str.contains(q, na=False)
        | filtered["address"].fillna("").str.lower().str.contains(q, na=False)
    )
    filtered = filtered[mask]

with f4:
    st.markdown(
        f'<div style="text-align:right;padding-top:24px;font-size:11.5px;color:var(--hf-muted);font-variant-numeric:tabular-nums">'
        f'{len(filtered)} résultats</div>',
        unsafe_allow_html=True,
    )


# ============================================================================
#  Card grid (3 per row)
# ============================================================================
if filtered.empty:
    st.info("Aucun fournisseur ne correspond aux filtres.")
else:
    rows = filtered.to_dict("records")
    for i in range(0, len(rows), 3):
        cols = st.columns(3, gap="small")
        for j, r in enumerate(rows[i : i + 3]):
            with cols[j]:
                with st.container(border=True):
                    stars = _stars(r.get("rating"))
                    fresh = _freshness_chip(r.get("days_since_update"))
                    cat = r.get("category") or "—"
                    addr_short = (r.get("address") or "").split(",")[-1].strip() or "—"
                    pc = _safe_int(r.get("product_count"))

                    st.markdown(
                        f"""
                        <div class="hf-row hf-between">
                          <div style="font-weight:600;font-size:14.5px;color:var(--hf-ink);line-height:1.15">{r['name']}</div>
                          <div>{stars}</div>
                        </div>
                        <div class="hf-muted" style="font-size:11.5px;margin-top:2px">{cat} · {addr_short}</div>
                        <hr style="border:none;border-top:1px solid var(--hf-border-soft);margin:10px 0">
                        <div class="hf-row hf-between" style="font-size:11.5px">
                          <span><span style="font-weight:600;color:var(--hf-ink)">{pc}</span> <span class="hf-muted">produits</span></span>
                          {fresh}
                        </div>
                        <div style="height:14px"></div>
                        """,
                        unsafe_allow_html=True,
                    )
                    btn_l, btn_r = st.columns(2)
                    with btn_l:
                        if st.button(
                            "→ produits",
                            key=f"sup_goto_prod_{r['id']}",
                            use_container_width=True,
                        ):
                            st.session_state["supplier_filter_name"] = r["name"]
                            st.switch_page("pages/3_Produits.py")
                    with btn_r:
                        if st.button(
                            "modifier",
                            key=f"sup_edit_{r['id']}",
                            use_container_width=True,
                        ):
                            st.session_state["supplier_edit_id"] = int(r["id"])
                            st.session_state["supplier_add_open"] = True
                            st.rerun()


# ============================================================================
#  Add/Edit dialog
# ============================================================================
edit_id = st.session_state.get("supplier_edit_id")
prefill: dict = {}
if edit_id:
    row = fetch_one("SELECT * FROM suppliers WHERE id = :id", {"id": edit_id})
    if row:
        prefill = row
    else:
        st.session_state.pop("supplier_edit_id", None)

if st.session_state.get("supplier_add_open"):
    @st.dialog("Fournisseur" + (f" #{edit_id}" if edit_id else " — nouveau"), width="large")
    def _supplier_dialog():
        with st.form("supplier_form", clear_on_submit=False):
            ca, cb = st.columns(2)
            with ca:
                name = st.text_input("Nom *", value=prefill.get("name", ""))
                contact_name = st.text_input("Contact", value=prefill.get("contact_name") or "")
                contact_email = st.text_input("Email", value=prefill.get("contact_email") or "")
                contact_phone = st.text_input("Téléphone", value=prefill.get("contact_phone") or "")
            with cb:
                category = st.text_input(
                    "Catégorie",
                    value=prefill.get("category") or "",
                    help="Ex. pépiniériste, matériaux minéraux, fournitures",
                )
                payment_terms = st.text_input(
                    "Conditions de paiement",
                    value=prefill.get("payment_terms") or "",
                    help="Ex. 30j fin de mois",
                )
                rating = st.slider(
                    "Note (0–5)", 0, 5, value=int(prefill.get("rating") or 0)
                )
            address = st.text_area("Adresse", value=prefill.get("address") or "", height=70)
            notes = st.text_area("Notes", value=prefill.get("notes") or "", height=70)

            bs, bc, bd = st.columns([1, 1, 1])
            with bs:
                submitted = st.form_submit_button(
                    "Mettre à jour" if edit_id else "Ajouter",
                    type="primary",
                    use_container_width=True,
                )
            with bc:
                cancel = st.form_submit_button("Annuler", use_container_width=True)
            with bd:
                if edit_id:
                    delete = st.form_submit_button("Supprimer", use_container_width=True)
                else:
                    delete = False

            if cancel:
                st.session_state["supplier_add_open"] = False
                st.session_state.pop("supplier_edit_id", None)
                st.rerun()

            if delete and edit_id:
                pc = int(prefill.get("product_count") or 0) if "product_count" in prefill else (
                    fetch_one("SELECT count(*) AS c FROM products WHERE supplier_id=:id", {"id": edit_id})["c"]
                )
                if pc > 0:
                    st.error(
                        f"Impossible de supprimer « {prefill.get('name', '?')} » : "
                        f"{pc} produit(s) y sont rattachés."
                    )
                else:
                    try:
                        execute("DELETE FROM suppliers WHERE id = :id", {"id": edit_id})
                        st.toast(f"« {prefill.get('name')} » supprimé", icon="🗑")
                        st.session_state.pop("supplier_edit_id", None)
                        st.session_state["supplier_add_open"] = False
                        st.rerun()
                    except IntegrityError as exc:
                        st.error(f"Suppression bloquée : {exc.orig}")

            if submitted:
                if not name.strip():
                    st.error("Le nom est obligatoire.")
                else:
                    params = {
                        "name": name.strip(),
                        "contact_name": contact_name.strip() or None,
                        "contact_email": contact_email.strip() or None,
                        "contact_phone": contact_phone.strip() or None,
                        "address": address.strip() or None,
                        "category": category.strip() or None,
                        "payment_terms": payment_terms.strip() or None,
                        "rating": rating if rating > 0 else None,
                        "notes": notes.strip() or None,
                    }
                    try:
                        if edit_id:
                            params["id"] = edit_id
                            execute(
                                """
                                UPDATE suppliers SET
                                    name = :name,
                                    contact_name = :contact_name,
                                    contact_email = :contact_email,
                                    contact_phone = :contact_phone,
                                    address = :address,
                                    category = :category,
                                    payment_terms = :payment_terms,
                                    rating = :rating,
                                    notes = :notes
                                WHERE id = :id
                                """,
                                params,
                            )
                            st.toast(f"✓ « {name} » mis à jour", icon="🌿")
                            st.session_state.pop("supplier_edit_id", None)
                        else:
                            execute(
                                """
                                INSERT INTO suppliers
                                    (name, contact_name, contact_email, contact_phone,
                                     address, category, payment_terms, rating, notes)
                                VALUES
                                    (:name, :contact_name, :contact_email, :contact_phone,
                                     :address, :category, :payment_terms, :rating, :notes)
                                """,
                                params,
                            )
                            st.toast(f"✓ « {name} » ajouté", icon="🌿")
                        st.session_state["supplier_add_open"] = False
                        st.rerun()
                    except IntegrityError as exc:
                        if "suppliers_name_key" in str(exc):
                            st.error(f"Un fournisseur nommé « {name} » existe déjà.")
                        else:
                            st.error(f"Erreur d'intégrité : {exc.orig}")

    _supplier_dialog()


render_footer()
