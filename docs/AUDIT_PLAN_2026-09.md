# PTCG Deck Builder — 全面審計與修復計劃 (2026-09)

> 由 5 個 reconnaissance subagent（admin / AI / mobile / security / data-integrity）匯報後整合。
> 每項都有 file:line 證據；詳細原文可向 Agent Zero 索取。

---

## 0. 執行摘要 (TL;DR)

項目有 **5 個獨立的系統性問題群**，而佢哋好多其實係**同一個根因**反覆出現：

1. **Schema drift → 靜默寫入失敗**（同 M6a / sort_order 一模一樣嘅問題）：`jp_cards.set_total` 欄位根本冇建立 → **所有 JP 卡寫入全部失敗但回報成功**。
2. **狀態放喺 process 記憶體**（同部署 504 同源）：AI job store、rate limiter、anon quota 全部 per-worker，而生產跑 2 個 gunicorn worker → 隨機失效。
3. **HTML5 drag-and-drop 主導一切**：手機完全做唔到加卡/刪卡/搬資料夾。
4. **Admin 面板功能爆炸**：66 條 admin route、多個死端點、一個壞咗嘅按鈕、冇 CSRF、冇 last-admin 保護。
5. **AI Agent 架構性死亡**：冇 key、RAG=0 行、冇 CJK 分詞、job 綁 worker、冇 streaming。

**最高危（可即刻被利用 / 已壞）**：
- 🔴 密碼重設 token 直接回傳 API response → **帳號接管 (ATO)**
- 🔴 `jp_cards.set_total` 缺失 → JP 卡庫永遠係空
- 🔴 冇 CSRF → 所有寫入可被跨站偽造
- 🔴 搜尋無 CJK 分詞 + AI 語義搜尋死 → 中文查詢基本失效

---

## 1. 五個工作流發現摘要

### A. Security（hacker subagent）
| 危 | 問題 | 位置 |
|---|---|---|
| HIGH | 重設/驗證 token 經 API 回傳（dev/寄信失敗時） | `routes.py:862-867, 889-895` |
| HIGH | 全站無 CSRF | 全 repo，`app.py:15-21` |
| HIGH | 盲信 `CF-Connecting-IP` → 限流可繞過 | `security.py:29-38` |
| HIGH | 限流/配額 in-memory per-worker | `security.py:3-84` + `--workers 2` |
| MED | Deck IDOR：任何 id 可讀私人牌組 | `routes.py:1590-1606` |
| MED | 牌組 id 用 `random`（可枚舉） | `routes.py:281-283` |
| MED | SSRF：admin `custom_url` 無驗證 | `routes.py:1721-1763`, `crawler.py:1178` |
| MED | 匿名可觸發爬蟲 + DB 寫入 | `routes.py:1765-1774` |
| MED | Host header 注入 reset 連結（SERVER_NAME 未設） | `config.py:79`, `routes.py:880` |
| MED | `SESSION_COOKIE_SECURE` 預設 False | `config.py:76`, `app.py:18` |
| MED | 弱 SECRET_KEY 驗證 | `config.py:65-74` |
| MED | `database.get_workspace_db()` 未定義 → move 功能 500 | `routes.py:4029` |
| LOW | 錯誤 `str(e)` 洩漏內部 | 多處 |
| LOW | `/api/tools/convert-live` 匿名可觸發外部請求 | `routes.py:3245` |
| LOW | 容器以 root 執行；依賴未 pin | `Dockerfile*`, `requirements.txt` |

**False positives 已排除**：未發現 SQL injection（所有用戶值都用 `%s`）；無 unsafe deserialize；`.env` 已 gitignore。

