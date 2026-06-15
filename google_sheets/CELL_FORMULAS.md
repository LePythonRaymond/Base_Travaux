# Master DPGF Sheet — cell-formula migration

This is the formula-by-formula diff to apply manually to the master
Google Sheet when migrating from the old (single size_class) cascade to
the new (Famille · Sous-catégorie · Conditionnement) taxonomy.

**Before you start** : the new `mr_cascade.gs` is already in
`google_sheets/mr_cascade.gs`. Replace the bound script content first
(Extensions → Apps Script), THEN apply the cell changes below. Order
matters — the script reads the Bordereau columns at fixed positions, so
the column shift below must land in one consistent state.

## Two structural shifts to keep in mind

### 1. Bordereau tab — one column inserted (`subcategory` at D)

`/api/bordereau.csv` now emits these columns in order:
```
A: id              H: unit_type        O: heure_u_pose       V: tier_3_heure_u_decharge
B: reference_name  I: attributes       P: nombre_uth         W: quality_rating
C: family_name     J: cost_ht          Q: tier_1_label       X: last_price_update
D: subcategory     K: cost_currency    R: tier_1_h_decharge  Y: months_since_update
E: brand           L: supplier_name    S: tier_2_label       Z: freshness_status
F: material        M: supplier_rating  T: tier_2_h_decharge  AA: is_active
G: packaging       N: labor_task       U: tier_3_label       AB: is_average
```

**Old → New** column mapping (everything from D onward shifts right by 1; `size_class` is gone):

| Old col | Field                  | New col |
|---------|------------------------|---------|
| D       | brand                  | E       |
| E       | material               | F       |
| F       | packaging              | G       |
| G       | unit_type              | H       |
| H       | attributes             | I       |
| I       | cost_ht                | J       |
| J       | cost_currency          | K       |
| K       | supplier_name          | L       |
| L       | supplier_rating        | M       |
| M       | labor_task             | N       |
| N       | heure_u_pose_default   | O       |
| O       | nombre_uth_default     | P       |
| P       | tier_1_label           | Q       |
| Q       | tier_1_heure_u_decharge| R       |
| R       | tier_2_label           | S       |
| S       | tier_2_heure_u_decharge| T       |
| T       | tier_3_label           | U       |
| U       | tier_3_heure_u_decharge| V       |
| V       | quality_rating         | W       |
| W       | last_price_update      | X       |
| X       | months_since_update    | Y       |
| Y       | freshness_status       | Z       |
| Z       | is_active              | AA      |
| AA      | **size_class** (gone)  | —       |
| AB      | **is_average** (was)   | AB      |

**Rule of thumb**: any formula that references `Bordereau!<col>` for a column letter D-Z in the old layout must be bumped by +1 letter (D → E, E → F, …, Y → Z). Old `Bordereau!AA:AA` references (size_class) must be deleted entirely. `Bordereau!A:C` references are unchanged.

The `=IMPORTDATA(...)` formula in **Bordereau!A1** does NOT change — the endpoint URL stays the same.

### 2. DPGF tab — one column inserted (`Sous-catégorie` at AE)

| Old col | Role                        | New col |
|---------|-----------------------------|---------|
| AA      | Désignation (client mirror) | AA      |
| AB      | Unité   (client mirror)     | AB      |
| AC      | Quantité (client mirror)    | AC      |
| AD      | Famille (filter)            | AD      |
| —       | **Sous-catégorie (filter)** | **AE** new |
| AE      | Conditionnement (filter)    | AF      |
| AF      | Produit (picker)            | AG      |
| AG      | Tier                        | AH      |
| AH      | Fournisseur                 | AI      |
| AI      | Fraîcheur                   | AJ      |
| AJ-AO   | COÛT HUMAIN                 | AK-AP   |
| AP-AQ   | FOURNITURE                  | AQ-AR   |
| AR-AS   | COÛT TOTAL                  | AS-AT   |
| AT-AU   | LOC / LIV                   | AU-AV   |
| AV-AW   | DÉPENSES SUP                | AW-AX   |
| AX-AZ   | MARGES                      | AY-BA   |
| BA-BB   | PRIX CLIENT                 | BB-BC   |

**Rule of thumb**: any DPGF-tab formula that references a DPGF column AE-BB in the old layout must be bumped by +1 letter (AE → AF, AF → AG, …, BB → BC). AA-AD references are unchanged.

### 3. DPGF tab — hidden stable product-id column at **BE** (NEW)

