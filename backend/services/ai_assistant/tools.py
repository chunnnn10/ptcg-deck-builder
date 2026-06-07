from __future__ import annotations

import json
import os
import re
from collections import Counter, defaultdict
from typing import Any

import config
import database

from .embeddings import embed_texts, vector_literal
from .indexer import STANDARD_MARKS, ensure_ai_schema, parse_skills


CARD_LIMIT = 20
META_LIMIT = 10


def _image_url(row: dict[str, Any], language: str) -> str:
    image_file = str(row.get("image_file") or "").strip()
    if image_file.startswith(("http://", "https://")):
        return image_file
    if language == "jp":
        return f"/images_jp/{image_file}" if image_file else ""
    if image_file:
        local_path = os.path.join(config.IMAGE_FOLDER, image_file)
        if os.path.exists(local_path):
            return f"/images/{image_file}"
        return f"https://asia.pokemon-card.com/tw/card-img/{image_file}"
    raw = str(row.get("card_id") or "").strip()
    if raw.isdigit():
        return f"https://asia.pokemon-card.com/tw/card-img/tw{int(raw):08d}.png"
    return ""


def _skill_payload(skills: list[dict[str, Any]], full: bool = True) -> list[dict[str, Any]]:
    result = []
    for skill in skills:
        item = {
            "type": skill.get("type") or skill.get("category") or ("ability" if skill.get("isAbility") else "attack"),
            "name": skill.get("name") or skill.get("ability_name") or "",
            "cost": skill.get("cost") or [],
            "damage": str(skill.get("damage") or ""),
            "effect": skill.get("effect") or skill.get("text") or skill.get("description") or "",
        }
        if full:
            result.append(item)
        else:
            result.append({k: v for k, v in item.items() if k in ("type", "name", "damage", "effect") and v})
    return result


def _card_payload(row: dict[str, Any], language: str, full_skills: bool = True) -> dict[str, Any]:
    skills = parse_skills(row.get("skills_json"))
    payload = {
        "card_id": row.get("card_id"),
        "id": row.get("card_id"),
        "language": language,
        "name": row.get("name"),
        "card_type": row.get("card_type"),
        "sub_type": row.get("sub_type"),
        "hp": row.get("hp"),
        "element_type": row.get("element_type"),
        "weakness_type": row.get("weakness_type"),
        "weakness_value": row.get("weakness_value"),
        "resistance_type": row.get("resistance_type"),
        "resistance_value": row.get("resistance_value"),
        "retreat_cost": row.get("retreat_cost"),
        "rarity": row.get("rarity"),
        "set_code": row.get("set_code"),
        "set_number": row.get("set_number"),
        "set_name": row.get("set_name"),
        "regulation_mark": row.get("regulation_mark"),
        "description": row.get("description"),
        "image_file": row.get("image_file"),
        "image_url": _image_url(row, language),
        "skills": _skill_payload(skills, full_skills),
    }
    if language == "tw" and row.get("japanese_name"):
        payload["japanese_name"] = row.get("japanese_name")
    if language == "jp" and row.get("chinese_name"):
        payload["chinese_name"] = row.get("chinese_name")
    return payload


def _select_columns(table: str) -> str:
    extra_name = "japanese_name" if table == "cards" else "chinese_name"
    return (
        f"card_id, image_file, card_type, name, sub_type, hp, element_type, "
        f"weakness_type, weakness_value, resistance_type, resistance_value, retreat_cost, rarity, "
        f"{extra_name}, set_code, set_number, set_name, regulation_mark, skills_json, description"
    )


def _normalize_marks(filters: dict[str, Any] | None = None) -> list[str]:
    raw = (filters or {}).get("standard_marks") or (filters or {}).get("regulation_marks") or list(STANDARD_MARKS)
    marks = [str(mark).strip().upper() for mark in raw if str(mark).strip()]
    marks = [mark for mark in marks if mark in STANDARD_MARKS]
    return marks or list(STANDARD_MARKS)


