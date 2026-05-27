# Merci Raymond — Pricing DB Prototype

Internal pricing database for Merci Raymond. Three containers on Docker:

- **`postgres:15`** — strict-typed price catalog with audit triggers.
- **`streamlit`** — admin UI (CRUD + Gemini-assisted invoice ingestion).
- **`bordereau_api`** — FastAPI single-endpoint CSV exporter for Power Query.
- **`caddy`** — HTTPS termination + basic auth at the proxy.

The build spec is `../PRD.md` (read sections 1–4 for context, 5–14 for the spec, 15 for acceptance tests).

---

## Quickstart

```bash
# 1. Copy the env template, fill in real values, save as `.env`
cp .env.template .env
# (skip this step — `.env` is already populated locally for the Taddeo dev environment)

# 2. Boot the stack
docker compose up -d

# 3. Watch the logs until Postgres is healthy and Streamlit is up
docker compose logs -f streamlit
```

Then:

- Admin UI: <https://localhost/> (basicauth + Streamlit login: user `admin`, password `Raymond-2026`)
- API health (no auth): <https://localhost/api/health>
- Bordereau CSV: <https://localhost/api/bordereau.csv> with header `X-API-Key: <BORDEREAU_API_KEY from .env>`

> **First-time HTTPS warning:** in `local` mode (PUBLIC_DOMAIN=localhost), Caddy issues a self-signed certificate via its internal CA. Browsers will warn until you trust the CA. Curl: use `-k`.

---

## Local-mode vs production

Set `PUBLIC_DOMAIN` in `.env`:

| Value | Behavior |
|---|---|
| `localhost` | Caddy uses its internal CA → self-signed cert. Good for local dev. |
| `prices.merciraymond.fr` (or any DNS-pointed name) | Caddy auto-provisions a Let's Encrypt cert. DNS must be in place first. |

---

## Master DPGF Google Sheet (Vincent's working template)

The DPGF template is a Google Sheet now (not Excel + Power Query) because
Vincent's customers send DPGFs in widely different layouts. The flexible
template adapts via a column-mapping panel in the `Paramètres` tab and a
**progressive filter** in the picker — any subset of (Famille,
Sous-catégorie, Conditionnement) narrows the Produit dropdown; nothing
required, type-search the full list if you prefer. Driven by a small
Apps Script bound to the **master Sheet**.

Setup is documented step-by-step here:
- **[`google_sheets/INSTALL.md`](google_sheets/INSTALL.md)** — Taddeo's
  one-time setup (upload xlsx, attach script, generate "Faire une copie"
  URL).
- **[`google_sheets/mr_cascade.gs`](google_sheets/mr_cascade.gs)** — the
  Apps Script that powers the cascade.
- **[`google_sheets/CELL_FORMULAS.md`](google_sheets/CELL_FORMULAS.md)** —
  the cell-formula diff to apply when migrating an existing master Sheet
  to the 4-column (Famille · Sous-cat · Conditionnement · Produit) layout.

For every new project, Vincent clicks the "Make a copy" URL, authorises
the script once, and starts pasting the customer's DPGF.

The bordereau-API key still lives in `.env`; Vincent's master Sheet
references the public URL through `=IMPORTDATA(...)` on the Bordereau
tab. See INSTALL.md step 4.

---

## Project structure

```
merci-raymond-pricing/
├── docker-compose.yml
├── Caddyfile
├── .env.template          ← committed, with placeholders
├── .env                   ← gitignored, real secrets
├── init/
│   ├── 01_extensions.sql  ← pg_trgm, unaccent
│   ├── 02_schema.sql      ← verbatim copy of ../schema_v1.sql
│   ├── 03_seed.sql        ← 16 product_families + 17 labor_norms + placeholder supplier
│   ├── 04_size_class.sql  ← (historical) per-plant-family averages migration
│   └── 05_taxonomy.sql    ← (latest) Famille → Sous-cat → Conditionnement taxonomy
├── streamlit_app/         ← admin UI (Python 3.12)
│   ├── main.py
│   ├── pages/             ← Dashboard, Suppliers, Products, Labor_Norms, Ingest_Invoice, À classifier, Settings
│   └── lib/               ← db, auth, gemini, matcher, prompts, schemas
├── bordereau_api/         ← FastAPI (Python 3.12)
│   └── main.py
├── google_sheets/         ← Apps Script + install doc for the master Sheet
│   ├── mr_cascade.gs        ← progressive filter Famille / Sous-cat / Cond → Produit
│   ├── CELL_FORMULAS.md     ← cell-formula migration diff for the taxonomy rework
│   └── INSTALL.md           ← one-time master-Sheet setup
└── data/invoices/         ← bind-mounted; PDFs persist on the host
```

---

## Cardinal invariants (per PRD §3)

