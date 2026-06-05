import streamlit as st
import threading
import queue
import time
import pandas as pd
import sqlite3
import os
from db_logic import worker_thread, TOOL_DB, SRC_DB

# 設置頁面
st.set_page_config(page_title="PTCG AI Factory", layout="wide", page_icon="⚡")

# --- 核心修復：使用 cache_resource 確保線程與 UI 共用同一個狀態 ---
class StateManager:
    def __init__(self):
        self.log_queue = queue.Queue()
        self.thread_status = {"Worker-1": "Stopped", "Worker-2": "Stopped"}
        self.debug_info = {
            "Worker-1": {"action": "Waiting", "prompt": "", "output": {}},
            "Worker-2": {"action": "Waiting", "prompt": "", "output": {}}
        }
        self.logs = []

@st.cache_resource
def get_manager():
    return StateManager()

state = get_manager()

# --- 資料庫診斷工具 ---
def check_db_health():
    health_status = {"connected": False, "card_count": 0, "path": SRC_DB, "error": None}
    try:
        if not os.path.exists(SRC_DB):
            health_status["error"] = f"File not found at: {SRC_DB}"
            return health_status
            
        conn = sqlite3.connect(SRC_DB)
        cursor = conn.cursor()
        # 檢查是否有 cards 表
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='cards';")
        if not cursor.fetchone():
            health_status["error"] = "Table 'cards' not found in DB."
            conn.close()
            return health_status
            
        # 檢查卡片數量
        cursor.execute("SELECT COUNT(*) FROM cards WHERE skills_json IS NOT NULL AND skills_json != ''")
        health_status["card_count"] = cursor.fetchone()[0]
        health_status["connected"] = True
        conn.close()
    except Exception as e:
        health_status["error"] = str(e)
    return health_status

# 啟動線程函數
def start_workers():
    # 檢查線程是否真的活著
    active_threads = [t.name for t in threading.enumerate()]
    
    if "Worker-1" not in active_threads:
        t1 = threading.Thread(
            target=worker_thread, 
            args=("Worker-1", state.log_queue, state.thread_status, state.debug_info), 
            name="Worker-1", 
            daemon=True
        )
        t1.start()
        
    if "Worker-2" not in active_threads:
        t2 = threading.Thread(
            target=worker_thread, 
            args=("Worker-2", state.log_queue, state.thread_status, state.debug_info), 
            name="Worker-2", 
            daemon=True
        )
        t2.start()
    
    st.toast("Workers are running in background!")

# --- UI Layout ---

st.title("⚡ PTCG AI Logic Factory")

# 1. 系統診斷區 (幫助你找出為什麼沒反應)
with st.expander("🛠️ System Diagnostics (Check this if Idle)", expanded=True):
    db_health = check_db_health()
    c1, c2, c3 = st.columns([2, 2, 1])
    
    with c1:
        st.caption("Source DB Path")
        st.code(db_health["path"])
        if db_health["connected"]:
            st.success(f"✅ DB Connected! Found {db_health['card_count']} cards.")
        else:
            st.error(f"❌ DB Error: {db_health['error']}")
            st.info("Tip: Check docker-compose volumes mapping.")

    with c2:
        # 控制按鈕
        st.caption("Control Panel")
        if st.button("🚀 (Re)Start Production Line", type="primary"):
            start_workers()
            st.rerun()

# 2. 頂部數據儀表板
col1, col2, col3 = st.columns(3)

def get_stats():
    if not os.path.exists(TOOL_DB): return 0, 0, 0
    try:
        conn = sqlite3.connect(TOOL_DB)
        c = conn.cursor()
        total_processed = c.execute("SELECT COUNT(*) FROM processed_cards").fetchone()[0]
        quarantined = c.execute("SELECT COUNT(*) FROM processed_cards WHERE status='QUARANTINE'").fetchone()[0]
        api_count = c.execute("SELECT COUNT(*) FROM api_logs").fetchone()[0]
        conn.close()
        return total_processed, quarantined, api_count
    except:
        return 0, 0, 0

processed, quarantine, api_calls = get_stats()
col1.metric("Cards Processed", processed)
col2.metric("Quarantine Zone", quarantine, delta_color="inverse")
col3.metric("API Calls Total", api_calls)

st.divider()

# 3. Worker 即時監控 (Debug 視窗)
st.subheader("🧠 AI Brain Scan (Real-time Debug)")
w_col1, w_col2 = st.columns(2)

# Worker 1 區塊
with w_col1:
    status_color = "🟢" if "Processing" in state.thread_status["Worker-1"] else "⚪"
    st.markdown(f"### {status_color} Worker 1 (Grok)")
    st.caption(f"Status: **{state.thread_status['Worker-1']}**")
    
    info_1 = state.debug_info["Worker-1"]
    
    # 顯示 JSON 輸出
    st.markdown("**Last Output:**")
    output_1 = info_1.get("output", {})
    if output_1 and output_1 != {}:
        st.json(output_1, expanded=False)
    else:
        st.info("Waiting for first result...", icon="⏳")
        
    # 顯示 Prompt
    with st.expander("View Input Prompt"):
        st.text(info_1.get("prompt", "No prompt yet"))

# Worker 2 區塊
with w_col2:
    status_color = "🟢" if "Processing" in state.thread_status["Worker-2"] else "⚪"
    st.markdown(f"### {status_color} Worker 2 (Gemini)")
    st.caption(f"Status: **{state.thread_status['Worker-2']}**")
    
    info_2 = state.debug_info["Worker-2"]
    
    # 顯示 JSON 輸出
    st.markdown("**Last Output:**")
    output_2 = info_2.get("output", {})
    if output_2 and output_2 != {}:
        st.json(output_2, expanded=False)
    else:
        st.info("Waiting for first result...", icon="⏳")

    # 顯示 Prompt
    with st.expander("View Input Prompt"):
        st.text(info_2.get("prompt", "No prompt yet"))

st.divider()

# 4. 實時日誌
st.subheader("📝 Event Logs")
log_container = st.container(height=300)

# --- 自動刷新與日誌處理 ---
# 從 Queue 取出新日誌並放入 State
while not state.log_queue.empty():
    msg = state.log_queue.get()
    timestamp = time.strftime('%H:%M:%S')
    state.logs.insert(0, f"[{timestamp}] {msg}") # 最新日誌在最上面

# 保持日誌長度在 50 行以內
if len(state.logs) > 50:
    state.logs = state.logs[:50]

# 顯示日誌
with log_container:
    for log in state.logs:
        st.text(log)

# 自動刷新頁面 (每 2 秒)
time.sleep(2)
st.rerun()