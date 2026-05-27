-- =============================================================================
-- Post-04 migration: codified 3-level taxonomy (Famille → Sous-cat → Conditionnement)
-- =============================================================================
-- Replaces free-text `size_class` + free-text `packaging` with a strict
-- lookup table `product_taxonomy` and a composite FK from products. The
-- new model unifies plant size dimensions and physical packagings into
-- a single `packaging` column, gated by the taxonomy. Drift is impossible
-- by construction: any (family, subcategory, packaging) triplet on
-- products must exist in product_taxonomy first.
--
-- Existing rows are migrated into the special subcategory 'À classifier'
-- per family — they show up in the "À classifier" Streamlit page where
-- a human reclassifies them at their own pace. Nothing is lost; the FK
-- is enforced from day 1.
-- =============================================================================

-- -----------------------------------------------------------------------------
-- 1. The taxonomy table itself
-- -----------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS product_taxonomy (
    id              SERIAL PRIMARY KEY,
    family_id       INTEGER NOT NULL REFERENCES product_families(id) ON DELETE RESTRICT,
    subcategory     TEXT NOT NULL,
    packaging       TEXT NOT NULL,
    notes           TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT,                            -- STREAMLIT_AUTH_USER on app inserts; 'migration' on seed rows
    UNIQUE (family_id, subcategory, packaging)
);

CREATE INDEX IF NOT EXISTS idx_product_taxonomy_family
    ON product_taxonomy(family_id, subcategory);

