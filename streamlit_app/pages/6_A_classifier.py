"""À classifier — triage queue with three tabs:

  • Produits à reclasser — hi-fi card-per-row layout. Oldest items first.
    Each card has a triplet picker + "✓ classer". Batch mode lets Vincent
    apply the same triplet to N selected rows in one transaction.
  • Ingestion en attente — queue rows in 'needs_info' that need a human
    to complete the triplet + cost before they enter `products`.
  • Référentiel taxonomie — read-only view of `product_taxonomy` + add form.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone

import streamlit as st
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from lib.auth import require_login
from lib.branding import (
    apply_branding,
    hf_chip,
    hf_dot,
    render_footer,
    render_header,
    render_sidebar_brand,
)
from lib.db import execute, fetch_all, fetch_df, fetch_one, transaction
from lib.pickers import (
    FAMILY_NEW_ID,
    LABOR_NEW_ID,
    quick_create_labor_norm,
    render_labor_norm_picker,
    resolve_family,
)

UNIT_TYPES = ["u", "m3", "ml", "m2", "Ft", "kg", "l"]
NEW_VALUE_SENTINEL = "+ Créer nouveau…"

st.set_page_config(page_title="À classifier — Merci Raymond", page_icon="🌳", layout="wide")
require_login()
apply_branding()
render_sidebar_brand()

# ============================================================================
#  Shared metadata + cascade helpers
# ============================================================================
families = fetch_all("SELECT id, name FROM product_families ORDER BY name")
suppliers = fetch_all("SELECT id, name FROM suppliers ORDER BY name")
labor_norms = fetch_all("SELECT id, task_name FROM labor_norms ORDER BY task_name")

family_by_id = {f["id"]: f["name"] for f in families}
family_by_name_lower = {f["name"].lower(): f["id"] for f in families}


def _load_taxonomy_lookups() -> tuple[dict[int, list[str]], dict[tuple[int, str], list[str]]]:
    rows = fetch_all(
        """
        SELECT family_id, subcategory, packaging
          FROM product_taxonomy
         ORDER BY family_id, subcategory, packaging
        """
    )
    subs: dict[int, list[str]] = {}
    packs: dict[tuple[int, str], list[str]] = {}
    for r in rows:
        subs.setdefault(r["family_id"], [])
        if r["subcategory"] not in subs[r["family_id"]]:
            subs[r["family_id"]].append(r["subcategory"])
        key = (r["family_id"], r["subcategory"])
        packs.setdefault(key, [])
        if r["packaging"] not in packs[key]:
            packs[key].append(r["packaging"])
    return subs, packs


def _ensure_taxonomy_row(conn, family_id: int, subcategory: str, packaging: str) -> None:
    actor = os.environ.get("STREAMLIT_AUTH_USER", "system")
    conn.execute(
        text(
            """
            INSERT INTO product_taxonomy (family_id, subcategory, packaging, created_by, notes)
            VALUES (:fid, :sub, :pkg, :by, 'Ajouté depuis À classifier')
            ON CONFLICT (family_id, subcategory, packaging) DO NOTHING
            """
        ),
        {"fid": family_id, "sub": subcategory, "pkg": packaging, "by": actor},
    )


def render_triplet_picker(
    *,
    key_prefix: str,
    initial_family_id: int | None,
    initial_subcategory: str | None,
    initial_packaging: str | None,
    subs_lookup: dict[int, list[str]],
    packs_lookup: dict[tuple[int, str], list[str]],
    compact: bool = False,
) -> tuple[int, str, str, str]:
    """Render three cascading selectboxes (family / sub / packaging) with
    "Créer nouveau…" sentinels on ALL THREE levels (famille included).
    Returns (family_id, new_family_name, subcategory, packaging) where
    family_id may be FAMILY_NEW_ID — the caller resolves it with
    `lib.pickers.resolve_family` at commit time.
    """
    if compact:
        # Three-column row, no labels above each (caller is expected to be in
        # a horizontal context where vertical space is tight).
        c1, c2, c3 = st.columns(3)
    else:
        c1, c2, c3 = (st.container(), st.container(), st.container())

    with c1:
        family_id_options = [f["id"] for f in families] + [FAMILY_NEW_ID]
        family_default_idx = (
            family_id_options.index(initial_family_id)
            if initial_family_id in family_id_options
            else 0
        )
        chosen_family_id = st.selectbox(
            "Famille",
            options=family_id_options,
            index=family_default_idx,
            format_func=lambda i: (
                NEW_VALUE_SENTINEL if i == FAMILY_NEW_ID else family_by_id.get(i, str(i))
            ),
            key=f"{key_prefix}_family",
        )
        new_family_name = ""
        if chosen_family_id == FAMILY_NEW_ID:
            new_family_name = st.text_input(
                "Nouvelle famille",
                value="",
                key=f"{key_prefix}_family_new",
                placeholder="ex. Mobilier outdoor…",
            ).strip()

    with c2:
        existing_subs = subs_lookup.get(chosen_family_id, [])
        sub_options = existing_subs + [NEW_VALUE_SENTINEL]
        sub_default_idx = (
            existing_subs.index(initial_subcategory)
            if initial_subcategory in existing_subs
            else 0
        )
        chosen_sub = st.selectbox(
            "Sous-catégorie",
            options=sub_options,
            index=sub_default_idx,
            key=f"{key_prefix}_sub",
        )
        if chosen_sub == NEW_VALUE_SENTINEL:
            chosen_sub = st.text_input(
                "Nouvelle sous-catégorie",
                value="",
                key=f"{key_prefix}_sub_new",
                placeholder="Conifère, Topiaire…",
            ).strip()

    with c3:
        existing_packs = packs_lookup.get((chosen_family_id, chosen_sub), [])
        pack_options = existing_packs + [NEW_VALUE_SENTINEL]
        pack_default_idx = (
            existing_packs.index(initial_packaging)
            if initial_packaging in existing_packs
            else 0
        )
        chosen_pack = st.selectbox(
            "Conditionnement",
            options=pack_options,
            index=pack_default_idx,
            key=f"{key_prefix}_pack",
        )
        if chosen_pack == NEW_VALUE_SENTINEL:
            chosen_pack = st.text_input(
                "Nouveau conditionnement",
                value="",
                key=f"{key_prefix}_pack_new",
                placeholder="Conteneur 7L, Sac 25kg…",
            ).strip()

    return chosen_family_id, new_family_name, chosen_sub or "", chosen_pack or ""


# ============================================================================
#  Counts + header
# ============================================================================
n_to_reclass = fetch_one(
    "SELECT count(*) AS n FROM products WHERE subcategory = 'À classifier'"
)["n"]
# Both `pending` (extracted, waiting for review) and `needs_info` (under
# review, missing data) are unprocessed and belong here — the only place
# in the UI where Vincent can finish them. We intentionally don't split
# them: the user just sees "things to finish".
n_needs_info = fetch_one(
    "SELECT count(*) AS n FROM ingestion_queue "
    "WHERE status IN ('pending', 'needs_info')"
)["n"]

hdr_l, hdr_r = st.columns([3, 2])
with hdr_l:
    render_header(
        title="À classifier",
        subtitle=f"file d'attente · {n_to_reclass + n_needs_info} éléments",
    )
# (Header buttons for the Référentiel taxonomie tab were removed — that
# tab no longer exists; the triplet dictionary is managed implicitly via
# the "+ créer nouveau…" cascade inside the product / ingestion forms.)

# Persisted initial-count for the session, so the progress bar makes sense.
if "classify_session_start" not in st.session_state:
    st.session_state["classify_session_start"] = n_to_reclass + n_needs_info
sess_start = int(st.session_state["classify_session_start"])
sess_done = max(0, sess_start - (n_to_reclass + n_needs_info))
sess_frac = (sess_done / sess_start) if sess_start else 1.0

st.markdown(
    f"""
    <div class="hf-card" style="padding:12px 16px;margin-bottom:10px">
      <div class="hf-row hf-between" style="margin-bottom:8px">
        <div style="font-weight:600;font-size:13px;color:var(--hf-ink)">Progression de la semaine</div>
        <span class="hf-mono" style="color:var(--hf-muted);font-size:11px">{sess_done} / {sess_start} traités</span>
      </div>
      <div class="hf-progress"><span style="width:{int(sess_frac*100)}%"></span></div>
      <div class="hf-row" style="gap:6px;margin-top:12px">
        {hf_chip(f'{n_to_reclass} produits', 'warn')}
        {hf_chip(f'{n_needs_info} ingestions en attente', 'danger')}
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)


