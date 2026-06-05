import json
import os
import re
from typing import Any

import config
import database


CARD_LIMIT = 20
FULL_SKILL_LIMIT = 10


def parse_skills(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    if isinstance(value, dict):
        for key in ("skills", "attacks"):
            if isinstance(value.get(key), list):
                return [v for v in value[key] if isinstance(v, dict)]
        return []
    try:
        parsed = json.loads(value)
        return parse_skills(parsed)
    except Exception:
        return []


def _official_tw_image_filename(card_id: Any) -> str:
    raw = str(card_id or "").strip()
    if raw.isdigit():
        return f"tw{int(raw):08d}.png"
    return ""


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
    fallback = _official_tw_image_filename(row.get("card_id"))
    return f"https://asia.pokemon-card.com/tw/card-img/{fallback}" if fallback else ""


def _skill_text(skills: list[dict[str, Any]], full: bool = True) -> list[dict[str, Any]]:
    result = []
    for skill in skills:
        result.append({
            "type": skill.get("type") or skill.get("category") or ("ability" if skill.get("isAbility") else "attack"),
            "name": skill.get("name") or skill.get("ability_name") or "",
            "cost": skill.get("cost") or [],
            "damage": str(skill.get("damage") or ""),
            "effect": skill.get("effect") or skill.get("text") or skill.get("description") or "",
        })
    if full:
        return result
    return [
        {k: v for k, v in item.items() if k in ("type", "name", "effect") and v}
        for item in result
    ]


def _card_payload(row: dict[str, Any], language: str, include_full_skills: bool = True) -> dict[str, Any]:
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
        "skills": _skill_text(skills, include_full_skills),
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


def _looks_like_card_id(query: str) -> bool:
    return bool(re.match(r"^(jp)?[A-Za-z0-9_.-]{3,}$", query or ""))


def _is_pokemon(card: dict[str, Any]) -> bool:
    return "pok" in str(card.get("card_type") or "").lower()


def _matches_skill_type(skills: list[dict[str, Any]], skill_type: str) -> bool:
    if not skill_type:
        return True
    aliases = {
        "ability": {"ability", "特性"},
        "attack": {"attack", "攻擊", "攻击", "招式"},
    }.get(skill_type, {skill_type})
    for skill in _skill_text(skills, True):
        current = str(skill.get("type") or "").strip()
        if current.lower() in aliases or current in aliases:
            return True
    return False


def _trim_skills(cards: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for idx, card in enumerate(cards):
        if idx >= FULL_SKILL_LIMIT:
            card["skills"] = _skill_text(card.get("skills") or [], False)
    return cards


def _effect_text(card: dict[str, Any]) -> str:
    skill_text = " ".join(
        f"{skill.get('name') or ''} {skill.get('damage') or ''} {skill.get('effect') or ''}"
        for skill in card.get("skills") or []
    )
    return f"{skill_text} {card.get('description') or ''}"


def _attack_texts(card: dict[str, Any]) -> list[str]:
    texts = []
    for skill in card.get("skills") or []:
        if str(skill.get("type") or "").strip().lower() != "attack":
            continue
        texts.append(f"{skill.get('name') or ''} {skill.get('damage') or ''} {skill.get('effect') or ''}")
    return texts


def _is_hand_size_damage_card(card: dict[str, Any]) -> bool:
    if not _is_pokemon(card):
        return False
    patterns = (
        "手牌的張數",
        "手牌張數",
        "手牌的数量",
        "手牌数量",
        "自己手牌的張數",
        "對手手牌的張數",
        "自己的手牌",
        "對手的手牌",
        "比對手手牌",
        "比自己手牌",
        "手牌都沒有",
        "手牌都没有",
        "1張手牌都沒有",
        "1张手牌都没有",
        "手牌的數量",
        "手牌的数量",
    )
    multiplier_markers = ("×", "增加", "+", "多", "相同", "沒有", "没有", "滿足", "满足", "傷害指示物")
    for text in _attack_texts(card):
        if "手牌" not in text:
            continue
        if not any(pattern in text for pattern in patterns):
            continue
        if any(marker in text for marker in multiplier_markers):
            return True
    return False


def _hand_size_damage_rank(card: dict[str, Any]) -> tuple[int, int]:
    best = 100
    for text in _attack_texts(card):
        if "手牌" not in text:
            continue
        if "造成對手的手牌的張數" in text or "造成對手手牌的張數" in text:
            best = min(best, 0)
        elif "將與自己的手牌的張數" in text or "造成自己的手牌的張數" in text or "造成自己手牌的張數" in text:
            best = min(best, 1)
        elif "自己的手牌與對手的手牌張數" in text or "自己的手牌的張數與對手的手牌的張數" in text or "手牌張數相同" in text:
            best = min(best, 2)
        elif "查看對手的手牌" in text and "張數" in text:
            best = min(best, 4)
        elif "從自己的手牌" in text and "其張數" in text:
            best = min(best, 6)
        elif "手牌的張數" in text or "手牌張數" in text:
            best = min(best, 3)
        else:
            best = min(best, 8)
    raw_id = str(card.get("card_id") or "0")
    numeric_id = int(raw_id) if raw_id.isdigit() else 0
    return (best, -numeric_id)


def _trainer_energy_attach_rank(card: dict[str, Any]) -> tuple[int, int]:
    text = _effect_text(card)
    subtype = str(card.get("sub_type") or "")
    if "附於自己的" in text or "附給自己的" in text:
        best = 0
    elif "改附於" in text:
        best = 2
    elif "附於" in text:
        best = 3
    else:
        best = 8
    if subtype == "Supporter":
        best += 0
    elif subtype == "Item":
        best += 1
    elif subtype == "Pokémon Tool":
        best += 2
    else:
        best += 4
    raw_id = str(card.get("card_id") or "0")
    numeric_id = int(raw_id) if raw_id.isdigit() else 0
    return (best, -numeric_id)


def search_cards(query: str, language: str = "tw", limit: int = CARD_LIMIT) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        return []
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))

    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        search = f"%{query}%"
        params: list[Any] = []
        if _looks_like_card_id(query):
            where = """
                WHERE card_id = %s OR card_id ILIKE %s OR name ILIKE %s
                   OR set_code ILIKE %s OR set_number ILIKE %s
            """
            params.extend([query, search, search, search, search])
        else:
            where = """
                WHERE name ILIKE %s
                   OR card_id ILIKE %s
                   OR set_code ILIKE %s
                   OR set_number ILIKE %s
            """
            params.extend([search, search, search, search])
            if language == "tw":
                where += " OR japanese_name ILIKE %s"
                params.append(search)
            else:
                where += " OR chinese_name ILIKE %s"
                params.append(search)
        cursor.execute(
            f"""
            SELECT {_select_columns(table)}
            FROM {table}
            {where}
            ORDER BY
                CASE WHEN name = %s THEN 0 ELSE 1 END,
                CASE WHEN card_id = %s THEN 0 ELSE 1 END,
                card_id DESC
            LIMIT %s
            """,
            [*params, query, query, limit],
        )
        rows = cursor.fetchall()
        return [_card_payload(row, folder_lang, idx < FULL_SKILL_LIMIT) for idx, row in enumerate(rows)]
    finally:
        conn.close()


