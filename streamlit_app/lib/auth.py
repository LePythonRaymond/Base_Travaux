"""Streamlit auth gate — hi-fi v2 (split-screen login).

Caddy already enforces basic auth at the proxy level using the same bcrypt
hash; this is a UX nicety so the URL bar shows a session-aware login UI
instead of a raw 401 prompt.

Design: 55/45 split. Left pane is a full-bleed paysagisme photo with a
brand overlay (wordmark top, tagline bottom). Right pane is a quiet login
card with title, sub, password input, button, helper, and a live meta
footer (version + catalog size + a weather wink).

Fallback: if the photo fails to load, the left pane falls back to a
forest-green gradient. Login still works visually.
"""

from __future__ import annotations

import os

import bcrypt
import streamlit as st

from .branding import apply_branding

_AUTH_USER_ENV = "STREAMLIT_AUTH_USER"
_AUTH_HASH_ENV = "STREAMLIT_AUTH_PASSWORD_HASH"

# Hero photo. Defaults to a free-to-use Unsplash shot of a green-roof (urban
# garden, Paris-feeling). Swap with a real Merci Raymond chantier image
# by changing this URL — or by serving `/static/hero.jpg` from the
# Streamlit app and pointing at it.
HERO_PHOTO_URL = (
    "https://images.unsplash.com/photo-1416879595882-3373a0480b5b"
    "?w=1600&q=80&auto=format&fit=crop"
)
HERO_PHOTO_CREDIT = "photo · Federico Respini · Unsplash"


def _check_password(password: str) -> bool:
    expected_hash = os.environ.get(_AUTH_HASH_ENV, "")
    if not expected_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), expected_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _catalog_count() -> str:
    """Live catalog count for the footer meta strip. Returns '—' on DB error."""
    try:
        from .db import fetch_one  # late import to avoid circulars

        row = fetch_one("SELECT count(*) AS c FROM products WHERE is_active")
        return f"{int(row['c']):,}".replace(",", " ") if row else "—"
    except Exception:  # noqa: BLE001 — DB might be down at login time
        return "—"


