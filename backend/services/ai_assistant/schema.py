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

    CREATE TABLE IF NOT EXISTS ai_assistant_jobs (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        kind TEXT NOT NULL DEFAULT 'chat',
        status TEXT NOT NULL DEFAULT 'pending',
        message TEXT DEFAULT '',
        result JSONB,
        error TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_ai_assistant_jobs_user ON ai_assistant_jobs(user_id, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_ai_assistant_jobs_status ON ai_assistant_jobs(status);
    CREATE INDEX IF NOT EXISTS idx_ai_assistant_jobs_updated ON ai_assistant_jobs(updated_at);

    CREATE TABLE IF NOT EXISTS ai_assistant_threads (
        id TEXT PRIMARY KEY,
        user_id TEXT,
        title TEXT DEFAULT '',
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_ai_assistant_threads_user ON ai_assistant_threads(user_id, updated_at DESC);

    CREATE TABLE IF NOT EXISTS ai_assistant_messages (
        id BIGSERIAL PRIMARY KEY,
        thread_id TEXT,
        role TEXT,
        content TEXT,
        tool_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS idx_ai_assistant_messages_thread ON ai_assistant_messages(thread_id, id);
    CREATE INDEX IF NOT EXISTS idx_ai_assistant_messages_created ON ai_assistant_messages(created_at);
    """
