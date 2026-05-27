"""Branding for the Streamlit admin UI — hi-fi design system v2.

Translated from the Claude Design `styles-hifi.css` package, adapted to
override Streamlit's default chrome. The visual language: warm paper
background, deep forest-green sidebar, Inter for body, JetBrains Mono for
tabular numbers, compact 13 px base size, all-caps short uppercase labels.

Public API (used by the pages):

  apply_branding()                          → CSS injection. Once per page.
  render_header(title, sub=None,
                breadcrumb=None, right=None)→ hi-fi H1 row (no longer serif).
  render_footer()                           → small footer line.
  render_sidebar_brand()                    → top-of-sidebar brand mark.
  page(...) ↦ context manager               → apply_branding + header + footer.

  hf_chip(label, kind="")                   → HTML string for inline use.
  hf_dot(state="ok")                        → HTML string (status dot).
  hf_kpi(label, value, ...)                 → renders one KPI tile.
  hf_card(kind="")                          → context manager wrapping a card.
  hf_stepper(steps, current_idx)            → wizard step indicator.

  StepTracker(...)                          → legacy progress-square loader,
                                              still used by Ingestion facture.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator

import streamlit as st


# ---------------------------------------------------------------------------
# Design tokens — verbatim from styles-hifi.css
# ---------------------------------------------------------------------------
PAPER       = "#f6efdd"
PAPER_2     = "#efe8d4"
CREAM       = "#fbf7eb"
WHITE       = "#ffffff"
INK         = "#111613"
BODY        = "#2a2e2c"
MUTED       = "#6e7068"
SOFT        = "#a9a896"
BORDER      = "#d8d1b8"
BORDER_SOFT = "#e7e0c8"
HOVER       = "#ece5cf"

GREEN       = "#1d3a2a"
GREEN_2     = "#244a36"
LEAF        = "#3a7d52"
LEAF_SOFT   = "#d7e8dd"
AMBER       = "#b58a2a"
AMBER_SOFT  = "#f3e6c2"
TERRA       = "#a14b30"
TERRA_SOFT  = "#ecd0c5"
SKY         = "#3a5d7d"


_CSS = r"""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap');

/* ────────────────────────────────────────────────────────────────────────
   hi-fi root tokens
   ──────────────────────────────────────────────────────────────────────── */
:root {
  --hf-paper:        #f6efdd;
  --hf-paper-2:      #efe8d4;
  --hf-cream:        #fbf7eb;
  --hf-white:        #ffffff;
  --hf-ink:          #111613;
  --hf-body:         #2a2e2c;
  --hf-muted:        #6e7068;
  --hf-soft:         #a9a896;
  --hf-border:       #d8d1b8;
  --hf-border-soft:  #e7e0c8;
  --hf-hover:        #ece5cf;

  --hf-green:        #1d3a2a;
  --hf-green-2:      #244a36;
  --hf-leaf:         #3a7d52;
  --hf-leaf-soft:    #d7e8dd;
  --hf-amber:        #b58a2a;
  --hf-amber-soft:   #f3e6c2;
  --hf-terra:        #a14b30;
  --hf-terra-soft:   #ecd0c5;
  --hf-sky:          #3a5d7d;

  --hf-radius:       6px;
  --hf-radius-sm:    4px;
  --hf-radius-lg:    10px;
  --hf-shadow:       0 1px 0 rgba(17,22,19,0.04), 0 2px 6px rgba(17,22,19,0.06);
}

/* ────────────────────────────────────────────────────────────────────────
   hide Streamlit chrome
   ──────────────────────────────────────────────────────────────────────── */
#MainMenu, footer, [data-testid="stToolbar"], [data-testid="stDecoration"] {
    visibility: hidden !important;
    height: 0 !important;
}
header[data-testid="stHeader"] {
    background: transparent;
    height: 0;
}
[data-testid="stStatusWidget"] { display: none !important; }

/* ────────────────────────────────────────────────────────────────────────
   global typography + page background
   ──────────────────────────────────────────────────────────────────────── */
html, body, .stApp, .main, .block-container {
    font-family: 'Inter', system-ui, -apple-system, sans-serif !important;
    color: var(--hf-body);
    font-size: 13px;
    line-height: 1.45;
    font-feature-settings: 'cv11', 'ss01';
    font-variant-numeric: tabular-nums;
}
.stApp { background: var(--hf-paper) !important; }
.main, .block-container { background: var(--hf-paper) !important; }

h1, h2, h3, h4, h5, h6 {
    font-family: 'Inter', system-ui, sans-serif !important;
    color: var(--hf-ink) !important;
    letter-spacing: -0.012em;
    font-weight: 600 !important;
}
h1 { font-size: 22px !important; margin: 0 0 6px 0 !important; }
h2 { font-size: 13px !important; text-transform: uppercase; letter-spacing: 0.06em; color: var(--hf-muted) !important; font-weight: 600 !important; }
h3 { font-size: 15px !important; font-weight: 600 !important; }

