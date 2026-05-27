-- =============================================================================
-- Post-PRD migration: per-plant-family average-price fallback
-- =============================================================================
-- Vincent's pricing workflow distinguishes between:
--   * exact match — the species is already in the catalogue and Vincent
--     wants its unit price;
--   * category-based fallback — the species isn't in the catalogue, but
--     Vincent still needs a defensible per-unit estimate based on the size
--     class (godet, conteneur XL, tige X/Y, calibre X/Y…).
--
-- We add a `size_class` flat column to products, populated by Gemini at
-- ingestion time and editable by the PM in Streamlit. Then we expose two
-- new views:
--   * products_averages         — one row per (family, size_class) over
--                                 the plant families when ≥ 2 active rows
--                                 exist, holding the AVG(cost_ht) and the
--                                 most-common labor_norm.
--   * products_with_averages    — UNION of real products and synthetic
--                                 averages, with an is_average flag.
-- The bordereau API serves products_with_averages so Vincent's Google
-- Sheets picker lists both real SKUs and the synthetic catalog-averages.
-- =============================================================================

-- 1. New flat column on products
ALTER TABLE products
    ADD COLUMN IF NOT EXISTS size_class TEXT;

CREATE INDEX IF NOT EXISTS idx_products_size_class
    ON products(family_id, size_class)
    WHERE size_class IS NOT NULL;

-- 2. Recreate products_enriched with the new size_class column
DROP VIEW IF EXISTS products_with_averages;
DROP VIEW IF EXISTS products_enriched;

CREATE VIEW products_enriched AS
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
    p.is_active,
    p.size_class
FROM products p
LEFT JOIN product_families pf ON pf.id = p.family_id
JOIN suppliers s               ON s.id = p.supplier_id
JOIN labor_norms ln            ON ln.id = p.labor_norm_id
WHERE p.is_active = TRUE;

-- 3. Synthetic-averages view (plant families only, N >= 2)
CREATE OR REPLACE VIEW products_averages AS
WITH plant_families AS (
    SELECT id
    FROM product_families
    WHERE name IN ('Arbre', 'Arbuste', 'Vivace', 'Graminée', 'Couvre-sol', 'Bulbe')
),
group_stats AS (
    SELECT
        p.family_id,
        p.size_class,
        p.unit_type,
        ROUND(AVG(p.cost_ht), 2)::NUMERIC(10,2) AS avg_cost_ht,
        MAX(p.last_price_update)                AS max_update,
        count(*)                                AS n_products
    FROM products p
    JOIN plant_families pf_p ON pf_p.id = p.family_id
    WHERE p.is_active = TRUE
      AND p.size_class IS NOT NULL
      AND p.size_class <> ''
    GROUP BY p.family_id, p.size_class, p.unit_type
    HAVING count(*) >= 2
),
mode_labor AS (
    -- Most-common labor_norm per (family_id, size_class).
    -- DISTINCT ON keeps the labor_norm_id with the highest usage count.
    SELECT DISTINCT ON (family_id, size_class)
        family_id,
        size_class,
        labor_norm_id
    FROM (
        SELECT family_id, size_class, labor_norm_id, count(*) AS n_using
        FROM products
        WHERE is_active = TRUE
          AND size_class IS NOT NULL
          AND size_class <> ''
        GROUP BY family_id, size_class, labor_norm_id
    ) t
    ORDER BY family_id, size_class, n_using DESC, labor_norm_id ASC
)
SELECT
    gs.family_id,
    gs.size_class,
    gs.unit_type,
    gs.avg_cost_ht,
    gs.max_update,
    gs.n_products,
    ml.labor_norm_id
FROM group_stats gs
JOIN mode_labor ml ON ml.family_id = gs.family_id
                  AND ml.size_class = gs.size_class;

-- 4. Final union: real products + synthetic averages, with is_average flag.
CREATE OR REPLACE VIEW products_with_averages AS
SELECT
    id,
    reference_name,
    family_name,
    brand,
    material,
    packaging,
    unit_type,
    attributes,
    cost_ht,
    cost_currency,
    supplier_name,
    supplier_rating,
    labor_task,
    heure_u_pose_default,
    nombre_uth_default,
    tier_1_label, tier_1_heure_u_decharge,
    tier_2_label, tier_2_heure_u_decharge,
    tier_3_label, tier_3_heure_u_decharge,
    quality_rating,
    last_price_update,
    months_since_update,
    freshness_status,
    is_active,
    size_class,
    FALSE AS is_average
FROM products_enriched

UNION ALL

SELECT
    NULL::INTEGER                                  AS id,
    'Moyenne catalogue (' || pa.n_products || ' produits)' AS reference_name,
    pf.name                                        AS family_name,
    NULL::TEXT                                     AS brand,
    NULL::TEXT                                     AS material,
    pa.size_class                                  AS packaging,
    pa.unit_type,
    '{}'::jsonb                                    AS attributes,
    pa.avg_cost_ht                                 AS cost_ht,
    'EUR'::CHAR(3)                                 AS cost_currency,
    '(catalogue moyen)'                            AS supplier_name,
    NULL::SMALLINT                                 AS supplier_rating,
    ln.task_name                                   AS labor_task,
    ln.heure_u_pose_default,
    ln.nombre_uth_default,
    ln.tier_1_label, ln.tier_1_heure_u_decharge,
    ln.tier_2_label, ln.tier_2_heure_u_decharge,
    ln.tier_3_label, ln.tier_3_heure_u_decharge,
    NULL::SMALLINT                                 AS quality_rating,
    pa.max_update                                  AS last_price_update,
    EXTRACT(MONTH FROM AGE(now(), pa.max_update))  AS months_since_update,
    CASE
        WHEN pa.max_update < now() - INTERVAL '9 months' THEN 'stale_9mo'
        WHEN pa.max_update < now() - INTERVAL '6 months' THEN 'stale_6mo'
        ELSE 'fresh'
    END                                            AS freshness_status,
    TRUE                                           AS is_active,
    pa.size_class,
    TRUE                                           AS is_average
FROM products_averages pa
JOIN product_families pf ON pf.id = pa.family_id
JOIN labor_norms ln       ON ln.id = pa.labor_norm_id;
