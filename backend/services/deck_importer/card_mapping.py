"""
卡牌對照表建立腳本
透過 ptcgtw 單卡 API (mysqli_api_2.php) 批次查詢所有 variant_id，
用 set_name+set_no 匹配本地 cards 表的 set_code+set_number，
將對照寫入 id_mapping 表。

支援多 worker 併發，進度可透過 MappingState 查詢。
"""
import json
import time
import threading
import requests
import psycopg2
import psycopg2.extras
from concurrent.futures import ThreadPoolExecutor, as_completed

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
import config
import database

# ── ptcgtw API ──
PTCGTW_CARD_API = "https://ptcgtw.shop/index_function/api/mysqli_api_2.php"
API_PARAMS_TEMPLATE = "?type=%E5%96%AE%E5%8D%A1%E8%B3%87%E6%96%99&lan=0&format=json&variant_id="
REQUEST_TIMEOUT = 15  # seconds
BATCH_DELAY = 0.05    # small delay between batches to avoid hammering

# ── 掃描範圍 ──
MIN_VARIANT_ID = 1
MAX_VARIANT_ID = 100000

# ── 全域狀態 ──
class MappingState:
    """全執行緒共用的狀態追蹤"""
    def __init__(self):
        self.running = False
        self.total = 0
        self.processed = 0
        self.matched = 0
        self.unmatched = 0
        self.errors = 0
        self.message = "就緒"
        self.start_time = None
        self._lock = threading.Lock()

    def reset(self):
        with self._lock:
            self.running = True
            self.total = 0
            self.processed = 0
            self.matched = 0
            self.unmatched = 0
            self.errors = 0
            self.message = "初始化中..."
            self.start_time = time.time()

    def update(self, processed=None, matched=None, unmatched=None, errors=None, message=None):
        with self._lock:
            if processed is not None:
                self.processed = processed
            if matched is not None:
                self.matched = matched
            if unmatched is not None:
                self.unmatched = unmatched
            if errors is not None:
                self.errors = errors
            if message is not None:
                self.message = message

    def increment_matched(self):
        with self._lock:
            self.matched += 1
            self.processed += 1

    def increment_unmatched(self):
        with self._lock:
            self.unmatched += 1
            self.processed += 1

    def increment_errors(self):
        with self._lock:
            self.errors += 1
            self.processed += 1

    def finish(self):
        with self._lock:
            self.running = False
            self.message = "對照表更新完成"

    def to_dict(self):
        with self._lock:
            elapsed = ""
            if self.start_time:
                elapsed_sec = int(time.time() - self.start_time)
                elapsed = f"{elapsed_sec // 60}分{elapsed_sec % 60}秒"
            return {
                "running": self.running,
                "total": self.total,
                "processed": self.processed,
                "matched": self.matched,
                "unmatched": self.unmatched,
                "errors": self.errors,
                "message": self.message,
                "elapsed": elapsed,
                "progress": round(self.processed / self.total * 100, 1) if self.total > 0 else 0
            }


# 全域實例
mapping_state = MappingState()


# ── 資料庫輔助 ──
def ensure_id_mapping_columns():
    """確保 id_mapping 表有新欄位（confidence, matched_at, match_detail, source）"""
    conn = database.get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        # 檢查並新增欄位
        cursor.execute("""
            SELECT column_name FROM information_schema.columns 
            WHERE table_name = 'id_mapping'
        """)
        existing = {row['column_name'] for row in cursor.fetchall()}

        new_cols = {
            "confidence": "VARCHAR DEFAULT 'MEDIUM'",
            "score": "INTEGER DEFAULT 0",
            "match_detail": "TEXT",
            "matched_at": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            "source": "VARCHAR DEFAULT 'ptcgtw'",
        }
        for col_name, col_def in new_cols.items():
            if col_name not in existing:
                cursor.execute(f"ALTER TABLE id_mapping ADD COLUMN {col_name} {col_def}")
                print(f"[Migration] Added column id_mapping.{col_name}")

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[Migration] Error: {e}")
    finally:
        conn.close()


# ── 單卡查詢 ──
def fetch_ptcgtw_card(variant_id, session):
    """查詢單張 ptcgtw 卡牌資料"""
    url = f"{PTCGTW_CARD_API}{API_PARAMS_TEMPLATE}{variant_id}"
    try:
        resp = session.get(url, timeout=REQUEST_TIMEOUT)
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("data"):
                card = data["data"]
                return {
                    "variant_id": card.get("variant_id"),
                    "set_name": (card.get("set_name") or "").strip(),
                    "set_no": (card.get("set_no") or "").strip(),
                    "name_tw": (card.get("name_tw") or "").strip(),
                    "rarity": (card.get("rarity") or "").strip(),
                }
        return None
    except Exception:
        return None


