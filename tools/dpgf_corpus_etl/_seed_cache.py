"""Persist classifications to classification_cache.json (the resumable checkpoint).

Runs the genus + domain-rule classifier over the worklist and writes each result
to the cache, keyed by canonical designation. RESUMABLE: it never overwrites an
existing cache entry, so hand-edits / prior runs survive and re-running only fills
the gaps.

    python -m dpgf_corpus_etl._seed_cache
"""

from __future__ import annotations

import json
import os
from decimal import Decimal

from .classify import CACHE_PATH, classify_fallback, load_cache
from .models import RawLine

WORKLIST = os.path.join(os.path.dirname(__file__), "classification_worklist.json")


def main() -> int:
    work = json.load(open(WORKLIST, encoding="utf-8"))
    cache = load_cache()
    added = 0
    for k, e in work.items():
        if k in cache:
            continue  # keep existing (hand) entries — resumable
        ln = RawLine(
            file="", sheet="", row=0, designation=e["designation"], unit_raw="",
            unit=(e["units"][0] if e["units"] else None), quantity=None, cost_ht=Decimal("1"),
            heure_u_decharge=None, heure_u_pose=None, nombre_uth=None,
            comment=(e["comments"][0] if e["comments"] else ""),
            section_path=tuple(e["sections"][0].split(" > ") if e["sections"] else []))
        c = classify_fallback(ln)
        cache[k] = {
            "designation": e["designation"],
            "family": c["family"], "subcategory": c["subcategory"], "labor_task": c["labor_task"],
            "brand": c["brand"], "material": c["material"], "attributes": c["attributes"],
            "confidence": c["confidence"], "method": c["method"],
        }
        added += 1
    with open(CACHE_PATH, "w", encoding="utf-8") as fh:
        json.dump(cache, fh, ensure_ascii=False, indent=1)
    classified = sum(1 for v in cache.values() if v.get("family"))
    print(f"seeded {added} new entries; cache now {len(cache)} ({classified} with a family)")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