1. **Costs only.** `products.cost_ht` is supplier cost HT. **No sale prices, no margins, no coefficients.**
2. **Coefficients live in the Excel.** `app_settings` holds defaults; per-DPGF overrides happen in the workbook.
3. **No silent writes.** Automated channels (`supplier_catalog`, `historical_devis`, `dpgf_return`) land in `ingestion_queue` first.
4. **Old prices are never lost.** Every change to `cost_ht` is logged in `price_history` (trigger).
5. **French content, English code.** UI labels & data are French; identifiers, comments, files are English.

---

## Resolved PRD ambiguities

The PRD has a few small inconsistencies; here's what we landed on:

| Topic | Decision |
|---|---|
| **Labor norm seed count** | 17 rows (16 task-specific + 1 fallback `Norme par défaut (à classifier)`). PRD §15.1's "16" is off by one. |
| **Product families seed count** | 16 rows. |
| **`bordereau_endpoint_path` setting** | Documentation only; the path is hardcoded in FastAPI (`/api/bordereau.csv`) and Caddy. |
| **`dpgf_exports` table** | Created from schema verbatim. No Streamlit page in the prototype (per §16). |
| **`current_user` in commit step** | We use the `STREAMLIT_AUTH_USER` env var as `recorded_by` / `reviewed_by`. |
| **`llm_model` hot-reload** | `lib/gemini.py` reads `app_settings.llm_model` per call. Editing in Settings page takes effect immediately. |
| **Source name for invoice ingestion** | `'supplier_catalog'`, per §10.8 (schema-aligned vocabulary). |
| **Initial price logging on INSERT** | Trigger fires only on `UPDATE OF cost_ht`. The ingest page manually inserts a `price_history` row when creating a new product. |
| **Gemini model fallback** | If `app_settings.llm_model` returns 404/NotFound, `lib/gemini.py` retries with `gemini-3-flash-preview` (the current Gemini 3 Flash tier) and logs a warning. |
| **PDF input fallback** | If direct PDF input is rejected, `lib/gemini.py` rasterizes via `pypdfium2` and resends as PNG image parts (max 12 pages). |

---

## Acceptance tests (PRD §15)

| Section | Test | How to verify |
|---|---|---|
| 15.1 | `docker compose up -d` boots cleanly | `docker compose ps` |
| 15.1 | Schema + seed applied on first boot | `docker compose exec postgres psql -U mr_app -d merci_raymond -c "SELECT count(*) FROM labor_norms;"` → 17 |
| 15.1 | `/api/health` returns `{"ok": true}` | `curl -k https://localhost/api/health` |
| 15.1 | `/api/bordereau.csv` rejects without key | `curl -k -i https://localhost/api/bordereau.csv` → 401 |
| 15.1 | `/api/bordereau.csv` works with key | `curl -k -H "X-API-Key: $KEY" https://localhost/api/bordereau.csv` |
| 15.2 | `cost_ht = -1` rejected | Try in Streamlit Products page |
| 15.2 | `cost_ht` update writes `price_history` | Edit a product's cost, then check Page 3 history expander |
| 15.2 | Delete supplier with products → blocked | Try in Streamlit Suppliers page |
| 15.3 | Add supplier / product / edit cost | Streamlit pages 2 & 3 |
| 15.4 | Upload invoice → extract → review → commit | Page 5; needs a real PDF in `data/invoices/` |
| 15.5 | Non-PDF rejected, duplicate detected, bad key handled | Page 5 error paths |
| 15.6 | Power Query pulls CSV | Excel side-of-the-house |

---

## Operational notes

- **Init scripts run only on first boot** (when `postgres_data` is empty). To re-init, drop the volume: `docker compose down -v` (warning: destroys all data).
- **Postgres has no host port** — it's reachable only by the other containers. Add Adminer if you need GUI access from the host.
- **`data/invoices/`** is bind-mounted (not a named volume) so PDFs are visible on the host filesystem for backup/inspection.
- **Logs**: `docker compose logs -f <service>`.

## Post-deploy checklist (Taddeo / Vincent)

These steps are NOT for Claude Code — they go here so the human deployer knows what to do.

1. Point DNS for the production domain at the VPS public IP.
2. Generate `STREAMLIT_AUTH_PASSWORD_HASH` via:
   ```
   docker run --rm caddy:2-alpine caddy hash-password
   ```
   (or via Python: `python3 -c "import bcrypt; print(bcrypt.hashpw(b'<pwd>', bcrypt.gensalt()).decode())"`)
3. Generate `BORDEREAU_API_KEY` via `openssl rand -hex 32`.
4. Get a Gemini API key from Google AI Studio.
5. Fill `.env` from `.env.template`.
6. `docker compose up -d`.
7. Visit `/api/health`, then log into `/`.
8. Set up Vincent's Excel template (see Power Query section above).