# ── 本地匹配 ──
def match_local_card(cursor, set_name, set_no):
    """用 set_code + set_number 在本地 cards 表中查詢"""
    if not set_name or not set_no:
        return None
    cursor.execute(
        "SELECT card_id FROM cards WHERE set_code = %s AND set_number = %s LIMIT 1",
        (set_name, set_no)
    )
    row = cursor.fetchone()
    return row["card_id"] if row else None


# ── 寫入對照 ──
def upsert_mapping(cursor, variant_id, local_card_id, confidence="HIGH", score=100, match_detail=None):
    """插入或更新 id_mapping"""
    cursor.execute("SELECT 1 FROM id_mapping WHERE external_variant_id = %s", (variant_id,))
    exists = cursor.fetchone()
    detail_json = json.dumps(match_detail or {}, ensure_ascii=False)
    if exists:
        cursor.execute("""
            UPDATE id_mapping 
            SET local_card_id = %s, confidence = %s, score = %s, 
                match_detail = %s, matched_at = CURRENT_TIMESTAMP, source = 'ptcgtw'
            WHERE external_variant_id = %s
        """, (local_card_id, confidence, score, detail_json, variant_id))
    else:
        cursor.execute("""
            INSERT INTO id_mapping (external_variant_id, local_card_id, confidence, score, match_detail, matched_at, source)
            VALUES (%s, %s, %s, %s, %s, CURRENT_TIMESTAMP, 'ptcgtw')
        """, (variant_id, local_card_id, confidence, score, detail_json))


# ── 批次掃描（一個 worker 負責一段 ID） ──
def scan_id_range(start_id, end_id, worker_id):
    """單一 worker：掃描 [start_id, end_id] 的 variant_id"""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    })

    conn = database.get_db_connection()
    if not conn:
        mapping_state.update(message=f"Worker {worker_id}: DB connection failed")
        return

    cursor = conn.cursor()
    local_matched = 0
    local_unmatched = 0
    local_errors = 0

    for vid in range(start_id, end_id + 1):
        if not mapping_state.running:
            break

        card = fetch_ptcgtw_card(vid, session)
        if card is None:
            # NO_DATA_FOUND or network error — skip silently
            continue

        try:
            local_id = match_local_card(cursor, card["set_name"], card["set_no"])
            if local_id:
                detail = {
                    "set": f"{card['set_name']} {card['set_no']}",
                    "name": card["name_tw"],
                    "rarity": card["rarity"],
                }
                upsert_mapping(cursor, vid, local_id, "HIGH", 100, detail)
                conn.commit()
                local_matched += 1
                mapping_state.increment_matched()
            else:
                local_unmatched += 1
                mapping_state.increment_unmatched()
        except Exception as e:
            conn.rollback()
            local_errors += 1
            mapping_state.increment_errors()

    conn.close()
    session.close()

    mapping_state.update(message=f"Worker {worker_id}: matched={local_matched}, unmatched={local_unmatched}, errors={local_errors}")


# ── 主入口（由後端 API 呼叫） ──
def run_mapping(worker_count=5):
    """啟動多 worker 併發掃描"""
    if mapping_state.running:
        return False, "對照表更新已在進行中"

    # 確保表結構最新
    ensure_id_mapping_columns()

    # 估算有效 ID 範圍（可根據實際情況調整）
    # 保守設 1-100000，其中約 43000 有效
    mapping_state.reset()
    mapping_state.total = MAX_VARIANT_ID - MIN_VARIANT_ID + 1  # 100000
    mapping_state.message = f"啟動 {worker_count} 個機器人，掃描 variant_id {MIN_VARIANT_ID}–{MAX_VARIANT_ID}..."

    # 分配 ID 範圍給各 worker
    chunk_size = (MAX_VARIANT_ID - MIN_VARIANT_ID + 1) // worker_count
    ranges = []
    for i in range(worker_count):
        start = MIN_VARIANT_ID + i * chunk_size
        end = start + chunk_size - 1 if i < worker_count - 1 else MAX_VARIANT_ID
        ranges.append((start, end, i + 1))

    # 在背景執行緒中執行
    def _run():
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = [
                executor.submit(scan_id_range, start, end, wid)
                for start, end, wid in ranges
            ]
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    print(f"[Mapping] Worker error: {e}")
        mapping_state.finish()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True, f"已啟動 {worker_count} 個機器人"


def get_mapping_status():
    """回傳目前對照表更新狀態"""
    return mapping_state.to_dict()