def _keyword_card_search(query: str, language: str = "tw", limit: int = CARD_LIMIT, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    marks = _normalize_marks(filters)
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    extra_name = "chinese_name" if language == "jp" else "japanese_name"
    search = f"%{query}%"

    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_select_columns(table)}
            FROM {table}
            WHERE regulation_mark = ANY(%s)
              AND (
                name ILIKE %s OR card_id ILIKE %s OR set_code ILIKE %s OR set_number ILIKE %s
                OR COALESCE({extra_name}, '') ILIKE %s
                OR COALESCE(description, '') ILIKE %s
                OR COALESCE(skills_json::text, '') ILIKE %s
              )
            ORDER BY
                CASE WHEN name = %s THEN 0 ELSE 1 END,
                CASE WHEN name ILIKE %s THEN 0 ELSE 1 END,
                card_id DESC
            LIMIT %s
            """,
            (marks, search, search, search, search, search, search, search, query, search, limit),
        )
        return [_card_payload(row, folder_lang, idx < 8) for idx, row in enumerate(cursor.fetchall())]
    finally:
        conn.close()


def _expanded_keyword_card_search(query: str, language: str = "tw", limit: int = CARD_LIMIT, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    seen = set()

    def append_cards(cards: list[dict[str, Any]]) -> None:
        for card in cards:
            key = f"{card.get('language') or language}:{card.get('card_id') or card.get('id')}"
            if key in seen:
                continue
            seen.add(key)
            results.append(card)
            if len(results) >= limit:
                break

    terms = _card_query_terms(query)
    for term in terms:
        append_cards(_keyword_card_search(term, language, limit, filters))
        if len(results) >= limit:
            break
    return results[:limit]


def _card_query_terms(query: str) -> list[str]:
    text = str(query or "").strip()
    terms: list[str] = []
    seen = set()

    def add(value: str) -> None:
        term = str(value or "").strip()
        term = term.strip(" 「」『』<>＜＞《》【】")
        if len(term) < 2:
            return
        if term.lower() in {"ex", "v", "vstar", "vmax", "gx", "deck"}:
            return
        if term not in seen:
            seen.add(term)
            terms.append(term)

    for term in _meta_query_terms(text):
        add(term)
    for quoted in re.findall(r"[<＜《「『【]([^>＞》」』】]{2,40})[>＞》」』】]", text):
        add(quoted)
    cleaned = re.sub(r"(有沒有|有没有|推薦|推荐|牌組|卡組|卡组|構築|构筑|請問|请问|幫我|帮我|想組|想组|一套)", " ", text)
    cleaned = re.sub(r"[<＞>《》「」『』【】（）()\[\]：:，,。?？!！/\\]", " ", cleaned)
    for token in re.split(r"[\s\u3000]+", cleaned):
        add(token)
        if "的" in token:
            owner, subject = token.split("的", 1)
            add(owner)
            add(subject)
    return sorted(terms, key=len, reverse=True)[:10]


def semantic_search_cards(query: str, limit: int = CARD_LIMIT, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    language = str((filters or {}).get("language") or "tw")
    if language not in ("tw", "jp"):
        language = "tw"
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    marks = _normalize_marks(filters)

    conn = database.get_db_connection()
    if not conn:
        return _expanded_keyword_card_search(query, language, limit, filters)
    try:
        ensure_ai_schema(conn)
        vector = embed_texts([query])[0]
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT source_id, language, title, metadata, 1 - (embedding <=> %s::vector) AS score
            FROM ai_embeddings
            WHERE source_type = 'card'
              AND language = %s
              AND metadata->>'regulation_mark' = ANY(%s)
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vector_literal(vector), language, marks, vector_literal(vector), limit),
        )
        rows = cursor.fetchall()
        ids = [row["source_id"] for row in rows]
        score_by_id = {row["source_id"]: float(row["score"] or 0) for row in rows}
        if not ids:
            return _expanded_keyword_card_search(query, language, limit, filters)
        table = "jp_cards" if language == "jp" else "cards"
        cursor.execute(
            f"SELECT {_select_columns(table)} FROM {table} WHERE card_id = ANY(%s)",
            (ids,),
        )
        by_id = {row["card_id"]: row for row in cursor.fetchall()}
        cards = []
        for cid in ids:
            row = by_id.get(cid)
            if row:
                payload = _card_payload(row, language, len(cards) < 8)
                payload["semantic_score"] = round(score_by_id.get(cid, 0), 4)
                cards.append(payload)
        keyword_cards = _expanded_keyword_card_search(query, language, min(limit, 6), filters)
        seen = {card.get("card_id") for card in cards}
        for card in keyword_cards:
            if card.get("card_id") not in seen and len(cards) < limit:
                card["semantic_score"] = None
                cards.append(card)
        return cards
    except Exception:
        return _expanded_keyword_card_search(query, language, limit, filters)
    finally:
        conn.close()


def search_cards(query: str, language: str = "tw", limit: int = CARD_LIMIT) -> list[dict[str, Any]]:
    return semantic_search_cards(query, limit, {"language": language, "standard_marks": list(STANDARD_MARKS)})


def get_card_detail(card_id: str, language: str = "tw") -> dict[str, Any] | None:
    card_id = str(card_id or "").strip()
    if not card_id:
        return None
    language = language if language in ("tw", "jp") else "tw"
    table = "jp_cards" if language == "jp" else "cards"
    conn = database.get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_select_columns(table)} FROM {table} WHERE card_id = %s AND regulation_mark = ANY(%s) LIMIT 1",
            (card_id, list(STANDARD_MARKS)),
        )
        row = cursor.fetchone()
        return _card_payload(row, language, True) if row else None
    finally:
        conn.close()


def get_card(card_id: str, language: str = "tw") -> dict[str, Any] | None:
    return get_card_detail(card_id, language)


def _meta_from_embedding(query: str, source_types: list[str], limit: int) -> list[dict[str, Any]]:
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        ensure_ai_schema(conn)
        vector = embed_texts([query])[0]
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT source_type, source_id, title, content, metadata, 1 - (embedding <=> %s::vector) AS score
            FROM ai_embeddings
            WHERE source_type = ANY(%s)
            ORDER BY embedding <=> %s::vector
            LIMIT %s
            """,
            (vector_literal(vector), source_types, vector_literal(vector), limit),
        )
        results = []
        for row in cursor.fetchall():
            metadata = row.get("metadata") or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            results.append({
                "source_type": row.get("source_type"),
                "source_id": row.get("source_id"),
                "title": row.get("title"),
                "score": round(float(row.get("score") or 0), 4),
                "metadata": metadata,
            })
        return results
    except Exception:
        return []
    finally:
        conn.close()