# Mode switcher (two tabs only — the Référentiel taxonomie tab was
# removed because the triplet dictionary is informative reference data,
# not work-to-do. New triplets are now added implicitly via the
# "+ créer nouveau…" option in the product / ingestion forms).
_mode_options = [
    f"Produits à reclasser ({n_to_reclass})",
    f"Ingestion en attente ({n_needs_info})",
]
# Honour any tab-switch request that may still arrive from older
# session_state. If the legacy "Référentiel taxonomie" value was stored,
# silently drop it.
if "force_taxo_mode" in st.session_state:
    prefix = st.session_state.pop("force_taxo_mode")
    for opt in _mode_options:
        if opt.startswith(prefix):
            st.session_state["taxo_mode"] = opt
            break
# Same guard if the previous session left taxo_mode pointing at the
# defunct option.
if (
    "taxo_mode" in st.session_state
    and st.session_state["taxo_mode"] not in _mode_options
):
    st.session_state["taxo_mode"] = _mode_options[0]

_taxo_mode = st.radio(
    "Mode",
    options=_mode_options,
    horizontal=True,
    label_visibility="collapsed",
    key="taxo_mode",
)
_mode_key = (
    "Produits à reclasser" if _taxo_mode.startswith("Produits")
    else "Ingestion en attente"
)
tab_reclass = st.container()
tab_needs_info = st.container()


