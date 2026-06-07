from __future__ import annotations

import config


def ai_schema_sql() -> str:
    dimensions = int(getattr(config, "AI_EMBEDDING_DIMENSIONS", 1536) or 1536)
    return f"""
    CREATE EXTENSION IF NOT EXISTS vector;

    CREATE TABLE IF NOT EXISTS ai_embeddings (
        id TEXT PRIMARY KEY,
        source_type TEXT NOT NULL CHECK (source_type IN ('card', 'meta_deck', 'meta_archetype')),
        source_id TEXT NOT NULL,
        language TEXT DEFAULT 'tw',
        title TEXT NOT NULL,
        content TEXT NOT NULL,
        metadata JSONB DEFAULT '{{}}'::jsonb,
        embedding vector({dimensions}),
        content_hash TEXT NOT NULL,
        model TEXT NOT NULL,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );

    CREATE INDEX IF NOT EXISTS idx_ai_embeddings_source ON ai_embeddings(source_type, source_id);
    CREATE INDEX IF NOT EXISTS idx_ai_embeddings_language ON ai_embeddings(language);
    CREATE INDEX IF NOT EXISTS idx_ai_embeddings_metadata_gin ON ai_embeddings USING GIN (metadata);
    CREATE INDEX IF NOT EXISTS idx_ai_embeddings_vector_cosine
        ON ai_embeddings USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100);

    CREATE TABLE IF NOT EXISTS ai_embedding_jobs (
        id SERIAL PRIMARY KEY,
        status TEXT NOT NULL DEFAULT 'idle',
        source_type TEXT,
        processed INTEGER DEFAULT 0,
        failed INTEGER DEFAULT 0,
        message TEXT DEFAULT '',
        error TEXT DEFAULT '',
        started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        finished_at TIMESTAMP
    );
    """
