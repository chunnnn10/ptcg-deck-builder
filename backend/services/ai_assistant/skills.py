"""LangChain-style skill catalog.

Skills are playbooks, not hardcoded pipelines. The agent reads the catalog,
picks one skill, then calls tools itself. Python does not pre-run Limitless
or semantic search unless the model asks.
"""

from __future__ import annotations

from typing import Any


SKILLS: list[dict[str, Any]] = [
    {
        "name": "plain_chat",
        "description": "簡單規則、閒聊、或已經有足夠上文嘅短問答。",
        "when": "用戶唔係搵卡、唔係改牌、唔係要牌表或對局分析。",
        "tools": [],
        "flow": ["可以直接答就答", "只有提到一張具體卡先先 get_card_detail"],
        "stop": "一句答案就夠；唔好搜 meta、唔好交 decklists。",
        "output": "answer only",
    },
    {
        "name": "card_lookup",
        "description": "讀一張或幾張卡嘅正確效果、HP、費用、規則盒。",
        "when": "用戶問「呢張卡做咩／幾多血／係唔係太晶」。",
        "tools": ["semantic_search_cards", "get_card_detail"],
        "flow": [
            "用名搜卡或直接 get_card_detail",
            "只引用工具返回嘅原文",
            "需要用法時再 get_matchup_sheet 或 search_card_roles",
        ],
        "stop": "效果講完就停。",
        "output": "answer + cards",
    },
    {
        "name": "card_compare",
        "description": "比較兩張功能卡（抽牌、檢索、填能、干擾）。",
        "when": "用戶問揀邊張、分別喺邊。",
        "tools": ["semantic_search_cards", "get_card_detail", "search_card_roles"],
        "flow": ["resolve 兩張卡", "讀效果／role", "對代價同觸發條件", "需要賽場張數先 search_meta_decks"],
        "stop": "對完就停，除非用戶要職業套樣本。",
        "output": "answer + cards",
    },
    {
        "name": "search_standard_cards",
        "description": "喺 H/I/J 標準卡池用自然語言或條件搵卡。",
        "when": "用戶要「唔棄手嘅抽牌」「HP<=90 嘅基礎」等。",
        "tools": ["semantic_search_cards", "search_card_roles", "get_card_detail"],
        "flow": [
            "功能問法優先 search_card_roles",
            "其餘 semantic_search_cards",
            "重要卡再 get_card_detail 核對原文",
        ],
        "stop": "有可用名單就停。",
        "output": "answer + cards",
    },
    {
        "name": "search_meta",
        "description": "查 Limitless 近期套、名次、樣本牌表。",
        "when": "用戶問而家環境、某 archetype 職業表、某卡出現喺邊套。",
        "tools": ["search_meta_decks", "get_meta_deck_cards", "summarize_meta_archetype", "search_japanese_decks_by_card"],
        "flow": [
            "search_meta_decks 或 search_japanese_decks_by_card",
            "用戶要具體 60 張先 get_meta_deck_cards",
            "要共通牌先 summarize_meta_archetype",
        ],
        "stop": "有證據就答；冇命中就講未索引到。",
        "output": "answer + meta_references + 可選 decklists",
    },
    {
        "name": "explain_gameplan",
        "description": "解釋一套牌點打：展開、輸出線、能量、防護。",
        "when": "用戶 @tab、貼牌表、或問某套點玩。",
        "tools": ["analyze_current_deck", "get_card_detail", "get_matchup_sheet", "search_meta_decks"],
        "flow": [
            "有牌表先 analyze_current_deck",
            "讀核心卡原文",
            "有 brief／matchup sheet 就引用",
            "按 setup / quirks / 輸出線寫，唔好只交形容詞",
        ],
        "stop": "四段寫完就停：點打、爆發線、成本、防護。",
        "output": "answer；除非用戶要，否則唔好交完整 decklists",
    },
    {
        "name": "matchup_analysis",
        "description": "用戶套 vs 環境套：優缺、規則盒、血量斷點、斬殺線。",
        "when": "用戶問對局、斬殺、點解怕某套、節奏快慢。",
        "tools": ["get_matchup_sheet", "analyze_current_deck", "get_card_detail", "search_meta_decks", "get_meta_deck_cards"],
        "flow": [
            "認用戶套同對手套",
            "get_matchup_sheet；有種子就用，並標明 verified 狀態",
            "缺數字就讀雙方核心卡 HP／招式",
            "輸出固定：setup、quirks、breakpoints、vs_archetype",
        ],
        "stop": "列出可核對數字後先寫分析句。冇證據嘅斬殺線要標未驗證。",
        "output": "answer + cards；可附 matchup_sheet",
    },
    {
        "name": "audit_deck",
        "description": "檢查牌組結構問題：張數、進化線、能量、consistency。",
        "when": "用戶問呢副差啲乜、穩唔穩。",
        "tools": ["analyze_current_deck", "get_card_detail", "search_card_roles", "search_meta_decks"],
        "flow": ["analyze_current_deck", "對住核心卡原文指出缺口", "先提出問題，唔好偷偷改牌"],
        "stop": "審計完成。用戶要改先轉 patch_deck。",
        "output": "answer + cards",
    },
    {
        "name": "patch_deck",
        "description": "提出加減牌草案，等用戶確認。",
        "when": "用戶明確要改牌、換卡、補引擎。",
        "tools": ["analyze_current_deck", "semantic_search_cards", "get_card_detail", "propose_deck_patch", "search_meta_decks"],
        "flow": ["分析現況", "用工具找到可驗證替代卡", "propose_deck_patch", "解釋每一張點解"],
        "stop": "交 deck_actions／deck_diff 就停，唔好當已套用。",
        "output": "answer + deck_actions + deck_diff + cards",
    },
    {
        "name": "format_field",
        "description": "讀而家 Limitless 窗內前 20 佔比同環境打擊帶。",
        "when": "用戶問而家環境有邊套、佔比、一線係邊啲。",
        "tools": ["search_meta_decks", "summarize_meta_archetype", "get_matchup_sheet"],
        "flow": [
            "用 Limitless 搜近期套，不要用模型記憶報佔比",
            "未有 format_snapshot 工具前，只講樣本同名次，不要假裝有精確 %",
            "用戶要對局先轉 matchup_analysis",
        ],
        "stop": "列出可見套同來源就停。",
        "output": "answer + meta_references",
    },
    {
        "name": "recommend_archetype",
        "description": "推薦一套並交具體牌表。",
        "when": "用戶要推薦套、組一套、要 60 張。",
        "tools": ["search_meta_decks", "get_meta_deck_cards", "summarize_meta_archetype", "semantic_search_cards"],
        "flow": [
            "先搜 Limitless／日本賽",
            "讀至少一份具體牌表",
            "解釋點打同點解揀呢套",
            "完整 60 張只放 decklists",
        ],
        "stop": "有具體牌表或明確講庫內冇呢套。",
        "output": "answer + decklists + meta_references",
    },
]


def list_skills() -> list[dict[str, Any]]:
    return [
        {
            "name": skill["name"],
            "description": skill["description"],
            "when": skill["when"],
            "tools": list(skill.get("tools") or []),
        }
        for skill in SKILLS
    ]


def get_skill(name: str) -> dict[str, Any] | None:
    key = str(name or "").strip()
    for skill in SKILLS:
        if skill["name"] == key:
            return skill
    return None


def skill_catalog_text() -> str:
    lines = ["Available skills:"]
    for skill in SKILLS:
        tools = ", ".join(skill.get("tools") or []) or "(none)"
        lines.append(f"- {skill['name']}: {skill['description']} | when: {skill['when']} | tools: {tools}")
    return "\n".join(lines)