-- -----------------------------------------------------------------------------
-- 2. Strawman seed — 2 real sous-catégories per famille + "À classifier"
-- -----------------------------------------------------------------------------
-- The packagings listed per (family, subcategory) come from prompts.py
-- conventions (plant sizes) and standard French paysagisme vocabulary
-- (non-plant vessels). Vincent will grow this via the "À classifier" page
-- as new combinations are encountered.
-- -----------------------------------------------------------------------------
INSERT INTO product_taxonomy (family_id, subcategory, packaging, created_by) VALUES
    -- Arbre
    ((SELECT id FROM product_families WHERE name='Arbre'),    'Tige',   'Tige 10/12',     'migration'),
    ((SELECT id FROM product_families WHERE name='Arbre'),    'Tige',   'Tige 12/14',     'migration'),
    ((SELECT id FROM product_families WHERE name='Arbre'),    'Tige',   'Tige 14/16',     'migration'),
    ((SELECT id FROM product_families WHERE name='Arbre'),    'Tige',   'Tige 16/18',     'migration'),
    ((SELECT id FROM product_families WHERE name='Arbre'),    'Tige',   'Tige 18/20',     'migration'),
    ((SELECT id FROM product_families WHERE name='Arbre'),    'Cépée',  'Cépée 125/150',  'migration'),
    ((SELECT id FROM product_families WHERE name='Arbre'),    'Cépée',  'Cépée 150/175',  'migration'),
    ((SELECT id FROM product_families WHERE name='Arbre'),    'Cépée',  'Cépée 175/200',  'migration'),
    ((SELECT id FROM product_families WHERE name='Arbre'),    'Cépée',  'Cépée 200/250',  'migration'),
    -- Arbuste
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Caduc',       'Conteneur 3L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Caduc',       'Conteneur 5L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Caduc',       'Conteneur 10L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Caduc',       'Conteneur 15L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Caduc',       'Motte',         'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Caduc',       'Racines nues',  'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Persistant',  'Conteneur 3L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Persistant',  'Conteneur 5L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Persistant',  'Conteneur 10L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Persistant',  'Conteneur 15L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Arbuste'),  'Persistant',  'Motte',         'migration'),
    -- Vivace
    ((SELECT id FROM product_families WHERE name='Vivace'),   'Persistante', 'Godet',         'migration'),
    ((SELECT id FROM product_families WHERE name='Vivace'),   'Persistante', 'Conteneur 1L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Vivace'),   'Persistante', 'Conteneur 2L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Vivace'),   'Caduque',     'Godet',         'migration'),
    ((SELECT id FROM product_families WHERE name='Vivace'),   'Caduque',     'Conteneur 1L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Vivace'),   'Caduque',     'Conteneur 2L',  'migration'),
    -- Graminée
    ((SELECT id FROM product_families WHERE name='Graminée'), 'Persistante', 'Godet',         'migration'),
    ((SELECT id FROM product_families WHERE name='Graminée'), 'Persistante', 'Conteneur 1L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Graminée'), 'Persistante', 'Conteneur 2L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Graminée'), 'Caduque',     'Godet',         'migration'),
    ((SELECT id FROM product_families WHERE name='Graminée'), 'Caduque',     'Conteneur 1L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Graminée'), 'Caduque',     'Conteneur 2L',  'migration'),
    -- Couvre-sol
    ((SELECT id FROM product_families WHERE name='Couvre-sol'), 'Persistant', 'Godet',        'migration'),
    ((SELECT id FROM product_families WHERE name='Couvre-sol'), 'Persistant', 'Conteneur 1L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Couvre-sol'), 'Caduc',      'Godet',        'migration'),
    ((SELECT id FROM product_families WHERE name='Couvre-sol'), 'Caduc',      'Conteneur 1L', 'migration'),
    -- Bulbe
    ((SELECT id FROM product_families WHERE name='Bulbe'), 'Printemps',     'Calibre 8/10',  'migration'),
    ((SELECT id FROM product_families WHERE name='Bulbe'), 'Printemps',     'Calibre 10/12', 'migration'),
    ((SELECT id FROM product_families WHERE name='Bulbe'), 'Printemps',     'Calibre 12/14', 'migration'),
    ((SELECT id FROM product_families WHERE name='Bulbe'), 'Été/Automne',   'Calibre 8/10',  'migration'),
    ((SELECT id FROM product_families WHERE name='Bulbe'), 'Été/Automne',   'Calibre 10/12', 'migration'),
    ((SELECT id FROM product_families WHERE name='Bulbe'), 'Été/Automne',   'Calibre 12/14', 'migration'),
    -- Terre végétale
    ((SELECT id FROM product_families WHERE name='Terre végétale'), 'Standard',   'BigBag',  'migration'),
    ((SELECT id FROM product_families WHERE name='Terre végétale'), 'Standard',   'Vrac',    'migration'),
    ((SELECT id FROM product_families WHERE name='Terre végétale'), 'Standard',   'Sac 50L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Terre végétale'), 'Drainante',  'BigBag',  'migration'),
    ((SELECT id FROM product_families WHERE name='Terre végétale'), 'Drainante',  'Vrac',    'migration'),
    ((SELECT id FROM product_families WHERE name='Terre végétale'), 'Drainante',  'Sac 50L', 'migration'),
    -- Substrat / amendement
    ((SELECT id FROM product_families WHERE name='Substrat / amendement'), 'Engrais',                'Sac 25L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Substrat / amendement'), 'Engrais',                'Sac 50L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Substrat / amendement'), 'Engrais',                'Bidon 5L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Substrat / amendement'), 'Amendement organique',   'BigBag',   'migration'),
    ((SELECT id FROM product_families WHERE name='Substrat / amendement'), 'Amendement organique',   'Sac 50L',  'migration'),
    ((SELECT id FROM product_families WHERE name='Substrat / amendement'), 'Amendement organique',   'Vrac',     'migration'),
    -- Compost
    ((SELECT id FROM product_families WHERE name='Compost'), 'Végétal', 'BigBag',  'migration'),
    ((SELECT id FROM product_families WHERE name='Compost'), 'Végétal', 'Sac 50L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Compost'), 'Végétal', 'Vrac',    'migration'),
    ((SELECT id FROM product_families WHERE name='Compost'), 'Mixte',   'BigBag',  'migration'),
    ((SELECT id FROM product_families WHERE name='Compost'), 'Mixte',   'Sac 50L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Compost'), 'Mixte',   'Vrac',    'migration'),
    -- Paillage minéral
    ((SELECT id FROM product_families WHERE name='Paillage minéral'), 'Roulé',     'BigBag',   'migration'),
    ((SELECT id FROM product_families WHERE name='Paillage minéral'), 'Roulé',     'Sac 25kg', 'migration'),
    ((SELECT id FROM product_families WHERE name='Paillage minéral'), 'Roulé',     'Vrac',     'migration'),
    ((SELECT id FROM product_families WHERE name='Paillage minéral'), 'Concassé',  'BigBag',   'migration'),
    ((SELECT id FROM product_families WHERE name='Paillage minéral'), 'Concassé',  'Sac 25kg', 'migration'),
    ((SELECT id FROM product_families WHERE name='Paillage minéral'), 'Concassé',  'Vrac',     'migration'),
    -- Paillage végétal
    ((SELECT id FROM product_families WHERE name='Paillage végétal'), 'Écorces', 'BigBag',  'migration'),
    ((SELECT id FROM product_families WHERE name='Paillage végétal'), 'Écorces', 'Sac 50L', 'migration'),
    ((SELECT id FROM product_families WHERE name='Paillage végétal'), 'Écorces', 'Vrac',    'migration'),
    ((SELECT id FROM product_families WHERE name='Paillage végétal'), 'Broyat',  'BigBag',  'migration'),
    ((SELECT id FROM product_families WHERE name='Paillage végétal'), 'Broyat',  'Vrac',    'migration'),
    -- Géotextile
    ((SELECT id FROM product_families WHERE name='Géotextile'), 'Tissé',      'Rouleau', 'migration'),
    ((SELECT id FROM product_families WHERE name='Géotextile'), 'Tissé',      'm²',      'migration'),
    ((SELECT id FROM product_families WHERE name='Géotextile'), 'Non-tissé',  'Rouleau', 'migration'),
    ((SELECT id FROM product_families WHERE name='Géotextile'), 'Non-tissé',  'm²',      'migration'),
    -- Tuteur / piquet
    ((SELECT id FROM product_families WHERE name='Tuteur / piquet'), 'Bois',   'Unité',     'migration'),
    ((SELECT id FROM product_families WHERE name='Tuteur / piquet'), 'Bois',   'Lot de 10', 'migration'),
    ((SELECT id FROM product_families WHERE name='Tuteur / piquet'), 'Métal',  'Unité',     'migration'),
    ((SELECT id FROM product_families WHERE name='Tuteur / piquet'), 'Métal',  'Lot de 10', 'migration'),
    -- Arrosage / irrigation
    ((SELECT id FROM product_families WHERE name='Arrosage / irrigation'), 'Goutte-à-goutte', 'Mètre linéaire', 'migration'),
    ((SELECT id FROM product_families WHERE name='Arrosage / irrigation'), 'Goutte-à-goutte', 'Bobine 100m',    'migration'),
    ((SELECT id FROM product_families WHERE name='Arrosage / irrigation'), 'Aspersion',       'Unité',          'migration'),
    ((SELECT id FROM product_families WHERE name='Arrosage / irrigation'), 'Aspersion',       'Forfait',        'migration'),
    -- Minéral (gravier, pierre)
    ((SELECT id FROM product_families WHERE name='Minéral (gravier, pierre)'), 'Gravier',     'BigBag',   'migration'),
    ((SELECT id FROM product_families WHERE name='Minéral (gravier, pierre)'), 'Gravier',     'Sac 25kg', 'migration'),
    ((SELECT id FROM product_families WHERE name='Minéral (gravier, pierre)'), 'Gravier',     'Vrac',     'migration'),
    ((SELECT id FROM product_families WHERE name='Minéral (gravier, pierre)'), 'Bloc / Pavé', 'Unité',    'migration'),
    ((SELECT id FROM product_families WHERE name='Minéral (gravier, pierre)'), 'Bloc / Pavé', 'm²',       'migration'),
    ((SELECT id FROM product_families WHERE name='Minéral (gravier, pierre)'), 'Bloc / Pavé', 'Palette',  'migration'),
    -- Mobilier extérieur
    ((SELECT id FROM product_families WHERE name='Mobilier extérieur'), 'Assise / Banc',     'Unité', 'migration'),
    ((SELECT id FROM product_families WHERE name='Mobilier extérieur'), 'Bac / Jardinière',  'Unité', 'migration')
