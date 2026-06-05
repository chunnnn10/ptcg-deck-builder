import json
import re
import unicodedata
from difflib import SequenceMatcher

import requests

try:
    from .card_mapping import ensure_id_mapping_columns
except Exception:
    ensure_id_mapping_columns = None


PTCGTW_CARD_API = "https://ptcgtw.shop/index_function/api/mysqli_api_2.php"
PTCGTW_API_PARAMS = "?type=%E5%96%AE%E5%8D%A1%E8%B3%87%E6%96%99&lan=0&format=json&variant_id="
DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}
SIMILARITY_THRESHOLD = 0.85

_MAPPING_COLUMNS_READY = False


def _ensure_mapping_columns_once(cursor=None):
    global _MAPPING_COLUMNS_READY
    if _MAPPING_COLUMNS_READY:
        return
    if cursor is not None:
        new_cols = {
            "confidence": "VARCHAR DEFAULT 'MEDIUM'",
            "score": "INTEGER DEFAULT 0",
            "match_detail": "TEXT",
            "matched_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "source": "VARCHAR DEFAULT 'ptcgtw'",
        }
        for name, definition in new_cols.items():
            cursor.execute(f"ALTER TABLE id_mapping ADD COLUMN IF NOT EXISTS {name} {definition}")
        _MAPPING_COLUMNS_READY = True
        return
    if ensure_id_mapping_columns:
        try:
            ensure_id_mapping_columns()
        except Exception:
            pass
    _MAPPING_COLUMNS_READY = True


def normalize_text(value):
    value = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s\W_]+", "", value, flags=re.UNICODE)


def normalize_name(value):
    name = normalize_text(value)
    return re.sub(r"(vmax|vstar|ex|gx|v)$", "", name)


def _ratio(left, right):
    left = normalize_text(left)
    right = normalize_text(right)
    if not left or not right:
        return 0.0
    return SequenceMatcher(None, left, right).ratio()


def _safe_json(value):
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def _ptcgtw_skills(card):
    skills = []
    ability_name = card.get("ability_name")
    ability_text = card.get("ability_text")
    if ability_name or ability_text:
        skills.append({
            "type": "ability",
            "name": ability_name or "",
            "cost": "",
            "damage": "",
            "effect": ability_text or "",
        })
    attacks = card.get("attacks") or []
    if isinstance(attacks, str):
        attacks = _safe_json(attacks) or []
    for attack in attacks:
        if not isinstance(attack, dict):
            continue
        name = attack.get("name") or attack.get("n") or ""
        effect = attack.get("effect") or attack.get("t") or ""
        damage = attack.get("damage") or attack.get("d") or ""
        cost = attack.get("cost") or attack.get("c") or ""
        if name or effect or damage or cost:
            skills.append({
                "type": "attack",
                "name": name,
                "cost": cost,
                "damage": str(damage or ""),
                "effect": effect,
            })
    return skills


def _local_skills(row):
    skills = _safe_json(row.get("skills_json")) or []
    if isinstance(skills, dict):
        skills = skills.get("skills") or skills.get("attacks") or []
    normalized = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        normalized.append({
            "type": skill.get("type") or skill.get("category") or "",
            "name": skill.get("name") or skill.get("ability_name") or "",
            "cost": skill.get("cost") or "",
            "damage": str(skill.get("damage") or ""),
            "effect": skill.get("effect") or skill.get("text") or skill.get("description") or "",
        })
    return normalized


def _skills_signature(skills):
    parts = []
    for skill in skills or []:
        cost = skill.get("cost", "")
        if isinstance(cost, list):
            cost = "".join(str(c) for c in cost)
        parts.append("|".join([
            str(skill.get("type", "")),
            str(skill.get("name", "")),
            str(cost or ""),
            str(skill.get("damage", "")),
            str(skill.get("effect", "")),
        ]))
    return "||".join(parts)


def _set_number_prefix(value):
    return str(value or "").strip().split("/")[0]


def _set_number_candidates(value):
    raw = str(value or "").strip()
    if not raw:
        return []
    prefix = _set_number_prefix(raw)
    values = {raw, prefix}
    if prefix.isdigit():
        values.add(str(int(prefix)))
        values.add(prefix.zfill(3))
    return [v for v in values if v]


