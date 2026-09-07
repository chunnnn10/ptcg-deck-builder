"""30-day Limitless field stats, combo naming, and permanent monthly briefs."""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from typing import Any

import database


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS format_window_cache (
    cache_key TEXT PRIMARY KEY,
    payload JSONB NOT NULL,
    computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS format_archetype_briefs (
    combo_key TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    label_zh TEXT NOT NULL,
    first_month TEXT NOT NULL,
    last_seen_month TEXT NOT NULL,
    last_rank INTEGER,
    last_share REAL,
    last_n INTEGER,
    sample_deck_ids JSONB DEFAULT '[]'::jsonb,
    profile JSONB DEFAULT '{}'::jsonb,
    analysis JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_format_briefs_month ON format_archetype_briefs(last_seen_month);
"""

# Canonical family -> display names. Keys are matched after name normalization.
FAMILY_ALIASES: dict[str, tuple[str, str]] = {
    "dragapult": ("Dragapult", "多龍巴魯托"),
    "dreepy": ("Dragapult", "多龍巴魯托"),
    "drakloak": ("Dragapult", "多龍巴魯托"),
    "多龍巴魯托": ("Dragapult", "多龍巴魯托"),
    "多龍梅西亞": ("Dragapult", "多龍巴魯托"),
    "多龍奇": ("Dragapult", "多龍巴魯托"),
    "dudunsparce": ("Dudunsparce", "土龍節節"),
    "dunsparce": ("Dudunsparce", "土龍節節"),
    "土龍節節": ("Dudunsparce", "土龍節節"),
    "土龍弟弟": ("Dudunsparce", "土龍節節"),
    "dusknoir": ("Dusknoir", "黑夜魔靈"),
    "dusclops": ("Dusknoir", "黑夜魔靈"),
    "duskull": ("Dusknoir", "黑夜魔靈"),
    "黑夜魔靈": ("Dusknoir", "黑夜魔靈"),
    "夜巡靈": ("Dusknoir", "黑夜魔靈"),
    "blaziken": ("Blaziken", "火焰雞"),
    "combusken": ("Blaziken", "火焰雞"),
    "torchic": ("Blaziken", "火焰雞"),
    "火焰雞": ("Blaziken", "火焰雞"),
    "zoroark": ("N Zoroark", "N的索羅亞克"),
    "zorua": ("N Zoroark", "N的索羅亞克"),
    "索羅亞克": ("N Zoroark", "N的索羅亞克"),
    "索羅亞": ("N Zoroark", "N的索羅亞克"),
    "mewtwo": ("Rocket Mewtwo", "火箭隊的超夢"),
    "超夢": ("Rocket Mewtwo", "火箭隊的超夢"),
    "alakazam": ("Alakazam", "胡地"),
    "kadabra": ("Alakazam", "胡地"),
    "abra": ("Alakazam", "胡地"),
    "胡地": ("Alakazam", "胡地"),
    "凱西": ("Alakazam", "胡地"),
    "凯西": ("Alakazam", "胡地"),
    "勇基拉": ("Alakazam", "胡地"),
    "lucario": ("Lucario", "路卡利歐"),
    "riolu": ("Lucario", "路卡利歐"),
    "路卡利歐": ("Lucario", "路卡利歐"),
    "greninja": ("Greninja", "甲賀忍蛙"),
    "frogadier": ("Greninja", "甲賀忍蛙"),
    "froakie": ("Greninja", "甲賀忍蛙"),
    "甲賀忍蛙": ("Greninja", "甲賀忍蛙"),
    "忍蛙": ("Greninja", "甲賀忍蛙"),
    "水忍蛙": ("Greninja", "甲賀忍蛙"),
    "froslass": ("Froslass", "雪妖女"),
    "snorunt": ("Froslass", "雪妖女"),
    "雪妖女": ("Froslass", "雪妖女"),
    "雪童子": ("Froslass", "雪妖女"),
    "garchomp": ("Garchomp", "烈咬陸鯊"),
    "gabite": ("Garchomp", "烈咬陸鯊"),
    "gible": ("Garchomp", "烈咬陸鯊"),
    "烈咬陸鯊": ("Garchomp", "烈咬陸鯊"),
    "roserade": ("Roserade", "羅絲雷朵"),
    "roselia": ("Roserade", "羅絲雷朵"),
    "budew": ("Roserade", "羅絲雷朵"),
    "羅絲雷朵": ("Roserade", "羅絲雷朵"),
    "charizard": ("Charizard", "噴火龍"),
    "charmeleon": ("Charizard", "噴火龍"),
    "charmander": ("Charizard", "噴火龍"),
    "噴火龍": ("Charizard", "噴火龍"),
    "gardevoir": ("Gardevoir", "沙奈朵"),
    "kirlia": ("Gardevoir", "沙奈朵"),
    "ralts": ("Gardevoir", "沙奈朵"),
    "沙奈朵": ("Gardevoir", "沙奈朵"),
    "tinkaton": ("Tinkaton", "巨鍛匠"),
    "tinkatuff": ("Tinkaton", "巨鍛匠"),
    "tinkatink": ("Tinkaton", "巨鍛匠"),
    "巨鍛匠": ("Tinkaton", "巨鍛匠"),
    "pidgeot": ("Pidgeot", "大比鳥"),
    "pidgeotto": ("Pidgeot", "大比鳥"),
    "pidgey": ("Pidgeot", "大比鳥"),
    "大比鳥": ("Pidgeot", "大比鳥"),
    "wellspring": ("Wellspring Mask", "水瀾之假面"),
    "水瀾": ("Wellspring Mask", "水瀾之假面"),
    "ogerpon": ("Ogerpon", "奧利瓦"),
    "奧利瓦": ("Ogerpon", "奧利瓦"),
    "joltik": ("Joltik", "電蜘蛛"),
    "galvantula": ("Joltik", "電蜘蛛"),
    "電蜘蛛": ("Joltik", "電蜘蛛"),
    "banette": ("Banette", "詛咒娃娃"),
    "shuppet": ("Banette", "詛咒娃娃"),
    "詛咒娃娃": ("Banette", "詛咒娃娃"),
    "garganacl": ("Garganacl", "岩殿居蟹"),
    "naclstack": ("Garganacl", "岩殿居蟹"),
    "nacli": ("Garganacl", "岩殿居蟹"),
    "岩殿居蟹": ("Garganacl", "岩殿居蟹"),
}

STAPLE_FAMILIES = {
    "fezandipiti", "喵喵",
    "shaymin", "含羞苞",
    "oranguru", "願增猿",
    "gholdengo", "賽富豪", "吉隆戈",
    "munkidori", "謀智靈",
    "okidogi", "夠讚狗",
    "bloodmoon", "赫月",
    "pecharunt", "桃歹郎",
    "budew",  # already mapped roserade if full line; lone budew is staple sometimes
}

_CACHE_TTL_MINUTES = 20


def ensure_schema(conn=None) -> None:
    owns = conn is None
    conn = conn or database.get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute(SCHEMA_SQL)
        if owns:
            conn.commit()
    except Exception:
        if owns:
            conn.rollback()
        raise
    finally:
        if owns and conn:
            conn.close()


def _norm(name: str) -> str:
    text = str(name or "")
    text = re.sub(r"[\'’].+$", "", text)
    text = re.sub(r"(ex|EX|VSTAR|VMAX|V-UNION|V|Mega|MEGA|太晶|輝耀)", "", text)
    text = re.sub(r"[的の]", "", text)
    text = re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text)
    return text.strip().lower()


def _family_of(name: str) -> tuple[str, str, str] | None:
    raw = str(name or "").strip()
    if not raw:
        return None
    key = _norm(raw)
    if not key:
        return None
    if key in FAMILY_ALIASES:
        en, zh = FAMILY_ALIASES[key]
        return en.lower().replace(" ", "_"), en, zh
    for alias, pair in FAMILY_ALIASES.items():
        if alias in key or key in alias:
            en, zh = pair
            return en.lower().replace(" ", "_"), en, zh
    return key, raw, raw


def _is_staple(family_id: str, display_zh: str) -> bool:
    blob = f"{family_id} {display_zh}".lower()
    return any(token in blob for token in STAPLE_FAMILIES)


def classify_pokemon_lines(cards: list[dict[str, Any]]) -> dict[str, Any]:
    families: dict[str, dict[str, Any]] = {}
    for card in cards:
        if not isinstance(card, dict):
            continue
        name = str(card.get("tw_name") or card.get("card_name") or card.get("name") or "").strip()
        fam = _family_of(name)
        if not fam:
            continue
        fid, en, zh = fam
        try:
            count = max(1, int(card.get("count") or 1))
        except Exception:
            count = 1
        item = families.setdefault(fid, {"id": fid, "en": en, "zh": zh, "count": 0, "staple": _is_staple(fid, zh), "names": []})
        item["count"] += count
        if name and name not in item["names"]:
            item["names"].append(name)
    ranked = sorted(families.values(), key=lambda row: (-row["count"], row["zh"]))
    partners = [row for row in ranked if not row["staple"] and row["count"] >= 2]
    if not partners:
        partners = [row for row in ranked if not row["staple"]][:1]
    primary = partners[0] if partners else (ranked[0] if ranked else None)
    extras = [row for row in partners[1:] if row["count"] >= 2][:2]
    if primary:
        parts_en = [primary["en"]] + [row["en"] for row in extras]
        parts_zh = [primary["zh"]] + [row["zh"] for row in extras]
        if extras:
            label_en = " ".join(parts_en)
            label_zh = " ".join(parts_zh)
        else:
            label_en = f"Pure {primary['en']}"
            label_zh = f"純{primary['zh']}"
        combo_key = "+".join([primary["id"]] + [row["id"] for row in extras])
    else:
        label_en, label_zh, combo_key = "Unknown", "未分類", "unknown"
    return {
        "combo_key": combo_key,
        "label": label_en,
        "label_zh": label_zh,
        "primary": primary,
        "partners": extras,
        "families": ranked,
    }


def _window_bounds(days: int) -> tuple[date, date]:
    days = max(7, min(int(days or 30), 120))
    end = date.today()
    start = end - timedelta(days=days)
    return start, end


def _fetch_window_rows(cursor, start: date, end: date, fmt: str = "standard") -> list[dict[str, Any]]:
    cursor.execute(
        """
        SELECT d.deck_id, d.archetype, d.title, d.placement, d.player_name,
               t.tournament_id, t.title AS tournament_title, t.date, t.players,
               t.location, t.format, t.source_region
        FROM limitless_decks d
        JOIN limitless_tournaments t ON t.tournament_id = d.tournament_id
        WHERE t.date BETWEEN %s AND %s
          AND (
                %s = ''
                OR COALESCE(t.format, '') ILIKE %s
                OR COALESCE(t.format, '') = ''
              )
        ORDER BY t.date DESC, COALESCE(t.players, 0) DESC
        """,
        (start, end, fmt, f"%{fmt}%"),
    )
    return list(cursor.fetchall() or [])


def _fetch_pokemon_cards(cursor, deck_ids: list[str]) -> dict[str, list[dict[str, Any]]]:
    if not deck_ids:
        return {}
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    chunk = 400
    for i in range(0, len(deck_ids), chunk):
        part = deck_ids[i:i + chunk]
        cursor.execute(
            """
            SELECT c.deck_id, c.card_name, c.count, c.local_tw_card_id, c.local_jp_card_id,
                   tw.name AS tw_name, tw.hp AS tw_hp, tw.sub_type AS tw_sub_type,
                   tw.skills_json AS tw_skills, tw.regulation_mark AS tw_mark,
                   jp.name AS jp_name, jp.hp AS jp_hp, jp.sub_type AS jp_sub_type,
                   jp.skills_json AS jp_skills
            FROM limitless_deck_cards c
            LEFT JOIN cards tw ON tw.card_id = c.local_tw_card_id
            LEFT JOIN jp_cards jp ON jp.card_id = c.local_jp_card_id
            WHERE c.language = 'jp' AND c.mode = 'normal' AND c.section = 'pokemon'
              AND c.deck_id = ANY(%s)
            """,
            (part,),
        )
        for row in cursor.fetchall() or []:
            grouped[str(row.get("deck_id"))].append(row)
    return grouped


def _parse_damage(value: Any) -> int | None:
    text = str(value or "").strip()
    match = re.search(r"(\d{2,4})", text)
    if not match:
        return None
    return int(match.group(1))


def _parse_skills(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, dict):
        return [item for item in (value.get("skills") or value.get("attacks") or []) if isinstance(item, dict)]
    try:
        return _parse_skills(json.loads(value))
    except Exception:
        return []


def _printed_lines(card: dict[str, Any]) -> list[dict[str, Any]]:
    skills = _parse_skills(card.get("tw_skills") or card.get("jp_skills"))
    lines = []
    for skill in skills:
        damage = _parse_damage(skill.get("damage"))
        effect = str(skill.get("effect") or skill.get("text") or skill.get("description") or "")
        kind = "ability" if str(skill.get("type") or skill.get("category") or "").lower() in ("ability", "特性") or skill.get("isAbility") else "attack"
        if damage is None and not effect:
            continue
        lines.append({
            "kind": kind,
            "name": skill.get("name") or "",
            "damage": damage,
            "cost": skill.get("cost") or [],
            "condition": effect[:240],
            "target": "bench" if "備戰" in effect or "bench" in effect.lower() else "active",
        })
    return lines


def _profile_from_cards(cards: list[dict[str, Any]], combo: dict[str, Any]) -> dict[str, Any]:
    attackers = []
    hp_table = []
    seen = set()
    for card in cards:
        name = str(card.get("tw_name") or card.get("card_name") or "").strip()
        if not name:
            continue
        key = name
        if key in seen:
            continue
        seen.add(key)
        try:
            hp = int(card.get("tw_hp") or card.get("jp_hp") or 0)
        except Exception:
            hp = 0
        lines = _printed_lines(card)
        if hp:
            hp_table.append({"name": name, "hp": hp, "card_id": card.get("local_tw_card_id") or card.get("local_jp_card_id")})
        if lines:
            attackers.append({
                "name": name,
                "card_id": card.get("local_tw_card_id") or card.get("local_jp_card_id"),
                "hp": hp or None,
                "sub_type": card.get("tw_sub_type") or card.get("jp_sub_type"),
                "printed_lines": lines,
            })
    return {
        "combo_key": combo.get("combo_key"),
        "label": combo.get("label"),
        "label_zh": combo.get("label_zh"),
        "families": [
            {"id": row["id"], "en": row["en"], "zh": row["zh"], "count": row["count"], "staple": row["staple"]}
            for row in combo.get("families") or []
        ],
        "attackers": attackers[:12],
        "hp_table": hp_table[:16],
    }


def classify_deck_cards(cards: list[dict[str, Any]]) -> dict[str, Any]:
    return classify_pokemon_lines(cards)


def get_deck_combo(deck_id: str) -> dict[str, Any] | None:
    conn = database.get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        grouped = _fetch_pokemon_cards(cursor, [str(deck_id)])
        cards = grouped.get(str(deck_id)) or []
        if not cards:
            return None
        combo = classify_pokemon_lines(cards)
        combo["profile"] = _profile_from_cards(cards, combo)
        return combo
    finally:
        conn.close()


def _official_label(archetype: str | None) -> str:
    text = str(archetype or "").strip() or "Unknown"
    return text


def build_field_stats(days: int = 30, fmt: str = "standard", use_cache: bool = True) -> dict[str, Any]:
    ensure_schema()
    start, end = _window_bounds(days)
    cache_key = f"field:{start}:{end}:{fmt}"
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "database unavailable"}
    try:
        cursor = conn.cursor()
        if use_cache:
            cursor.execute(
                """
                SELECT payload, computed_at FROM format_window_cache
                WHERE cache_key = %s AND computed_at > NOW() - (%s || ' minutes')::interval
                """,
                (cache_key, _CACHE_TTL_MINUTES),
            )
            cached = cursor.fetchone()
            if cached and cached.get("payload"):
                payload = cached["payload"]
                if isinstance(payload, str):
                    payload = json.loads(payload)
                payload["cached"] = True
                return payload

        rows = _fetch_window_rows(cursor, start, end, fmt)
        deck_ids = [str(row.get("deck_id")) for row in rows if row.get("deck_id")]
        pokemon_map = _fetch_pokemon_cards(cursor, deck_ids)

        combo_counter: Counter[str] = Counter()
        combo_meta: dict[str, dict[str, Any]] = {}
        official_counter: Counter[str] = Counter()
        combo_events: dict[str, set[str]] = defaultdict(set)
        combo_players: dict[str, int] = defaultdict(int)

        for row in rows:
            deck_id = str(row.get("deck_id"))
            official = _official_label(row.get("archetype") or row.get("title"))
            official_counter[official] += 1
            cards = pokemon_map.get(deck_id) or []
            combo = classify_pokemon_lines(cards) if cards else {
                "combo_key": f"title:{_norm(official) or 'unknown'}",
                "label": official,
                "label_zh": official,
                "families": [],
            }
            key = combo["combo_key"]
            combo_counter[key] += 1
            combo_events[key].add(str(row.get("tournament_id") or ""))
            try:
                combo_players[key] += int(row.get("players") or 0)
            except Exception:
                pass
            meta = combo_meta.setdefault(key, {
                "combo_key": key,
                "label": combo.get("label"),
                "label_zh": combo.get("label_zh"),
                "sample_deck_id": deck_id,
                "official_titles": Counter(),
            })
            meta["official_titles"][official] += 1

        total = max(1, len(rows))
        combinations = []
        for key, n in combo_counter.most_common():
            meta = combo_meta[key]
            official_titles = meta["official_titles"].most_common(4)
            combinations.append({
                "combo_key": key,
                "label": meta["label"],
                "label_zh": meta["label_zh"],
                "n": n,
                "share": round(n / total, 4),
                "share_pct": f"{(n / total) * 100:.2f}%",
                "events": len([item for item in combo_events[key] if item]),
                "players_touched": combo_players[key],
                "sample_deck_id": meta["sample_deck_id"],
                "official_titles": [{"name": name, "n": count} for name, count in official_titles],
                "has_brief": False,
            })

        cursor.execute(
            "SELECT combo_key FROM format_archetype_briefs WHERE combo_key = ANY(%s)",
            ([row["combo_key"] for row in combinations[:80]] or ["__none__"],),
        )
        existing = {row["combo_key"] for row in cursor.fetchall() or []}
        for row in combinations:
            row["has_brief"] = row["combo_key"] in existing

        tournaments: dict[str, dict[str, Any]] = {}
        for row in rows:
            tid = str(row.get("tournament_id") or "")
            if not tid:
                continue
            item = tournaments.setdefault(tid, {
                "tournament_id": tid,
                "title": row.get("tournament_title"),
                "date": row.get("date").isoformat() if hasattr(row.get("date"), "isoformat") else row.get("date"),
                "players": row.get("players") or 0,
                "location": row.get("location"),
                "source_region": row.get("source_region"),
                "decks": 0,
            })
            item["decks"] += 1

        t_list = sorted(tournaments.values(), key=lambda item: (item.get("date") or "", int(item.get("players") or 0)), reverse=True)
        player_values = [int(item.get("players") or 0) for item in t_list if item.get("players")]
        payload = {
            "success": True,
            "cached": False,
            "window": {"from": start.isoformat(), "to": end.isoformat(), "days": (end - start).days, "format": fmt},
            "tournaments": {
                "count": len(t_list),
                "total_players": sum(player_values),
                "median_players": sorted(player_values)[len(player_values) // 2] if player_values else 0,
                "largest": t_list[0] if t_list else None,
                "items": t_list[:40],
            },
            "decks": len(rows),
            "combinations": combinations,
            "official_titles": [
                {"name": name, "n": n, "share": round(n / total, 4), "share_pct": f"{(n / total) * 100:.2f}%"}
                for name, n in official_counter.most_common(30)
            ],
            "briefs": {
                "stored": len(existing),
                "top20_new": sum(1 for row in combinations[:20] if not row["has_brief"]),
            },
        }
        cursor.execute(
            """
            INSERT INTO format_window_cache (cache_key, payload, computed_at)
            VALUES (%s, %s::jsonb, CURRENT_TIMESTAMP)
            ON CONFLICT (cache_key) DO UPDATE
              SET payload = EXCLUDED.payload, computed_at = CURRENT_TIMESTAMP
            """,
            (cache_key, json.dumps(payload, ensure_ascii=False, default=str)),
        )
        conn.commit()
        return payload
    except Exception as exc:
        conn.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def list_briefs() -> dict[str, Any]:
    ensure_schema()
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "database unavailable", "briefs": []}
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT combo_key, label, label_zh, first_month, last_seen_month,
                   last_rank, last_share, last_n, sample_deck_ids, profile, analysis,
                   created_at, updated_at
            FROM format_archetype_briefs
            ORDER BY last_seen_month DESC, last_rank NULLS LAST, label_zh
            """
        )
        rows = []
        for row in cursor.fetchall() or []:
            item = dict(row)
            for key in ("created_at", "updated_at"):
                if hasattr(item.get(key), "isoformat"):
                    item[key] = item[key].isoformat()
            rows.append(item)
        return {"success": True, "count": len(rows), "briefs": rows}
    finally:
        conn.close()


def get_brief(combo_key: str) -> dict[str, Any]:
    ensure_schema()
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "database unavailable"}
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM format_archetype_briefs WHERE combo_key = %s", (combo_key,))
        row = cursor.fetchone()
        if not row:
            return {"success": False, "error": "brief not found"}
        item = dict(row)
        for key in ("created_at", "updated_at"):
            if hasattr(item.get(key), "isoformat"):
                item[key] = item[key].isoformat()
        return {"success": True, "brief": item}
    finally:
        conn.close()



