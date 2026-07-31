#!/usr/bin/env bash
# PTCG Deck Builder — 每日備份（僅用戶資料）
# 由 VPS crontab 每日呼叫。備份僅含 users / user_workspace /
# user_workspace_timeline / decks（用戶牌組）；卡片資料（cards、jp_cards、
# imported_decks、deck_cards…）可由爬蟲重建，不備份。
# 檔案：/opt/ptcg/data/backups/ptcg_user_backup_<UTC時間>.sql.gz，保留 7 天。
set -euo pipefail

BACKUP_DIR="/opt/ptcg/data/backups"
DB_CONTAINER="ptcg_db"
DB_USER="ptcg"
DB_NAME="ptcg_db"
TABLES=("users" "user_workspace" "user_workspace_timeline" "decks")
RETENTION_DAYS=7

mkdir -p "$BACKUP_DIR"
LOG_FILE="$BACKUP_DIR/backup.log"
STAMP="$(date -u +%Y%m%dT%H%MZ)"
OUT="$BACKUP_DIR/ptcg_user_backup_${STAMP}.sql.gz"

log() { echo "[$(date -u +%Y-%m-%dT%H:%M:%SZ)] $*" >> "$LOG_FILE"; }

TABLE_ARGS=()
for t in "${TABLES[@]}"; do TABLE_ARGS+=("-t" "$t"); done

log "開始備份 → $OUT"
# docker exec 走 container 內 unix socket（trust auth），不需密碼
if docker exec "$DB_CONTAINER" pg_dump -U "$DB_USER" -d "$DB_NAME" "${TABLE_ARGS[@]}" \
        --no-owner --no-privileges 2>>"$LOG_FILE" | gzip > "$OUT"; then
    SIZE=$(du -h "$OUT" | cut -f1)
    log "備份完成（$SIZE）"
else
    log "備份失敗！"
    rm -f "$OUT"
    exit 1
fi

# 保留最近 7 天，其餘刪除
find "$BACKUP_DIR" -name 'ptcg_user_backup_*.sql.gz' -mtime +"$RETENTION_DAYS" -delete
log "清理完成（保留 ${RETENTION_DAYS} 天）"
exit 0
