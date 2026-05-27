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

import io
import os
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
from lib.dpgf import DpgfFormatError, DpgfLine, parse_dpgf, stats as dpgf_stats
from lib.matcher import find_similar_products

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
S.setdefault("dpgf_matches", {})           # row_index -> selected product_id ("create_new" or int or None)


def _reset() -> None:
    for k in ("dpgf_step", "dpgf_lines", "dpgf_filename", "dpgf_project_name", "dpgf_matches"):
        S.pop(k, None)


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

        # Pre-compute matching candidates
        st.markdown(
            '<div class="hf-card" style="padding:14px">'
            f'<b>{len(lines)} ligne(s)</b> détectée(s) — calcul des correspondances…</div>',
            unsafe_allow_html=True,
        )
        matches: dict[int, dict[str, Any]] = {}
        progress = st.progress(0.0)
        for i, line in enumerate(lines):
            cands = []
            # If the line has a parsed picker, do exact-match lookup first
            if line.reference_name:
                exact = fetch_one(
                    """
                    SELECT p.id, p.reference_name, pf.name AS family_name,
                           p.subcategory, p.packaging, p.cost_ht, s.name AS supplier_name
                      FROM products p
                      JOIN product_families pf ON pf.id = p.family_id
                      JOIN suppliers s         ON s.id = p.supplier_id
                     WHERE p.is_active
                       AND p.reference_name = :ref
                       AND p.packaging = :pkg
                     LIMIT 1
                    """,
                    {"ref": line.reference_name, "pkg": line.conditionnement or ""},
                )
                if exact:
                    cands.append(dict(exact))
            # Always layer in fuzzy candidates from the client_designation
            fuzzy_text = line.client_designation or line.reference_name or ""
            fuzzy = find_similar_products(fuzzy_text, top_k=5)
            # Dedup by product id
            seen = {c["id"] for c in cands}
            for c in fuzzy:
                if c["id"] not in seen:
                    cands.append(c)
                    seen.add(c["id"])
            matches[line.row_index] = {
                "candidates": cands[:5],
                "selected_id": cands[0]["id"] if cands else None,
            }
            progress.progress((i + 1) / max(1, len(lines)))
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

    # Counts
    n_total = len(lines)
    n_matched = sum(1 for r in matches.values() if r.get("selected_id") not in (None, "create_new"))
    n_ambig = sum(
        1
        for r in matches.values()
        if r.get("selected_id") not in (None, "create_new")
        and len(r.get("candidates") or []) > 1
    )
    n_unmatched = sum(
        1 for r in matches.values() if r.get("selected_id") in (None, "create_new")
    )

    chips_html = (
        f'<div class="hf-row" style="gap:8px;margin:2px 0 10px 0">'
        f'{hf_chip(f"✅ {n_matched} matchées", "ok")}'
        f'{hf_chip(f"🟡 {n_ambig} ambiguës", "warn")}'
        f'{hf_chip(f"⛔ {n_unmatched} sans match", "danger")}'
        f"</div>"
    )
    st.markdown(chips_html, unsafe_allow_html=True)

    ac1, ac2, ac3 = st.columns([1, 1, 2])
    with ac1:
        if st.button("↺ recommencer", key="dpgf_reset_top", use_container_width=True):
            _reset()
            st.rerun()
    with ac2:
        pass
    with ac3:
        if st.button(
            f"→ Étape suivante : valider ({n_matched})",
            key="dpgf_to_step3",
            type="primary",
            disabled=(n_matched == 0),
            use_container_width=True,
        ):
            S["dpgf_step"] = 2
            st.rerun()

    st.markdown(
        '<h2 class="hf-h2" style="margin-top:8px">Tableau de rapprochement</h2>',
        unsafe_allow_html=True,
    )

    for line in lines:
        ri = line.row_index
        m = matches[ri]
        cands: list[dict[str, Any]] = m.get("candidates") or []
        selected = m.get("selected_id")

        # Build options: top-5 candidates + "create_new" + "(skip)"
        option_ids: list[Any] = [c["id"] for c in cands]
        if "create_new" not in option_ids:
            option_ids = option_ids + ["create_new"]
        option_ids = [None] + option_ids
        sel_idx = (
            option_ids.index(selected) if selected in option_ids else 0
        )

        def _label_for(cid):
            if cid is None:
                return "(ignorer cette ligne)"
            if cid == "create_new":
                return "+ créer un produit"
            c = next((x for x in cands if x["id"] == cid), None)
            if not c:
                return f"produit #{cid}"
            return f"#{c['id']} {c['reference_name']} · {c['family_name']} · {c['packaging']}"

        # Status dot
        if selected is None:
            dot_state, status_text = "bad", "à mapper"
        elif selected == "create_new":
            dot_state, status_text = "warn", "à créer"
        elif len(cands) > 1:
            dot_state, status_text = "warn", "à vérifier"
        else:
            dot_state, status_text = "ok", "matché"

        pu = f"{line.pu_client:,.2f} €".replace(",", " ") if line.pu_client else "—"
        qty = (
            f"{line.quantity:,.2f}".rstrip("0").rstrip(",").replace(",", " ")
            if line.quantity is not None else "—"
        )
        prix_total = (
            f"{(line.pu_client * line.quantity):,.2f} €".replace(",", " ")
            if (line.pu_client and line.quantity) else "—"
        )

        cont = st.container(border=True)
        with cont:
            row_l, row_r, row_btn = st.columns([5, 2, 1.5])
            with row_l:
                st.markdown(
                    f"""
                    <div class="hf-row" style="gap:10px;align-items:center">
                      {hf_dot(dot_state)}
                      <div style="font-family:JetBrains Mono,monospace;font-size:10.5px;color:var(--hf-muted);width:42px">L{ri}</div>
                      <div style="min-width:0;flex:1">
                        <div style="font-size:13px;color:var(--hf-ink);font-weight:500">{line.client_designation or '(sans désignation)'}</div>
                        <div class="hf-muted" style="font-size:11px">
                          qté {qty} · PU {pu} · total {prix_total} · {status_text}
                        </div>
                      </div>
                    </div>
                    """,
                    unsafe_allow_html=True,
                )
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


