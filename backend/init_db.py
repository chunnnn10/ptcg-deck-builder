"""
PostgreSQL Schema 初始化腳本
用法: python backend/init_db.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import psycopg2
import psycopg2.extras
import config
from services.limitless_decks.schema import LIMITLESS_SCHEMA_SQL
from services.ai_assistant.schema import ai_schema_sql

SCHEMA_SQL = """
BEGIN;

-- === 卡牌主表 ===
CREATE TABLE IF NOT EXISTS cards (
    card_id VARCHAR PRIMARY KEY,
    image_file TEXT,
    card_type VARCHAR NOT NULL CHECK (card_type IN ('Pokémon', 'Trainer', 'Energy')),
    name TEXT NOT NULL,
    sub_type VARCHAR,
    hp INTEGER,
    element_type VARCHAR,
    weakness_type VARCHAR,
    weakness_value VARCHAR,
    resistance_type VARCHAR,
    resistance_value VARCHAR,
    retreat_cost INTEGER,
    skills_json JSONB,
    rarity VARCHAR,
    processing_status INTEGER DEFAULT 0,
    japanese_name VARCHAR,
    evolution_stage VARCHAR,
    evolves_from VARCHAR,
    set_code VARCHAR,
    set_number VARCHAR,
    set_name VARCHAR,
    jp_id VARCHAR,
    regulation_flags VARCHAR,
    regulation_mark VARCHAR DEFAULT '',
    description TEXT DEFAULT '',
    flavor_text TEXT,
    pokedex_number VARCHAR,
    pokedex_category VARCHAR,
    height VARCHAR,
    weight VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_cards_name ON cards(name);
CREATE INDEX IF NOT EXISTS idx_cards_set_code ON cards(set_code);
CREATE INDEX IF NOT EXISTS idx_cards_set_code_number ON cards(set_code, set_number);

-- === 使用者牌組 ===
CREATE TABLE IF NOT EXISTS decks (
    id VARCHAR PRIMARY KEY,
    name TEXT,
    content TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    is_public INTEGER DEFAULT 0,
    user_id VARCHAR
);

-- === 使用者帳號 ===
CREATE TABLE IF NOT EXISTS users (
    id VARCHAR PRIMARY KEY,
    username VARCHAR UNIQUE NOT NULL,
    email VARCHAR UNIQUE,
    password_hash VARCHAR NOT NULL,
    role VARCHAR DEFAULT 'user',
    is_verified INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === 工作區 ===
CREATE TABLE IF NOT EXISTS user_workspace (
    id VARCHAR PRIMARY KEY,
    user_id VARCHAR NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    parent_id VARCHAR DEFAULT NULL,
    item_type VARCHAR NOT NULL CHECK (item_type IN ('folder', 'deck')),
    content TEXT DEFAULT '[]',
    sort_order INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_workspace_user ON user_workspace(user_id);
CREATE INDEX IF NOT EXISTS idx_workspace_parent ON user_workspace(parent_id);

CREATE TABLE IF NOT EXISTS user_workspace_timeline (
    id VARCHAR PRIMARY KEY,
    item_id VARCHAR NOT NULL REFERENCES user_workspace(id) ON DELETE CASCADE,
    user_id VARCHAR NOT NULL,
    action TEXT,
    source TEXT,
    content_json TEXT NOT NULL,
    card_count INTEGER DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_workspace_timeline_item_created
ON user_workspace_timeline(item_id, created_at DESC);

-- === 擴充包 ===
CREATE TABLE IF NOT EXISTS expansion_sets (
    set_code VARCHAR PRIMARY KEY,
    set_name VARCHAR,
    series VARCHAR DEFAULT '',
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === 日本卡牌主表 (與 cards 相同結構) ===
CREATE TABLE IF NOT EXISTS jp_cards (
    card_id VARCHAR PRIMARY KEY,
    image_file TEXT,
    card_type VARCHAR NOT NULL CHECK (card_type IN ('Pokémon', 'Trainer', 'Energy')),
    name TEXT NOT NULL,
    sub_type VARCHAR,
    hp INTEGER,
    element_type VARCHAR,
    weakness_type VARCHAR,
    weakness_value VARCHAR,
    resistance_type VARCHAR,
    resistance_value VARCHAR,
    retreat_cost INTEGER,
    skills_json JSONB,
    rarity VARCHAR,
    processing_status INTEGER DEFAULT 0,
    chinese_name VARCHAR,
    evolution_stage VARCHAR,
    evolves_from VARCHAR,
    set_code VARCHAR,
    set_number VARCHAR,
    set_name VARCHAR,
    regulation_flags VARCHAR,
    regulation_mark VARCHAR DEFAULT '',
    description TEXT DEFAULT '',
    flavor_text TEXT,
    pokedex_number VARCHAR,
    pokedex_category VARCHAR,
    height VARCHAR,
    weight VARCHAR
);

CREATE INDEX IF NOT EXISTS idx_jp_cards_name ON jp_cards(name);
CREATE INDEX IF NOT EXISTS idx_jp_cards_set_code ON jp_cards(set_code);
CREATE INDEX IF NOT EXISTS idx_jp_cards_set_code_number ON jp_cards(set_code, set_number);

-- === 日本擴充包 ===
CREATE TABLE IF NOT EXISTS jp_expansion_sets (
    set_code VARCHAR PRIMARY KEY,
    set_name VARCHAR,
    series VARCHAR DEFAULT '',
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === 匯入牌組 ===
CREATE TABLE IF NOT EXISTS imported_decks (
    id SERIAL PRIMARY KEY,
    deck_code VARCHAR UNIQUE,
    name VARCHAR,
    imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    deck_date VARCHAR,
    title VARCHAR,
    image_url TEXT,
    tags TEXT,
    card_list TEXT DEFAULT '[]'
);
ALTER TABLE imported_decks
    ADD COLUMN IF NOT EXISTS card_list TEXT DEFAULT '[]';
CREATE INDEX IF NOT EXISTS idx_imported_decks_deck_date_desc ON imported_decks(deck_date DESC);

-- === 匯入牌組卡片關聯 ===
CREATE TABLE IF NOT EXISTS deck_cards (
    id SERIAL PRIMARY KEY,
    deck_id INTEGER REFERENCES imported_decks(id) ON DELETE CASCADE,
    local_card_id VARCHAR,
    quantity INTEGER
);

-- === ID 映射 (外部 <-> 本地) ===
CREATE TABLE IF NOT EXISTS id_mapping (
    external_variant_id INTEGER PRIMARY KEY,
    local_card_id VARCHAR
);

-- === AI 處理卡片 ===
CREATE TABLE IF NOT EXISTS processed_cards (
    card_id VARCHAR PRIMARY KEY,
    card_name VARCHAR,
    original_text TEXT,
    logic_json TEXT,
    status VARCHAR,
    attempts INTEGER DEFAULT 0,
    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === Schema 變更記錄 ===
CREATE TABLE IF NOT EXISTS schema_changes (
    id SERIAL PRIMARY KEY,
    card_id VARCHAR,
    change_json TEXT,
    reason TEXT,
    status VARCHAR,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- === API 日誌 ===
CREATE TABLE IF NOT EXISTS api_logs (
    id SERIAL PRIMARY KEY,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    provider VARCHAR,
    model VARCHAR,
    status VARCHAR,
    duration REAL,
    tokens_in INTEGER,
    tokens_out INTEGER,
    error_msg TEXT
);

CREATE TABLE IF NOT EXISTS deck_search_index (
    deck_id INTEGER REFERENCES imported_decks(id) ON DELETE CASCADE,
    card_name TEXT NOT NULL,
    count INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_dsi_deck ON deck_search_index(deck_id);
CREATE INDEX IF NOT EXISTS idx_dsi_name ON deck_search_index(card_name);
CREATE INDEX IF NOT EXISTS idx_dsi_deck_card_name ON deck_search_index(deck_id, card_name);

COMMIT;
"""

def main():
    print(f"Connecting to: {config.DATABASE_URL}")
    try:
        conn = psycopg2.connect(config.DATABASE_URL)
        cursor = conn.cursor()
        cursor.execute(SCHEMA_SQL)
        cursor.execute(LIMITLESS_SCHEMA_SQL)
        cursor.execute(ai_schema_sql())
        conn.commit()
        cursor.close()
        conn.close()
        print("Database schema initialized successfully!")

        conn2 = psycopg2.connect(config.DATABASE_URL)
        cur2 = conn2.cursor()
        cur2.execute("""
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public' ORDER BY table_name
        """)
        tables = [r[0] for r in cur2.fetchall()]
        conn2.close()

        print(f"\nTables created: {len(tables)}")
        for t in tables:
            print(f"  - {t}")

    except Exception as e:
        print(f"Error: {e}")
        sys.exit(1)

if __name__ == '__main__':
    main()
