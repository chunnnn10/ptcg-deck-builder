"""Database adapter for Gap A structured-logic extraction.

The functions in this module are inert until called by server runtime code.
They assume ``backend/migrations/001_logic_layer.sql`` has already been applied
before any write path is used.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Iterable

from services.logic_extractor.extractor import extract_card_logic
from services.logic_extractor.schema import OPERATOR_TOKENS, Predicate, THRESHOLD_TYPES


EXTRACTOR_VERSION = "gap_a_threshold_only"
SOURCE_LANGUAGE = "jp"


def _psycopg_json(value: Any) -> Any:
    from psycopg2.extras import Json

    return Json(value)


def _real_dict_cursor() -> Any:
    from psycopg2.extras import RealDictCursor

    return RealDictCursor


def normalize_source_text(source_text: str) -> str:
    """Normalize JP source text for dedupe before extraction."""

    return re.sub(r"\s+", " ", (source_text or "").strip())


def source_text_hash(source_text: str) -> str:
    normalized = normalize_source_text(source_text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def build_jp_source_text(row: dict[str, Any]) -> str:
    """Build the extractor input from a jp_cards row.

    Trainer/effect cards usually store text in ``description``. Pokemon effects
    live under ``skills_json`` as ability/attack text or effect fields.
    """

    parts: list[str] = []
    description = (row.get("description") or "").strip()
    if description:
        parts.append(description)

    for text in _skill_effect_texts(row.get("skills_json")):
        if text and text not in parts:
            parts.append(text)

    return normalize_source_text(" ".join(parts))


def _skill_effect_texts(skills_json: Any) -> Iterable[str]:
    if not skills_json:
        return []
    skills = skills_json
    if isinstance(skills_json, str):
        try:
            skills = json.loads(skills_json)
        except json.JSONDecodeError:
            return []
    if not isinstance(skills, list):
        return []

    texts: list[str] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        for key in ("effect", "text"):
            value = (skill.get(key) or "").strip()
            if value and value not in texts:
                texts.append(value)
    return texts


def extract_gap_a_predicates(source_text: str) -> list[Predicate]:
    """Extract only predicates safe for the Gap A threshold ambiguity.

    This intentionally drops partial Gap B action predicates. Missing action
    predicates must not be interpreted as "the card has no such effect".
    """

    predicates = extract_card_logic(source_text)
    return filter_gap_a_predicates(predicates)


def filter_gap_a_predicates(predicates: list[dict[str, Any]]) -> list[Predicate]:
    threshold_spans = [
        str(predicate.get("jp_source_span") or "")
        for predicate in predicates
        if predicate.get("type") in THRESHOLD_TYPES
    ]

    filtered: list[Predicate] = []
    for predicate in predicates:
        predicate_type = predicate.get("type")
        if predicate_type in THRESHOLD_TYPES:
            filtered.append(dict(predicate))  # type: ignore[arg-type]
            continue
        if predicate_type == "search_deck" and _is_hp_threshold_search(predicate, threshold_spans):
            filtered.append(dict(predicate))  # type: ignore[arg-type]
    return filtered


def _is_hp_threshold_search(predicate: dict[str, Any], threshold_spans: list[str]) -> bool:
    span = str(predicate.get("jp_source_span") or "")
    if "HPが" not in span:
        return False
    if not any(token in span for token in OPERATOR_TOKENS):
        return False
    return any(threshold_span and threshold_span in span for threshold_span in threshold_spans)


def logic_json_payload(predicates: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "version": EXTRACTOR_VERSION,
        "scope": "gap_a_threshold_only",
        "source_language": SOURCE_LANGUAGE,
        "predicates": predicates,
        "note": "Action predicates are intentionally incomplete in this version.",
    }


def upsert_processed_card(
    conn: Any,
    *,
    card_id: str,
    card_name: str,
    source_text: str,
    predicates: list[dict[str, Any]],
    source_card_id: str | None = None,
    validation_errors: list[dict[str, Any]] | None = None,
) -> None:
    """Upsert one processed_cards row with Gap A-only predicates."""

    normalized_text = normalize_source_text(source_text)
    payload = logic_json_payload(predicates)
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO processed_cards (
            card_id, card_name, original_text, logic_json, status, attempts, last_updated,
            predicates, confidence, extractor_version, source_language, source_card_id,
            source_text_hash, validation_errors, last_verified_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP,
            %s, %s, %s, %s, %s,
            %s, %s, CURRENT_TIMESTAMP
        )
        ON CONFLICT (card_id) DO UPDATE SET
            card_name = EXCLUDED.card_name,
            original_text = EXCLUDED.original_text,
            logic_json = EXCLUDED.logic_json,
            status = EXCLUDED.status,
            attempts = EXCLUDED.attempts,
            last_updated = CURRENT_TIMESTAMP,
            predicates = EXCLUDED.predicates,
            confidence = EXCLUDED.confidence,
            extractor_version = EXCLUDED.extractor_version,
            source_language = EXCLUDED.source_language,
            source_card_id = EXCLUDED.source_card_id,
            source_text_hash = EXCLUDED.source_text_hash,
            validation_errors = EXCLUDED.validation_errors,
            last_verified_at = CURRENT_TIMESTAMP
        """,
        (
            card_id,
            card_name,
            normalized_text,
            json.dumps(payload, ensure_ascii=False),
            "completed",
            0,
            _psycopg_json(predicates),
            1.0,
            EXTRACTOR_VERSION,
            SOURCE_LANGUAGE,
            source_card_id or card_id,
            source_text_hash(normalized_text),
            _psycopg_json(validation_errors or []),
        ),
    )


