#!/usr/bin/env python3
"""Seed a deterministic, realistic PTCG card set into an ISOLATED benchmark DB.

Usage:
    python backend/eval/seed_bench.py --db postgresql://ptcg:ptcg_secret@127.0.0.1:5432/ptcg_bench

Behaviour:
* Sets DATABASE_URL in-process BEFORE importing config/database, so the whole
  process (schema init + inserts) only ever talks to the isolated DB.
* Refuses to run against ptcg_db.
* Idempotent: deletes rows whose card_id starts with ``BENCH-`` and re-inserts
  the same 60 base cards (Pokemon / Trainer / Energy) plus ~18 additive H/I/J
  deck-search Pokemon, with Chinese names, HP, element, sub_type,
  evolution_stage, evolves_from, set_code/set_number, regulation_mark and
  Chinese skills_json. The deck-search Pokemon carry 牌庫 search/filter effects
  so question ``q09-pokemon-deck-filter`` is answerable from the seed.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]

ID_PREFIX = "BENCH-"

# (name, element, hp, stage, evolves_from, attacks)
# attack = (name, cost, damage, effect)
POKEMON = [
    ("妙蛙種子", "草", 70, "基礎", None, [("撞擊", ["無色"], "10", ""), ("藤鞭", ["草", "無色"], "30", "")]),
    ("妙蛙草", "草", 100, "1階進化", "妙蛙種子", [("藤鞭", ["草", "無色"], "40", ""), ("飛葉快刀", ["草", "草", "無色"], "60", "")]),
    ("妙蛙花ex", "草", 340, "2階進化", "妙蛙草", [("日光束", ["草", "草", "無色"], "160", "這隻寶可夢恢復30點HP。")]),
    ("小火龍", "火", 70, "基礎", None, [("抓", ["無色"], "10", ""), ("火花", ["火", "無色"], "30", "")]),
    ("火恐龍", "火", 100, "1階進化", "小火龍", [("火花", ["火", "無色"], "40", ""), ("火焰旋渦", ["火", "火", "無色"], "70", "")]),
    ("噴火龍ex", "火", 330, "2階進化", "火恐龍", [("烈焰", ["火", "火", "無色"], "180", "將這隻寶可夢身上2個能量丟到棄牌區。")]),
    ("傑尼龜", "水", 70, "基礎", None, [("水槍", ["水", "無色"], "30", "")]),
    ("卡咪龜", "水", 100, "1階進化", "傑尼龜", [("貝殼攻擊", ["水", "無色"], "40", "")]),
    ("水箭龜ex", "水", 340, "2階進化", "卡咪龜", [("水砲", ["水", "水", "無色"], "160", "")]),
    ("皮卡丘ex", "雷", 190, "基礎", None, [("十萬伏特", ["雷", "雷", "無色"], "200", "這隻寶可夢也受到30點傷害。")]),
    ("雷丘", "雷", 120, "1階進化", "皮卡丘", [("電擊", ["雷", "無色"], "60", "")]),
    ("超夢ex", "超", 220, "基礎", None, [("精神強念", ["超", "超", "無色"], "160", "")]),
    ("耿鬼ex", "超", 250, "2階進化", "鬼斯通", [("暗影球", ["超", "超", "無色"], "150", "")]),
    ("沙奈朵ex", "超", 310, "2階進化", "奇魯莉安", [("妖精之風", ["超", "無色"], "120", "從棄牌區拿1張能量卡附於這隻寶可夢。")]),
    ("烈咬陸鯊ex", "鬥", 320, "2階進化", "尖牙陸鯊", [("龍之俯衝", ["鬥", "無色"], "150", "")]),
    ("路卡利歐ex", "鬥", 260, "1階進化", "利歐路", [("波導彈", ["鬥", "無色"], "130", "")]),
    ("洛奇亞V", "無色", 220, "基礎", None, [("氣旋吹拂", ["無色", "無色"], "120", "")]),
    ("拉帝亞斯ex", "無色", 260, "基礎", None, [("潔淨之翼", ["無色", "無色"], "100", "")]),
    ("甲賀忍蛙ex", "水", 300, "2階進化", "呱頭蛙", [("水之手裡劍", ["水", "無色"], "140", "")]),
    ("木木梟", "草", 60, "基礎", None, [("啄", ["無色"], "20", "")]),
    ("火焰雞ex", "火", 310, "2階進化", "力壯雞", [("烈焰踢", ["火", "火", "無色"], "170", "")]),
    ("鐵包袱ex", "鋼", 220, "基礎", None, [("鐵壁", ["鋼", "無色"], "90", "這隻寶可夢不會受到特殊狀態影響。")]),
    ("賽富豪ex", "鋼", 260, "基礎", None, [("閃耀爆擊", ["鋼", "鋼", "無色"], "160", "")]),
    ("密勒頓ex", "雷", 220, "基礎", None, [("光子爆破", ["雷", "雷", "無色"], "180", "這隻寶可夢也受到30點傷害。")]),
    ("厄鬼椪", "草", 120, "基礎", None, [("碧草面具", ["草", "無色"], "60", "若場上有競技場卡，這招式的傷害+50。")]),
    ("袋獸", "無色", 130, "基礎", None, [("拳擊", ["無色", "無色"], "70", "")]),
    ("鐵頭殼ex", "鋼", 230, "基礎", None, [("金屬爆破", ["鋼", "無色"], "110", "")]),
    ("帝王拿波ex", "水", 320, "2階進化", "波皇子", [("水之衝擊", ["水", "水", "無色"], "160", "")]),
    ("老翁龍", "無色", 120, "基礎", None, [("龍之咆哮", ["無色"], "30", "對手下回合不能出支援者卡。")]),
    ("阿爾宙斯V", "無色", 220, "基礎", None, [("三重奏", ["無色", "無色"], "120", "")]),
]

# (name, sub_type, effect)
TRAINERS = [
    ("博士的研究", "支援者", "從牌庫抽7張卡。"),
    ("莎莉娜", "支援者", "將對手戰鬥寶可夢與備戰寶可夢互換。"),
    ("阿塞蘿拉", "支援者", "將這隻寶可夢與備戰寶可夢互換，並恢復30點HP。"),
    ("老大", "支援者", "將對手戰鬥寶可夢與備戰寶可夢互換。"),
    ("小茜", "支援者", "從牌庫抽5張卡。"),
    ("奇樹", "支援者", "雙方玩家將手牌洗回牌庫，然後各抽4張卡。"),
    ("派帕", "支援者", "從牌庫搜尋1張道具卡與1張支援者卡。"),
    ("巢穴球", "物品", "從牌庫搜尋1張基礎寶可夢卡。"),
    ("高級球", "物品", "從手牌丟棄2張卡，從牌庫搜尋1張寶可夢卡。"),
    ("先機球", "物品", "從牌庫搜尋1張寶可夢卡，若為V則放回。"),
    ("精靈球", "物品", "擲1次硬幣，正面從牌庫搜尋1張寶可夢卡。"),
    ("能量回收", "物品", "從棄牌區拿回2張基本能量卡。"),
    ("能量轉換", "物品", "將1個基本能量移到另1隻寶可夢身上。"),
    ("神奇糖果", "物品", "讓基礎寶可夢直接進化成2階進化寶可夢。"),
    ("反擊捕捉器", "物品", "若己方剩餘獎勵卡較少，將對手備戰寶可夢換為戰鬥寶可夢。"),
    ("寶可夢交替", "物品", "將戰鬥寶可夢與備戰寶可夢互換。"),
    ("消除之森", "競技場", "雙方玩家不能從手牌出支援者卡。"),
    ("對戰VIP通行證", "物品", "從牌庫搜尋最多2張基礎寶可夢卡，然後放回牌庫洗牌。"),
    ("學習裝置", "寶可夢道具", "附有這張卡的寶可夢使用招式時可少付1個能量。"),
    ("森林封印石", "寶可夢道具", "附有這張卡的寶可夢不會受到對手支援者卡的效果影響。"),
    ("進化奇石", "寶可夢道具", "若附有這張卡的寶可夢為進化寶可夢，受到招式傷害-30。"),
    ("歡迎燈籠", "物品", "從牌庫搜尋1張支援者卡。"),
]

# (name, sub_type, effect)
ENERGY = [
    ("基本草能量", "基本能量", "提供1個草能量。"),
    ("基本火能量", "基本能量", "提供1個火能量。"),
    ("基本水能量", "基本能量", "提供1個水能量。"),
    ("基本雷能量", "基本能量", "提供1個雷能量。"),
    ("基本超能量", "基本能量", "提供1個超能量。"),
    ("基本鬥能量", "基本能量", "提供1個鬥能量。"),
    ("雙重無色能量", "特殊能量", "提供2個無色能量。"),
    ("反擊能量", "特殊能量", "若己方剩餘獎勵卡較少，提供2個無色能量。"),
]

SET_CODES = ["SV8", "SV8a", "SV9", "SV7", "SV6a", "SV5a"]
REG_MARKS = ["H", "I", "J"]
# Cards explicitly pushed out of rotation for the benchmark question set.
OFF_ROTATION = {"老翁龍", "學習裝置"}

# Deck-filtering / deck-search Pokémon (H/I/J regulation marks).
#
# The original 60 cards contain no Pokémon whose skills mention 牌庫, so a
# search for 「牌庫 / 濾牌 / 搜尋牌庫」 found nothing. These realistic H/I/J
# Pokémon have effects that genuinely mention 牌庫 so keyword / trigram search
# can return them. They are appended AFTER the original cards, so every
# existing ``BENCH-`` row keeps its exact card_id and regulation_mark and the
# seed stays idempotent.
#
# (name, element, hp, stage, evolves_from, regulation_mark, set_code,
#  set_number, japanese_name, skills)
# skill = {"type": "ability"|"attack", "name": str, "cost": [..],
#          "damage": str, "effect": str}
DECK_FILTER_POKEMON = [
    (
        "多龍梅西亞", "超", 60, "基礎", None, "I", "SV8a", "087/106", "ドラメシヤ",
        [
            {"type": "attack", "name": "呼朋引伴", "cost": ["超"], "damage": "10", "effect": "從牌庫搜尋1張「多龍奇」加入手牌，然後放回牌庫洗牌。"},
            {"type": "attack", "name": "咬住", "cost": ["無色"], "damage": "10", "effect": ""},
        ],
    ),
    (
        "多龍奇", "超", 90, "1階進化", "多龍梅西亞", "I", "SV8a", "088/106", "ドロンチ",
        [
            {"type": "ability", "name": "偵查", "cost": [], "damage": "", "effect": "查看牌庫上方2張卡，將其中1張加入手牌，其餘放回牌庫。"},
            {"type": "attack", "name": "龍之波動", "cost": ["超", "無色"], "damage": "70", "effect": ""},
        ],
    ),
    (
        "多龍巴魯托ex", "超", 320, "2階進化", "多龍奇", "I", "SV8", "130/106", "ドラパルトex",
        [
            {"type": "attack", "name": "幻影潛襲", "cost": ["超", "無色"], "damage": "200", "effect": "從牌庫搜尋最多2張「多龍梅西亞」放於備戰區，然後放回牌庫洗牌。"},
        ],
    ),
    (
        "咕咕", "無色", 70, "基礎", None, "H", "SV7", "080/102", "ホーホー",
        [
            {"type": "attack", "name": "啄", "cost": ["無色"], "damage": "10", "effect": ""},
            {"type": "attack", "name": "夜巡", "cost": ["無色", "無色"], "damage": "20", "effect": "查看牌庫上方3張卡，以任意順序放回牌庫上方。"},
        ],
    ),
    (
        "貓頭夜鷹", "無色", 110, "1階進化", "咕咕", "H", "SV7", "081/102", "ヨルノズク",
        [
            {"type": "ability", "name": "寶石探尋", "cost": [], "damage": "", "effect": "從牌庫搜尋最多2張訓練家卡加入手牌，然後放回牌庫洗牌。"},
            {"type": "attack", "name": "空氣斬", "cost": ["無色", "無色"], "damage": "70", "effect": ""},
        ],
    ),
    (
        "拉魯拉絲", "超", 60, "基礎", None, "J", "SV6a", "032/064", "ラルトス",
        [
            {"type": "attack", "name": "念力", "cost": ["超"], "damage": "10", "effect": ""},
            {"type": "attack", "name": "呼喚", "cost": ["無色"], "damage": "", "effect": "從牌庫搜尋1張「奇魯莉安」加入手牌，然後放回牌庫洗牌。"},
        ],
    ),
    (
        "奇魯莉安", "超", 80, "1階進化", "拉魯拉絲", "J", "SV6a", "033/064", "キルリア",
        [
            {"type": "ability", "name": "提煉", "cost": [], "damage": "", "effect": "查看牌庫上方2張卡，將其中1張能量卡加入手牌，其餘放回牌庫。"},
            {"type": "attack", "name": "魔法射擊", "cost": ["超", "無色"], "damage": "50", "effect": ""},
        ],
    ),
    (
        "呆呆獸", "水", 70, "基礎", None, "H", "SV5a", "026/066", "ヤドン",
        [
            {"type": "attack", "name": "發呆", "cost": ["無色"], "damage": "", "effect": "從牌庫抽出1張卡。"},
            {"type": "attack", "name": "水槍", "cost": ["水", "無色"], "damage": "20", "effect": ""},
        ],
    ),
    (
        "呆殼獸", "水", 100, "1階進化", "呆呆獸", "H", "SV5a", "027/066", "ヤドラン",
        [
            {"type": "ability", "name": "悠然抽牌", "cost": [], "damage": "", "effect": "從牌庫抽出2張卡。"},
            {"type": "attack", "name": "熱水", "cost": ["水", "無色"], "damage": "60", "effect": ""},
        ],
    ),
    (
        "呆呆王", "水", 120, "1階進化", "呆呆獸", "I", "SV9", "045/098", "ヤドキング",
        [
            {"type": "attack", "name": "王者的智慧", "cost": ["水", "水", "無色"], "damage": "120", "effect": "從牌庫搜尋1張支援者卡加入手牌，然後放回牌庫洗牌。"},
        ],
    ),
    (
        "波波", "無色", 60, "基礎", None, "I", "SV9", "079/098", "ポッポ",
        [
            {"type": "attack", "name": "風起", "cost": ["無色"], "damage": "", "effect": "從牌庫搜尋1張基本能量卡加入手牌，然後放回牌庫洗牌。"},
        ],
    ),
    (
        "比比鳥", "無色", 90, "1階進化", "波波", "I", "SV9", "080/098", "ピジョン",
        [
            {"type": "attack", "name": "翅膀攻擊", "cost": ["無色", "無色"], "damage": "50", "effect": ""},
            {"type": "attack", "name": "空中偵察", "cost": ["無色"], "damage": "", "effect": "查看牌庫上方3張卡，將其中1張加入手牌，其餘以任意順序放回牌庫。"},
        ],
    ),
    (
        "大比鳥", "無色", 130, "2階進化", "比比鳥", "I", "SV8a", "089/106", "ピジョット",
        [
            {"type": "ability", "name": "快速搜尋", "cost": [], "damage": "", "effect": "從牌庫搜尋1張卡加入手牌，然後放回牌庫洗牌。"},
            {"type": "attack", "name": "暴風", "cost": ["無色", "無色", "無色"], "damage": "130", "effect": ""},
        ],
    ),
    (
        "迷布莉姆", "超", 60, "基礎", None, "J", "SV8", "068/106", "ミブリム",
        [
            {"type": "attack", "name": "安撫", "cost": ["超"], "damage": "10", "effect": ""},
            {"type": "attack", "name": "尋覓", "cost": ["無色"], "damage": "", "effect": "從牌庫搜尋1張「提布莉姆」加入手牌，然後放回牌庫洗牌。"},
        ],
    ),
    (
        "提布莉姆", "超", 90, "1階進化", "迷布莉姆", "J", "SV8", "069/106", "テブリム",
        [
            {"type": "ability", "name": "靜謐篩選", "cost": [], "damage": "", "effect": "查看牌庫上方2張卡，將其中1張加入手牌，其餘放入棄牌區。"},
            {"type": "attack", "name": "念力", "cost": ["超", "無色"], "damage": "60", "effect": ""},
        ],
    ),
    (
        "布莉姆溫", "超", 150, "2階進化", "提布莉姆", "J", "SV8", "070/106", "ブリムオン",
        [
            {"type": "attack", "name": "魔法閃耀", "cost": ["超", "超", "無色"], "damage": "160", "effect": "從牌庫搜尋最多2張「迷布莉姆」放於備戰區，然後放回牌庫洗牌。"},
        ],
    ),
    (
        "小貓怪", "雷", 60, "基礎", None, "H", "SV7", "034/102", "コリンク",
        [
            {"type": "attack", "name": "充電", "cost": ["雷"], "damage": "", "effect": "從牌庫搜尋1張「基本雷能量」附於這隻寶可夢，然後放回牌庫洗牌。"},
            {"type": "attack", "name": "咬住", "cost": ["無色", "無色"], "damage": "30", "effect": ""},
        ],
    ),
    (
        "倫琴貓", "雷", 150, "2階進化", "勒克貓", "H", "SV7", "036/102", "レントラー",
        [
            {"type": "ability", "name": "透視", "cost": [], "damage": "", "effect": "查看牌庫上方5張卡，將其中的寶可夢卡加入手牌，其餘放回牌庫。"},
            {"type": "attack", "name": "放電", "cost": ["雷", "雷", "無色"], "damage": "150", "effect": "這隻寶可夢也受到30點傷害。"},
        ],
    ),
    (
        "索偵蟲", "草", 60, "基礎", None, "J", "SV6a", "008/064", "サッチムシ",
        [
            {"type": "attack", "name": "情報收集", "cost": ["無色"], "damage": "", "effect": "將牌庫上方2張卡放入棄牌區。"},
        ],
    ),
]


def _build_rows():
    rows = []
    seq = 0
    for name, element, hp, stage, evolves_from, attacks in POKEMON:
        seq += 1
        mark = "E" if name in OFF_ROTATION else REG_MARKS[seq % len(REG_MARKS)]
        skills = [
            {
                "type": "attack",
                "name": attack_name,
                "cost": cost,
                "damage": damage,
                "effect": effect,
            }
            for attack_name, cost, damage, effect in attacks
        ]
        rows.append(
            {
                "card_id": f"{ID_PREFIX}{SET_CODES[seq % len(SET_CODES)]}-{seq:03d}",
                "card_type": "Pokémon",
                "name": name,
                "sub_type": stage,
                "hp": hp,
                "element_type": element,
                "weakness_type": "鬥" if element in ("雷", "鋼") else "超",
                "weakness_value": "×2",
                "resistance_type": None,
                "resistance_value": None,
                "retreat_cost": 1 if stage == "基礎" else 2,
                "skills_json": skills,
                "rarity": "RR" if name.endswith("ex") else "C",
                "japanese_name": None,
                "evolution_stage": stage,
                "evolves_from": evolves_from,
                "set_code": SET_CODES[seq % len(SET_CODES)],
                "set_number": f"{seq:03d}",
                "set_name": f"測試擴充包 {SET_CODES[seq % len(SET_CODES)]}",
                "regulation_mark": mark,
                "description": "",
            }
        )
    # A couple of JP-suffixed names so mixed-language lookup has something to hit.
    rows[9]["japanese_name"] = "ピカチュウex"
    rows[5]["japanese_name"] = "リザードンex"

    for name, sub_type, effect in TRAINERS:
        seq += 1
        mark = "E" if name in OFF_ROTATION else REG_MARKS[seq % len(REG_MARKS)]
        rows.append(
            {
                "card_id": f"{ID_PREFIX}{SET_CODES[seq % len(SET_CODES)]}-{seq:03d}",
                "card_type": "Trainer",
                "name": name,
                "sub_type": sub_type,
                "hp": None,
                "element_type": None,
                "weakness_type": None,
                "weakness_value": None,
                "resistance_type": None,
                "resistance_value": None,
                "retreat_cost": None,
                "skills_json": [{"type": "trainer", "name": name, "cost": [], "damage": "", "effect": effect}],
                "rarity": "U",
                "japanese_name": None,
                "evolution_stage": None,
                "evolves_from": None,
                "set_code": SET_CODES[seq % len(SET_CODES)],
                "set_number": f"{seq:03d}",
                "set_name": f"測試擴充包 {SET_CODES[seq % len(SET_CODES)]}",
                "regulation_mark": mark,
                "description": effect,
            }
        )

    for name, sub_type, effect in ENERGY:
        seq += 1
        rows.append(
            {
                "card_id": f"{ID_PREFIX}{SET_CODES[seq % len(SET_CODES)]}-{seq:03d}",
                "card_type": "Energy",
                "name": name,
                "sub_type": sub_type,
                "hp": None,
                "element_type": None,
                "weakness_type": None,
                "weakness_value": None,
                "resistance_type": None,
                "resistance_value": None,
                "retreat_cost": None,
                "skills_json": [{"type": "energy", "name": name, "cost": [], "damage": "", "effect": effect}],
                "rarity": "C",
                "japanese_name": None,
                "evolution_stage": None,
                "evolves_from": None,
                "set_code": SET_CODES[seq % len(SET_CODES)],
                "set_number": f"{seq:03d}",
                "set_name": f"測試擴充包 {SET_CODES[seq % len(SET_CODES)]}",
                "regulation_mark": REG_MARKS[seq % len(REG_MARKS)],
                "description": effect,
            }
        )

    # Additive deck-search Pokémon, appended last so the original 60 cards keep
    # their exact card_id / regulation_mark and re-seeding stays idempotent.
    for (
        name,
        element,
        hp,
        stage,
        evolves_from,
        mark,
        set_code,
        set_number,
        japanese_name,
        skills,
    ) in DECK_FILTER_POKEMON:
        seq += 1
        rows.append(
            {
                "card_id": f"{ID_PREFIX}{set_code}-{set_number.split('/')[0]}",
                "card_type": "Pokémon",
                "name": name,
                "sub_type": stage,
                "hp": hp,
                "element_type": element,
                "weakness_type": "鬥" if element in ("雷", "鋼") else "超",
                "weakness_value": "×2",
                "resistance_type": None,
                "resistance_value": None,
                "retreat_cost": 1 if stage == "基礎" else 2,
                "skills_json": skills,
                "rarity": "RR" if name.endswith("ex") else "C",
                "japanese_name": japanese_name,
                "evolution_stage": stage,
                "evolves_from": evolves_from,
                "set_code": set_code,
                "set_number": set_number,
                "set_name": f"測試擴充包 {set_code}",
                "regulation_mark": mark,
                "description": "",
            }
        )
    return rows


INSERT_SQL = """
    INSERT INTO cards (
        card_id, card_type, name, sub_type, hp, element_type,
        weakness_type, weakness_value, resistance_type, resistance_value,
        retreat_cost, skills_json, rarity, japanese_name, evolution_stage,
        evolves_from, set_code, set_number, set_name, regulation_mark, description
    ) VALUES (
        %(card_id)s, %(card_type)s, %(name)s, %(sub_type)s, %(hp)s, %(element_type)s,
        %(weakness_type)s, %(weakness_value)s, %(resistance_type)s, %(resistance_value)s,
        %(retreat_cost)s, %(skills_json)s::jsonb, %(rarity)s, %(japanese_name)s, %(evolution_stage)s,
        %(evolves_from)s, %(set_code)s, %(set_number)s, %(set_name)s, %(regulation_mark)s, %(description)s
    )
    ON CONFLICT (card_id) DO UPDATE SET
        card_type = EXCLUDED.card_type,
        name = EXCLUDED.name,
        sub_type = EXCLUDED.sub_type,
        hp = EXCLUDED.hp,
        element_type = EXCLUDED.element_type,
        skills_json = EXCLUDED.skills_json,
        japanese_name = EXCLUDED.japanese_name,
        evolution_stage = EXCLUDED.evolution_stage,
        evolves_from = EXCLUDED.evolves_from,
        set_code = EXCLUDED.set_code,
        set_number = EXCLUDED.set_number,
        set_name = EXCLUDED.set_name,
        regulation_mark = EXCLUDED.regulation_mark,
        description = EXCLUDED.description