def search_meta_decks(archetype_or_query: str, limit: int = META_LIMIT) -> list[dict[str, Any]]:
    query = str(archetype_or_query or "").strip()
    limit = max(1, min(int(limit or META_LIMIT), META_LIMIT))
    semantic = _meta_from_embedding(query, ["meta_deck"], limit)
    if semantic:
        return [_meta_reference_from_embedding(item) for item in semantic]

    terms = _meta_query_terms(query)
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        if not terms:
            cursor.execute(
                """
                SELECT d.deck_id, d.archetype, d.title, d.player_name, d.placement, d.deck_url,
                       t.title AS tournament_title, t.date, t.players,
                       NULL::jsonb AS matched_cards
                FROM limitless_decks d
                LEFT JOIN limitless_tournaments t ON t.tournament_id = d.tournament_id
                ORDER BY COALESCE(t.date, DATE '1900-01-01') DESC, COALESCE(d.placement, 9999)
                LIMIT %s
                """,
                (limit,),
            )
            return [_meta_reference_from_deck_row(row) for row in cursor.fetchall()]

        likes = [f"%{term.replace('%', '').replace('_', '')}%" for term in terms if term]
        cursor.execute(
            """
            SELECT d.deck_id, d.archetype, d.title, d.player_name, d.placement, d.deck_url,
                   t.title AS tournament_title, t.date, t.players,
                   cm.matched_cards
            FROM limitless_decks d
            LEFT JOIN limitless_tournaments t ON t.tournament_id = d.tournament_id
            LEFT JOIN LATERAL (
                SELECT jsonb_agg(
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
                ) AS matched_cards
                FROM limitless_deck_cards c
                LEFT JOIN cards tw ON tw.card_id = c.local_tw_card_id
                WHERE c.deck_id = d.deck_id
                  AND c.language = 'jp'
                  AND c.mode = 'normal'
                  AND (
                      c.card_name ILIKE ANY(%s)
                      OR c.set_code ILIKE ANY(%s)
                      OR c.set_number ILIKE ANY(%s)
                      OR COALESCE(tw.name, '') ILIKE ANY(%s)
                  )
            ) cm ON TRUE
            WHERE COALESCE(d.archetype, '') ILIKE ANY(%s)
               OR COALESCE(d.title, '') ILIKE ANY(%s)
               OR COALESCE(d.player_name, '') ILIKE ANY(%s)
               OR COALESCE(d.tags::text, '') ILIKE ANY(%s)
               OR cm.matched_cards IS NOT NULL
            ORDER BY
                CASE WHEN cm.matched_cards IS NOT NULL THEN 0 ELSE 1 END,
                COALESCE(t.date, DATE '1900-01-01') DESC,
                COALESCE(d.placement, 9999)
            LIMIT %s
            """,
            (likes, likes, likes, likes, likes, likes, likes, likes, limit),
        )
        return [_meta_reference_from_deck_row(row) for row in cursor.fetchall()]
    finally:
        conn.close()