def logic_schema_ready(conn: Any) -> bool:
    """Return whether ``processed_cards`` has the Phase 0 logic columns."""

    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'processed_cards'
          AND column_name IN ('predicates', 'extractor_version', 'source_text_hash')
        """
    )
    row = cursor.fetchone()
    return int(row.get("count") or 0) == 3


def upsert_gap_a_for_jp_card(
    conn: Any,
    row: dict[str, Any],
    *,
    skip_empty: bool = True,
) -> dict[str, int | bool | str]:
    """Extract and upsert Gap A predicates for one JP card row.

    This is intended for crawler ingest hooks. It no-ops before
    ``001_logic_layer.sql`` has been applied, so code deployment can safely
    precede the DB migration.
    """

    summary = {
        "schema_ready": False,
        "extractor_version": EXTRACTOR_VERSION,
        "scanned": 0,
        "with_gap_a_predicates": 0,
        "skipped_empty": 0,
        "written": 0,
    }
    if not logic_schema_ready(conn):
        return summary

    summary["schema_ready"] = True
    summary["scanned"] = 1
    source_text = build_jp_source_text(row)
    if not source_text:
        summary["skipped_empty"] = 1
        return summary

    predicates = extract_gap_a_predicates(source_text)
    if predicates:
        summary["with_gap_a_predicates"] = 1
    elif skip_empty:
        summary["skipped_empty"] = 1
        return summary

    upsert_processed_card(
        conn,
        card_id=str(row.get("card_id") or ""),
        card_name=str(row.get("name") or ""),
        source_text=source_text,
        predicates=predicates,
        source_card_id=str(row.get("card_id") or ""),
    )
    summary["written"] = 1
    return summary


def fetch_jp_card_sources(conn: Any, *, limit: int | None = None, offset: int = 0) -> list[dict[str, Any]]:
    """Read JP card source rows. SELECT-only."""

    cursor = conn.cursor(cursor_factory=_real_dict_cursor())
    sql = """
        SELECT card_id, name, description, skills_json
        FROM jp_cards
        WHERE COALESCE(description, '') <> ''
           OR (
                jsonb_typeof(skills_json) = 'array'
                AND jsonb_array_length(skills_json) > 0
           )
        ORDER BY card_id
        OFFSET %s
    """
    params: list[Any] = [offset]
    if limit is not None:
        sql += " LIMIT %s"
        params.append(limit)
    cursor.execute(sql, params)
    return [dict(row) for row in cursor.fetchall()]


def backfill_gap_a_threshold_only(
    conn: Any,
    *,
    limit: int | None = None,
    offset: int = 0,
    dry_run: bool = True,
    skip_empty: bool = True,
) -> dict[str, int | bool | str]:
    """Extract Gap A predicates from jp_cards and optionally upsert them.

    ``dry_run=True`` performs only SELECT + local extraction. Set ``dry_run`` to
    false only from server/admin runtime after the migration has been applied.
    """

    rows = fetch_jp_card_sources(conn, limit=limit, offset=offset)
    predicate_cache: dict[str, list[Predicate]] = {}
    summary = {
        "dry_run": dry_run,
        "extractor_version": EXTRACTOR_VERSION,
        "scanned": 0,
        "with_gap_a_predicates": 0,
        "skipped_empty": 0,
        "written": 0,
    }

    for row in rows:
        summary["scanned"] += 1
        source_text = build_jp_source_text(row)
        if not source_text:
            summary["skipped_empty"] += 1
            continue

        text_hash = source_text_hash(source_text)
        if text_hash not in predicate_cache:
            predicate_cache[text_hash] = extract_gap_a_predicates(source_text)
        predicates = predicate_cache[text_hash]

        if predicates:
            summary["with_gap_a_predicates"] += 1
        elif skip_empty:
            summary["skipped_empty"] += 1
            continue

        if dry_run:
            continue

        upsert_processed_card(
            conn,
            card_id=row["card_id"],
            card_name=row.get("name") or "",
            source_text=source_text,
            predicates=predicates,
            source_card_id=row["card_id"],
        )
        summary["written"] += 1

    return summary