p, label, span, div {
    font-family: 'Inter', system-ui, sans-serif !important;
}

code, pre { font-family: 'JetBrains Mono', monospace !important; font-size: 11.5px; }

.block-container {
    padding-top: 1.8rem !important;
    padding-bottom: 1rem !important;
    max-width: 1400px;
}

/* ────────────────────────────────────────────────────────────────────────
   sidebar — dark green w/ brand mark on top
   ──────────────────────────────────────────────────────────────────────── */
[data-testid="stSidebar"] {
    background: var(--hf-green) !important;
    border-right: 1px solid rgba(255,255,255,0.05) !important;
}
[data-testid="stSidebar"] > div:first-child { padding-top: 8px !important; }
[data-testid="stSidebar"] [data-testid="stSidebarNav"] { padding-top: 0 !important; }
[data-testid="stSidebarNav"] li a {
    color: #d6dccb !important;
    border-radius: 5px;
    padding: 7px 10px !important;
    font-size: 12.5px !important;
    margin: 0 6px;
}
[data-testid="stSidebarNav"] li a span {
    font-family: 'Inter', sans-serif !important;
    font-size: 12.5px !important;
    letter-spacing: 0 !important;
    font-weight: 500;
    color: #d6dccb !important;
}
[data-testid="stSidebarNav"] li a:hover { background: rgba(255,255,255,0.04) !important; }
[data-testid="stSidebarNav"] li a[aria-current="page"] {
    background: #faf5e6 !important;
    color: var(--hf-green) !important;
}
[data-testid="stSidebarNav"] li a[aria-current="page"] span { color: var(--hf-green) !important; font-weight: 600; }

/* Paramètres is visible in the sidebar (was hidden in v1 — exposed in v2). */

/* sidebar brand block (rendered via render_sidebar_brand) */
.hf-sb-brand {
    display: flex; align-items: center; gap: 9px;
    padding: 6px 10px 14px;
    margin: 0 0 6px 0;
    border-bottom: 1px solid rgba(255,255,255,0.08);
}
.hf-sb-brand .mark {
    width: 28px; height: 28px;
    border: 1.5px solid #e8e3d2;
    border-radius: 50%;
    display: flex; align-items: center; justify-content: center;
    font-size: 15px; color: #e8e3d2;
    flex-shrink: 0;
}
.hf-sb-brand .name {
    font-weight: 600; font-size: 14px;
    letter-spacing: -0.005em;
    color: #faf5e6;
    line-height: 1.05;
}
.hf-sb-brand .name small {
    display: block; font-weight: 400; font-size: 9.5px;
    letter-spacing: 0.1em; text-transform: uppercase;
    color: #b7c5b5; margin-top: 2px;
}
.hf-sb-foot {
    margin: auto 8px 8px;
    padding: 10px 8px 0;
    font-size: 10px;
    color: #8aa088;
    border-top: 1px solid rgba(255,255,255,0.08);
    font-variant-numeric: tabular-nums;
    line-height: 1.5;
}

/* ────────────────────────────────────────────────────────────────────────
   buttons — flat ghost style by default, brand-green for primary
   ────────────────────────────────────────────────────────────────────────
   Non-primary buttons render with NO visible chrome at rest (transparent
   bg + transparent border) — they read as text labels with a cursor:pointer
   affordance. Hover/focus reveals a paper_2 bg + soft border so the user
   can see they're interactive. This kills the "box-in-box" effect that
   visible button chrome creates everywhere — both on pages and inside
   bordered cards.

   `white-space: nowrap` prevents Streamlit's per-character wrap when a
   button label sits in a narrow column. */
.stButton > button, .stDownloadButton > button, .stFormSubmitButton > button, .stLinkButton > a {
    background: transparent !important;
    color: var(--hf-ink) !important;
    border: 1px solid var(--hf-border-soft) !important;
    border-radius: var(--hf-radius-sm) !important;
    padding: 6px 14px !important;
    font-family: 'Inter', sans-serif !important;
    font-size: 12px !important;
    font-weight: 500 !important;
    letter-spacing: 0 !important;
    text-transform: none !important;
    box-shadow: none !important;
    transition: background 0.12s ease, border-color 0.12s ease, color 0.12s ease;
    min-height: 0 !important;
    line-height: 1.2 !important;
    white-space: nowrap !important;
}
.stButton > button:hover, .stDownloadButton > button:hover,
.stFormSubmitButton > button:hover, .stLinkButton > a:hover {
    background: var(--hf-paper-2) !important;
    border-color: var(--hf-border) !important;
    color: var(--hf-ink) !important;
}
.stButton > button:focus-visible, .stDownloadButton > button:focus-visible,
.stFormSubmitButton > button:focus-visible, .stLinkButton > a:focus-visible {
    outline: none !important;
    border-color: var(--hf-green) !important;
    box-shadow: 0 0 0 2px rgba(29,58,42,0.10) !important;
}
.stButton > button[kind="primary"],
.stFormSubmitButton > button[kind="primary"],
.stFormSubmitButton > button[kind="primaryFormSubmit"] {
    background: var(--hf-green) !important;
    color: #faf5e6 !important;
    border-color: var(--hf-green) !important;
}
.stButton > button[kind="primary"]:hover,
.stFormSubmitButton > button[kind="primary"]:hover,
.stFormSubmitButton > button[kind="primaryFormSubmit"]:hover {
    background: var(--hf-green-2) !important;
    border-color: var(--hf-green-2) !important;
    color: #faf5e6 !important;
}
.stButton > button:disabled, .stFormSubmitButton > button:disabled {
    background: transparent !important;
    color: var(--hf-soft) !important;
    border-color: transparent !important;
}

