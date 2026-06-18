"""Track the section-header path (e.g. Plantations → Arbres tiges) per data line.

Section-header rows have a designation but no unit / quantity / cost. We maintain a
small outline stack keyed by the numbering depth ('1' → '1.1' → '1.1.1'); absent
numbering, ALL-CAPS short rows are treated as top-level. The resulting path seeds
the LLM classifier's context and the labor-norm task identity (it is NOT trusted as
the final family — that's the classifier's job).
"""

from __future__ import annotations

import re

from .workbook import Grid

_NUM_RE = re.compile(r"^\s*(\d+(?:[.\-]\d+)*)")


def numbering_depth(text: str) -> int | None:
    """Depth from a leading outline number: '2'→1, '2.3'→2, '2.3.1'→3."""
    if not text:
        return None
    m = _NUM_RE.match(str(text))
    if not m:
        return None
    return len([p for p in re.split(r"[.\-]", m.group(1)) if p != ""])


class PathTracker:
    def __init__(self) -> None:
        self.stack: list[tuple[int, str]] = []  # (depth, label)

    def update_header(self, label: str, depth: int | None) -> None:
        d = depth if depth is not None else (len(self.stack) + 1)
        # pop deeper-or-equal levels, then push
        self.stack = [(dd, ll) for (dd, ll) in self.stack if dd < d]
        self.stack.append((d, label))

    @property
    def path(self) -> tuple[str, ...]:
        return tuple(label for _, label in self.stack)


def is_section_header(designation: str, has_unit: bool, has_qty: bool,
                      has_cost: bool, has_labor: bool) -> bool:
    """A row with a designation but no unit/qty/cost/labor is a section header."""
    if not designation or not designation.strip():
        return False
    return not (has_unit or has_qty or has_cost or has_labor)
