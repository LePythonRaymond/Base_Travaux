-- =============================================================================
-- Seed data — populated on first boot before the app starts.
-- =============================================================================
-- Required for FK constraints on the products table:
--   - product_families(id) for family_id
--   - labor_norms(id)      for labor_norm_id NOT NULL
--   - suppliers(id)        for supplier_id NOT NULL
-- The "Norme par défaut" labor_norm and "Fournisseur inconnu" supplier are
-- placeholders so ingestion can always succeed even when extraction is
-- low-confidence; PM reclassifies later in Streamlit.
-- =============================================================================

-- ----- product_families (16 rows) -----
INSERT INTO product_families (name) VALUES
    ('Arbre'),
    ('Arbuste'),
    ('Vivace'),
    ('Graminée'),
    ('Couvre-sol'),
    ('Bulbe'),
    ('Terre végétale'),
    ('Substrat / amendement'),
    ('Compost'),
    ('Paillage minéral'),
    ('Paillage végétal'),
    ('Géotextile'),
    ('Tuteur / piquet'),
    ('Arrosage / irrigation'),
    ('Minéral (gravier, pierre)'),
    ('Mobilier extérieur');

-- ----- labor_norms (16 task-specific + 1 fallback = 17 rows) -----
-- Realistic prototype values. Refine with Vincent before going live.
INSERT INTO labor_norms (
    task_name, unit_type, nombre_uth_default, heure_u_pose_default,
    tier_1_heure_u_decharge, tier_2_heure_u_decharge, tier_3_heure_u_decharge,
    notes
) VALUES
    ('Plantation arbre tige (8/10–10/12)', 'u',  2, 0.500, 0.300, 0.600, 1.200, 'Default — refine with Vincent'),
    ('Plantation arbre fort (12/14+)',     'u',  3, 1.000, 0.500, 1.000, 2.000, 'Default — refine with Vincent'),
    ('Plantation arbre cépée',             'u',  2, 0.800, 0.400, 0.800, 1.500, 'Default — refine with Vincent'),
    ('Plantation arbuste conteneur 3-7L',  'u',  1, 0.100, 0.050, 0.100, 0.200, 'Default — refine with Vincent'),
    ('Plantation arbuste conteneur 10L+',  'u',  1, 0.200, 0.100, 0.200, 0.400, 'Default — refine with Vincent'),
    ('Plantation vivace godet',            'u',  1, 0.030, 0.010, 0.030, 0.060, 'Default — refine with Vincent'),
    ('Plantation graminée godet',          'u',  1, 0.040, 0.020, 0.040, 0.080, 'Default — refine with Vincent'),
    ('Pose terre végétale vrac',           'm3', 2, 0.500, 0.300, 0.500, 1.000, 'Default — refine with Vincent'),
    ('Pose terre végétale BigBag',         'm3', 2, 0.400, 0.200, 0.400, 0.800, 'Default — refine with Vincent'),
    ('Pose substrat / amendement',         'm3', 2, 0.500, 0.300, 0.500, 1.000, 'Default — refine with Vincent'),
    ('Pose paillage minéral',              'm2', 1, 0.050, 0.020, 0.050, 0.100, 'Default — refine with Vincent'),
    ('Pose paillage végétal',              'm2', 1, 0.040, 0.020, 0.040, 0.080, 'Default — refine with Vincent'),
    ('Pose géotextile',                    'm2', 1, 0.020, 0.010, 0.020, 0.040, 'Default — refine with Vincent'),
    ('Pose tuteur / piquet',               'u',  1, 0.100, 0.050, 0.100, 0.200, 'Default — refine with Vincent'),
    ('Pose réseau arrosage',               'ml', 1, 0.050, 0.020, 0.050, 0.100, 'Default — refine with Vincent'),
    ('Travaux divers (forfait)',           'Ft', 2, 1.000, 0.500, 1.000, 2.000, 'Default — refine with Vincent'),
    ('Norme par défaut (à classifier)',    'u',  1, 0.100, 0.050, 0.100, 0.200,
     'Fallback used by ingestion when no confident labor norm match — reassign manually in Products page.');

-- ----- suppliers (1 placeholder row) -----
INSERT INTO suppliers (name, category, notes) VALUES
    ('Fournisseur inconnu',
     'placeholder',
     'Used by ingestion when supplier extraction fails — should be reassigned manually.');