/* ────────────────────────────────────────────────────────────────────────
   inputs (text, number, select, multiselect, textarea, date)
   ──────────────────────────────────────────────────────────────────────── */
[data-baseweb="input"] > div,
[data-baseweb="textarea"] > div,
[data-baseweb="select"] > div,
[data-baseweb="popover"] > div,
.stNumberInput input,
.stDateInput input {
    background: var(--hf-white) !important;
    border-color: var(--hf-border) !important;
    border-radius: var(--hf-radius-sm) !important;
    font-size: 12px !important;
}
[data-baseweb="input"] input,
[data-baseweb="textarea"] textarea,
[data-baseweb="select"] {
    font-family: 'Inter', sans-serif !important;
    font-size: 12px !important;
    color: var(--hf-ink) !important;
}
[data-baseweb="input"]:focus-within > div,
[data-baseweb="select"]:focus-within > div {
    border-color: var(--hf-green) !important;
    box-shadow: 0 0 0 2px rgba(29,58,42,0.08) !important;
}

/* slider thumb + track */
[data-testid="stSlider"] [role="slider"] {
    background: var(--hf-green) !important;
    border-color: var(--hf-green) !important;
}
[data-testid="stSlider"] .stSlider > div > div > div {
    background: var(--hf-green-2) !important;
}

/* labels above inputs (Streamlit's stWidgetLabel) */
[data-testid="stWidgetLabel"] p {
    font-size: 10.5px !important;
    color: var(--hf-muted) !important;
    letter-spacing: 0.02em;
    font-weight: 500 !important;
    margin-bottom: 6px !important;
}

/* Hide Streamlit's auto-anchor link icons that appear next to h1/h2/h3
   tags rendered inside `st.markdown(..., unsafe_allow_html=True)`. They
   appear as a small chain icon to the right of section headings and add
   visual noise to the form. */
[data-testid="stMarkdownContainer"] a.anchor-link,
[data-testid="stHeaderActionElements"],
.stMarkdown a[href^="#"][aria-label] {
    display: none !important;
}

/* Give inputs/selects a consistent height so empty fields don't look
   like flat rectangles next to filled ones. Padding is on the inner
   baseweb container, which my "inside a card" rule above strips chrome
   from — but we still want a comfortable click target. */
[data-baseweb="input"] > div,
[data-baseweb="select"] > div {
    min-height: 34px !important;
}
[data-baseweb="input"] input,
[data-baseweb="select"] {
    padding: 6px 4px !important;
}

/* Streamlit columns: add a touch of horizontal gap so neighbour fields
   inside a card breathe a little (default is too tight at our font
   scale). Limited to columns INSIDE a bordered card so the catalogue
   filter row keeps its original layout. */
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-testid="stHorizontalBlock"] {
    gap: 18px !important;
}

/* And add vertical breathing room between consecutive rows of widgets
   inside the staged-form cards. */
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-testid="stVerticalBlock"] {
    gap: 12px !important;
}

/* ────────────────────────────────────────────────────────────────────────
   file uploader
   ──────────────────────────────────────────────────────────────────────── */
[data-testid="stFileUploader"] section {
    border: 1px dashed var(--hf-border) !important;
    border-radius: var(--hf-radius) !important;
    background: var(--hf-cream) !important;
    padding: 18px !important;
}
[data-testid="stFileUploader"] section button {
    background: var(--hf-white) !important;
    color: var(--hf-ink) !important;
    border: 1px solid var(--hf-border) !important;
}
[data-testid="stFileUploader"] section button:hover {
    background: var(--hf-green) !important;
    color: #faf5e6 !important;
    border-color: var(--hf-green) !important;
}

/* ────────────────────────────────────────────────────────────────────────
   containers, expanders, dataframes, alerts
   ──────────────────────────────────────────────────────────────────────── */
