"""Shared inline-create pickers for the cascade taxonomy (famille /
sous-catégorie / conditionnement) and labor norms (norme de pose).

Two layers:

  • CONN-LEVEL RESOLVERS — call inside a `lib.db.transaction()` block. They
    take a live SQLAlchemy connection, INSERT-if-missing, and return the
    resolved id. Used by every page's commit path (product form, Retour DPGF
    clarification, À classifier, Ingestion) so creation is consistent and
    atomic. Mirrors the INSERT patterns already used in pages/3_Produits.py.

  • RENDER HELPERS — Streamlit selectboxes with a "+ créer nouveau…" sentinel
    that reveal inline inputs, returning a small dict the caller hands to the
    resolvers at save time. This brings the product-form's inline-create
    capability to every other surface, so the user never has to leave the
    page to add a famille / sous-cat / conditionnement / norme.
"""

from __future__ import annotations

import streamlit as st
from sqlalchemy import text

# Sentinels. Kept identical to the strings already used across the app so the
# UX reads the same everywhere.
NEW_SENTINEL = "+ créer nouveau…"
FAMILY_NEW_ID = -1
LABOR_NEW_ID = -1
SUPPLIER_NEW_ID = -1

UNIT_TYPES = ["u", "m3", "ml", "m2", "Ft", "kg", "l"]


# ===========================================================================
#  Conn-level resolvers (call inside transaction())
# ===========================================================================
def resolve_family(conn, family_id, new_name: str) -> int:
    """Return a product_families.id. If family_id == FAMILY_NEW_ID, INSERT
    `new_name` (idempotent on the UNIQUE name) and return the new id."""
    if family_id != FAMILY_NEW_ID:
        return int(family_id)
    name = (new_name or "").strip()
    if not name:
        raise ValueError("Nom de famille requis pour en créer une nouvelle.")
    row = conn.execute(
        text(
            "INSERT INTO product_families (name) VALUES (:n) "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING id"
        ),
        {"n": name},
    ).mappings().first()
    return int(row["id"])


def resolve_supplier(conn, supplier_id, new_name: str) -> int:
    """Return a suppliers.id. If supplier_id == SUPPLIER_NEW_ID, INSERT
    `new_name` (idempotent on the UNIQUE name) and return the new id."""
    if supplier_id != SUPPLIER_NEW_ID:
        return int(supplier_id)
    name = (new_name or "").strip()
    if not name:
        raise ValueError("Nom du fournisseur requis pour en créer un nouveau.")
    row = conn.execute(
        text(
            "INSERT INTO suppliers (name) VALUES (:n) "
            "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
            "RETURNING id"
        ),
        {"n": name},
    ).mappings().first()
    return int(row["id"])


def ensure_taxonomy(conn, family_id: int, subcategory: str, packaging: str,
                    created_by: str = "inline") -> None:
    """Idempotently insert the (family, subcat, packaging) triplet so the
    composite FK on products is satisfied before the product insert."""
    conn.execute(
        text(
            "INSERT INTO product_taxonomy "
            "(family_id, subcategory, packaging, created_by, notes) "
            "VALUES (:fid, :sub, :pkg, :by, 'Créé inline') "
            "ON CONFLICT (family_id, subcategory, packaging) DO NOTHING"
        ),
        {"fid": family_id, "sub": subcategory, "pkg": packaging, "by": created_by},
    )


def quick_create_labor_norm(conn, name: str, unit: str, pose_hours: float) -> int:
    """Quick-create a labor norm from the minimum useful fields: name + unit +
    pose time. UTH defaults to 1 and the three décharge tiers default to the
    pose time (a conservative placeholder that satisfies the NOT NULL
    constraints — refine later on the Normes de pose page). Idempotent on
    task_name."""
    name = (name or "").strip()
    if not name:
        raise ValueError("Nom de la norme de pose requis.")
    h = float(pose_hours or 0)
    row = conn.execute(
        text(
            """
            INSERT INTO labor_norms
                (task_name, unit_type, nombre_uth_default, heure_u_pose_default,
                 tier_1_label, tier_1_heure_u_decharge,
                 tier_2_label, tier_2_heure_u_decharge,
                 tier_3_label, tier_3_heure_u_decharge, notes)
            VALUES
                (:n, :u, 1, :h,
                 'facile', :h, 'moyen', :h, 'difficile', :h,
                 'Créée inline (quick) — à affiner sur Normes de pose')
            ON CONFLICT (task_name) DO UPDATE SET unit_type = EXCLUDED.unit_type
            RETURNING id
            """
        ),
        {"n": name, "u": unit, "h": h},
    ).mappings().first()
    return int(row["id"])