def _meta_query_terms(query: str) -> list[str]:
    text = str(query or "").strip()
    if not text:
        return []
    raw_parts: list[str] = []
    raw_parts.extend(re.findall(r"[<＜《「『【]([^>＞》」』】]{2,40})[>＞》」』】]", text))
    cleaned = re.sub(r"[<＞>《》「」『』【】（）()\[\]：:，,。?？!！/\\]", " ", text)
    for token in re.split(r"[\s\u3000]+", cleaned):
        token = token.strip()
        if token:
            raw_parts.append(token)
    raw_parts.extend(re.findall(r"[A-Za-z0-9\u3400-\u9fff]{2,40}", cleaned))

    filler = (
        "有沒有", "有没有", "推薦", "推荐", "牌組", "卡組", "卡组", "構築", "构筑",
        "一套", "想組", "想组", "請問", "请问", "幫我", "帮我", "找", "deck", "Deck",
    )
    generic = {"ex", "EX", "v", "V", "vstar", "VSTAR", "vmax", "VMAX", "gx", "GX"}

    def add_term(value: str) -> None:
        term = str(value or "").strip()
        if len(term) < 2 or term in generic:
            return
        if term not in seen:
            seen.add(term)
            terms.append(term)

        without_suffix = re.sub(r"\s*(ex|EX|VSTAR|VMAX|V|GX)$", "", term).strip()
        if len(without_suffix) >= 2 and without_suffix not in generic and without_suffix not in seen:
            seen.add(without_suffix)
            terms.append(without_suffix)

    terms: list[str] = []
    seen = set()
    for part in raw_parts:
        term = str(part or "").strip()
        for word in filler:
            term = term.replace(word, " ")
        term = re.sub(r"\s+", " ", term).strip(" 的")
        term = re.sub(r"(牌組|卡組|卡组|構築|构筑|deck)$", "", term, flags=re.I).strip()
        if len(term) < 2 or term in generic:
            continue
        add_term(term)

        if "的" in term and not any(mark in term for mark in "<>＜＞《》「」『』【】"):
            owner, subject = term.split("的", 1)
            add_term(owner)
            add_term(subject)

        for split_term in re.split(r"[\s/]+", term):
            add_term(split_term)

    return terms[:8]


def _meta_reference_from_embedding(item: dict[str, Any]) -> dict[str, Any]:
    meta = item.get("metadata") or {}
    if item.get("source_type") == "meta_archetype":
        return {
            "type": "archetype",
            "archetype": meta.get("archetype") or item.get("title"),
            "deck_count": meta.get("deck_count"),
            "latest_date": meta.get("latest_date"),
            "best_placement": meta.get("best_placement"),
            "common_cards": meta.get("common_cards") or [],
            "sample_decks": meta.get("sample_decks") or [],
            "score": item.get("score"),
        }
    return {
        "type": "deck",
        "deck_id": meta.get("deck_id") or item.get("source_id"),
        "archetype": meta.get("archetype") or item.get("title"),
        "player_name": meta.get("player_name"),
        "placement": meta.get("placement"),
        "tournament_title": meta.get("tournament_title"),
        "date": meta.get("date"),
        "players": meta.get("players"),
        "deck_url": meta.get("deck_url"),
        "cards": meta.get("cards") or [],
        "score": item.get("score"),
    }


