# Format Analyst 框架

用而家站內 Limitless DeckList 計佔比，抽出前 20 套，讀核心打手卡文，計傷害線同血量斷點，再把一份壓縮快照交給聊天 Agent。

唔好把 56,690 副全文塞進每一句 chat。庫內而家 `total=56690`，page1 只係最新賽。佔比一定要有時間窗。

## 0. 角色分工

| 角色 | 何時跑 | 輸入 | 輸出 |
|---|---|---|---|
| Format Builder | 每晚／Limitless 更新後 | `limitless_decks` + `limitless_deck_cards` + `cards`／`jp_cards` | `format_snapshot` JSON |
| Format Analyst（離線 LLM） | Builder 之後 | snapshot 草稿 + 核心卡原文 | 補 `quirks`／組合線／tempo_note，但數字唔准改 |
| Chat Agent | 用戶發問 | 用戶牌組 + snapshot 前 20 | 對局表、優缺、斬殺線 |

Builder 係程式。Analyst 只准在已有數字上寫註解。Chat Agent 只准引用 snapshot 同 `get_card_detail`。

## 1. 時間窗同佔比

預設窗：

- `format = standard`
- 賽事日期 ≥ 今日 − 30 日
- 可選：只計 `players >= 60`，避免小屋賽污染

```text
share[archetype] = decks[archetype] / decks[window]
```

另外記：

- `n`：窗內副數
- `events`：窗內賽事數
- `best_placement` / `median_placement`
- `last_seen`

Limitless 已分變體，**唔好合併**：

- 多龍巴魯托
- 多龍巴魯托 黑夜魔靈
- 多龍巴魯托 火焰雞
- N的索羅亞克
- 火箭隊的超夢

用戶點名邊個名，就用邊個 archetype 字串。

取 `ORDER BY n DESC` 前 20。第 21 名以後只留名稱同 %，唔養傷害線。

## 2. 每套要抽出嘅資料（Archetype Profile）

```text
archetype_id          # slug
display_name          # EN Limitless 名
display_name_zh
window                # {from, to, format, min_players}
share                 # 0.000–1.000
n
median_placement
sample_deck_ids[]     # 最多 3 副代表表（最近 + 最好名次）

setup:
  evolution_stage     # 由核心寶可夢 sub_type 多數決
  energy_to_attack    # 主打手最常用招式能量數
  energy_type
  boost_cost          # 卡文寫明嘅加傷代價（棄能量等），無則 null
  search_filter_notes

core_line[]:
  name, card_id, typical_count {p25,p50,p75}, stage, hp

attackers[]:          # 窗內代表表出現、且有傷害數字或特性傷
  name, card_id, hp, stage, tera
  printed_lines[]:
    kind              # attack | ability
    name
    damage            # int；可變傷害先標 null + formula
    target            # active | bench | both | self
    cost
    condition
    source_span       # 卡文原句

hp_table[]:
  name, hp, role      # attacker | engine | tank | tech
```

`typical_count` 用窗內該 archetype 全部牌表計分位，唔用人手估。

傷害數字只准由 `skills_json`／`description` parse。parse 唔到就 `damage=null`，留原句。

## 3. 環境打擊帶（Attack Band Table）

前 20 套全部 `printed_lines.damage` ∪ 全部 `hp_table.hp` 做一個集合。

```text
bands[]:
  value               # 例如 120, 130, 200, 300, 310, 330
  produced_by[]       # {archetype, card, line}
  survived_by[]       # HP > value 嘅環境常見體
  killed[]            # HP <= value
```

組合線（200+130）**唔**由 Builder 自動發明。只准兩種來源：

1. 離線 Analyst 提出，且兩段數字都已在 `printed_lines`
2. 用戶 gold overlay

Chat 可以做加法，但必須寫公式，而且兩段都要喺 snapshot。

## 4. Matchup Sheet（聊天時砌）

用戶套 × snapshot 其中一套：

```text
user_archetype
opponent_archetype
verified              # 雙方 printed_lines 都對過 card_id
setup                 # 抄用戶套
quirks[]
breakpoints[]:
  defender, hp, modifier, effective_hp
  killed_by[], not_killed_by[]
vs_archetype:
  advantages[], disadvantages[], kill_lines[], tempo_note
```

Chat 輸出順序固定：setup → quirks → breakpoints → vs。形容詞只准指上面某行。

## 5. 塞給 Chat 嘅上下文有幾大

每一轉最多帶：

- snapshot header：窗、前 20 名同 %
- 用戶牌組核心 ≤ 12 張卡原文
- 對手套 profile 1 份（或用戶點名嘅 2–3 份）
- 相關 bands 列

禁止每轉附 20 份完整 60 張表。要具體牌表先 `get_meta_deck_cards(sample_deck_id)`。

## 6. 你要唔要提供斬殺線／血量表？

唔需要交齊 20 套。

| 資料 | 邊個出 |
|---|---|
| 佔比、代表牌表、典型張數 | Limitless 統計 |
| HP、印刷傷害、能量、太晶、階段 | 卡庫 |
| 單段 A 傷害 ≥ B HP | Builder 算 |
| 跨卡組合、點傷改帶、道具加傷 | 你可選提供 gold；否則 Analyst 標未驗證 |
| 「節奏慢」 | 由 setup 展開，唔當獨立事實 |

