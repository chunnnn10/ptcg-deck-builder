# PTCG Deck Builder

> 寶可夢卡牌牌組構築工具 / A fan-made Pokemon TCG deck builder.

![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)
![Flask](https://img.shields.io/badge/Flask-2.x-green.svg)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16-blue.svg)
![Docker](https://img.shields.io/badge/Docker-ready-2496ED.svg)

PTCG Deck Builder is a web-based deck building and research tool for Pokemon TCG players. It focuses on fast card search, deck editing, workspace management, opening-hand simulation, and AI-assisted card lookup.

PTCG Deck Builder 係一個為寶可夢卡牌玩家而設的網頁工具，重點係快速搵卡、砌牌組、管理工作區、模擬起手/獎賞卡，以及用 AI 輔助查詢卡牌資料。

## Features / 功能

- Card search with filters for name, type, element, stage, set, and regulation mark.
- Deck builder with desktop drag-and-drop and mobile-friendly controls.
- Workspace system for organizing decks in folders.
- Opening hand and prize card simulation.
- PTCG Live export format.
- Japanese tournament deck import and local matching.
- Admin tools for card data updates and user/deck management.
- Experimental AI assistant that can query local card data before answering.

- 卡牌搜尋：支援名稱、類別、屬性、進化階段、系列、賽制標記等條件。
- 牌組構築：桌面支援拖放，手機有觸控友善操作。
- 工作區：用資料夾整理多副牌組。
- 起手與獎賞卡模擬：用來測試穩定性同關鍵卡風險。
- PTCG Live 匯出格式。
- 日本賽事牌組導入與本地卡牌配對。
- 管理員工具：更新卡牌資料、管理用戶與牌組。
- 實驗性 AI 助手：回答前會先查本地卡牌資料庫。

## Tech Stack / 技術棧

| Area | Stack |
|---|---|
| Backend | Python, Flask, Flask-Login |
| Database | PostgreSQL 16 |
| Frontend | Static HTML/CSS/JS, Vue 3 CDN, Tailwind CSS |
| Data tools | requests, BeautifulSoup4, crawler scripts |
| Deployment | Docker, Docker Compose |
| AI integration | OpenAI-compatible chat completion providers |

## Project Structure / 專案結構

```text
backend/      Flask app, routes, database helpers, service modules
frontend/     Static web UI and Vue modules
tools/        Local crawler, migration, and experiment scripts
deploy/       Deployment notes and helper scripts
docs/         Extra documentation
```

Large local datasets, card images, database dumps, caches, and secrets are intentionally excluded from this repository.

大型本地資料、卡圖快取、資料庫 dump、cache 同 secret 都刻意唔放入 repo。

## Quick Start / 快速開始

### Docker

```bash
cp .env.example .env
docker compose up -d
```

Then open:

```text
http://localhost:5000
```

### Local Development

```bash
cp .env.example .env
pip install -r requirements.txt
python backend/init_db.py
python backend/app.py
```

Set `DATABASE_URL` first if your PostgreSQL database is not using the local default.

如果你 PostgreSQL 唔係用本機預設設定，請先設定 `DATABASE_URL`。

## Environment / 環境變數

Copy `.env.example` to `.env`, then edit the local `.env` file. Do not commit `.env`.

先將 `.env.example` 複製成 `.env`，再改本地 `.env`。唔好 commit `.env`。

Common settings:

```env
DATABASE_URL=postgresql://ptcg:ptcg_secret@localhost:5432/ptcg_db
SECRET_KEY=change-this
SECURITY_PASSWORD_SALT=change-this-too
AI_BASE_URL=https://api.openai.com/v1
AI_API_KEY=
AI_MODEL=
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=true
MAIL_USERNAME=
MAIL_PASSWORD=
MAIL_DEFAULT_SENDER=
```

For production, always replace the development secrets and database password.

正式部署時，必須換走開發用 secret 同資料庫密碼。

## API Documentation / API 文件

See [docs/API.md](docs/API.md) for route summaries.

API 路由摘要請睇 [docs/API.md](docs/API.md)。

## Data Notice / 資料說明

This repository does not include the full card image cache, generated deck JSON exports, local database dumps, or private configuration files.

本 repo 唔包含完整卡圖快取、生成牌組 JSON、本地資料庫 dump 或私人設定檔。

## Disclaimer / 免責聲明

This is an unofficial fan-made project. It is not affiliated with, endorsed, sponsored, or approved by The Pokemon Company, Nintendo, Creatures Inc., or GAME FREAK.

本項目係非官方 fan-made 工具，並非 The Pokemon Company、Nintendo、Creatures Inc. 或 GAME FREAK 官方項目，亦未獲其認可、贊助或批准。

Pokemon and Pokemon TCG related names are trademarks of their respective owners.

Pokemon 及 Pokemon TCG 相關名稱屬其各自權利持有人所有。

## License / 授權

License is not finalized yet. Please contact the maintainer before reusing this project commercially.

授權條款尚未最終確定。如需商業用途，請先聯絡維護者。
