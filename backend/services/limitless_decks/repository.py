from __future__ import annotations

import json
import os
import re
import threading
from datetime import datetime

import config
import database
from services.tcgdex.client import get_client as get_tcgdex_client

from .schema import LIMITLESS_SCHEMA_SQL


_schema_lock = threading.Lock()
_schema_ready = False
_SCHEMA_ADVISORY_KEY = 2026060101


def ensure_schema(conn=None) -> None:
    global _schema_ready
    if _schema_ready:
        return
    owns_conn = conn is None
    conn = conn or database.get_db_connection()
    if not conn:
        return
    try:
        if not owns_conn:
            cursor = conn.cursor()
            cursor.execute(LIMITLESS_SCHEMA_SQL)
            _schema_ready = True
        else:
            with _schema_lock:
                if _schema_ready:
                    return
                cursor = conn.cursor()
                cursor.execute("SELECT pg_advisory_xact_lock(%s)", (_SCHEMA_ADVISORY_KEY,))
                cursor.execute(LIMITLESS_SCHEMA_SQL)
                conn.commit()
                _schema_ready = True
    except Exception:
        conn.rollback()
        raise
    finally:
        if owns_conn:
            conn.close()


def normalize_set_number(set_number: str | None) -> str:
    value = str(set_number or "").strip()
    if "/" in value:
        value = value.split("/", 1)[0].strip()
    return value


def set_number_candidates(set_number: str | None) -> list[str]:
    value = normalize_set_number(set_number)
    if not value:
        return []
    candidates = [value]
    if value.isdigit():
        for candidate in (str(int(value)), value.zfill(3)):
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def parse_skills(skills_data) -> list[dict]:
    if not skills_data:
        return []
    if isinstance(skills_data, list):
        return skills_data
    try:
        return json.loads(skills_data)
    except Exception:
        return []


def _json_value(value) -> str:
    return json.dumps(value or [], ensure_ascii=False)


ARCHETYPE_PHRASE_TRANSLATIONS = {
    "Festival Lead": "祭典主角",
    "Tera Box": "太晶盒",
    "Future Box": "未來盒",
    "Poison Box": "中毒盒",
    "Snorlax Stall": "卡比獸封鎖",
    "Great Tusk Mill": "雄偉牙棄牌",
}

ARCHETYPE_WORD_TRANSLATIONS = {
    "Box": "盒子",
    "Control": "控制",
    "Stall": "封鎖",
    "Mill": "棄牌",
    "Combo": "組合",
    "Donk": "速攻",
    "Future": "未來",
    "Poison": "中毒",
    "Tera": "太晶",
    "Mega": "超級",
    "Festival": "祭典",
    "Lead": "主角",
}


def _normalize_archetype_key(value: str | None) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", str(value or "").lower()))


def _strip_variant_suffix(tw_name: str, source_segment: str) -> str:
    source_key = _normalize_archetype_key(source_segment)
    if any(token in source_key.split() for token in ("ex", "gx", "v", "vmax", "vstar")):
        return tw_name
    return re.sub(r"(?:ex|GX|VSTAR|VMAX|V)$", "", str(tw_name or "")).strip()


def _candidate_keys(en_name: str) -> list[str]:
    key = _normalize_archetype_key(en_name)
    if not key:
        return []
    keys = [key]
    tokens = key.split()
    while tokens and tokens[-1] in ("ex", "gx", "v", "vmax", "vstar"):
        tokens = tokens[:-1]
        shorter = " ".join(tokens)
        if shorter and shorter not in keys:
            keys.append(shorter)
    if key.startswith("team "):
        without_team = key.removeprefix("team ").strip()
        if without_team and without_team not in keys:
            keys.append(without_team)
    return keys


def _build_translation_candidates(card_pairs: list[dict]) -> list[dict]:
    candidates = []
    seen = set()
    for pair in card_pairs:
        en_name = str(pair.get("en_name") or "").strip()
        tw_name = str(pair.get("tw_name") or "").strip()
        if not en_name or not tw_name:
            continue
        for key in _candidate_keys(en_name):
            marker = (key, tw_name)
            if marker in seen:
                continue
            seen.add(marker)
            candidates.append({"key": key, "en_name": en_name, "tw_name": tw_name})
    candidates.sort(key=lambda item: len(item["key"]), reverse=True)
    return candidates


def _match_archetype_segment(segment: str, candidates: list[dict]) -> str | None:
    segment_key = _normalize_archetype_key(segment)
    if not segment_key:
        return None
    for candidate in candidates:
        key = candidate["key"]
        if segment_key == key or key.startswith(segment_key) or segment_key in key:
            return _strip_variant_suffix(candidate["tw_name"], segment)
    return None