ON CONFLICT (family_id, subcategory, packaging) DO NOTHING;

-- -----------------------------------------------------------------------------
-- 3. Add the new subcategory column (nullable for now — backfilled below)
-- -----------------------------------------------------------------------------
ALTER TABLE products ADD COLUMN IF NOT EXISTS subcategory TEXT;

-- -----------------------------------------------------------------------------
-- 4. Promote size_class → packaging where size_class is the more specific value.
--    Plants previously stored "Conteneur 5L" in size_class and a less-specific
--    string in packaging ("Conteneur"/"Pot"/empty). The new model puts the
--    size_class value into packaging directly.
-- -----------------------------------------------------------------------------
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name='products' AND column_name='size_class'
    ) THEN
        UPDATE products
           SET packaging = size_class
         WHERE size_class IS NOT NULL
           AND size_class <> ''
           AND (packaging IS NULL OR packaging = '' OR packaging IN ('Conteneur', 'Pot', 'Standard'));
    END IF;
END $$;

-- -----------------------------------------------------------------------------
-- 5. Seed safety net: ensure every existing (family_id, packaging) tuple has
--    an "À classifier" taxonomy row, so step 6's backfill never fails the FK.
-- -----------------------------------------------------------------------------
INSERT INTO product_taxonomy (family_id, subcategory, packaging, created_by, notes)
SELECT DISTINCT p.family_id, 'À classifier', p.packaging, 'migration',
       'Auto-créé pendant la migration 05 — produit existant à reclasser dans À classifier.'
  FROM products p
 WHERE p.family_id IS NOT NULL
   AND p.packaging IS NOT NULL
   AND p.packaging <> ''
