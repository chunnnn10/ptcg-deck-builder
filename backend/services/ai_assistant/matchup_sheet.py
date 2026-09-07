"""Matchup Sheet schema + seed rows.

Nightly batch should refresh archetype_profile (setup / quirks / printed attacks).
Chat-time composes a matchup sheet from two profiles + user deck.
Seed rows below are user-described 2026-09 examples and are NOT card-DB verified.
"""

from __future__ import annotations

from typing import Any


SCHEMA_VERSION = "2026-09-07.1"

# Nightly: store one profile per live archetype.
ARCHETYPE_PROFILE_SCHEMA = {
    "archetype_id": "slug",
    "display_name": "str",
    "updated_at": "ISO-8601",
    "source": ["limitless", "jp_tournament", "user_seed"],
    "setup": {
        "evolution_stage": "basic | stage1 | stage2 | mega | mixed",
        "energy_to_attack": "int",
        "energy_type": "str",
        "boost_cost": "str | null  # e.g. discard Water energy",
        "search_filter_notes": "str",
    },
    "quirks": [
        {
            "id": "slug",
            "rule": "str  # tera_bench_prevents_attack_damage",
            "applies_to": "active | bench | self | opponent",
            "note": "str",
        }
    ],
    "printed_lines": [
        {
            "card": "str",
            "kind": "attack | ability | item | tool",
            "damage": "int | null",
            "target": "active | bench | both | self",
            "cost": "str",
            "condition": "str",
        }
    ],
    "hp_table": [
        {"card": "str", "hp": "int", "notes": "str"}
    ],
}

# Chat-time: one sheet per (user_deck x opponent_archetype).
MATCHUP_SHEET_SCHEMA = {
    "schema_version": SCHEMA_VERSION,
    "user_archetype": "str",
    "opponent_archetype": "str",
    "verified": "bool  # false unless numbers were checked against card rows",
    "setup": ARCHETYPE_PROFILE_SCHEMA["setup"],
    "quirks": ARCHETYPE_PROFILE_SCHEMA["quirks"],
    "breakpoints": [
        {
            "defender": "str",
            "hp": "int",
            "modifier": "none | chip | tool | ability",
            "modifier_note": "str",
            "effective_hp": "int",
            "killed_by": ["str"],
            "not_killed_by": ["str"],
        }
    ],
    "vs_archetype": {
        "advantages": ["str"],
        "disadvantages": ["str"],
        "kill_lines": ["str"],
        "tempo_note": "str",
    },
}


SEED_SHEETS: list[dict[str, Any]] = [
    {
        "schema_version": SCHEMA_VERSION,
        "user_archetype": "mega_greninja",
        "user_display_name": "Mega 水忍蛙／甲賀忍蛙",
        "opponent_archetype": "dragapult",
        "opponent_display_name": "多龍",
        "verified": False,
        "source": "user_seed_2026-09-07",
        "setup": {
            "evolution_stage": "stage2",
            "energy_to_attack": 2,
            "energy_type": "Water",
            "boost_cost": "加傷要棄水能量，能量需求再升，且要濾到那張能量",
            "search_filter_notes": "2 階展開本身慢，再加 2 能量同棄能加傷",
        },
        "quirks": [
            {
                "id": "tera_bench_block_attack",
                "rule": "太晶在備戰區可防止招式傷害",
                "applies_to": "bench",
                "note": "後排忍蛙唔驚多龍穿招式傷害；特性傷／放置指示物仍要另計",
            }
        ],
        "breakpoints": [
            {
                "defender": "甲賀忍蛙／Mega 水忍蛙",
                "hp": 310,
                "modifier": "none",
                "modifier_note": "原血",
                "effective_hp": 310,
                "killed_by": ["主攻 + 黑夜魔靈／夜巡靈特性自昏 130"],
                "not_killed_by": ["多數單招未加炸彈"],
            }
        ],
        "vs_archetype": {
            "advantages": [
                "太晶後排防多龍穿招式傷害",
                "水忍蛙可選 200 前排、後排 120x2、或 170 再選牌",
            ],
            "disadvantages": [
                "310 低過部分多龍線，加炸彈後容易被集火",
                "2 階 + 2 能量 + 棄能加傷，節奏慢過穿傷循環",
            ],
            "kill_lines": [
                "水忍蛙 200 + 黑夜魔靈特性 130 = 330，可打高端血量線",
            ],
            "tempo_note": "多龍通常較快開到穿傷；忍蛙要進化同填能先有完整輸出",
        },
    },
    {
        "schema_version": SCHEMA_VERSION,
        "user_archetype": "mega_greninja",
        "user_display_name": "Mega 水忍蛙／甲賀忍蛙",
        "opponent_archetype": "lucario",
        "opponent_display_name": "路卡",
        "verified": False,
        "source": "user_seed_2026-09-07",
        "setup": {
            "evolution_stage": "stage2",
            "energy_to_attack": 2,
            "energy_type": "Water",
            "boost_cost": "加傷要棄水能量",
            "search_filter_notes": "展開慢；被點傷後進入更多打擊帶",
        },
        "quirks": [
            {
                "id": "chip_opens_300_band",
                "rule": "含羞苞等小點傷可把 310 推進 300 帶",
                "applies_to": "active",
                "note": "斷點改變後路卡線嘅蛋白飲等工具會對上",
            }
        ],
        "breakpoints": [
            {
                "defender": "甲賀忍蛙／Mega 水忍蛙",
                "hp": 310,
                "modifier": "chip",
                "modifier_note": "含羞苞點到約 300",
                "effective_hp": 300,
                "killed_by": ["路卡 + 蛋白飲"],
                "not_killed_by": ["未標明嘅單發路卡招式（需對卡文核對）"],
            }
        ],
        "vs_archetype": {
            "advantages": [
                "太晶後排仍防招式穿傷",
            ],
            "disadvantages": [
                "310 原血已經尷尬；被點 10 之後進入 300 帶，路卡工具線變齊",
            ],
            "kill_lines": [
                "含羞苞把 310→300 後，路卡 + 蛋白飲可一刀",
            ],
            "tempo_note": "路卡爆發快過 2 階忍蛙開槍",
        },
    },
    {
        "schema_version": SCHEMA_VERSION,
        "user_archetype": "mega_greninja",
        "user_display_name": "Mega 水忍蛙／甲賀忍蛙",
        "opponent_archetype": "rika_grass",
        "opponent_display_name": "竹蘭草（竹蘭的羅絲雷朵）",
        "verified": False,
        "source": "user_seed_2026-09-07",
        "setup": {
            "evolution_stage": "stage2",
            "energy_to_attack": 2,
            "energy_type": "Water",
            "boost_cost": "加傷要棄水能量",
            "search_filter_notes": "第二招 120 打唔死 130 血羅絲雷朵",
        },
        "quirks": [
            {
                "id": "awkward_120_vs_130",
                "rule": "輸出數字低過對手引擎體 HP",
                "applies_to": "opponent",
                "note": "竹蘭的羅絲雷朵 130 HP，忍蛙 2 技能 120，清唔走引擎",
            }
        ],
        "breakpoints": [
            {
                "defender": "竹蘭的羅絲雷朵",
                "hp": 130,
                "modifier": "none",
                "modifier_note": "原血",
                "effective_hp": 130,
                "killed_by": [],
                "not_killed_by": ["忍蛙第二招 120"],
            },
            {
                "defender": "甲賀忍蛙／Mega 水忍蛙",
                "hp": 310,
                "modifier": "chip",
                "modifier_note": "含羞苞點到約 300",
                "effective_hp": 300,
                "killed_by": ["烈咬陸鯊 + 2 張竹蘭的羅絲雷朵"],
                "not_killed_by": [],
            },
        ],
        "vs_archetype": {
            "advantages": [
                "有 200 前排同 330 組合線時， theoretically 可壓過單隻引擎體",
            ],
            "disadvantages": [
                "120 打唔死 130，清場尷尬",
                "被點到 300 後，陸鯊 + 2 羅絲雷朵可一刀返斬 310 線",
            ],
            "kill_lines": [
                "對方：含羞苞 310→300，烈咬陸鯊 + 2 竹蘭羅絲雷朵斬忍蛙",
                "己方：第二招 120 斬唔死 130 羅絲雷朵",
            ],
            "tempo_note": "草線用點傷改斷點，忍蛙要用更高數字或組合線先划算",
        },
    },
]


