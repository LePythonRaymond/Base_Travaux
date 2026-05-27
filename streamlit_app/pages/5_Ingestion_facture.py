"""Ingest Invoice — the centerpiece page (hi-fi v2).

Flow:
  A. Upload  : .pdf saved to /data/invoices, SHA-256 checked for duplicates.
  B. Extract : Gemini call → ExtractedInvoice; one ingestion_queue row per line
               (status='pending'); matcher runs per line.
  C. Review  : two-column layout — sticky line list (left) + focused editor
               (right). "Approuver + N similaires" applies a single triplet
               to all lines with similar extracted text in one transaction.
  D. Commit  : single transaction — supplier upsert, product upserts (with
               manual price_history rows for inserts), queue row transitions.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import streamlit as st
from sqlalchemy import text

from lib.auth import require_login
from lib.branding import (
    StepTracker,
    apply_branding,
    hf_chip,
    hf_dot,
    hf_stepper,
    render_footer,
    render_header,
    render_sidebar_brand,
)
from lib.db import fetch_all, fetch_one, get_engine, transaction
from lib.gemini import extract_invoice
from lib.matcher import find_similar_lines, match
from lib.schemas import ExtractedInvoice, ExtractionError

log = logging.getLogger(__name__)

UNIT_TYPES = ["u", "m3", "ml", "m2", "Ft", "kg", "l"]
INGESTION_SOURCE = "supplier_catalog"
NEW_VALUE_SENTINEL = "+ Créer nouveau…"


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
            VALUES (:fid, :sub, :pkg, :by, 'Ajouté pendant l''ingestion d''une facture')
            ON CONFLICT (family_id, subcategory, packaging) DO NOTHING
            """
        ),
        {"fid": family_id, "sub": subcategory, "pkg": packaging, "by": actor},
    )


st.set_page_config(page_title="Ingestion facture — Merci Raymond", page_icon="🌳", layout="wide")
require_login()
apply_branding()
render_sidebar_brand()

# Page-local CSS: make the line-list buttons look like list rows (active = green left border).
st.markdown(
    """
    <style>
    .hf-line-list-wrapper .stButton > button {
        background: transparent !important;
        border: 1px solid transparent !important;
        border-left: 3px solid transparent !important;
        width: 100%;
        padding: 6px 10px !important;
        text-align: left !important;
        font-size: 12px !important;
        font-weight: 500 !important;
        color: var(--hf-body) !important;
        border-radius: 5px !important;
        justify-content: flex-start !important;
        min-height: 30px !important;
        font-family: 'Inter', sans-serif !important;
    }
    .hf-line-list-wrapper .stButton > button:hover {
        background: var(--hf-cream) !important;
    }
    .hf-line-list-wrapper .stButton > button[kind="primary"] {
        background: var(--hf-hover) !important;
        border-left-color: var(--hf-green) !important;
        color: var(--hf-ink) !important;
        font-weight: 600 !important;
    }
    .hf-line-list-wrapper .stButton > button[kind="primary"]:hover {
        background: var(--hf-hover) !important;
    }
    </style>
    """,
    unsafe_allow_html=True,
)


# Mapping action code (matcher) → libellé en français pour l'affichage
ACTION_LABEL_FR = {
    "auto-match": "Correspondance auto",
    "match": "Correspondance LLM",
    "new": "Nouveau produit",
    "skip": "Ligne ignorée",
}


# ============================================================================
#  Helpers
# ============================================================================
def _invoice_dir() -> Path:
    return Path(os.environ.get("INVOICE_DIR", "/data/invoices"))


def _save_pdf(uploaded_file) -> tuple[Path, str]:
    raw = uploaded_file.getvalue()
    sha = hashlib.sha256(raw).hexdigest()
    invoice_dir = _invoice_dir()
    invoice_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{uuid.uuid4().hex}__{Path(uploaded_file.name).name}"
    path = invoice_dir / stem
    path.write_bytes(raw)
    return path, sha


def _check_duplicate(sha: str) -> dict[str, Any] | None:
    return fetch_one(
        """
        SELECT id, source_reference, created_at, status
        FROM ingestion_queue
        WHERE source = :src
          AND raw_payload->>'_invoice_sha256' = :h
        ORDER BY created_at DESC
        LIMIT 1
        """,
        {"src": INGESTION_SOURCE, "h": sha},
    )


def _render_pdf_preview(pdf_bytes: bytes) -> None:
    try:
        doc = pdfium.PdfDocument(pdf_bytes)
        try:
            page = doc[0]
            pil = page.render(scale=1.5).to_pil()
            buf = io.BytesIO()
            pil.save(buf, format="PNG")
            st.image(buf.getvalue(), caption="Aperçu (première page)", use_column_width=True)
        finally:
            doc.close()
    except Exception as exc:
        st.warning(f"Aperçu impossible : {exc}")