# ============================================================================
#  Tab 1 — Produits à reclasser  (hi-fi card-per-row)
# ============================================================================
with tab_reclass:
  if _mode_key == "Produits à reclasser":
    if n_to_reclass == 0:
        st.markdown(
            '<div class="hf-card ok" style="text-align:center;padding:24px">'
            '<div style="font-weight:600;font-size:14px;color:var(--hf-ink)">'
            'Aucun produit en attente de reclassification 🌿</div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            '<h2 class="hf-h2" style="margin:8px 0 6px 0">Les plus anciens d\'abord</h2>',
            unsafe_allow_html=True,
        )

        # Load fresh taxonomy + rows
        subs_lookup, packs_lookup = _load_taxonomy_lookups()
        rows = fetch_all(
            """
            SELECT p.id, p.reference_name, p.family_id, pf.name AS family_name,
                   p.subcategory, p.packaging, p.unit_type, p.cost_ht,
                   s.name AS supplier_name, p.created_at
              FROM products p
              JOIN product_families pf ON pf.id = p.family_id
              JOIN suppliers s         ON s.id = p.supplier_id
             WHERE p.subcategory = 'À classifier'
             ORDER BY p.created_at ASC
            """
        )

        # Batch-select state
        if "reclass_selected" not in st.session_state:
            st.session_state["reclass_selected"] = set()

        now_utc = datetime.now(timezone.utc)

        for r in rows:
            pid = r["id"]
            created = r["created_at"]
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            days_old = (now_utc - created).days if created else 0
            old = days_old > 30
            age_color = "var(--hf-terra)" if old else "var(--hf-ink)"
            day_str = created.strftime("%d/%m/%y") if created else "?"

            cont = st.container(border=True)
            with cont:
                meta_col, sel_col, btn_col = st.columns([4, 4, 1.2])

                with meta_col:
                    check_col, info_col = st.columns([0.18, 4])
                    with check_col:
                        is_sel = st.checkbox(
                            "✓",
                            key=f"sel_{pid}",
                            value=(pid in st.session_state["reclass_selected"]),
                            label_visibility="collapsed",
                        )
                        if is_sel:
                            st.session_state["reclass_selected"].add(pid)
                        else:
                            st.session_state["reclass_selected"].discard(pid)
                    with info_col:
                        cost_str = f"{float(r['cost_ht']):,.2f} €".replace(",", " ")
                        st.markdown(
                            f"""
                            <div class="hf-row" style="gap:12px;align-items:center">
                              <div style="width:60px">
                                <div style="font-weight:600;font-size:17px;color:{age_color};line-height:1">{days_old} j</div>
                                <div class="hf-mono" style="font-size:10px;color:var(--hf-muted)">{day_str}</div>
                              </div>
                              <div style="min-width:0;flex:1">
                                <div style="font-weight:600;font-size:13px;color:var(--hf-ink)">{r['reference_name']}</div>
                                <div class="hf-muted" style="font-size:11px">
                                  {r['family_name']} · à classifier · {r['packaging']} · {r['supplier_name']} · {cost_str}
                                </div>
                              </div>
                            </div>
                            """,
                            unsafe_allow_html=True,
                        )

                with sel_col:
                    chosen_family_id, chosen_new_fam, chosen_sub, chosen_pack = render_triplet_picker(
                        key_prefix=f"reclass_{pid}",
                        initial_family_id=r["family_id"],
                        initial_subcategory=None,
                        initial_packaging=r["packaging"],
                        subs_lookup=subs_lookup,
                        packs_lookup=packs_lookup,
                        compact=True,
                    )

                with btn_col:
                    ready = (
                        chosen_family_id is not None
                        and (chosen_family_id != FAMILY_NEW_ID or chosen_new_fam)
                        and chosen_sub
                        and chosen_sub != "À classifier"
                        and chosen_pack
                    )
                    st.markdown(
                        '<div style="padding-top:18px"></div>', unsafe_allow_html=True
                    )
                    if st.button(
                        "✓ classer",
                        key=f"reclass_btn_{pid}",
                        type="primary",
                        disabled=not ready,
                        use_container_width=True,
                    ):
                        try:
                            with transaction(ingestion_source="admin_streamlit") as conn:
                                _fid = resolve_family(conn, chosen_family_id, chosen_new_fam)
                                _ensure_taxonomy_row(
                                    conn, _fid, chosen_sub, chosen_pack
                                )
                                conn.execute(
                                    text(
                                        """
                                        UPDATE products SET
                                            family_id   = :fid,
                                            subcategory = :sub,
                                            packaging   = :pkg
                                        WHERE id = :id
                                        """
                                    ),
                                    {
                                        "fid": _fid,
                                        "sub": chosen_sub,
                                        "pkg": chosen_pack,
                                        "id": pid,
                                    },
                                )
                            st.session_state["reclass_selected"].discard(pid)
                            st.toast(
                                f"✓ {r['reference_name']} classé",
                                icon="🌿",
                            )
                            st.rerun()
                        except IntegrityError as exc:
                            st.error(f"Erreur d'intégrité : {exc.orig}")

        # ---- Batch action bar (sticky-ish at the bottom of the tab) ----
        n_sel = len(st.session_state["reclass_selected"])
        st.markdown(
            f'<div style="border-top:1px solid var(--hf-border-soft);padding-top:8px;margin-top:8px"></div>',
            unsafe_allow_html=True,
        )
        bar_l, bar_m, bar_r = st.columns([1.2, 3, 1.5])
        with bar_l:
            st.markdown(
                hf_chip(f"{n_sel} sélectionné(s)", "solid" if n_sel else "ghost"),
                unsafe_allow_html=True,
            )
        with bar_m:
            if n_sel > 0:
                with st.expander("≡ appliquer le même triplet à la sélection", expanded=False):
                    bf_id, bf_new_fam, bf_sub, bf_pack = render_triplet_picker(
                        key_prefix="batch_apply",
                        initial_family_id=None,
                        initial_subcategory=None,
                        initial_packaging=None,
                        subs_lookup=subs_lookup,
                        packs_lookup=packs_lookup,
                        compact=True,
                    )
                    batch_ready = (
                        bf_id is not None
                        and (bf_id != FAMILY_NEW_ID or bf_new_fam)
                        and bf_sub and bf_sub != "À classifier" and bf_pack
                    )
                    if st.button(
                        f"✓ classer {n_sel} produit(s) en une fois",
                        key="batch_apply_btn",
                        type="primary",
                        disabled=not batch_ready,
                    ):
                        try:
                            with transaction(ingestion_source="admin_streamlit") as conn:
                                _bfid = resolve_family(conn, bf_id, bf_new_fam)
                                _ensure_taxonomy_row(conn, _bfid, bf_sub, bf_pack)
                                for pid in list(st.session_state["reclass_selected"]):
                                    conn.execute(
                                        text(
                                            """
                                            UPDATE products SET
                                                family_id   = :fid,
                                                subcategory = :sub,
                                                packaging   = :pkg
                                              WHERE id = :id
                                              AND subcategory = 'À classifier'
                                            """
                                        ),
                                        {"fid": _bfid, "sub": bf_sub, "pkg": bf_pack, "id": pid},
                                    )
                            n_done = len(st.session_state["reclass_selected"])
                            st.session_state["reclass_selected"] = set()
                            st.toast(f"✓ {n_done} produits classés en une opération", icon="🌿")
                            st.rerun()
                        except IntegrityError as exc:
                            st.error(f"Erreur d'intégrité : {exc.orig}")
        with bar_r:
            if n_sel > 0 and st.button("✗ tout désélectionner", key="clear_sel", use_container_width=True):
                st.session_state["reclass_selected"] = set()
                st.rerun()


