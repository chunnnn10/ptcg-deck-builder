# DB 備份與恢復說明

## 備份範圍（每日）

僅備份**用戶資料**四張表：

- `users` — 帳號
- `user_workspace` — 工作區（資料夾/牌組樹）
- `user_workspace_timeline` — 工作區時間軸
- `decks` — 用戶牌組

**不備份**：`cards` / `jp_cards` / `processed_cards` / `imported_decks` /
`deck_cards` / `deck_search_index` / `id_mapping` / `limitless_*` /
`expansion_sets` — 全部可由爬蟲與對照表重建。

## 自動排程（主要機制）

`ptcg_web` 容器內的背景執行緒（`backend/services/user_backup.py`，
由 `app.py` 啟動，受 `ENABLE_USER_BACKUP` 控制，預設開啟）：

- 每日 **UTC 04:17**（= 台灣/新加坡 12:17）執行一次
- 格式：`COPY public.<table> TO STDOUT` 純文字資料
  （schema 由 `database.py init_db` 啟動時重建，故備份不含 CREATE TABLE）
- 檔案：`/opt/ptcg/data/backups/ptcg_user_data_<UTC時間>.sql.gz`
- 保留：最近 7 天（共用清理規則）

容器日誌會出現：`>>> [User Backup] enabled, first run in XhYm ...` 與
`>>> [User Backup] <UTC時間> UTC 備份完成：...`

## 手動備份

```bash
# 方式一：容器內（COPY 格式，與自動排程相同）
docker exec ptcg_web python3 -c "import sys; sys.path.insert(0,'/app/backend'); from services.user_backup import run_backup; print(run_backup())"

# 方式二：主機（pg_dump 完整格式，含 CREATE TABLE，需 root 權限）
bash /opt/ptcg/scripts/backup_user_data.sh   # → ptcg_user_backup_<UTC時間>.sql.gz
```

## 恢復

### COPY 格式（`ptcg_user_data_*.sql.gz`，自動排程產出）

schema 由 init_db 自動重建，故只恢復資料：

```bash
# 1) 找出要恢復的備份檔
ls -lt /opt/ptcg/data/backups/ptcg_user_data_*.sql.gz

# 2) 在容器內執行恢復（會 TRUNCATE 四張表再匯入）
docker exec ptcg_web python3 -c "
import sys; sys.path.insert(0,'/app/backend')
from services.user_backup import restore_backup
print(restore_backup('/opt/ptcg/data/backups/ptcg_user_data_XXXXXXXXTXXXXZ.sql.gz'))"
```

### pg_dump 格式（`ptcg_user_backup_*.sql.gz`，主機手動產出）

```bash
# 1) 先刪除既有四張表（備份檔內含完整 CREATE TABLE，直接恢復會衝突）
docker exec ptcg_db psql -U ptcg -d ptcg_db -c \
  "DROP TABLE IF EXISTS users, user_workspace, user_workspace_timeline, decks CASCADE"

# 2) 恢復（重啟 ptcg-web 讓 init_db 重建其他相依 schema）
gunzip -c /opt/ptcg/data/backups/ptcg_user_backup_XXXXXXXXTXXXXZ.sql.gz \
  | docker exec -i ptcg_db psql -U ptcg -d ptcg_db --set ON_ERROR_STOP=1
docker restart ptcg_web
```

### 恢復後驗證

```bash
docker exec ptcg_db psql -U ptcg -d ptcg_db \
  -c "SELECT count(*) FROM users" -c "SELECT count(*) FROM decks"
```

## 時區

所有時間戳（備份檔名、`auto_update_runs`、log）統一為 **UTC**；
排程時間 UTC 04:17 = 主機本地（Asia/Singapore, UTC+8）12:17。
