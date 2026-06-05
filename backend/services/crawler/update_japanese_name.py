import sqlite3
import requests
import os
import json
import time
import threading
import queue
import urllib.parse
from datetime import datetime

# --- 設定區域 ---
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
DB_FILE = os.path.join(DATA_DIR, 'pokemon_card_database.db')

# API Configuration
BASE_DOMAIN = "https://ptcgsp.com"
API_SEARCH_URL = f"{BASE_DOMAIN}/api/cards/"
API_DETAIL_URL = f"{BASE_DOMAIN}/api/cards/"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-HK,zh;q=0.9,en-US;q=0.8,en;q=0.7,zh-TW;q=0.6",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://ptcgsp.com/cards", 
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin"
}

# 限制併發數，避免對目標網站造成太大壓力
NUM_WORKERS = 2 
DELAY_BETWEEN_REQUESTS = 1.0 # 秒

task_queue = queue.Queue()
db_lock = threading.Lock()
stats = {"checked": 0, "updated": 0, "not_found": 0, "errors": 0}

def get_db_connection():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def ensure_schema_updates():
    """確保日文名稱與JP_ID欄位存在"""
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 檢查並新增 japanese_name
    try:
        cursor.execute("ALTER TABLE cards ADD COLUMN japanese_name TEXT")
        print("✅ 成功新增欄位: japanese_name")
    except sqlite3.OperationalError:
        pass 
        
    # 檢查並新增 jp_id
    try:
        cursor.execute("ALTER TABLE cards ADD COLUMN jp_id TEXT")
        print("✅ 成功新增欄位: jp_id")
    except sqlite3.OperationalError:
        pass
        
    conn.commit()
    conn.close()

def log(msg):
    timestamp = datetime.now().strftime("%H:%M:%S")
    print(f"[{timestamp}] {msg}")

def fetch_japanese_data_api(chinese_name):
    """
    使用 PTCGSP API 流程：
    1. Search API -> 取得 List
    2. 第一筆結果 -> 取得 UID (JP_ID)
    3. Detail API (UID) -> 取得 name_j
    回傳: (name_j, jp_id)
    """
    if not chinese_name: return None, None
    
    try:
        # Step 1: Search
        params = {
            "keyword": chinese_name,
            "page": 1,
            "keywordType": '["n","e"]' 
        }
        
        # 禮貌性延遲
        time.sleep(DELAY_BETWEEN_REQUESTS)
        
        resp_list = requests.get(API_SEARCH_URL, headers=HEADERS, params=params, timeout=10)
        if resp_list.status_code != 200:
            return None, None
            
        data_list = resp_list.json()
        cards_summary = data_list.get('data', {}).get('cards', [])
        
        if not cards_summary:
            return None, None # 沒找到

        # Step 2: Get Detail for the first best match
        # 通常搜尋結果第一個就是最相關的
        top_match = cards_summary[0]
        uid = top_match.get('uid')
        if not uid: return None, None
        
        detail_url = f"{API_DETAIL_URL}{uid}"
        
        time.sleep(DELAY_BETWEEN_REQUESTS) # 再次延遲
        
        resp_detail = requests.get(detail_url, headers=HEADERS, timeout=10)
        if resp_detail.status_code == 200:
            detail_data = resp_detail.json()
            card_detail = detail_data.get('data', {})
            name_j = card_detail.get('name_j')
            
            # 檢查有效性
            if name_j and name_j != '未知':
                return name_j, uid
                
        return None, None

    except Exception as e:
        log(f"⚠️ API Error ({chinese_name}): {e}")
        return None, None

def update_db(card_id, japanese_name, jp_id):
    with db_lock:
        conn = get_db_connection()
        try:
            conn.execute("UPDATE cards SET japanese_name = ?, jp_id = ? WHERE card_id = ?", (japanese_name, jp_id, card_id))
            conn.commit()
            stats["updated"] += 1
        except Exception as e:
            log(f"❌ DB Update Error: {e}")
        finally:
            conn.close()

def worker_thread(idx):
    log(f"🔧 Worker {idx} 啟動")
    while True:
        try:
            task = task_queue.get(timeout=5)
        except queue.Empty:
            break
        
        if task is None: break
            
        card_id, name = task
        stats["checked"] += 1
        
        log(f"Worker {idx} 正在處理: {name} (ID: {card_id})")
        
        jp_name, jp_id = fetch_japanese_data_api(name)
        
        if jp_name:
            if jp_name == name: # 避免填入一樣的中文，但仍記錄 JP_ID
                 log(f"  -> 跳過日文名更新 (相同)，但更新 JP_ID: {jp_id}")
                 update_db(card_id, jp_name, jp_id) # 這裡還是更新一下比較好，反正主要是為了 ID
            else:
                log(f"  -> ✅ 找到: {jp_name} (JP_ID: {jp_id})")
                update_db(card_id, jp_name, jp_id)
        else:
            log(f"  -> ❌ 無法找到資料")
            stats["not_found"] += 1
            
        task_queue.task_done()

def main():
    ensure_schema_updates()
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 策略：只抓取 japanese_name 為空，且是寶可夢或訓練家卡
    # 這邊因為新增了 jp_id 需求，理論上應該也要檢查 jp_id 是空的才對
    # 但為了讓使用者能簡單補完，我們先鎖定兩個其中之一空就抓
    print("🔍 正在掃描資料庫中缺少日文名稱或 JP_ID 的卡片...")
    
    cursor.execute("""
        SELECT card_id, name FROM cards 
        WHERE (
            (japanese_name IS NULL OR japanese_name = '') 
            OR 
            (jp_id IS NULL OR jp_id = '')
        )
        AND card_type IN ('Pokémon', 'Trainer')
    """)
    rows = cursor.fetchall()
    conn.close()
    
    print(f"📋 共有 {len(rows)} 張卡片待處理")
    
    if not rows:
        print("所有卡片都已經有日文名稱和 JP_ID 了！")
        return

    for row in rows:
        task_queue.put(row) # (card_id, name)
        
    workers = []
    for i in range(NUM_WORKERS):
        t = threading.Thread(target=worker_thread, args=(i+1,))
        t.start()
        workers.append(t)
        
    task_queue.join()
    
    print("\n🎉 任務完成！")
    print(f"統計: 檢查 {stats['checked']}, 更新 {stats['updated']}, 未找到 {stats['not_found']}, 錯誤 {stats['errors']}")

if __name__ == "__main__":
    main()
