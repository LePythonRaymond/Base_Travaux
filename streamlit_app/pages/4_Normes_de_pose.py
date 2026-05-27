"""Normes de pose — hi-fi card grid (2 per row) + dialog edit/add.

Each norm card shows task name, unit chip, heure_u_pose + nombre_uth as
mono metrics, and a three-tier "Décharge selon accès" chip row.
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

UNIT_TYPES = ["u", "m3", "ml", "m2", "Ft", "kg", "l"]

st.set_page_config(page_title="Normes — Merci Raymond", page_icon="🌳", layout="wide")
require_login()
apply_branding()
render_sidebar_brand()


# ============================================================================
#  Header
# ============================================================================
n_norms = (fetch_one("SELECT count(*) AS c FROM labor_norms") or {"c": 0})["c"]

hdr_l, hdr_r = st.columns([3, 1])
with hdr_l:
    render_header(title="Normes de pose", subtitle=f"{n_norms} normes")
with hdr_r:
    if st.button("+ norme", key="ln_hdr_add", type="primary", use_container_width=True):
        st.session_state["labor_add_open"] = True

st.markdown(
    '<p class="hf-muted" style="margin:0 0 8px 0;font-size:13px;max-width:680px">'
    "Tarifs horaires associés à chaque tâche. Trois niveaux selon l'accès chantier — "
    "sol, étage, toit-terrasse."
    "</p>",
    unsafe_allow_html=True,
)


# ============================================================================
#  Load norms + render in 2-column card grid
# ============================================================================
norms = fetch_all(
    """
    SELECT ln.id, ln.task_name, ln.unit_type, ln.nombre_uth_default,
           ln.heure_u_pose_default,
           ln.tier_1_label, ln.tier_1_heure_u_decharge,
           ln.tier_2_label, ln.tier_2_heure_u_decharge,
           ln.tier_3_label, ln.tier_3_heure_u_decharge,
           (SELECT count(*) FROM products p WHERE p.labor_norm_id = ln.id) AS product_count,
           ln.notes
      FROM labor_norms ln
     ORDER BY ln.task_name
    """
)

if not norms:
    st.info("Aucune norme définie.")
else:
    for i in range(0, len(norms), 2):
        cols = st.columns(2, gap="small")
        for j, r in enumerate(norms[i : i + 2]):
            with cols[j]:
                with st.container(border=True):
                    h_pose = f"{float(r['heure_u_pose_default']):.3f} h"
                    n_uth = f"{float(r['nombre_uth_default']):.2f} UTH"
                    t1 = f"{float(r['tier_1_heure_u_decharge']):.3f} h"
                    t2 = f"{float(r['tier_2_heure_u_decharge']):.3f} h"
                    t3 = f"{float(r['tier_3_heure_u_decharge']):.3f} h"
                    pc = int(r["product_count"] or 0)
                    pc_chip = hf_chip(f"{pc} produits", "ghost") if pc else hf_chip("inutilisée", "ghost")

                    st.markdown(
                        f"""
                        <div class="hf-row hf-between">
                          <div style="font-weight:600;font-size:14px;color:var(--hf-ink)">{r['task_name']}</div>
                          {hf_chip(r['unit_type'], 'ghost')}
                        </div>

                        <div class="hf-row" style="gap:18px;margin-top:8px">
                          <div>
                            <div style="font-size:10.5px;color:var(--hf-muted);text-transform:uppercase;letter-spacing:0.04em;font-weight:600">heure_u_pose</div>
                            <div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:13px;color:var(--hf-ink)">{h_pose}</div>
                          </div>
                          <div>
                            <div style="font-size:10.5px;color:var(--hf-muted);text-transform:uppercase;letter-spacing:0.04em;font-weight:600">nombre_uth</div>
                            <div style="font-family:JetBrains Mono,monospace;font-weight:600;font-size:13px;color:var(--hf-ink)">{n_uth}</div>
                          </div>
                          <div style="margin-left:auto">{pc_chip}</div>
                        </div>

                        <hr style="border:none;border-top:1px solid var(--hf-border-soft);margin:10px 0 8px">
                        <div style="font-size:10.5px;color:var(--hf-muted);text-transform:uppercase;letter-spacing:0.04em;font-weight:600;margin-bottom:6px">Décharge selon accès</div>
                        <div class="hf-row" style="gap:6px;flex-wrap:wrap">
                          {hf_chip(f'🌱 {r["tier_1_label"]} &nbsp;{t1}', 'ok')}
                          {hf_chip(f'🪟 {r["tier_2_label"]} &nbsp;{t2}', 'warn')}
                          {hf_chip(f'🛗 {r["tier_3_label"]} &nbsp;{t3}', 'danger')}
                        </div>
                        <div style="height:14px"></div>
                        """,
                        unsafe_allow_html=True,
                    )
                    if st.button("✏ modifier", key=f"ln_edit_{r['id']}", use_container_width=True):
                        st.session_state["labor_edit_id"] = int(r["id"])
                        st.session_state["labor_add_open"] = True
                        st.rerun()


# ============================================================================
#  Add/Edit dialog
# ============================================================================
edit_id = st.session_state.get("labor_edit_id")
prefill: dict = {}
if edit_id:
    row = fetch_one("SELECT * FROM labor_norms WHERE id = :id", {"id": edit_id})
    if row:
        prefill = row
    else:
        st.session_state.pop("labor_edit_id", None)

if st.session_state.get("labor_add_open"):
    @st.dialog(
        "Norme de pose" + (f" #{edit_id}" if edit_id else " — nouvelle"),
        width="large",
    )
    def _labor_dialog():
        with st.form("labor_form", clear_on_submit=False):
            c1, c2 = st.columns(2)
            with c1:
                task_name = st.text_input("Nom de la tâche *", value=prefill.get("task_name", ""))
                unit_type = st.selectbox(
                    "Unité *",
                    options=UNIT_TYPES,
                    index=UNIT_TYPES.index(prefill.get("unit_type"))
                    if prefill.get("unit_type") in UNIT_TYPES
                    else 0,
                )
                nombre_uth_default = st.number_input(
                    "UTH par défaut",
                    min_value=0.0, max_value=99.0,
                    value=float(prefill.get("nombre_uth_default") or 1),
                    step=0.25,
                )
                heure_u_pose_default = st.number_input(
                    "Heure pose / unité *",
                    min_value=0.0,
                    value=float(prefill.get("heure_u_pose_default") or 0),
                    step=0.001, format="%.3f",
                )
            with c2:
                tier_1_label = st.text_input(
                    "Tier 1 — label", value=prefill.get("tier_1_label") or "facile"
                )
                # Each tier's heure number_input has an explicit key so the
                # "Auto-remplir" submit button below can rewrite them in
                # one go (via session_state + a `_pending_tX` baton that
                # the widget consumes on the next render).
                tier_1_h = st.number_input(
                    "Tier 1 — heure",
                    min_value=0.0,
                    value=st.session_state.pop(
                        "_pending_t1",
                        float(prefill.get("tier_1_heure_u_decharge") or 0),
                    ),
                    step=0.001, format="%.3f",
                    key="lndlg_tier1_h",
                )
                tier_2_label = st.text_input(
                    "Tier 2 — label", value=prefill.get("tier_2_label") or "moyen"
                )
                tier_2_h = st.number_input(
                    "Tier 2 — heure",
                    min_value=0.0,
                    value=st.session_state.pop(
                        "_pending_t2",
                        float(prefill.get("tier_2_heure_u_decharge") or 0),
                    ),
                    step=0.001, format="%.3f",
                    key="lndlg_tier2_h",
                )
                tier_3_label = st.text_input(
                    "Tier 3 — label", value=prefill.get("tier_3_label") or "difficile"
                )
                tier_3_h = st.number_input(
                    "Tier 3 — heure",
                    min_value=0.0,
                    value=st.session_state.pop(
                        "_pending_t3",
                        float(prefill.get("tier_3_heure_u_decharge") or 0),
                    ),
                    step=0.001, format="%.3f",
                    key="lndlg_tier3_h",
                )
                # Auto-fill the 3 tier values from the entered pose time
                # using fixed ×1 / ×2 / ×3 multipliers. The user can still
                # tweak each value manually afterwards.
                auto_fill = st.form_submit_button(
                    "↻ Auto-remplir décharges (×1 / ×2 / ×3)",
                    use_container_width=True,
                )
                if auto_fill:
                    if heure_u_pose_default and heure_u_pose_default > 0:
                        st.session_state["_pending_t1"] = float(heure_u_pose_default) * 1.0
                        st.session_state["_pending_t2"] = float(heure_u_pose_default) * 2.0
                        st.session_state["_pending_t3"] = float(heure_u_pose_default) * 3.0
                        # Drop the widget keys so the new `value=` is read
                        # on next render rather than the just-submitted one.
                        for _k in ("lndlg_tier1_h", "lndlg_tier2_h", "lndlg_tier3_h"):
                            st.session_state.pop(_k, None)
                        st.rerun()
                    else:
                        st.warning(
                            "Renseigne d'abord la Heure pose / unité, puis "
                            "clique sur ↻ Auto-remplir."
                        )
            notes = st.text_area("Notes", value=prefill.get("notes") or "", height=60)

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
                delete = (
                    st.form_submit_button("Supprimer", use_container_width=True)
                    if edit_id else False
                )

            if cancel:
                st.session_state["labor_add_open"] = False
                st.session_state.pop("labor_edit_id", None)
                st.rerun()

            if delete and edit_id:
                pc = fetch_one(
                    "SELECT count(*) AS c FROM products WHERE labor_norm_id=:id", {"id": edit_id}
                )["c"]
                if pc > 0:
                    st.error(f"Suppression bloquée : {pc} produit(s) référencent cette norme.")
                else:
                    try:
                        execute("DELETE FROM labor_norms WHERE id = :id", {"id": edit_id})
                        st.toast("Norme supprimée", icon="🗑")
                        st.session_state.pop("labor_edit_id", None)
                        st.session_state["labor_add_open"] = False
                        st.rerun()
                    except IntegrityError as exc:
                        st.error(f"Erreur : {exc.orig}")

            if submitted:
                if not task_name.strip():
                    st.error("Le nom de la tâche est obligatoire.")
                else:
                    params = {
                        "task_name": task_name.strip(),
                        "unit_type": unit_type,
                        "nombre_uth_default": nombre_uth_default,
                        "heure_u_pose_default": heure_u_pose_default,
                        "tier_1_label": tier_1_label.strip() or "facile",
                        "tier_1_heure_u_decharge": tier_1_h,
                        "tier_2_label": tier_2_label.strip() or "moyen",
                        "tier_2_heure_u_decharge": tier_2_h,
                        "tier_3_label": tier_3_label.strip() or "difficile",
                        "tier_3_heure_u_decharge": tier_3_h,
                        "notes": notes.strip() or None,
                    }
                    try:
                        if edit_id:
                            params["id"] = edit_id
                            execute(
                                """
                                UPDATE labor_norms SET
                                    task_name=:task_name,
                                    unit_type=:unit_type,
                                    nombre_uth_default=:nombre_uth_default,
                                    heure_u_pose_default=:heure_u_pose_default,
                                    tier_1_label=:tier_1_label,
                                    tier_1_heure_u_decharge=:tier_1_heure_u_decharge,
                                    tier_2_label=:tier_2_label,
                                    tier_2_heure_u_decharge=:tier_2_heure_u_decharge,
                                    tier_3_label=:tier_3_label,
                                    tier_3_heure_u_decharge=:tier_3_heure_u_decharge,
                                    notes=:notes
                                WHERE id=:id
                                """,
                                params,
                            )
                            st.toast("✓ Norme mise à jour", icon="🌿")
                            st.session_state.pop("labor_edit_id", None)
                        else:
                            execute(
                                """
                                INSERT INTO labor_norms (
                                    task_name, unit_type, nombre_uth_default, heure_u_pose_default,
                                    tier_1_label, tier_1_heure_u_decharge,
                                    tier_2_label, tier_2_heure_u_decharge,
                                    tier_3_label, tier_3_heure_u_decharge,
                                    notes
                                ) VALUES (
                                    :task_name, :unit_type, :nombre_uth_default, :heure_u_pose_default,
                                    :tier_1_label, :tier_1_heure_u_decharge,
                                    :tier_2_label, :tier_2_heure_u_decharge,
                                    :tier_3_label, :tier_3_heure_u_decharge,
                                    :notes
                                )
                                """,
                                params,
                            )
                            st.toast("✓ Norme ajoutée", icon="🌿")
                        st.session_state["labor_add_open"] = False
                        st.rerun()
                    except IntegrityError as exc:
                        if "labor_norms_task_name_key" in str(exc):
                            st.error(f"Une norme nommée « {task_name} » existe déjà.")
                        else:
                            st.error(f"Erreur d'intégrité : {exc.orig}")

    _labor_dialog()


render_footer()
