"""30-day Limitless field stats, combo naming, and permanent monthly briefs."""

from __future__ import annotations

import json
import re
import threading
import time
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
                   tw.description AS tw_description,
                   jp.name AS jp_name, jp.hp AS jp_hp, jp.sub_type AS jp_sub_type,
                   jp.skills_json AS jp_skills, jp.description AS jp_description
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


def _format_cost(cost: Any) -> str:
    if isinstance(cost, list):
        parts = [str(item).strip() for item in cost if str(item).strip()]
        return "".join(parts)
    return str(cost or "").strip()


def _skill_kind(skill: dict[str, Any]) -> str:
    blob = " ".join(str(skill.get(key) or "") for key in ("type", "category", "name")).lower()
    if skill.get("isAbility") or "ability" in blob or "特性" in blob:
        return "ability"
    return "attack"


def _normalize_skill(skill: dict[str, Any]) -> dict[str, Any] | None:
    name = str(skill.get("name") or skill.get("ability_name") or "").strip()
    effect = str(skill.get("effect") or skill.get("text") or skill.get("description") or "").strip()
    damage_raw = str(skill.get("damage") or "").strip()
    kind = _skill_kind(skill)
    if not name and not effect and not damage_raw:
        return None
    return {
        "kind": kind,
        "name": name,
        "damage": damage_raw,
        "damage_value": _parse_damage(damage_raw),
        "cost": skill.get("cost") or [],
        "cost_text": _format_cost(skill.get("cost")),
        "effect": effect,
        "target": "bench" if ("備戰" in effect or "bench" in effect.lower()) else "active",
    }


def _card_kit(card: dict[str, Any]) -> dict[str, Any]:
    skills = [_normalize_skill(skill) for skill in _parse_skills(card.get("tw_skills") or card.get("jp_skills"))]
    skills = [skill for skill in skills if skill]
    name = str(card.get("tw_name") or card.get("card_name") or "").strip()
    try:
        hp = int(card.get("tw_hp") or card.get("jp_hp") or 0)
    except Exception:
        hp = 0
    try:
        count = int(card.get("count") or 1)
    except Exception:
        count = 1
    abilities = [skill for skill in skills if skill["kind"] == "ability"]
    attacks = [skill for skill in skills if skill["kind"] != "ability"]
    return {
        "name": name,
        "card_id": card.get("local_tw_card_id") or card.get("local_jp_card_id"),
        "hp": hp or None,
        "count": count,
        "sub_type": card.get("tw_sub_type") or card.get("jp_sub_type"),
        "description": str(card.get("tw_description") or card.get("jp_description") or "")[:400],
        "abilities": abilities,
        "attacks": attacks,
        "printed_lines": skills,
    }


def _catalog_lines(kit: dict[str, Any]) -> list[str]:
    header = kit.get("name") or "未命名"
    extras = []
    if kit.get("hp"):
        extras.append(f"HP{kit['hp']}")
    if kit.get("sub_type"):
        extras.append(str(kit["sub_type"]))
    if kit.get("count"):
        extras.append(f"x{kit['count']}")
    lines = [header + (("　" + " / ".join(extras)) if extras else "")]
    if not kit.get("abilities") and not kit.get("attacks"):
        if kit.get("description"):
            lines.append(f"  卡文：{kit['description']}")
        else:
            lines.append("  （未綁到中文／日文卡文）")
        return lines
    for idx, skill in enumerate(kit.get("abilities") or [], start=1):
        text = f"  特性{idx} {skill.get('name') or ''}".rstrip()
        if skill.get("effect"):
            text += f"：{skill['effect']}"
        lines.append(text)
    for idx, skill in enumerate(kit.get("attacks") or [], start=1):
        bits = [f"  招式{idx}"]
        if skill.get("name"):
            bits.append(str(skill["name"]))
        if skill.get("cost_text"):
            bits.append(f"費用{skill['cost_text']}")
        if skill.get("damage"):
            bits.append(f"傷害{skill['damage']}")
        text = " ".join(bits)
        if skill.get("effect"):
            text += f"：{skill['effect']}"
        lines.append(text)
    return lines


def _printed_lines(card: dict[str, Any]) -> list[dict[str, Any]]:
    return _card_kit(card).get("printed_lines") or []