[data-testid="stExpander"] {
    border: 1px solid var(--hf-border-soft) !important;
    border-radius: var(--hf-radius) !important;
    background: var(--hf-cream) !important;
    box-shadow: var(--hf-shadow) !important;
}
[data-testid="stExpander"] summary {
    font-family: 'Inter', sans-serif !important;
    font-weight: 500 !important;
    font-size: 12.5px !important;
    color: var(--hf-ink) !important;
}

/* st.container(border=True) — our card primitive.

   GOTCHA: Streamlit wraps EVERY layout block in a div with
   data-testid="stVerticalBlockBorderWrapper", regardless of whether the
   user passed border=True. The only differentiator is the emotion-cache
   class: auto-wrappers get the empty `st-emotion-cache-0` placeholder
   class; real `border=True` containers get a different cache class that
   carries Streamlit's default 1px border. We scope our card styling to
   "wrappers that are NOT the empty-placeholder one" — robust across
   Streamlit upgrades because `st-emotion-cache-0` is Streamlit's stable
   identifier for "no styles applied". */
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) {
    background: var(--hf-cream) !important;
    border: 1px solid var(--hf-border-soft) !important;
    border-radius: var(--hf-radius) !important;
    /* Generous padding so the last element inside (a helper line, an
       empty-state caption, the last supplier row) doesn't kiss the card
       border. Extra weight on the bottom keeps the visual rhythm even
       when the last child has no margin-bottom of its own. */
    padding: 18px 20px 22px 20px !important;
    box-shadow: var(--hf-shadow);
}

/* Inside a REAL bordered card (not an auto-wrapper): re-assert primary
   button green identity so calls-to-action stay vivid against cream bg. */
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stButton > button[kind="primary"],
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stFormSubmitButton > button[kind="primary"],
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stFormSubmitButton > button[kind="primaryFormSubmit"] {
    background: var(--hf-green) !important;
    color: #faf5e6 !important;
    border-color: var(--hf-green) !important;
}
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stButton > button[kind="primary"]:hover,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stFormSubmitButton > button[kind="primary"]:hover,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stFormSubmitButton > button[kind="primaryFormSubmit"]:hover {
    background: var(--hf-green-2) !important;
    border-color: var(--hf-green-2) !important;
    color: #faf5e6 !important;
}

/* Text/number/select inputs INSIDE a real bordered card: drop the white
   (or auto-cream) fill, use a flat transparent bg with bottom hairline
   only. Kills the "filled-rectangle on cream card" nested-card feel.
   Focused state turns the bottom underline green.

   GOTCHA: Streamlit's text input has TWO wrapper divs that both carry
   bg styling: the outer `[data-testid="stTextInputRootElement"]` (which
   defaults to a slightly-darker cream rectangle) and the inner
   `[data-baseweb="input"] > div`. We have to flatten BOTH. Same dual
   wrapper applies to selectbox (stSelectboxRootElement) and number
   input (stNumberInputContainer + step buttons). */
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-testid="stTextInputRootElement"],
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-baseweb="input"],
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-baseweb="input"] > div,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-baseweb="select"] > div,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-baseweb="textarea"] > div,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-testid="stNumberInputContainer"],
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-testid="stNumberInputStepUp"],
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-testid="stNumberInputStepDown"],
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stNumberInput input,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stDateInput input {
    background: transparent !important;
    border-color: transparent !important;
    border-bottom: 1px solid var(--hf-border-soft) !important;
    border-radius: 0 !important;
    box-shadow: none !important;
}
/* Hover/focus underline turns soft green */
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-testid="stTextInputRootElement"]:focus-within,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-baseweb="input"]:focus-within > div,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-baseweb="select"]:focus-within > div,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-baseweb="textarea"]:focus-within > div {
    border-color: transparent !important;
    border-bottom-color: var(--hf-green) !important;
    box-shadow: none !important;
}

/* Non-primary buttons INSIDE a real bordered card: drop the visible
   outline at rest so they don't read as nested rectangles against the
   card. Hover reveals the soft border + paper_2 bg for affordance.
   Primary buttons (and primaryFormSubmit) keep their green chrome — the
   rule further above already re-asserts that. */
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stButton > button:not([kind="primary"]):not([kind="primaryFormSubmit"]),
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stDownloadButton > button,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stLinkButton > a {
    border-color: transparent !important;
}
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stButton > button:not([kind="primary"]):not([kind="primaryFormSubmit"]):hover,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stDownloadButton > button:hover,
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) .stLinkButton > a:hover {
    border-color: var(--hf-border-soft) !important;
    background: var(--hf-paper-2) !important;
}

/* Expanders inside a real bordered card: flat (transparent bg, no shadow). */
[data-testid="stVerticalBlockBorderWrapper"]:not(.st-emotion-cache-0) [data-testid="stExpander"] {
    background: transparent !important;
    border: 1px solid var(--hf-border-soft) !important;
    box-shadow: none !important;
}