So reverse-ingestion can match a line to its DB product **exactly** (instead of fuzzy-matching the picker string), the DPGF carries the product database `id` in a hidden column **BE** that resolves it from the picked product:

| Cell | Formula |
|------|---------|
| `BE3` (and fill down to BE502) | `=IFERROR(INDEX(Bordereau!A:A, MATCH(AG3, Helpers!A:A, 0)), "")` |

**Easiest way to add it:** this is the *same lookup pattern* as the Fournisseur
(AI) and Fraîcheur (AJ) columns already in the sheet. Copy `AI3`, paste into
`BE3`, and change the INDEX range to `Bordereau!A:A` (the product id). Fill down,
then hide the column.

- `Bordereau!A` is the product `id` (column A of `/api/bordereau.csv`).
- `Helpers!A` is the picker concat string that `AG` validates against (`Famille — Sous-cat — Référence — Cond.`), row-aligned with `Bordereau`.
- So BE = "the DB id of whatever product the user picked in AG", or blank if AG is empty / a manual override.
- **Hide the column** (right-click BE → Hide) — it's plumbing, not for the client or Vincent.
- The reverse-ingestion parser (`lib/dpgf.py`, `COL_BE = 57`) reads it; when blank/absent it falls back to picker + fuzzy matching, so **older DPGFs without this column keep working**.
- A blank BE on a line that DOES have a price (Vincent typed a manual price over the formula for a product not in the DB) is exactly the signal the Retour-DPGF "needs clarification" step uses to ask for taxonomy/supplier/norme.

This is a passive lookup column — no cascade-logic change in `mr_cascade.gs` is required.

### 4. Tab renamed **`Paramètres` → `Pilotage de rentabilité`** + rentability recap (NEW)

The project-settings tab is renamed **`Pilotage de rentabilité`** and now hosts a
**live rentability recap** computed by formulas — the same block Vincent sees at the
bottom of his working xlsx. **The sheet is the source of truth**; the Streamlit app
*reads* these cells (it no longer re-derives the numbers).

**Presentation layout** (rows 20+): **col A = human label · col B = value · col C =
machine identifier (HIDDEN)**. Vincent sees a clean styled card (green section bands
*Temps chantier* / *Rentabilité — GLOBAL* / *Hors SST*, bold right-aligned values, KV
+ marge in green, the planning inputs *Personnes / heures…* in the yellow input
colour). Reverse-ingestion matches on the **col-C identifier** and reads the **col-B
value** (`lib/dpgf.py::_read_project_recap_cells`). Coefficients (rows 6–12, A=id /
B=value) and column mappings (rows 16–18) are unchanged.

Identifier (col C) → formula/value (col B):

| Row | C (identifier — parser key) | B (formula / input) |
|----|----|----|
| 21 | `Tps_chantier` | `=SUMPRODUCT(IFERROR((DPGF!AK3:AK502+DPGF!AL3:AL502)*DPGF!AM3:AM502*DPGF!AC3:AC502, 0))` |
| 22 | `Personnes` | **input** (équipe, ex. 5) |
| 23 | `Heures_par_jour` | **input** (ex. 7) |
| 24 | `Jours_par_semaine` | **input** (ex. 5) |
| 25 | `Semaines_par_mois` | **input** (ex. 4,48) |
| 26 | `Jours` | `=IF(Personnes*Heures_par_jour=0,"",Tps_chantier/(Personnes*Heures_par_jour))` |
| 27 | `Semaines` | `=IF(Jours_par_semaine=0,"",Jours/Jours_par_semaine)` |
| 28 | `Mois` | `=IF(Semaines_par_mois=0,"",Semaines/Semaines_par_mois)` |
| 31 | `Rent_prix_vente` | `=SUM(DPGF!BB3:BB502)` |
| 32 | `Rent_prix_revient` | `=SUM(DPGF!AT3:AT502)+SUM(DPGF!AU3:AU502)+SUM(DPGF!AV3:AV502)+SUM(DPGF!AW3:AW502)+SUM(DPGF!AX3:AX502)` |
| 33 | `Rent_marge_eur` | `=Rent_prix_vente-Rent_prix_revient` |
| 34 | `Rent_marge_pct` | `=IF(Rent_prix_vente=0,"",Rent_marge_eur/Rent_prix_vente*100)` |
| 35 | `Rent_kv` | `=IF(Rent_prix_revient=0,"",Rent_prix_vente/Rent_prix_revient)` |
| 38 | `Rent_hs_prix_vente` | `=SUMIFS(DPGF!BB3:BB502, DPGF!BD3:BD502, FALSE)` |
| 39 | `Rent_hs_prix_revient` | `=SUMIFS(DPGF!AT…,BD,FALSE)+…+SUMIFS(DPGF!AX…,BD,FALSE)` |
| 40 | `Rent_hs_marge_eur` | `=Rent_hs_prix_vente-Rent_hs_prix_revient` |
| 41 | `Rent_hs_marge_pct` | `=IF(Rent_hs_prix_vente=0,"",Rent_hs_marge_eur/Rent_hs_prix_vente*100)` |
| 42 | `Rent_hs_kv` | `=IF(Rent_hs_prix_revient=0,"",Rent_hs_prix_vente/Rent_hs_prix_revient)` |