ON CONFLICT (family_id, subcategory, packaging) DO NOTHING;

-- Products with no family_id can't be backfilled — they need a human to
-- assign a family first. Pick a placeholder family for them so the NOT NULL
-- constraint below can be enforced; the À classifier page will surface them.
DO $$
DECLARE
    placeholder_family_id INTEGER;
BEGIN
    -- Use "Mobilier extérieur" as a neutral placeholder (least likely to be
    -- a real plant misclassification). The À classifier page reassigns it.
    SELECT id INTO placeholder_family_id
      FROM product_families WHERE name = 'Mobilier extérieur';

    -- Make sure there's a taxonomy row for any orphan (NULL family) products.
    INSERT INTO product_taxonomy (family_id, subcategory, packaging, created_by, notes)
    SELECT DISTINCT placeholder_family_id, 'À classifier',
                    COALESCE(NULLIF(p.packaging, ''), 'Inconnu'),
                    'migration',
                    'Auto-créé pour produit orphelin (sans famille) — à reclasser.'
      FROM products p
     WHERE p.family_id IS NULL
    ON CONFLICT (family_id, subcategory, packaging) DO NOTHING;

    -- Then assign the placeholder family to those orphans.
    UPDATE products
       SET family_id = placeholder_family_id,
           packaging = COALESCE(NULLIF(packaging, ''), 'Inconnu')
     WHERE family_id IS NULL;
END $$;

-- -----------------------------------------------------------------------------
-- 6. Backfill all existing products to subcategory='À classifier'
-- -----------------------------------------------------------------------------
UPDATE products SET subcategory = 'À classifier' WHERE subcategory IS NULL;

-- -----------------------------------------------------------------------------
-- 7. Drop dependent objects + old size_class column, then enforce constraints
-- -----------------------------------------------------------------------------
DROP VIEW IF EXISTS products_with_averages;
DROP VIEW IF EXISTS products_averages;
DROP VIEW IF EXISTS products_enriched;
DROP INDEX IF EXISTS idx_products_size_class;

ALTER TABLE products DROP COLUMN IF EXISTS size_class;

ALTER TABLE products ALTER COLUMN subcategory SET NOT NULL;
ALTER TABLE products ALTER COLUMN family_id   SET NOT NULL;

ALTER TABLE products
    ADD CONSTRAINT fk_products_taxonomy
    FOREIGN KEY (family_id, subcategory, packaging)
    REFERENCES product_taxonomy(family_id, subcategory, packaging)
    DEFERRABLE INITIALLY IMMEDIATE;

CREATE INDEX idx_products_taxonomy
    ON products(family_id, subcategory, packaging);

-- -----------------------------------------------------------------------------
-- 8. Rebuild views with subcategory replacing size_class
-- -----------------------------------------------------------------------------
CREATE VIEW products_enriched AS
SELECT
    p.id,
    p.reference_name,
    pf.name                             AS family_name,
    p.subcategory,
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
JOIN product_families pf ON pf.id = p.family_id
JOIN suppliers s         ON s.id = p.supplier_id
JOIN labor_norms ln      ON ln.id = p.labor_norm_id
WHERE p.is_active = TRUE;

