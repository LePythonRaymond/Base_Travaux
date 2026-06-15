-- =============================================================================
-- Merci Raymond — Pricing DB · migration 06: DPGF project history
-- =============================================================================
-- Persists every re-ingested (signed/finished) DPGF: the .xlsx itself, the
-- computed + recap rentability stats, and the project coefficient snapshot.
-- Links each price_history row back to the project it came from, and stores
-- the full per-line coefficient breakdown for client-price entries.
--
-- Idempotent (IF NOT EXISTS / ADD COLUMN IF NOT EXISTS) so it can be applied
-- to an already-running DB as well as a fresh install.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- dpgf_projects — one row per re-ingested DPGF (kept forever)
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS dpgf_projects (
    id              BIGSERIAL PRIMARY KEY,
    project_name    TEXT,
    filename        TEXT,
    file_bytes      BYTEA,                 -- the uploaded .xlsx, kept forever
    file_sha256     TEXT,                  -- dedup / idempotency hint
    imported_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    imported_by     TEXT,
    n_lines         INTEGER NOT NULL DEFAULT 0,
    n_matched       INTEGER NOT NULL DEFAULT 0,
    n_created       INTEGER NOT NULL DEFAULT 0,
    -- Project coefficient snapshot read from the DPGF header
    -- (taux_horaire, securite_humain, install_chantier, log_gestion,
    --  loc_livr_marge, humain_marge, fourn_marge).
    coefficients    JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- App-computed cross-check, summed from the ingested lines:
    -- {prix_vente, prix_revient, marge_eur, marge_pct, kv}. No longer the
    -- canonical figure — see `recap`.
    stats           JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- AUTHORITATIVE rentability read from the DPGF's "Pilotage de rentabilité"
    -- tab (the sheet's own formulas — ground truth): GLOBAL {prix_vente,
    -- prix_revient, marge_eur, marge_pct, kv} + hors_sst.{…} + the Tps-chantier
    -- planning fields {tps_chantier, personnes, jours, semaines, mois}. Nullable;
    -- empty on older imports → the app falls back to `stats`.
    recap           JSONB,
    notes           TEXT
);

CREATE INDEX IF NOT EXISTS idx_dpgf_projects_time ON dpgf_projects(imported_at DESC);

-- -----------------------------------------------------------------------------
-- price_history — link to the source project + per-line coefficient breakdown
-- -----------------------------------------------------------------------------
-- Both columns are nullable so existing rows are unaffected. `source` stays
-- the discriminator: 'dpgf_return' = supplier cost via a DPGF (neutral/black),
-- 'dpgf_client_price' = client selling price (red). `breakdown` is only set on
-- client-price rows; it holds the cost components + each coefficient + KV +
-- the final PU that produced the price.
-- -----------------------------------------------------------------------------
ALTER TABLE price_history
    ADD COLUMN IF NOT EXISTS project_id BIGINT REFERENCES dpgf_projects(id) ON DELETE SET NULL;
ALTER TABLE price_history
    ADD COLUMN IF NOT EXISTS breakdown JSONB;

CREATE INDEX IF NOT EXISTS idx_price_history_project ON price_history(project_id);

-- -----------------------------------------------------------------------------
-- log_price_change — let the audit trigger stamp the supplier-cost row it
-- auto-inserts (on a cost_ht UPDATE) with the originating project + reference.
-- -----------------------------------------------------------------------------
-- The Retour-DPGF commit inserts the dpgf_projects row first, then issues
--   SELECT set_config('app.ingestion_project_id', <id>, true);
--   SELECT set_config('app.ingestion_reference',  <ref>, true);
-- so the supplier-cost row born from the products.cost_ht UPDATE carries the
-- same project_id (and a human reference) as the client-price row we insert
-- by hand. Both settings are transaction-local and default to NULL when unset,
-- so every other caller (manual edits, invoice ingestion) is unaffected.
-- -----------------------------------------------------------------------------
CREATE OR REPLACE FUNCTION public.log_price_change()
RETURNS trigger
LANGUAGE plpgsql
AS $function$
BEGIN
    IF OLD.cost_ht IS DISTINCT FROM NEW.cost_ht THEN
        INSERT INTO price_history(
            product_id, cost_ht, cost_currency, source,
            source_reference, recorded_by, project_id
        )
        VALUES (
            NEW.id, NEW.cost_ht, NEW.cost_currency,
            COALESCE(current_setting('app.ingestion_source', true), 'direct_update'),
            NULLIF(current_setting('app.ingestion_reference', true), ''),
            COALESCE(current_setting('app.ingestion_actor', true), 'system'),
            NULLIF(current_setting('app.ingestion_project_id', true), '')::bigint
        );
        NEW.last_price_update = now();
    END IF;
    RETURN NEW;
END;
$function$;