def _serialize_brief_row(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    for key in ("created_at", "updated_at"):
        if hasattr(item.get(key), "isoformat"):
            item[key] = item[key].isoformat()
    return item


def update_brief(combo_key: str, analysis: dict[str, Any] | None = None, label_zh: str | None = None, note: str = "") -> dict[str, Any]:
    """Create or overwrite a stored brief analysis. Manual edits win."""
    ensure_schema()
    key = str(combo_key or "").strip()
    if not key:
        return {"success": False, "error": "missing combo_key"}
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "database unavailable"}
    month = date.today().strftime("%Y-%m")
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM format_archetype_briefs WHERE combo_key = %s", (key,))
        existing = cursor.fetchone()
        payload = analysis if isinstance(analysis, dict) else {}
        if existing:
            current = existing.get("analysis") if isinstance(existing.get("analysis"), dict) else {}
            if isinstance(existing.get("analysis"), str):
                try:
                    current = json.loads(existing["analysis"])
                except Exception:
                    current = {}
            merged = dict(current)
            merged.update(payload)
            revisions = list(merged.get("revisions") or [])
            if note:
                revisions.append({"at": datetime.utcnow().isoformat(timespec="seconds") + "Z", "note": note, "source": "manual"})
            merged["revisions"] = revisions[-20:]
            merged["verified"] = True
            cursor.execute(
                """
                UPDATE format_archetype_briefs
                SET analysis = %s::jsonb,
                    label_zh = COALESCE(NULLIF(%s, ''), label_zh),
                    updated_at = CURRENT_TIMESTAMP
                WHERE combo_key = %s
                RETURNING *
                """,
                (json.dumps(merged, ensure_ascii=False, default=str), str(label_zh or "").strip(), key),
            )
        else:
            payload = dict(payload)
            payload.setdefault("verified", True)
            payload["revisions"] = [{"at": datetime.utcnow().isoformat(timespec="seconds") + "Z", "note": note or "manual create", "source": "manual"}]
            cursor.execute(
                """
                INSERT INTO format_archetype_briefs (
                    combo_key, label, label_zh, first_month, last_seen_month, analysis
                ) VALUES (%s, %s, %s, %s, %s, %s::jsonb)
                RETURNING *
                """,
                (key, label_zh or key, label_zh or key, month, month, json.dumps(payload, ensure_ascii=False, default=str)),
            )
        row = cursor.fetchone()
        conn.commit()
        return {"success": True, "brief": _serialize_brief_row(row)}
    except Exception as exc:
        conn.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def _message_text(message: Any) -> str:
    if isinstance(message, str):
        return message
    if not isinstance(message, dict):
        return str(message or "")
    content = message.get("content")
    if isinstance(content, list):
        parts = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or part.get("content") or ""))
            else:
                parts.append(str(part))
        content = "\n".join(parts)
    chunks = [content, message.get("reasoning_content"), message.get("reasoning")]
    return "\n".join(str(chunk) for chunk in chunks if chunk)