def require_login() -> None:
    """Render the split-screen login if the session isn't authed; halt otherwise."""
    if st.session_state.get("authed"):
        return

    apply_branding()

    # Login-only chrome overrides. We can't scope by parent class because
    # st.markdown doesn't wrap subsequent widgets — instead we target
    # Streamlit's data-testids directly and trust that on the login render
    # there's nothing else on the page that would be miscaught.
    st.markdown(
        f"""
        <style>
        /* ── kill all Streamlit chrome + gutters ─────────────────── */
        [data-testid="stSidebar"], [data-testid="stSidebarNav"],
        [data-testid="stHeader"], [data-testid="stToolbar"] {{
            display: none !important;
        }}
        [data-testid="stAppViewContainer"] > .main,
        [data-testid="stMain"] {{
            padding: 0 !important;
        }}
        [data-testid="stMainBlockContainer"],
        .main .block-container {{
            padding: 0 !important;
            max-width: none !important;
        }}
        /* zero the column gap so the two panes touch */
        [data-testid="stHorizontalBlock"] {{
            gap: 0 !important;
        }}
        [data-testid="stColumn"] {{
            padding: 0 !important;
        }}

        /* ── HERO (left column) ──────────────────────────────────── */
        .mr-login-hero {{
            position: relative;
            height: 100vh;
            min-height: 640px;
            background-image:
              linear-gradient(180deg,
                rgba(15,28,20,0.30) 0%,
                rgba(15,28,20,0.10) 38%,
                rgba(15,28,20,0.70) 100%
              ),
              url("{HERO_PHOTO_URL}");
            background-color: #1d3a2a;
            background-size: cover;
            background-position: center;
            color: #faf5e6;
            margin: 0;
            overflow: hidden;
        }}
        .mr-login-hero-inner {{
            position: absolute;
            inset: 40px;
            display: flex;
            flex-direction: column;
            justify-content: space-between;
            font-family: 'Inter', system-ui, sans-serif;
        }}
        .mr-login-mark {{
            display: inline-flex;
            align-items: center;
            gap: 10px;
            font-size: 17px;
            font-weight: 600;
            color: #faf5e6;
            letter-spacing: -0.005em;
            width: fit-content;
        }}
        .mr-login-mark .leaf {{
            display: inline-flex;
            width: 32px;
            height: 32px;
            border-radius: 50%;
            border: 1px solid rgba(250,245,230,0.45);
            background: rgba(250,245,230,0.16);
            justify-content: center;
            align-items: center;
            font-size: 16px;
            backdrop-filter: blur(4px);
        }}
        .mr-login-tagline-sm {{
            font-size: 11px;
            letter-spacing: 0.18em;
            text-transform: uppercase;
            color: rgba(250,245,230,0.72);
            font-weight: 500;
        }}
        .mr-login-tagline-big {{
            font-size: 46px;
            font-weight: 500;
            letter-spacing: -0.025em;
            line-height: 1.02;
            margin-top: 12px;
            color: #faf5e6;
            text-shadow: 0 4px 14px rgba(0,0,0,0.35);
        }}
        .mr-login-credit {{
            position: absolute;
            bottom: 28px;
            right: 28px;
            font-family: 'JetBrains Mono', monospace;
            font-size: 10px;
            color: rgba(250,245,230,0.55);
        }}

        /* ── FORM PANE (right column = nth-of-type 2) ────────────── */
        /* Make the column itself 100vh tall + vertical-center its block.
           Generous right/bottom padding so the meta footer doesn't kiss
           the column edge. */
        [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-of-type(2) {{
            min-height: 100vh;
            display: flex !important;
            align-items: center !important;
            justify-content: center !important;
            padding: 60px 56px 60px 48px !important;
        }}
        [data-testid="stHorizontalBlock"] > [data-testid="stColumn"]:nth-of-type(2) > [data-testid="stVerticalBlock"] {{
            width: 100%;
            max-width: 360px;
            gap: 0 !important;
        }}

        /* Kill st.form's default border/padding/background. Global rule —
           we're on the login render only, no other forms on this page. */
        [data-testid="stForm"] {{
            border: none !important;
            padding: 0 !important;
            background: transparent !important;
            box-shadow: none !important;
        }}

        /* Form widget label ("Mot de passe") restyle */
        [data-testid="stForm"] [data-testid="stWidgetLabel"] p {{
            font-size: 11px !important;
            letter-spacing: 0.04em;
            text-transform: uppercase;
            font-weight: 600 !important;
            color: var(--hf-muted) !important;
        }}

        .mr-login-meta {{
            font-size: 11px;
            letter-spacing: 0.12em;
            text-transform: uppercase;
            color: var(--hf-muted);
            margin-bottom: 28px;
        }}
        .mr-login-title {{
            font-size: 38px !important;
            font-weight: 500 !important;
            letter-spacing: -0.025em !important;
            color: var(--hf-ink) !important;
            margin: 0 0 12px 0 !important;
            line-height: 1.05 !important;
        }}
        .mr-login-sub {{
            font-size: 14px;
            line-height: 1.55;
            color: var(--hf-muted);
            margin: 0 0 24px 0;
        }}
        .mr-login-help {{
            font-size: 12px;
            color: var(--hf-muted);
            margin-top: 14px;
            line-height: 1.5;
        }}
        .mr-login-help a {{
            color: var(--hf-green);
            text-decoration: underline;
            text-underline-offset: 2px;
            text-decoration-thickness: 1px;
        }}
        .mr-login-footer {{
            margin-top: 36px;
            padding: 18px 4px 4px 4px;
            border-top: 1px solid var(--hf-border-soft);
            font-size: 11px;
            color: var(--hf-muted);
            font-variant-numeric: tabular-nums;
            line-height: 1.6;
        }}
        .mr-login-footer .sep {{ color: var(--hf-soft); padding: 0 6px; }}
        </style>
        """,
        unsafe_allow_html=True,
    )

    col_hero, col_form = st.columns([0.55, 0.45], gap="small")

    # ────────── LEFT: hero ──────────
    with col_hero:
        st.markdown(
            f"""
            <div class="mr-login-hero">
              <div class="mr-login-hero-inner">
                <div class="mr-login-mark">
                  <span class="leaf">❦</span>
                  <span>merci raymond</span>
                </div>
                <div>
                  <div class="mr-login-tagline-sm">depuis 2014</div>
                  <div class="mr-login-tagline-big">Reconnectons<br>les citadins<br>à la nature.</div>
                </div>
              </div>
              <div class="mr-login-credit">{HERO_PHOTO_CREDIT}</div>
            </div>
            """,
            unsafe_allow_html=True,
        )

    # ────────── RIGHT: login form ──────────
    # No wrapping divs here — st.markdown does NOT wrap subsequent widgets,
    # so all layout/centering is done in CSS via the stColumn:nth-of-type(2)
    # selector above.
    with col_form:
        st.markdown(
            '<div class="mr-login-meta">Paris · Base de prix interne</div>',
            unsafe_allow_html=True,
        )
        st.markdown('<h1 class="mr-login-title">Bienvenue.</h1>', unsafe_allow_html=True)
        st.markdown(
            '<p class="mr-login-sub">Espace sécurisé. Entrez votre mot de passe '
            "pour accéder à la base de prix.</p>",
            unsafe_allow_html=True,
        )

        with st.form("login_form", clear_on_submit=False):
            password = st.text_input(
                "Mot de passe",
                type="password",
                placeholder="•••••••••••",
                autocomplete="current-password",
            )
            submitted = st.form_submit_button(
                "Entrer", type="primary", use_container_width=True
            )

        if submitted:
            if _check_password(password):
                st.session_state["authed"] = True
                st.session_state["user"] = os.environ.get(_AUTH_USER_ENV, "admin")
                st.rerun()
            else:
                st.error("Mot de passe invalide.")

        st.markdown(
            '<div class="mr-login-help">↳ accès oublié ? écrivez à '
            '<a href="mailto:taddeo.carpinelli@merciraymond.fr">'
            "taddeo.carpinelli@merciraymond.fr</a></div>",
            unsafe_allow_html=True,
        )

        # Live footer meta strip.
        n_products = _catalog_count()
        st.markdown(
            f'<div class="mr-login-footer">'
            f'<span>v0.3.1</span>'
            f'<span class="sep">·</span>'
            f'<span>catalogue · {n_products} produits</span>'
            f'<span class="sep">·</span>'
            f'<span>Paris · 19 °C</span>'
            f"</div>",
            unsafe_allow_html=True,
        )

    st.stop()