# ============================================================================
#  Tab 2 — Ingestion en attente (needs_info)  — hi-fi card-per-row
# ============================================================================
with tab_needs_info:
  if _mode_key == "Ingestion en attente":
    if n_needs_info == 0:
        st.markdown(
            '<div class="hf-card ok" style="text-align:center;padding:24px">'
            '<div style="font-weight:600;font-size:14px;color:var(--hf-ink)">'
            'Aucune ligne d\'ingestion en attente d\'information 🌿</div></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f'<h2 class="hf-h2" style="margin:8px 0 6px 0">{n_needs_info} ligne(s) en attente d\'une décision humaine</h2>',
            unsafe_allow_html=True,
        )

        subs_lookup, packs_lookup = _load_taxonomy_lookups()
        queue_rows = fetch_all(
            """
            SELECT id, source, source_reference, raw_payload,
                   candidate_reference_name, candidate_family_hint,
                   candidate_packaging, candidate_unit_type,
                   candidate_supplier_id, candidate_supplier_hint,
                   candidate_labor_norm_id, candidate_labor_hint,
                   candidate_cost_ht, review_notes, created_at
              FROM ingestion_queue
             WHERE status IN ('pending', 'needs_info')
             ORDER BY created_at ASC
            """
        )

        now_utc = datetime.now(timezone.utc)
        for q in queue_rows:
            created = q["created_at"]
            if created and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            days_old = (now_utc - created).days if created else 0
            old = days_old > 30
            age_color = "var(--hf-terra)" if old else "var(--hf-ink)"
            day_str = created.strftime("%d/%m/%y") if created else "?"

            cont = st.container(border=True)
            with cont:
                top = st.columns([0.5, 5, 1.5])
                with top[0]:
                    st.markdown(
                        f'<div style="font-weight:600;font-size:17px;color:{age_color};line-height:1">{days_old} j</div>'
                        f'<div class="hf-mono" style="font-size:10px;color:var(--hf-muted)">{day_str}</div>',
                        unsafe_allow_html=True,
                    )
                with top[1]:
                    st.markdown(
                        f"""
                        <div style="font-weight:600;font-size:13px;color:var(--hf-ink)">{q['candidate_reference_name'] or '(sans nom)'}</div>
                        <div class="hf-muted" style="font-size:11px">
                          {q['source']} · {q['source_reference'] or '?'}
                        </div>
                        """,
                        unsafe_allow_html=True,
                    )
                with top[2]:
                    st.markdown(
                        hf_chip("needs_info", "danger"),
                        unsafe_allow_html=True,
                    )

                if q["review_notes"]:
                    st.warning(f"Notes : {q['review_notes']}")

                with st.expander("Charge utile brute Gemini", expanded=False):
                    payload = q["raw_payload"] or {}
                    if isinstance(payload, str):
                        try:
                            payload = json.loads(payload)
                        except json.JSONDecodeError:
                            payload = {"raw": payload}
                    st.json(payload)

                c1, c2 = st.columns(2)
                with c1:
                    ref_name = st.text_input(
                        "Nom de référence *",
                        value=q["candidate_reference_name"] or "",
                        key=f"qni_ref_{q['id']}",
                    )
                    family_hint = (q["candidate_family_hint"] or "").lower()
                    initial_family_id = family_by_name_lower.get(family_hint)
                    chosen_family_id, chosen_new_fam, chosen_sub, chosen_pack = render_triplet_picker(
                        key_prefix=f"qni_triplet_{q['id']}",
                        initial_family_id=initial_family_id,
                        initial_subcategory=None,
                        initial_packaging=q["candidate_packaging"],
                        subs_lookup=subs_lookup,
                        packs_lookup=packs_lookup,
                    )

                with c2:
                    unit_type = st.selectbox(
                        "Unité *",
                        options=UNIT_TYPES,
                        index=UNIT_TYPES.index(q["candidate_unit_type"])
                        if q["candidate_unit_type"] in UNIT_TYPES
                        else 0,
                        key=f"qni_unit_{q['id']}",
                    )
                    cost_ht = st.number_input(
                        "Coût HT (€) *",
                        min_value=0.0,
                        value=float(q["candidate_cost_ht"] or 0.0),
                        step=0.01,
                        format="%.2f",
                        key=f"qni_cost_{q['id']}",
                    )
                    sup_options = [None] + [s["id"] for s in suppliers]
                    sup_default_idx = (
                        sup_options.index(q["candidate_supplier_id"])
                        if q["candidate_supplier_id"] in sup_options
                        else 0
                    )
                    supplier_id = st.selectbox(
                        "Fournisseur *",
                        options=sup_options,
                        index=sup_default_idx,
                        format_func=lambda i: "(non choisi)"
                        if i is None
                        else next((s["name"] for s in suppliers if s["id"] == i), str(i)),
                        key=f"qni_sup_{q['id']}",
                    )
                    _labor_by_id = {ln["id"]: ln["task_name"] for ln in labor_norms}
                    norm_pick = render_labor_norm_picker(
                        key_prefix=f"qni_ln_{q['id']}",
                        labor_norms=labor_norms,
                        labor_by_id=_labor_by_id,
                        default_unit=unit_type,
                        initial_labor_norm_id=q["candidate_labor_norm_id"],
                        label="Norme de pose *",
                    )
                    labor_norm_id = norm_pick["labor_norm_id"]

                ready = (
                    ref_name.strip()
                    and chosen_family_id is not None
                    and (chosen_family_id != FAMILY_NEW_ID or chosen_new_fam)
                    and chosen_sub
                    and chosen_sub != "À classifier"
                    and chosen_pack
                    and cost_ht > 0
                    and supplier_id is not None
                    and labor_norm_id is not None
                    and (labor_norm_id != LABOR_NEW_ID or norm_pick["new_name"])
                )
                cb1, cb2 = st.columns(2)
                with cb1:
                    if st.button(
                        "✓ Valider et insérer",
                        key=f"qni_save_{q['id']}",
                        type="primary",
                        disabled=not ready,
                        use_container_width=True,
                    ):
                        try:
                            actor = os.environ.get("STREAMLIT_AUTH_USER", "system")
                            with transaction(ingestion_source=q["source"]) as conn:
                                _fid = resolve_family(conn, chosen_family_id, chosen_new_fam)
                                _lid = labor_norm_id
                                if _lid == LABOR_NEW_ID:
                                    _lid = quick_create_labor_norm(
                                        conn, norm_pick["new_name"],
                                        norm_pick["new_unit"], norm_pick["new_pose_hours"],
                                    )
                                _ensure_taxonomy_row(conn, _fid, chosen_sub, chosen_pack)
                                res = conn.execute(
                                    text(
                                        """
                                        INSERT INTO products
                                            (reference_name, family_id, subcategory,
                                             supplier_id, labor_norm_id,
                                             packaging, unit_type, cost_ht)
                                        VALUES
                                            (:ref, :fid, :sub, :sup, :ln, :pkg, :unit, :cost)
                                        ON CONFLICT (reference_name, packaging, supplier_id)
                                          DO UPDATE SET
                                            cost_ht       = EXCLUDED.cost_ht,
                                            family_id     = EXCLUDED.family_id,
                                            subcategory   = EXCLUDED.subcategory,
                                            labor_norm_id = EXCLUDED.labor_norm_id,
                                            unit_type     = EXCLUDED.unit_type
                                        RETURNING id, (xmax = 0) AS is_insert
                                        """
                                    ),
                                    {
                                        "ref": ref_name.strip(),
                                        "fid": _fid,
                                        "sub": chosen_sub,
                                        "sup": supplier_id,
                                        "ln": _lid,
                                        "pkg": chosen_pack,
                                        "unit": unit_type,
                                        "cost": float(cost_ht),
                                    },
                                ).first()
                                product_id, is_insert = int(res[0]), bool(res[1])
                                if is_insert:
                                    conn.execute(
                                        text(
                                            """
                                            INSERT INTO price_history
                                                (product_id, cost_ht, source,
                                                 source_reference, recorded_by)
                                            VALUES
                                                (:pid, :cost, :src, :ref, :by)
                                            """
                                        ),
                                        {
                                            "pid": product_id, "cost": float(cost_ht),
                                            "src": q["source"], "ref": q["source_reference"], "by": actor,
                                        },
                                    )
                                conn.execute(
                                    text(
                                        """
                                        UPDATE ingestion_queue SET
                                            status = 'approved',
                                            reviewed_at = now(),
                                            reviewed_by = :by,
                                            matched_product_id = :pid,
                                            candidate_supplier_id = :sup,
                                            candidate_labor_norm_id = :ln
                                          WHERE id = :id
                                        """
                                    ),
                                    {
                                        "by": actor, "pid": product_id, "sup": supplier_id,
                                        "ln": _lid, "id": q["id"],
                                    },
                                )
                            st.toast(
                                f"✓ Produit {'créé' if is_insert else 'mis à jour'} (id {product_id})",
                                icon="🌿",
                            )
                            st.rerun()
                        except IntegrityError as exc:
                            st.error(f"Erreur d'intégrité : {exc.orig}")
                with cb2:
                    if st.button("⛔ Rejeter", key=f"qni_reject_{q['id']}", use_container_width=True):
                        actor = os.environ.get("STREAMLIT_AUTH_USER", "system")
                        execute(
                            """
                            UPDATE ingestion_queue SET
                                status = 'rejected',
                                reviewed_at = now(),
                                reviewed_by = :by
                              WHERE id = :id
                            """,
                            {"id": q["id"], "by": actor},
                        )
                        st.info("Ligne rejetée.")
                        st.rerun()


render_footer()
