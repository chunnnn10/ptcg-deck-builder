from __future__ import annotations

import json
import re
import threading
from collections import Counter, defaultdict
from datetime import date, datetime
from typing import Any

import config
import database

from .embeddings import content_hash, embed_texts, vector_literal
from .schema import ai_schema_sql


STANDARD_MARKS = ("H", "I", "J")
_rebuild_lock = threading.Lock()
_rebuild_state = {
    "running": False,
    "status": "idle",
    "processed": 0,
    "failed": 0,
    "message": "",
    "error": "",
}


def ensure_ai_schema(conn=None) -> None:
    owns_conn = conn is None
    conn = conn or database.get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute(ai_schema_sql())
        if owns_conn:
            conn.commit()
    except Exception:
        if owns_conn:
            conn.rollback()
        raise
    finally:
        if owns_conn:
            conn.close()


def parse_skills(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        for key in ("skills", "attacks"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
        return []
    try:
        return parse_skills(json.loads(value))
    except Exception:
        return []


def _skill_lines(skills: list[dict[str, Any]]) -> list[str]:
    lines = []
    for skill in skills:
        name = str(skill.get("name") or "").strip()
        kind = str(skill.get("type") or skill.get("category") or "").strip()
        cost = skill.get("cost") or []
        cost_text = "/".join(str(item) for item in cost) if isinstance(cost, list) else str(cost or "")
        damage = str(skill.get("damage") or "").strip()
        effect = str(skill.get("effect") or skill.get("text") or skill.get("description") or "").strip()
        pieces = [part for part in (kind, name, cost_text, damage, effect) if part]
        if pieces:
            lines.append(" | ".join(pieces))
    return lines


def _card_doc(row: dict[str, Any], language: str) -> dict[str, Any]:
    skills = parse_skills(row.get("skills_json"))
    skill_text = "\n".join(_skill_lines(skills))
    title = str(row.get("name") or "").strip()
    content = "\n".join(part for part in (
        f"Card: {title}",
        f"Language: {language}",
        f"Type: {row.get('card_type') or ''} {row.get('sub_type') or ''}",
        f"Element: {row.get('element_type') or ''}",
        f"HP: {row.get('hp') or ''}",
        f"Regulation: {row.get('regulation_mark') or ''}",
        f"Set: {row.get('set_code') or ''} {row.get('set_number') or ''} {row.get('set_name') or ''}",
        f"Japanese name: {row.get('japanese_name') or ''}" if language == "tw" else f"Chinese name: {row.get('chinese_name') or ''}",
        f"Description: {row.get('description') or ''}",
        f"Skills:\n{skill_text}" if skill_text else "",
    ) if str(part).strip())
    source_id = str(row.get("card_id") or "")
    return {
        "id": f"card:{language}:{source_id}",
        "source_type": "card",
        "source_id": source_id,
        "language": language,
        "title": title,
        "content": content,
        "metadata": {
            "card_id": source_id,
            "language": language,
            "name": title,
            "card_type": row.get("card_type"),
            "sub_type": row.get("sub_type"),
            "element_type": row.get("element_type"),
            "set_code": row.get("set_code"),
            "set_number": row.get("set_number"),
            "set_name": row.get("set_name"),
            "regulation_mark": row.get("regulation_mark"),
        },
    }


def _fetch_card_docs(cursor, language: str, limit: int, offset: int) -> list[dict[str, Any]]:
    table = "jp_cards" if language == "jp" else "cards"
    extra = "chinese_name" if language == "jp" else "japanese_name"
    cursor.execute(
        f"""
        SELECT card_id, name, card_type, sub_type, hp, element_type, skills_json, description,
               set_code, set_number, set_name, regulation_mark, {extra}
        FROM {table}
        WHERE regulation_mark = ANY(%s)
        ORDER BY card_id
        LIMIT %s OFFSET %s
        """,
        (list(STANDARD_MARKS), limit, offset),
    )
    return [_card_doc(row, language) for row in cursor.fetchall()]


def _normalize_archetype(value: str | None) -> str:
    text = str(value or "").strip()
    text = re.sub(r"\s+", " ", text)
    return text or "Unknown"


def _fetch_meta_deck_docs(cursor, limit: int, offset: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT d.deck_id, d.archetype, d.title, d.player_name, d.placement, d.source_region,
               d.deck_url, t.title AS tournament_title, t.date, t.players,
               jsonb_agg(
                   jsonb_build_object(
                       'count', c.count,
                       'name', COALESCE(tw.name, c.card_name),
                       'jp_name', c.card_name,
                       'section', c.section,
                       'set_code', COALESCE(tw.set_code, c.set_code),
                       'set_number', COALESCE(tw.set_number, c.set_number),
                       'regulation_mark', tw.regulation_mark
                   )
                   ORDER BY c.line_order
               ) FILTER (WHERE c.id IS NOT NULL) AS cards
        FROM limitless_decks d
        LEFT JOIN limitless_tournaments t ON t.tournament_id = d.tournament_id
        JOIN limitless_deck_cards c ON c.deck_id = d.deck_id AND c.language = 'jp' AND c.mode = 'normal'
        LEFT JOIN cards tw ON tw.card_id = c.local_tw_card_id
        GROUP BY d.deck_id, t.title, t.date, t.players
        ORDER BY COALESCE(t.date, DATE '1900-01-01') DESC, COALESCE(d.fetched_at, TIMESTAMP '1900-01-01') DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )
    docs = []
    for row in cursor.fetchall():
        cards = row.get("cards") or []
        if isinstance(cards, str):
            try:
                cards = json.loads(cards)
            except Exception:
                cards = []
        card_lines = [
            f"{card.get('count')} {card.get('name') or card.get('jp_name')} [{card.get('section')}]"
            for card in cards[:80]
        ]
        archetype = _normalize_archetype(row.get("archetype") or row.get("title"))
        title = f"{archetype} - {row.get('tournament_title') or row.get('deck_id')}"
        content = "\n".join(part for part in (
            f"Meta deck: {title}",
            f"Archetype: {archetype}",
            f"Player: {row.get('player_name') or ''}",
            f"Placement: {row.get('placement') or ''}",
            f"Tournament: {row.get('tournament_title') or ''}",
            f"Date: {row.get('date') or ''}",
            f"Region: {row.get('source_region') or ''}",
            "Cards:\n" + "\n".join(card_lines),
        ) if str(part).strip())
        docs.append({
            "id": f"meta_deck:{row.get('deck_id')}",
            "source_type": "meta_deck",
            "source_id": str(row.get("deck_id") or ""),
            "language": "tw",
            "title": title,
            "content": content,
            "metadata": {
                "deck_id": row.get("deck_id"),
                "archetype": archetype,
                "player_name": row.get("player_name"),
                "placement": row.get("placement"),
                "tournament_title": row.get("tournament_title"),
                "date": row.get("date").isoformat() if row.get("date") else None,
                "players": row.get("players"),
                "source_region": row.get("source_region"),
                "deck_url": row.get("deck_url"),
                "cards": cards[:80],
            },
        })
    return docs


def _fetch_meta_archetype_docs(cursor, limit: int, offset: int) -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT COALESCE(NULLIF(d.archetype, ''), NULLIF(d.title, ''), 'Unknown') AS archetype,
               COUNT(DISTINCT d.deck_id) AS deck_count,
               MAX(t.date) AS latest_date,
               MIN(d.placement) AS best_placement,
               jsonb_agg(DISTINCT jsonb_build_object(
                   'deck_id', d.deck_id,
                   'placement', d.placement,
                   'date', t.date,
                   'tournament_title', t.title,
                   'deck_url', d.deck_url
               )) AS decks,
               jsonb_agg(jsonb_build_object(
                   'count', c.count,
                   'name', COALESCE(tw.name, c.card_name),
                   'section', c.section,
                   'regulation_mark', tw.regulation_mark
               )) FILTER (WHERE c.id IS NOT NULL) AS cards
        FROM limitless_decks d
        LEFT JOIN limitless_tournaments t ON t.tournament_id = d.tournament_id
        JOIN limitless_deck_cards c ON c.deck_id = d.deck_id AND c.language = 'jp' AND c.mode = 'normal'
        LEFT JOIN cards tw ON tw.card_id = c.local_tw_card_id
        GROUP BY COALESCE(NULLIF(d.archetype, ''), NULLIF(d.title, ''), 'Unknown')
        ORDER BY MAX(t.date) DESC NULLS LAST, COUNT(DISTINCT d.deck_id) DESC
        LIMIT %s OFFSET %s
        """,
        (limit, offset),
    )
    docs = []
    for row in cursor.fetchall():
        archetype = _normalize_archetype(row.get("archetype"))
        cards = row.get("cards") or []
        decks = row.get("decks") or []
        if isinstance(cards, str):
            cards = json.loads(cards)
        if isinstance(decks, str):
            decks = json.loads(decks)
        counts: dict[str, Counter] = defaultdict(Counter)
        appearances = Counter()
        for card in cards:
            name = str(card.get("name") or "").strip()
            if not name:
                continue
            appearances[name] += 1
            try:
                counts[name][int(card.get("count") or 0)] += 1
            except Exception:
                pass
        common = []
        deck_count = int(row.get("deck_count") or 0)
        for name, appear_count in appearances.most_common(30):
            count_bucket = counts[name].most_common(1)
            common.append({
                "name": name,
                "typical_count": count_bucket[0][0] if count_bucket else None,
                "appearances": appear_count,
                "share": round(appear_count / max(deck_count, 1), 3),
            })
        common_lines = [
            f"{item['name']} typical {item['typical_count']} appearances {item['appearances']}/{deck_count}"
            for item in common[:20]
        ]
        content = "\n".join(part for part in (
            f"Meta archetype: {archetype}",
            f"Deck count: {deck_count}",
            f"Latest date: {row.get('latest_date') or ''}",
            f"Best placement: {row.get('best_placement') or ''}",
            "Common cards:\n" + "\n".join(common_lines),
        ) if str(part).strip())
        docs.append({
            "id": f"meta_archetype:{content_hash(archetype)[:16]}",
            "source_type": "meta_archetype",
            "source_id": archetype,
            "language": "tw",
            "title": archetype,
            "content": content,
            "metadata": {
                "archetype": archetype,
                "deck_count": deck_count,
                "latest_date": row.get("latest_date").isoformat() if row.get("latest_date") else None,
                "best_placement": row.get("best_placement"),
                "common_cards": common,
                "sample_decks": decks[:10],
            },
        })
    return docs


def _upsert_docs(cursor, docs: list[dict[str, Any]], vectors: list[list[float]], model: str) -> None:
    for doc, vector in zip(docs, vectors):
        cursor.execute(
            """
            INSERT INTO ai_embeddings (
                id, source_type, source_id, language, title, content, metadata,
                embedding, content_hash, model, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s::vector, %s, %s, CURRENT_TIMESTAMP)
            ON CONFLICT (id) DO UPDATE SET
                source_type = EXCLUDED.source_type,
                source_id = EXCLUDED.source_id,
                language = EXCLUDED.language,
                title = EXCLUDED.title,
                content = EXCLUDED.content,
                metadata = EXCLUDED.metadata,
                embedding = EXCLUDED.embedding,
                content_hash = EXCLUDED.content_hash,
                model = EXCLUDED.model,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                doc["id"],
                doc["source_type"],
                doc["source_id"],
                doc["language"],
                doc["title"],
                doc["content"],
                json.dumps(doc["metadata"], ensure_ascii=False, default=str),
                vector_literal(vector),
                content_hash(doc["content"]),
                model,
            ),
        )