def _persist_extraction(
    extracted: ExtractedInvoice,
    *,
    file_path: Path,
    file_hash: str,
) -> list[int]:
    queue_ids: list[int] = []
    engine = get_engine()
    with engine.begin() as conn:
        for line in extracted.line_items:
            payload = json.loads(extracted.model_dump_json())
            payload["_invoice_sha256"] = file_hash
            payload["_line_index"] = len(queue_ids)
            payload["_line"] = json.loads(line.model_dump_json())
            row = conn.execute(
                text(
                    """
                    INSERT INTO ingestion_queue (
                        source, source_reference, raw_payload,
                        candidate_reference_name, candidate_family_hint,
                        candidate_packaging, candidate_unit_type,
                        candidate_supplier_hint, candidate_labor_hint,
                        candidate_cost_ht, status, review_notes
                    )
                    VALUES (
                        :source, :ref, CAST(:payload AS jsonb),
                        :name, :family, :packaging, :unit, :supplier_hint,
                        :labor_hint, :cost, :status, :notes
                    )
                    RETURNING id
                    """
                ),
                {
                    "source": INGESTION_SOURCE,
                    "ref": file_path.name,
                    "payload": json.dumps(payload, default=str, ensure_ascii=False),
                    "name": line.reference_name,
                    "family": line.family_hint,
                    "packaging": line.packaging,
                    "unit": line.unit_type_normalized,
                    "supplier_hint": extracted.supplier.name,
                    "labor_hint": line.suggested_labor_task,
                    "cost": float(line.unit_price_ht) if line.unit_price_ht is not None else None,
                    "status": "rejected" if not line.is_product_line else "pending",
                    "notes": "non-product line" if not line.is_product_line else None,
                },
            ).first()
            queue_ids.append(row[0])
    return queue_ids


# ============================================================================
#  Session state init
# ============================================================================
S = st.session_state
S.setdefault("ing_step", "upload")
S.setdefault("ing_file_path", None)
S.setdefault("ing_file_hash", None)
S.setdefault("ing_pdf_bytes", None)
S.setdefault("ing_extracted", None)
S.setdefault("ing_queue_ids", [])
S.setdefault("ing_match_results", [])
S.setdefault("ing_lines_state", [])
S.setdefault("ing_supplier_choice", "auto")


def _reset() -> None:
    for k in [
        "ing_step", "ing_file_path", "ing_file_hash", "ing_pdf_bytes",
        "ing_extracted", "ing_queue_ids", "ing_match_results", "ing_lines_state",
        "ing_supplier_choice", "active_line_idx", "ing_commit_requested",
    ]:
        S.pop(k, None)