# ===========================================================================
#  Render helpers — selectbox + "+ créer nouveau…" sentinel
# ===========================================================================
def render_taxonomy_picker(
    *,
    key_prefix: str,
    families: list[dict],
    family_by_id: dict[int, str],
    subs_lookup: dict[int, list[str]],
    packs_lookup: dict[tuple[int, str], list[str]],
    initial_family_id: int | None = None,
    initial_subcategory: str | None = None,
    initial_packaging: str | None = None,
    horizontal: bool = True,
) -> dict:
    """Three cascading selects (famille / sous-cat / conditionnement), each
    offering "+ créer nouveau…". Unlike the legacy pages/6 picker, the FAMILLE
    select also offers create. Returns a dict the caller resolves at save:
        {family_id, new_family_name, subcategory, packaging}
    where family_id may be FAMILY_NEW_ID.
    """
    cols = st.columns(3) if horizontal else (st.container(), st.container(), st.container())

    # ── Famille (with create) ──
    with cols[0]:
        fam_opts = [f["id"] for f in families] + [FAMILY_NEW_ID]
        fam_idx = (
            fam_opts.index(initial_family_id)
            if initial_family_id in fam_opts else 0
        )
        family_id = st.selectbox(
            "Famille *",
            options=fam_opts,
            index=fam_idx,
            format_func=lambda i: NEW_SENTINEL if i == FAMILY_NEW_ID else family_by_id.get(i, str(i)),
            key=f"{key_prefix}_family",
        )
        new_family_name = ""
        if family_id == FAMILY_NEW_ID:
            new_family_name = st.text_input(
                "Nouvelle famille",
                value="",
                key=f"{key_prefix}_family_new",
                placeholder="ex. Mobilier outdoor, Arrosage…",
            ).strip()

    # ── Sous-catégorie (with create) ──
    with cols[1]:
        existing_subs = subs_lookup.get(family_id, []) if family_id != FAMILY_NEW_ID else []
        sub_opts = existing_subs + [NEW_SENTINEL]
        sub_idx = existing_subs.index(initial_subcategory) if initial_subcategory in existing_subs else (
            len(sub_opts) - 1 if family_id == FAMILY_NEW_ID else 0
        )
        chosen_sub = st.selectbox(
            "Sous-catégorie *",
            options=sub_opts,
            index=min(sub_idx, len(sub_opts) - 1),
            key=f"{key_prefix}_sub",
        )
        if chosen_sub == NEW_SENTINEL:
            chosen_sub = st.text_input(
                "Nouvelle sous-catégorie",
                value="",
                key=f"{key_prefix}_sub_new",
                placeholder="Conifère, Topiaire…",
            ).strip()

    # ── Conditionnement (with create) ──
    with cols[2]:
        existing_packs = (
            packs_lookup.get((family_id, chosen_sub), [])
            if family_id != FAMILY_NEW_ID else []
        )
        pack_opts = existing_packs + [NEW_SENTINEL]
        pack_idx = existing_packs.index(initial_packaging) if initial_packaging in existing_packs else (
            len(pack_opts) - 1 if not existing_packs else 0
        )
        chosen_pack = st.selectbox(
            "Conditionnement *",
            options=pack_opts,
            index=min(pack_idx, len(pack_opts) - 1),
            key=f"{key_prefix}_pack",
        )
        if chosen_pack == NEW_SENTINEL:
            chosen_pack = st.text_input(
                "Nouveau conditionnement",
                value="",
                key=f"{key_prefix}_pack_new",
                placeholder="Conteneur 7L, Sac 25kg…",
            ).strip()

    return {
        "family_id": family_id,
        "new_family_name": new_family_name,
        "subcategory": chosen_sub or "",
        "packaging": chosen_pack or "",
    }


