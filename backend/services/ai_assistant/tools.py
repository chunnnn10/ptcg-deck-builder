from __future__ import annotations

import json
import os
import re
import threading
import time
from collections import Counter, defaultdict
from typing import Any

import config
import database

from .embeddings import embed_texts, vector_literal
from .indexer import STANDARD_MARKS, ensure_ai_schema, parse_skills
from .predicates import (
    card_matches_filter,
    hp_bounds_from_spec,
    normalize_card_type,
    parse_predicates,
)
from services.logic_extractor.adapter import EXTRACTOR_VERSION as LOGIC_EXTRACTOR_VERSION
from services.card_roles.tagger import FILTERABLE_PARAM_KEYS


CARD_LIMIT = 20
META_LIMIT = 10

# regulation_settings 讀唔到時嘅標準賽制標記回退值。
FALLBACK_STANDARD_MARKS = ("F", "G", "H", "I", "J")
_STANDARD_MARKS_TTL = 300.0
_STANDARD_MARKS_CACHE: dict[str, Any] = {"marks": None, "expires": 0.0}
_STANDARD_MARKS_LOCK = threading.Lock()

# pg_trgm 只探測一次；失敗就永遠行純 ILIKE 路徑，唔會再撞。
_PG_TRGM_CACHE: dict[str, Any] = {"checked": False, "available": False, "reason": ""}
_PG_TRGM_LOCK = threading.Lock()

_CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")


def _standard_marks() -> list[str]:
    """由 regulation_settings(is_standard=TRUE) 讀標準標記，帶 5 分鐘快取。

    DB 失敗或查無資料時回退 FALLBACK_STANDARD_MARKS，永不拋錯。
    """
    now = time.time()
    cached = _STANDARD_MARKS_CACHE.get("marks")
    if cached and float(_STANDARD_MARKS_CACHE.get("expires") or 0) > now:
        return list(cached)
    marks: list[str] = []
    conn = database.get_db_connection()
    if conn:
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT mark FROM regulation_settings WHERE is_standard = TRUE ORDER BY mark")
            marks = [str(row.get("mark") or "").strip().upper() for row in cursor.fetchall()]
            marks = [mark for mark in marks if mark]
        except Exception as exc:
            print(f"[ai_tools] WARNING: regulation_settings 讀取失敗：{exc}（回退 {FALLBACK_STANDARD_MARKS}）", flush=True)
        finally:
            conn.close()
    if not marks:
        marks = list(FALLBACK_STANDARD_MARKS)
    with _STANDARD_MARKS_LOCK:
        _STANDARD_MARKS_CACHE["marks"] = list(marks)
        _STANDARD_MARKS_CACHE["expires"] = now + _STANDARD_MARKS_TTL
    return marks


def _ensure_pg_trgm(conn) -> tuple[bool, str]:
    """嘗試啟用 pg_trgm，只做一次並快取結果；失敗回 (False, reason)。"""
    if _PG_TRGM_CACHE.get("checked"):
        return bool(_PG_TRGM_CACHE.get("available")), str(_PG_TRGM_CACHE.get("reason") or "")
    with _PG_TRGM_LOCK:
        if _PG_TRGM_CACHE.get("checked"):
            return bool(_PG_TRGM_CACHE.get("available")), str(_PG_TRGM_CACHE.get("reason") or "")
        available = False
        reason = ""
        try:
            cursor = conn.cursor()
            cursor.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
            conn.commit()
            available = True
        except Exception as exc:
            reason = str(exc)
            try:
                conn.rollback()
            except Exception:
                pass
        _PG_TRGM_CACHE["checked"] = True
        _PG_TRGM_CACHE["available"] = available
        _PG_TRGM_CACHE["reason"] = reason
        return available, reason


def _degraded_note(reason: str) -> dict[str, Any]:
    """檢索降級診斷物件。

    list 回傳形狀維持不變（assistant.py 直接當 list 用，唔可以改成 dict），
    所以只在尾端附加一個冇 card_id 嘅明顯診斷物件：
      - _collect_cards_from_value 需要 card_id+name 才會當卡，會自動忽略；
      - merged_cards / meta 收集亦會忽略；
      - 呼叫方可以用 item.get('degraded') 判斷係唔係降級。
    """
    return {"degraded": True, "reason": str(reason or "unknown"), "_diagnostic": True}


def _with_degraded(cards: list[dict[str, Any]] | None, reason: str) -> list[dict[str, Any]]:
    clean = [item for item in (cards or []) if isinstance(item, dict) and not item.get("_diagnostic")]
    clean.append(_degraded_note(reason))
    return clean


def _split_diagnostics(items: list[dict[str, Any]] | None) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """分開正常結果同 degraded 診斷物件，令內部語意路徑唔會被診斷污染。"""
    clean: list[dict[str, Any]] = []
    notes: list[dict[str, Any]] = []
    for item in items or []:
        if isinstance(item, dict) and item.get("_diagnostic"):
            notes.append(item)
        else:
            clean.append(item)
    return clean, notes


def _has_cjk(text: str) -> bool:
    return bool(_CJK_RE.search(str(text or "")))


