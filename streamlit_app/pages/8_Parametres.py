"""Paramètres — app-wide defaults (app_settings KV) + Pilotage de rentabilité.

Two sections:
  • Pilotage de rentabilité — every re-ingested DPGF kept in `dpgf_projects`:
    a KPI strip (nb projets · prix de vente cumulé · marge moyenne · KV moyen)
    and per-project cards (Prix vente / Prix de revient / Marge € + % / KV,
    the Hors-SST variant when captured, and a download of the stored .xlsx).
    Where the computed numbers and the captured recap block diverge, a small
    "≠ recap" flag surfaces the cross-check the user asked for.
  • Réglages — the coefficient / matcher / LLM / API key-value defaults.
"""

from __future__ import annotations

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
from lib.db import execute, fetch_all, fetch_one

st.set_page_config(page_title="Paramètres — Merci Raymond", page_icon="🌳", layout="wide")
require_login()
apply_branding()
render_sidebar_brand()
render_header(
    title="Paramètres",
    subtitle="pilotage de rentabilité · réglages",
    breadcrumb="Paramètres",
)


# ============================================================================
#  Helpers
# ============================================================================
def _fmt_money(v, decimals: int = 0) -> str:
    try:
        return f"{float(v):,.{decimals}f} €".replace(",", " ")
    except (TypeError, ValueError):
        return "—"


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


@st.cache_data(show_spinner=False)
def _project_xlsx(pid: int) -> bytes | None:
    """Lazily load (and cache) the stored .xlsx for one project."""
    row = fetch_one("SELECT file_bytes FROM dpgf_projects WHERE id = :id", {"id": pid})
    if not row or row["file_bytes"] is None:
        return None
    return bytes(row["file_bytes"])


_XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


# ============================================================================
#  Section 1 — Pilotage de rentabilité
# ============================================================================
st.markdown('<h2 class="hf-h2" style="margin:6px 0 2px 0">Pilotage de rentabilité</h2>', unsafe_allow_html=True)
st.markdown(
    '<p class="hf-muted" style="font-size:12.5px;margin:0 0 12px 0;max-width:780px">'
    "Chaque DPGF re-ingéré (signé) est conservé ici — le fichier et sa rentabilité. "
    "Les chiffres viennent de la <b>feuille « Pilotage de rentabilité » du DPGF</b> "
    "(source de vérité — c'est là que Vincent gère prix, coefficients et marges) ; "
    "le calcul interne de l'app ne sert que de contre-vérification "
    "(badge <b>≠ calcul</b> en cas d'écart). Les imports plus anciens, sans bloc "
    "rentabilité, retombent sur le calcul interne (badge <i>calculé</i>).</p>",
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
        "Aucun projet ingéré pour l'instant. Re-ingère un DPGF signé depuis la page "
        "<b>Retour DPGF</b> — il apparaîtra ici avec ses statistiques de rentabilité "
        "et le fichier téléchargeable.</div></div>",
        unsafe_allow_html=True,
    )
else:
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
                    f'<div style="font-weight:700;font-size:15px;color:var(--hf-accent)">'
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
                        key=f"dl_{p['id']}",
                        use_container_width=True,
                        help=f"{size_kb} · fichier d'origine conservé",
                    )
                else:
                    st.markdown(
                        '<span class="hf-muted" style="font-size:10px">fichier absent</span>',
                        unsafe_allow_html=True,
                    )

st.markdown('<div style="height:18px"></div>', unsafe_allow_html=True)


# ============================================================================
#  Section 2 — Réglages (app_settings KV)
# ============================================================================
st.markdown('<h2 class="hf-h2" style="margin:6px 0 2px 0">Réglages</h2>', unsafe_allow_html=True)
st.markdown(
    '<p class="hf-muted" style="font-size:12.5px;margin:0 0 12px 0;max-width:760px">'
    "Coefficients par défaut, seuils du matcher, modèle LLM. Ces valeurs servent de "
    "<b>défauts</b> ; les coefficients réellement appliqués à un chiffrage vivent dans "
    "l'onglet Paramètres du DPGF.</p>",
    unsafe_allow_html=True,
)

GROUPS: dict[str, list[str]] = {
    "Coefficients DPGF (par défaut)": [
        "default_hourly_rate",
        "default_safety_margin",
        "default_install_chantier",
        "default_log_gestion",
        "default_loc_livr_margin",
        "default_humain_margin",
        "default_fourn_gest_margin",
    ],
    "Matcher d'ingestion": [
        "matching_threshold_high",
        "matching_threshold_low",
    ],
    "LLM": [
        "llm_provider",
        "llm_model",
    ],
    "API Bordereau": [
        "bordereau_endpoint_path",
    ],
}

settings = {row["key"]: row for row in fetch_all("SELECT key, value, notes FROM app_settings ORDER BY key")}
known = {k for keys in GROUPS.values() for k in keys}
extras = [k for k in settings.keys() if k not in known]
if extras:
    GROUPS["Autres"] = extras

for group_label, keys in GROUPS.items():
    present_keys = [k for k in keys if settings.get(k)]
    if not present_keys:
        continue
    with st.container(border=True):
        st.markdown(
            f'<h3 class="hf-h3" style="margin:0 0 8px 0;font-size:13.5px">{group_label}</h3>',
            unsafe_allow_html=True,
        )
        with st.form(f"settings_form_{group_label}"):
            edited: dict[str, str] = {}
            for key in present_keys:
                row = settings.get(key)
                edited[key] = st.text_input(
                    key,
                    value=row["value"],
                    help=row.get("notes") or None,
                )
            if st.form_submit_button("Enregistrer ce groupe", use_container_width=True):
                changes = 0
                for key, new_value in edited.items():
                    if settings[key]["value"] != new_value:
                        execute(
                            "UPDATE app_settings SET value = :v, updated_at = now() WHERE key = :k",
                            {"k": key, "v": new_value},
                        )
                        changes += 1
                if changes:
                    st.success(f"{changes} paramètre(s) mis à jour.")
                    st.rerun()
                else:
                    st.info("Aucun changement à enregistrer.")
    st.markdown('<div style="height:8px"></div>', unsafe_allow_html=True)

render_footer()