def render_supplier_picker(
    *,
    key_prefix: str,
    suppliers: list[dict],
    supplier_by_id: dict[int, str],
    initial_supplier_id: int | None = None,
    initial_name: str | None = None,
    label: str = "Fournisseur *",
) -> dict:
    """Supplier select + "+ créer nouveau…" inline text input. Returns:
        {supplier_id, new_name}
    where supplier_id may be SUPPLIER_NEW_ID. When `initial_name` is given and
    no `initial_supplier_id` matches, the select defaults to the create path
    pre-filled with that name (used by Retour DPGF when the AI column carried a
    supplier name that isn't in the DB yet)."""
    opts = [s["id"] for s in suppliers] + [SUPPLIER_NEW_ID]
    if initial_supplier_id in opts:
        idx = opts.index(initial_supplier_id)
    elif initial_name:
        idx = len(opts) - 1  # default to "+ créer nouveau…"
    else:
        idx = 0
    supplier_id = st.selectbox(
        label,
        options=opts,
        index=idx,
        format_func=lambda i: NEW_SENTINEL if i == SUPPLIER_NEW_ID else supplier_by_id.get(i, str(i)),
        key=f"{key_prefix}_sup",
    )
    new_name = ""
    if supplier_id == SUPPLIER_NEW_ID:
        new_name = st.text_input(
            "Nouveau fournisseur",
            value=initial_name or "",
            key=f"{key_prefix}_sup_new",
            placeholder="ex. Jardin de Mado, Pépinières de l'Aulne…",
        ).strip()
    return {"supplier_id": supplier_id, "new_name": new_name}


def render_labor_norm_picker(
    *,
    key_prefix: str,
    labor_norms: list[dict],
    labor_by_id: dict[int, str],
    default_unit: str = "u",
    initial_labor_norm_id: int | None = None,
    label: str = "Norme de pose",
) -> dict:
    """Norme-de-pose select + a quick "+ créer nouveau…" inline form
    (name + unit + pose time only). Returns a dict the caller resolves:
        {labor_norm_id, new_name, new_unit, new_pose_hours}
    where labor_norm_id may be LABOR_NEW_ID.
    """
    opts = [ln["id"] for ln in labor_norms] + [LABOR_NEW_ID]
    idx = opts.index(initial_labor_norm_id) if initial_labor_norm_id in opts else 0
    labor_norm_id = st.selectbox(
        label,
        options=opts,
        index=idx,
        format_func=lambda i: NEW_SENTINEL if i == LABOR_NEW_ID else labor_by_id.get(i, str(i)),
        key=f"{key_prefix}_norm",
    )
    new_name = new_unit = ""
    new_pose_hours = 0.0
    if labor_norm_id == LABOR_NEW_ID:
        with st.container(border=True):
            st.markdown(
                '<div class="hf-muted" style="font-size:11px;margin-bottom:4px">'
                "↳ Nouvelle norme (rapide) — UTH=1, décharges = temps de pose ; "
                "affinez plus tard sur « Normes de pose ».</div>",
                unsafe_allow_html=True,
            )
            new_name = st.text_input(
                "Nom de la tâche *",
                value="",
                key=f"{key_prefix}_norm_name",
                placeholder="ex. Plantation arbre 10/12, Pose paillage…",
            ).strip()
            nc1, nc2 = st.columns(2)
            with nc1:
                new_unit = st.selectbox(
                    "Unité *",
                    options=UNIT_TYPES,
                    index=UNIT_TYPES.index(default_unit) if default_unit in UNIT_TYPES else 0,
                    key=f"{key_prefix}_norm_unit",
                )
            with nc2:
                new_pose_hours = st.number_input(
                    "Heure pose / unité *",
                    min_value=0.0, value=0.0, step=0.001, format="%.3f",
                    key=f"{key_prefix}_norm_pose",
                )
    return {
        "labor_norm_id": labor_norm_id,
        "new_name": new_name,
        "new_unit": new_unit or default_unit,
        "new_pose_hours": new_pose_hours,
    }