# ============================================================================
#  Step A & B — Upload + Extract
# ============================================================================
if S["ing_step"] == "upload":
    render_header(title="Ingestion facture", subtitle="étape 1 / 4 · dépôt")
    hf_stepper(["Dépôt", "Extraction", "Relecture", "Valider"], current_idx=0)

    uploaded = st.file_uploader("Choisissez un PDF de facture", type=["pdf"])
    if uploaded is not None:
        raw = uploaded.getvalue()
        sha = hashlib.sha256(raw).hexdigest()
        dup = _check_duplicate(sha)
        if dup:
            st.warning(
                f"Cette facture (SHA-256 : `{sha[:12]}…`) a déjà été ingérée le "
                f"{dup['created_at'].strftime('%Y-%m-%d')} (référence `{dup['source_reference']}`, "
                f"statut « {dup['status']} »). Téléversez-la quand même uniquement si vous savez ce que vous faites."
            )
        col_p, col_a = st.columns([2, 1])
        with col_p:
            _render_pdf_preview(raw)
        with col_a:
            st.write(f"**Nom** : {uploaded.name}")
            st.write(f"**Taille** : {len(raw) / 1024:.1f} KB")
            st.write(f"**SHA-256** : `{sha[:16]}…`")
            if st.button("Extraire avec Gemini", type="primary", use_container_width=True):
                tracker = StepTracker(
                    [
                        "Préparation de l'espace de travail",
                        "Lecture du PDF",
                        "Envoi à Gemini (extraction LLM)",
                        "Parsing et validation de la réponse",
                        "Matching avec produits existants",
                        "Préparation de la relecture",
                    ],
                    status_label="Traitement en cours…",
                )

                tracker.activate(0, fill=2)
                file_path, _ = _save_pdf(uploaded)
                labor_norms = fetch_all("SELECT task_name FROM labor_norms ORDER BY task_name")
                families = fetch_all("SELECT id, name FROM product_families ORDER BY name")
                family_id_by_name_lower = {f["name"].lower(): f["id"] for f in families}
                tracker.complete(0)

                tracker.activate(1, fill=4)
                time.sleep(0.15)
                tracker.complete(1)

                tracker.activate(2, fill=2)
                try:
                    extracted = extract_invoice(
                        raw,
                        labor_norm_names=[ln["task_name"] for ln in labor_norms],
                        family_names=[f["name"] for f in families],
                    )
                except ExtractionError as exc:
                    tracker.fail(2, status_label="Échec de l'extraction.")
                    st.error(f"Échec de l'extraction Gemini : {exc}")
                    engine = get_engine()
                    with engine.begin() as conn:
                        conn.execute(
                            text(
                                """
                                INSERT INTO ingestion_queue
                                    (source, source_reference, raw_payload, status, review_notes)
                                VALUES
                                    (:src, :ref, CAST(:payload AS jsonb), 'needs_info', :notes)
                                """
                            ),
                            {
                                "src": INGESTION_SOURCE,
                                "ref": file_path.name,
                                "payload": json.dumps(
                                    {
                                        "_invoice_sha256": sha,
                                        "_error": str(exc),
                                        "_raw_response": getattr(exc, "raw_response", None),
                                    },
                                    ensure_ascii=False,
                                ),
                                "notes": f"Extraction failure: {exc}"[:500],
                            },
                        )
                    st.info("Une entrée `needs_info` a été créée dans la file d'attente.")
                    st.stop()
                tracker.complete(2)

                tracker.activate(3, fill=3)
                queue_ids = _persist_extraction(extracted, file_path=file_path, file_hash=sha)
                tracker.complete(3)

                tracker.activate(4, fill=1)
                match_results: list[dict] = []
                for li in extracted.line_items:
                    if not li.is_product_line:
                        match_results.append({"action": "skip", "product_id": None,
                                              "confidence": 0.0, "reasoning": "Non-product line (Gemini)"})
                        continue
                    query = {
                        "reference_name": li.reference_name or li.designation_raw,
                        "family_id": family_id_by_name_lower.get((li.family_hint or "").lower()),
                        "subcategory": li.subcategory,
                        "packaging": li.packaging,
                        "unit_type": li.unit_type_normalized,
                        "brand": li.brand,
                        "material": li.material,
                        "attributes": li.attributes or {},
                    }
                    result = match(query)
                    match_results.append({"action": result.action, "product_id": result.product_id,
                                          "confidence": result.confidence, "reasoning": result.reasoning})
                tracker.complete(4)

                tracker.activate(5, fill=4)
                lines_state: list[dict] = []
                for li, mr in zip(extracted.line_items, match_results):
                    cost = float(li.unit_price_ht) if li.unit_price_ht else 0.0
                    if not li.is_product_line:
                        initial_status = "Rejeter"
                    elif mr["action"] in {"auto-match", "match"} and cost > 0:
                        initial_status = "Approuver"
                    else:
                        initial_status = "À décider"
                    lines_state.append({
                        "status": initial_status,
                        "reference_name": li.reference_name or "",
                        "family_hint": li.family_hint or "",
                        "brand": li.brand or "",
                        "material": li.material or "",
                        "packaging": li.packaging or "",
                        "unit_type": li.unit_type_normalized or "u",
                        "cost_ht": cost,
                        "attributes": dict(li.attributes or {}),
                        "matched_product_id": mr["product_id"],
                        "suggested_labor_task": li.suggested_labor_task,
                        "subcategory": li.subcategory or "",
                    })
                tracker.finish(status_label="Prêt pour relecture.")
                time.sleep(0.4)

                S["ing_file_path"] = str(file_path)
                S["ing_file_hash"] = sha
                S["ing_pdf_bytes"] = raw
                S["ing_extracted"] = json.loads(extracted.model_dump_json())
                S["ing_queue_ids"] = queue_ids
                S["ing_match_results"] = match_results
                S["ing_lines_state"] = lines_state
                S["ing_step"] = "review"
                S["active_line_idx"] = 0
                st.rerun()