### B. Data integrity（developer subagent）
| # | 問題 | 位置 | 效應 |
|---|---|---|---|
| D1 | `jp_cards.set_total` 只寫不建 → **所有 JP UPSERT 失敗** | `jp_crawler.py:582,613,631`; `limitless_jp_crawler.py:485,515,540`; schema 冇 | 🔴 JP 卡庫永遠空 |
| D2 | 兩個 schema owner 分歧（`init_db.py` vs `database.py`） | `database.py:619-620` | 啟動建的 DB 缺欄位 |
| D3 | `deck_cards.deck_id` 缺 `ON DELETE CASCADE`（database.py 版） | `database.py:782` | 孤兒行 |
| D4 | `cards` 缺 `name`/`set_code` index（runtime schema） | `database.py:622` | 搜尋慢 |
| D5 | `deck_search_index` 冇 UNIQUE 但用 `ON CONFLICT DO NOTHING` | `init_db.py:223`, `database.py:842` | 重複行累積 |
| D6 | `id_mapping` 兩處重複 ALTER | `card_resolver.py:70`, `card_mapping.py:133` | 重複 |
| D7 | `ai_enabled` default 矛盾（0 vs 1） | `database.py:644` vs `migrations/002` | 行為不一 |
| D8 | migrations 從不自動套用，無 tracking table | `app.py:255` | 邏輯層靜默休眠 |
| D9 | `DATABASE_STRUCTURE.md` 係 SQLite 時代文件 | 整份 | 誤導 |
| D10 | `set_number` 格式不一（TW 原樣 vs JP 補零） | `crawler.py:680`, `jp_crawler.py:255` | 對應失敗 |
| S1 | `conn.execute(...)` 用喺 psycopg2 connection（唔存在）→ JP 名補完永遠唔跑 | `crawler.py:990-994` | 靜默死 |
| S2 | 大量 bare `except:` | 多處 | 隱藏錯誤 |
| S4 | `server_meta.json` 從不更新（`save_local_meta` 無 caller） | `crawler.py:65` | check_version 用死數 |
| Q1 | `provisional_cards` 插入 `prov_` 非數字 id → 同幽靈卡同類 | `provisional_cards.py:131` | 再次污染 |
| Q2 | JP `regulation_flags` 硬編 `'Standard'` | `jp_crawler.py:632` | 同 TW 已修 bug 一樣 |
| Q3 | `replace_if_official_match` 每次都 O(cards×decks) 全掃 | `crawler.py:922` | 批量爬蟲極慢 |
| X1 | 三個 crawler 大量重複 | `crawler.py`, `jp_crawler.py`, `limitless_jp_crawler.py` | 維護地獄 |
| X3 | 一批 SQLite 遺留檔 + `crawler_app.py.bak` | `tools/*`, `crawler/*.bak` | 死代碼 |

### C. Admin 面板（developer subagent）
- **66 條 `@admin_required` route**，其中 **約 15 條完全冇前端 caller**（dead endpoints）。
- **`toggleAiAccess` 冇 export** → 撳落去會 `undefined` 報錯（`admin_function.js:390` vs return `1714-1889`）。
- **兩個 JP 爬蟲棧**（legacy `/api/jp/crawler/*` vs `/api/limitless-jp/*`），UI 只用新嘅。
- **三個「牌組管理」**命名混亂（牌組管理 / 匯入牌組管理 / LimitLess 管理）。
- **冇 CSRF、冇 audit log、冇 pagination**（users/decks 全量回傳）。
- **冇 last-admin 保護** → 可以鎖死自己。
- **破壞性操作確認不足**（limitless-meta/reset、card-roles/clear 只有 1 個 confirm；多個長任務零 confirm）。
- `/api/limitless-jp/status`、`/api/jp/crawler/status` **無認證**洩漏內部狀態。
- `/api/limitless-jp/test` 只需 login（非 admin）就可觸發外部請求。

