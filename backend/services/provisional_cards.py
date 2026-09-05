"""
未發售卡：admin 上傳圖片 → AI 結構化 → 臨時入 cards。
正式版爬蟲寫入同名卡時，取代臨時卡並 remap 牌組。
"""
from __future__ import annotations

import json
import os
import re
import uuid
from typing import Any

import config
import database


def normalize_card_name(value: str | None) -> str:
    text = re.sub(r"\s+", "", str(value or ""))
    text = text.replace("（", "(").replace("）", ")")
    return text.lower()


def _parse_json_object(text: str) -> dict:
    raw = str(text or "").strip()
    if not raw:
        return {}
    raw = raw.replace("```json", "```").strip()
    if "```" in raw:
        parts = raw.split("```")
        raw = parts[1] if len(parts) > 1 else raw
    match = re.search(r"\{.*\}", raw, re.S)
    if match:
        raw = match.group(0)
    try:
        data = json.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def extract_from_image(image_bytes: bytes, mime: str = "image/jpeg") -> dict:
    import base64
    from services.ai_assistant.client import chat_completion

    encoded = base64.b64encode(image_bytes).decode("ascii")
    prompt = (
        "這是一張尚未正式收錄資料庫的寶可夢 TCG 卡片圖。"
        "請只輸出 JSON，不要解釋。欄位："
        '{"name":"中文名或圖上主標題","japanese_name":"日文名若有",'
        '"english_name":"英文名若有","card_type":"Pokémon|Trainer|Energy",'
        '"sub_type":"Basic|Stage 1|Stage 2|Item|Supporter|Stadium|Tool|Special|Basic Energy",'
        '"hp":0,"element_type":"Grass|Fire|Water|Lightning|Psychic|Fighting|Darkness|Metal|Fairy|Dragon|Colorless",'
        '"rarity":"","set_code":"","set_number":"","description":"訓練家或能量效果",'
        '"skills":[{"type":"ability|attack","name":"","cost":["Fire"],"damage":"","effect":""}]}'
        "cost 用英文屬性。看不到的欄位用空字串或 0。"
    )
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": prompt},
            {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
        ],
    }]
    content = chat_completion(messages, temperature=0.1)
    parsed = _parse_json_object(content)
    if not parsed:
        raise ValueError("AI 未能從圖片抽出卡牌資料")
    parsed["raw_ai_text"] = content
    return parsed


def _next_temp_id(cursor) -> str:
    token = uuid.uuid4().hex[:10]
    return f"prov_{token}"


def save_provisional(parsed: dict, image_filename: str, created_by: str | None = None, approve: bool = True) -> dict:
    conn = database.get_db_connection()
    if not conn:
        raise RuntimeError("Database unavailable")
    try:
        cursor = conn.cursor()
        temp_id = _next_temp_id(cursor)
        name = str(parsed.get("name") or "").strip() or temp_id
        card_type = parsed.get("card_type") or "Trainer"
        if card_type not in ("Pokémon", "Trainer", "Energy"):
            card_type = "Trainer"
        skills = parsed.get("skills") or []
        if isinstance(skills, str):
            try:
                skills = json.loads(skills)
            except Exception:
                skills = []
        status = "approved" if approve else "pending"
        cursor.execute(
            """
            INSERT INTO provisional_cards (
                temp_card_id, name, japanese_name, english_name, card_type, sub_type,
                hp, element_type, skills_json, rarity, set_code, set_number,
                description, image_file, raw_ai_json, status, created_by
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id
            """,
            (
                temp_id,
                name,
                parsed.get("japanese_name") or "",
                parsed.get("english_name") or "",
                card_type,
                parsed.get("sub_type") or "",
                int(parsed.get("hp") or 0) or None,
                parsed.get("element_type") or "",
                json.dumps(skills, ensure_ascii=False),
                parsed.get("rarity") or "",
                parsed.get("set_code") or "",
                str(parsed.get("set_number") or ""),
                parsed.get("description") or "",
                image_filename,
                json.dumps(parsed, ensure_ascii=False),
                status,
                created_by or "",
            ),
        )
        if approve:
            cursor.execute(
                """
                INSERT INTO cards (
                    card_id, image_file, card_type, name, sub_type, hp, element_type,
                    skills_json, rarity, set_code, set_number, japanese_name, description,
                    source
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'provisional')
                ON CONFLICT (card_id) DO UPDATE SET
                    name = EXCLUDED.name,
                    image_file = EXCLUDED.image_file,
                    card_type = EXCLUDED.card_type,
                    sub_type = EXCLUDED.sub_type,
                    hp = EXCLUDED.hp,
                    element_type = EXCLUDED.element_type,
                    skills_json = EXCLUDED.skills_json,
                    source = 'provisional'
                """,
                (
                    temp_id,
                    image_filename,
                    card_type,
                    name,
                    parsed.get("sub_type") or "",
                    int(parsed.get("hp") or 0) or None,
                    parsed.get("element_type") or "",
                    json.dumps(skills, ensure_ascii=False),
                    parsed.get("rarity") or "",
                    parsed.get("set_code") or "",
                    str(parsed.get("set_number") or ""),
                    parsed.get("japanese_name") or "",
                    parsed.get("description") or "",
                ),
            )
        conn.commit()
        return {
            "temp_card_id": temp_id,
            "name": name,
            "card_type": card_type,
            "status": status,
            "image_file": image_filename,
            "parsed": parsed,
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_provisional(limit: int = 100) -> list[dict]:
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM provisional_cards
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (limit,),
        )
        rows = []
        for row in cursor.fetchall():
            item = dict(row)
            if item.get("created_at"):
                item["created_at"] = str(item["created_at"])
            rows.append(item)
        return rows
    except Exception:
        return []
    finally:
        conn.close()