def _meta_reference_from_deck_row(row: dict[str, Any]) -> dict[str, Any]:
    matched_cards = row.get("matched_cards") or []
    if isinstance(matched_cards, str):
        try:
            matched_cards = json.loads(matched_cards)
        except Exception:
            matched_cards = []
    return {
        "type": "deck",
        "deck_id": row.get("deck_id"),
        "archetype": row.get("archetype") or row.get("title"),
        "player_name": row.get("player_name"),
        "placement": row.get("placement"),
        "tournament_title": row.get("tournament_title"),
        "date": row.get("date").isoformat() if row.get("date") else None,
        "players": row.get("players"),
        "deck_url": row.get("deck_url"),
        "matched_cards": matched_cards[:12] if isinstance(matched_cards, list) else [],
    }


def get_meta_deck_cards(deck_id: str, language: str = "tw", mode: str = "normal") -> dict[str, Any]:
    deck_id = str(deck_id or "").strip()
    if not deck_id:
        return {"success": False, "error": "Missing deck_id", "deck_id": deck_id, "cards": []}

    language = language if language in ("tw", "jp", "en") else "tw"
    mode = mode if mode in ("normal", "bling") else "normal"
    try:
        from services.limitless_decks.repository import get_deck_cards as limitless_get_deck_cards

        result = limitless_get_deck_cards(deck_id, language=language, mode=mode, include_debug=False)
    except Exception as exc:
        return {"success": False, "error": str(exc), "deck_id": deck_id, "cards": []}

    if not result.get("success"):
        return {
            "success": False,
            "error": result.get("error") or "Limitless deck not found",
            "deck_id": deck_id,
            "cards": [],
        }

    deck = result.get("deck") if isinstance(result.get("deck"), dict) else {}
    cards = [_normalize_limitless_deck_card(card, language) for card in (result.get("cards") or [])]
    total_count = sum(int(card.get("count") or 0) for card in cards)
    section_counts = Counter()
    for card in cards:
        section_counts[str(card.get("section") or "unknown")] += int(card.get("count") or 0)

    return {
        "success": True,
        "source": "limitless",
        "deck_id": deck_id,
        "name": deck.get("archetype_zh") or deck.get("title_zh") or deck.get("archetype") or deck.get("title") or deck_id,
        "language": language,
        "mode": mode,
        "deck": deck,
        "cards": cards,
        "total_count": total_count,
        "section_counts": dict(section_counts),
    }


def _normalize_limitless_deck_card(card: dict[str, Any], language: str) -> dict[str, Any]:
    item = dict(card or {})
    count = int(item.get("count") or 0)
    section = str(item.get("section") or "unknown").strip() or "unknown"
    local_id = str(item.get("local_tw_card_id") or item.get("card_id") or "").strip()
    detail = get_card_detail(local_id, "tw") if local_id and language == "tw" else None

    if detail:
        image_url = item.get("image_url") or detail.get("image_url") or item.get("limitless_image_url") or ""
        merged = {**detail, **item}
        merged["image_url"] = image_url
    else:
        merged = item

    name = merged.get("name") or merged.get("card_name") or merged.get("jp_card_name") or ""
    merged.update({
        "count": count,
        "section": section,
        "name": name,
        "card_name": merged.get("card_name") or name,
        "card_id": merged.get("card_id") or local_id or None,
        "id": merged.get("id") or merged.get("card_id") or local_id or None,
        "language": language,
        "source": "limitless",
        "image_url": merged.get("image_url") or merged.get("limitless_image_url") or "",
    })
    if not merged.get("card_type"):
        if section == "pokemon":
            merged["card_type"] = "Pokémon"
        elif section == "energy":
            merged["card_type"] = "Energy"
        else:
            merged["card_type"] = "Trainer"
    return merged