def get_card(card_id: str, language: str = "tw") -> dict[str, Any] | None:
    card_id = str(card_id or "").strip()
    if not card_id:
        return None
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    conn = database.get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_select_columns(table)} FROM {table} WHERE card_id = %s LIMIT 1",
            (card_id,),
        )
        row = cursor.fetchone()
        return _card_payload(row, folder_lang, True) if row else None
    finally:
        conn.close()


def _skill_keyword_rank(card: dict[str, Any], keyword: str, skill_type: str = "") -> int:
    rank = 100
    for skill in card.get("skills") or []:
        text = f"{skill.get('name') or ''} {skill.get('effect') or ''}"
        current_type = str(skill.get("type") or "").strip().lower()
        has_keyword = keyword in text
        is_requested_type = not skill_type or current_type == skill_type
        if has_keyword and is_requested_type:
            rank = min(rank, 0)
        elif is_requested_type:
            rank = min(rank, 10)
        elif has_keyword:
            rank = min(rank, 20)
    if not _is_pokemon(card):
        rank += 30
    return rank


def _skill_terms_rank(card: dict[str, Any], terms: list[str], skill_type: str = "") -> int:
    rank = 100
    for skill in card.get("skills") or []:
        text = f"{skill.get('name') or ''} {skill.get('effect') or ''}"
        current_type = str(skill.get("type") or "").strip().lower()
        matched = sum(1 for term in terms if term and term in text)
        is_requested_type = not skill_type or current_type == skill_type
        if matched == len(terms) and is_requested_type:
            rank = min(rank, 0)
        elif matched >= max(1, len(terms) - 1) and is_requested_type:
            rank = min(rank, 5)
        elif is_requested_type:
            rank = min(rank, 20)
        elif matched:
            rank = min(rank, 30)
    if not _is_pokemon(card):
        rank += 30
    return rank