[data-testid="stDataFrame"], [data-testid="stTable"] {
    border: 1px solid var(--hf-border-soft) !important;
    border-radius: var(--hf-radius) !important;
    font-size: 12px !important;
}
[data-testid="stDataFrame"] table { font-family: 'Inter', sans-serif !important; }
[data-testid="stDataFrame"] th {
    background: var(--hf-cream) !important;
    font-size: 10.5px !important;
    text-transform: uppercase;
    letter-spacing: 0.05em;
    color: var(--hf-muted) !important;
    font-weight: 600 !important;
}
[data-testid="stDataFrame"] td.num,
[data-testid="stDataFrame"] td[data-type="number"] {
    font-variant-numeric: tabular-nums;
    font-family: 'JetBrains Mono', monospace !important;
    font-size: 11.5px !important;
}

[data-testid="stAlert"] {
    border-radius: var(--hf-radius) !important;
    border: 1px solid var(--hf-border-soft) !important;
    background: var(--hf-cream) !important;
}
[data-testid="stAlert"][data-baseweb="notification"] { padding: 10px 14px !important; }

/* st.metric — convert to hi-fi KPI look */
[data-testid="stMetric"] {
    background: var(--hf-cream);
    border: 1px solid var(--hf-border-soft);
    border-radius: var(--hf-radius);
    padding: 12px 14px;
    box-shadow: var(--hf-shadow);
}
[data-testid="stMetricLabel"] p {
    font-size: 10.5px !important;
    color: var(--hf-muted) !important;
    letter-spacing: 0.04em;
    text-transform: uppercase;
    font-weight: 600 !important;
}
[data-testid="stMetricValue"] {
    font-size: 26px !important;
    font-weight: 600 !important;
    letter-spacing: -0.018em !important;
    color: var(--hf-ink) !important;
    line-height: 1.1 !important;
}
[data-testid="stMetricDelta"] {
    font-size: 11px !important;
    color: var(--hf-muted) !important;
}

/* st.progress — green bar on soft track */
[data-testid="stProgress"] > div > div {
    background: var(--hf-border-soft) !important;
}
[data-testid="stProgress"] > div > div > div {
    background: var(--hf-leaf) !important;
}

/* st.tabs */
[data-baseweb="tab-list"] {
    background: transparent !important;
    border-bottom: 1px solid var(--hf-border) !important;
    gap: 18px !important;
}
[data-baseweb="tab"] {
    background: transparent !important;
    color: var(--hf-muted) !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    padding: 8px 0 !important;
    border-bottom: 2px solid transparent !important;
    margin-bottom: -1px !important;
}
[data-baseweb="tab"][aria-selected="true"] {
    color: var(--hf-green) !important;
    border-bottom-color: var(--hf-green) !important;
    font-weight: 600 !important;
}
[data-baseweb="tab-highlight"] { display: none !important; }

/* ────────────────────────────────────────────────────────────────────────
   st.radio (horizontal) — styled as tabs

   We use a radio rather than `st.tabs` on pages that need programmatic
   tab switching (Streamlit ≤1.39 doesn't allow code to flip a tab). The
   rules below hide the radio bullet, lay options out as underlined
   pill-less tab buttons, and mark the checked option with a forest-green
   bottom border to match the real st.tabs styling above.
   ──────────────────────────────────────────────────────────────────────── */
[data-testid="stRadio"] [role="radiogroup"] {
    gap: 22px !important;
    border-bottom: 1px solid var(--hf-border) !important;
    padding-bottom: 0 !important;
    margin-bottom: 8px !important;
}
[data-testid="stRadio"] [role="radiogroup"] > label {
    cursor: pointer !important;
    padding: 8px 0 !important;
    margin-bottom: -1px !important;
    border-bottom: 2px solid transparent !important;
    color: var(--hf-muted) !important;
    font-weight: 500 !important;
    font-size: 13px !important;
    background: transparent !important;
    transition: color 0.12s ease, border-color 0.12s ease;
}
/* Hide the radio circle indicator */
[data-testid="stRadio"] [role="radiogroup"] > label > div:first-child,
[data-testid="stRadio"] [role="radiogroup"] > label [data-testid="stMarkdownContainer"] ~ div,
[data-testid="stRadio"] [role="radiogroup"] > label [data-baseweb="radio"] > div:first-child {
    display: none !important;
}
[data-testid="stRadio"] [role="radiogroup"] > label:hover {
    color: var(--hf-ink) !important;
}
/* Active radio (has a checked input descendant) */
[data-testid="stRadio"] [role="radiogroup"] > label:has(input:checked) {
    color: var(--hf-green) !important;
    border-bottom-color: var(--hf-green) !important;
    font-weight: 600 !important;
}

/* ────────────────────────────────────────────────────────────────────────
   hi-fi atoms — chips, dots, kpi, stepper, card variants
   ──────────────────────────────────────────────────────────────────────── */