"""


def _guard_url(url: str) -> None:
    if not url:
        raise SystemExit("--db is required (isolated benchmark DB URL)")
    if "/ptcg_db" in url or url.rstrip("/").endswith("ptcg_db"):
        raise SystemExit(f"REFUSING to seed protected production DB: {url}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", required=True, help="Isolated benchmark DB URL (e.g. .../ptcg_bench)")
    args = parser.parse_args()

    _guard_url(args.db)

    # Env MUST be set before importing config/database (config reads env at import).
    os.environ["DATABASE_URL"] = args.db
    os.environ.setdefault("PTCG_ALLOW_WEAK_SECRETS", "1")
    for flag in (
        "ENABLE_JP_DECK_AUTO_UPDATE",
        "ENABLE_LIMITLESS_AUTO_UPDATE",
        "ENABLE_USER_BACKUP",
        "ENABLE_CARD_DB_AUTO_UPDATE",
    ):
        os.environ.setdefault(flag, "0")

    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    import database  # noqa: E402  (import after env is finalised)

    print(f"[seed_bench] initialising schema on {args.db}")
    database.init_db()

    try:
        from services.ai_assistant.indexer import ensure_ai_schema

        ensure_ai_schema()
    except Exception as exc:  # pragma: no cover - schema helpers are best effort
        print(f"[seed_bench] WARNING: ensure_ai_schema failed: {exc}")

    rows = _build_rows()
    conn = database.get_db_connection()
    if not conn:
        raise SystemExit("cannot connect to isolated benchmark DB")
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM cards WHERE card_id LIKE %s", (ID_PREFIX + "%",))
        for row in rows:
            payload = dict(row)
            payload["skills_json"] = json.dumps(row["skills_json"], ensure_ascii=False)
            cursor.execute(INSERT_SQL, payload)
        conn.commit()
        cursor.execute("SELECT card_type, COUNT(*) FROM cards WHERE card_id LIKE %s GROUP BY card_type ORDER BY card_type", (ID_PREFIX + "%",))
        counts = cursor.fetchall()
    finally:
        conn.close()

    print(f"[seed_bench] seeded {len(rows)} cards")
    for row in counts:
        print(f"  - {row['card_type']}: {row['count']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
