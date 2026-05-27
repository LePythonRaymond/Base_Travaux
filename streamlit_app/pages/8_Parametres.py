"""App-wide settings editor (key-value rows in app_settings)."""

from __future__ import annotations

import streamlit as st

from lib.auth import require_login
from lib.branding import apply_branding, render_footer, render_header, render_sidebar_brand
from lib.db import execute, fetch_all

st.set_page_config(page_title="Paramètres — Merci Raymond", page_icon="🌳", layout="wide")
require_login()
apply_branding()
render_sidebar_brand()
render_header(subtitle="Paramètres")

st.title("Paramètres")
st.caption(
    "Coefficients par défaut, seuils du matcher, modèle LLM. "
    "Ces valeurs servent de défauts ; les coefficients par projet vivent dans le DPGF."
)

# Group settings by intent for easier scanning.
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
    st.subheader(group_label)
    with st.form(f"settings_form_{group_label}"):
        edited: dict[str, str] = {}
        for key in keys:
            row = settings.get(key)
            if not row:
                continue
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
    st.divider()

render_footer()