你只需要為你常打、而且印刷傷害解釋唔到嘅對局加 gold，例如：

- 含羞苞把 310→300
- 蛋白飲、2 羅絲雷朵
- 黑夜魔靈特性 130 + 主攻

## 7. 工具（下一刀實作）

```text
list_format_snapshot()          # 前 20 名同 %
get_archetype_profile(name)     # 一套 profile
get_attack_bands(hp?)           # 打擊帶
get_matchup_sheet(user, opp)    # 即場砌
get_meta_deck_cards(deck_id)    # 已有
get_card_detail(card_id)        # 已有
```

Builder 寫入 `format_snapshots` 表（一窗一列 JSONB）。舊窗保留，chat 用 `latest`。

---

# 離線 Format Analyst 完整提示詞

把下面整段當 system。User 訊息只准係 JSON：`profiles` + `card_texts` + 可選 `gold_overlays`。

```text
你是 PTCG Standard Format Analyst。你只註解程式已抽出的 Limitless 前 20 套資料，不發明卡牌效果與數字。

硬限制
- 傷害、HP、能量、階段、太晶、佔比、張數只能引用輸入 JSON。
- 輸入寫 damage=null 的招式，不准估一個整數。
- 組合線只有在兩段 printed_lines 都有整數時才能寫成公式，例如 200+130=330。
- 不准合併不同 archetype 名稱。Dragapult 與 Dragapult Dusknoir 是兩套。
- 不准用訓練記憶補「而家環境一定係點」。環境以 snapshot.window 為準。
- 輸出必須是一個 JSON 物件，不要 markdown。

任務
為每個 archetype 補這些欄，不准改已有數字欄：
- quirks[]: {id, rule, applies_to, note}
  只描述輸入卡文已出現的規則盒或代價（太晶後排、棄能量加傷、特性自昏）。
- combo_lines[]: {pieces[], formula, damage, note, verified}
  verified=true 僅當公式每個加數都來自 printed_lines.damage。
- tempo_note: 一句，必須點名 setup.evolution_stage 與 energy_to_attack。
- vs_field_notes[]: {vs, advantage, disadvantage, breakpoint_hint}
  vs 只能是今次輸入裡的其他 archetype 名。breakpoint_hint 只能引用兩邊 hp_table 或 printed_lines。

寫作規則
- 用繁體中文。
- 「節奏慢」只能寫成「2階 + 2能量 + 棄能加傷」，不能單獨當結論。
- 若樣本 n < 8，vs_field_notes 標 sample_thin=true。
- 不要輸出 60 張牌表。
- 不要建議禁牌或預測下一週 meta。

輸出形狀
{
  "window": {"from": "", "to": "", "format": "standard"},
  "archetypes": [
    {
      "display_name": "",
      "quirks": [],
      "combo_lines": [],
      "tempo_note": "",
      "vs_field_notes": []
    }
  ],
  "unresolved": ["哪些卡文缺傷害數字或對唔到 card_id"]
}
```

User 訊息模板：

```text
{
  "task": "annotate_format_snapshot",
  "window": {"from": "2026-08-08", "to": "2026-09-07", "format": "standard", "min_players": 60},
  "profiles": [ /* Builder 產出的 20 份 profile，含 share/n/setup/attackers/hp_table */ ],
  "card_texts": [
    {"card_id": "", "name": "", "hp": 0, "sub_type": "", "skills": [], "description": ""}
  ],
  "gold_overlays": [
    {
      "user_archetype": "mega_greninja",
      "opponent_archetype": "dragapult",
      "kill_lines": ["200+130=330"],
      "notes": "可選；Analyst 只能在數字已存在於 card_texts 時採用"
    }
  ]
}
```

---

# 聊天 Agent 附加提示詞

加在而家 skill catalog 之後。有 snapshot 先生效。

```text
當用戶問環境、對局、斬殺、點解某套一線／唔一線：
1. 用 list_format_snapshot 看前 20 同 %，不要靠記憶。
2. 認用戶套：@tab 或用戶講嘅 archetype。認唔到就問一句，不要默認推薦新套。
3. 只拉對手套 get_archetype_profile，不要拉齊 20 份。
4. 先列 setup / quirks / breakpoints，再寫 vs。
5. 任何組合傷害寫公式。缺 printed damage 就講未驗證。
6. 唔好因為用戶提到「牌組」就去 recommend_archetype。
7. 用戶套核心卡若唔在 snapshot，get_card_detail 後即場比 HP／傷害，但佔比欄寫「非前20，無 share」。
```

## 8. 落地順序

1. 已完成：Chat Agent 改為自行揀 Skill／Tool。
2. 下一刀：SQL 計 30 日佔比 + 前 20 + 代表牌表。
3. 再下一刀：對代表表核心寶可夢 parse `skills_json` 出 printed_lines／hp_table。
4. 再下一刀：存 `format_snapshots`，掛 `list_format_snapshot`／`get_archetype_profile`。
5. 先用 3 行 gold（忍蛙 vs 多龍／路卡／竹蘭草）試 Chat 輸出形。
6. 先至考慮離線 Analyst 補 quirks。

未做 2–4 之前，唔好把 56,690 副或 20 份完整牌表直接灌進 chat。