def _extract_json_object(text: str) -> dict[str, Any] | None:
    raw = str(text or "").strip()
    if not raw:
        return None
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
        raw = re.sub(r"```$", "", raw).strip()
    candidates = [raw]
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start:end + 1])
    for item in candidates:
        try:
            parsed = json.loads(item)
        except Exception:
            continue
        if isinstance(parsed, dict) and parsed:
            if isinstance(parsed.get("analysis"), dict):
                return parsed["analysis"]
            return parsed
    return None


def revise_brief_with_ai(combo_key: str, message: str) -> dict[str, Any]:
    """Ask the chat model to propose an analysis patch, then save it."""
    from services.ai_assistant.client import AIClientError, AIConfigError, chat_message

    current = get_brief(combo_key)
    brief = current.get("brief") if current.get("success") else {"combo_key": combo_key, "analysis": {}, "label_zh": combo_key}
    analysis = brief.get("analysis") if isinstance(brief.get("analysis"), dict) else {}
    prompt = [
        {
            "role": "system",
            "content": (
                "你係 PTCG Format Analyst 編輯助手。用戶會指出分析錯邊。"
                "回一個 JSON 物件，可以包 markdown 都可以，但一定要有呢幾欄："
                "tempo_note, quirks (string array), combo_lines (string array), note, reply。"
                "combo_lines 只保留用戶話仍然有效嘅打點；quirks 寫血線同版本差。"
                "唔准發明新傷害數字。reply 用繁中一句總結你改咗咩。"
            ),
        },
        {
            "role": "user",
            "content": json.dumps({
                "instruction": message,
                "combo_key": brief.get("combo_key") or combo_key,
                "label_zh": brief.get("label_zh"),
                "profile": brief.get("profile") or {},
                "analysis": analysis,
            }, ensure_ascii=False, default=str),
        },
    ]
    raw_text = ""
    last_error = ""
    for use_format in (True, False):
        try:
            msg = chat_message(
                prompt,
                temperature=0.2,
                response_format={"type": "json_object"} if use_format else None,
            )
            raw_text = _message_text(msg)
            parsed = _extract_json_object(raw_text)
            if parsed:
                break
        except (AIConfigError, AIClientError) as exc:
            last_error = str(exc)
            parsed = None
            if "json" not in last_error.lower() and use_format:
                continue
            if not use_format:
                return {"success": False, "error": last_error}
    else:
        parsed = None

    if not parsed:
        # Keep the user's correction instead of dying on format.
        parsed = {
            "tempo_note": analysis.get("tempo_note") or "",
            "quirks": analysis.get("quirks") or [],
            "combo_lines": analysis.get("combo_lines") or [],
            "note": message,
            "reply": "模型無出到完整 JSON，已把你嘅指示寫入備註，請再檢查傷害線。",
        }
        if last_error:
            parsed["reply"] += f"（{last_error[:120]}）"

    reply = str(parsed.get("reply") or parsed.get("note") or "已按你嘅指示更新分析")
    parsed["reply"] = reply
    saved = update_brief(combo_key, analysis=parsed, label_zh=brief.get("label_zh"), note=f"ai: {message[:180]}")
    saved["proposal"] = parsed
    saved["reply"] = reply
    saved["raw"] = raw_text[:500]
    return saved