# ============================================================================
#  Step C — Review  (hi-fi two-column layout)
# ============================================================================
if S["ing_step"] == "review":
    extracted = S["ing_extracted"]
    queue_ids = S["ing_queue_ids"]
    match_results = S["ing_match_results"]
    lines_state = S["ing_lines_state"]
    line_items_raw = extracted["line_items"]
    n_lines = len(line_items_raw)
    sup = extracted.get("supplier") or {}
    sup_name = (sup.get("name") or "").strip()

    suppliers = fetch_all("SELECT id, name FROM suppliers ORDER BY name")
    sup_by_id = {s["id"]: s for s in suppliers}
    labor_norms = fetch_all("SELECT id, task_name FROM labor_norms ORDER BY task_name")
    labor_by_name = {ln["task_name"]: ln for ln in labor_norms}
    labor_options = [None] + [ln["id"] for ln in labor_norms]
    families = fetch_all("SELECT id, name FROM product_families ORDER BY name")
    family_by_id = {f["id"]: f["name"] for f in families}
    family_by_name = {f["name"].lower(): f["id"] for f in families}
    subs_by_family, packs_by_pair = _load_taxonomy_lookups()
    products_for_match = fetch_all(
        "SELECT id, reference_name, packaging, unit_type FROM products WHERE is_active = TRUE ORDER BY reference_name"
    )
    product_options = [None, "create_new"] + [p["id"] for p in products_for_match]
    product_by_id = {p["id"]: p for p in products_for_match}

    def _format_product(o: Any) -> str:
        if o is None:
            return "(non choisi)"
        if o == "create_new":
            return "+ Créer un nouveau produit"
        p = product_by_id[o]
        return f"#{p['id']} {p['reference_name']} ({p['packaging']}, {p['unit_type']})"

    # Match supplier on first load
    if S.get("ing_supplier_choice", "auto") == "auto":
        matched_id = None
        if sup_name:
            sl = sup_name.lower()
            for s in suppliers:
                if s["name"] == "Fournisseur inconnu":
                    continue
                nl = s["name"].lower()
                if nl == sl or sl in nl or nl in sl:
                    matched_id = s["id"]
                    break
        if matched_id is not None:
            S["ing_supplier_choice"] = matched_id
        elif sup_name:
            S["ing_supplier_choice"] = "create_new"
        else:
            placeholder = next((s for s in suppliers if s["name"] == "Fournisseur inconnu"), None)
            S["ing_supplier_choice"] = (
                placeholder["id"] if placeholder else (suppliers[0]["id"] if suppliers else "create_new")
            )

    # ---- Aggregate counts for top bar ----
    ready_count = blocked_count = rejected_count = undecided_count = 0
    for ls in lines_state:
        status = ls.get("status") or "À décider"
        cost = float(ls.get("cost_ht") or 0)
        triplet_ok = (
            ls.get("family_id") is not None
            and bool((ls.get("subcategory") or "").strip())
            and bool((ls.get("packaging") or "").strip())
        )
        if status == "Rejeter":
            rejected_count += 1
        elif status == "À décider":
            undecided_count += 1
        elif status == "Approuver":
            if triplet_ok and cost > 0:
                ready_count += 1
            else:
                blocked_count += 1

    # ---- TOP: header + breadcrumb + stepper ----
    file_name = Path(S["ing_file_path"]).name if S.get("ing_file_path") else "—"
    file_short = file_name.split("__", 1)[-1] if "__" in file_name else file_name
    sha_short = (S.get("ing_file_hash") or "")[:6]
    bc_html = (
        f"{file_short} <span class='sep'>·</span> {n_lines} lignes "
        f"<span class='sep'>·</span> sha {sha_short}…"
    )

    hdr_l, hdr_r = st.columns([3, 2])
    with hdr_l:
        render_header(
            title="Ingestion facture",
            subtitle="étape 3 / 4 · relecture",
            breadcrumb=bc_html,
        )
    with hdr_r:
        hf_stepper(["Dépôt", "Extraction", "Relecture", "Valider"], current_idx=2)

    # ---- Status chips + actions row ----
    chips_html = (
        f'<div class="hf-row" style="gap:8px;margin:2px 0 10px 0">'
        f'{hf_chip(f"✅ {ready_count} prêtes", "ok")}'
        f'{hf_chip(f"🟡 {undecided_count} à décider", "warn")}'
        f'{hf_chip(f"⛔ {rejected_count} rejetées", "danger")}'
    )
    if blocked_count:
        chips_html += hf_chip(f"⚠ {blocked_count} bloquées", "danger")
    chips_html += "</div>"
    st.markdown(chips_html, unsafe_allow_html=True)

    ac1, ac2, ac3 = st.columns([1, 1, 2])
    with ac1:
        if st.button("↺ recommencer", key="ing_reset_top", use_container_width=True):
            _reset()
            st.rerun()
    with ac2:
        with st.popover("JSON brut Gemini", use_container_width=True):
            st.json(extracted)
    with ac3:
        if st.button(
            f"✓ Valider et insérer ({ready_count})",
            key="ing_commit_top",
            type="primary",
            disabled=(ready_count == 0 or blocked_count > 0),
            use_container_width=True,
        ):
            S["ing_commit_requested"] = True

    # ---- Supplier resolver (global, compact) ----
    sup_options_pick: list[Any] = ["create_new"] + [s["id"] for s in suppliers]

    def _format_sup(o: Any) -> str:
        if o == "create_new":
            return f"+ Créer nouveau (« {sup_name or '?'} »)"
        return sup_by_id[o]["name"]

    default_sup_idx = (
        sup_options_pick.index(S["ing_supplier_choice"])
        if S["ing_supplier_choice"] in sup_options_pick
        else 0
    )
    chosen_sup = st.selectbox(
        f"Fournisseur · global (Gemini : {sup_name or '?'})",
        options=sup_options_pick,
        index=default_sup_idx,
        format_func=_format_sup,
        key="ing_sup_select",
    )
    S["ing_supplier_choice"] = chosen_sup

    # ---- ACTIVE LINE INDEX ----
    if "active_line_idx" not in S:
        S["active_line_idx"] = 0
    active_idx = int(S["active_line_idx"])
    if active_idx >= n_lines:
        active_idx = 0
        S["active_line_idx"] = 0

    # ---- TWO-COLUMN: list | editor ----
    col_list, col_editor = st.columns([0.32, 0.68], gap="medium")

    # ===== LEFT: sticky line list =====
    with col_list:
        st.markdown(
            f'<h2 class="hf-h2" style="margin:0 0 6px 0">Lignes · {n_lines}</h2>',
            unsafe_allow_html=True,
        )
        st.markdown('<div class="hf-line-list-wrapper">', unsafe_allow_html=True)
        for i, (li, mr, ls) in enumerate(zip(line_items_raw, match_results, lines_state)):
            status = ls.get("status") or "À décider"
            cost = float(ls.get("cost_ht") or 0)
            triplet_ok = (
                ls.get("family_id") is not None
                and bool((ls.get("subcategory") or "").strip())
                and bool((ls.get("packaging") or "").strip())
            )
            if status == "Rejeter":
                dot = "⛔"
            elif status == "Approuver":
                dot = "✅" if (triplet_ok and cost > 0) else "⚠"
            else:
                dot = "🟡"
            desig = (
                li.get("designation_raw")
                or li.get("reference_name")
                or f"ligne {i+1}"
            )
            desig_short = desig if len(desig) <= 30 else (desig[:28] + "…")
            label = f"{i+1:02d}  {dot}  {desig_short}"
            btn_type = "primary" if i == active_idx else "secondary"
            if st.button(label, key=f"line_btn_{i}", use_container_width=True, type=btn_type):
                S["active_line_idx"] = i
                st.rerun()
        st.markdown("</div>", unsafe_allow_html=True)

    # ===== RIGHT: focused editor =====
    with col_editor:
        if n_lines == 0:
            st.info("Aucune ligne extraite.")
        else:
            i = active_idx
            li = line_items_raw[i]
            mr = match_results[i]
            ls = lines_state[i]
            is_product = li.get("is_product_line", True)
            verdict_fr = ACTION_LABEL_FR.get(mr.get("action") or "", mr.get("action") or "")
            conf = mr.get("confidence") or 0
            current_status = ls.get("status") or "À décider"

            status_chip_html = {
                "Approuver": hf_chip("✓ approuvée", "ok"),
                "À décider": hf_chip("🟡 à décider", "warn"),
                "Rejeter": hf_chip("⛔ rejetée", "danger"),
            }.get(current_status, hf_chip(current_status, "ghost"))

            qty = li.get("quantity")
            pu = li.get("unit_price_ht")
            total = li.get("total_ht")

            def _fmt(x):
                if x is None:
                    return "—"
                try:
                    return f"{float(x):.2f}".replace(".", ",")
                except (TypeError, ValueError):
                    return str(x)

            st.markdown(
                f"""
                <div class="hf-row hf-between">
                  <div>
                    <div class="hf-row" style="gap:8px;align-items:baseline">
                      <span class="hf-h2" style="margin:0">Ligne {i+1:02d}</span>
                      <span class="hf-muted" style="font-size:11px">texte extrait du PDF</span>
                    </div>
                    <div style="font-size:15px;color:var(--hf-ink);font-weight:500;margin-top:2px">
                      « {li.get('designation_raw') or '(sans désignation)'} »
                    </div>
                    <div class="hf-mono hf-muted" style="font-size:11px;margin-top:4px">
                      qté {_fmt(qty)} · PU {_fmt(pu)} € · total {_fmt(total)} €
                    </div>
                  </div>
                  <div style="text-align:right">
                    {status_chip_html}
                    <div class="hf-mono hf-muted" style="font-size:10px;margin-top:4px">conf. Gemini {conf:.2f}</div>
                    <div class="hf-muted" style="font-size:11px;margin-top:2px">{verdict_fr}</div>
                  </div>
                </div>
                """,
                unsafe_allow_html=True,
            )
            st.markdown('<div style="height:6px"></div>', unsafe_allow_html=True)

            if not is_product:
                st.info(
                    "Cette ligne n'est pas un produit (main d'œuvre, transport, remise, etc.) "
                    "— elle sera rejetée au commit."
                )
                ls["status"] = "Rejeter"
            else:
                # Cascade row 1
                f1, f2, f3, f4 = st.columns(4)
                with f1:
                    ls["reference_name"] = st.text_input(
                        "Nom de référence *",
                        value=ls.get("reference_name", ""),
                        key=f"name_{i}",
                    )
                with f2:
                    hint_id = family_by_name.get((ls.get("family_hint") or "").lower())
                    family_opts_strict = [f["id"] for f in families]
                    idx_f = (
                        family_opts_strict.index(hint_id)
                        if hint_id in family_opts_strict
                        else (
                            family_opts_strict.index(ls["family_id"])
                            if ls.get("family_id") in family_opts_strict
                            else 0
                        )
                    )
                    ls["family_id"] = st.selectbox(
                        "Famille *",
                        options=family_opts_strict,
                        index=idx_f,
                        format_func=lambda o: family_by_id.get(o, str(o)),
                        key=f"family_{i}",
                    )
                with f3:
                    existing_subs = subs_by_family.get(ls["family_id"], [])
                    sub_options = existing_subs + [NEW_VALUE_SENTINEL]
                    current_sub = ls.get("subcategory") or ""
                    sub_idx = existing_subs.index(current_sub) if current_sub in existing_subs else 0
                    chosen_sub = st.selectbox(
                        "Sous-catégorie *",
                        options=sub_options,
                        index=sub_idx if existing_subs else 0,
                        key=f"subcat_{i}",
                    )
                    if chosen_sub == NEW_VALUE_SENTINEL:
                        chosen_sub = st.text_input(
                            "Nouvelle sous-cat", value="",
                            key=f"subcat_new_{i}", placeholder="Conifère…",
                        ).strip()
                    ls["subcategory"] = chosen_sub
                with f4:
                    existing_packs = packs_by_pair.get((ls["family_id"], chosen_sub), [])
                    pack_options = existing_packs + [NEW_VALUE_SENTINEL]
                    current_pack = ls.get("packaging") or ""
                    pack_idx = existing_packs.index(current_pack) if current_pack in existing_packs else 0
                    chosen_pack = st.selectbox(
                        "Conditionnement *",
                        options=pack_options,
                        index=pack_idx if existing_packs else 0,
                        key=f"pack_{i}",
                    )
                    if chosen_pack == NEW_VALUE_SENTINEL:
                        chosen_pack = st.text_input(
                            "Nouveau cond.", value="",
                            key=f"pack_new_{i}", placeholder="Conteneur 7L…",
                        ).strip()
                    ls["packaging"] = chosen_pack

                # Row 2
                g1, g2, g3, g4 = st.columns(4)
                with g1:
                    ls["brand"] = st.text_input(
                        "Marque", value=ls.get("brand", ""), key=f"brand_{i}"
                    )
                with g2:
                    ls["material"] = st.text_input(
                        "Matériau", value=ls.get("material", ""), key=f"mat_{i}"
                    )
                with g3:
                    ls["unit_type"] = st.selectbox(
                        "Unité",
                        options=UNIT_TYPES,
                        index=UNIT_TYPES.index(ls["unit_type"]) if ls["unit_type"] in UNIT_TYPES else 0,
                        key=f"unit_{i}",
                    )
                with g4:
                    ls["cost_ht"] = st.number_input(
                        "Coût HT (€) / U *",
                        min_value=0.0,
                        value=float(ls["cost_ht"] or 0.0),
                        step=0.01, format="%.2f",
                        key=f"cost_{i}",
                    )

                # Row 3: labor norm + matched product
                h1, h2 = st.columns(2)
                with h1:
                    suggested_id = labor_by_name.get(ls.get("suggested_labor_task") or "", {}).get("id")
                    if suggested_id is None:
                        fb = labor_by_name.get("Norme par défaut (à classifier)")
                        suggested_id = fb["id"] if fb else (labor_norms[0]["id"] if labor_norms else None)
                    ln_idx = labor_options.index(suggested_id) if suggested_id in labor_options else 0
                    ls["labor_norm_id"] = st.selectbox(
                        "Norme de pose",
                        options=labor_options,
                        index=ln_idx,
                        format_func=lambda o: "—" if o is None else next(
                            (ln["task_name"] for ln in labor_norms if ln["id"] == o), str(o)
                        ),
                        key=f"labor_{i}",
                    )
                with h2:
                    matched_pid = ls.get("matched_product_id")
                    if isinstance(matched_pid, int) and matched_pid in product_options:
                        default_pidx = product_options.index(matched_pid)
                    else:
                        default_pidx = product_options.index("create_new")
                    chosen_product = st.selectbox(
                        "Produit existant à mettre à jour",
                        options=product_options,
                        index=default_pidx,
                        format_func=_format_product,
                        key=f"prod_{i}",
                    )
                    ls["chosen_product"] = chosen_product

                # Attributes (collapsed by default)
                with st.expander("Attributs", expanded=False):
                    import pandas as _pd
                    attrs = ls.get("attributes") or {}
                    attr_df = _pd.DataFrame(
                        [{"clé": k, "valeur": v} for k, v in attrs.items()]
                        or [{"clé": "", "valeur": ""}]
                    )
                    edited = st.data_editor(
                        attr_df,
                        num_rows="dynamic",
                        use_container_width=True,
                        key=f"attr_edit_{i}",
                    )
                    new_attrs: dict[str, str] = {}
                    for _, r in edited.iterrows():
                        k = (str(r.get("clé") or "")).strip()
                        v = (str(r.get("valeur") or "")).strip()
                        if k:
                            new_attrs[k] = v
                    ls["attributes"] = new_attrs

                # Bottom action row
                st.markdown(
                    '<div style="border-top:1px solid var(--hf-border-soft);margin-top:10px;padding-top:8px"></div>',
                    unsafe_allow_html=True,
                )

                # Compute "Approuver + N similaires" target count BEFORE buttons.
                all_texts = [(l.get("designation_raw") or "") for l in line_items_raw]
                target_text = li.get("designation_raw") or ""
                similar_idxs = (
                    find_similar_lines(target_text, all_texts, exclude_self_index=i)
                    if target_text else []
                )
                n_sim = len(similar_idxs)
                triplet_ok = (
                    ls.get("family_id") is not None
                    and bool((ls.get("subcategory") or "").strip())
                    and bool((ls.get("packaging") or "").strip())
                )
                approve_ready = triplet_ok and float(ls.get("cost_ht") or 0) > 0

                a1, a2, a3, a4, a5, a6 = st.columns([0.6, 0.6, 0.95, 0.95, 1.0, 1.6])
                with a1:
                    if st.button("↑", key=f"prev_{i}", use_container_width=True, disabled=(i == 0)):
                        S["active_line_idx"] = max(0, i - 1)
                        st.rerun()
                with a2:
                    if st.button("↓", key=f"next_{i}", use_container_width=True, disabled=(i == n_lines - 1)):
                        S["active_line_idx"] = min(n_lines - 1, i + 1)
                        st.rerun()
                with a3:
                    if st.button("⛔ rejeter", key=f"rej_{i}", use_container_width=True):
                        ls["status"] = "Rejeter"
                        if i < n_lines - 1:
                            S["active_line_idx"] = i + 1
                        st.rerun()
                with a4:
                    if st.button("🟡 à décider", key=f"undec_{i}", use_container_width=True):
                        ls["status"] = "À décider"
                        st.rerun()
                with a5:
                    if st.button(
                        "✓ approuver",
                        key=f"appr_{i}",
                        use_container_width=True,
                        type="primary",
                        disabled=not approve_ready,
                    ):
                        ls["status"] = "Approuver"
                        if i < n_lines - 1:
                            S["active_line_idx"] = i + 1
                        st.rerun()
                with a6:
                    sim_label = f"✓ approuver + {n_sim} similaires"
                    if st.button(
                        sim_label,
                        key=f"appr_sim_{i}",
                        use_container_width=True,
                        type="primary",
                        disabled=(not approve_ready) or n_sim == 0,
                    ):
                        ls["status"] = "Approuver"
                        for j in similar_idxs:
                            sib = lines_state[j]
                            sib["family_id"] = ls["family_id"]
                            sib["subcategory"] = ls["subcategory"]
                            sib["packaging"] = ls["packaging"]
                            sib["status"] = "Approuver"
                        st.toast(
                            f"✓ {n_sim + 1} lignes approuvées avec le même triplet",
                            icon="🌿",
                        )
                        if i < n_lines - 1:
                            S["active_line_idx"] = i + 1
                        st.rerun()

    # ===== COMMIT (triggered by top-bar button) =====
    if S.pop("ing_commit_requested", False):
        try:
            with transaction(ingestion_source=INGESTION_SOURCE) as conn:
                # 1. Resolve / create supplier
                if S["ing_supplier_choice"] == "create_new":
                    sup_payload = sup if sup else {}
                    sup_name_final = (sup_payload.get("name") or "").strip() or "Fournisseur sans nom"
                    res = conn.execute(
                        text(
                            """
                            INSERT INTO suppliers
                                (name, contact_email, contact_phone, address, notes)
                            VALUES
                                (:name, :email, :phone, :addr, :notes)
                            ON CONFLICT (name) DO UPDATE
                              SET contact_email = COALESCE(EXCLUDED.contact_email, suppliers.contact_email),
                                  contact_phone = COALESCE(EXCLUDED.contact_phone, suppliers.contact_phone),
                                  address       = COALESCE(EXCLUDED.address, suppliers.address)
                            RETURNING id
                            """
                        ),
                        {
                            "name": sup_name_final,
                            "email": (sup_payload.get("contact_email") or None),
                            "phone": (sup_payload.get("contact_phone") or None),
                            "addr": (sup_payload.get("address") or None),
                            "notes": "Créé automatiquement lors de l'ingestion d'une facture",
                        },
                    ).first()
                    supplier_id = int(res[0])
                else:
                    supplier_id = int(S["ing_supplier_choice"])

                # 2. Per line — upsert + queue transition
                created = updated = rejected = 0
                for i, (ls, qid) in enumerate(zip(lines_state, queue_ids)):
                    status = ls.get("status") or "À décider"
                    if status == "Rejeter":
                        conn.execute(
                            text(
                                """
                                UPDATE ingestion_queue SET
                                    status='rejected', reviewed_at=now(), reviewed_by=:by
                                WHERE id=:id
                                """
                            ),
                            {"id": qid, "by": os.environ.get("STREAMLIT_AUTH_USER", "system")},
                        )
                        rejected += 1
                        continue
                    if status != "Approuver":
                        continue
                    if float(ls.get("cost_ht") or 0) <= 0:
                        continue

                    attrs_json = json.dumps(ls.get("attributes") or {}, ensure_ascii=False)
                    common = {
                        "ref": ls["reference_name"].strip(),
                        "family_id": ls.get("family_id"),
                        "subcategory": (ls.get("subcategory") or "").strip(),
                        "supplier_id": supplier_id,
                        "labor_norm_id": ls.get("labor_norm_id"),
                        "brand": ls.get("brand") or None,
                        "material": ls.get("material") or None,
                        "packaging": ls["packaging"].strip(),
                        "unit": ls["unit_type"],
                        "cost": float(ls["cost_ht"]),
                        "attrs": attrs_json,
                        "notes": None,
                    }
                    _ensure_taxonomy_row(
                        conn, common["family_id"], common["subcategory"], common["packaging"]
                    )

                    chosen = ls.get("chosen_product")
                    if isinstance(chosen, int):
                        conn.execute(
                            text(
                                """
                                UPDATE products SET
                                    cost_ht = :cost,
                                    family_id = :family_id,
                                    subcategory = :subcategory,
                                    supplier_id = :supplier_id,
                                    labor_norm_id = :labor_norm_id,
                                    brand = COALESCE(:brand, brand),
                                    material = COALESCE(:material, material),
                                    packaging = :packaging,
                                    unit_type = :unit,
                                    attributes = attributes || CAST(:attrs AS jsonb)
                                WHERE id = :id
                                """
                            ),
                            {**common, "id": chosen},
                        )
                        product_id = chosen
                        updated += 1
                    else:
                        res = conn.execute(
                            text(
                                """
                                INSERT INTO products
                                    (reference_name, family_id, subcategory, supplier_id,
                                     labor_norm_id, brand, material, packaging, unit_type,
                                     cost_ht, attributes, notes)
                                VALUES
                                    (:ref, :family_id, :subcategory, :supplier_id,
                                     :labor_norm_id, :brand, :material, :packaging, :unit,
                                     :cost, CAST(:attrs AS jsonb), :notes)
                                ON CONFLICT (reference_name, packaging, supplier_id) DO UPDATE
                                  SET cost_ht       = EXCLUDED.cost_ht,
                                      family_id     = EXCLUDED.family_id,
                                      subcategory   = EXCLUDED.subcategory,
                                      labor_norm_id = EXCLUDED.labor_norm_id,
                                      brand         = COALESCE(EXCLUDED.brand, products.brand),
                                      material      = COALESCE(EXCLUDED.material, products.material),
                                      unit_type     = EXCLUDED.unit_type,
                                      attributes    = products.attributes || EXCLUDED.attributes
                                RETURNING id, (xmax = 0) AS is_insert
                                """
                            ),
                            common,
                        ).first()
                        product_id = int(res[0])
                        if res[1]:
                            conn.execute(
                                text(
                                    """
                                    INSERT INTO price_history
                                        (product_id, cost_ht, source, source_reference, recorded_by)
                                    VALUES
                                        (:pid, :cost, :src, :ref, :by)
                                    """
                                ),
                                {
                                    "pid": product_id,
                                    "cost": float(ls["cost_ht"]),
                                    "src": INGESTION_SOURCE,
                                    "ref": Path(S["ing_file_path"]).name if S.get("ing_file_path") else None,
                                    "by": os.environ.get("STREAMLIT_AUTH_USER", "system"),
                                },
                            )
                            created += 1
                        else:
                            updated += 1

                    conn.execute(
                        text(
                            """
                            UPDATE ingestion_queue SET
                                status='approved',
                                reviewed_at=now(),
                                reviewed_by=:by,
                                matched_product_id=:pid,
                                candidate_supplier_id=:sup,
                                candidate_labor_norm_id=:ln
                            WHERE id=:id
                            """
                        ),
                        {
                            "id": qid,
                            "by": os.environ.get("STREAMLIT_AUTH_USER", "system"),
                            "pid": product_id,
                            "sup": supplier_id,
                            "ln": ls.get("labor_norm_id"),
                        },
                    )

            st.success(
                f"✓ {created} produit(s) créé(s), {updated} mis à jour, {rejected} ligne(s) rejetée(s). "
                "→ Voir la page **Produits**."
            )
            _reset()
            st.balloons()
        except Exception as exc:
            log.exception("Commit failed")
            st.error(f"Échec du commit : {exc}")


render_footer()
