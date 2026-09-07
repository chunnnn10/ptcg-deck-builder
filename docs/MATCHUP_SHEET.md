# Matchup Sheet schema

兩層資料，唔好混：

1. **Archetype profile**（每晚 batch）  
   一套環境牌自己嘅 setup / quirks / 印刷輸出線 / HP。
2. **Matchup sheet**（聊天時即場砌）  
   用戶套 × 對手套。引用兩份 profile + 用戶牌表核心卡原文。

## 點解唔全部聊天時現計、亦唔全部 nightly 寫死

| 內容 | 幾時生 | 原因 |
|---|---|---|
| 進化階段、攻擊能量、太晶規則、印刷傷害、HP | nightly profile | 卡唔變就唔使每句 chat 重爬 |
| 用戶而家呢副 vs 某套 | chat-time | 用戶 tech 每副唔同 |
| 含羞苞→300 再蛋白飲、200+130=330 | 可計算或 gold overlay | 單卡印刷對 HP 可算；跨卡組合最好有人標一次 |
| 「節奏慢所以唔一線」 | chat-time 由 setup 展開 | 形容詞本身唔入庫 |

## 你要唔要提供全部主流套斬殺線／血量表？

**唔需要一次過交齊。**

- **血量同印刷招式傷害**：應由卡庫自動出，你唔使抄。
- **單段斬殺**（A 招式傷害 ≥ B HP）：程式可算，你唔使提供。
- **你先至要出手嘅**：跨卡組合、點傷改帶、道具／特性加傷、你在意嘅尷尬數字（120 vs 130、310 vs 炸彈）。  
  呢啲先叫 gold overlay，用呢份 schema 一行一行加。優先寫你常打嘅 10–20 套，唔係全圖鑑。

種子三行（`verified: false`）已經用你講嘅 Mega 水忍蛙對局。上線後要用 `get_card_detail` 對過先把 `verified` 打開。

## Archetype profile

```text
archetype_id
display_name
updated_at
source: limitless | jp_tournament | user_seed
setup:
  evolution_stage
  energy_to_attack
  energy_type
  boost_cost
  search_filter_notes
quirks[]:
  id, rule, applies_to, note
printed_lines[]:
  card, kind, damage, target, cost, condition
hp_table[]:
  card, hp, notes
```

## Matchup sheet

```text
user_archetype
opponent_archetype
verified
setup          # 通常抄用戶套 profile
quirks[]
breakpoints[]:
  defender, hp, modifier, modifier_note, effective_hp
  killed_by[], not_killed_by[]
vs_archetype:
  advantages[]
  disadvantages[]
  kill_lines[]
  tempo_note
```

## 樣本三行（用戶種子，未對卡庫）

### Mega 水忍蛙 vs 多龍

- setup：2 階、2 能量、加傷棄水能量
- quirk：太晶後排防招式穿傷
- breakpoint：310 原血怕主攻+夜巡靈 130
- kill line：水忍蛙 200 + 黑夜魔靈 130 = 330
- tempo：多龍較快開穿傷

### Mega 水忍蛙 vs 路卡

- quirk：含羞苞把 310 推進 300 帶
- breakpoint：有效 300 時路卡+蛋白飲可斬
- tempo：路卡爆發快過 2 階開槍

### Mega 水忍蛙 vs 竹蘭草

- quirk：忍蛙第二招 120 < 羅絲雷朵 130
- breakpoint：含羞苞→300 後，烈咬陸鯊+2 竹蘭羅絲雷朵可返斬
- 尷尬：清唔走 130 引擎體

實作：`backend/services/ai_assistant/matchup_sheet.py`  
Agent 用 tool `get_matchup_sheet`。
