"""Normalize the messy unit tokens in the corpus to the DB enum [u,m3,ml,m2,Ft,kg,l].

'Ens/Fft/forfait' collapse to 'Ft' (a lump unit — its per-unit cost is per-forfait,
flagged so it isn't averaged against real m²/u costs downstream).
"""

from __future__ import annotations

from .config import normalize

# normalized token -> canonical UNIT_TYPES value
_ALIASES: dict[str, str] = {
    "u": "u", "un": "u", "unite": "u", "unites": "u", "u.": "u", "un.": "u",
    "pce": "u", "piece": "u", "pieces": "u", "p": "u", "nb": "u", "nbre": "u", "u/u": "u",
    "m2": "m2", "m²": "m2", "metre carre": "m2", "metres carres": "m2", "m^2": "m2",
    "m3": "m3", "m³": "m3", "metre cube": "m3", "metres cubes": "m3", "m^3": "m3",
    "ml": "ml", "metre lineaire": "ml", "metres lineaires": "ml", "metre": "ml",
    "metres": "ml", "m": "ml", "mlt": "ml",
    "kg": "kg", "kilo": "kg", "kilos": "kg", "kilogramme": "kg",
    "l": "l", "litre": "l", "litres": "l", "lt": "l",
    "ft": "Ft", "fft": "Ft", "forfait": "Ft", "forf": "Ft", "f": "Ft",
    "ens": "Ft", "ens.": "Ft", "ensemble": "Ft", "u forfait": "Ft",
}

LUMP_UNITS = {"Ft"}  # per-forfait cost; flag, don't average against physical units


def normalize_unit(raw: object) -> tuple[str | None, str, bool]:
    """Return (canonical_unit_or_None, raw_text, is_lump).

    Unknown tokens → (None, raw, False); caller keeps raw + flags for review.
    """
    raw_text = "" if raw is None else str(raw).strip()
    norm = normalize(raw)
    canon = _ALIASES.get(norm)
    if canon is None and norm:
        # tolerate a trailing 's' or stray dot
        canon = _ALIASES.get(norm.rstrip("s.").strip())
    is_lump = canon in LUMP_UNITS if canon else False
    return canon, raw_text, is_lump