def fetch_ptcgtw_card(variant_id, session=None):
    close_session = False
    if session is None:
        session = requests.Session()
        session.headers.update(DEFAULT_HEADERS)
        close_session = True
    try:
        url = f"{PTCGTW_CARD_API}{PTCGTW_API_PARAMS}{variant_id}"
        resp = session.get(url, timeout=10, headers=DEFAULT_HEADERS)
        if resp.status_code != 200:
            return None
        data = resp.json()
        if not data.get("success") or not data.get("data"):
            return None
        card = data["data"]
        card["variant_id"] = card.get("variant_id") or variant_id
        return card
    except Exception:
        return None
    finally:
        if close_session:
            session.close()


def _fetch_mapping(cursor, variant_id):
    cursor.execute("""
        SELECT m.local_card_id, c.*
        FROM id_mapping m
        LEFT JOIN cards c ON c.card_id = m.local_card_id
        WHERE m.external_variant_id = %s
    """, (variant_id,))
    row = cursor.fetchone()
    if row and row.get("card_id"):
        return row
    return None


def _find_by_set_number(cursor, set_code, set_no):
    if not set_code or not set_no:
        return None
    candidates = _set_number_candidates(set_no)
    if not candidates:
        return None
    cursor.execute("""
        SELECT *
        FROM cards
        WHERE set_code = %s
          AND (
              set_number = ANY(%s)
              OR split_part(set_number, '/', 1) = ANY(%s)
          )
        ORDER BY
          CASE WHEN image_file IS NULL OR image_file = '' THEN 1 ELSE 0 END,
          CASE WHEN skills_json IS NULL THEN 1 ELSE 0 END,
          card_id DESC
        LIMIT 1
    """, (set_code, candidates, candidates))
    return cursor.fetchone()


def _find_by_name_skill(cursor, ptcgtw_card):
    name_tw = (ptcgtw_card.get("name_tw") or "").strip()
    if not name_tw:
        return None, 0.0, "missing_name"
    cursor.execute("""
        SELECT *
        FROM cards
        WHERE name = %s
           OR replace(replace(replace(replace(replace(name, 'ex', ''), 'GX', ''), 'VSTAR', ''), 'VMAX', ''), 'V', '')
              = replace(replace(replace(replace(replace(%s, 'ex', ''), 'GX', ''), 'VSTAR', ''), 'VMAX', ''), 'V', '')
        LIMIT 80
    """, (name_tw, name_tw))
    rows = cursor.fetchall()
    if not rows:
        return None, 0.0, "name_not_found"

    source_name = normalize_name(name_tw)
    source_skills = _skills_signature(_ptcgtw_skills(ptcgtw_card))
    source_desc = ptcgtw_card.get("description") or ptcgtw_card.get("card_text") or ""
    best = None
    best_score = 0.0
    best_reason = "low_similarity"

    for row in rows:
        local_name = normalize_name(row.get("name"))
        if local_name != source_name and normalize_text(row.get("name")) != normalize_text(name_tw):
            continue
        local_skills = _skills_signature(_local_skills(row))
        if source_skills and local_skills:
            score = _ratio(source_skills, local_skills)
            reason = "skill_similarity"
        else:
            local_desc = row.get("description") or row.get("flavor_text") or ""
            score = _ratio(source_desc, local_desc)
            reason = "description_similarity"
        if score > best_score:
            best = row
            best_score = score
            best_reason = reason

    if best and best_score >= SIMILARITY_THRESHOLD:
        return best, best_score, best_reason
    return None, best_score, best_reason


def upsert_mapping(cursor, variant_id, local_card_id, confidence="FALLBACK", score=100, source="name_skill_fallback", detail=None):
    _ensure_mapping_columns_once(cursor)
    detail_json = json.dumps(detail or {}, ensure_ascii=False)
    try:
        cursor.execute("""
            INSERT INTO id_mapping (external_variant_id, local_card_id, confidence, score, match_detail, matched_at, source)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, %s)
            ON CONFLICT (external_variant_id) DO UPDATE SET
                local_card_id = EXCLUDED.local_card_id,
                confidence = EXCLUDED.confidence,
                score = EXCLUDED.score,
                match_detail = EXCLUDED.match_detail,
                matched_at = CURRENT_TIMESTAMP,
                source = EXCLUDED.source
        """, (variant_id, local_card_id, confidence, int(score), detail_json, source))
    except Exception:
        cursor.execute("""
            INSERT INTO id_mapping (external_variant_id, local_card_id)
            VALUES (%s, %s)
            ON CONFLICT (external_variant_id) DO UPDATE SET local_card_id = EXCLUDED.local_card_id
        """, (variant_id, local_card_id))


