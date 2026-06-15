# Master DPGF Google Sheet — installation

This folder contains the Apps Script that powers the **progressive
filter** on the DPGF tab: AD Famille → AE Sous-catégorie → AF
Conditionnement → AG Produit. Any subset of the first three filters
narrows the AG dropdown; nothing is required. The script must live
inside a **master Google Sheet**. Every new project is started by
duplicating that master — the Apps Script comes along for the ride.

> **Migrating from the old (3-column, size_class) layout?** See
> [`CELL_FORMULAS.md`](./CELL_FORMULAS.md) for the cell-formula diff
> first, then come back here for the script + auth steps.

## One-time setup (Taddeo)

1. **Generate the xlsx**:
   ```bash
   python3 /tmp/build_dpgf_template.py
   ```
   produces `MR_DPGF_Template.xlsx`.

2. **Upload to Drive** in a folder Vincent has read access to (e.g.
   `Merci Raymond / Modèles / DPGF`).

3. **Open with Google Sheets** (right-click → *Ouvrir avec → Google Sheets*).
   Drive auto-converts the xlsx. Rename the file to
   **`MR DPGF Template (Master)`** so it's obviously the source-of-truth.

4. **Switch Bordereau to live mode**:
   - Tab Bordereau → cell A1 → delete demo rows 2-3 → type:
     ```
     =IMPORTDATA("https://<your-domain>/api/bordereau.csv?key=<KEY>")
     ```
     Replace `<your-domain>` with the ngrok URL or the production
     `prices.merciraymond.fr` once DNS is in place; `<KEY>` is the
     `BORDEREAU_API_KEY` from `.env`.

5. **Attach the Apps Script**:
   - `Extensions → Apps Script`.
   - In the editor, delete the default `Code.gs` content and paste the
     full contents of [`mr_cascade.gs`](./mr_cascade.gs).
   - `Fichier → Renommer` → `MR Cascade`.
   - Press Save (Ctrl/Cmd-S). The first save will prompt you to authorise
     the script for spreadsheet access — accept.

6. **Verify the cascade**:
   - Back on the spreadsheet, in any empty row (say row 14): type a number
     in the client zone where you mapped `Col_Quantite` (e.g. `E14 = 50`).
     The mirror cells AA14-AC14 light up yellow.
   - **Empty-filter case**: click `AG14` directly (no AD/AE/AF set). The
     dropdown lists every product in the bordereau. Type a few letters
     ("lavand", "teralt", …) — the modern Sheets dropdown filters live.
   - **Cascade narrowing**: click `AD14` → pick a famille (e.g.
     `Substrat / amendement`). Now `AG14`'s dropdown is narrowed to that
     family only. Add `AE14 = Amendement organique` to narrow further.
     Add `AF14 = BigBag` to narrow to a single triplet.
   - Pick a product in AG14. `AI14` (Fournisseur) and `AJ14` (Fraîcheur)
     auto-populate; cost cells AK-BC all compute through to PRIX CLIENT.

7. **Lock the master read-only-ish**:
   - `Fichier → Partager → Partager` → set link sharing to
     **`Tous les utilisateurs ayant le lien : Lecteur`**.
   - This prevents accidental edits to the master while still letting
     anyone start a new project from it.

8. **Generate the "make a copy" URL**:
   ```
   https://docs.google.com/spreadsheets/d/<MASTER_ID>/copy
   ```
   Replace `<MASTER_ID>` with the long alphanumeric ID from the master
   Sheet's URL. Send this link to Vincent — and pin it somewhere obvious.

## Vincent's per-project flow (no setup required)

1. Click the **"Make a copy"** link Taddeo shared.
2. Google creates a fresh copy of the master in Vincent's Drive. The Apps
   Script comes with it.
3. On first interaction with the cascade dropdown, Google asks Vincent to
   authorise the script ("This app wants to edit your spreadsheet"). He
   clicks accept. **Once per project copy.**
4. Renames the file to the project name (e.g. `DPGF Pavillons Quai
   Austerlitz 2026-05`).
5. Runs **🌿 Merci Raymond → 📊 Installer / MAJ rentabilité** once (rebinds the
   workbook-scoped named ranges used by the cost chain + the rentability recap,
   which don't survive *Faire une copie*).
6. Pastes the customer's DPGF into the LEFT zone (columns A-Z). Sets the
   three column mappings on the **Pilotage de rentabilité** tab (the renamed
   *Paramètres* tab — `Col_Designation` / `Col_Unite` / `Col_Quantite` in B16-B18).
7. Works the cascade picker row by row, ticking **`SST ?`** (col BD) on any
   subcontractor line. The recap block on the **Pilotage de rentabilité** tab
   (Prix vente / revient / Marge / KV, GLOBAL + Hors-SST + Tps chantier) updates
   live as he prices.

## Updating the master script later

The script lives **inside** each spreadsheet copy. Updating the master's
script does NOT propagate to copies already in flight. The trade-off:

- **For NEW projects**: update `mr_cascade.gs` here, redeploy by repeating
  step 5 above (paste-replace the script in the master). New copies pick
  up the changes automatically.
- **For IN-FLIGHT projects**: Vincent must re-paste the new script into
  his project copy (Extensions → Apps Script → replace contents).
  Usually not needed — cascade logic doesn't change often. Document the
  date of the script revision at the top of `mr_cascade.gs` if you want a
  change marker.

## Troubleshooting

- **Cascade doesn't fire** → did Google block the script? Check
  `Extensions → Apps Script → Triggers`. There should be no manual
  triggers needed; `onEdit(e)` is a *simple trigger* that fires
  automatically when the user is signed in. If the script never runs:
  - Confirm the script was saved.
  - Try a tiny edit anywhere on the DPGF tab to force a script load.
  - Re-authorise: `Apps Script → Run → onEdit` → accept the auth prompt.

- **"Authorisation required" on every edit** → user is not signed in to
  the Google account that owns the Sheet. Sign in.

- **Dropdown empty after cascade** → no rows in the Bordereau match the
  current AD/AE/AF combination. Either fix the catalog data, or clear
  one or more of the filters (they're optional — `AG` lists everything
  with all three blank).

- **`#REF!` in formulas** → almost always means the Bordereau sheet name
  isn't `Bordereau`, or the IMPORTDATA URL/key is wrong. Check the
  Bordereau A1 formula.

- **AI (Fournisseur) and AJ (Fraîcheur) are blank** → these are not
  Apps-Script-driven, they're spreadsheet INDEX/MATCH formulas. If AG has
  a value but AI/AJ are blank, the picker string in AG doesn't match any
  row in `Helpers!A`. Most common cause: Bordereau hasn't loaded yet
  (IMPORTDATA still fetching). Wait a few seconds and re-pick. If the
  picker string format itself looks wrong (e.g. only 3 em-dash parts
  instead of 4), `Helpers!A` is still using the old concat — see
  [`CELL_FORMULAS.md`](./CELL_FORMULAS.md) section A.

- **Adding new (Famille, Sous-catégorie, Conditionnement) triplets** →
  done in Streamlit, not the Sheet. Open the **À classifier** page →
  **Référentiel taxonomie** tab → click "Ajouter au référentiel". The
  Sheet picks up new triplets on the next IMPORTDATA refresh (≤ 30 s).