def _profile_from_cards(cards: list[dict[str, Any]], combo: dict[str, Any]) -> dict[str, Any]:
    pokemon = []
    hp_table = []
    seen = set()
    for card in cards:
        kit = _card_kit(card)
        name = kit.get("name") or ""
        if not name or name in seen:
            continue
        seen.add(name)
        pokemon.append(kit)
        if kit.get("hp"):
            hp_table.append({"name": name, "hp": kit["hp"], "card_id": kit.get("card_id")})
    return {
        "combo_key": combo.get("combo_key"),
        "label": combo.get("label"),
        "label_zh": combo.get("label_zh"),
        "families": [
            {"id": row["id"], "en": row["en"], "zh": row["zh"], "count": row["count"], "staple": row["staple"]}
            for row in combo.get("families") or []
        ],
        "pokemon_kits": pokemon,
        "attackers": pokemon,
        "hp_table": hp_table,
        "skill_catalog": [line for kit in pokemon for line in _catalog_lines(kit)],
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


def _split_think(text: str) -> tuple[str, str]:
    raw = str(text or "")
    thinks = re.findall(r"<think>(.*?)</think>", raw, flags=re.S | re.I)
    rest = re.sub(r"<think>.*?</think>", "", raw, flags=re.S | re.I)
    leftover = re.search(r"<think>(.*)$", rest, flags=re.S | re.I)
    if leftover:
        thinks.append(leftover.group(1))
        rest = rest[: leftover.start()]
    return rest.strip(), "\n".join(part.strip() for part in thinks if part.strip())


def _extract_json_object(text: str) -> dict[str, Any] | None:
    rest, think = _split_think(text)
    blobs = [rest, think, str(text or "")]
    candidates = []
    for blob in blobs:
        raw = blob.strip()
        if not raw:
            continue
        if raw.startswith("```"):
            raw = re.sub(r"^```(?:json)?", "", raw, flags=re.I).strip()
            raw = re.sub(r"```$", "", raw).strip()
        candidates.append(raw)
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
                "source": "full_skill_catalog",
                "tempo_note": None,
                "combo_lines": profile.get("skill_catalog") or [],
                "pokemon_kits": profile.get("pokemon_kits") or [],
                "verified": False,
                "note": "已列出代表牌表每張寶可夢嘅特性同招式全文（含穿備戰等效果）。尚未判斷邊條先係主打點。",
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
        created_keys = [row["combo_key"] for row in created]
        annotate_status = start_brief_annotations(created_keys)
        return {
            "success": True,
            "month": month,
            "window": field.get("window"),
            "quota": slots,
            "created": created,
            "skipped_existing": skipped[:40],
            "created_count": len(created),
            "skipped_count": len(skipped),
            "annotate": annotate_status,
        }
    except Exception as exc:
        conn.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        conn.close()




_ANNOTATE_LOCK = threading.Lock()
_ANNOTATE_QUEUE: list[str] = []
_ANNOTATE_JOB: dict[str, Any] = {
    "running": False,
    "total": 0,
    "done": 0,
    "failed": 0,
    "current": "",
    "last_key": "",
    "last_ok": False,
    "last_error": "",
    "last_reply": "",
    "message": "就緒",
    "log": [],
}


def get_annotate_status() -> dict[str, Any]:
    with _ANNOTATE_LOCK:
        payload = dict(_ANNOTATE_JOB)
        payload["queued"] = list(_ANNOTATE_QUEUE)
        return payload


def start_brief_annotations(combo_keys: list[str]) -> dict[str, Any]:
    keys = [str(key) for key in combo_keys if key]
    if not keys:
        return {"running": False, "total": 0, "message": "沒有新 brief 需要 AI 整理"}
    with _ANNOTATE_LOCK:
        if _ANNOTATE_JOB.get("running"):
            current = _ANNOTATE_JOB.get("current") or ""
            for key in keys:
                if key != current and key not in _ANNOTATE_QUEUE:
                    _ANNOTATE_QUEUE.append(key)
                    _ANNOTATE_JOB["total"] = int(_ANNOTATE_JOB.get("total") or 0) + 1
            _ANNOTATE_JOB["message"] = f"已排入隊列：{keys[0]}"
            payload = dict(_ANNOTATE_JOB)
            payload["queued"] = list(_ANNOTATE_QUEUE)
            payload["started"] = True
            return payload
        _ANNOTATE_JOB.update({
            "running": True,
            "total": len(keys),
            "done": 0,
            "failed": 0,
            "current": keys[0],
            "last_key": "",
            "last_ok": False,
            "last_error": "",
            "last_reply": "",
            "message": f"準備整理 {len(keys)} 份 brief",
            "log": [{"at": datetime.utcnow().isoformat(timespec="seconds") + "Z", "step": "start", "text": f"開始整理 {len(keys)} 套", "error": "", "raw": ""}],
        })
    threading.Thread(target=_annotate_worker, args=(keys,), daemon=True).start()
    status = get_annotate_status()
    status["started"] = True
    return status


def _annotate_worker(combo_keys: list[str]) -> None:
    pending = list(combo_keys)
    while pending:
        key = pending.pop(0)
        with _ANNOTATE_LOCK:
            _ANNOTATE_JOB["current"] = key
            _ANNOTATE_JOB["message"] = f"AI 三步整理中：{key}"
        result = annotate_brief_with_ai(key)
        with _ANNOTATE_LOCK:
            _ANNOTATE_JOB["last_key"] = key
            _ANNOTATE_JOB["last_ok"] = bool(result.get("success"))
            _ANNOTATE_JOB["last_error"] = "" if result.get("success") else str(result.get("error") or "整理失敗")
            _ANNOTATE_JOB["last_reply"] = str(result.get("reply") or "")
            if result.get("success"):
                _ANNOTATE_JOB["done"] += 1
            else:
                _ANNOTATE_JOB["failed"] += 1
                _ANNOTATE_JOB["message"] = _ANNOTATE_JOB["last_error"]
            pending.extend(_ANNOTATE_QUEUE)
            _ANNOTATE_QUEUE.clear()
    with _ANNOTATE_LOCK:
        _ANNOTATE_JOB["running"] = False
        _ANNOTATE_JOB["current"] = ""
        _ANNOTATE_JOB["message"] = (
            f"AI 整理完成：成功 {_ANNOTATE_JOB['done']}，失敗 {_ANNOTATE_JOB['failed']}"
        )


def append_annotate_log(step: str, text: str, error: str = "", raw: str = "") -> None:
    entry = {
        "at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "step": step,
        "text": text,
        "error": error,
        "raw": str(raw or "")[:800],
    }
    with _ANNOTATE_LOCK:
        logs = list(_ANNOTATE_JOB.get("log") or [])
        logs.append(entry)
        _ANNOTATE_JOB["log"] = logs[-40:]
        _ANNOTATE_JOB["message"] = error or text


def _compact_catalog(catalog: Any, limit: int = 60) -> list[str]:
    lines = catalog if isinstance(catalog, list) else [str(catalog or "")]
    compact = []
    for line in lines:
        text = str(line or "").strip()
        if not text:
            continue
        compact.append(text[:500])
        if len(compact) >= limit:
            break
    return compact


def _function_tool(name: str, description: str, properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


ROLE_TOOL = _function_tool(
    "classify_pokemon_roles",
    "分類牌組入面每張寶可夢係主打手、引擎定工具寵。",
    {
        "roles": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "role": {"type": "string", "enum": ["main_attacker", "engine", "tool", "tech", "setup", "tank"]},
                    "reason": {"type": "string"},
                },
                "required": ["name", "role", "reason"],
            },
        },
        "main_attackers": {"type": "array", "items": {"type": "string"}},
        "tools": {"type": "array", "items": {"type": "string"}},
        "engines": {"type": "array", "items": {"type": "string"}},
        "techs": {"type": "array", "items": {"type": "string"}},
    },
    ["roles", "main_attackers", "tools"],
)

