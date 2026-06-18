"""Cross-project labor-norm computation.

pose / UTH defaults = median across observations. Décharge tiers
facile/moyen/difficile = p25/p50/p75 computed ACROSS PROJECTS (aggregate within a
project to its median first, so one big job can't dominate). Thin-sample fallback
scales a single value by corpus-wide ratio constants. Every norm carries n_obs and
min/median/max so Vincent can override before load.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from decimal import Decimal

from .models import LaborObservation

# Fallback tier ratios (decharge p25/p50 and p75/p50) for thin samples.
K_LOW, K_HIGH = 0.6, 1.6

# Keep only "sure" norms: at least this many observations + real time data.
MIN_OBS = 3
DEFAULT_TASK = "Norme par défaut (à classifier)"


def _f(x) -> float | None:
    return float(x) if x is not None else None


def _percentile(sorted_vals: list[float], q: float) -> float:
    if not sorted_vals:
        return 0.0
    if len(sorted_vals) == 1:
        return sorted_vals[0]
    idx = q * (len(sorted_vals) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(sorted_vals) - 1)
    frac = idx - lo
    return sorted_vals[lo] * (1 - frac) + sorted_vals[hi] * frac


def _mad_filter(vals: list[float]) -> list[float]:
    """Drop non-positive and gross outliers (a coefficient leaked into a time col)."""
    pos = [v for v in vals if v is not None and v > 0]
    if len(pos) < 4:
        return pos
    med = statistics.median(pos)
    mad = statistics.median([abs(v - med) for v in pos]) or 1e-9
    return [v for v in pos if abs(v - med) / mad <= 6]


def compute_norms(observations: list[LaborObservation]) -> list[dict]:
    by_task: dict[tuple[str, str | None], list[LaborObservation]] = defaultdict(list)
    for o in observations:
        by_task[(o.task_name, o.unit)].append(o)

    norms: list[dict] = []
    for (task, unit), obs in sorted(by_task.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or "")):
        pose = _mad_filter([_f(o.heure_u_pose) for o in obs])
        uth = _mad_filter([_f(o.nombre_uth) for o in obs])
        # décharge: within-project median first, then percentiles across projects
        by_proj: dict[str, list[float]] = defaultdict(list)
        for o in obs:
            v = _f(o.heure_u_decharge)
            if v is not None and v >= 0:
                by_proj[o.file].append(v)
        proj_meds = sorted(statistics.median(v) for v in by_proj.values() if v)

        n_obs = len(obs)
        flags: list[str] = []
        pose_def = round(statistics.median(pose), 3) if pose else 0.0
        uth_def = round(statistics.median(uth), 2) if uth else 1.0

        if len(proj_meds) >= 5:
            t1 = _percentile(proj_meds, 0.25)
            t2 = _percentile(proj_meds, 0.50)
            t3 = _percentile(proj_meds, 0.75)
        elif len(proj_meds) >= 2:
            t2 = statistics.median(proj_meds)
            t1, t3 = t2 * K_LOW, t2 * K_HIGH
            flags.append("tiers_synthetic")
        elif len(proj_meds) == 1:
            t2 = proj_meds[0]
            t1, t3 = t2 * K_LOW, t2 * K_HIGH
            flags.append("low_confidence")
        else:
            t1 = t2 = t3 = 0.0
            flags.append("no_decharge_obs")

        norms.append({
            "base_task": task,
            "task_name": task,
            "unit_type": unit or "u",
            "nombre_uth_default": uth_def,
            "heure_u_pose_default": pose_def,
            "tier_1_heure_u_decharge": round(t1, 3),
            "tier_2_heure_u_decharge": round(t2, 3),
            "tier_3_heure_u_decharge": round(t3, 3),
            "n_obs": n_obs,
            "decharge_min": round(min(proj_meds), 3) if proj_meds else None,
            "decharge_median": round(statistics.median(proj_meds), 3) if proj_meds else None,
            "decharge_max": round(max(proj_meds), 3) if proj_meds else None,
            "flags": ",".join(flags),
        })
    return norms


def finalize_norms(norms: list[dict], min_obs: int = MIN_OBS):
    """Prune to 'sure' norms, give each a stable ID, and make task_name unique.

    - Keeps a norm only if n_obs >= min_obs AND it has real time data (the default
      placeholder is always kept). The rest are dropped → their products fall back
      to the default norm at load.
    - A base task that spans several units (e.g. 'Mise en œuvre paillage' for m2 & m3)
      is split into distinct, DB-safe names: 'Mise en œuvre paillage [m2]' / '[m3]'.
      This avoids the UNIQUE(task_name) collision that would silently drop norms.
    - Returns (kept_norms, task_map) where task_map[(base_task, unit)] = final name
      (or None if pruned) so callers can repoint each product to the right norm.
    """
    base_count = Counter(n["base_task"] for n in norms)
    task_map: dict[tuple, str | None] = {}
    kept: list[dict] = []
    seen: set[str] = set()
    i = 0
    for n in sorted(norms, key=lambda x: -(x.get("n_obs") or 0)):
        base, unit = n["base_task"], n["unit_type"]
        is_default = base == DEFAULT_TASK
        has_data = (n.get("heure_u_pose_default") or 0) > 0 or (n.get("tier_2_heure_u_decharge") or 0) > 0
        sure = is_default or ((n.get("n_obs") or 0) >= min_obs and has_data)
        final = base if (is_default or base_count[base] == 1) else f"{base} [{unit}]"
        task_map[(base, unit)] = final if sure else None
        if sure and final not in seen:
            seen.add(final)
            i += 1
            row = dict(n)
            row["task_name"] = final
            row["labor_id"] = f"LN{i:03d}"
            kept.append(row)
    return kept, task_map