.hf-chip {
    display: inline-flex; align-items: center; gap: 4px;
    padding: 2px 8px;
    font-size: 11px; font-weight: 500;
    border-radius: 999px;
    background: var(--hf-cream);
    color: var(--hf-body);
    border: 1px solid var(--hf-border);
    line-height: 1.5;
    font-variant-numeric: tabular-nums;
}
.hf-chip.ok    { background: var(--hf-leaf-soft);  color: #1f5836; border-color: #b6d3c0; }
.hf-chip.warn  { background: var(--hf-amber-soft); color: #7a5a14; border-color: #dfc987; }
.hf-chip.danger{ background: var(--hf-terra-soft); color: #7a3019; border-color: #d9b09e; }
.hf-chip.ghost { background: transparent; color: var(--hf-muted); }
.hf-chip.solid { background: var(--hf-ink); color: #faf5e6; border-color: var(--hf-ink); }
.hf-chip.green { background: var(--hf-green); color: #faf5e6; border-color: var(--hf-green); }
.hf-chip.outline { background: transparent; }

.hf-dot { display:inline-block; width:7px; height:7px; border-radius:50%; vertical-align: middle; margin-right: 4px; }
.hf-dot.ok   { background: var(--hf-leaf); }
.hf-dot.warn { background: var(--hf-amber); }
.hf-dot.bad  { background: var(--hf-terra); }
.hf-dot.ink  { background: var(--hf-ink); }
.hf-dot.muted{ background: var(--hf-soft); }

/* hi-fi KPI tile (for markdown-rendered KPIs; st.metric also gets styled above) */
.hf-kpi {
    background: var(--hf-cream);
    border: 1px solid var(--hf-border-soft);
    border-radius: var(--hf-radius);
    padding: 12px 14px;
    display: flex; flex-direction: column; gap: 4px;
    box-shadow: var(--hf-shadow);
    min-width: 0;
}
.hf-kpi .k { font-size: 10.5px; color: var(--hf-muted); letter-spacing: 0.04em; text-transform: uppercase; font-weight: 600; }
.hf-kpi .v { font-size: 26px; font-weight: 600; letter-spacing: -0.018em; color: var(--hf-ink); line-height: 1.1; }
.hf-kpi .v .unit { font-size: 14px; color: var(--hf-muted); font-weight: 400; margin-left: 3px; letter-spacing: 0; }
.hf-kpi .d { font-size: 11px; color: var(--hf-muted); }
.hf-kpi.accent { background: var(--hf-green); color: #faf5e6; border-color: var(--hf-green); }
.hf-kpi.accent .k { color: #b6c5b3; }
.hf-kpi.accent .v { color: #faf5e6; }
.hf-kpi.accent .d { color: #b6c5b3; }

/* card variants */
.hf-card { background: var(--hf-cream); border: 1px solid var(--hf-border-soft); border-radius: var(--hf-radius); padding: 18px 20px 22px 20px; box-shadow: var(--hf-shadow); }
.hf-card.flat   { box-shadow: none; }
.hf-card.dark   { background: var(--hf-green); color: #faf5e6; border-color: var(--hf-green); }
.hf-card.warn   { background: var(--hf-amber-soft); border-color: #dfc987; }
.hf-card.danger { background: var(--hf-terra-soft); border-color: #d9b09e; }
.hf-card.ok     { background: var(--hf-leaf-soft);  border-color: #b6d3c0; }

/* wizard stepper */
.hf-stepper { display: inline-flex; align-items: center; gap: 10px; margin: 4px 0 12px 0; }
.hf-step    { display: inline-flex; align-items: center; gap: 6px; font-size: 12px; color: var(--hf-muted); font-weight: 500; }
.hf-step .num {
    display: inline-flex; width: 19px; height: 19px;
    border-radius: 50%;
    background: var(--hf-soft); color: var(--hf-cream);
    justify-content: center; align-items: center;
    font-size: 10.5px; font-weight: 600;
}
.hf-step.active { color: var(--hf-ink); font-weight: 600; }
.hf-step.active .num { background: var(--hf-green); color: #faf5e6; }
.hf-step.done .num { background: var(--hf-leaf); color: #faf5e6; }
.hf-step-sep { color: var(--hf-soft); font-size: 12px; }

/* mini helpers reusable in any st.markdown */
.hf-row     { display: flex; gap: 10px; align-items: center; }
.hf-between { justify-content: space-between; }
.hf-grow    { flex: 1; }
.hf-mono    { font-family: 'JetBrains Mono', monospace; font-size: 11px; color: var(--hf-muted); }
.hf-muted   { color: var(--hf-muted); }
.hf-num     { font-variant-numeric: tabular-nums; }

/* breadcrumb */
.hf-bc {
    font-size: 11px;
    color: var(--hf-muted);
    font-variant-numeric: tabular-nums;
    margin-bottom: 2px;
}
.hf-bc .sep { color: var(--hf-soft); padding: 0 4px; }

/* page footer */
.hf-footer {
    margin: 32px 0 8px 0;
    padding-top: 12px;
    border-top: 1px solid var(--hf-border-soft);
    font-size: 10.5px;
    color: var(--hf-muted);
    letter-spacing: 0.04em;
    text-align: center;
}

/* ────────────────────────────────────────────────────────────────────────
   legacy: progress-square loader (StepTracker), retained for the
   Ingestion facture page until we move to a hi-fi stepper there too.
   ──────────────────────────────────────────────────────────────────────── */
.prog-block { margin: 8px 0; font-family: 'Inter', sans-serif; }
.prog-row { display: flex; align-items: center; padding: 4px 0; font-size: 12px; color: var(--hf-ink); }
.prog-row.muted { color: var(--hf-muted); }
.prog-squares { display: inline-flex; margin-right: 10px; }
.prog-square {
    width: 9px; height: 9px;
    margin-right: 3px;
    border: 1px solid var(--hf-ink);
    background: transparent;
    transition: background 0.15s linear;
}
.prog-square.filled { background: var(--hf-ink); }
.prog-row.muted .prog-square { border-color: var(--hf-soft); }
.prog-row.muted .prog-square.filled { background: var(--hf-soft); }
.prog-status { font-weight: 500; font-size: 12.5px; margin-bottom: 6px; color: var(--hf-ink); }

/* login screen (legacy auth gate) */
.login-shell { max-width: 460px; margin: 0 auto; padding: 0 0 2rem 0; }
.login-caption { text-align: center; font-size: 12.5px; color: var(--hf-muted); margin: 16px 0; }
</style>
"""


# ============================================================================
#  Public API — apply_branding + structural helpers
# ============================================================================

def apply_branding() -> None:
    """Inject the global hi-fi stylesheet. Call near the top of every page."""
    st.markdown(_CSS, unsafe_allow_html=True)


def render_header(
    title: str | None = None,
    subtitle: str | None = None,
    breadcrumb: str | None = None,
    *,
    # legacy kwargs (older pages still pass `subtitle="..."` only)
    show_divider: bool = False,
) -> None:
    """Render the hi-fi H1 row.

    `title` overrides the page's natural title (passed when you don't want
    to call `st.title` yourself). `breadcrumb` shows above as small muted
    text. `subtitle` appears next to the title in a smaller weight.

    Legacy compatibility: if called with just `subtitle="..."` (the old
    signature), we still render a small framing block. The pages will be
    progressively updated to use the new signature in Phase 2.
    """
    if breadcrumb:
        st.markdown(
            f'<div class="hf-bc">{breadcrumb}</div>',
            unsafe_allow_html=True,
        )
    if title:
        sub_html = f' <small style="font-weight:400;color:var(--hf-muted);font-size:12px;margin-left:8px">{subtitle}</small>' if subtitle else ""
        st.markdown(
            f'<h1 class="hf-h1" style="margin:0 0 6px 0">{title}{sub_html}</h1>',
            unsafe_allow_html=True,
        )
    elif subtitle:
        # legacy: just the subtitle line (rendered as a soft caption)
        st.markdown(
            f'<div class="hf-bc">{subtitle}</div>',
            unsafe_allow_html=True,
        )


def render_sidebar_brand() -> None:
    """Render the brand mark at the top of the sidebar."""
    st.sidebar.markdown(
        """
        <div class="hf-sb-brand">
            <div class="mark">❦</div>
            <div class="name">
                merci raymond
                <small>pricing · admin</small>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_footer() -> None:
    """Render the small page footer."""
    st.markdown(
        '<div class="hf-footer">Merci Raymond · Paris &nbsp;·&nbsp; Reconnectons les citadins à la nature</div>',
        unsafe_allow_html=True,
    )


# ============================================================================
#  Atomic helpers — chips, dots, KPI, card, stepper
# ============================================================================

def hf_chip(label: str, kind: str = "") -> str:
    """Return inline HTML for a chip. Embed inside any st.markdown call.

    Kinds: '', 'ok', 'warn', 'danger', 'ghost', 'solid', 'green', 'outline'.
    """
    return f'<span class="hf-chip {kind}">{label}</span>'


def hf_dot(state: str = "ok") -> str:
    """Return inline HTML for a status dot.

    States: 'ok' (green), 'warn' (amber), 'bad' (terra), 'ink', 'muted'.
    """
    return f'<span class="hf-dot {state}"></span>'


def hf_kpi(
    label: str,
    value: str | int | float,
    *,
    unit: str | None = None,
    delta: str | None = None,
    accent: bool = False,
) -> None:
    """Render one KPI tile via markdown. Use inside an existing st.column."""
    unit_html = f'<span class="unit">{unit}</span>' if unit else ""
    delta_html = f'<div class="d">{delta}</div>' if delta else ""
    cls = "hf-kpi" + (" accent" if accent else "")
    st.markdown(
        f'<div class="{cls}"><div class="k">{label}</div>'
        f'<div class="v">{value}{unit_html}</div>{delta_html}</div>',
        unsafe_allow_html=True,
    )


@contextmanager
def hf_card(kind: str = "") -> Iterator[None]:
    """Context manager that wraps its body in a hi-fi card div.

    Kinds: '', 'flat', 'dark', 'warn', 'danger', 'ok'.

    Note: cannot fully replace `st.container(border=True)` (Streamlit's
    container is a stateful block that this CSS already styles). Use this
    for ad-hoc cards built from `st.markdown`-rendered content. For widgets
    inside a card, prefer `st.container(border=True)`.
    """
    cls = "hf-card" + (f" {kind}" if kind else "")
    st.markdown(f'<div class="{cls}">', unsafe_allow_html=True)
    try:
        yield
    finally:
        st.markdown("</div>", unsafe_allow_html=True)


def hf_stepper(steps: list[str], current_idx: int) -> None:
    """Render a horizontal wizard step indicator.

    Items before `current_idx` are 'done' (leaf-green circle), the current
    is 'active' (dark-green circle), items after are pending (soft circle).
    """
    pieces: list[str] = []
    for i, s in enumerate(steps):
        if i < current_idx:
            klass = "hf-step done"
        elif i == current_idx:
            klass = "hf-step active"
        else:
            klass = "hf-step"
        pieces.append(
            f'<span class="{klass}"><span class="num">{i+1}</span> {s}</span>'
        )
    sep = '<span class="hf-step-sep">→</span>'
    st.markdown(
        '<div class="hf-stepper">' + sep.join(pieces) + "</div>",
        unsafe_allow_html=True,
    )


# ============================================================================
#  StepTracker — legacy progress-square loader. Keep for the Ingestion page.
# ============================================================================
@dataclass
class _Step:
    label: str
    fill: int = 0
    state: str = "pending"  # pending | active | done


class StepTracker:
    """List of named steps with N square boxes per step, filled incrementally.

    Used by Ingestion facture's Gemini extraction loader. Retained from v1
    branding for back-compat.
    """

    def __init__(
        self,
        labels: list[str],
        *,
        squares_per_step: int = 9,
        status_label: str = "Traitement en cours…",
        placeholder: "st.delta_generator.DeltaGenerator | None" = None,
    ) -> None:
        self._steps = [_Step(label=l) for l in labels]
        self._n = squares_per_step
        self._status = status_label
        self._placeholder = placeholder or st.empty()
        self._render()

    def _render(self) -> None:
        rows: list[str] = []
        rows.append(f'<div class="prog-status">○ {self._status}</div>')
        for s in self._steps:
            squares = "".join(
                f'<span class="prog-square{" filled" if i < s.fill else ""}"></span>'
                for i in range(self._n)
            )
            row_class = "prog-row" + (" muted" if s.state == "pending" else "")
            rows.append(
                f'<div class="{row_class}">'
                f'<span class="prog-squares">{squares}</span>'
                f'<span>{s.label}</span>'
                f'</div>'
            )
        self._placeholder.markdown(
            '<div class="prog-block">' + "".join(rows) + "</div>",
            unsafe_allow_html=True,
        )

    def activate(self, idx: int, fill: int = 1) -> None:
        for s in self._steps[:idx]:
            s.state = "done"
            s.fill = self._n
        self._steps[idx].state = "active"
        self._steps[idx].fill = min(fill, self._n)
        self._render()

    def tick(self, idx: int, n: int = 1) -> None:
        s = self._steps[idx]
        s.fill = min(s.fill + n, self._n)
        s.state = "active"
        self._render()

    def complete(self, idx: int) -> None:
        s = self._steps[idx]
        s.fill = self._n
        s.state = "done"
        self._render()

    def finish(self, status_label: str | None = None) -> None:
        for s in self._steps:
            if s.state != "done":
                s.fill = self._n
                s.state = "done"
        if status_label:
            self._status = status_label
        self._render()

    def fail(self, idx: int, status_label: str = "Échec.") -> None:
        self._status = status_label
        self._steps[idx].state = "active"
        self._render()


# ============================================================================
#  Page context manager (used by some pages)
# ============================================================================
@contextmanager
def page(subtitle: str | None = None) -> Iterator[None]:
    """Convenience: apply branding + sidebar brand + (optional) header,
    then run the body, then footer.

    Usage:
        with page():
            st.title("...")
            ...
    """
    apply_branding()
    render_sidebar_brand()
    if subtitle:
        render_header(subtitle=subtitle)
    try:
        yield
    finally:
        render_footer()
