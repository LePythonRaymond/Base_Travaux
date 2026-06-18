"""Single source of truth: anchor vocabulary, enums, regexes, thresholds.

Column letters are NEVER hard-coded — the corpus shifts them per file. We detect
columns by the *meaning* of their header text. These vocabularies drive that.
"""

from __future__ import annotations

import unicodedata

# --- enum targets (must match the DB) ------------------------------------------
UNIT_TYPES = ["u", "m3", "ml", "m2", "Ft", "kg", "l"]  # mirrors lib/pickers.UNIT_TYPES

# --- header anchors (normalized: accent-folded, lowercased, ws-collapsed) -------
# Band row labels (merged cells spanning their sub-columns).
BAND_LABELS = {
    "cout humain",
    "fourniture",
    "cout total",
    "location/livraison",
    "location / livraison",
    "depenses sup",
    "depense sup",
    "marges",
    "marge",
    "prix client",
}
# The cost column may only live under this band.
COST_BAND = "fourniture"
# Bands whose columns are post-margin / derived — cost must NEVER bind here.
DENY_BANDS = {
    "cout total",
    "marges",
    "marge",
    "prix client",
    "depenses sup",
    "depense sup",
    "location/livraison",
    "location / livraison",
}

# Sub-header role → accepted normalized patterns (substring / fuzzy matched).
SUBHEADER_ROLES: dict[str, list[str]] = {
    # RAW supplier cost HT, pre-margin. THE cost column.
    "cost_ht": ["fourniture/u", "fourniture / u", "fourniture u", "fourniture/ u"],
    # acheminement / déchargement time per unit → heure_u_decharge
    "heure_u_decharge": [
        "heure/u appro",
        "heure /u appro",
        "heure u appro",
        "heure/u appro.",
        "heure/u decharge",
        "heure/u decharget",
        "heure/u dechauge",
        "h/u appro",
    ],
    "heure_u_pose": ["heure/u pose", "heure /u pose", "heure u pose", "h/u pose"],
    "nombre_uth": [
        "nombre d'uth",
        "nombre d uth",
        "nombre duth",
        "nb pers.",
        "nb pers",
        "nb personnes",
        "nombre uth",
        "nbre pers",
        "nb. pers",
    ],
}

# Left-block roles (designation / unit / qty / the client PU we must ignore).
LEFT_ROLES: dict[str, list[str]] = {
    "designation": [
        "designation des ouvrages",
        "designation des travaux",
        "description des ouvrages",
        "description des travaux",
        "designation du poste",
        "designation",
        "description",
        "libelle",
        "intitule",
        "poste",
        "ouvrage",
        "nom vegetaux",
        "nom de plante",
        "nom",
    ],
    "unit": ["u", "un", "unite", "unites", "unit", "un.", "u."],
    "quantite": ["quantite", "quantites", "qte", "qtes", "quant.", "quant", "qte."],
    "client_pu": [
        "prix client / u",
        "prix client/u",
        "prix fourni pose client",
        "prix unitaire",
        "prix unit",
        "prix u",
        "p.u. €",
        "p.u €",
        "p.u.",
        "p.u",
        "pu ht",
        "pu €",
        "prix unitaire (€)",
        "prix unitaire (€).h.t.",
    ],
    "comment": [
        "commentaire mr",
        "commentaires mr",
        "commentaire",
        "commentaires",
        "remarque",
        "remarques",
        "cctp",
        "info cahier des charges",
        "commentaire cctp",
        "comment.",
    ],
}

# Derived/client sub-headers cost must never bind to (extra guard at sub-header level).
DENY_SUBHEADERS = [
    "prix client",
    "prix fourni pose client",
    "cout u fourni pose",
    "cout total fourni pose",
    "cout mo/u",
    "cout humain total",
    "prix unitaire",
    "p.u.",
]

# Cells meaning "no value" in cost/number columns.
EMPTY_TOKENS = {"-", "—", "–", "pm", "p.m.", "n/a", "na", "néant", "neant", "", "hors lot", "inclus"}

# Excel formula-error litter to coerce to None.
ERROR_TOKENS = {"#div/0!", "#value!", "#ref!", "#n/a", "#name?", "#null!", "#num!", "####"}

# --- thresholds ----------------------------------------------------------------
ANCHOR_MIN_CONFIDENCE = 0.5      # below this, skip the sheet (no silent guesses)
FUZZY_RATIO = 0.86              # difflib SequenceMatcher cutoff for header matching
HEADER_SCAN_ROWS = 45          # how deep to look for the header band
MULTIPLIER_LOOKBACK = 4        # rows above band row that are multiplier candidates

# Coefficient-range fingerprint for the multiplier row.
RATE_RANGE = (20.0, 60.0)      # €/h labor rate (seen 31, 32, 45)
MARGIN_RANGE = (1.0, 2.5)      # markup coeffs (1.02 .. 2.0)
SECURITY_RANGE = (0.0, 0.25)   # sécurité-temps coeff (0.05 .. 0.10)

MONEY_QUANT = "0.01"           # Decimal rounding for cost_ht (NUMERIC(10,2))


def normalize(value: object) -> str:
    """Accent-fold, lowercase, collapse whitespace, strip trailing punctuation.

    Used for all header matching so 'Qté' == 'qte' and 'Désignation' == 'designation'.
    Drops the € sign (matching is content-based) but keeps internal '/' and '.'.
    """
    if value is None:
        return ""
    s = str(value).replace("€", " ")
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = " ".join(s.split())  # collapse all whitespace (incl NBSP via str.split)
    return s.strip(" :*.-")
