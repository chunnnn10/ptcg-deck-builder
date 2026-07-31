"""
每日備份（僅用戶資料）— 在 ptcg_web 容器內以 psycopg2 執行。
備份僅含 users / user_workspace / user_workspace_timeline / decks（用戶牌組）；
卡片資料可由爬蟲重建，不備份。

格式：每張表以 `COPY public.<table> TO STDOUT` 匯出純文字資料，
schema 由 database.py init_db 於啟動時重建，恢復流程見 docs/BACKUP_RESTORE.md。

排程：背景執行緒每日 UTC 04:17 執行一次（ENABLE_USER_BACKUP 控制，預設開）。
檔名：ptcg_user_data_<UTC時間>.sql.gz，保留 7 天。
（另有主機層手動腳本 scripts/backup_user_data.sh 使用 pg_dump 完整格式，
  檔名 ptcg_user_backup_*.sql.gz，兩者共用保留策略。）
"""
import gzip
import os
import time
import threading

import config
import database

BACKUP_TABLES = ['users', 'user_workspace', 'user_workspace_timeline', 'decks']
BACKUP_DIR = os.path.normpath(os.path.join(config.ROOT_DIR, 'data', 'backups'))
RETENTION_DAYS = 7
# 每日執行時間（UTC）
BACKUP_HOUR = int(os.environ.get('USER_BACKUP_HOUR', 4))
BACKUP_MINUTE = int(os.environ.get('USER_BACKUP_MINUTE', 17))

_lock = threading.Lock()


def _write_table(conn, cursor, table, out):
    out.write(f"COPY public.{table} FROM stdin;\n".encode('utf-8'))
    cursor.copy_expert(f"COPY public.{table} TO STDOUT", out)
    out.write(b"\\.\n")


def run_backup():
    """執行一次備份。回傳 (success, message)。"""
    with _lock:
        try:
            os.makedirs(BACKUP_DIR, exist_ok=True)
            stamp = time.strftime('%Y%m%dT%H%MZ', time.gmtime())
            path = os.path.join(BACKUP_DIR, f"ptcg_user_data_{stamp}.sql.gz")

            conn = database.get_db_connection()
            if not conn:
                return False, "備份失敗：資料庫無法連線"
            try:
                cursor = conn.cursor()
                with gzip.open(path, 'wb') as out:
                    for table in BACKUP_TABLES:
                        _write_table(conn, cursor, table, out)
            finally:
                conn.close()

            # 保留最近 7 天（兩種備份格式共用）
            now = time.time()
            for fn in os.listdir(BACKUP_DIR):
                if (fn.startswith('ptcg_user_backup_') or fn.startswith('ptcg_user_data_')) \
                        and fn.endswith('.sql.gz'):
                    fp = os.path.join(BACKUP_DIR, fn)
                    if now - os.path.getmtime(fp) > RETENTION_DAYS * 86400:
                        os.remove(fp)

            size_kb = os.path.getsize(path) // 1024
            return True, f"備份完成：{os.path.basename(path)}（{size_kb}K）"
        except Exception as e:
            return False, f"備份失敗: {e}"


def restore_backup(path):
    """從 COPY 格式備份檔恢復四張用戶表（TRUNCATE 後重建）。
    path: 容器內可讀取的 .sql.gz 檔路徑。
    備份檔內每張表格式為：
        COPY public.<table> FROM stdin;
        <tab-separated rows>
        \\.
    恢復時跳過標頭與終止行，只把資料餵給 COPY FROM STDIN。"""
    import io

    with _lock:
        conn = database.get_db_connection()
        if not conn:
            return False, "恢復失敗：資料庫無法連線"
        try:
            with gzip.open(path, 'rt', encoding='utf-8') as src:
                lines = src.readlines()

            cursor = conn.cursor()
            cursor.execute("TRUNCATE " + ", ".join(BACKUP_TABLES))

            idx = 0
            restored = []
            while idx < len(lines):
                line = lines[idx].strip()
                if line.startswith('COPY public.') and 'FROM stdin' in line:
                    table = line.split(' ')[1].replace('public.', '').strip('"')
                    idx += 1
                    buf = io.StringIO()
                    while idx < len(lines) and lines[idx].strip() != '\\.':
                        buf.write(lines[idx])
                        idx += 1
                    buf.seek(0)
                    cursor.copy_expert(f"COPY public.{table} FROM STDIN", buf)
                    restored.append(table)
                idx += 1

            conn.commit()
            return True, f"已從 {os.path.basename(path)} 恢復 {len(restored)} 張表（{', '.join(restored)}）"
        except Exception as e:
            conn.rollback()
            return False, f"恢復失敗: {e}"
        finally:
            conn.close()


def _seconds_until_next_run():
    """到下一次排程時間（UTC HH:MM）的秒數。"""
    now = time.gmtime()
    seconds_today = now.tm_hour * 3600 + now.tm_min * 60 + now.tm_sec
    target = BACKUP_HOUR * 3600 + BACKUP_MINUTE * 60
    delta = target - seconds_today
    if delta <= 0:
        delta += 86400
    return delta


def start_backup_scheduler():
    """背景執行緒：每日 UTC 04:17 執行一次備份。"""
    print(
        f">>> [User Backup] enabled, first run in {_seconds_until_next_run() // 3600}h"
        f"{_seconds_until_next_run() % 3600 // 60}m (UTC {BACKUP_HOUR:02d}:{BACKUP_MINUTE:02d})",
        flush=True,
    )
    while True:
        time.sleep(_seconds_until_next_run())
        success, message = run_backup()
        print(f">>> [User Backup] {time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime())} UTC {message}", flush=True)