def rebuild_embeddings(source_type: str = "all", batch_size: int = 64, max_items: int | None = None) -> dict[str, Any]:
    from .embeddings import get_embedding_config

    source_type = source_type if source_type in ("all", "cards", "meta_decks", "meta_archetypes") else "all"
    batch_size = max(1, min(int(batch_size or 64), 128))
    max_items = int(max_items) if max_items else None
    cfg = get_embedding_config()
    model = cfg["embedding_model"]

    with _rebuild_lock:
        if _rebuild_state["running"]:
            return {"success": False, "error": "Embedding rebuild is already running", "status": dict(_rebuild_state)}
        _rebuild_state.update({"running": True, "status": "running", "processed": 0, "failed": 0, "message": "Starting", "error": ""})

    conn = database.get_db_connection()
    if not conn:
        _rebuild_state.update({"running": False, "status": "failed", "error": "Database unavailable"})
        return {"success": False, "error": "Database unavailable"}

    job_id = None
    try:
        ensure_ai_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO ai_embedding_jobs (status, source_type, message) VALUES ('running', %s, %s) RETURNING id",
            (source_type, "Starting"),
        )
        job_id = cursor.fetchone()["id"]
        conn.commit()

        sources: list[tuple[str, Any]] = []
        if source_type in ("all", "cards"):
            sources.extend([("cards_tw", lambda offset: _fetch_card_docs(cursor, "tw", batch_size, offset))])
            sources.extend([("cards_jp", lambda offset: _fetch_card_docs(cursor, "jp", batch_size, offset))])
        if source_type in ("all", "meta_decks"):
            sources.append(("meta_decks", lambda offset: _fetch_meta_deck_docs(cursor, batch_size, offset)))
        if source_type in ("all", "meta_archetypes"):
            sources.append(("meta_archetypes", lambda offset: _fetch_meta_archetype_docs(cursor, batch_size, offset)))

        processed = failed = 0
        for label, fetcher in sources:
            offset = 0
            while True:
                if max_items and processed >= max_items:
                    break
                docs = fetcher(offset)
                if not docs:
                    break
                if max_items:
                    docs = docs[:max_items - processed]
                _rebuild_state["message"] = f"Embedding {label} offset {offset}"
                try:
                    vectors = embed_texts([doc["content"] for doc in docs])
                    _upsert_docs(cursor, docs, vectors, model)
                    conn.commit()
                    processed += len(docs)
                except Exception as exc:
                    conn.rollback()
                    failed += len(docs)
                    _rebuild_state["error"] = str(exc)
                _rebuild_state.update({"processed": processed, "failed": failed})
                offset += batch_size
                if len(docs) < batch_size or (max_items and processed >= max_items):
                    break

        cursor.execute(
            """
            UPDATE ai_embedding_jobs
            SET status = %s, processed = %s, failed = %s, message = %s, error = %s, finished_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            ("finished" if failed == 0 else "finished_with_errors", processed, failed, "Finished", _rebuild_state.get("error", ""), job_id),
        )
        conn.commit()
        _rebuild_state.update({"running": False, "status": "finished", "message": "Finished", "processed": processed, "failed": failed})
        return {"success": True, "processed": processed, "failed": failed}
    except Exception as exc:
        conn.rollback()
        if job_id:
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE ai_embedding_jobs SET status = 'failed', error = %s, finished_at = CURRENT_TIMESTAMP WHERE id = %s",
                    (str(exc), job_id),
                )
                conn.commit()
            except Exception:
                conn.rollback()
        _rebuild_state.update({"running": False, "status": "failed", "error": str(exc)})
        return {"success": False, "error": str(exc), "status": dict(_rebuild_state)}
    finally:
        conn.close()


def start_rebuild_embeddings(source_type: str = "all", batch_size: int = 64, max_items: int | None = None) -> tuple[bool, dict[str, Any]]:
    with _rebuild_lock:
        if _rebuild_state["running"]:
            return False, dict(_rebuild_state)
    thread = threading.Thread(target=rebuild_embeddings, args=(source_type, batch_size, max_items), daemon=True)
    thread.start()
    return True, dict(_rebuild_state)


def embedding_status() -> dict[str, Any]:
    conn = database.get_db_connection()
    counts = []
    latest_job = None
    schema_error = ""
    if conn:
        try:
            ensure_ai_schema(conn)
            cursor = conn.cursor()
            cursor.execute("SELECT source_type, language, COUNT(*) AS count FROM ai_embeddings GROUP BY source_type, language ORDER BY source_type, language")
            counts = [dict(row) for row in cursor.fetchall()]
            cursor.execute("SELECT * FROM ai_embedding_jobs ORDER BY id DESC LIMIT 1")
            row = cursor.fetchone()
            latest_job = dict(row) if row else None
            if latest_job:
                for key, value in list(latest_job.items()):
                    if isinstance(value, (datetime, date)):
                        latest_job[key] = value.isoformat()
            conn.commit()
        except Exception as exc:
            conn.rollback()
            schema_error = str(exc)
        finally:
            conn.close()
    return {
        "success": not bool(schema_error),
        "status": dict(_rebuild_state),
        "counts": counts,
        "latest_job": latest_job,
        "error": schema_error,
        "embedding_model": getattr(config, "AI_EMBEDDING_MODEL", ""),
        "embedding_dimensions": getattr(config, "AI_EMBEDDING_DIMENSIONS", 0),
    }