-- Plant-family averages: group by (family, subcategory, packaging) instead
-- of (family, size_class). Same plant-family allowlist and N>=2 threshold.
CREATE VIEW products_averages AS
WITH plant_families AS (
    SELECT id
      FROM product_families
     WHERE name IN ('Arbre', 'Arbuste', 'Vivace', 'Graminée', 'Couvre-sol', 'Bulbe')
),
group_stats AS (
    SELECT
        p.family_id,
        p.subcategory,
        p.packaging,
        p.unit_type,
        ROUND(AVG(p.cost_ht), 2)::NUMERIC(10,2) AS avg_cost_ht,
        MAX(p.last_price_update)                AS max_update,
        count(*)                                AS n_products
      FROM products p
      JOIN plant_families pf_p ON pf_p.id = p.family_id
     WHERE p.is_active = TRUE
       AND p.subcategory <> 'À classifier'   -- exclude triage bucket from averages
     GROUP BY p.family_id, p.subcategory, p.packaging, p.unit_type
    HAVING count(*) >= 2
),
mode_labor AS (
    SELECT DISTINCT ON (family_id, subcategory, packaging)
        family_id, subcategory, packaging, labor_norm_id
      FROM (
        SELECT family_id, subcategory, packaging, labor_norm_id, count(*) AS n_using
          FROM products
         WHERE is_active = TRUE
           AND subcategory <> 'À classifier'
         GROUP BY family_id, subcategory, packaging, labor_norm_id
      ) t
     ORDER BY family_id, subcategory, packaging, n_using DESC, labor_norm_id ASC
)
SELECT
    gs.family_id,
    gs.subcategory,
    gs.packaging,
    gs.unit_type,
    gs.avg_cost_ht,
    gs.max_update,
    gs.n_products,
    ml.labor_norm_id
  FROM group_stats gs
  JOIN mode_labor ml
    ON ml.family_id = gs.family_id
   AND ml.subcategory = gs.subcategory
   AND ml.packaging = gs.packaging;

-- Final union — real products + synthetic averages, with is_average flag.
CREATE VIEW products_with_averages AS
SELECT
    id, reference_name, family_name, subcategory, brand, material, packaging,
    unit_type, attributes, cost_ht, cost_currency, supplier_name, supplier_rating,
    labor_task, heure_u_pose_default, nombre_uth_default,
    tier_1_label, tier_1_heure_u_decharge,
    tier_2_label, tier_2_heure_u_decharge,
    tier_3_label, tier_3_heure_u_decharge,
    quality_rating, last_price_update, months_since_update, freshness_status,
    is_active,
    FALSE AS is_average
FROM products_enriched

UNION ALL

SELECT
    NULL::INTEGER                                              AS id,
    'Moyenne catalogue (' || pa.n_products || ' produits)'     AS reference_name,
    pf.name                                                    AS family_name,
    pa.subcategory,
    NULL::TEXT                                                 AS brand,
    NULL::TEXT                                                 AS material,
    pa.packaging,
    pa.unit_type,
    '{}'::jsonb                                                AS attributes,
    pa.avg_cost_ht                                             AS cost_ht,
    'EUR'::CHAR(3)                                             AS cost_currency,
    '(catalogue moyen)'                                        AS supplier_name,
    NULL::SMALLINT                                             AS supplier_rating,
    ln.task_name                                               AS labor_task,
    ln.heure_u_pose_default,
    ln.nombre_uth_default,
    ln.tier_1_label, ln.tier_1_heure_u_decharge,
    ln.tier_2_label, ln.tier_2_heure_u_decharge,
    ln.tier_3_label, ln.tier_3_heure_u_decharge,
    NULL::SMALLINT                                             AS quality_rating,
    pa.max_update                                              AS last_price_update,
    EXTRACT(MONTH FROM AGE(now(), pa.max_update))              AS months_since_update,
    CASE
        WHEN pa.max_update < now() - INTERVAL '9 months' THEN 'stale_9mo'
        WHEN pa.max_update < now() - INTERVAL '6 months' THEN 'stale_6mo'
        ELSE 'fresh'
    END                                                        AS freshness_status,
    TRUE                                                       AS is_active,
    TRUE                                                       AS is_average
FROM products_averages pa
JOIN product_families pf ON pf.id = pa.family_id
JOIN labor_norms ln      ON ln.id = pa.labor_norm_id;