def _cjk_ngrams(text: str, sizes: tuple[int, ...] = (4, 3, 2), limit: int = 12) -> list[str]:
    """由連續中文片段切 4/3/2 字 n-gram 做關鍵字候選；拉丁字串唔會產生。"""
    out: list[str] = []
    seen: set[str] = set()
    for frag in re.findall(r"[\u3400-\u9fff\uf900-\ufaff]+", str(text or "")):
        length = len(frag)
        for size in sizes:
            if length < size:
                continue
            for idx in range(length - size + 1):
                gram = frag[idx:idx + size]
                if gram not in seen:
                    seen.add(gram)
                    out.append(gram)
                    if len(out) >= limit:
                        return out
    return out


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
    predicates = parse_predicates(row.get("logic_predicates"))
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
        "predicates": predicates,
        "logic_extractor_version": row.get("logic_extractor_version"),
        "logic_source_card_id": row.get("logic_source_card_id"),
    }
    if language == "tw" and row.get("japanese_name"):
        payload["japanese_name"] = row.get("japanese_name")
    if language == "jp" and row.get("chinese_name"):
        payload["chinese_name"] = row.get("chinese_name")
    return payload


def _qualified(column: str, alias: str | None) -> str:
    return f"{alias}.{column}" if alias else column


def _select_columns(table: str, alias: str | None = None) -> str:
    extra_name = "japanese_name" if table == "cards" else "chinese_name"
    columns = (
        "card_id", "image_file", "card_type", "name", "sub_type", "hp", "element_type",
        "weakness_type", "weakness_value", "resistance_type", "resistance_value", "retreat_cost", "rarity",
        extra_name, "set_code", "set_number", "set_name", "regulation_mark", "skills_json", "description",
    )
    return ", ".join(_qualified(column, alias) for column in columns)


def _logic_columns_ready(cursor) -> bool:
    cursor.execute(
        """
        SELECT COUNT(*) AS count
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'processed_cards'
          AND column_name IN ('predicates', 'extractor_version', 'source_card_id')
        """
    )
    row = cursor.fetchone()
    return int(row.get("count") or 0) == 3


def _select_columns_with_logic(table: str, alias: str = "c") -> str:
    return (
        f"{_select_columns(table, alias)}, "
        "pc.predicates AS logic_predicates, "
        "pc.extractor_version AS logic_extractor_version, "
        "pc.source_card_id AS logic_source_card_id"
    )


def _select_columns_without_logic(table: str, alias: str | None = None) -> str:
    return (
        f"{_select_columns(table, alias)}, "
        "'[]'::jsonb AS logic_predicates, "
        "NULL::text AS logic_extractor_version, "
        "NULL::text AS logic_source_card_id"
    )


def _logic_join_sql(language: str, table_alias: str = "c") -> str:
    if language == "jp":
        return (
            "LEFT JOIN processed_cards pc "
            f"ON pc.card_id = {table_alias}.card_id "
            "AND pc.extractor_version = %s"
        )
    return f"""
        LEFT JOIN LATERAL (
            SELECT jp.card_id
            FROM jp_cards jp
            WHERE {table_alias}.set_code = jp.set_code
              AND split_part({table_alias}.set_number, '/', 1) ~ '^[0-9]+$'
              AND split_part(jp.set_number, '/', 1) ~ '^[0-9]+$'
              AND split_part({table_alias}.set_number, '/', 1)::int = split_part(jp.set_number, '/', 1)::int
            ORDER BY jp.card_id
            LIMIT 1
        ) jp ON TRUE
        LEFT JOIN processed_cards pc
          ON pc.card_id = jp.card_id
         AND pc.extractor_version = %s
    """


def _normalize_marks(filters: dict[str, Any] | None = None) -> list[str]:
    standard = _standard_marks()
    allowed = set(standard) | set(FALLBACK_STANDARD_MARKS)
    raw = (filters or {}).get("standard_marks") or (filters or {}).get("regulation_marks") or standard
    marks = [str(mark).strip().upper() for mark in raw if str(mark).strip()]
    marks = [mark for mark in marks if mark in allowed]
    return marks or standard


# ── 結構化 filter 嘅原始欄位查詢 ──────────────────────────────────────────
# predicate_filter 可以描述「卡文 predicate」，亦可以描述「卡本身原始欄位」
# （hp / card_type / sub_type…）。原始欄位唔可以只靠 keyword／語意檢索嘅候選，
# 否則符合條件嘅卡根本冇被檢索到，就永遠回 []。所以呢度直接由 SQL 依原始欄位揀卡，
# 再交 card_matches_filter 做最終驗證。

_CARD_TYPE_SQL_VALUES: dict[str, tuple[str, ...]] = {
    "pokemon": ("Pokémon", "Pokemon", "POKEMON", "ポケモン", "寶可夢", "宝可梦", "神奇寶貝", "神奇宝贝"),
    "trainer": ("Trainer", "TRAINER", "トレーナー", "訓練家", "训练家"),
    "energy": ("Energy", "ENERGY", "エネルギー", "能量"),
}

_BASIC_SUB_TYPE_SQL_TOKENS = ("基礎", "基本", "basic", "Basic", "BASIC", "たね")

def _filter_specs(filters: dict[str, Any] | None) -> list[dict[str, Any]]:
    raw = (filters or {}).get("predicate_filter")
    if not raw:
        return []
    specs = raw if isinstance(raw, list) else [raw]
    return [spec for spec in specs if isinstance(spec, dict)]


def _like_token(value: Any) -> str:
    return str(value or "").strip().replace("%", "").replace("_", "")


def _card_type_sql(alias: str, kind: str, params: list[Any]) -> str:
    params.append(list(_CARD_TYPE_SQL_VALUES.get(kind) or ()))
    params.append(f"%{kind}%")
    return f"({alias}.card_type = ANY(%s) OR lower({alias}.card_type) LIKE %s)"


def _basic_sub_type_sql(alias: str) -> str:
    likes = " OR ".join(f"{alias}.sub_type LIKE '%%{token}%%'" for token in _BASIC_SUB_TYPE_SQL_TOKENS)
    return f"({likes})"