### D. AI Agent（developer subagent）— 「完全是廢的」根因
1. **R1 未設定 + 靜默失敗**：無 `AI_API_KEY`，`ai_settings` 吞異常。
2. **R2 RAG 架構性死亡**：`ai_embeddings` = 0 行；重建係 admin-only；embedding base 跟 chat base（DeepSeek/MiniMax 冇 `/embeddings`）→ 永遠 keyword fallback。
3. **R3 中文搜尋壞**：fallback 用整句 `ILIKE '%query%'`，全 repo **冇 CJK 分詞**。
4. **R4 agent loop 脆弱**：`_chat_anthropic` 直接丟棄 `tools`；base-URL 子串嗅探注入非標準欄位；第一步冇 tool_calls 就結束。
5. **R5 tool 結果被字元截斷**破壞 JSON（`assistant.py:339-343`）。
6. **R6 job store 係 process-local dict**，2 worker 下 ~50% poll 撞錯 worker → 隨機「job not found」。
7. **R7 冇對話狀態、冇 streaming**；前端假進度條（`ai_assistant.js:248-278`）。
8. **R8 deck patch 係 regex NLU + 幻覺 fallback**（`tools.py:882-918`）。
9. **R9 grounding 只係文字指令，冇 citation**。
10. **R10 matchup 只有 3 行 hardcoded seed**。

### E. Mobile（developer subagent）
1. **DnD 係核心操作唯一路徑**，全無 touch fallback（add/reorder/remove/move/reference）。
2. **手機刪卡完全壞**：long-press handler double-fire（`touchstart`+`mousedown`）；刪卡靠 `contextmenu`，iOS Safari 唔會 fire。
3. **Simulation 手機完全入唔到**（`header.html:47` `hidden md:flex`，`openSimModal` 冇其他入口）。
4. **語言 + 標準/自由 toggle 手機入唔到**（`hidden md:flex`），但會影響卡牌合法性。
5. **Context menu 開出畫面外**（無 viewport clamp）。
6. **viewport-fit=cover 冇設** → `.safe-area-bottom` 完全無效；`overflow-hidden` + `vh` → 鍵盤遮住輸入。
7. **手機效能稅**：runtime Tailwind JIT、unpkg Vue、圖片無尺寸無 lazy。
8. **tap target 普遍過細**（20-36px）；50 處 `text-[10px]/[11px]`。

---

## 2. 修復路線圖（分階段，逐步）

> 原則：先修「已壞 + 高危」，再修「架構」，最後做「體驗」。每階段可獨立部署驗證。

### Phase 0 — 止血（P0，全部 S，可即刻做）
目標：移除直接風險與明顯壞功能。
1. 停回傳 reset/verify token（`routes.py:862-895`）。
2. 建立 `jp_cards.set_total` + 一次性 migration（解鎖 JP 卡庫）。
3. 修 `conn.execute` → `cursor.execute`（`crawler.py:990`）。
4. 修 `toggleAiAccess` 未 export（`admin_function.js`）。
5. 修 `database.get_workspace_db()` 未定義（`routes.py:4029`）。
6. JP `regulation_flags` 改用 mark 判定（`jp_crawler.py:632`）。
7. 為 status 端點加 `@admin_required`；`limitless-jp/test` 加 admin。
8. `deck_cards` 補 `ON DELETE CASCADE`；`deck_search_index` 補 UNIQUE + 去重。
9. 加 last-admin 保護；破壞性操作補 confirm。
10. 更新 `server_meta.json`（接 `save_local_meta` 落 crawl 完成）。

### Phase 1 — 安全硬化（P0/P1，S-M）
1. 加 Flask-WTF CSRFProtect，SPA 用 `X-CSRFToken`。
2. `SESSION_COOKIE_SECURE=True`（prod）+ HSTS；設 `SERVER_NAME`。
3. 修 `CF-Connecting-IP` 信任邏輯（配 allow-list）。
4. 限流/配額移去 Postgres/Redis（跨 worker）。
5. Deck IDOR owner 檢查；id 改用 `secrets`。
6. 爬蟲 `custom_url` host allow-list + 私有 IP 封鎖。
7. 強化 `SECRET_KEY` 驗證 + 生成。
8. 錯誤統一唔回 `str(e)`。