def summarize_meta_archetype(query: str) -> dict[str, Any]:
    semantic = _meta_from_embedding(query, ["meta_archetype"], 3)
    if semantic:
        ref = _meta_reference_from_embedding(semantic[0])
        return {
            "query": query,
            "summary": f"{ref.get('archetype')} has {ref.get('deck_count') or 0} indexed Limitless deck(s).",
            "reference": ref,
            "common_cards": ref.get("common_cards") or [],
            "sample_decks": ref.get("sample_decks") or [],
        }

    decks = search_meta_decks(query, 10)
    card_counter = Counter()
    for deck in decks:
        for card in deck.get("cards") or []:
            name = str(card.get("name") or card.get("card_name") or "").strip()
            if name:
                card_counter[name] += 1
    return {
        "query": query,
        "summary": f"Found {len(decks)} matching Limitless deck(s).",
        "reference": decks[0] if decks else None,
        "common_cards": [{"name": name, "appearances": count} for name, count in card_counter.most_common(20)],
        "sample_decks": decks[:5],
    }


def analyze_current_deck(deck: list[dict[str, Any]]) -> dict[str, Any]:
    deck = deck if isinstance(deck, list) else []
    names = Counter(str(card.get("name") or "").strip() for card in deck if card.get("name"))
    types = Counter()
    marks = Counter()
    for card in deck:
        ctype = str(card.get("card_type") or "").lower()
        if "energy" in ctype:
            types["energy"] += 1
        elif "pok" in ctype:
            types["pokemon"] += 1
        else:
            types["trainer"] += 1
        mark = str(card.get("regulation_mark") or "").strip().upper()
        if mark:
            marks[mark] += 1
    over_four = [
        {"name": name, "count": count}
        for name, count in names.items()
        if count > 4 and not _is_basic_energy_name(name)
    ]
    non_standard = [
        {"name": card.get("name"), "regulation_mark": card.get("regulation_mark")}
        for card in deck
        if str(card.get("regulation_mark") or "").strip().upper() not in STANDARD_MARKS
        and not _is_basic_energy_name(card.get("name"))
    ]
    return {
        "card_count": len(deck),
        "type_counts": dict(types),
        "regulation_counts": dict(marks),
        "top_counts": [{"name": name, "count": count} for name, count in names.most_common(30)],
        "over_four": over_four,
        "non_standard": non_standard[:30],
        "available_slots": max(0, 60 - len(deck)),
    }


def _is_basic_energy_name(name: Any) -> bool:
    text = str(name or "")
    return "基本" in text and "能量" in text or "Basic" in text and "Energy" in text


def _find_card_for_action(name: str, language: str = "tw") -> dict[str, Any] | None:
    results = _keyword_card_search(name, language, 5, {"standard_marks": list(STANDARD_MARKS), "language": language})
    if not results and _is_basic_energy_name(name):
        results = _keyword_card_search_any_mark(name, language, 5)
    if not results:
        results = semantic_search_cards(name, 5, {"standard_marks": list(STANDARD_MARKS), "language": language})
    if not results:
        return None
    exact = [card for card in results if str(card.get("name") or "").strip() == name]
    return (exact or results)[0]


