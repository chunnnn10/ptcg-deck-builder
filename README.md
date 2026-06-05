# Chun Deck Builder

> 寶可夢集換式卡牌遊戲 (PTCG) 牌組構築與模擬測試工具

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.x-green.svg)
![Vue.js](https://img.shields.io/badge/Vue.js-3.x-brightgreen.svg)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue.svg)
![Docker](https://img.shields.io/badge/Docker-✓-2496ED.svg)

## 📖 目錄

- [功能概述](#功能概述)
- [技術架構](#技術架構)
- [專案結構](#專案結構)
- [快速開始](#快速開始)
- [API 文件](#api-文件)
- [資料庫結構](#資料庫結構)
- [環境變數](#環境變數)
- [更新日誌](#更新日誌)

---

## 功能概述

### 核心功能
- **卡牌搜尋**：支援按名稱、ID、類別、屬性、進化階段過濾搜尋
- **牌組構築**：拖曳式介面 (桌面版) / 點擊式操作 (手機版)，支援標準模式 (60張) 與自定義模式
- **起手模擬**：蒙地卡羅模擬 + 超幾何分佈精確數學計算，計算 Mulligan 與獎賞卡機率
- **獎賞分析**：設定關鍵卡，分析同時落入獎賞區的機率
- **牌組分享**：一鍵生成分享連結，支援公開/私密設定
- **PTCG Live 格式轉換**：將本地牌組導出為 PTCG Live 可匯入的字串格式

### 📱 移動端優化
- **響應式設計**：介面自動適配桌面與手機螢幕
- **底部導航欄**：手機版專屬導航，快速切換搜尋與牌組介面
- **觸控友善**：優化卡片點擊操作，長按進入多選模式
- **抽屜式側邊欄**：手機上自動收合工作區，極大化操作空間

### 🆕 工作區系統 (VS Code 風格)
- **📁 資料夾分類**：自由建立多層資料夾組織牌組
- **💾 自動儲存**：編輯後 2 秒自動儲存至伺服器
- **⏱️ 時光機 (Timeline)**：支援最多 50 步 **撤銷 (Undo)** 與 **重做 (Redo)**，`Ctrl+Z` / `Ctrl+Y` 快捷鍵
- **🖱️ 右鍵選單**：重新命名、刪除、移動、公開分享、批量刪除選取卡片
- **📤 日本牌組導入**：從 ptcgtw.shop 自動爬取日本大賽牌組，支援關聯度/日期排序，導入時自動偵測工作區狀態

### 🆕 管理員功能 (Admin Panel)
- **卡牌資料更新**：從 asia.pokemon-card.com 爬取最新卡牌，支援指定擴充包與賽制過濾，可選日文名稱同步
- **用戶管理面板**：查看用戶狀態、手動驗證、角色升降、刪除用戶
- **牌組管理**：編輯/公開/私密/刪除任何用戶的牌組

---

## 技術架構

| 層 | 技術 | 說明 |
|---|---|---|
| **後端框架** | Python Flask 2.x | RESTful API，Jinja2 模板渲染 SPA 入口 |
| **前端** | Vue.js 3 (CDN) + Tailwind CSS | 無建構工具，`[[ ]]` 分隔符避免與 Jinja2 衝突 |
| **主資料庫** | PostgreSQL 16 | Docker 容器化，psycopg2 驅動 |
| **遺留/工具資料庫** | SQLite 3 | `tools/` 獨立腳本仍使用 SQLite |
| **認證** | Flask-Login + Werkzeug | 密碼雜湊、Email 驗證 (SMTP)、角色權限 (admin/user) |
| **部署** | Docker Compose | PostgreSQL + Flask 雙容器 |
| **爬蟲** | requests + BeautifulSoup4 | 多線程並行下載卡牌圖片與資料 |
| **AI 整合** | GoModel / OpenRouter | LLM 生成卡牌遊戲邏輯 JSON (實驗性) |

### 前端模組架構

```
Vue 3 createApp (app.js)
├── useAuth()              ← auth.js      認證狀態
├── useDeckManager()       ← deck_manager.js  牌組核心狀態、undo/redo、選取
├── useCardManager()       ← card_manager.js  卡牌搜尋、詳情、新增表單
├── useSimulation()        ← simulation.js    蒙地卡羅 + 超幾何分佈
├── useWorkspace()         ← workspace.js     樹狀工作區 CRUD、自動儲存
├── useIOManager()         ← io_manager.js    導入/導出/分享/日本牌組庫
└── useAdminUpdate()       ← admin_function.js 管理員面板
```

每個模組以 `use*()` 工廠函數匯出，在 `app.js` 的 `setup()` 中組裝並注入依賴。

---

## 專案結構

```
.
├── backend/                        # Flask 後端
│   ├── app.py                      # 入口：Flask 初始化、LoginManager、背景 DeckImporter 線程
│   ├── config.py                   # 環境變數、路徑、SMTP、爬蟲 headers
│   ├── database.py                 # PostgreSQL 連線、CRUD 工具、init_db() schema 建立
│   ├── models.py                   # User 模型 (Flask-Login、密碼雜湊、驗證 token)
│   ├── routes.py                   # 全部 API 路由 (~700 行，含 @admin_required 裝飾器)
│   ├── init_db.py                  # 獨立資料庫初始化腳本
│   └── services/
│       ├── crawler/
│       │   ├── crawler.py          # 主爬蟲：多線程爬取 asia.pokemon-card.com
│       │   ├── crawler_app.py      # 獨立 Flask GUI 版爬蟲
│       │   ├── update_pokemon.py   # 增強爬蟲：日文名稱/進化資訊 (SQLite)
│       │   └── update_japanese_name.py  # ptcgsp.com API 日文資料同步
│       └── deck_importer/
│           └── deck_importer.py    # 日本牌組導入：從 ptcgtw.shop 爬取大賽牌組
│
├── frontend/                       # 靜態前端 (無建構)
│   ├── favicon.ico
│   ├── html/
│   │   ├── index.html              # SPA 入口 (Vue 3 mount point)
│   │   └── partials/               # Jinja2 模板片段
│   │       ├── header.html         # 頂部導覽：搜尋欄、篩選器、認證按鈕
│   │       ├── workspace.html      # 左側 VS Code 風格工作區側邊欄
│   │       ├── main_panel.html     # 主面板：搜尋結果 + 牌組編輯區
│   │       ├── overlay.html        # 覆蓋層
│   │       └── modals/             # 彈窗模板
│   │           ├── auth.html       # 登入/註冊
│   │           ├── simulation.html # 模擬器 (蒙地卡羅 + 數學模式)
│   │           ├── admin.html      # 管理員面板
│   │           ├── cards.html      # 卡牌詳情 + 新增卡牌表單
│   │           └── io.html         # 導入/導出/日本牌組庫
│   ├── js/
│   │   ├── app.js                  # Vue 3 root component，組裝所有模組
│   │   ├── auth.js                 # useAuth() — 登入/註冊/登出/會話檢查
│   │   ├── deck_manager.js         # useDeckManager() — 牌組狀態、undo/redo、多選、右鍵選單
│   │   ├── card_manager.js         # useCardManager() — 搜尋、過濾、詳情、技能編輯器
│   │   ├── simulation.js           # useSimulation() — 蒙地卡羅 + 超幾何分佈
│   │   ├── workspace.js            # useWorkspace() — 樹狀工作區、拖曳、自動儲存
│   │   ├── io_manager.js           # useIOManager() — 導入/導出/分享/PTCG Live 轉換
│   │   └── admin_function.js       # useAdminUpdate() — 爬蟲更新、用戶/牌組管理
│   └── css/
│       └── style.css               # 全域樣式、scrollbar、context menu
│
├── tools/                          # 獨立工具腳本 (非 Web 部分)
│   ├── main.py / main_gui.py       # 桌面 GUI 應用
│   ├── bridge_manager.py           # 與 pokemontcg.io API 比對，填充英文 ID/名稱
│   ├── limitless_db_crawler.py     # 從 Limitless TCG 爬取英文牌組資料
│   ├── limitless_lab.py            # Limitless 實驗腳本
│   ├── db_logic.py                 # AI 卡牌邏輯處理
│   ├── api_manager.py              # LLM API 管理 (GoModel/OpenRouter)
│   ├── db_migration.py             # 資料庫遷移
│   ├── translation_lab.py          # 翻譯實驗
│   ├── create_english_db.py        # 建立英文卡牌資料庫
│   ├── RarityFinder.py             # 稀有度查找
│   ├── update_schema.py            # Schema 更新
│   └── add_gold_energy.py          # 一次性資料修復腳本
│
├── data/                           # 資料目錄
│   ├── pokemon_card_database.db    # SQLite 主卡牌庫 (遺留)
│   ├── english_card_database.db    # 英文卡牌資料庫
│   ├── imported_decks.db           # 導入的日本牌組 (SQLite 遺留)
│   ├── ptcg_ai_tool.db             # AI 邏輯處理記錄
│   ├── users.db                    # 用戶資料庫 (SQLite 遺留)
│   ├── master_schema.json          # AI 邏輯 schema 定義
│   ├── pokemon_translations.json   # 翻譯快取
│   ├── deck_json_exports/          # 22,000+ 牌組 JSON (每個一副)
│   ├── images/                     # 2,000+ 卡牌圖片 (twXXXXXXXX.png)
│   └── pgdata/                     # PostgreSQL 持久化資料
│
├── 參考資料/                       # 參考檔案
│   ├── official_hk.py              # 香港官網爬蟲參考
│   └── pokemon-asia-pokemon.html   # 官網 HTML 範本
│
├── docker-compose.yml              # Docker Compose (PostgreSQL + Flask)
├── Dockerfile                      # Flask 容器映像
├── requirements.txt                # Python 依賴
├── .env                            # 環境變數 (SECRET_KEY, SMTP, API keys)
├── DATABASE_STRUCTURE.md           # 詳細資料庫 Schema 文件
└── README.md                       # 本文件
```

---

## 快速開始

### 前置需求
- Docker & Docker Compose
- 或 Python 3.10+ + PostgreSQL 16 (本機開發)

### Docker 部署 (推薦)

```bash
# 1. 設定環境變數
cp .env.example .env   # 編輯 .env 填入 SMTP 等設定

# 2. 啟動
docker-compose up -d

# 3. 訪問
open http://localhost:5000
```

服務啟動後：
- Flask 監聽 `0.0.0.0:5000`
- PostgreSQL 監聽 `5432` (僅容器內部)
- 背景線程每 24 小時自動檢查日本牌組更新

### 本機開發

```bash
# 1. 安裝依賴
pip install -r requirements.txt

# 2. 確保 PostgreSQL 運行中，設定 DATABASE_URL
export DATABASE_URL="postgresql://ptcg:ptcg_secret@localhost:5432/ptcg_db"

# 3. 啟動 Flask
python backend/app.py
# Flask debug mode，預設 http://localhost:5000
```

### 管理員帳號
第一個註冊的使用者自動成為管理員。之後可透過 Admin Panel 管理其他用戶。

---

## API 文件

Base URL: `http://localhost:5000`

### 認證

| 方法 | 路由 | 說明 | 認證 |
|---|---|---|---|
| `POST` | `/api/auth/register` | 註冊 (`username`, `email`, `password`) | - |
| `POST` | `/api/auth/login` | 登入 (`username`, `password`) | - |
| `POST` | `/api/auth/logout` | 登出 | Session |
| `GET` | `/api/auth/user` | 取得當前用戶資訊 | - |
| `GET` | `/verify/<token>` | Email 驗證連結 | - |

### 卡牌

| 方法 | 路由 | 說明 |
|---|---|---|
| `GET` | `/api/search?q=&type=&element=&stage=` | 搜尋卡牌 (支援多重過濾，limit 50) |
| `POST` | `/api/cards/batch` | 批量查詢 (`{"ids": [...]}`) |
| `POST` | `/api/card/add` | 新增卡牌 (multipart form, 🔒 admin) |

**搜尋過濾參數：**
- `q` — 關鍵字 (名稱/ID/圖片檔名)
- `type` — `Pokémon`, `Trainer`, `Energy`, `Item`, `Pokémon Tool`, `Supporter`, `Stadium`
- `element` — `Grass`, `Fire`, `Water`, `Lightning`, `Psychic`, `Fighting`, `Darkness`, `Metal`, `Dragon`, `Colorless`
- `stage` — `Basic`, `Stage 1`, `Stage 2`, `VMAX`, `VSTAR`

### 牌組

| 方法 | 路由 | 說明 |
|---|---|---|
| `POST` | `/api/deck/save` | 儲存牌組 (`name`, `deck`, `is_public`) |
| `GET` | `/api/deck/<id>` | 取得牌組內容 |
| `GET` | `/api/decks/public?q=` | 公開牌組列表 |

### 工作區 (🔒 需登入)

| 方法 | 路由 | 說明 |
|---|---|---|
| `GET` | `/api/workspace` | 取得使用者完整工作區樹 |
| `POST` | `/api/workspace/item` | 建立項目 (`name`, `type`, `parent_id`, `content`) |
| `GET` | `/api/workspace/item/<id>` | 取得項目詳細內容 |
| `PUT` | `/api/workspace/item/<id>` | 更新項目 (`name` / `content` / `parent_id`) |
| `DELETE` | `/api/workspace/item/<id>` | 刪除項目 (含子項目) |
| `POST` | `/api/workspace/item/<id>/move` | 移動到新父資料夾 |
| `POST` | `/api/workspace/item/<id>/publish` | 公開分享牌組 |

### 日本牌組

| 方法 | 路由 | 說明 |
|---|---|---|
| `GET` | `/api/decks/japanese/list?q=&sort=&page=` | 日本牌組列表 (含動態寶可夢標籤) |
| `GET` | `/api/decks/japanese/<code>` | 取得牌組完整 JSON |

- `sort`: `match_count` (關聯度優先) 或 `date` (日期優先)

### 爬蟲管理 (🔒 admin)

| 方法 | 路由 | 說明 |
|---|---|---|
| `GET` | `/api/admin/check_version` | 檢查官網與本地版本差異 |
| `GET` | `/api/crawler/expansions` | 取得擴充包列表 |
| `POST` | `/api/crawler/start` | 啟動更新 (可指定 `target_expansion_codes`, `target_regulations`, `update_japanese`, `skip_images`) |
| `GET` | `/api/crawler/status` | 取得更新進度與日誌 |

### 管理員 (🔒 admin)

| 方法 | 路由 | 說明 |
|---|---|---|
| `GET` | `/api/admin/users` | 用戶列表 |
| `POST` | `/api/admin/users/role` | 變更用戶角色 (`user_id`, `role`) |
| `POST` | `/api/admin/users/verify` | 手動驗證用戶 (`user_id`) |
| `POST` | `/api/admin/users/delete` | 刪除用戶 (`user_id`) |
| `GET` | `/api/admin/decks?q=&all=` | 牌組管理列表 |
| `PUT` | `/api/admin/deck/<id>` | 編輯牌組 (`name`, `is_public`) |
| `DELETE` | `/api/admin/deck/<id>` | 刪除牌組 |

### 工具

| 方法 | 路由 | 說明 |
|---|---|---|
| `POST` | `/api/tools/convert-live` | 將牌組轉換為 PTCG Live 匯入格式 |

---

## 資料庫結構

本專案使用 **PostgreSQL 16** 作為主要資料庫（Web 應用），部分獨立工具仍使用 SQLite。

### PostgreSQL 核心表 (由 `backend/database.py` 的 `init_db()` 建立)

| 表名 | 說明 |
|---|---|
| `cards` | 卡牌主表 (20+ 欄位，含中日英文名稱、技能 JSON、系列資訊) |
| `decks` | 用戶分享的公開牌組 |
| `users` | 用戶帳號 (username, email, password_hash, role, is_verified) |
| `user_workspace` | 工作區樹狀結構 (folder/deck，含 parent_id 遞迴) |
| `expansion_sets` | 官方擴充包代碼與名稱 |
| `imported_decks` | 日本大賽牌組中繼資料 |
| `deck_cards` | 牌組-卡牌關聯表 (含數量) |
| `id_mapping` | 外部卡牌 ID ↔ 本地 card_id 對照 |
| `processed_cards` | AI 生成的卡牌遊戲邏輯 JSON |
| `schema_changes` | AI 邏輯 schema 變更記錄 |
| `api_logs` | LLM API 呼叫日誌 |

### SQLite 遺留資料庫 (tools/ 使用)

| 檔案 | 說明 |
|---|---|
| `data/pokemon_card_database.db` | 舊版主卡牌庫 |
| `data/english_card_database.db` | 英文卡牌資料庫 |
| `data/imported_decks.db` | 舊版導入牌組 |
| `data/ptcg_ai_tool.db` | AI 邏輯處理記錄 |
| `data/users.db` | 舊版用戶資料庫 |

> 詳細欄位定義、JSON 結構範例，請參閱 [DATABASE_STRUCTURE.md](DATABASE_STRUCTURE.md)

---

## 環境變數

`.env` 檔案中的關鍵設定：

| 變數 | 說明 | 預設值 |
|---|---|---|
| `SECRET_KEY` | Flask session 加密金鑰 | 必須設定 |
| `SECURITY_PASSWORD_SALT` | 密碼雜湊鹽 | 必須設定 |
| `DATABASE_URL` | PostgreSQL 連線字串 | `postgresql://ptcg:ptcg_secret@localhost:5432/ptcg_db` |
| `MAIL_SERVER` | SMTP 伺服器 | `smtp.gmail.com` |
| `MAIL_PORT` | SMTP 埠號 | `587` |
| `MAIL_USE_TLS` | 啟用 STARTTLS | `true` |
| `MAIL_USERNAME` | SMTP 帳號 | - |
| `MAIL_PASSWORD` | SMTP 密碼 | - |
| `MAIL_DEFAULT_SENDER` | 寄件人地址 | - |
| `GOMODEL_KEY` | GoModel API Key (AI 邏輯生成) | - |
| `OPENROUTER_KEY_*` | OpenRouter API Keys (備用) | - |

---

## 更新日誌

### 2026-02-10 (Mobile & Features Update)

#### 📱 手機版全適配
- **介面重構**：將桌面版的分割視窗改為手機版的分頁標籤 (Tabs) 設計
- **操作優化**：手機上點擊牌組卡片會彈出「移除/新增」面板，取代拖曳操作
- **Bottom Navigation**：新增手機版底部導航列
- **模擬器優化**：手機版模擬器改為分頁顯示，並縮小卡片尺寸以適配螢幕

#### 🔎 搜尋功能升級
- **多重過濾**：搜尋欄新增篩選器，支援依據類別、屬性、進化階段過濾

#### ⏱️ 編輯體驗升級
- **撤銷/重做 (Undo/Redo)**：牌組編輯紀錄每一步操作，最多 50 步歷史
- **快捷鍵支援**：`Ctrl+Z` (撤銷) 與 `Ctrl+Y` (重做)

#### 🇯🇵 日本牌組功能增強
- **智能導入**：自動偵測工作區狀態，提示「儲存並關閉」
- **標籤視覺化**：顯示數量最多的前 3 隻寶可夢及其數量
- **排序功能**：支援依關聯度優先或日期優先排序