def _remap_json_card_ids(payload: Any, mapping: dict[str, str]) -> tuple[Any, int]:
    changed = 0
    if isinstance(payload, list):
        out = []
        for item in payload:
            new_item, delta = _remap_json_card_ids(item, mapping)
            changed += delta
            out.append(new_item)
        return out, changed
    if isinstance(payload, dict):
        out = dict(payload)
        for key in ("card_id", "id", "local_card_id", "local_tw_card_id"):
            value = str(out.get(key) or "")
            if value in mapping:
                out[key] = mapping[value]
                changed += 1
        if "content" in out and isinstance(out["content"], (list, dict, str)):
            nested, delta = _remap_json_card_ids(out["content"], mapping)
            out["content"] = nested
            changed += delta
        return out, changed
    if isinstance(payload, str):
        try:
            parsed = json.loads(payload)
        except Exception:
            return payload, 0
        nested, changed = _remap_json_card_ids(parsed, mapping)
        return json.dumps(nested, ensure_ascii=False), changed
    return payload, 0


def _remap_stored_decks(cursor, mapping: dict[str, str]) -> int:
    changed_total = 0
    for table, column, id_col in (
        ("user_workspace", "content", "id"),
        ("decks", "content", "id"),
    ):
        try:
            cursor.execute(f"SELECT {id_col}, {column} FROM {table}")
        except Exception:
            try:
                cursor.connection.rollback()
            except Exception:
                pass
            continue
        for row in cursor.fetchall():
            item = dict(row)
            new_content, changed = _remap_json_card_ids(item.get(column), mapping)
            if changed:
                cursor.execute(
                    f"UPDATE {table} SET {column} = %s WHERE {id_col} = %s",
                    (new_content if isinstance(new_content, str) else json.dumps(new_content, ensure_ascii=False), item[id_col]),
                )
                changed_total += changed
    return changed_total


def replace_if_official_match(official_name: str | None, official_card_id: str | None) -> dict:
    """當正式卡寫入時，用名稱取代仍生效嘅臨時卡。"""
    name = str(official_name or "").strip()
    official_id = str(official_card_id or "").strip()
    result = {"replaced": 0, "remapped": 0}
    if not name or not official_id or official_id.startswith("prov_"):
        return result

    conn = database.get_db_connection()
    if not conn:
        return result
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT temp_card_id, name, japanese_name, english_name
            FROM provisional_cards
            WHERE status IN ('pending', 'approved')
            """
        )
        mapping = {}
        target_norm = normalize_card_name(name)
        for row in cursor.fetchall():
            item = dict(row)
            names = [
                item.get("name"),
                item.get("japanese_name"),
                item.get("english_name"),
            ]
            if any(normalize_card_name(candidate) == target_norm and target_norm for candidate in names):
                mapping[item["temp_card_id"]] = official_id
        if not mapping:
            return result

        remapped = _remap_stored_decks(cursor, mapping)
        for temp_id, new_id in mapping.items():
            cursor.execute(
                """
                UPDATE provisional_cards
                SET status = 'replaced', replaced_by = %s
                WHERE temp_card_id = %s
                """,
                (new_id, temp_id),
            )
            cursor.execute(
                """
                UPDATE cards
                SET source = 'replaced', replaced_by = %s
                WHERE card_id = %s
                """,
                (new_id, temp_id),
            )
            cursor.execute(
                """
                UPDATE limitless_deck_cards
                SET local_tw_card_id = %s
                WHERE local_tw_card_id = %s
                """,
                (new_id, temp_id),
            )
        conn.commit()
        result["replaced"] = len(mapping)
        result["remapped"] = remapped
        if mapping:
            print(f">>> [Provisional] replaced {mapping} with {official_id}", flush=True)
        return result
    except Exception as exc:
        conn.rollback()
        print(f">>> [Provisional] replace failed: {exc}", flush=True)
        return result
    finally:
        conn.close()