def list_matchup_sheets() -> list[dict[str, Any]]:
    return [
        {
            "user_archetype": row["user_archetype"],
            "opponent_archetype": row["opponent_archetype"],
            "user_display_name": row.get("user_display_name"),
            "opponent_display_name": row.get("opponent_display_name"),
            "verified": bool(row.get("verified")),
            "source": row.get("source"),
        }
        for row in SEED_SHEETS
    ]


def _norm(value: str) -> str:
    return str(value or "").strip().lower().replace(" ", "").replace("／", "/")


def get_matchup_sheet(user_archetype: str = "", opponent_archetype: str = "") -> dict[str, Any]:
    user_q = _norm(user_archetype)
    opp_q = _norm(opponent_archetype)
    aliases = {
        "忍蛙": "mega_greninja",
        "水忍蛙": "mega_greninja",
        "甲賀忍蛙": "mega_greninja",
        "mega水忍蛙": "mega_greninja",
        "greninja": "mega_greninja",
        "多龍": "dragapult",
        "多龍梅里亞": "dragapult",
        "dragapult": "dragapult",
        "路卡": "lucario",
        "路卡利歐": "lucario",
        "lucario": "lucario",
        "竹蘭": "rika_grass",
        "竹蘭草": "rika_grass",
        "羅絲雷朵": "rika_grass",
        "rika": "rika_grass",
    }
    user_id = aliases.get(user_q, user_q or "mega_greninja")
    opp_id = aliases.get(opp_q, opp_q)
    matches = []
    for row in SEED_SHEETS:
        if user_id and row["user_archetype"] != user_id and _norm(row.get("user_display_name") or "") != user_q:
            if user_q and user_q not in _norm(row.get("user_display_name") or "") and user_q != row["user_archetype"]:
                continue
        if opp_id and row["opponent_archetype"] != opp_id:
            if opp_q and opp_q not in _norm(row.get("opponent_display_name") or "") and opp_q != row["opponent_archetype"]:
                continue
        matches.append(row)
    if not user_q and not opp_q:
        matches = list(SEED_SHEETS)
    return {
        "schema_version": SCHEMA_VERSION,
        "schema": MATCHUP_SHEET_SCHEMA,
        "profile_schema": ARCHETYPE_PROFILE_SCHEMA,
        "generation": {
            "archetype_profile": "nightly_batch",
            "matchup_sheet": "chat_time",
            "user_kill_lines": "optional_gold_overlay",
        },
        "count": len(matches),
        "sheets": matches,
        "available": list_matchup_sheets(),
        "note": "種子列來自用戶 2026-09-07 對局描述，verified=false，聊天時必須標未對卡文。",
    }