def search_skill_keyword(
    keyword: str,
    language: str = "tw",
    limit: int = CARD_LIMIT,
    skill_type: str = "",
) -> list[dict[str, Any]]:
    keyword = str(keyword or "").strip()
    if not keyword:
        return []
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    fetch_limit = max(limit, 80 if skill_type else limit)
    search = f"%{keyword}%"

    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_select_columns(table)}
            FROM {table}
            WHERE COALESCE(skills_json::text, '') ILIKE %s
               OR COALESCE(description, '') ILIKE %s
               OR name ILIKE %s
            ORDER BY
                CASE WHEN COALESCE(skills_json::text, '') ILIKE %s THEN 0 ELSE 1 END,
                card_id DESC
            LIMIT %s
            """,
            (search, search, search, search, fetch_limit),
        )
        rows = cursor.fetchall()
        cards = [_card_payload(row, folder_lang, True) for row in rows]
        if skill_type:
            cards = [card for card in cards if _matches_skill_type(card.get("skills") or [], skill_type)]
        cards.sort(key=lambda card: _skill_keyword_rank(card, keyword, skill_type))
        return _trim_skills(cards[:limit])
    finally:
        conn.close()


def search_skill_terms(
    terms: list[str],
    language: str = "tw",
    limit: int = CARD_LIMIT,
    skill_type: str = "",
) -> list[dict[str, Any]]:
    terms = [str(term or "").strip() for term in terms if str(term or "").strip()]
    if not terms:
        return []
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    fetch_limit = max(limit, 120)

    text_expr = "COALESCE(skills_json::text, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(name, '')"
    where_parts = [f"({text_expr}) ILIKE %s" for _ in terms]
    params = [f"%{term}%" for term in terms]

    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_select_columns(table)}
            FROM {table}
            WHERE {' AND '.join(where_parts)}
            ORDER BY card_id DESC
            LIMIT %s
            """,
            [*params, fetch_limit],
        )
        rows = cursor.fetchall()
        cards = [_card_payload(row, folder_lang, True) for row in rows]
        if skill_type:
            cards = [card for card in cards if _matches_skill_type(card.get("skills") or [], skill_type)]
        cards.sort(key=lambda card: _skill_terms_rank(card, terms, skill_type))
        return _trim_skills(cards[:limit])
    finally:
        conn.close()


def search_hand_size_damage(language: str = "tw", limit: int = CARD_LIMIT) -> list[dict[str, Any]]:
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    # Modern card pools contain many incidental "hand" + "damage" matches.
    # Keep a larger candidate window, then do semantic filtering/ranking in Python.
    fetch_limit = max(limit * 50, 1000)

    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_select_columns(table)}
            FROM {table}
            WHERE COALESCE(skills_json::text, '') ILIKE %s
              AND (
                COALESCE(skills_json::text, '') ILIKE %s
                OR COALESCE(skills_json::text, '') ILIKE %s
                OR COALESCE(skills_json::text, '') ILIKE %s
                OR COALESCE(skills_json::text, '') ILIKE %s
              )
            ORDER BY card_id DESC
            LIMIT %s
            """,
            ("%手牌%", "%傷害%", "%增加%", "%×%", "%傷害指示物%", fetch_limit),
        )
        rows = cursor.fetchall()
        cards = [_card_payload(row, folder_lang, True) for row in rows]
        cards = [card for card in cards if _is_hand_size_damage_card(card)]
        cards.sort(key=_hand_size_damage_rank)
        return _trim_skills(cards[:limit])
    finally:
        conn.close()


def search_trainer_energy_attach(
    language: str = "tw",
    limit: int = CARD_LIMIT,
    subtypes: list[str] | None = None,
) -> list[dict[str, Any]]:
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    subtypes = [str(item).strip() for item in (subtypes or []) if str(item).strip()]

    text_expr = "COALESCE(skills_json::text, '') || ' ' || COALESCE(description, '') || ' ' || COALESCE(name, '')"
    params: list[Any] = ["%能量%", "%附%", "%自己%"]
    subtype_sql = ""
    if subtypes:
        subtype_sql = "AND sub_type = ANY(%s)"
        params.append(subtypes)

    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT {_select_columns(table)}
            FROM {table}
            WHERE card_type = 'Trainer'
              AND ({text_expr}) ILIKE %s
              AND ({text_expr}) ILIKE %s
              AND ({text_expr}) ILIKE %s
              {subtype_sql}
            ORDER BY card_id DESC
            LIMIT %s
            """,
            [*params, max(limit * 10, 120)],
        )
        rows = cursor.fetchall()
        cards = [_card_payload(row, folder_lang, True) for row in rows]
        cards = [
            card for card in cards
            if "能量" in _effect_text(card)
            and "附" in _effect_text(card)
            and ("自己的" in _effect_text(card) or "自己的" in str(card.get("description") or ""))
            and not any(term in _effect_text(card) for term in ("將其丟棄", "放回對手", "減少", "所需的能量"))
        ]
        cards.sort(key=_trainer_energy_attach_rank)
        if subtypes and len(subtypes) > 1:
            mixed: list[dict[str, Any]] = []
            seen_ids = set()
            per_type_limit = max(3, limit // len(subtypes))
            for subtype in subtypes:
                bucket = [card for card in cards if str(card.get("sub_type") or "") == subtype][:per_type_limit]
                for card in bucket:
                    cid = card.get("card_id") or card.get("id")
                    if cid not in seen_ids:
                        seen_ids.add(cid)
                        mixed.append(card)
            for card in cards:
                if len(mixed) >= limit:
                    break
                cid = card.get("card_id") or card.get("id")
                if cid not in seen_ids:
                    seen_ids.add(cid)
                    mixed.append(card)
            cards = mixed
        return _trim_skills(cards[:limit])
    finally:
        conn.close()
