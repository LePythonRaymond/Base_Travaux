"""Paramètres — app-wide defaults (app_settings KV).

  • Réglages — the coefficient / matcher / LLM / API key-value defaults.

The "Pilotage de rentabilité" project history used to live here; it moved to
the **Retour DPGF** page, where it reads naturally as the history of everything
that page has ingested.
"""

from __future__ import annotations

import streamlit as st

from lib.auth import require_login
from lib.branding import (
    apply_branding,
    render_footer,
    render_header,
    render_sidebar_brand,
)
from lib.db import execute, fetch_all

st.set_page_config(page_title="Paramètres — Merci Raymond", page_icon="🌳", layout="wide")
require_login()
apply_branding()
render_sidebar_brand()
render_header(
    title="Paramètres",
    subtitle="réglages de l'application",
    breadcrumb="Paramètres",
)


# ============================================================================
#  Réglages (app_settings KV)
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