# ============================================================================
#  Step 3 — Valider
# ============================================================================
elif S["dpgf_step"] == 2:
    lines: list[DpgfLine] = S["dpgf_lines"]
    matches = S["dpgf_matches"]

    # Categorise: matched lines update an existing product, create_new lines
    # spawn a fresh product (the parsed picker pieces become its triplet, or
    # we fall back to "À classifier" and the À classifier queue picks it up).
    match_lines: list[tuple[DpgfLine, int]] = []
    create_lines: list[DpgfLine] = []
    for line in lines:
        m = matches.get(line.row_index, {})
        sel = m.get("selected_id")
        # A usable line must carry at least a positive client PU (col. BC).
        if not (line.pu_client and line.pu_client > 0):
            continue
        if isinstance(sel, int):
            match_lines.append((line, sel))
        elif sel == "create_new":
            create_lines.append(line)

    n_match = len(match_lines)
    n_create = len(create_lines)
    n_ready = n_match + n_create
    # Lines where the client PU differs from the supplier cost — those are
    # the ones that get an extra price_history validation point. The rest
    # only refresh cost_ht silently.
    n_distinct_client = sum(
        1 for line, _ in match_lines if line.client_price_differs_from_supplier
    ) + sum(
        1 for line in create_lines if line.client_price_differs_from_supplier
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
              {hf_chip(f"{n_distinct_client} PU client distincts", "danger" if n_distinct_client else "ghost")}
            </div>
            <div class="hf-mono" style="font-size:12px;color:var(--hf-ink);font-weight:600">
              {n_ready} ligne(s) prête(s)
            </div>
          </div>
          <div class="hf-muted" style="font-size:11.5px;margin-top:8px;line-height:1.55">
            ↳ <b>Coût HT</b> du produit mis à jour avec le PU fournisseur (col. AQ) — c'est notre coût d'achat réel.<br>
            ↳ <b>PU client accepté</b> (col. BC) enregistré dans l'historique
            <code style="font-family:JetBrains Mono,monospace;background:var(--hf-cream);padding:1px 5px;border-radius:3px;font-size:10.5px">source = dpgf_return</code>
            <b>uniquement si</b> ce PU diffère du PU fournisseur. Quand les deux sont identiques,
            la mise à jour de coût suffit, pas besoin d'un point séparé.<br>
            ↳ Pour les <b>nouveaux produits</b>, fournisseur = nom détecté ou « Fournisseur inconnu » ;
            norme = « Norme par défaut (à classifier) ». S'il manque la sous-cat ou le conditionnement,
            le produit part dans la page <b>À classifier</b>.
          </div>
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
            "PU client distinct": (
                "🔴 oui" if line.client_price_differs_from_supplier else "—"
            ),
        })
    for line in create_lines:
        new_label = (
            f"{line.famille or '?'} · {line.sous_cat or 'À classifier'} · "
            f"{line.reference_name or line.client_designation[:24]} · "
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
            "PU client distinct": (
                "🔴 oui" if line.client_price_differs_from_supplier else "—"
            ),
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

                    # ── 1. Update existing matched products ──────────
                    for line, pid in match_lines:
                        # Pick the actual supplier cost (AQ) — falls back
                        # to the client PU if AQ is empty in the row.
                        new_cost = (
                            float(line.pu_fourniture)
                            if line.pu_fourniture else float(line.pu_client)
                        )
                        # The audit trigger on products.cost_ht will insert
                        # a price_history row tagged with the ingestion
                        # source from the `transaction(...)` SET LOCAL.
                        conn.execute(
                            text(
                                "UPDATE products SET cost_ht = :cost, "
                                "last_price_update = now() "
                                "WHERE id = :pid"
                            ),
                            {"cost": new_cost, "pid": pid},
                        )
                        n_updated += 1

                        # Validation point: log the client PU (BC) ONLY
                        # if it differs from the supplier cost (AQ) we
                        # just used to update cost_ht. When they're
                        # identical, the BC entry would just duplicate
                        # the cost update — no extra signal.
                        if line.client_price_differs_from_supplier:
                            conn.execute(
                                text(
                                    "INSERT INTO price_history "
                                    "(product_id, cost_ht, source, source_reference, recorded_by) "
                                    "VALUES (:pid, :cost, 'dpgf_return', :ref, :by)"
                                ),
                                {
                                    "pid": pid,
                                    "cost": float(line.pu_client),
                                    "ref": (
                                        source_ref
                                        + f" :: PU client {line.pu_client:.2f} "
                                        + f"≠ PU fournisseur {line.pu_fourniture:.2f}"
                                    ),
                                    "by": actor,
                                },
                            )
                            n_validated += 1

                    # ── 2. Create new products for unmatched lines ────
                    for line in create_lines:
                        # Resolve the family by name if we got one from the
                        # picker; otherwise we can't usefully create a row.
                        family_id: int | None = None
                        if line.famille:
                            fam_row = conn.execute(
                                text("SELECT id FROM product_families WHERE name = :n LIMIT 1"),
                                {"n": line.famille},
                            ).mappings().first()
                            if fam_row:
                                family_id = int(fam_row["id"])
                            else:
                                # Family from picker doesn't exist yet — make it.
                                fam_row = conn.execute(
                                    text(
                                        "INSERT INTO product_families (name) "
                                        "VALUES (:n) "
                                        "ON CONFLICT (name) DO UPDATE SET name = EXCLUDED.name "
                                        "RETURNING id"
                                    ),
                                    {"n": line.famille},
                                ).mappings().first()
                                family_id = int(fam_row["id"])
                        if family_id is None:
                            # No family info at all — skip this line.
                            continue

                        # Fall back to "À classifier" for missing pieces so
                        # the new product lands in the À classifier queue.
                        subcategory = line.sous_cat or "À classifier"
                        packaging = line.conditionnement or "À classifier"

                        # Ensure the taxonomy triplet exists (composite FK).
                        conn.execute(
                            text(
                                "INSERT INTO product_taxonomy "
                                "(family_id, subcategory, packaging, created_by, notes) "
                                "VALUES (:fid, :sub, :pkg, 'dpgf_return', "
                                "'Auto-créé via Retour DPGF') "
                                "ON CONFLICT (family_id, subcategory, packaging) DO NOTHING"
                            ),
                            {"fid": family_id, "sub": subcategory, "pkg": packaging},
                        )

                        # Resolve supplier — match by name (case-insensitive),
                        # else fall back to the "Fournisseur inconnu"
                        # placeholder.
                        supplier_id = _placeholder_supplier_id
                        if line.fournisseur:
                            sup_row = conn.execute(
                                text(
                                    "SELECT id FROM suppliers "
                                    "WHERE lower(name) = lower(:n) LIMIT 1"
                                ),
                                {"n": line.fournisseur.strip()},
                            ).mappings().first()
                            if sup_row:
                                supplier_id = int(sup_row["id"])

                        # Pick the cost: AQ first (the actual supplier cost
                        # from the finished template) → BC fallback.
                        new_cost = (
                            float(line.pu_fourniture)
                            if line.pu_fourniture else float(line.pu_client)
                        )
                        # SKU-ish reference name: prefer the picker's
                        # parsed name, fall back to the truncated client
                        # designation.
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
                                RETURNING id
                                """
                            ),
                            {
                                "ref": ref_name,
                                "fid": family_id,
                                "sub": subcategory,
                                "sid": supplier_id,
                                "lid": _default_labor_id,
                                "pkg": packaging,
                                "unit": line.unit or "u",
                                "cost": new_cost,
                                "notes": f"Auto-créé via Retour DPGF ({source_ref})",
                            },
                        ).mappings().first()
                        new_pid = int(ins["id"])
                        n_created += 1

                        # Validation point: same rule as the matched
                        # branch — only log when client PU differs from
                        # supplier cost.
                        if line.client_price_differs_from_supplier:
                            conn.execute(
                                text(
                                    "INSERT INTO price_history "
                                    "(product_id, cost_ht, source, source_reference, recorded_by) "
                                    "VALUES (:pid, :cost, 'dpgf_return', :ref, :by)"
                                ),
                                {
                                    "pid": new_pid,
                                    "cost": float(line.pu_client),
                                    "ref": (
                                        source_ref
                                        + f" :: PU client {line.pu_client:.2f} "
                                        + f"≠ PU fournisseur {line.pu_fourniture:.2f}"
                                    ),
                                    "by": actor,
                                },
                            )
                            n_validated += 1

                msg = (
                    f"✓ {n_updated} produit(s) mis à jour · "
                    f"{n_created} produit(s) créé(s) · "
                    f"{n_validated} point(s) de validation enregistré(s)."
                )
                st.success(msg)
                _reset()
                st.balloons()
            except Exception as exc:
                st.error(f"Échec de l'enregistrement : {exc}")


render_footer()
