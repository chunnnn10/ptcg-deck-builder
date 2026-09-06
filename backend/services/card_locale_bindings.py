"""JP/EN 來源卡 → 繁中卡 的本地綁定。

第一次可以打 TCGDex；結果寫入 card_locale_bindings。
之後同一 source_key（系列+編號，不是純卡名）只讀本地。
Admin 可批准、否決或手動改綁。
"""
from __future__ import annotations

from datetime import datetime

import database


def source_key(lang: str | None, set_code: str | None, set_number: str | None) -> str | None:
    lang = (lang or "jp").strip().lower() or "jp"
    set_code = str(set_code or "").strip().upper()
    number = str(set_number or "").strip()
    if "/" in number:
        number = number.split("/", 1)[0]
    number = number.lstrip("0") or "0"
    if not set_code or number in ("", "0"):
        return None
    return f"{lang}|{set_code}|{number}"


def _row_to_dict(row) -> dict | None:
    if not row:
        return None
    item = dict(row)
    for key in ("created_at", "reviewed_at"):
        if item.get(key):
            item[key] = str(item[key])
    return item


def get_binding(cursor, key: str | None) -> dict | None:
    if not key:
        return None
    try:
        cursor.execute("SELECT * FROM card_locale_bindings WHERE source_key = %s", (key,))
        return _row_to_dict(cursor.fetchone())
    except Exception:
        try:
            cursor.execute(
                """
                CREATE TABLE IF NOT EXISTS card_locale_bindings (
                    id SERIAL PRIMARY KEY,
                    source_key VARCHAR UNIQUE NOT NULL,
                    source_lang VARCHAR DEFAULT 'jp',
                    source_set_code VARCHAR,
                    source_set_number VARCHAR,
                    source_name TEXT,
                    tw_card_id VARCHAR,
                    tw_name TEXT,
                    tw_set_code VARCHAR,
                    tw_set_number VARCHAR,
                    status VARCHAR DEFAULT 'pending',
                    method VARCHAR DEFAULT 'auto',
                    context TEXT,
                    reviewed_by VARCHAR,
                    reviewed_at TIMESTAMP,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
        except Exception:
            pass
        return None


def upsert_binding(
    cursor,
    *,
    key: str,
    source_lang: str = "jp",
    source_set_code: str = "",
    source_set_number: str = "",
    source_name: str = "",
    tw_card_id: str | None = None,
    tw_name: str = "",
    tw_set_code: str = "",
    tw_set_number: str = "",
    status: str = "pending",
    method: str = "auto",
    context: str = "",
) -> None:
    cursor.execute(
        """
        INSERT INTO card_locale_bindings (
            source_key, source_lang, source_set_code, source_set_number, source_name,
            tw_card_id, tw_name, tw_set_code, tw_set_number, status, method, context
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (source_key) DO UPDATE SET
            source_name = COALESCE(EXCLUDED.source_name, card_locale_bindings.source_name),
            tw_card_id = COALESCE(EXCLUDED.tw_card_id, card_locale_bindings.tw_card_id),
            tw_name = COALESCE(EXCLUDED.tw_name, card_locale_bindings.tw_name),
            tw_set_code = COALESCE(EXCLUDED.tw_set_code, card_locale_bindings.tw_set_code),
            tw_set_number = COALESCE(EXCLUDED.tw_set_number, card_locale_bindings.tw_set_number),
            status = CASE
                WHEN card_locale_bindings.status = 'approved' THEN card_locale_bindings.status
                ELSE EXCLUDED.status
            END,
            method = CASE
                WHEN card_locale_bindings.status = 'approved' THEN card_locale_bindings.method
                ELSE EXCLUDED.method
            END,
            context = COALESCE(EXCLUDED.context, card_locale_bindings.context),
            updated_at = CURRENT_TIMESTAMP
        """,
        (
            key, source_lang, source_set_code, source_set_number, source_name,
            tw_card_id, tw_name, tw_set_code, tw_set_number, status, method, context,
        ),
    )


def list_bindings(status: str | None = None, limit: int = 100) -> list[dict]:
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        if status:
            cursor.execute(
                """
                SELECT * FROM card_locale_bindings
                WHERE status = %s
                ORDER BY updated_at DESC NULLS LAST, id DESC
                LIMIT %s
                """,
                (status, limit),
            )
        else:
            cursor.execute(
                """
                SELECT * FROM card_locale_bindings
                ORDER BY
                    CASE status WHEN 'pending' THEN 0 WHEN 'unresolved' THEN 1 ELSE 2 END,
                    updated_at DESC NULLS LAST
                LIMIT %s
                """,
                (limit,),
            )
        rows = [_row_to_dict(row) for row in cursor.fetchall()]
        for item in rows:
            tw_file = ""
            jp_file = ""
            if item.get("tw_card_id"):
                cursor.execute("SELECT image_file FROM cards WHERE card_id = %s", (item["tw_card_id"],))
                found = cursor.fetchone()
                if found:
                    tw_file = found.get("image_file") or ""
            cursor.execute(
                """
                SELECT image_file FROM jp_cards
                WHERE name = %s
                   OR (
                        LOWER(COALESCE(set_code,'')) = LOWER(%s)
                    AND split_part(COALESCE(set_number,''), '/', 1) = split_part(%s, '/', 1)
                   )
                ORDER BY CASE WHEN COALESCE(image_file,'') <> '' THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (item.get("source_name") or "", item.get("source_set_code") or "", item.get("source_set_number") or ""),
            )
            found = cursor.fetchone()
            if found:
                jp_file = found.get("image_file") or ""
            item["tw_image_url"] = f"/images/{tw_file}" if tw_file else ""
            item["jp_image_url"] = f"/images_jp/{jp_file}" if jp_file else ""
        return rows
    except Exception:
        return []
    finally:
        conn.close()


def review_binding(binding_id: int, *, action: str, tw_card_id: str | None = None, reviewer: str = "") -> tuple[bool, str]:
    conn = database.get_db_connection()
    if not conn:
        return False, "Database unavailable"
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM card_locale_bindings WHERE id = %s", (binding_id,))
        row = cursor.fetchone()
        if not row:
            return False, "找不到這筆綁定"
        status = "approved" if action == "approve" else "rejected" if action == "reject" else "approved"
        new_tw = tw_card_id or row.get("tw_card_id")
        tw_meta = {"name": row.get("tw_name"), "set_code": row.get("tw_set_code"), "set_number": row.get("tw_set_number")}
        if new_tw:
            cursor.execute(
                "SELECT name, set_code, set_number FROM cards WHERE card_id = %s",
                (new_tw,),
            )
            found = cursor.fetchone()
            if found:
                tw_meta = dict(found)
        cursor.execute(
            """
            UPDATE card_locale_bindings
            SET status = %s,
                tw_card_id = %s,
                tw_name = %s,
                tw_set_code = %s,
                tw_set_number = %s,
                method = 'admin',
                reviewed_at = %s,
                reviewed_by = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = %s
            """,
            (
                status,
                new_tw,
                tw_meta.get("name") or "",
                tw_meta.get("set_code") or "",
                tw_meta.get("set_number") or "",
                datetime.utcnow(),
                reviewer,
                binding_id,
            ),
        )
        if status == "approved" and new_tw:
            cursor.execute(
                """
                UPDATE limitless_deck_cards
                SET local_tw_card_id = %s
                WHERE LOWER(COALESCE(set_code,'')) = LOWER(%s)
                  AND split_part(COALESCE(set_number,''), '/', 1) = split_part(%s, '/', 1)
                """,
                (new_tw, row.get("source_set_code") or "", row.get("source_set_number") or "0"),
            )
        conn.commit()
        return True, "已更新"
    except Exception as exc:
        conn.rollback()
        return False, str(exc)
    finally:
        conn.close()
