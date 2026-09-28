-- 004_schema_integrity_fixes.sql
-- Phase 0 SCHEMA/DATA-INTEGRITY fixes (idempotent; safe to re-run on existing prod DBs)
--
-- 1) jp_cards.set_total was written by the JP crawlers but never created.
--    Crawler evidence:
--      backend/services/crawler/jp_crawler.py:124   set_total: str = ""   # e.g. "081"
--      backend/services/crawler/jp_crawler.py:256   card.set_total = cn_match.group(2)
--      backend/services/crawler/jp_crawler.py:582,613,631 INSERT/UPDATE set_total
--      backend/services/crawler/limitless_jp_crawler.py:412,485,515,540,653
--                                                 card['set_total'] = str(card_count)
--    Expected type is TEXT/VARCHAR (string, not an integer).
-- 2) deck_cards.deck_id FK must be ON DELETE CASCADE (matches backend/init_db.py).
-- 3) deck_search_index needs UNIQUE(deck_id, card_name) for `INSERT ... ON CONFLICT DO NOTHING`
--    (backend/services/deck_importer/rebuild_search_index.py:55,65 and deck_updater.py:546),
--    so existing duplicates must be removed first.
-- 4) cards(name) and cards(set_code) indexes missing from runtime schema (present in backend/init_db.py).
--
-- 執行方式（VPS）：
--   docker exec -i ptcg_db psql -U ptcg -d ptcg_db -f - < backend/migrations/004_schema_integrity_fixes.sql

BEGIN;

-- 1) jp_cards.set_total
ALTER TABLE jp_cards ADD COLUMN IF NOT EXISTS set_total VARCHAR;

-- 2) deck_cards.deck_id -> imported_decks(id) ON DELETE CASCADE
DO $$
DECLARE r RECORD;
BEGIN
    IF to_regclass('public.deck_cards') IS NULL
       OR to_regclass('public.imported_decks') IS NULL THEN
        RETURN;
    END IF;
    -- drop non-cascade FKs on deck_cards.deck_id (e.g. legacy implied NO ACTION)
    FOR r IN
        SELECT c.conname
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
        WHERE t.relname = 'deck_cards'
          AND c.contype = 'f'
          AND a.attname = 'deck_id'
          AND c.confdeltype <> 'c'
    LOOP
        EXECUTE format('ALTER TABLE deck_cards DROP CONSTRAINT %I', r.conname);
    END LOOP;
    -- add cascade FK if none exists
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint c
        JOIN pg_class t ON t.oid = c.conrelid
        JOIN pg_attribute a ON a.attrelid = t.oid AND a.attnum = ANY(c.conkey)
        WHERE t.relname = 'deck_cards'
          AND c.contype = 'f'
          AND a.attname = 'deck_id'
          AND c.confdeltype = 'c'
    ) THEN
        ALTER TABLE deck_cards
            ADD CONSTRAINT deck_cards_deck_id_fkey
            FOREIGN KEY (deck_id) REFERENCES imported_decks(id) ON DELETE CASCADE NOT VALID;
    END IF;
END $$;

-- 3) deck_search_index: de-duplicate then enforce UNIQUE(deck_id, card_name)
DELETE FROM deck_search_index a
USING deck_search_index b
WHERE a.deck_id = b.deck_id
  AND a.card_name = b.card_name
  AND a.ctid < b.ctid;

CREATE UNIQUE INDEX IF NOT EXISTS uq_deck_search_index_deck_card
    ON deck_search_index (deck_id, card_name);

-- 4) cards indexes missing from runtime schema
CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name);
CREATE INDEX IF NOT EXISTS idx_cards_set_code ON cards(set_code);

COMMIT;