def run_monthly_briefs(days: int = 30, quota: int = 20, fmt: str = "standard") -> dict[str, Any]:
    """Fill up to `quota` NEW combo briefs from current window ranking. Never re-analyze stored keys."""
    ensure_schema()
    field = build_field_stats(days=days, fmt=fmt, use_cache=False)
    if not field.get("success"):
        return field
    month = date.today().strftime("%Y-%m")
    combinations = field.get("combinations") or []
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "database unavailable"}
    created = []
    skipped = []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT combo_key FROM format_archetype_briefs")
        existing = {row["combo_key"] for row in cursor.fetchall() or []}
        slots = max(1, min(int(quota or 20), 20))
        for rank, combo in enumerate(combinations, start=1):
            key = combo["combo_key"]
            if key in existing:
                cursor.execute(
                    """
                    UPDATE format_archetype_briefs
                    SET last_seen_month = %s, last_rank = %s, last_share = %s, last_n = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE combo_key = %s
                    """,
                    (month, rank, combo.get("share"), combo.get("n"), key),
                )
                skipped.append({"combo_key": key, "label_zh": combo.get("label_zh"), "reason": "already_analyzed", "rank": rank})
                continue
            if len(created) >= slots:
                continue
            sample_id = combo.get("sample_deck_id")
            grouped = _fetch_pokemon_cards(cursor, [sample_id] if sample_id else [])
            cards = grouped.get(str(sample_id) or "") or []
            classified = classify_pokemon_lines(cards) if cards else combo
            profile = _profile_from_cards(cards, classified)
            analysis = {
                "source": "printed_lines",
                "tempo_note": None,
                "combo_lines": [
                    {
                        "card": attacker.get("name"),
                        "line": line.get("name"),
                        "damage": line.get("damage"),
                        "kind": line.get("kind"),
                    }
                    for attacker in profile.get("attackers") or []
                    for line in attacker.get("printed_lines") or []
                    if line.get("damage")
                ],
                "verified": False,
                "note": "自動由卡文印刷傷害抽出；組合斬殺線尚未人工／LLM 註解。",
            }
            cursor.execute(
                """
                INSERT INTO format_archetype_briefs (
                    combo_key, label, label_zh, first_month, last_seen_month,
                    last_rank, last_share, last_n, sample_deck_ids, profile, analysis
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb, %s::jsonb)
                """,
                (
                    key, combo.get("label"), combo.get("label_zh"), month, month,
                    rank, combo.get("share"), combo.get("n"),
                    json.dumps([sample_id] if sample_id else [], ensure_ascii=False),
                    json.dumps(profile, ensure_ascii=False, default=str),
                    json.dumps(analysis, ensure_ascii=False, default=str),
                ),
            )
            created.append({"combo_key": key, "label_zh": combo.get("label_zh"), "rank": rank, "share_pct": combo.get("share_pct")})
            existing.add(key)
        conn.commit()
        return {
            "success": True,
            "month": month,
            "window": field.get("window"),
            "quota": slots,
            "created": created,
            "skipped_existing": skipped[:40],
            "created_count": len(created),
            "skipped_count": len(skipped),
        }
    except Exception as exc:
        conn.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()


def reset_meta_data() -> dict[str, Any]:
    """Wipe cached field stats and all stored briefs. Limitless decklists stay."""
    ensure_schema()
    conn = database.get_db_connection()
    if not conn:
        return {"success": False, "error": "database unavailable"}
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) AS n FROM format_archetype_briefs")
        briefs = int((cursor.fetchone() or {}).get("n") or 0)
        cursor.execute("SELECT COUNT(*) AS n FROM format_window_cache")
        caches = int((cursor.fetchone() or {}).get("n") or 0)
        cursor.execute("TRUNCATE format_archetype_briefs")
        cursor.execute("TRUNCATE format_window_cache")
        conn.commit()
        return {
            "success": True,
            "deleted_briefs": briefs,
            "deleted_caches": caches,
            "message": f"已清空 {briefs} 份 brief 同 {caches} 份統計快取。Limitless 牌表未刪。",
        }
    except Exception as exc:
        conn.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()