- **Marge % is stored ×100** (e.g. `17,75`) with a literal-`%` number format (`0,00"%"`), so the parser reads `17.75` directly and it lines up with the app.
- **`BD` (col 56) = `SST ?`** — a **checkbox** on `BD3:BD502`. Ticked = sous-traitant line, **excluded** from the Hors-SST block (the `SUMIFS(…, BD, FALSE)` formulas).
- **`BF` (col 58) = `Commentaire`** — a **wide (340 px) free-text** column on `BF3:BF502`, filled with the **yellow input colour** (`#FFF2CC`) + wrap, for per-line / per-section notes by Vincent. It's the last visible column (BE is hidden). Not read by the app — purely for Vincent.
- Workbook-scoped **named ranges** are created for the cells referenced by name (`Tps_chantier`, `Personnes`, `Rent_prix_vente`, …) — same as the coefficients.

**How to apply:** all of the above (rename, styled recap block, named ranges, `BD` checkbox, hidden `BE` id column, `BF` Commentaire column) is written by the Apps-Script function **`applyRentabilite()`** — run once on the master via the 🌿 *Merci Raymond → 📊 Installer / MAJ rentabilité* menu (it's also called at the end of `applyV24Patch()`). Idempotent; INPUT cells are only set when blank, so re-running never clobbers Vincent's tuning. Named ranges don't survive *Faire une copie*, so run the menu item once per fresh copy.

> **Fidelity note:** the `prix_revient` composition (whether *install chantier* / *log gestion* are cost or margin) and the `Tps_chantier` hours basis should be reverse-checked against Vincent's real "doc de travail" xlsx — the formulas above are self-consistent and match the app's existing model, but the exact target values (revient 1 003 491,67 etc.) depend on his definitions.

## Specific formulas to change

For each formula below, the syntax assumes a data row `14`. Replicate down whatever range the sheet uses (likely rows 3 to 502, in line with `DATA_FIRST_ROW` and `BORDEREAU_LAST_ROW` in `mr_cascade.gs`).

### A. `Helpers!A` — the picker concat string

This is the string that AG (Produit) data-validates against and what the Fournisseur/Fraîcheur formulas key on.

| Cell | OLD formula | NEW formula |
|------|-------------|-------------|
| `Helpers!A2` (and down) | `=Bordereau!C2 & " — " & Bordereau!B2 & " — " & Bordereau!F2` | `=Bordereau!C2 & " — " & Bordereau!D2 & " — " & Bordereau!B2 & " — " & Bordereau!G2` |

The new concat is `Family — Sub-cat — Reference — Packaging` (4 parts). This is also the string format produced by the "Référence à coller" helper in the Streamlit Produits page, so the two stay in sync.

If the old Helpers!A only existed as a fallback drop-down source (some templates put it on Helpers!B instead), apply the same rename — wherever the picker concat lives.

### B. DPGF row 14, header row references (rows 1–2)

If row 1 or row 2 has bold column labels like "AD = Famille / AE = Cond. / AF = Produit", update those labels too:
- AD = **Famille** (unchanged)
- AE = **Sous-catégorie** (new label)
- AF = **Conditionnement** (was AE)
- AG = **Produit** (was AF)
- AH = **Tier** (was AG)
- AI = **Fournisseur** (was AH)
- AJ = **Fraîcheur** (was AI)

### C. DPGF `AI14` — Fournisseur (was AH14)

| Variant | Formula |
|---------|---------|
| **OLD (in AH14)** | `=IFERROR(INDEX(Bordereau!K:K, MATCH(AF14, Helpers!A:A, 0)), "")` |
| **NEW (in AI14)** | `=IFERROR(INDEX(Bordereau!L:L, MATCH(AG14, Helpers!A:A, 0)), "")` |

Two changes:
- `Bordereau!K:K` → `Bordereau!L:L` (supplier_name shifted by +1 due to subcategory insertion)
- `AF14` → `AG14` (Produit column shifted by +1 due to Sous-cat insertion)

### D. DPGF `AJ14` — Fraîcheur (was AI14)

Whatever the old formula looked like (most likely `CHOOSE(MATCH(freshness_status_value, ...), …)`), every reference must be updated:
- The bordereau lookup column for freshness_status: old `Y` → new `Z`
- The picker key: old `AF14` → new `AG14`

| Variant | Formula (typical shape) |
|---------|-------------------------|
| **OLD (in AI14)** | `=IFERROR(CHOOSE(MATCH(INDEX(Bordereau!Y:Y, MATCH(AF14, Helpers!A:A, 0)), {"fresh";"stale_6mo";"stale_9mo"}, 0), "🟢 Frais", "🟡 6–9 mois", "🔴 > 9 mois"), "")` |
| **NEW (in AJ14)** | `=IFERROR(CHOOSE(MATCH(INDEX(Bordereau!Z:Z, MATCH(AG14, Helpers!A:A, 0)), {"fresh";"stale_6mo";"stale_9mo"}, 0), "🟢 Frais", "🟡 6–9 mois", "🔴 > 9 mois"), "")` |

### E. The cost chain (AK14 onwards — was AJ14 onwards)

Every formula in the COÛT HUMAIN / FOURNITURE / COÛT TOTAL / LOC LIV / DÉPENSES SUP / MARGES / PRIX CLIENT blocks references:
1. **Bordereau lookup columns** — apply the +1 shift from the table at the top:
   - Old `Bordereau!I:I` (cost_ht) → new `Bordereau!J:J`
   - Old `Bordereau!N:N` (heure_u_pose) → new `Bordereau!O:O`
   - Old `Bordereau!O:O` (nombre_uth) → new `Bordereau!P:P`
   - Old `Bordereau!Q:Q` (tier_1_heure_u_decharge) → new `Bordereau!R:R`
   - Old `Bordereau!S:S` (tier_2_heure_u_decharge) → new `Bordereau!T:T`
   - Old `Bordereau!U:U` (tier_3_heure_u_decharge) → new `Bordereau!V:V`
2. **Picker key references** — old `AF14` (Produit) → new `AG14`
3. **DPGF cross-references within the row** — old `AG14` (Tier) → new `AH14`; old `AJ14` (first cost-humain cell) → new `AK14`; etc., shift every reference AE-BB by +1 letter.
4. **App-settings references** — any reference to a row-8 coefficient (`$O$8`, `$P$8`, etc.) is unchanged because row 8 is in the Paramètres tab, which doesn't move.

The easiest way to apply step 3 in bulk: use Sheets's "Find and Replace" on the DPGF tab with **Search using regular expressions** enabled, applied to **Formulas** only:

| Find (regex)   | Replace with |
|----------------|--------------|
| `\bAE([0-9]+)` | `AF$1`       |
| `\bAF([0-9]+)` | `AG$1`       |
| `\bAG([0-9]+)` | `AH$1`       |
| `\bAH([0-9]+)` | `AI$1`       |
| `\bAI([0-9]+)` | `AJ$1`       |
| `\bAJ([0-9]+)` | `AK$1`       |
| `\bAK([0-9]+)` | `AL$1`       |
| `\bAL([0-9]+)` | `AM$1`       |
| `\bAM([0-9]+)` | `AN$1`       |
| `\bAN([0-9]+)` | `AO$1`       |
| `\bAO([0-9]+)` | `AP$1`       |
| `\bAP([0-9]+)` | `AQ$1`       |
| `\bAQ([0-9]+)` | `AR$1`       |
| `\bAR([0-9]+)` | `AS$1`       |
| `\bAS([0-9]+)` | `AT$1`       |
| `\bAT([0-9]+)` | `AU$1`       |
| `\bAU([0-9]+)` | `AV$1`       |
| `\bAV([0-9]+)` | `AW$1`       |
| `\bAW([0-9]+)` | `AX$1`       |
| `\bAX([0-9]+)` | `AY$1`       |
| `\bAY([0-9]+)` | `AZ$1`       |
| `\bAZ([0-9]+)` | `BA$1`       |
| `\bBA([0-9]+)` | `BB$1`       |
| `\bBB([0-9]+)` | `BC$1`       |

⚠ **Apply these find-and-replace passes in REVERSE order** (BB→BC first, then BA→BB, …, ending with AE→AF) — otherwise the replacements chain on themselves and a single column ends up shifted by 2 or more.

⚠ **Limit the scope to the DPGF tab only**. The Bordereau, Helpers, and Paramètres tabs use those column letters for their own purposes — they must not be shifted.

⚠ The `\b` word boundary in the regex is important — otherwise "AE" inside "MAEx" gets matched. The regex above is correct.

Then, for the Bordereau column references (step E.1), do a second pass with these substitutions (also DPGF-tab-only, also in reverse order):

| Find (regex)               | Replace with                  |
|----------------------------|--------------------------------|
| `Bordereau!Z(:?[A-Z]*\b)`  | `Bordereau!AA$1`               |
| `Bordereau!Y(:?[A-Z]*\b)`  | `Bordereau!Z$1`                |
| `Bordereau!X(:?[A-Z]*\b)`  | `Bordereau!Y$1`                |
| `Bordereau!W(:?[A-Z]*\b)`  | `Bordereau!X$1`                |
| `Bordereau!V(:?[A-Z]*\b)`  | `Bordereau!W$1`                |
| `Bordereau!U(:?[A-Z]*\b)`  | `Bordereau!V$1`                |
| `Bordereau!T(:?[A-Z]*\b)`  | `Bordereau!U$1`                |
| `Bordereau!S(:?[A-Z]*\b)`  | `Bordereau!T$1`                |
| `Bordereau!R(:?[A-Z]*\b)`  | `Bordereau!S$1`                |
| `Bordereau!Q(:?[A-Z]*\b)`  | `Bordereau!R$1`                |
| `Bordereau!P(:?[A-Z]*\b)`  | `Bordereau!Q$1`                |
| `Bordereau!O(:?[A-Z]*\b)`  | `Bordereau!P$1`                |
| `Bordereau!N(:?[A-Z]*\b)`  | `Bordereau!O$1`                |
| `Bordereau!M(:?[A-Z]*\b)`  | `Bordereau!N$1`                |
| `Bordereau!L(:?[A-Z]*\b)`  | `Bordereau!M$1`                |
| `Bordereau!K(:?[A-Z]*\b)`  | `Bordereau!L$1`                |
| `Bordereau!J(:?[A-Z]*\b)`  | `Bordereau!K$1`                |
| `Bordereau!I(:?[A-Z]*\b)`  | `Bordereau!J$1`                |
| `Bordereau!H(:?[A-Z]*\b)`  | `Bordereau!I$1`                |
| `Bordereau!G(:?[A-Z]*\b)`  | `Bordereau!H$1`                |
| `Bordereau!F(:?[A-Z]*\b)`  | `Bordereau!G$1`                |
| `Bordereau!E(:?[A-Z]*\b)`  | `Bordereau!F$1`                |
| `Bordereau!D(:?[A-Z]*\b)`  | `Bordereau!E$1`                |

Also in reverse order. Same scope (DPGF tab only). Then delete any leftover `Bordereau!AA:AA` references (those were size_class, which no longer exists — replace with `""` or remove the surrounding formula component).

Also update Helpers references — if `Helpers!A` was at A:A in the old version and still is in the new version, no change. If you moved it (because of step A above), update accordingly.

## Verification after applying

1. Reload the Sheet. `Bordereau!A1` should still show the `=IMPORTDATA(...)` formula and the data should populate within 30 seconds.
2. `Helpers!A2` should produce a string like `Arbuste — Caduc — Forsythia x intermedia — Conteneur 5L` (4 parts, em-dash separated).
3. On any blank data row (say row 14): set AD=Arbuste, leave AE/AF empty. Click AG — dropdown should list every arbuste. Type "for" — autocomplete narrows to Forsythia.
4. Pick a Forsythia row. AI (Fournisseur) and AJ (Fraîcheur) auto-populate.
5. The cost chain AK-BC all evaluate to numbers (no `#REF!` or `#VALUE!`).
6. Test the cascade with mixed filters: AD=Arbuste, AE=Caduc — AG narrows to just caduc arbustes.
7. Test "filter empty wins": clear all of AD/AE/AF — AG dropdown lists every product.