def _spec_sql_condition(spec: dict[str, Any], params: list[Any], alias: str = "c") -> str | None:
    """把 spec 內所有辨認得到嘅原始欄位轉成一個 AND 條件；無從下推就回 None。

    同 `_raw_fields_match_spec` 一樣係「同一 spec 內所有面向都必須成立」：
    {"type": "hp_threshold", "value": 90, "sub_type": "基礎"} 會同時下推 hp 同
    sub_type。只描述卡文 predicate 嘅 spec（例如 {"type": "search_deck"}）冇
    原始欄位可下推，所以回 None，維持原本「靠已檢索候選再過濾」嘅行為。
    """
    clauses: list[str] = []

    bounds = hp_bounds_from_spec(spec)
    if bounds and bounds.get("applies_to") != "attached_pokemon_remaining_hp":
        low, high = bounds.get("min"), bounds.get("max")
        if low is not None or high is not None:
            applies_to = bounds.get("applies_to")
            clauses.append(f"{alias}.hp IS NOT NULL")
            if applies_to in ("pokemon", "basic_pokemon"):
                clauses.append(_card_type_sql(alias, "pokemon", params))
            if applies_to == "basic_pokemon":
                clauses.append(_basic_sub_type_sql(alias))
            if low is not None:
                clauses.append(f"{alias}.hp >= %s")
                params.append(int(low))
            if high is not None:
                clauses.append(f"{alias}.hp <= %s")
                params.append(int(high))

    kind = normalize_card_type(spec.get("card_type"))
    if kind:
        clauses.append(_card_type_sql(alias, kind, params))
    sub_type = _like_token(spec.get("sub_type"))
    if sub_type:
        clauses.append(f"{alias}.sub_type ILIKE %s")
        params.append(f"%{sub_type}%")
    for key in ("element_type", "name", "set_code", "set_number", "rarity"):
        token = _like_token(spec.get(key))
        if token:
            clauses.append(f"COALESCE({alias}.{key}, '') ILIKE %s")
            params.append(f"%{token}%")
    mark = str(spec.get("regulation_mark") or "").strip().upper()
    if mark:
        clauses.append(f"upper(COALESCE({alias}.regulation_mark, '')) = %s")
        params.append(mark)

    if not clauses:
        return None
    return "(" + " AND ".join(clauses) + ")"