def _keyword_card_search_any_mark(query: str, language: str = "tw", limit: int = 5) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        return []
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    search = f"%{query}%"
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_select_columns(table)}
            FROM {table}
            WHERE (name ILIKE %s OR COALESCE(description, '') ILIKE %s)
              AND card_type = 'Energy'
            ORDER BY CASE WHEN name = %s THEN 0 ELSE 1 END, card_id DESC
            LIMIT %s
            """,
            (search, search, query, max(1, min(int(limit or 5), 10))),
        )
        return [_card_payload(row, folder_lang, True) for row in cursor.fetchall()]
    finally:
        conn.close()


def propose_deck_patch(
    intent: str,
    deck: list[dict[str, Any]],
    retrieved_context: dict[str, Any] | None = None,
    language: str = "tw",
) -> dict[str, Any]:
    intent = str(intent or "")
    deck = deck if isinstance(deck, list) else []
    actions: list[dict[str, Any]] = []

    replace_match = re.search(r"(?:把|將)?\s*(\d+|一|二|兩|三|四)\s*張?(.+?)(?:換成|改成|替換成)(.+)", intent)
    if replace_match:
        count = _zh_int(replace_match.group(1))
        remove_name = _clean_card_name(replace_match.group(2))
        add_name = _clean_card_name(replace_match.group(3))
        if remove_name:
            actions.append({"type": "remove_card", "card_name": remove_name, "count": count})
        if add_name:
            card = _find_card_for_action(add_name, language)
            actions.append({"type": "add_card", "card_name": add_name, "count": count, "card": card})

    fill_match = re.search(r"(?:補滿|補到|補齊).{0,8}(基本.+?能量|.+?基本能量|Basic .+? Energy)", intent, re.I)
    if fill_match:
        energy_name = _clean_card_name(fill_match.group(1))
        current = len(deck) + sum(a.get("count", 0) for a in actions if a.get("type") == "add_card") - sum(a.get("count", 0) for a in actions if a.get("type") == "remove_card")
        count = max(0, 60 - current)
        card = _find_card_for_action(energy_name, language)
        actions.append({"type": "add_card", "card_name": energy_name, "count": count, "card": card, "reason": "fill_to_60"})

    if not actions and retrieved_context:
        cards = retrieved_context.get("cards") or []
        for card in cards[:3]:
            if card.get("card_id"):
                actions.append({"type": "add_card", "card_name": card.get("name"), "count": 1, "card": card})

    diff = build_deck_diff(deck, actions)
    return {"deck_actions": actions, "deck_diff": diff}


def _zh_int(value: str) -> int:
    mapping = {"一": 1, "二": 2, "兩": 2, "三": 3, "四": 4}
    value = str(value or "").strip()
    if value.isdigit():
        return int(value)
    return mapping.get(value, 1)


def _clean_card_name(value: str) -> str:
    text = str(value or "").strip()
    text = re.split(r"[，,。；;、\n]", text)[0]
    text = re.sub(r"^(?:的|兩張|二張|一張|三張|四張|\s)+", "", text).strip()
    return text.strip(" 「」『』\"'")


def build_deck_diff(deck: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, Any]:
    current = Counter(str(card.get("name") or "").strip() for card in deck if card.get("name"))
    projected = Counter(current)
    additions = []
    removals = []
    warnings = []
    for action in actions or []:
        name = str(action.get("card_name") or action.get("name") or "").strip()
        count = max(0, int(action.get("count") or 0))
        if not name or count <= 0:
            continue
        if action.get("type") in ("remove_card", "remove"):
            actual = min(projected.get(name, 0), count)
            if actual < count:
                warnings.append(f"{name} only has {projected.get(name, 0)} copy/copies in the current deck.")
            projected[name] -= actual
            removals.append({"card_name": name, "count": count, "available": current.get(name, 0)})
        elif action.get("type") in ("add_card", "add"):
            projected[name] += count
            additions.append({"card_name": name, "count": count, "card": action.get("card")})
    current_total = len(deck)
    projected_total = current_total + sum(item["count"] for item in additions) - sum(min(item["count"], item.get("available", 0)) for item in removals)
    if projected_total > 60:
        warnings.append(f"Projected deck has {projected_total} cards, over the 60-card limit.")
    return {
        "current_total": current_total,
        "projected_total": projected_total,
        "additions": additions,
        "removals": removals,
        "warnings": warnings,
    }


# Compatibility helpers used by older assistant paths/tests.
def search_skill_keyword(keyword: str, language: str = "tw", limit: int = CARD_LIMIT, skill_type: str = "") -> list[dict[str, Any]]:
    return semantic_search_cards(keyword, limit, {"language": language, "standard_marks": list(STANDARD_MARKS)})


def search_skill_terms(terms: list[str], language: str = "tw", limit: int = CARD_LIMIT, skill_type: str = "") -> list[dict[str, Any]]:
    return semantic_search_cards(" ".join(terms or []), limit, {"language": language, "standard_marks": list(STANDARD_MARKS)})


def search_hand_size_damage(language: str = "tw", limit: int = CARD_LIMIT) -> list[dict[str, Any]]:
    return semantic_search_cards("依照手牌數量造成傷害", limit, {"language": language, "standard_marks": list(STANDARD_MARKS)})


def search_trainer_energy_attach(language: str = "tw", limit: int = CARD_LIMIT, subtypes: list[str] | None = None) -> list[dict[str, Any]]:
    query = "從手牌或牌庫附加能量的訓練家卡"
    cards = semantic_search_cards(query, limit, {"language": language, "standard_marks": list(STANDARD_MARKS)})
    if subtypes:
        wanted = {str(item) for item in subtypes}
        filtered = [card for card in cards if str(card.get("sub_type") or "") in wanted]
        return filtered or cards
    return cards