KILL_TOOL = _function_tool(
    "extract_kill_lines",
    "列出主炮、補傷同工具便利線，傷害只能用目錄出現過嘅數字。",
    {
        "kill_lines": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "attacker": {"type": "string"},
                    "role": {"type": "string"},
                    "attack": {"type": "string"},
                    "damage": {"type": "string"},
                    "kind": {"type": "string", "enum": ["primary", "chip", "utility"]},
                    "breaks": {"type": "array", "items": {"type": "string"}},
                    "note": {"type": "string"},
                },
                "required": ["attacker", "attack", "damage", "kind"],
            },
        }
    },
    ["kill_lines"],
)

WRITEUP_TOOL = _function_tool(
    "write_deck_brief",
    "用已有角色同斬殺線寫打法、優勢、弱點。",
    {
        "gameplan": {"type": "string"},
        "advantages": {"type": "array", "items": {"type": "string"}},
        "weaknesses": {"type": "array", "items": {"type": "string"}},
        "tempo_note": {"type": "string"},
        "reply": {"type": "string"},
    },
    ["gameplan", "advantages", "weaknesses", "reply"],
)


def _tool_arguments(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return None
    calls = message.get("tool_calls") or []
    if isinstance(calls, dict):
        calls = [calls]
    if not calls and message.get("function_call"):
        calls = [{"function": message.get("function_call")}]
    for call in calls:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else call
        raw = fn.get("arguments") or fn.get("parameters") or fn.get("args")
        if isinstance(raw, dict) and raw:
            return raw
        if isinstance(raw, str) and raw.strip():
            parsed = _extract_json_object(raw)
            if parsed:
                return parsed
    return None


def _ai_json(system: str, payload: dict[str, Any], tool: dict[str, Any] | None = None) -> tuple[dict[str, Any] | None, str, str]:
    from services.ai_assistant.client import AIClientError, AIConfigError, chat_message

    if isinstance(payload, dict) and payload.get("catalog"):
        payload = dict(payload)
        payload["catalog"] = _compact_catalog(payload.get("catalog"))
    tool_name = ((tool or {}).get("function") or {}).get("name") or "submit_result"
    prompt = [
        {
            "role": "system",
            "content": system + " 必須用工具提交結果，不要只輸出 <think> 或純文字。",
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)},
    ]
    raw_text = ""
    last_error = ""
    for attempt in range(3):
        append_annotate_log("request", f"第 {attempt + 1} 次強制工具調用 {tool_name}")
        try:
            msg = chat_message(
                prompt,
                temperature=0.2,
                thinking=False,
                timeout=90,
                max_tokens=4096,
                tools=[tool] if tool else None,
                tool_choice={"type": "function", "function": {"name": tool_name}} if tool else None,
            )
            raw_text = _message_text(msg)
            rest, think = _split_think(raw_text)
            if think:
                append_annotate_log("think", "模型思考摘錄", raw=think[:800])
            parsed = _tool_arguments(msg) or _extract_json_object(raw_text)
            if parsed:
                append_annotate_log("tool", f"已收到工具參數 {tool_name}", raw=json.dumps(parsed, ensure_ascii=False, default=str)[:800])
                return parsed, raw_text or json.dumps(parsed, ensure_ascii=False), ""
            last_error = "模型沒有呼叫工具，亦沒有可用 JSON"
            append_annotate_log("parse", last_error, last_error, raw_text or str(msg)[:800])
        except (AIConfigError, AIClientError) as exc:
            last_error = str(exc)
            append_annotate_log("error", last_error, last_error, raw_text)
            if "401" in last_error or "402" in last_error or "未設定" in last_error:
                break
        time.sleep(1.5 * (attempt + 1))
    return None, raw_text, last_error