### Phase 2 — AI Agent 重建（P1，M-L）
1. Phase 0 可視化：`/api/ai/health`（key/model/embeddings/卡數），degraded 誠實回答。
2. 移除 `ai_settings` 靜默吞異常。
3. job store + 對話狀態改 DB-backed（`ai_threads/ai_messages/ai_jobs`）。
4. RAG：獨立 embedding provider 設定；卡牌 import 自動 enqueue 重建；啟動檢查索引。
5. 中文檢索：`pg_trgm`/`pg_bigm` + pgvector hybrid，取代整句 ILIKE。
6. Agent runtime：provider adapter（Anthropic 要轉 tool_use/tool_result）；結構化輸出 schema 驗證 + 1 次修復；row-based packing 取代字元截斷。
7. SSE streaming + 真步驟；刪前端假進度。
8. Grounding：citation contract + validator。
9. Deck patch 改用 `card_id` diff + 合法性檢查，取代 regex NLU。
10. Matchup：真生成 pipeline 或先移除。

### Phase 3 — Mobile 兼容（P1，S-M）
1. 功能可達性：un-hide Simulation、lang/format toggle、手機 Save；修 `xl:block` vs `md:hidden` 重疊。
2. `viewport-fit=cover` + 真正套用 safe-area；`vh`→`dvh`。
3. 修 long-press double-fire；刪卡改 tap flow（card modal 已有按鈕）。
4. 統一 gesture 抽象（Pointer Events：tap/longPress/drag）取代 HTML5 DnD。
5. 每個 DnD-only 動作補 tap 等價（add/remove/reorder/move-to-folder/reference）。
6. Context menu → 統一 action-sheet（手機 bottom sheet + clamp）。
7. tap target ≥44px utility；`text-[10/11px]`→`text-xs`。
8. 效能：self-host Vue、lazy+尺寸圖片、結果分頁。
9. design tokens（`--tap-target`, `--safe-bottom`, `--modal-max-h`）+ Tailwind `screens.xs`。

### Phase 4 — Admin 整理（P2，M）
1. 刪/接線 dead endpoints（~15 條）。
2. 合併兩個 JP 爬蟲棧；三個牌組管理改名 + 說明。
3. users/decks 加 pagination。
4. Audit log table + UI。
5. 統一 confirmation + 成本警告。
6. Backup/restore + auto-update history UI。

### Phase 5 — Schema/代碼健康（P2/P3，M-L）
1. 單一 schema owner；對齊 `init_db.py`/`database.py`。
2. migrations 自動套用 + `schema_migrations`。
3. `prov_` 卡改用 temp namespace + 擴充 cleanup。
4. `replace_if_official_match` 改查詢式，唔好全掃。
5. 抽三個 crawler 共用模組；刪 SQLite 遺留 + `.bak`；重寫 `DATABASE_STRUCTURE.md`。
6. 加 `cards(name)`/`cards(set_code)` index。

---

## 3. 驗證策略
- 每階段：單元/整合測試 + 真實 DB + 直播站 smoke test。
- Mobile：320/360/390/412/768/1024 六個寬度；重點 iOS Safari。
- AI：golden Q/A per skill（token 數、tool 呼叫、citation、refusal）。
- Security：CSRF/限流/IDOR 針對性回歸。
- 部署後必查：`/api/crawler/status`、`/api/search`、`/api/ai/health`、expansions 含 M6a。

---

## 4. 未驗證 / 待確認
- 部分 subagent 未能連線生產 DB 驗證 runtime 欄位，D1/D2/D3 建議喺 VPS 上 `\d jp_cards` 確認。
- AI 未做真實端到端請求（需 key + 有資料）。
- Admin `submitEditUser` 是否送 `ai_enabled` 待確認。
