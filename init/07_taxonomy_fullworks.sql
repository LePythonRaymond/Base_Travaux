-- =============================================================================
-- Full-works taxonomy expansion (pre-launch DPGF corpus load)
-- =============================================================================
-- The seed (03_seed.sql) had 16 plant-centric families. Mining Vincent's worked
-- DPGFs showed the real scope is FULL WORKS — études, terrassement/VRD, drainage,
-- étanchéité, revêtements, bacs, mobilier, treillage, biodiversité, entretien, …
-- This migration adds the new top-level families (idempotent) so the Streamlit
-- cascade and the À-classifier page offer them. Sub-categories + packagings grow
-- organically as the corpus load and the team encounter them (the loader calls
-- ensure_taxonomy per approved triplet). Existing plant families are untouched.
--
-- Naming is aligned with tools/dpgf_corpus_etl/classify.py so the loader's
-- product.family values map 1:1 to a real product_families row.
-- =============================================================================

INSERT INTO product_families (name) VALUES
    ('Grimpante'),
    ('Semis / engazonnement'),
    ('Paillage'),                              -- supersedes the split Paillage minéral/végétal
    ('Drainage'),
    ('Accessoire de plantation'),             -- supersedes Tuteur / piquet (broader)
    ('Études & honoraires'),
    ('Installation de chantier'),
    ('Terrassement / VRD'),
    ('Dépose / démolition'),
    ('Étanchéité / protection toiture'),
    ('Revêtement de sol / maçonnerie'),
    ('Bordure / élément linéaire'),
    ('Bac / jardinière'),
    ('Clôture / treillage / support'),
    ('Biodiversité / habitats'),
    ('Entretien / garantie / suivi cultural')
ON CONFLICT (name) DO NOTHING;

-- A handful of representative (subcategory, packaging) seeds per new family so the
-- cascade is usable on day 1. The composite FK requires the triplet to exist
-- before a product can reference it; the loader also ensures triplets on the fly.
INSERT INTO product_taxonomy (family_id, subcategory, packaging, created_by, notes)
SELECT pf.id, v.subcategory, v.packaging, 'migration', 'full-works seed'
FROM product_families pf
JOIN (VALUES
    ('Grimpante',                       'Sur support',       'Conteneur 3L'),
    ('Grimpante',                       'Sur support',       'Standard'),
    ('Semis / engazonnement',           'Prairie',           'kg'),
    ('Semis / engazonnement',           'Gazon',             'm2'),
    ('Paillage',                        'Minéral',           'Vrac'),
    ('Paillage',                        'Végétal',           'Vrac'),
    ('Drainage',                        'Couche drainante',  'm2'),
    ('Drainage',                        'Drain',             'ml'),
    ('Accessoire de plantation',        'Tuteurage',         'Unité'),
    ('Accessoire de plantation',        'Protection',        'Unité'),
    ('Études & honoraires',             'Études d''exécution','Forfait'),
    ('Installation de chantier',        'Installation',      'Forfait'),
    ('Terrassement / VRD',              'Terrassement',      'm3'),
    ('Terrassement / VRD',              'Fosse de plantation','Unité'),
    ('Dépose / démolition',             'Dépose',            'm2'),
    ('Étanchéité / protection toiture', 'Protection',        'm2'),
    ('Revêtement de sol / maçonnerie',  'Revêtement',        'm2'),
    ('Bordure / élément linéaire',      'Bordure',           'ml'),
    ('Bac / jardinière',                'Bac',               'Unité'),
    ('Clôture / treillage / support',   'Treillage',         'ml'),
    ('Biodiversité / habitats',         'Habitat',           'Unité'),
    ('Entretien / garantie / suivi cultural', 'Suivi cultural', 'Forfait'),
    ('Entretien / garantie / suivi cultural', 'Garantie de reprise', 'Forfait')
) AS v(family, subcategory, packaging) ON v.family = pf.name
ON CONFLICT (family_id, subcategory, packaging) DO NOTHING;
