-- =============================================================================
-- Merci Raymond — Pricing DB schema v1
-- =============================================================================
-- Naming convention: Vincent's DPGF column vocabulary is authoritative.
-- heure_u_decharge, heure_u_pose, nombre_uth, fourniture_u map 1:1 to the
-- J, L, N, S cells PM fills in the working DPGF (see project_dpgf_structure.md).
--
-- Core rule: this DB stores COSTS ONLY. Sale prices, margins, and project
-- coefficients never land here — they live in the DPGF at quote time.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. SUPPLIERS
-- -----------------------------------------------------------------------------
CREATE TABLE suppliers (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL,
    contact_name    TEXT,
    contact_email   TEXT,
    contact_phone   TEXT,
    address         TEXT,
    category        TEXT,               -- free-text tag, e.g. "pépiniériste", "matériaux minéraux"
    payment_terms   TEXT,               -- e.g. "30j fin de mois"
    rating          SMALLINT CHECK (rating BETWEEN 0 AND 5),
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (name)
);

-- -----------------------------------------------------------------------------
-- 2. LABOR_NORMS (Temps humain — 3-tier defaults per task)
-- -----------------------------------------------------------------------------
-- Stores defaults the PM loads into the DPGF; PM overrides per line when needed.
-- Tier 1/2/3 correspond to acheminement difficulty (e.g. rez-de-chaussée,
-- étage courant, toiture) — Vincent picks final labels.
-- -----------------------------------------------------------------------------
CREATE TABLE labor_norms (
    id                          SERIAL PRIMARY KEY,
    task_name                   TEXT NOT NULL,        -- e.g. "Plantation arbre 10/12"
    unit_type                   TEXT NOT NULL,        -- "u", "m3", "ml", "m2", "Ft"
    nombre_uth_default          NUMERIC(4,2) NOT NULL DEFAULT 1,   -- N on DPGF
    heure_u_pose_default        NUMERIC(6,3) NOT NULL,             -- L on DPGF (fixed install time/u)
    tier_1_label                TEXT NOT NULL DEFAULT 'facile',    -- acheminement tier 1
    tier_1_heure_u_decharge     NUMERIC(6,3) NOT NULL,             -- J on DPGF, tier 1
    tier_2_label                TEXT NOT NULL DEFAULT 'moyen',     -- acheminement tier 2
    tier_2_heure_u_decharge     NUMERIC(6,3) NOT NULL,             -- J on DPGF, tier 2
    tier_3_label                TEXT NOT NULL DEFAULT 'difficile', -- acheminement tier 3
    tier_3_heure_u_decharge     NUMERIC(6,3) NOT NULL,             -- J on DPGF, tier 3
    notes                       TEXT,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (task_name)
);

