# DPGF corpus load — results & progress

Status snapshot of the one-time pre-launch data load mined from Vincent's 23 worked
DPGFs (the "LOAD" tier). Everything here is **offline + checkpointed + resumable**.

## What's done

| Step | State | Artifact |
|---|---|---|
| Anchor detection (cost col by meaning) | ✅ validated 23/23 files | `anchors.py` + `_selftest` |
| Extraction (cost, unit, labor, attributes, suppliers) | ✅ all 23 files | `run.py extract` |
| Classification worklist (distinct products) | ✅ 1257 distinct | `classification_worklist.json` |
| **Classification (family / sous-cat / labor task)** | ✅ **1250/1257 (99.4%)** | `classification_cache.json` |
| Review spreadsheet | ✅ generated | `../../dpgf_corpus_review.xlsx` (project root) |
| Idempotent DB load | ⬜ ready, not run (needs DB) | `loader.py` / `run_load.py` |

## Latest review spreadsheet (`dpgf_corpus_review.xlsx`) — ALL 34 files

- **1943 products** with a real supplier cost (deduped across all 34 files = the original
  26 + 8 dropped into `sources/dpgf/new/`, incl. a macro-enabled `.xlsm`)
- **1937 classified to a family (99.7%)**; 6 left as À-classifier (genuine non-products: zone/section headers, a recap total, a note)
- **1783 auto-approved (✓)** — high-confidence, no flags; the rest await a human glance (precision-first)
- **71 labor-norm tasks** (the "sure" set: n_obs ≥ 3, each with a stable `labor_id` LN001…;
  pose/UTH = median, décharge tiers = p25/p50/p75 across projects). Pruned from a noisy 129:
  super-low-confidence norms dropped; multi-unit tasks split into DB-safe unique names
  (e.g. `Mise en œuvre substrat [m3]`); task identity derived deterministically from the
  family so wording can't duplicate (`finalize_norms` + `task_from_family`). 30 base tasks.
- **14 suppliers**: ADEZZ, ATECH, Carrez, Chausson, Colas, Eurovia, MySteel, Point P, Pépinière
  Poulain, Pépinières Allavoine, Pépinières du Plateau de Versailles, Raboni, TFB, + Fournisseur inconnu
- **191 taxonomy triplets**, **2056 price-history observations**

New files are auto-discovered from `sources/dpgf/new/` (`.xlsx`/`.xlsm`/`.csv`) — just drop
more in and re-run `worklist` → `_seed_cache` → `extract`; the cache skips anything already done.
Multi-version workbooks are pinned by `SHEET_OVERRIDES` in `run.py`: NEX → `Copie de DPGF`;
51LEM → `TCO … DPGF Final` + `TSs Client suivi`; Lot N°17 → `DPGF`; ROOFSCAPES 25 (Sébastopol)
→ `V1` (V2 / V2-Variante are reduced/alternative versions).

## How the classification was done (no Gemini)

The classification IS applied judgment, encoded deterministically so it runs offline
and is reproducible:

- **Plants → `genus.py`**: a botanical genus→family map (Arbre/Arbuste/Vivace/Graminée/
  Couvre-sol/Grimpante/Bulbe). The **genus overrides the section path**, so a *Persicaria
  bistorta* priced inside a "BACS JARDINIERES" section is correctly a Vivace, not a Bac.
  Arbre-vs-Arbuste is settled by forme (tige/cépée → Arbre) + species rules.
- **Non-plants → `classify.py` keyword rules**, which **win over the section path** (so
  "Apport de compost" in an *Arbres* section is Substrat, not Arbre).
- Each result is written to **`classification_cache.json`**, keyed by canonical designation.
  That file is the checkpoint and the source of truth the review sheet is built from.

The `classify.py` LLM (Gemini) backend is still wired and will be used automatically for
any *new* designation not in the cache, when run in an env with `google-generativeai` +
`GEMINI_API_KEY` (this box is py3.7, so it used the deterministic path).

## Resume / re-run

```bash
cd merci-raymond-pricing/tools
python3 -m dpgf_corpus_etl.run status            # X/Y classified
python3 -m dpgf_corpus_etl._seed_cache           # fills only MISSING cache keys (safe to re-run)
python3 -m dpgf_corpus_etl.run extract --all-load --out ../../dpgf_corpus_review.xlsx
```

Adding more files later: extend `_all_load_files()` (or pass `--files`), regenerate the
worklist, run `_seed_cache` (classifies only the new designations), re-extract. Hand-edits
to `classification_cache.json` are never overwritten.

## Load to DB (when ready)

```bash
python3 -m dpgf_corpus_etl.run_load --review ../../dpgf_corpus_review.xlsx --dry-run
python3 -m dpgf_corpus_etl.run_load --review ../../dpgf_corpus_review.xlsx
```
Idempotent, FK-ordered, `source='historical_dpgf'`. Apply `init/07_taxonomy_fullworks.sql`
first. Only rows with `approve=✓` load.

## What a human should still review (precision-first)

1. **Labor-norm tiers** with `n_obs` low or flagged `low_confidence`/`tiers_synthetic`
   (many décharge values are 0 in the source → tiers collapse to 0; Vincent sets real ones).
2. **Arbre vs Arbuste / Vivace vs Couvre-sol** edge calls on ambiguous genera.
3. **~30 continuation/detail rows** that carry a cost but aren't standalone products
   ("Ep. 10cm - R+2", "En relevé ht: 50cm", "Modules") — unflag/merge as needed.
4. Supplier attribution beyond the consultation-matched plants (most non-plant lines =
   Fournisseur inconnu until invoices arrive).

## Remaining optional enhancement

All 26 source files are now extracted. The only thing NOT yet mined is **51LEM's
`CMDAchats` sheet** — real purchase-order *actuals* (supplier + paid cost), a different
schema from the doc-de-travail. It would let us replace estimated `Fourniture/U` with
paid costs for that one won job (with estimate-vs-actual dedup). Low priority; the main
51LEM DPGF lines are already loaded.
