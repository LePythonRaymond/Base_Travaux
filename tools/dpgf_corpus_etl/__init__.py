"""Offline ETL that mines Vincent's worked DPGFs into a human-review spreadsheet.

One-time pre-launch bulk load. Deterministic parsing for the mechanical columns
(cost = Fourniture/U, unit, the raw pose/appro/UTH labor numbers, supplier net
prices); LLM-assisted classification for the subtle semantic fields (taxonomy
family/sous-catégorie + labor-norm task). See
``~/.claude/plans/done-everything-is-in-drifting-ladybug.md`` for the full plan.

The package is intentionally standalone: the ``extract`` phase needs no DB and no
Streamlit. Only the ``load`` phase imports ``lib.db`` / ``lib.pickers``.
"""