def _structured_card_search(language: str = "tw", limit: int = CARD_LIMIT, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """依 predicate_filter 嘅原始欄位（hp / card_type / sub_type…）直接由 DB 揀卡。

    spec 只描述卡文 predicate 時唔會行呢條路（SQL 冇條件可下推），維持原本行為；
    spec 描述原始欄位時，就算 keyword／語意檢索搵唔到，都一定回傳符合嘅卡。
    """
    specs = _filter_specs(filters)
    if not specs:
        return []
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    marks = _normalize_marks(filters)
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"

    params: list[Any] = []
    conditions = [cond for cond in (_spec_sql_condition(spec, params) for spec in specs) if cond]
    if not conditions:
        return []

    fetch_limit = min(max(limit * 10, 50), 400)
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        logic_ready = _logic_columns_ready(cursor)
        select_sql = _select_columns_with_logic(table, "c") if logic_ready else _select_columns_without_logic(table, "c")
        logic_join = _logic_join_sql(language, "c") if logic_ready else ""
        cursor.execute(
            f"""
            SELECT {select_sql}
            FROM {table} c
            {logic_join}
            WHERE c.regulation_mark = ANY(%s)
              AND ({" OR ".join(conditions)})
            ORDER BY c.card_id DESC
            LIMIT %s
            """,
            ((LOGIC_EXTRACTOR_VERSION,) if logic_ready else ()) + (marks,) + tuple(params) + (fetch_limit,),
        )
        cards: list[dict[str, Any]] = []
        for row in cursor.fetchall():
            payload = _card_payload(row, folder_lang, len(cards) < 8)
            if card_matches_filter(payload, specs):
                payload["structured_match"] = True
                cards.append(payload)
            if len(cards) >= limit:
                break
        return cards
    except Exception as exc:
        print(f"[ai_tools] WARNING: _structured_card_search 失敗：{exc}", flush=True)
        return []
    finally:
        conn.close()


def _keyword_card_search(query: str, language: str = "tw", limit: int = CARD_LIMIT, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    if not query:
        return []
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    marks = _normalize_marks(filters)
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    extra_name = "chinese_name" if language == "jp" else "japanese_name"
    predicate_filter = (filters or {}).get("predicate_filter")
    fetch_limit = min(limit * 4, 80) if predicate_filter else limit
    search = f"%{query}%"

    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        logic_ready = _logic_columns_ready(cursor)
        select_sql = _select_columns_with_logic(table, "c") if logic_ready else _select_columns_without_logic(table, "c")
        logic_join = _logic_join_sql(language, "c") if logic_ready else ""
        cursor.execute(
            f"""
            SELECT {select_sql}
            FROM {table} c
            {logic_join}
            WHERE c.regulation_mark = ANY(%s)
              AND (
                c.name ILIKE %s OR c.card_id ILIKE %s OR c.set_code ILIKE %s OR c.set_number ILIKE %s
                OR COALESCE(c.{extra_name}, '') ILIKE %s
                OR COALESCE(c.description, '') ILIKE %s
                OR COALESCE(c.skills_json::text, '') ILIKE %s
              )
            ORDER BY
                CASE WHEN c.name = %s THEN 0 ELSE 1 END,
                CASE WHEN c.name ILIKE %s THEN 0 ELSE 1 END,
                c.card_id DESC
            LIMIT %s
            """,
            ((LOGIC_EXTRACTOR_VERSION,) if logic_ready else ())
            + (marks, search, search, search, search, search, search, search, query, search, fetch_limit),
        )
        cards = [_card_payload(row, folder_lang, idx < 8) for idx, row in enumerate(cursor.fetchall())]
        if predicate_filter:
            cards = [card for card in cards if card_matches_filter(card, predicate_filter)]
        return cards[:limit]
    finally:
        conn.close()


def _trigram_card_search(query: str, language: str = "tw", limit: int = CARD_LIMIT, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """pg_trgm 相似度 + ILIKE 中文檢索；pg_trgm 唔可用時回傳 []（由呼叫方回退 ILIKE）。

    只針對 CJK 或含 CJK 嘅 query 用 similarity，避免拉丁短字被相似度噪音污染。
    """
    query = str(query or "").strip()
    if not query or not _has_cjk(query):
        return []
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    marks = _normalize_marks(filters)
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    extra_name = "chinese_name" if language == "jp" else "japanese_name"
    predicate_filter = (filters or {}).get("predicate_filter")
    fetch_limit = min(limit * 4, 80) if predicate_filter else limit
    terms = _card_query_terms(query) or [query]

    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        available, _reason = _ensure_pg_trgm(conn)
        if not available:
            return []
        cursor = conn.cursor()
        logic_ready = _logic_columns_ready(cursor)
        select_sql = _select_columns_with_logic(table, "c") if logic_ready else _select_columns_without_logic(table, "c")
        logic_join = _logic_join_sql(language, "c") if logic_ready else ""
        likes = [f"%{term.replace('%', '').replace('_', '')}%" for term in terms if term]
        if not likes:
            return []
        cursor.execute(
            f"""
            SELECT {select_sql},
                   GREATEST(
                       similarity(c.name, %s),
                       similarity(COALESCE(c.{extra_name}, ''), %s),
                       COALESCE((SELECT MAX(similarity(term, c.name)) FROM unnest(%s::text[]) AS term), 0)
                   ) AS trgm_score
            FROM {table} c
            {logic_join}
            WHERE c.regulation_mark = ANY(%s)
              AND (
                c.name ILIKE ANY(%s)
                OR COALESCE(c.{extra_name}, '') ILIKE ANY(%s)
                OR COALESCE(c.description, '') ILIKE ANY(%s)
                OR COALESCE(c.skills_json::text, '') ILIKE ANY(%s)
                OR similarity(c.name, %s) > 0.25
              )
            ORDER BY trgm_score DESC, CASE WHEN c.name ILIKE ANY(%s) THEN 0 ELSE 1 END, c.card_id DESC
            LIMIT %s
            """,
            ((LOGIC_EXTRACTOR_VERSION,) if logic_ready else ())
            + (query, query, terms, marks, likes, likes, likes, likes, query, likes, fetch_limit),
        )
        cards = [_card_payload(row, folder_lang, idx < 8) for idx, row in enumerate(cursor.fetchall())]
        if predicate_filter:
            cards = [card for card in cards if card_matches_filter(card, predicate_filter)]
        return cards[:limit]
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return []
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

    # predicate_filter 描述卡本身原始欄位時，keyword／trigram 檢索未必命中；
    # 先由結構化 SQL 直接取符合嘅卡，再補關鍵字候選。
    append_cards(_structured_card_search(language, limit, filters))

    # 先試 trigram 相似度（CJK 專用，pg_trgm 可用時），再逐 term ILIKE 補齊。
    append_cards(_trigram_card_search(query, language, limit, filters))
    terms = _card_query_terms(query)
    for term in terms:
        if len(results) >= limit:
            break
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
    # 中文無空格查詢：原本只會產生一個長 token，ILIKE 幾乎唔會命中。
    # 補 2-4 字 n-gram 做候選，交由 ILIKE/pg_trgm 逐個查；拉丁查詢完全唔受影響。
    if _has_cjk(text) and not re.search(r"[\s\u3000]", text.strip()):
        for gram in _cjk_ngrams(cleaned, limit=12):
            add(gram)
    return sorted(terms, key=len, reverse=True)[:10]


def semantic_search_cards(query: str, limit: int = CARD_LIMIT, filters: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    query = str(query or "").strip()
    language = str((filters or {}).get("language") or "tw")
    if language not in ("tw", "jp"):
        language = "tw"
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))
    marks = _normalize_marks(filters)
    predicate_filter = (filters or {}).get("predicate_filter")
    fetch_limit = min(limit * 4, 80) if predicate_filter else limit

    conn = database.get_db_connection()
    if not conn:
        return _with_degraded(_expanded_keyword_card_search(query, language, limit, filters), "db_unavailable")
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
            (vector_literal(vector), language, marks, vector_literal(vector), fetch_limit),
        )
        rows = cursor.fetchall()
        ids = [row["source_id"] for row in rows]
        score_by_id = {row["source_id"]: float(row["score"] or 0) for row in rows}
        if not ids:
            return _with_degraded(_expanded_keyword_card_search(query, language, limit, filters), "semantic_empty_fallback_keyword")
        table = "jp_cards" if language == "jp" else "cards"
        logic_ready = _logic_columns_ready(cursor)
        select_sql = _select_columns_with_logic(table, "c") if logic_ready else _select_columns_without_logic(table, "c")
        logic_join = _logic_join_sql(language, "c") if logic_ready else ""
        cursor.execute(
            f"""
            SELECT {select_sql}
            FROM {table} c
            {logic_join}
            WHERE c.card_id = ANY(%s)
            """,
            ((LOGIC_EXTRACTOR_VERSION,) if logic_ready else ()) + (ids,),
        )
        by_id = {row["card_id"]: row for row in cursor.fetchall()}
        cards = []
        for cid in ids:
            row = by_id.get(cid)
            if row:
                payload = _card_payload(row, language, len(cards) < 8)
                if predicate_filter and not card_matches_filter(payload, predicate_filter):
                    continue
                payload["semantic_score"] = round(score_by_id.get(cid, 0), 4)
                cards.append(payload)
        if predicate_filter and len(cards) < limit:
            seen_before = {card.get("card_id") for card in cards}
            for card in _structured_card_search(language, limit, filters):
                if card.get("card_id") in seen_before or len(cards) >= limit:
                    continue
                card["semantic_score"] = None
                cards.append(card)
        keyword_cards = _expanded_keyword_card_search(query, language, min(limit, 6), filters)
        seen = {card.get("card_id") for card in cards}
        for card in keyword_cards:
            if card.get("card_id") not in seen and len(cards) < limit:
                card["semantic_score"] = None
                cards.append(card)
        return cards
    except Exception as exc:
        # 唔再靜默：向量路徑壞咗要回報 degraded，令呼叫方/模型知道係「壞」唔係「冇結果」。
        print(f"[ai_tools] WARNING: semantic_search_cards 降級為關鍵字檢索：{exc}", flush=True)
        return _with_degraded(_expanded_keyword_card_search(query, language, limit, filters), f"semantic_failed:{type(exc).__name__}")
    finally:
        conn.close()


def search_cards(query: str, language: str = "tw", limit: int = CARD_LIMIT) -> list[dict[str, Any]]:
    return semantic_search_cards(query, limit, {"language": language, "standard_marks": _standard_marks()})


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
        logic_ready = _logic_columns_ready(cursor)
        select_sql = _select_columns_with_logic(table, "c") if logic_ready else _select_columns_without_logic(table, "c")
        logic_join = _logic_join_sql(language, "c") if logic_ready else ""
        cursor.execute(
            f"""
            SELECT {select_sql}
            FROM {table} c
            {logic_join}
            WHERE c.card_id = %s AND c.regulation_mark = ANY(%s)
            LIMIT 1
            """,
            ((LOGIC_EXTRACTOR_VERSION,) if logic_ready else ()) + (card_id, _standard_marks()),
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
    except Exception as exc:
        print(f"[ai_tools] WARNING: _meta_from_embedding 降級為關鍵字檢索：{exc}", flush=True)
        return [_degraded_note(f"meta_semantic_failed:{type(exc).__name__}")]
    finally:
        conn.close()


def search_meta_decks(archetype_or_query: str, limit: int = META_LIMIT) -> list[dict[str, Any]]:
    query = str(archetype_or_query or "").strip()
    limit = max(1, min(int(limit or META_LIMIT), META_LIMIT))
    semantic, semantic_notes = _split_diagnostics(_meta_from_embedding(query, ["meta_deck"], limit))
    if semantic:
        return [_meta_reference_from_embedding(item) for item in semantic] + semantic_notes

    terms = _meta_query_terms(query)
    conn = database.get_db_connection()
    if not conn:
        return semantic_notes + [_degraded_note("db_unavailable")]
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


def search_japanese_decks_by_card(
    card_name: str,
    min_count: int = 1,
    limit: int = META_LIMIT,
    sort: str = "count",
) -> list[dict[str, Any]]:
    query = str(card_name or "").strip()
    if not query:
        return []
    min_count = max(1, min(int(min_count or 1), 60))
    limit = max(1, min(int(limit or META_LIMIT), META_LIMIT))
    sort = str(sort or "count").lower()
    order_clause = "matched_card_count DESC, d.deck_date DESC"
    if sort == "date":
        order_clause = "d.deck_date DESC, matched_card_count DESC"

    search = f"%{query.replace('%', '').replace('_', '')}%"
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            WITH matched_cards AS (
                SELECT deck_id, card_name, COALESCE(SUM(count), 0) AS card_count
                FROM deck_search_index
                WHERE card_name ILIKE %s
                GROUP BY deck_id, card_name
            ),
            matched_decks AS (
                SELECT deck_id,
                       COALESCE(SUM(card_count), 0) AS matched_card_count,
                       jsonb_agg(
                           jsonb_build_object('name', card_name, 'count', card_count)
                           ORDER BY card_count DESC, card_name
                       ) AS matched_cards
                FROM matched_cards
                GROUP BY deck_id
                HAVING COALESCE(SUM(card_count), 0) >= %s
            )
            SELECT d.deck_code, d.title, d.deck_date, d.image_url, d.tags,
                   matched_decks.matched_card_count, matched_decks.matched_cards
            FROM matched_decks
            JOIN imported_decks d ON d.id = matched_decks.deck_id
            ORDER BY {order_clause}
            LIMIT %s
            """,
            (search, min_count, limit),
        )
        results = []
        for row in cursor.fetchall():
            matched_cards = row.get("matched_cards") or []
            if isinstance(matched_cards, str):
                try:
                    matched_cards = json.loads(matched_cards)
                except Exception:
                    matched_cards = []
            tags = row.get("tags") or []
            if isinstance(tags, str):
                try:
                    tags = json.loads(tags)
                except Exception:
                    tags = []
            deck_date = row.get("deck_date")
            results.append({
                "source": "japanese",
                "code": row.get("deck_code"),
                "title": row.get("title"),
                "date": deck_date.isoformat() if hasattr(deck_date, "isoformat") else deck_date,
                "image": row.get("image_url") or "",
                "tags": tags if isinstance(tags, list) else [],
                "matched_card_count": int(row.get("matched_card_count") or 0),
                "matched_cards": matched_cards if isinstance(matched_cards, list) else [],
            })
        return results
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
        # embedding 路徑回傳 cards、關鍵字路徑回傳 matched_cards，
        # 兩者都讀，修復關鍵字路徑恆空的問題。
        for card in deck.get("matched_cards") or deck.get("cards") or []:
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
        if str(card.get("regulation_mark") or "").strip().upper() not in set(_standard_marks())
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
    results = _keyword_card_search(name, language, 5, {"standard_marks": _standard_marks(), "language": language})
    if not results and _is_basic_energy_name(name):
        results = _keyword_card_search_any_mark(name, language, 5)
    if not results:
        results = semantic_search_cards(name, 5, {"standard_marks": _standard_marks(), "language": language})
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
        logic_ready = _logic_columns_ready(cursor)
        select_sql = _select_columns_with_logic(table, "c") if logic_ready else _select_columns_without_logic(table, "c")
        logic_join = _logic_join_sql(language, "c") if logic_ready else ""
        cursor.execute(
            f"""
            SELECT {select_sql}
            FROM {table} c
            {logic_join}
            WHERE (c.name ILIKE %s OR COALESCE(c.description, '') ILIKE %s)
              AND c.card_type = 'Energy'
            ORDER BY CASE WHEN c.name = %s THEN 0 ELSE 1 END, c.card_id DESC
            LIMIT %s
            """,
            ((LOGIC_EXTRACTOR_VERSION,) if logic_ready else ())
            + (search, search, query, max(1, min(int(limit or 5), 10))),
        )
        return [_card_payload(row, folder_lang, True) for row in cursor.fetchall()]
    finally:
        conn.close()


def _resolve_deck_card(name: str, deck: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    """用現有牌組嘅卡名解析（牌組 entry 通常已經有 card_id）。

    同名但唔同 card_id 多過一個 → ambiguous，拒絕猜測。
    """
    cleaned = str(name or "").strip()
    matches = []
    for card in deck or []:
        if not isinstance(card, dict):
            continue
        card_name = str(card.get("name") or card.get("card_name") or "").strip()
        if card_name == cleaned:
            matches.append(card)
    if not matches:
        return None, "not_in_deck"
    ids = {str(card.get("card_id") or card.get("id") or "").strip() for card in matches}
    ids.discard("")
    if len(ids) > 1:
        return None, "ambiguous"
    if not ids:
        return dict(matches[0]), ""
    for card in matches:
        if str(card.get("card_id") or card.get("id") or "").strip() in ids:
            return dict(card), ""
    return dict(matches[0]), ""


def _resolve_card_exact(name: str, language: str = "tw", allow_any_mark: bool = False) -> tuple[dict[str, Any] | None, str]:
    """將卡名解析成唯一一張卡（card_id 級別），永不猜測。

    規則：
      1. 先精確同名或別名（japanese_name / chinese_name）。
      2. 精確命中多過一張 → (None, "ambiguous:<候選名>")，要求用戶消歧。
      3. 精確 0 張時做嚴格 LIKE，LIKE 命中必須唯一，否則一樣當 ambiguous。
      4. 完全搵唔到 → (None, "not_found")；DB 唔通 → (None, "db_unavailable")。
    """
    cleaned = str(name or "").strip()
    if not cleaned:
        return None, "not_found"
    language = language if language in ("tw", "jp") else "tw"
    table = "jp_cards" if language == "jp" else "cards"
    folder_lang = "jp" if language == "jp" else "tw"
    extra_name = "chinese_name" if language == "jp" else "japanese_name"

    conn = database.get_db_connection()
    if not conn:
        return None, "db_unavailable"
    try:
        cursor = conn.cursor()
        logic_ready = _logic_columns_ready(cursor)
        select_sql = _select_columns_with_logic(table, "c") if logic_ready else _select_columns_without_logic(table, "c")
        logic_join = _logic_join_sql(language, "c") if logic_ready else ""
        logic_params: list[Any] = [LOGIC_EXTRACTOR_VERSION] if logic_ready else []
        if allow_any_mark:
            mark_sql = ""
            mark_params: list[Any] = []
        else:
            mark_sql = "AND c.regulation_mark = ANY(%s)"
            mark_params = [_standard_marks()]

        def _fetch(where_sql: str, where_params: list[Any]) -> list[dict[str, Any]]:
            cursor.execute(
                f"""
                SELECT {select_sql}
                FROM {table} c
                {logic_join}
                WHERE TRUE
                  {mark_sql}
                  AND {where_sql}
                ORDER BY CASE WHEN c.name = %s THEN 0 ELSE 1 END, c.card_id
                LIMIT 20
                """,
                logic_params + mark_params + where_params + [cleaned],
            )
            return [_card_payload(row, folder_lang, True) for row in cursor.fetchall()]

        exact = _fetch(f"(c.name = %s OR COALESCE(c.{extra_name}, '') = %s)", [cleaned, cleaned])
        if len(exact) == 1:
            return exact[0], ""
        if len(exact) > 1:
            names = "|".join(sorted({str(card.get("name") or "") for card in exact}))
            return None, f"ambiguous:{names}"

        like = f"%{cleaned}%"
        loose = _fetch(f"(c.name ILIKE %s OR COALESCE(c.{extra_name}, '') ILIKE %s)", [like, like])
        if len(loose) == 1:
            return loose[0], ""
        if len(loose) > 1:
            names = "|".join(sorted({str(card.get("name") or "") for card in loose}))
            return None, f"ambiguous:{names}"
        return None, "not_found"
    except Exception as exc:
        print(f"[ai_tools] WARNING: _resolve_card_exact 失敗：{exc}", flush=True)
        try:
            conn.rollback()
        except Exception:
            pass
        return None, "db_error"
    finally:
        conn.close()


def _plan_legality(deck: list[dict[str, Any]], actions: list[dict[str, Any]]) -> dict[str, Any]:
    """基本合法性檢查：總張數 60、非基本能量卡最多 4 張。

    以 card_id 為主要 key（冇 card_id 就用名稱），同名但唔同 card_id 嘅重印卡
    唔會被錯誤合併計數。
    """
    projected: dict[str, dict[str, Any]] = {}

    def key_for(card_id: Any, name: Any) -> str:
        cid = str(card_id or "").strip()
        return f"id:{cid}" if cid else f"name:{str(name or '').strip()}"

    def bump(card_id: Any, name: Any, delta: int, card_type: str = "") -> None:
        key = key_for(card_id, name)
        entry = projected.setdefault(
            key,
            {
                "card_id": str(card_id or "").strip() or None,
                "name": str(name or "").strip(),
                "count": 0,
                "card_type": str(card_type or ""),
            },
        )
        entry["count"] += delta
        if card_type and not entry.get("card_type"):
            entry["card_type"] = str(card_type)

    for card in deck or []:
        if isinstance(card, dict):
            bump(card.get("card_id") or card.get("id"), card.get("name") or card.get("card_name"), 1, str(card.get("card_type") or ""))

    for action in actions or []:
        if not isinstance(action, dict):
            continue
        card = action.get("card") if isinstance(action.get("card"), dict) else {}
        count = max(0, int(action.get("count") or 0))
        card_id = action.get("card_id") or card.get("card_id") or card.get("id")
        name = action.get("card_name") or action.get("name") or card.get("name")
        card_type = str(card.get("card_type") or action.get("card_type") or "")
        if action.get("type") in ("remove_card", "remove"):
            # 同 build_deck_diff 一致：移除數量唔可以超過牌組實際擁有嘅數量。
            available = max(0, int(projected.get(key_for(card_id, name), {}).get("count") or 0))
            bump(card_id, name, -min(count, available), card_type)
        elif action.get("type") in ("add_card", "add"):
            bump(card_id, name, count, card_type)

    total = sum(max(0, int(entry.get("count") or 0)) for entry in projected.values())
    violations: list[dict[str, Any]] = []
    over_four: list[dict[str, Any]] = []
    for entry in projected.values():
        count = max(0, int(entry.get("count") or 0))
        if count <= 0:
            continue
        name = str(entry.get("name") or "")
        if count > 4 and not _is_basic_energy_name(name):
            item = {"name": name, "card_id": entry.get("card_id"), "count": count}
            over_four.append(item)
            violations.append({"type": "max_copies", **item})
    if total > 60:
        violations.append({"type": "over_60", "projected_total": total})
    return {
        "ok": not violations,
        "violations": violations,
        "current_total": len(deck or []),
        "projected_total": total,
        "total_ok": total == 60,
        "over_four": over_four,
    }


def propose_deck_patch(
    intent: str,
    deck: list[dict[str, Any]],
    retrieved_context: dict[str, Any] | None = None,
    language: str = "tw",
) -> dict[str, Any]:
    """由使用者指令產生確定性嘅牌組變更草案。

    所有被引用嘅卡都會經 DB / 牌組解析成真實 card_id；解析唔到就回 ok=False，
    絕對唔會再由 retrieved_context 亂咁砌 action（舊版 bug）。
    """
    intent = str(intent or "")
    deck = deck if isinstance(deck, list) else []
    language = language if language in ("tw", "jp") else "tw"
    actions: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []

    def _fail(error: str) -> dict[str, Any]:
        return {
            "ok": False,
            "reason": "無法解析指令",
            "error": error,
            "unresolved": unresolved,
            "deck_actions": [],
            "deck_diff": build_deck_diff(deck, []),
            "legality": _plan_legality(deck, []),
        }

    def _resolve_add(name: str) -> tuple[dict[str, Any] | None, str]:
        card, reason = _resolve_card_exact(name, language)
        if not card and _is_basic_energy_name(name):
            card, reason = _resolve_card_exact(name, language, allow_any_mark=True)
        return card, reason

    replace_match = re.search(r"(?:把|將)?\s*(\d+|一|二|兩|三|四)\s*張?(.+?)(?:換成|改成|替換成)(.+)", intent)
    if replace_match:
        count = _zh_int(replace_match.group(1))
        remove_name = _clean_card_name(replace_match.group(2))
        add_name = _clean_card_name(replace_match.group(3))
        if remove_name:
            card, reason = _resolve_deck_card(remove_name, deck)
            if not card and reason != "ambiguous":
                card, reason = _resolve_card_exact(remove_name, language)
            if not card:
                unresolved.append({"card_name": remove_name, "role": "remove", "reason": reason})
            else:
                actions.append({
                    "type": "remove_card",
                    "card_name": str(card.get("name") or remove_name),
                    "card_id": card.get("card_id"),
                    "count": count,
                    "card": card,
                })
        if add_name:
            card, reason = _resolve_add(add_name)
            if not card:
                unresolved.append({"card_name": add_name, "role": "add", "reason": reason})
            else:
                actions.append({
                    "type": "add_card",
                    "card_name": str(card.get("name") or add_name),
                    "card_id": card.get("card_id"),
                    "count": count,
                    "card": card,
                })

    fill_match = re.search(r"(?:補滿|補到|補齊).{0,8}(基本.+?能量|.+?基本能量|Basic .+? Energy)", intent, re.I)
    if fill_match:
        energy_name = _clean_card_name(fill_match.group(1))
        card, reason = _resolve_add(energy_name)
        if not card:
            unresolved.append({"card_name": energy_name, "role": "fill", "reason": reason})
        else:
            current = len(deck) + sum(a.get("count", 0) for a in actions if a.get("type") == "add_card") - sum(a.get("count", 0) for a in actions if a.get("type") == "remove_card")
            count = max(0, 60 - current)
            actions.append({
                "type": "add_card",
                "card_name": str(card.get("name") or energy_name),
                "card_id": card.get("card_id"),
                "count": count,
                "card": card,
                "reason": "fill_to_60",
            })

    if unresolved:
        return _fail("; ".join(f"{item['card_name']}:{item['reason']}" for item in unresolved))
    if not actions:
        return _fail("no_supported_intent")

    diff = build_deck_diff(deck, actions)
    legality = _plan_legality(deck, actions)
    if not legality.get("ok"):
        warnings = diff.setdefault("warnings", [])
        for violation in legality.get("violations") or []:
            if violation.get("type") == "max_copies":
                msg = f"{violation.get('name')} would have {violation.get('count')} copies (max 4)."
            elif violation.get("type") == "over_60":
                msg = f"Projected deck has {violation.get('projected_total')} cards, over the 60-card limit."
            else:
                msg = str(violation)
            if msg not in warnings:
                warnings.append(msg)
    return {"ok": True, "deck_actions": actions, "deck_diff": diff, "legality": legality}



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


def search_card_roles(
    role: str | None = None,
    params: dict[str, Any] | None = None,
    query: str | None = None,
    limit: int = CARD_LIMIT,
) -> list[dict[str, Any]]:
    """依效果角色查已人工批准（approved）的卡片。

    只查 approved 狀態——pending 標註不進入 Agent 視野。
    params 以 jsonb 包含（@>）比對，例如 {"source": "deck", "old_hand": "discard"}。
    """
    role = str(role or "").strip() or None
    query = str(query or "").strip() or None
    limit = max(1, min(int(limit or CARD_LIMIT), CARD_LIMIT))

    where = ["t.status = 'approved'"]
    params_list: list[Any] = []

    if role:
        where.append("t.role = %s")
        params_list.append(role)
    if query:
        like = f"%{query.replace('%', '').replace('_', '')}%"
        where.append("(c.name ILIKE %s OR COALESCE(c.japanese_name, '') ILIKE %s)")
        params_list.extend([like, like])
    if isinstance(params, dict) and params:
        filtered = {k: v for k, v in params.items() if k in FILTERABLE_PARAM_KEYS and v is not None}
        if filtered:
            where.append("t.params @> %s::jsonb")
            params_list.append(json.dumps(filtered, ensure_ascii=False))

    conn = database.get_db_connection()
    if not conn:
        return [_degraded_note("db_unavailable")]
    try:
        cursor = conn.cursor()
        cursor.execute(
            f"""
            SELECT t.card_id, t.role, t.params, t.evidence_span, t.confidence,
                   c.name, c.japanese_name, c.card_type, c.sub_type, c.hp,
                   c.element_type, c.set_code, c.set_number, c.set_name, c.regulation_mark
            FROM card_role_tags t
            JOIN cards c ON c.card_id = t.card_id
            WHERE {' AND '.join(where)}
            ORDER BY t.confidence DESC, t.id
            LIMIT %s
            """,
            params_list + [limit],
        )
        results = []
        for row in cursor.fetchall():
            tag_params = row.get("params") or {}
            if isinstance(tag_params, str):
                try:
                    tag_params = json.loads(tag_params)
                except Exception:
                    tag_params = {}
            results.append({
                "card_id": row.get("card_id"),
                "name": row.get("name"),
                "japanese_name": row.get("japanese_name"),
                "card_type": row.get("card_type"),
                "sub_type": row.get("sub_type"),
                "hp": row.get("hp"),
                "element_type": row.get("element_type"),
                "set_code": row.get("set_code"),
                "set_number": row.get("set_number"),
                "set_name": row.get("set_name"),
                "regulation_mark": row.get("regulation_mark"),
                "role": row.get("role"),
                "params": tag_params,
                "evidence_span": row.get("evidence_span"),
                "confidence": round(float(row.get("confidence") or 0), 3),
            })
        return results
    except Exception as exc:
        print(f"[ai_tools] WARNING: search_card_roles 失敗：{exc}", flush=True)
        return [_degraded_note(f"roles_failed:{type(exc).__name__}")]
    finally:
        conn.close()


# Compatibility helpers used by older assistant paths/tests.
def search_skill_keyword(keyword: str, language: str = "tw", limit: int = CARD_LIMIT, skill_type: str = "") -> list[dict[str, Any]]:
    return semantic_search_cards(keyword, limit, {"language": language, "standard_marks": _standard_marks()})


def search_skill_terms(terms: list[str], language: str = "tw", limit: int = CARD_LIMIT, skill_type: str = "") -> list[dict[str, Any]]:
    return semantic_search_cards(" ".join(terms or []), limit, {"language": language, "standard_marks": _standard_marks()})


def search_hand_size_damage(language: str = "tw", limit: int = CARD_LIMIT) -> list[dict[str, Any]]:
    return semantic_search_cards("依照手牌數量造成傷害", limit, {"language": language, "standard_marks": _standard_marks()})


def search_trainer_energy_attach(language: str = "tw", limit: int = CARD_LIMIT, subtypes: list[str] | None = None) -> list[dict[str, Any]]:
    query = "從手牌或牌庫附加能量的訓練家卡"
    cards = semantic_search_cards(query, limit, {"language": language, "standard_marks": _standard_marks()})
    if subtypes:
        wanted = {str(item) for item in subtypes}
        filtered = [card for card in cards if str(card.get("sub_type") or "") in wanted]
        return filtered or cards
    return cards
