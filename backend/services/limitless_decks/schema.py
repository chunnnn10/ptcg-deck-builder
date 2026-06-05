LIMITLESS_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS limitless_tournaments (
    tournament_id TEXT PRIMARY KEY,
    source_region TEXT NOT NULL,
    title TEXT,
    date DATE,
    location TEXT,
    format TEXT,
    players INTEGER,
    url TEXT,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    raw_html TEXT
);

CREATE TABLE IF NOT EXISTS limitless_decks (
    deck_id TEXT PRIMARY KEY,
    tournament_id TEXT REFERENCES limitless_tournaments(tournament_id) ON DELETE SET NULL,
    player_name TEXT,
    placement INTEGER,
    archetype TEXT,
    title TEXT,
    tags JSONB DEFAULT '[]'::jsonb,
    deck_url TEXT,
    source_region TEXT,
    fetched_at TIMESTAMP,
    raw_jp_text TEXT,
    raw_en_text TEXT,
    raw_jp_bling_text TEXT,
    raw_en_bling_text TEXT
);

CREATE TABLE IF NOT EXISTS limitless_tournament_deck_entries (
    entry_id TEXT PRIMARY KEY,
    tournament_id TEXT REFERENCES limitless_tournaments(tournament_id) ON DELETE CASCADE,
    deck_id TEXT REFERENCES limitless_decks(deck_id) ON DELETE CASCADE,
    player_name TEXT,
    placement INTEGER,
    archetype TEXT,
    title TEXT,
    tags JSONB DEFAULT '[]'::jsonb,
    deck_url TEXT,
    source_region TEXT,
    entry_order INTEGER,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS limitless_deck_cards (
    id SERIAL PRIMARY KEY,
    deck_id TEXT REFERENCES limitless_decks(deck_id) ON DELETE CASCADE,
    language TEXT NOT NULL CHECK (language IN ('jp', 'en')),
    mode TEXT NOT NULL CHECK (mode IN ('normal', 'bling')),
    section TEXT NOT NULL CHECK (section IN ('pokemon', 'trainer', 'energy', 'unknown')),
    line_order INTEGER NOT NULL,
    count INTEGER NOT NULL,
    card_name TEXT,
    set_code TEXT,
    set_number TEXT,
    local_jp_card_id TEXT,
    local_tw_card_id TEXT,
    limitless_card_url TEXT,
    limitless_image_url TEXT,
    UNIQUE(deck_id, language, mode, line_order)
);

CREATE TABLE IF NOT EXISTS limitless_card_mapping (
    id SERIAL PRIMARY KEY,
    jp_set_code TEXT,
    jp_set_number TEXT,
    jp_name TEXT,
    en_set_code TEXT,
    en_set_number TEXT,
    en_name TEXT,
    mode TEXT NOT NULL CHECK (mode IN ('normal', 'bling')),
    confidence REAL DEFAULT 1.0,
    source_deck_id TEXT REFERENCES limitless_decks(deck_id) ON DELETE SET NULL,
    first_seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(jp_set_code, jp_set_number, en_set_code, en_set_number, mode)
);

CREATE TABLE IF NOT EXISTS limitless_update_logs (
    id SERIAL PRIMARY KEY,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    level TEXT DEFAULT 'info',
    context TEXT,
    message TEXT,
    detail TEXT
);

CREATE INDEX IF NOT EXISTS idx_limitless_decks_tournament ON limitless_decks(tournament_id);
CREATE INDEX IF NOT EXISTS idx_limitless_decks_tournament_fetched ON limitless_decks(tournament_id, fetched_at);
CREATE INDEX IF NOT EXISTS idx_limitless_decks_fetched_desc ON limitless_decks(fetched_at DESC);
CREATE INDEX IF NOT EXISTS idx_limitless_tournaments_date_seen ON limitless_tournaments(date DESC, last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_limitless_entries_tournament_only ON limitless_tournament_deck_entries(tournament_id);
CREATE INDEX IF NOT EXISTS idx_limitless_entries_tournament ON limitless_tournament_deck_entries(tournament_id, entry_order);
CREATE INDEX IF NOT EXISTS idx_limitless_entries_deck ON limitless_tournament_deck_entries(deck_id);
CREATE INDEX IF NOT EXISTS idx_limitless_deck_cards_deck ON limitless_deck_cards(deck_id);
CREATE INDEX IF NOT EXISTS idx_limitless_deck_cards_lookup ON limitless_deck_cards(language, mode, set_code, set_number);
CREATE INDEX IF NOT EXISTS idx_limitless_mapping_jp ON limitless_card_mapping(jp_set_code, jp_set_number);
CREATE INDEX IF NOT EXISTS idx_limitless_mapping_en ON limitless_card_mapping(en_set_code, en_set_number);

ALTER TABLE limitless_deck_cards
    ADD COLUMN IF NOT EXISTS limitless_card_url TEXT;
ALTER TABLE limitless_deck_cards
    ADD COLUMN IF NOT EXISTS limitless_image_url TEXT;
"""