def annotate_brief_with_ai(combo_key: str) -> dict[str, Any]:
    """3-step pipeline: roles -> kill/utility lines -> gameplan writeup."""
    current = get_brief(combo_key)
    if not current.get("success"):
        return current
    brief = current["brief"]
    analysis = brief.get("analysis") if isinstance(brief.get("analysis"), dict) else {}
    profile = brief.get("profile") if isinstance(brief.get("profile"), dict) else {}
    catalog = analysis.get("combo_lines") or profile.get("skill_catalog") or []
    base = {
        "combo_key": brief.get("combo_key") or combo_key,
        "label_zh": brief.get("label_zh"),
        "share": brief.get("last_share"),
        "n": brief.get("last_n"),
        "catalog": catalog,
        "hp_table": profile.get("hp_table") or [],
        "families": profile.get("families") or [],
        "common_hp": [20, 30, 40, 50, 60, 70, 80, 90, 100, 110, 120, 130, 140, 150, 160, 170, 180, 190, 200, 220, 230, 240, 250, 270, 280, 300, 310, 330, 340],
    }

    append_annotate_log("roles", f"步驟 1／3：分類主打手同工具寵（{brief.get('label_zh') or combo_key}）")
    roles, raw1, err1 = _ai_json(
        (
            "你係 PTCG 牌組角色分類員。先讀完整特性同招式效果，禁止只睇印刷傷害數字。"
            "combo_key／牌組名只係按張數起名，唔等於主炮。"
            "判斷順序：1) 特性上能量＝engine，即使招式只有20-30傷（碧草面具係引擎不是30點傷tool）。"
            "2) 招式棄能量／按數量加傷＝用有效傷害。猛雷鼓印刷70、棄4能就係主炮，唔好寫70傷次要輸出。"
            "3) 印刷180-200但無加傷、費用高＝副攻。超級袋獸200、拉帝亞斯無限之刃200，若牌組已有棄能加傷主軸就唔是主炮。"
            "4) 喵喵ex／含羞苞／願增猿／吉雉雞＝tool/tech。5) 低傷對血線＝utility tool。"
            "main_attackers 只放真正決定輸出曲線嗰1-2隻。reason 必須引用效果原文，禁止只寫「200傷主炮」。"
        ),
        base,
        ROLE_TOOL,
    )
    if not roles:
        append_annotate_log("roles", "步驟 1 失敗", err1 or "角色分類失敗", raw1)
        return {"success": False, "error": err1 or "角色分類失敗", "combo_key": combo_key, "raw": raw1[:800]}
    append_annotate_log(
        "roles",
        "步驟 1 完成：主打 "
        + "、".join(str(x) for x in (roles.get("main_attackers") or [])[:6])
        + "／工具 "
        + "、".join(str(x) for x in (roles.get("tools") or [])[:6]),
        raw=json.dumps(roles, ensure_ascii=False, default=str)[:800],
    )

    append_annotate_log("kills", "步驟 2／3：整理主炮、補傷同工具便利線")
    lines, raw2, err2 = _ai_json(
        (
            "你係 PTCG 斬殺線分析員。先用角色分類，再按招式效果計有效傷害。"
            "印刷傷害只係基數。如果效果寫棄 X 個能量／按數量加傷，要寫「印刷70＋棄4能→有效280」呢類公式，並且 kind=primary。"
            "唔好把固定 200 傷但無加傷嘅副攻寫成 primary。"
            "utility 仍然要包括低傷對血線：30 含羞苞、60/70 土龍細體。"
            "breaks 寫具體血量。傷害每段都要能喺目錄效果搵到，唔好發明基數。"
        ),
        {**base, "roles": roles},
        KILL_TOOL,
    )
    if not lines:
        append_annotate_log("kills", "步驟 2 失敗", err2 or "斬殺線整理失敗", raw2)
        return {"success": False, "error": err2 or "斬殺線整理失敗", "combo_key": combo_key, "raw": raw2[:800], "roles": roles}
    kill_rows = lines.get("kill_lines") if isinstance(lines, dict) else lines
    append_annotate_log("kills", f"步驟 2 完成：{len(kill_rows or [])} 條斬殺／便利線", raw=json.dumps(lines, ensure_ascii=False, default=str)[:800])

    append_annotate_log("writeup", "步驟 3／3：寫打法、優勢、弱點")
    writeup, raw3, err3 = _ai_json(
        (
            "你係 PTCG 牌組評論員。打法必須跟角色分類同有效傷害，唔好跟牌組名。"
            "如果主炮係猛雷鼓棄能加傷，就寫點灌能（碧草面具引擎）同點棄能打出有效傷害，唔好寫超級袋獸／拉帝亞斯 200 主軸。"
            "gameplan 3-8 句。advantages／weaknesses 每點綁效果或斬殺線。"
        ),
        {**base, "roles": roles, "kill_lines": lines.get("kill_lines") if isinstance(lines, dict) else lines},
        WRITEUP_TOOL,
    )
    if not writeup:
        append_annotate_log("writeup", "步驟 3 失敗", err3 or "打法整理失敗", raw3)
        return {"success": False, "error": err3 or "打法整理失敗", "combo_key": combo_key, "raw": raw3[:800], "roles": roles, "kill_lines": lines}
    append_annotate_log("writeup", str(writeup.get("reply") or "步驟 3 完成"), raw=json.dumps(writeup, ensure_ascii=False, default=str)[:800])

    merged = dict(analysis)
    merged["pokemon_roles"] = roles.get("roles") if isinstance(roles, dict) else roles
    merged["role_groups"] = {
        "main_attackers": roles.get("main_attackers") if isinstance(roles, dict) else [],
        "tools": roles.get("tools") if isinstance(roles, dict) else [],
        "engines": roles.get("engines") if isinstance(roles, dict) else [],
        "techs": roles.get("techs") if isinstance(roles, dict) else [],
    }
    merged["kill_lines"] = lines.get("kill_lines") if isinstance(lines, dict) else lines
    for field in ("gameplan", "advantages", "weaknesses", "tempo_note", "reply"):
        if field in writeup:
            merged[field] = writeup[field]
    merged["pipeline"] = ["roles", "kill_lines", "writeup"]
    merged["source"] = "catalog+role+kill+writeup"
    merged["annotated"] = True
    with _ANNOTATE_LOCK:
        merged["pipeline_log"] = list(_ANNOTATE_JOB.get("log") or [])[-20:]
    saved = update_brief(combo_key, analysis=merged, label_zh=brief.get("label_zh"), note="ai pipeline roles/kills/writeup")
    saved["reply"] = writeup.get("reply") or "已完成三角色分類、斬殺線同打法整理"
    return saved


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
