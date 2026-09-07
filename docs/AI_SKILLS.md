# AI agent：Skill + Tool（LangChain 形，唔引入套件）

舊流程會喺模型開口前強制：

- 關鍵字判斷「係咪組牌」
- semantic search H/I/J
- 搜 Limitless
- 拉牌表同玩法分析

而家改成最基本 agent loop：

1. 系統提示附 skill catalog
2. 模型可 `list_skills` / `get_skill`
3. 再自己揀 tool、自己停
4. 簡單問題可以 0 tool

## Skills

`plain_chat` `card_lookup` `card_compare` `search_standard_cards` `search_meta` `explain_gameplan` `matchup_analysis` `audit_deck` `patch_deck` `recommend_archetype`

定義喺 `backend/services/ai_assistant/skills.py`。

## Tools

原有：`semantic_search_cards` `get_card_detail` `search_card_roles` `search_meta_decks` `search_japanese_decks_by_card` `get_meta_deck_cards` `summarize_meta_archetype` `analyze_current_deck` `propose_deck_patch`

新增：`list_skills` `get_skill` `get_matchup_sheet`

## 輸出

只有 `recommend_archetype` / `patch_deck` 先要 `decklists` 或 `deck_actions`。  
其餘 skill 交 `answer`（+ cards／matchup）就得。