def _missing_info(variant_id, ptcgtw_card=None, reason="not_found"):
    ptcgtw_card = ptcgtw_card or {}
    return {
        "variant_id": variant_id,
        "name_tw": ptcgtw_card.get("name_tw") or "",
        "name_jp": ptcgtw_card.get("name_jp") or "",
        "set_name": ptcgtw_card.get("set_name") or "",
        "set_no": ptcgtw_card.get("set_no") or "",
        "reason": reason,
        "image_url": ptcgtw_card.get("image_url") or ptcgtw_card.get("image_normal") or "",
    }


def resolve_variant(cursor, variant_id, session=None, write_mapping=True):
    mapped = _fetch_mapping(cursor, variant_id)
    if mapped:
        return {
            "local_card_id": mapped["card_id"],
            "card_row": mapped,
            "source": "id_mapping",
            "ptcgtw_card": None,
            "missing": None,
        }

    ptcgtw_card = fetch_ptcgtw_card(variant_id, session=session)
    if not ptcgtw_card:
        return {
            "local_card_id": None,
            "card_row": None,
            "source": "missing",
            "ptcgtw_card": None,
            "missing": _missing_info(variant_id, None, "ptcgtw_card_not_found"),
        }

    set_match = _find_by_set_number(
        cursor,
        (ptcgtw_card.get("set_name") or "").strip(),
        (ptcgtw_card.get("set_no") or "").strip(),
    )
    if set_match:
        if write_mapping:
            upsert_mapping(
                cursor,
                variant_id,
                set_match["card_id"],
                confidence="HIGH",
                score=100,
                source="set_number_fallback",
                detail={
                    "set_name": ptcgtw_card.get("set_name"),
                    "set_no": ptcgtw_card.get("set_no"),
                    "name_tw": ptcgtw_card.get("name_tw"),
                },
            )
        return {
            "local_card_id": set_match["card_id"],
            "card_row": set_match,
            "source": "set_number_fallback",
            "ptcgtw_card": ptcgtw_card,
            "missing": None,
        }

    name_match, score, reason = _find_by_name_skill(cursor, ptcgtw_card)
    if name_match:
        if write_mapping:
            upsert_mapping(
                cursor,
                variant_id,
                name_match["card_id"],
                confidence="FALLBACK",
                score=round(score * 100),
                source="name_skill_fallback",
                detail={
                    "reason": reason,
                    "similarity": score,
                    "set_name": ptcgtw_card.get("set_name"),
                    "set_no": ptcgtw_card.get("set_no"),
                    "name_tw": ptcgtw_card.get("name_tw"),
                },
            )
        return {
            "local_card_id": name_match["card_id"],
            "card_row": name_match,
            "source": "name_skill_fallback",
            "ptcgtw_card": ptcgtw_card,
            "missing": None,
        }

    return {
        "local_card_id": None,
        "card_row": None,
        "source": "missing",
        "ptcgtw_card": ptcgtw_card,
        "missing": _missing_info(variant_id, ptcgtw_card, reason),
    }


def card_row_to_payload(row, count=1, include_logic=False, logic_loader=None):
    import random
    import time

    cards = []
    base = dict(row)
    try:
        base["skills"] = json.loads(base["skills_json"]) if base.get("skills_json") else []
    except Exception:
        base["skills"] = []
    if base.get("image_file"):
        base["image_url"] = f"/images/{base['image_file']}"
    if include_logic and logic_loader:
        base["logic"] = logic_loader(base.get("card_id"))
    for _ in range(int(count or 1)):
        card = dict(base)
        card["uniqueId"] = f"{int(time.time() * 1000)}{random.randint(100000, 999999)}"
        cards.append(card)
    return cards
