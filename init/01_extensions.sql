-- Postgres extensions used by the schema.
-- pg_trgm: trigram similarity for fuzzy product-name matching (Stage A of the matcher)
-- unaccent: strip diacritics so 'Chêne' matches 'Chene' in the matcher
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS unaccent;