def _translate_archetype_text(value: str | None, card_pairs: list[dict] | None = None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text in ARCHETYPE_PHRASE_TRANSLATIONS:
        return ARCHETYPE_PHRASE_TRANSLATIONS[text]

    tokens = text.split()
    candidates = _build_translation_candidates(card_pairs or [])
    translated = []
    index = 0
    while index < len(tokens):
        matched = None
        matched_len = 0
        max_window = min(4, len(tokens) - index)
        for window in range(max_window, 0, -1):
            segment = " ".join(tokens[index:index + window])
            if segment in ARCHETYPE_PHRASE_TRANSLATIONS:
                matched = ARCHETYPE_PHRASE_TRANSLATIONS[segment]
                matched_len = window
                break
            matched = _match_archetype_segment(segment, candidates)
            if matched:
                matched_len = window
                break
            if window == 1 and segment in ARCHETYPE_WORD_TRANSLATIONS:
                matched = ARCHETYPE_WORD_TRANSLATIONS[segment]
                matched_len = 1
                break
        if matched:
            translated.append(matched)
            index += matched_len
        else:
            translated.append(tokens[index])
            index += 1

    result = " ".join(part for part in translated if part).strip()
    return "" if result == text else result


def _apply_deck_localization(cursor, decks: list[dict]) -> None:
    deck_ids = [str(deck.get("deck_id") or "").strip() for deck in decks if deck.get("deck_id")]
    pairs_by_deck = {deck_id: [] for deck_id in deck_ids}
    if deck_ids:
        cursor.execute(
            """
            SELECT jp.deck_id, en.card_name AS en_name, tw.name AS tw_name
            FROM limitless_deck_cards jp
            JOIN limitless_deck_cards en
              ON en.deck_id = jp.deck_id
             AND en.language = 'en'
             AND en.mode = jp.mode
             AND en.section = jp.section
             AND en.line_order = jp.line_order
             AND en.count = jp.count
            JOIN cards tw ON tw.card_id = jp.local_tw_card_id
            WHERE jp.deck_id = ANY(%s::text[])
              AND jp.language = 'jp'
              AND jp.mode = 'normal'
              AND jp.section = 'pokemon'
            """,
            (deck_ids,),
        )
        for row in cursor.fetchall():
            pairs_by_deck.setdefault(row["deck_id"], []).append(dict(row))

    for deck in decks:
        pairs = pairs_by_deck.get(deck.get("deck_id"), [])
        deck["archetype_zh"] = _translate_archetype_text(deck.get("archetype"), pairs)
        deck["title_zh"] = _translate_archetype_text(deck.get("title"), pairs)
        tags = deck.get("tags") if isinstance(deck.get("tags"), list) else []
        deck["tags_zh"] = [_translate_archetype_text(tag, pairs) or tag for tag in tags]


def _matching_tournament_ids_for_card_query(cursor, like: str, limit: int = 5000) -> list[str]:
    cursor.execute(
        """
        SELECT DISTINCT d.tournament_id
        FROM limitless_decks d
        JOIN limitless_deck_cards c ON c.deck_id = d.deck_id
        LEFT JOIN cards tw ON tw.card_id = c.local_tw_card_id
        WHERE d.tournament_id IS NOT NULL
          AND (
            c.card_name ILIKE %s OR c.set_code ILIKE %s
            OR c.set_number ILIKE %s OR tw.name ILIKE %s
          )
        LIMIT %s
        """,
        (like, like, like, like, limit),
    )
    return [row["tournament_id"] for row in cursor.fetchall()]


def _image_url_for(row: dict | None, folder: str) -> str:
    if not row:
        return ""
    image_file = str(row.get("image_file") or "").strip()
    if not image_file:
        return ""
    if image_file.startswith(("http://", "https://")):
        return image_file
    if folder == "images_jp":
        path = os.path.join(config.JP_IMAGE_FOLDER, image_file)
        if os.path.exists(path):
            return f"/images_jp/{image_file}"
        return f"/images_jp/{image_file}"
    path = os.path.join(config.IMAGE_FOLDER, image_file)
    if os.path.exists(path):
        return f"/images/{image_file}"
    return f"https://asia.pokemon-card.com/tw/card-img/{image_file}"


def _card_payload_from_row(row: dict | None, folder: str) -> dict | None:
    if not row:
        return None
    card = dict(row)
    if "skills_json" in card:
        card["skills"] = parse_skills(card.get("skills_json"))
    card["image_url"] = _image_url_for(card, folder)
    card["id"] = card.get("card_id")
    return card


def _skill_signature_from_list(skills: list[dict]) -> list[dict]:
    signature = []
    for skill in skills or []:
        signature.append({
            "name": str(skill.get("name") or "").strip(),
            "cost": [str(c).strip() for c in (skill.get("cost") or [])],
            "damage": str(skill.get("damage") or "").strip(),
            "effect": str(skill.get("effect") or skill.get("text") or "").strip(),
        })
    return signature


def _tcgdex_skills(card: dict) -> list[dict]:
    skills = []
    for ability in card.get("abilities") or []:
        skills.append({
            "name": ability.get("name", ""),
            "cost": [],
            "damage": "",
            "effect": ability.get("effect", "") or "",
        })
    for attack in card.get("attacks") or []:
        damage = attack.get("damage", "")
        skills.append({
            "name": attack.get("name", ""),
            "cost": attack.get("cost") or [],
            "damage": "" if damage is None else str(damage),
            "effect": attack.get("effect", "") or "",
        })
    return skills


def _tcgdex_image_url(image_value: str | None) -> str:
    image_value = str(image_value or "").strip()
    if not image_value:
        return ""
    if image_value.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        return image_value
    return f"{image_value.rstrip('/')}/high.webp"


ENERGY_NAME_MAP = {
    "基本草エネルギー": "基本草能量",
    "基本炎エネルギー": "基本火能量",
    "基本水エネルギー": "基本水能量",
    "基本雷エネルギー": "基本雷能量",
    "基本超エネルギー": "基本超能量",
    "基本闘エネルギー": "基本鬥能量",
    "基本悪エネルギー": "基本惡能量",
    "基本鋼エネルギー": "基本鋼能量",
}


def log_event(level: str, context: str, message: str, detail: str | None = None, conn=None) -> None:
    owns_conn = conn is None
    conn = conn or database.get_db_connection()
    if not conn:
        return
    try:
        ensure_schema(conn)
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO limitless_update_logs (level, context, message, detail)
            VALUES (%s, %s, %s, %s)
            """,
            (level, context, message, detail),
        )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        if owns_conn:
            conn.close()


def upsert_tournament(cursor, tournament: dict, raw_html: str | None = None) -> None:
    cursor.execute(
        """
        INSERT INTO limitless_tournaments (
            tournament_id, source_region, title, date, location, format, players, url,
            last_seen_at, raw_html
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
        ON CONFLICT (tournament_id) DO UPDATE SET
            source_region = EXCLUDED.source_region,
            title = COALESCE(EXCLUDED.title, limitless_tournaments.title),
            date = COALESCE(EXCLUDED.date, limitless_tournaments.date),
            location = COALESCE(EXCLUDED.location, limitless_tournaments.location),
            format = COALESCE(EXCLUDED.format, limitless_tournaments.format),
            players = COALESCE(EXCLUDED.players, limitless_tournaments.players),
            url = COALESCE(EXCLUDED.url, limitless_tournaments.url),
            last_seen_at = CURRENT_TIMESTAMP,
            raw_html = COALESCE(EXCLUDED.raw_html, limitless_tournaments.raw_html)
        """,
        (
            tournament.get("tournament_id"),
            tournament.get("source_region"),
            tournament.get("title"),
            tournament.get("date"),
            tournament.get("location"),
            tournament.get("format"),
            tournament.get("players"),
            tournament.get("url"),
            raw_html,
        ),
    )


def upsert_deck_metadata(cursor, deck: dict) -> None:
    cursor.execute(
        """
        INSERT INTO limitless_decks (
            deck_id, tournament_id, player_name, placement, archetype, title, tags,
            deck_url, source_region
        ) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s)
        ON CONFLICT (deck_id) DO UPDATE SET
            tournament_id = COALESCE(EXCLUDED.tournament_id, limitless_decks.tournament_id),
            player_name = COALESCE(EXCLUDED.player_name, limitless_decks.player_name),
            placement = COALESCE(EXCLUDED.placement, limitless_decks.placement),
            archetype = COALESCE(EXCLUDED.archetype, limitless_decks.archetype),
            title = COALESCE(EXCLUDED.title, limitless_decks.title),
            tags = COALESCE(EXCLUDED.tags, limitless_decks.tags),
            deck_url = COALESCE(EXCLUDED.deck_url, limitless_decks.deck_url),
            source_region = COALESCE(EXCLUDED.source_region, limitless_decks.source_region)
        """,
        (
            deck.get("deck_id"),
            deck.get("tournament_id"),
            deck.get("player_name"),
            deck.get("placement"),
            deck.get("archetype"),
            deck.get("title"),
            _json_value(deck.get("tags")),
            deck.get("deck_url"),
            deck.get("source_region"),
        ),
    )


def upsert_tournament_deck_entry(cursor, deck: dict, entry_order: int | None = None) -> None:
    tournament_id = deck.get("tournament_id")
    deck_id = deck.get("deck_id")
    if not tournament_id or not deck_id:
        return
    placement = deck.get("placement")
    player_name = deck.get("player_name") or ""
    entry_id = f"{tournament_id}:{placement or entry_order or 0}:{deck_id}:{player_name}"
    cursor.execute(
        """
        INSERT INTO limitless_tournament_deck_entries (
            entry_id, tournament_id, deck_id, player_name, placement, archetype, title,
            tags, deck_url, source_region, entry_order, last_seen_at
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (entry_id) DO UPDATE SET
            tournament_id = EXCLUDED.tournament_id,
            deck_id = EXCLUDED.deck_id,
            player_name = COALESCE(EXCLUDED.player_name, limitless_tournament_deck_entries.player_name),
            placement = COALESCE(EXCLUDED.placement, limitless_tournament_deck_entries.placement),
            archetype = COALESCE(EXCLUDED.archetype, limitless_tournament_deck_entries.archetype),
            title = COALESCE(EXCLUDED.title, limitless_tournament_deck_entries.title),
            tags = COALESCE(EXCLUDED.tags, limitless_tournament_deck_entries.tags),
            deck_url = COALESCE(EXCLUDED.deck_url, limitless_tournament_deck_entries.deck_url),
            source_region = COALESCE(EXCLUDED.source_region, limitless_tournament_deck_entries.source_region),
            entry_order = COALESCE(EXCLUDED.entry_order, limitless_tournament_deck_entries.entry_order),
            last_seen_at = CURRENT_TIMESTAMP
        """,
        (
            entry_id,
            tournament_id,
            deck_id,
            player_name,
            placement,
            deck.get("archetype"),
            deck.get("title"),
            _json_value(deck.get("tags")),
            deck.get("deck_url"),
            deck.get("source_region"),
            entry_order,
        ),
    )


def find_local_jp_card(cursor, set_code: str | None, set_number: str | None) -> str | None:
    candidates = set_number_candidates(set_number)
    if not set_code or not candidates:
        return None
    placeholders = ",".join(["%s"] * len(candidates))
    cursor.execute(
        f"""
        SELECT card_id FROM jp_cards
        WHERE set_code = %s
          AND (
            set_number IN ({placeholders})
            OR split_part(set_number, '/', 1) IN ({placeholders})
          )
        ORDER BY card_id DESC
        LIMIT 1
        """,
        [set_code] + candidates + candidates,
    )
    row = cursor.fetchone()
    return row["card_id"] if row else None


def find_local_tw_card(cursor, set_code: str | None, set_number: str | None) -> str | None:
    row = find_local_tw_card_row(cursor, set_code, set_number)
    return row["card_id"] if row else None


def find_local_tw_card_row(cursor, set_code: str | None, set_number: str | None) -> dict | None:
    candidates = set_number_candidates(set_number)
    if not set_code or not candidates:
        return None
    placeholders = ",".join(["%s"] * len(candidates))
    cursor.execute(
        f"""
        SELECT *
        FROM cards
        WHERE set_code = %s
          AND (
            set_number IN ({placeholders})
            OR split_part(set_number, '/', 1) IN ({placeholders})
          )
        ORDER BY
            CASE WHEN COALESCE(image_file, '') <> '' THEN 0 ELSE 1 END,
            CASE WHEN COALESCE(skills_json::text, '') NOT IN ('', '[]') THEN 0 ELSE 1 END,
            CASE WHEN card_id ~ '^[0-9]+$' THEN card_id::integer ELSE 0 END DESC,
            card_id DESC
        LIMIT 1
        """,
        [set_code] + candidates + candidates,
    )
    return cursor.fetchone()


def find_local_tw_candidates(cursor, set_code: str | None, set_number: str | None, limit: int = 5) -> list[dict]:
    candidates = set_number_candidates(set_number)
    if not set_code or not candidates:
        return []
    placeholders = ",".join(["%s"] * len(candidates))
    cursor.execute(
        f"""
        SELECT card_id, name, image_file, set_code, set_number
        FROM cards
        WHERE set_code = %s
          AND (
            set_number IN ({placeholders})
            OR split_part(set_number, '/', 1) IN ({placeholders})
          )
        ORDER BY
            CASE WHEN COALESCE(image_file, '') <> '' THEN 0 ELSE 1 END,
            CASE WHEN card_id ~ '^[0-9]+$' THEN card_id::integer ELSE 0 END DESC,
            card_id DESC
        LIMIT %s
        """,
        [set_code] + candidates + candidates + [limit],
    )
    return [dict(row) for row in cursor.fetchall()]


def save_decklist(cursor, deck_id: str, parsed: dict) -> None:
    language = parsed["language"]
    mode = parsed["mode"]
    raw_column = f"raw_{language}_{'bling' if mode == 'bling' else 'text'}"
    if language == "jp" and mode == "normal":
        raw_column = "raw_jp_text"
    elif language == "en" and mode == "normal":
        raw_column = "raw_en_text"
    elif language == "jp":
        raw_column = "raw_jp_bling_text"
    else:
        raw_column = "raw_en_bling_text"

    cursor.execute(
        f"""
        UPDATE limitless_decks
        SET title = COALESCE(NULLIF(%s, ''), title),
            fetched_at = CURRENT_TIMESTAMP,
            {raw_column} = %s
        WHERE deck_id = %s
        """,
        (parsed.get("title"), parsed.get("raw_text"), deck_id),
    )

    cursor.execute(
        """
        DELETE FROM limitless_deck_cards
        WHERE deck_id = %s AND language = %s AND mode = %s
        """,
        (deck_id, language, mode),
    )

    for card in parsed.get("cards", []):
        local_jp_card_id = None
        local_tw_card_id = None
        if language == "jp":
            local_jp_card_id = find_local_jp_card(cursor, card.get("set_code"), card.get("set_number"))
            local_tw_card_id = find_local_tw_card(cursor, card.get("set_code"), card.get("set_number"))
        else:
            local_tw_card_id = find_local_tw_card(cursor, card.get("set_code"), card.get("set_number"))
        cursor.execute(
            """
            INSERT INTO limitless_deck_cards (
                deck_id, language, mode, section, line_order, count, card_name,
                set_code, set_number, local_jp_card_id, local_tw_card_id,
                limitless_card_url, limitless_image_url
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (deck_id, language, mode, line_order) DO UPDATE SET
                section = EXCLUDED.section,
                count = EXCLUDED.count,
                card_name = EXCLUDED.card_name,
                set_code = EXCLUDED.set_code,
                set_number = EXCLUDED.set_number,
                local_jp_card_id = EXCLUDED.local_jp_card_id,
                local_tw_card_id = EXCLUDED.local_tw_card_id,
                limitless_card_url = EXCLUDED.limitless_card_url,
                limitless_image_url = EXCLUDED.limitless_image_url
            """,
            (
                deck_id,
                language,
                mode,
                card.get("section", "unknown"),
                card.get("line_order"),
                card.get("count"),
                card.get("card_name"),
                card.get("set_code"),
                card.get("set_number"),
                local_jp_card_id,
                local_tw_card_id,
                card.get("limitless_card_url"),
                card.get("limitless_image_url"),
            ),
        )


def create_mappings_for_deck(cursor, deck_id: str, mode: str) -> int:
    cursor.execute(
        """
        SELECT
            jp.set_code AS jp_set_code, jp.set_number AS jp_set_number, jp.card_name AS jp_name,
            en.set_code AS en_set_code, en.set_number AS en_set_number, en.card_name AS en_name
        FROM limitless_deck_cards jp
        JOIN limitless_deck_cards en
          ON en.deck_id = jp.deck_id
         AND en.mode = jp.mode
         AND en.line_order = jp.line_order
         AND en.section = jp.section
         AND en.count = jp.count
        WHERE jp.deck_id = %s
          AND jp.language = 'jp'
          AND en.language = 'en'
          AND jp.mode = %s
          AND COALESCE(jp.set_code, '') <> ''
          AND COALESCE(jp.set_number, '') <> ''
          AND COALESCE(en.set_code, '') <> ''
          AND COALESCE(en.set_number, '') <> ''
        """,
        (deck_id, mode),
    )
    rows = cursor.fetchall()
    for row in rows:
        cursor.execute(
            """
            INSERT INTO limitless_card_mapping (
                jp_set_code, jp_set_number, jp_name,
                en_set_code, en_set_number, en_name,
                mode, confidence, source_deck_id
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT (jp_set_code, jp_set_number, en_set_code, en_set_number, mode)
            DO UPDATE SET
                jp_name = COALESCE(EXCLUDED.jp_name, limitless_card_mapping.jp_name),
                en_name = COALESCE(EXCLUDED.en_name, limitless_card_mapping.en_name),
                source_deck_id = COALESCE(limitless_card_mapping.source_deck_id, EXCLUDED.source_deck_id)
            """,
            (
                row["jp_set_code"],
                row["jp_set_number"],
                row["jp_name"],
                row["en_set_code"],
                row["en_set_number"],
                row["en_name"],
                mode,
                1.0,
                deck_id,
            ),
        )
    return len(rows)


def deck_needs_fetch(cursor, deck_id: str, stale_hours: int = 24) -> bool:
    cursor.execute(
        """
        SELECT fetched_at FROM limitless_decks
        WHERE deck_id = %s
          AND fetched_at IS NOT NULL
          AND fetched_at > CURRENT_TIMESTAMP - (%s || ' hours')::interval
        """,
        (deck_id, stale_hours),
    )
    return cursor.fetchone() is None


def list_tournaments(q: str = "", page: int = 1, region: str = "", fmt: str = "") -> dict:
    ensure_schema()
    page = max(1, int(page or 1))
    per_page = 20

    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "Database unavailable"}
    try:
        cursor = conn.cursor()
        params = []
        where = ["1=1"]
        if region:
            where.append("t.source_region = %s")
            params.append(region)
        if fmt:
            where.append("COALESCE(t.format, '') = %s")
            params.append(fmt)
        if q:
            for term in [t for t in re.split(r"[\s\u3000]+", q) if t]:
                like = f"%{term.replace('%', '').replace('_', '')}%"
                card_tournament_ids = _matching_tournament_ids_for_card_query(cursor, like)
                where.append(
                    """
                    (
                        t.title ILIKE %s OR t.location ILIKE %s
                        OR EXISTS (
                            SELECT 1
                            FROM limitless_decks d
                            WHERE d.tournament_id = t.tournament_id
                              AND (
                                d.player_name ILIKE %s OR d.archetype ILIKE %s
                                OR d.title ILIKE %s OR d.tags::text ILIKE %s
                              )
                        )
                        OR t.tournament_id = ANY(%s::text[])
                    )
                    """
                )
                params.extend([like] * 6 + [card_tournament_ids])
        where_sql = " AND ".join(where)
        cursor.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM limitless_tournaments t
            WHERE {where_sql}
            """,
            params,
        )
        total = cursor.fetchone()["cnt"]
        cursor.execute(
            f"""
            SELECT t.tournament_id, t.source_region, t.title, t.date, t.location,
                   t.format, t.players, t.url, t.last_seen_at,
                   (t.raw_html IS NOT NULL) AS detail_synced
            FROM limitless_tournaments t
            WHERE {where_sql}
            ORDER BY COALESCE(t.date, DATE '1900-01-01') DESC, t.last_seen_at DESC
            LIMIT %s OFFSET %s
            """,
            params + [per_page, (page - 1) * per_page],
        )
        tournaments = [dict(row) for row in cursor.fetchall()]
        tournament_ids = [t["tournament_id"] for t in tournaments]
        stats_by_id = {}
        if tournament_ids:
            cursor.execute(
                """
                WITH ids AS (
                    SELECT unnest(%s::text[]) AS tournament_id
                ),
                entry_stats AS (
                    SELECT e.tournament_id,
                           COUNT(*) AS entry_count,
                           MIN(e.placement) AS best_entry_placement
                    FROM limitless_tournament_deck_entries e
                    JOIN ids ON ids.tournament_id = e.tournament_id
                    GROUP BY e.tournament_id
                ),
                deck_stats AS (
                    SELECT d.tournament_id,
                           COUNT(DISTINCT d.deck_id) AS unique_deck_count,
                           COUNT(DISTINCT d.deck_id) FILTER (WHERE d.fetched_at IS NOT NULL) AS fetched_deck_count,
                           MIN(d.placement) AS best_deck_placement
                    FROM limitless_decks d
                    JOIN ids ON ids.tournament_id = d.tournament_id
                    GROUP BY d.tournament_id
                )
                SELECT ids.tournament_id,
                       COALESCE(NULLIF(entry_stats.entry_count, 0), deck_stats.unique_deck_count, 0) AS deck_count,
                       COALESCE(deck_stats.unique_deck_count, 0) AS unique_deck_count,
                       COALESCE(deck_stats.fetched_deck_count, 0) AS fetched_deck_count,
                       COALESCE(entry_stats.best_entry_placement, deck_stats.best_deck_placement) AS best_placement
                FROM ids
                LEFT JOIN entry_stats ON entry_stats.tournament_id = ids.tournament_id
                LEFT JOIN deck_stats ON deck_stats.tournament_id = ids.tournament_id
                """,
                (tournament_ids,),
            )
            stats_by_id = {row["tournament_id"]: dict(row) for row in cursor.fetchall()}

        for row in tournaments:
            item = dict(row)
            item.update(stats_by_id.get(item["tournament_id"], {
                "deck_count": 0,
                "unique_deck_count": 0,
                "fetched_deck_count": 0,
                "best_placement": None,
            }))
            for key in ("date", "last_seen_at"):
                if item.get(key) is not None:
                    item[key] = item[key].isoformat()
            row.clear()
            row.update(item)
        _apply_deck_localization(cursor, tournaments)
        return {
            "success": True,
            "tournaments": tournaments,
            "total": total,
            "page": page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }
    finally:
        conn.close()


def list_tournament_decks(tournament_id: str, q: str = "") -> dict:
    ensure_schema()
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "Database unavailable"}
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT tournament_id, source_region, title, date, location, format, players, url
                   , (raw_html IS NOT NULL) AS detail_synced
            FROM limitless_tournaments
            WHERE tournament_id = %s
            """,
            (tournament_id,),
        )
        tournament = cursor.fetchone()
        if not tournament:
            return {"success": False, "error": "Tournament not found"}
        cursor.execute("SELECT COUNT(*) AS cnt FROM limitless_tournament_deck_entries WHERE tournament_id = %s", (tournament_id,))
        has_entries = cursor.fetchone()["cnt"] > 0
        if has_entries:
            cursor.execute(
                """
                SELECT e.entry_id, e.deck_id, e.tournament_id, e.player_name, e.placement,
                       e.archetype, e.title, e.tags, e.deck_url, e.source_region,
                       e.entry_order, d.fetched_at
                FROM limitless_tournament_deck_entries e
                LEFT JOIN limitless_decks d ON d.deck_id = e.deck_id
                WHERE e.tournament_id = %s
                ORDER BY e.placement NULLS LAST, e.entry_order NULLS LAST, e.player_name ASC NULLS LAST
                """,
                (tournament_id,),
            )
        else:
            cursor.execute(
                """
                SELECT deck_id, tournament_id, player_name, placement, archetype, title,
                       tags, deck_url, source_region, fetched_at
                FROM limitless_decks
                WHERE tournament_id = %s
                ORDER BY placement NULLS LAST, player_name ASC NULLS LAST
                """,
                (tournament_id,),
            )
        decks = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get("fetched_at") is not None:
                item["fetched_at"] = item["fetched_at"].isoformat()
            if isinstance(item.get("tags"), str):
                try:
                    item["tags"] = json.loads(item["tags"])
                except Exception:
                    item["tags"] = []
            decks.append(item)
        _apply_deck_localization(cursor, decks)
        if q:
            terms = [term for term in re.split(r"[\s\u3000]+", q) if term]
            if terms:
                deck_ids = [deck["deck_id"] for deck in decks if deck.get("deck_id")]
                matches_by_deck = {deck_id: set() for deck_id in deck_ids}
                if deck_ids:
                    for term in terms:
                        like = f"%{term.replace('%', '').replace('_', '')}%"
                        cursor.execute(
                            """
                            SELECT DISTINCT c.deck_id
                            FROM limitless_deck_cards c
                            LEFT JOIN cards tw ON tw.card_id = c.local_tw_card_id
                            WHERE c.deck_id = ANY(%s::text[])
                              AND (
                                c.card_name ILIKE %s OR c.set_code ILIKE %s
                                OR c.set_number ILIKE %s OR tw.name ILIKE %s
                              )
                            """,
                            (deck_ids, like, like, like, like),
                        )
                        for row in cursor.fetchall():
                            matches_by_deck.setdefault(row["deck_id"], set()).add(term)

                def deck_matches(deck: dict) -> bool:
                    haystack = " ".join(str(value or "") for value in (
                        deck.get("player_name"), deck.get("archetype"), deck.get("title"),
                        deck.get("archetype_zh"), deck.get("title_zh"),
                        " ".join(deck.get("tags") or []),
                        " ".join(deck.get("tags_zh") or []),
                    )).lower()
                    for term in terms:
                        if term.lower() in haystack:
                            continue
                        if term in matches_by_deck.get(deck.get("deck_id"), set()):
                            continue
                        return False
                    return True

                decks = [deck for deck in decks if deck_matches(deck)]
        tournament = dict(tournament)
        if tournament.get("date") is not None:
            tournament["date"] = tournament["date"].isoformat()
        return {"success": True, "tournament": tournament, "decks": decks}
    finally:
        conn.close()


def list_decks(q: str = "", page: int = 1, sort: str = "date", region: str = "", fmt: str = "") -> dict:
    ensure_schema()
    page = max(1, int(page or 1))
    per_page = 20
    params = []
    where = ["1=1"]
    if region:
        where.append("d.source_region = %s")
        params.append(region)
    if fmt:
        where.append("COALESCE(t.format, '') = %s")
        params.append(fmt)
    if q:
        terms = [t for t in re.split(r"[\s\u3000]+", q) if t]
        for term in terms:
            like = f"%{term.replace('%', '').replace('_', '')}%"
            where.append(
                """
                (
                    d.player_name ILIKE %s OR d.archetype ILIKE %s OR d.title ILIKE %s
                    OR t.title ILIKE %s OR d.tags::text ILIKE %s
                    OR EXISTS (
                        SELECT 1 FROM limitless_deck_cards c
                        LEFT JOIN cards tw ON tw.card_id = c.local_tw_card_id
                        WHERE c.deck_id = d.deck_id
                          AND (
                            c.card_name ILIKE %s OR c.set_code ILIKE %s
                            OR c.set_number ILIKE %s OR tw.name ILIKE %s
                          )
                    )
                )
                """
            )
            params.extend([like] * 9)

    order = "COALESCE(t.date, DATE '1900-01-01') DESC, d.placement NULLS LAST"
    if sort == "placement":
        order = "d.placement NULLS LAST, COALESCE(t.date, DATE '1900-01-01') DESC"
    elif sort == "fetched":
        order = "d.fetched_at DESC NULLS LAST"
    elif sort == "player":
        order = "d.player_name ASC NULLS LAST"

    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "Database unavailable"}
    try:
        cursor = conn.cursor()
        where_sql = " AND ".join(where)
        cursor.execute(
            f"""
            SELECT COUNT(*) AS cnt
            FROM limitless_decks d
            LEFT JOIN limitless_tournaments t ON t.tournament_id = d.tournament_id
            WHERE {where_sql}
            """,
            params,
        )
        total = cursor.fetchone()["cnt"]
        cursor.execute(
            f"""
            SELECT d.deck_id, d.player_name, d.placement, d.archetype, d.title,
                   d.tags, d.deck_url, d.source_region, d.fetched_at,
                   t.title AS tournament_title, t.date, t.location, t.format, t.players
            FROM limitless_decks d
            LEFT JOIN limitless_tournaments t ON t.tournament_id = d.tournament_id
            WHERE {where_sql}
            ORDER BY {order}
            LIMIT %s OFFSET %s
            """,
            params + [per_page, (page - 1) * per_page],
        )
        decks = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get("date") is not None:
                item["date"] = item["date"].isoformat()
            if item.get("fetched_at") is not None:
                item["fetched_at"] = item["fetched_at"].isoformat()
            if isinstance(item.get("tags"), str):
                try:
                    item["tags"] = json.loads(item["tags"])
                except Exception:
                    item["tags"] = []
            decks.append(item)
        _apply_deck_localization(cursor, decks)
        return {
            "success": True,
            "decks": decks,
            "total": total,
            "page": page,
            "pages": max(1, (total + per_page - 1) // per_page),
        }
    finally:
        conn.close()


def _tw_card_from_limitless_card(cursor, card: dict) -> tuple[dict | None, list[dict]]:
    tw_id = card.get("local_tw_card_id")
    row = None
    if tw_id:
        cursor.execute("SELECT * FROM cards WHERE card_id = %s", (tw_id,))
        row = cursor.fetchone()
    if not row:
        row = find_local_tw_card_row(cursor, card.get("set_code"), card.get("set_number"))
    candidates = find_local_tw_candidates(cursor, card.get("set_code"), card.get("set_number"))
    return (_card_payload_from_row(row, "images") if row else None), candidates


def _find_basic_energy_tw_row(cursor, jp_name: str | None) -> dict | None:
    tw_name = ENERGY_NAME_MAP.get(str(jp_name or "").strip())
    if not tw_name:
        return None
    cursor.execute(
        """
        SELECT *
        FROM cards
        WHERE name = %s AND card_type = 'Energy'
        ORDER BY
            CASE WHEN COALESCE(image_file, '') <> '' THEN 0 ELSE 1 END,
            CASE WHEN card_id ~ '^[0-9]+$' THEN card_id::integer ELSE 0 END DESC,
            card_id DESC
        LIMIT 1
        """,
        (tw_name,),
    )
    return cursor.fetchone()


def _copy_limitless_base(card: dict) -> dict:
    base = dict(card)
    base["limitless_image_url"] = base.get("limitless_image_url") or ""
    base["limitless_card_url"] = base.get("limitless_card_url") or ""
    return base


def _tw_detail_card(cursor, jp_card: dict) -> dict:
    tw_card, candidates = _tw_card_from_limitless_card(cursor, jp_card)
    if not tw_card:
        energy_row = _find_basic_energy_tw_row(cursor, jp_card.get("card_name"))
        tw_card = _card_payload_from_row(energy_row, "images") if energy_row else None
    result = {
        "id": jp_card.get("id"),
        "deck_id": jp_card.get("deck_id"),
        "language": "tw",
        "mode": jp_card.get("mode"),
        "section": jp_card.get("section"),
        "line_order": jp_card.get("line_order"),
        "count": jp_card.get("count"),
        "jp_card_name": jp_card.get("card_name"),
        "jp_set_code": jp_card.get("set_code"),
        "jp_set_number": jp_card.get("set_number"),
        "limitless_card_url": jp_card.get("limitless_card_url") or "",
        "limitless_image_url": jp_card.get("limitless_image_url") or "",
        "tw_candidates": candidates,
        "missing": tw_card is None,
    }
    if tw_card:
        result.update({
            "card_id": tw_card.get("card_id"),
            "local_tw_card_id": tw_card.get("card_id"),
            "card_name": tw_card.get("name"),
            "name": tw_card.get("name"),
            "card_type": tw_card.get("card_type"),
            "set_code": tw_card.get("set_code"),
            "set_number": tw_card.get("set_number"),
            "image_url": tw_card.get("image_url") or jp_card.get("limitless_image_url") or "",
            "skills": tw_card.get("skills", []),
            "skills_json": tw_card.get("skills_json"),
            "element_type": tw_card.get("element_type"),
            "sub_type": tw_card.get("sub_type"),
            "hp": tw_card.get("hp"),
            "rarity": tw_card.get("rarity"),
            "logic": database.get_card_logic(tw_card.get("card_id")),
        })
    else:
        result.update({
            "card_id": None,
            "local_tw_card_id": None,
            "card_name": jp_card.get("card_name"),
            "name": jp_card.get("card_name"),
            "set_code": jp_card.get("set_code"),
            "set_number": jp_card.get("set_number"),
            "image_url": jp_card.get("limitless_image_url") or "",
            "skills": [],
        })
    return result


def _tw_card_from_joined_row(jp_card: dict, include_debug: bool = False) -> dict:
    tw_card_id = jp_card.get("tw_card_id") or jp_card.get("local_tw_card_id")
    image_url = _image_url_for({"image_file": jp_card.get("tw_image_file")}, "images") if tw_card_id else ""
    result = {
        "id": jp_card.get("id"),
        "deck_id": jp_card.get("deck_id"),
        "language": "tw",
        "mode": jp_card.get("mode"),
        "section": jp_card.get("section"),
        "line_order": jp_card.get("line_order"),
        "count": jp_card.get("count"),
        "jp_card_name": jp_card.get("card_name"),
        "jp_set_code": jp_card.get("set_code"),
        "jp_set_number": jp_card.get("set_number"),
        "limitless_card_url": jp_card.get("limitless_card_url") or "",
        "limitless_image_url": jp_card.get("limitless_image_url") or "",
        "missing": not bool(tw_card_id),
    }
    if tw_card_id:
        result.update({
            "card_id": tw_card_id,
            "local_tw_card_id": tw_card_id,
            "card_name": jp_card.get("tw_name"),
            "name": jp_card.get("tw_name"),
            "card_type": jp_card.get("tw_card_type"),
            "set_code": jp_card.get("tw_set_code"),
            "set_number": jp_card.get("tw_set_number"),
            "image_url": image_url or jp_card.get("limitless_image_url") or "",
            "element_type": jp_card.get("tw_element_type"),
            "sub_type": jp_card.get("tw_sub_type"),
            "hp": jp_card.get("tw_hp"),
            "rarity": jp_card.get("tw_rarity"),
        })
        if include_debug:
            result["skills_json"] = jp_card.get("tw_skills_json")
            result["skills"] = parse_skills(jp_card.get("tw_skills_json"))
    else:
        result.update({
            "card_id": None,
            "local_tw_card_id": None,
            "card_name": jp_card.get("card_name"),
            "name": jp_card.get("card_name"),
            "set_code": jp_card.get("set_code"),
            "set_number": jp_card.get("set_number"),
            "image_url": jp_card.get("limitless_image_url") or "",
            "skills": [] if include_debug else None,
        })
    if not include_debug:
        result.pop("skills", None)
    return result


def _same_set_number(card: dict, set_code: str | None, set_number: str | None) -> bool:
    set_info = card.get("set") or {}
    if str(set_info.get("id") or "") != str(set_code or ""):
        return False
    local_id = str(card.get("localId") or "").strip()
    return local_id in set_number_candidates(set_number)


def _find_tw_by_tcgdex(cursor, jp_card: dict) -> dict | None:
    energy_row = _find_basic_energy_tw_row(cursor, jp_card.get("jp_card_name") or jp_card.get("card_name"))
    if energy_row:
        return _card_payload_from_row(energy_row, "images")
    name = str(jp_card.get("card_name") or "").strip()
    if not name:
        return None
    client = get_tcgdex_client()
    try:
        source_cards = client.search_cards_full("ja", name)
    except Exception:
        source_cards = []
    matched_ids = []
    for source in source_cards:
        if _same_set_number(source, jp_card.get("set_code"), jp_card.get("set_number")):
            matched_ids.append(source.get("id"))
            continue
        target = client.get_card("zh-tw", source.get("id", ""))
        if not target:
            continue
        source_sig = _skill_signature_from_list(_tcgdex_skills(source))
        target_sig = _skill_signature_from_list(_tcgdex_skills(target))
        source_desc = str(source.get("description") or source.get("effect") or "").strip()
        target_desc = str(target.get("description") or target.get("effect") or "").strip()
        if source_sig and source_sig == target_sig:
            matched_ids.append(source.get("id"))
        elif source_desc and source_desc == target_desc:
            matched_ids.append(source.get("id"))
    for card_id in matched_ids:
        cursor.execute("SELECT * FROM cards WHERE card_id = %s", (card_id,))
        row = cursor.fetchone()
        if row:
            return _card_payload_from_row(row, "images")
        target = client.get_card("zh-tw", card_id)
        if target:
            payload = {
                "card_id": target.get("id", ""),
                "id": target.get("id", ""),
                "name": target.get("name", ""),
                "card_name": target.get("name", ""),
                "card_type": target.get("category", ""),
                "sub_type": target.get("stage") or target.get("trainerType") or target.get("energyType") or "",
                "hp": int(target.get("hp") or 0),
                "element_type": (target.get("types") or [""])[0],
                "set_code": (target.get("set") or {}).get("id", ""),
                "set_number": str(target.get("localId") or ""),
                "image_url": _tcgdex_image_url(target.get("image", "")),
                "image_file": _tcgdex_image_url(target.get("image", "")),
                "skills": _tcgdex_skills(target),
                "source": "tcgdex",
            }
            return payload
    return None


def import_deck(deck_id: str, language: str = "tw", mode: str = "normal") -> dict:
    if language != "tw":
        return {"success": False, "error": "Only Traditional Chinese import is supported"}
    detail = get_deck_detail(deck_id)
    if not detail.get("success"):
        return detail
    deck = detail["deck"]
    tw_cards = detail["cards"]["tw"].get(mode, [])
    imported = []
    missing = []
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "Database unavailable"}
    try:
        cursor = conn.cursor()
        for card in tw_cards:
            resolved = None
            if not card.get("missing") and card.get("card_id"):
                resolved = card
            else:
                resolved = _find_tw_by_tcgdex(cursor, card)
            if resolved and resolved.get("card_id"):
                count = int(card.get("count") or 0)
                for _ in range(count):
                    item = dict(resolved)
                    item["name"] = item.get("name") or item.get("card_name")
                    item["card_name"] = item.get("card_name") or item.get("name")
                    item["logic"] = database.get_card_logic(item.get("card_id"))
                    imported.append(item)
            else:
                missing.append({
                    "count": card.get("count"),
                    "jp_name": card.get("jp_card_name") or card.get("card_name"),
                    "jp_code": f"{card.get('jp_set_code') or card.get('set_code')} {card.get('jp_set_number') or card.get('set_number')}",
                    "section": card.get("section"),
                    "limitless_image_url": card.get("limitless_image_url") or card.get("image_url") or "",
                })
        return {
            "success": True,
            "name": deck.get("archetype_zh") or deck.get("title_zh") or deck.get("archetype") or deck.get("title") or deck_id,
            "deck": imported,
            "missing": missing,
            "imported_count": len(imported),
        }
    finally:
        conn.close()


def _serialize_deck_row(deck: dict) -> dict:
    result = dict(deck)
    for key in ("date", "fetched_at"):
        if result.get(key) is not None:
            result[key] = result[key].isoformat()
    if isinstance(result.get("tags"), str):
        try:
            result["tags"] = json.loads(result["tags"])
        except Exception:
            result["tags"] = []
    return result


def _fetch_deck_row(cursor, deck_id: str) -> dict | None:
    cursor.execute(
        """
        SELECT d.*, t.title AS tournament_title, t.date, t.location, t.format, t.players
        FROM limitless_decks d
        LEFT JOIN limitless_tournaments t ON t.tournament_id = d.tournament_id
        WHERE d.deck_id = %s
        """,
        (deck_id,),
    )
    return cursor.fetchone()


def _localized_deck(cursor, deck: dict) -> dict:
    item = _serialize_deck_row(deck)
    _apply_deck_localization(cursor, [item])
    return item


def get_deck_metadata(deck_id: str) -> dict:
    ensure_schema()
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "Database unavailable"}
    try:
        cursor = conn.cursor()
        deck = _fetch_deck_row(cursor, deck_id)
        if not deck:
            return {"success": False, "error": "Deck not found"}

        cursor.execute(
            """
            SELECT language, mode, COUNT(*) AS card_kinds, COALESCE(SUM(count), 0) AS card_count
            FROM limitless_deck_cards
            WHERE deck_id = %s
            GROUP BY language, mode
            """,
            (deck_id,),
        )
        counts = {"jp": {}, "en": {}, "tw": {}}
        available = {"jp": {}, "en": {}, "tw": {}}
        for row in cursor.fetchall():
            language = row["language"]
            mode = row["mode"]
            info = {"card_kinds": row["card_kinds"], "card_count": int(row["card_count"] or 0)}
            counts[language][mode] = info
            available[language][mode] = row["card_kinds"] > 0
            if language == "jp":
                counts["tw"][mode] = info
                available["tw"][mode] = row["card_kinds"] > 0

        return {
            "success": True,
            "deck": _localized_deck(cursor, deck),
            "available": available,
            "counts": counts,
            "cards": {"jp": {}, "en": {}, "tw": {}},
        }
    finally:
        conn.close()


def _slim_limitless_card(card: dict, include_debug: bool = False) -> dict:
    keep = {
        "id", "deck_id", "language", "mode", "section", "line_order", "count",
        "card_name", "name", "set_code", "set_number", "image_url",
        "limitless_card_url", "limitless_image_url", "card_id", "local_tw_card_id",
        "jp_card_name", "jp_set_code", "jp_set_number", "missing",
    }
    result = {key: card.get(key) for key in keep if key in card}
    if include_debug:
        for key in ("local_jp_card_id", "tw_candidates", "skills", "skills_json", "logic"):
            if key in card:
                result[key] = card.get(key)
    return result


def get_deck_cards(deck_id: str, language: str = "tw", mode: str = "normal", include_debug: bool = False) -> dict:
    ensure_schema()
    language = language if language in ("tw", "jp", "en") else "tw"
    mode = mode if mode in ("normal", "bling") else "normal"
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "Database unavailable"}
    try:
        cursor = conn.cursor()
        deck = _fetch_deck_row(cursor, deck_id)
        if not deck:
            return {"success": False, "error": "Deck not found"}

        source_language = "jp" if language == "tw" else language
        cursor.execute(
            """
            SELECT c.*,
                   jp.image_file AS jp_image_file,
                   tw.card_id AS tw_card_id,
                   tw.name AS tw_name,
                   tw.card_type AS tw_card_type,
                   tw.sub_type AS tw_sub_type,
                   tw.hp AS tw_hp,
                   tw.element_type AS tw_element_type,
                   tw.image_file AS tw_image_file,
                   tw.rarity AS tw_rarity,
                   tw.set_code AS tw_set_code,
                   tw.set_number AS tw_set_number,
                   tw.skills_json AS tw_skills_json
            FROM limitless_deck_cards c
            LEFT JOIN jp_cards jp ON jp.card_id = c.local_jp_card_id
            LEFT JOIN cards tw ON tw.card_id = c.local_tw_card_id
            WHERE c.deck_id = %s AND c.language = %s AND c.mode = %s
            ORDER BY c.line_order
            """,
            (deck_id, source_language, mode),
        )
        cards = []
        for row in cursor.fetchall():
            card = dict(row)
            jp_row = {"image_file": card.pop("jp_image_file", None)}
            if language == "tw":
                if include_debug:
                    for key in (
                        "tw_card_id", "tw_name", "tw_card_type", "tw_sub_type", "tw_hp",
                        "tw_element_type", "tw_image_file", "tw_rarity", "tw_set_code",
                        "tw_set_number", "tw_skills_json",
                    ):
                        card.pop(key, None)
                    tw_card = _tw_detail_card(cursor, card)
                else:
                    tw_card = _tw_card_from_joined_row(card, include_debug=False)
                cards.append(_slim_limitless_card(tw_card, include_debug=include_debug))
            else:
                tw_row = {"image_file": card.pop("tw_image_file", None)}
                for key in (
                    "tw_card_id", "tw_name", "tw_card_type", "tw_sub_type", "tw_hp",
                    "tw_element_type", "tw_rarity", "tw_set_code", "tw_set_number",
                    "tw_skills_json",
                ):
                    card.pop(key, None)
                if language == "jp":
                    card["image_url"] = _image_url_for(jp_row, "images_jp") or card.get("limitless_image_url") or ""
                else:
                    card["image_url"] = card.get("limitless_image_url") or _image_url_for(tw_row, "images")
                cards.append(_slim_limitless_card(card, include_debug=include_debug))

        return {
            "success": True,
            "deck": _localized_deck(cursor, deck),
            "language": language,
            "mode": mode,
            "cards": cards,
        }
    finally:
        conn.close()


def get_deck_detail(deck_id: str) -> dict:
    ensure_schema()
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "Database unavailable"}
    try:
        cursor = conn.cursor()
        deck = _fetch_deck_row(cursor, deck_id)
        if not deck:
            return {"success": False, "error": "Deck not found"}

        cursor.execute(
            """
            SELECT c.*,
                   jp.image_file AS jp_image_file,
                   tw.image_file AS tw_image_file
            FROM limitless_deck_cards c
            LEFT JOIN jp_cards jp ON jp.card_id = c.local_jp_card_id
            LEFT JOIN cards tw ON tw.card_id = c.local_tw_card_id
            WHERE c.deck_id = %s
            ORDER BY c.language, c.mode, c.line_order
            """,
            (deck_id,),
        )
        cards = {"jp": {"normal": [], "bling": []}, "en": {"normal": [], "bling": []}, "tw": {"normal": [], "bling": []}}
        jp_rows = []
        for row in cursor.fetchall():
            card = dict(row)
            jp_row = {"image_file": card.pop("jp_image_file", None)}
            tw_row = {"image_file": card.pop("tw_image_file", None)}
            if card["language"] == "jp":
                card["image_url"] = _image_url_for(jp_row, "images_jp") or card.get("limitless_image_url") or ""
            elif card["language"] == "en":
                card["image_url"] = card.get("limitless_image_url") or _image_url_for(tw_row, "images")
            else:
                card["image_url"] = _image_url_for(tw_row, "images") or card.get("limitless_image_url") or ""
            cards[card["language"]][card["mode"]].append(card)
            if card["language"] == "jp":
                jp_rows.append(card)

        for jp_card in jp_rows:
            cards["tw"][jp_card["mode"]].append(_tw_detail_card(cursor, jp_card))

        cursor.execute(
            """
            SELECT * FROM limitless_card_mapping
            WHERE source_deck_id = %s
            ORDER BY mode, id
            """,
            (deck_id,),
        )
        mappings = [dict(row) for row in cursor.fetchall()]

        return {"success": True, "deck": _localized_deck(cursor, deck), "cards": cards, "mappings": mappings}
    finally:
        conn.close()


def recent_logs(limit: int = 20) -> list[dict]:
    ensure_schema()
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT created_at, level, context, message, detail
            FROM limitless_update_logs
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        logs = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get("created_at"):
                item["created_at"] = item["created_at"].isoformat()
            logs.append(item)
        return logs
    finally:
        conn.close()
