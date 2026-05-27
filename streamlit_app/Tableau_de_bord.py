"""Merci Raymond — Pricing DB. Entry script.

Streamlit's multipage convention: this file is the home page. Other pages
live in `pages/`. Filename is intentional — Streamlit uses it as the
sidebar label for the entry, so "Tableau_de_bord.py" → "Tableau de bord".

Hi-fi v2 layout (per Claude Design handoff):
  - Top: title + subtitle (date) + right-side quick buttons.
  - 4-KPI strip (4th in accent green).
  - Two columns:
      Left (1.5×): "Ce qui demande ton attention" — three alert cards
                   (warn / danger / neutral) with one-click triage actions.
      Right (1×):  "Dernières activités" compact table + Actions rapides
                   buttons (use `st.switch_page` to jump to pages).
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import streamlit as st

from lib.auth import require_login
from lib.branding import (
    apply_branding,
    hf_chip,
    hf_kpi,
    render_footer,
    render_header,
    render_sidebar_brand,
)
from lib.db import fetch_df, fetch_one

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
)

st.set_page_config(
    page_title="Tableau de bord — Merci Raymond",
    page_icon="🌳",
    layout="wide",
    initial_sidebar_state="expanded",
)

require_login()
apply_branding()
render_sidebar_brand()

with st.sidebar:
    if st.button("Se déconnecter", use_container_width=True, key="logout_main"):
        st.session_state["authed"] = False
        st.session_state.pop("user", None)
        st.rerun()


# ============================================================================
#  Read aggregates upfront
# ============================================================================
def _safe_count(sql: str) -> int:
    row = fetch_one(sql)
    return int(row["c"]) if row and row.get("c") is not None else 0


count_total       = _safe_count("SELECT count(*) AS c FROM products WHERE is_active")
count_to_classify = _safe_count("SELECT count(*) AS c FROM products WHERE subcategory = 'À classifier'")
count_queue_pending = _safe_count("SELECT count(*) AS c FROM ingestion_queue WHERE status = 'pending'")
count_queue_needs  = _safe_count("SELECT count(*) AS c FROM ingestion_queue WHERE status = 'needs_info'")
count_queue_total  = count_queue_pending + count_queue_needs
count_stale_9      = _safe_count(
    "SELECT count(*) AS c FROM products WHERE is_active "
    "AND last_price_update < now() - INTERVAL '9 months'"
)

last_invoice = fetch_one(
    """
    SELECT created_at, source_reference, raw_payload->'supplier'->>'name' AS supplier_name,
           (SELECT count(*) FROM ingestion_queue iq2
              WHERE iq2.source_reference = iq.source_reference) AS n_lines
      FROM ingestion_queue iq
     WHERE source = 'supplier_catalog'
       AND source_reference IS NOT NULL
     ORDER BY created_at DESC
     LIMIT 1
    """
)


# ============================================================================
#  Header
# ============================================================================
now = datetime.now()
weekday_fr = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"][now.weekday()]
month_fr = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
][now.month - 1]
date_str = f"{weekday_fr} {now.day} {month_fr} {now.year} · {now.strftime('%H:%M')}"

render_header(title="Tableau de bord", subtitle=date_str)


# ============================================================================
#  KPI strip
# ============================================================================
k1, k2, k3, k4 = st.columns(4)
with k1:
    hf_kpi(
        "à classifier",
        f"{count_to_classify}",
        unit=" prod.",
        delta=("ouvrir la file de triage" if count_to_classify else "rien à reclasser"),
    )
with k2:
    delta_q = "vu hier" if count_queue_total else "tout traité"
    hf_kpi(
        "ingestions en attente",
        f"{count_queue_total}",
        delta=delta_q,
    )
with k3:
    hf_kpi(
        "prix > 9 mois",
        f"{count_stale_9}",
        unit=f" / {count_total}",
        delta="fraîcheur 🔴" if count_stale_9 else "tout frais",
    )
with k4:
    if last_invoice and last_invoice.get("created_at"):
        ts = last_invoice["created_at"]
        if isinstance(ts, datetime):
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            delta_days = max(0, (datetime.now(timezone.utc) - ts).days)
        else:
            delta_days = 0
        v = "aujourd'hui" if delta_days == 0 else f"il y a {delta_days} j"
        n_lines = last_invoice.get("n_lines") or 0
        supplier = last_invoice.get("supplier_name") or "(fournisseur inconnu)"
        delta_text = f"{supplier} · {n_lines} ligne(s)"
        hf_kpi("dernière facture", v, delta=delta_text, accent=True)
    else:
        hf_kpi("dernière facture", "—", delta="aucune ingestion", accent=True)


# ============================================================================
#  Two-column body
# ============================================================================
st.markdown('<div style="height:4px"></div>', unsafe_allow_html=True)
col_left, col_right = st.columns([3, 2], gap="medium")


# ----- LEFT: "Ce qui demande ton attention" --------------------------------
with col_left:
    st.markdown('<h2 class="hf-h2">Ce qui demande ton attention</h2>', unsafe_allow_html=True)

    # Card 1 — À classifier
    if count_to_classify > 0:
        st.markdown(
            f"""
            <div class="hf-card warn">
              <div class="hf-row hf-between" style="align-items:flex-start">
                <div>
                  <div style="font-weight:600;font-size:14px;color:var(--hf-ink)">{count_to_classify} produits à reclasser</div>
                  <div class="hf-muted" style="font-size:11.5px;margin-top:2px">
                    Migration + nouvelles extractions Gemini sans triplet
                  </div>
                </div>
                {hf_chip("action", "warn")}
              </div>
            </div>
            <div style="height:14px"></div>
            """,
            unsafe_allow_html=True,
        )
        a1, a2 = st.columns([1, 1])
        with a1:
            if st.button("Ouvrir la file de triage", key="goto_classifier"):
                st.switch_page("pages/6_A_classifier.py")
        with a2:
            if st.button("+ ajouter un triplet", key="add_triplet"):
                st.session_state["taxo_tab"] = "Référentiel taxonomie"
                st.switch_page("pages/6_A_classifier.py")
        st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)

    # Card 2 — Ingestions bloquées
    if count_queue_total > 0:
        blocked_rows = fetch_df(
            """
            SELECT source_reference, status, review_notes,
                   to_char(created_at, 'DD/MM') AS day,
                   raw_payload->'supplier'->>'name' AS supplier_name
              FROM ingestion_queue
             WHERE status IN ('needs_info', 'pending')
             ORDER BY created_at DESC
             LIMIT 3
            """
        )
        items_html = []
        for _, r in blocked_rows.iterrows():
            ref = (r.get("supplier_name") or "?") + " · " + (r.get("day") or "")
            reason = r.get("review_notes") or ("triplet incomplet" if r["status"] == "pending" else "extraction échouée")
            items_html.append(f"<div>{ref} — {reason}</div>")
        kind = "danger" if count_queue_needs > 0 else "warn"
        chip = hf_chip("bloqué", "danger") if count_queue_needs > 0 else hf_chip("à traiter", "warn")
        st.markdown(
            f"""
            <div class="hf-card {kind}">
              <div class="hf-row hf-between" style="align-items:flex-start">
                <div>
                  <div style="font-weight:600;font-size:14px;color:var(--hf-ink)">{count_queue_total} ingestion(s) en attente</div>
                  <div class="hf-muted" style="font-size:11.5px;margin-top:2px;line-height:1.5">{''.join(items_html)}</div>
                </div>
                {chip}
              </div>
            </div>
            <div style="height:14px"></div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("→ Voir la file d'ingestion", key="goto_ingest_queue", type="primary"):
            st.session_state["taxo_tab"] = "Ingestion en attente"
            st.switch_page("pages/6_A_classifier.py")
        st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)

    # Card 3 — Prix qui vieillissent
    if count_stale_9 > 0:
        # Top 3 oldest
        stale_top = fetch_df(
            """
            SELECT reference_name, EXTRACT(MONTH FROM AGE(now(), last_price_update))::int AS months
              FROM products
             WHERE is_active
               AND last_price_update < now() - INTERVAL '9 months'
             ORDER BY last_price_update ASC
             LIMIT 3
            """
        )
        examples = " · ".join(
            f"{r['reference_name']} ({int(r['months'])} m)"
            for _, r in stale_top.iterrows()
        )
        st.markdown(
            f"""
            <div class="hf-card">
              <div class="hf-row hf-between" style="align-items:flex-start">
                <div>
                  <div style="font-weight:600;font-size:14px;color:var(--hf-ink)">{count_stale_9} prix vieillissent</div>
                  <div class="hf-muted" style="font-size:11.5px;margin-top:2px">seuil 9 mois · top : {examples or '—'}</div>
                </div>
                {hf_chip("surveiller", "ghost")}
              </div>
            </div>
            <div style="height:14px"></div>
            """,
            unsafe_allow_html=True,
        )
        if st.button("→ voir la liste filtrée", key="goto_stale"):
            st.session_state["catalog_fresh_filter"] = "🔴 > 9 mois"
            st.switch_page("pages/3_Produits.py")
        st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)

    if count_to_classify == 0 and count_queue_total == 0 and count_stale_9 == 0:
        st.markdown(
            f"""
            <div class="hf-card ok">
              <div style="font-weight:600;font-size:14px;color:var(--hf-ink)">Tout est à jour 🌿</div>
              <div class="hf-muted" style="font-size:11.5px;margin-top:2px">
                Pas d'alerte à traiter pour le moment. Bonne journée.
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ----- RIGHT: "Dernières activités" + Actions rapides -----------------------
with col_right:
    st.markdown('<h2 class="hf-h2">Dernières activités</h2>', unsafe_allow_html=True)

    # Synthesis: latest price changes + queue events. Take 6 most recent rows.
    activity = fetch_df(
        """
        SELECT * FROM (
            SELECT recorded_at AS ts,
                   ('Prix · ' || p.reference_name) AS what,
                   coalesce(ph.recorded_by, 'system') AS who,
                   ph.source AS source
              FROM price_history ph
              JOIN products p ON p.id = ph.product_id
             ORDER BY ph.recorded_at DESC
             LIMIT 4
        ) AS prices
        UNION ALL
        SELECT * FROM (
            SELECT created_at AS ts,
                   ('Ingestion · ' || coalesce(raw_payload->'supplier'->>'name', source_reference, '?'))
                    AS what,
                   coalesce(reviewed_by, 'system') AS who,
                   source
              FROM ingestion_queue
             WHERE status = 'approved' OR status = 'pending'
             ORDER BY created_at DESC
             LIMIT 3
        ) AS ingest
        ORDER BY ts DESC
        LIMIT 6
        """
    )

    if activity.empty:
        st.markdown(
            '<div class="hf-card flat" style="padding:14px"><div class="hf-muted">Aucune activité enregistrée.</div></div>',
            unsafe_allow_html=True,
        )
    else:
        # Render as a compact HTML table inside a flat card (matches the design exactly)
        rows_html = []
        for _, r in activity.iterrows():
            ts = r["ts"]
            ts_str = ts.strftime("%d/%m %H:%M") if hasattr(ts, "strftime") else str(ts)
            who = r.get("who") or "system"
            what = r.get("what") or ""
            # Truncate to keep the row tight
            if len(what) > 38:
                what = what[:37] + "…"
            rows_html.append(
                f'<tr><td class="mono nowrap" style="color:var(--hf-muted);font-family:JetBrains Mono,monospace;font-size:11px;padding:5px 10px;">{ts_str}</td>'
                f'<td style="padding:5px 10px;font-size:12px;">{what}</td>'
                f'<td style="color:var(--hf-muted);font-size:11px;padding:5px 10px;text-align:right">{who}</td></tr>'
            )
        st.markdown(
            f"""
            <div class="hf-card flat" style="padding:0;overflow:hidden">
              <table style="width:100%;border-collapse:collapse">
                <tbody>{"".join(rows_html)}</tbody>
              </table>
            </div>
            """,
            unsafe_allow_html=True,
        )

    st.markdown('<div style="height:14px"></div>', unsafe_allow_html=True)
    st.markdown('<h2 class="hf-h2">Actions rapides</h2>', unsafe_allow_html=True)

    # Row 1 — primary CTA (most frequent action), full width
    if st.button(
        "↧ Nouvelle facture",
        key="qa_invoice",
        type="primary",
        use_container_width=True,
    ):
        st.session_state["open_picker"] = True
        st.switch_page("pages/5_Ingestion_facture.py")

    # Row 2 — two adds side by side
    qa_l, qa_r = st.columns(2)
    with qa_l:
        if st.button("+ Fournisseur", key="qa_sup", use_container_width=True):
            st.session_state["supplier_add_open"] = True
            st.switch_page("pages/2_Fournisseurs.py")
    with qa_r:
        if st.button("+ Produit", key="qa_prod", use_container_width=True):
            st.session_state.pop("product_edit_id", None)
            st.session_state["produits_tab"] = "Édition"
            st.switch_page("pages/3_Produits.py")

    # Row 3 — DPGF return upload, full width
    if st.button(
        "↧ Déposer un DPGF",
        key="qa_dpgf",
        use_container_width=True,
    ):
        st.switch_page("pages/8_Retour_DPGF.py")


render_footer()
