BEGIN;

CREATE TABLE IF NOT EXISTS golden_set (
    id BIGSERIAL PRIMARY KEY,
    case_id VARCHAR(128) NOT NULL UNIQUE,
    tw_card_id VARCHAR(64),
    jp_card_id VARCHAR(64),
    tw_name TEXT,
    jp_name TEXT,
    category VARCHAR(64) NOT NULL,
    difficulty VARCHAR(32) NOT NULL,
    source_language VARCHAR(8) NOT NULL DEFAULT 'jp',
    source_text TEXT NOT NULL,
    gold_predicates JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes TEXT,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_golden_set_category
    ON golden_set(category);

CREATE INDEX IF NOT EXISTS idx_golden_set_difficulty
    ON golden_set(difficulty);

CREATE INDEX IF NOT EXISTS idx_golden_set_gold_predicates
    ON golden_set USING GIN (gold_predicates);

ALTER TABLE processed_cards
    ADD COLUMN IF NOT EXISTS predicates JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS confidence NUMERIC(5, 4),
    ADD COLUMN IF NOT EXISTS extractor_version VARCHAR(64) NOT NULL DEFAULT 'uninitialized',
    ADD COLUMN IF NOT EXISTS source_language VARCHAR(8) NOT NULL DEFAULT 'jp',
    ADD COLUMN IF NOT EXISTS source_card_id VARCHAR(64),
    ADD COLUMN IF NOT EXISTS source_text_hash VARCHAR(64),
    ADD COLUMN IF NOT EXISTS validation_errors JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS last_verified_at TIMESTAMP WITHOUT TIME ZONE;

CREATE INDEX IF NOT EXISTS idx_processed_cards_predicates
    ON processed_cards USING GIN (predicates);

CREATE INDEX IF NOT EXISTS idx_processed_cards_source_text_hash
    ON processed_cards(source_text_hash);

COMMENT ON TABLE golden_set IS
    'Human-authored JP canonical fixtures for structured card logic extraction evaluation.';

COMMENT ON COLUMN processed_cards.logic_json IS
    'Legacy text payload. New structured extraction should write predicates JSONB and keep processed_cards as the single source of truth.';

COMMENT ON COLUMN processed_cards.predicates IS
    'Verified structured predicates extracted from JP canonical card text.';

COMMENT ON COLUMN processed_cards.confidence IS
    'Extractor confidence after deterministic verifier checks; NULL means not yet processed by the structured extractor.';

COMMENT ON COLUMN processed_cards.extractor_version IS
    'Version string for the extractor/verifier pipeline that produced predicates.';

COMMENT ON COLUMN processed_cards.source_text_hash IS
    'Hash of normalized JP source text, used for dedupe before extractor calls.';

-- Intentionally do not add cards.ai_logic_json. Historical callers should be
-- migrated to read/write processed_cards so structured logic has one owner.

COMMIT;