-- -----------------------------------------------------------------------------
-- 3. PRODUCT_FAMILIES (optional grouping — "all composts", "all tree stakes")
-- -----------------------------------------------------------------------------
CREATE TABLE product_families (
    id              SERIAL PRIMARY KEY,
    name            TEXT NOT NULL UNIQUE,              -- e.g. "Compost végétal"
    description     TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- -----------------------------------------------------------------------------
-- 4. PRODUCTS (the central price list — one row per SKU)
-- -----------------------------------------------------------------------------
-- Packaging is a first-class column. A BigBag of compost and a Vrac compost
-- are two separate rows that share a family via family_id.
--
-- cost_ht is the raw SUPPLIER cost HT per unit. It is never a sale price.
-- -----------------------------------------------------------------------------
CREATE TABLE products (
    id                  SERIAL PRIMARY KEY,
    reference_name      TEXT NOT NULL,          -- e.g. "Chêne commun 10/12"
    family_id           INTEGER REFERENCES product_families(id) ON DELETE SET NULL,  -- "Type de produit"
    supplier_id         INTEGER NOT NULL REFERENCES suppliers(id) ON DELETE RESTRICT,
    labor_norm_id       INTEGER NOT NULL REFERENCES labor_norms(id) ON DELETE RESTRICT,
    brand               TEXT,                   -- Marque (e.g. "Truffaut", "Pépinières Levavasseur")
    material            TEXT,                   -- Matériaux (e.g. "végétal", "minéral", "bois", "métal")
    packaging           TEXT NOT NULL,          -- "BigBag", "Sac 50L", "Vrac", "U", "Godet", etc.
    unit_type           TEXT NOT NULL,          -- "u", "m3", "ml" — redundancy with labor_norms is intentional; sanity check at ingestion
    cost_ht             NUMERIC(10,2) NOT NULL CHECK (cost_ht >= 0),
    cost_currency       CHAR(3) NOT NULL DEFAULT 'EUR',
    quality_rating      SMALLINT CHECK (quality_rating BETWEEN 0 AND 5),   -- star column
    attributes          JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- attributes holds the LONG TAIL: properties that only apply to some product
    -- types (granularité for terre, hauteur/circonférence for arbres, capacité
    -- for équipements). Promote any attribute to a flat column if it ends up
    -- applying to most products or you start filtering on it routinely.
    -- Example: {"granularité": "0/10", "hauteur": "200/250", "essence": "Chêne"}
    notes               TEXT,
    last_price_update   TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    is_active           BOOLEAN NOT NULL DEFAULT TRUE,
    UNIQUE (reference_name, packaging, supplier_id)
    -- Note: "Origine du prix" is NOT a column here — it's tracked per-change
    -- in price_history.source. Query the latest history row to find current origin.
);

CREATE INDEX idx_products_family ON products(family_id);
CREATE INDEX idx_products_supplier ON products(supplier_id);
CREATE INDEX idx_products_labor_norm ON products(labor_norm_id);
CREATE INDEX idx_products_brand ON products(brand);
CREATE INDEX idx_products_material ON products(material);
CREATE INDEX idx_products_name_trgm ON products USING gin (reference_name gin_trgm_ops);
CREATE INDEX idx_products_attributes ON products USING gin (attributes);
-- trigram index = Stage A of the ingestion matching pipeline (candidate retrieval).
-- jsonb index = supports Stage B scoring on structured attributes.
-- Stage C uses an LLM for candidates scoring between LOW and HIGH thresholds.
-- (Requires: CREATE EXTENSION IF NOT EXISTS pg_trgm;)

-- -----------------------------------------------------------------------------
-- 5. PRICE_HISTORY (every cost_ht change is append-only logged here)
-- -----------------------------------------------------------------------------
-- Enables the trend arrow and the 6mo/9mo freshness indicator in Notion.
-- Also the audit trail for reverse-ingested prices from returned DPGFs.
-- -----------------------------------------------------------------------------
CREATE TABLE price_history (
    id                  BIGSERIAL PRIMARY KEY,
    product_id          INTEGER NOT NULL REFERENCES products(id) ON DELETE CASCADE,
    cost_ht             NUMERIC(10,2) NOT NULL,
    cost_currency       CHAR(3) NOT NULL DEFAULT 'EUR',
    source              TEXT NOT NULL,          -- "admin_streamlit", "supplier_catalog", "historical_devis", "dpgf_return"
    source_reference    TEXT,                   -- filename, DPGF ID, catalog name, etc.
    recorded_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    recorded_by         TEXT                    -- user email / system actor
);

CREATE INDEX idx_price_history_product_time ON price_history(product_id, recorded_at DESC);

-- -----------------------------------------------------------------------------
-- 6. INGESTION_QUEUE (review-before-commit buffer for AUTOMATED channels only)
-- -----------------------------------------------------------------------------
-- Channels that use this queue:
--   - supplier_catalog  (OCR/LLM-parsed supplier PDFs or Excels)
--   - historical_devis  (bulk import of past devis)
--   - dpgf_return       (re-ingesting S cells from returned DPGFs)
-- Channels that BYPASS this queue (direct INSERT/UPDATE on products):
--   - admin_streamlit   (manual entry is already human-verified; no review step)
-- A human reviews rows in Streamlit, then approves → the row gets upserted into
-- products (+ a price_history entry) atomically.
-- -----------------------------------------------------------------------------
CREATE TABLE ingestion_queue (
    id                      BIGSERIAL PRIMARY KEY,
    source                  TEXT NOT NULL,          -- channel tag (same vocab as price_history.source)
    source_reference        TEXT,                   -- filename, email id, DPGF id
    raw_payload             JSONB NOT NULL,         -- the raw extracted blob from OCR/LLM
    candidate_reference_name TEXT,
    candidate_family_hint   TEXT,
    candidate_packaging     TEXT,
    candidate_unit_type     TEXT,
    candidate_supplier_id   INTEGER REFERENCES suppliers(id),
    candidate_supplier_hint TEXT,                   -- free-text if supplier not yet resolved
    candidate_labor_norm_id INTEGER REFERENCES labor_norms(id),
    candidate_labor_hint    TEXT,
    candidate_cost_ht       NUMERIC(10,2),
    matched_product_id      INTEGER REFERENCES products(id), -- NULL = new product; set = update
    status                  TEXT NOT NULL DEFAULT 'pending'
                            CHECK (status IN ('pending', 'approved', 'rejected', 'needs_info')),
    review_notes            TEXT,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    reviewed_at             TIMESTAMPTZ,
    reviewed_by             TEXT
);

CREATE INDEX idx_ingestion_queue_status ON ingestion_queue(status, created_at);

-- -----------------------------------------------------------------------------
-- 6b. DPGF_EXPORTS (audit log of every generated DPGF + coefficient snapshot)
-- -----------------------------------------------------------------------------
-- When a DPGF is generated for a project, log it here with the coefficients
-- that were applied. When the file comes back filled in, the reverse-ingestion
-- pipeline can verify row 8 matches what we sent (if it differs, Vincent
-- overrode coefficients in-place — fine, but worth recording).
-- -----------------------------------------------------------------------------
CREATE TABLE dpgf_exports (
    id                  BIGSERIAL PRIMARY KEY,
    export_reference    TEXT NOT NULL,           -- project name / quote ID / filename hint
    coefficients_snapshot JSONB NOT NULL,        -- {"O8": 36, "P8": 0.10, "Y8": 1.02, ...}
    product_ids         INTEGER[] NOT NULL,      -- which products were embedded in this DPGF
    exported_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    exported_by         TEXT,
    notes               TEXT
);

CREATE INDEX idx_dpgf_exports_time ON dpgf_exports(exported_at DESC);

-- -----------------------------------------------------------------------------
-- 7. APP_SETTINGS (simple KV for default coefficients + misc config)
-- -----------------------------------------------------------------------------
-- Project-level coefficients (row 8 of Vincent's DPGF) live HERE as defaults.
-- Each DPGF export reads these as the starting values; PM can edit per project
-- directly in the Excel — the DB is never the source of truth for a given
-- quote's coefficients, only for the defaults.
-- -----------------------------------------------------------------------------
CREATE TABLE app_settings (
    key         TEXT PRIMARY KEY,
    value       TEXT NOT NULL,
    notes       TEXT,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO app_settings (key, value, notes) VALUES
    ('default_hourly_rate',         '36',       'O8 — €/h labor rate'),
    ('default_safety_margin',       '0.10',     'P8 — labor security margin'),
    ('default_install_chantier',    '1.02',     'Y8 — install chantier markup'),
    ('default_log_gestion',         '1.06',     'Z8 — logistics + gestion markup'),
    ('default_loc_livr_margin',     '1.5',      'AA8 — rental/delivery margin'),
    ('default_humain_margin',       '1.8',      'AB8 — labor margin'),
    ('default_fourn_gest_margin',   '1.375',    'AC8 — supply + gestion margin'),
    ('matching_threshold_high',     '0.90',     'Ingestion matcher: auto-match cutoff (≥ this = auto-match)'),
    ('matching_threshold_low',      '0.50',     'Ingestion matcher: LLM escalation floor (below = "new product")'),
    ('llm_provider',                'gemini',   'LLM provider for Stage C matching + OCR/parse tasks'),
    ('llm_model',                   'gemini-3.1-pro-preview', 'Model identifier; can be swapped without code change'),
    ('bordereau_endpoint_path',     '/api/bordereau.csv', 'Path Streamlit serves the live bordereau on (Power Query target)');

-- -----------------------------------------------------------------------------
-- 8. Update-timestamp triggers (keep updated_at fresh automatically)
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION touch_updated_at() RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = now();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER suppliers_touch      BEFORE UPDATE ON suppliers      FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER labor_norms_touch    BEFORE UPDATE ON labor_norms    FOR EACH ROW EXECUTE FUNCTION touch_updated_at();
CREATE TRIGGER products_touch       BEFORE UPDATE ON products       FOR EACH ROW EXECUTE FUNCTION touch_updated_at();

-- -----------------------------------------------------------------------------
-- 9. Price-change trigger: any cost_ht change automatically logs to price_history
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION log_price_change() RETURNS TRIGGER AS $$
BEGIN
    IF OLD.cost_ht IS DISTINCT FROM NEW.cost_ht THEN
        INSERT INTO price_history(product_id, cost_ht, cost_currency, source, recorded_by)
        VALUES (NEW.id, NEW.cost_ht, NEW.cost_currency,
                COALESCE(current_setting('app.ingestion_source', true), 'direct_update'),
                COALESCE(current_setting('app.ingestion_actor', true), 'system'));
        NEW.last_price_update = now();
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER products_price_log BEFORE UPDATE OF cost_ht ON products
    FOR EACH ROW EXECUTE FUNCTION log_price_change();

-- -----------------------------------------------------------------------------
-- 10. View: products_enriched — the read model Notion will mirror daily
-- -----------------------------------------------------------------------------
CREATE OR REPLACE VIEW products_enriched AS
SELECT
    p.id,
    p.reference_name,
    pf.name                             AS family_name,
    p.brand,
    p.material,
    p.packaging,
    p.unit_type,
    p.attributes,
    p.cost_ht,
    p.cost_currency,
    s.name                              AS supplier_name,
    s.rating                            AS supplier_rating,
    ln.task_name                        AS labor_task,
    ln.heure_u_pose_default,
    ln.nombre_uth_default,
    ln.tier_1_label, ln.tier_1_heure_u_decharge,
    ln.tier_2_label, ln.tier_2_heure_u_decharge,
    ln.tier_3_label, ln.tier_3_heure_u_decharge,
    p.quality_rating,
    p.last_price_update,
    EXTRACT(MONTH FROM AGE(now(), p.last_price_update)) AS months_since_update,
    CASE
        WHEN p.last_price_update < now() - INTERVAL '9 months' THEN 'stale_9mo'
        WHEN p.last_price_update < now() - INTERVAL '6 months' THEN 'stale_6mo'
        ELSE 'fresh'
    END                                 AS freshness_status,
    p.is_active
FROM products p
LEFT JOIN product_families pf ON pf.id = p.family_id
JOIN suppliers s               ON s.id = p.supplier_id
JOIN labor_norms ln            ON ln.id = p.labor_norm_id
WHERE p.is_active = TRUE;

-- =============================================================================
-- Notes for next iteration:
--   - Add a `product_categories` lookup table if families alone aren't enough
--     for Notion filters (e.g. "arbres", "arbustes", "vivaces", "matériaux").
--   - Add user / role table once auth is wired to Streamlit.
--   - Add `dpgf_exports` table to log every generated DPGF with its coefficient
--     snapshot — needed for the reverse-ingestion pipeline to know which
--     coefficients were applied on the returned sheet.
-- =============================================================================
