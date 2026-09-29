"""動態 AI 設定：允許 admin 面板在執行期編輯 LLM/Agent 配置。

讀取優先順序：DB（ai_settings 表）→ 環境變量 → 默認值。
修改後透過 set_ai_setting / clear_cache 讓新設定立即生效（無需重啟）。

錯誤處理：DB 出錯不再靜默吞掉，會印出帶 key 的警告（同一 key+操作只提示一次，
避免高頻讀取洗版；clear_cache() 後會重新提示）。回傳值契約維持不變：
    get_ai_setting(key, default) -> DB 有值回傳該值，否則回傳 default
    set_ai_setting(key, value)   -> bool（成功 True / 失敗 False）
"""

import os
import threading

import database

_cache: dict[str, str] = {}
_cache_lock = threading.Lock()
_warned: set[str] = set()
_warn_lock = threading.Lock()


def _warn(key: str, action: str, exc: Exception) -> None:
    """印出 DB 錯誤警告；同一 key+action 只提示一次，避免每次讀寫都洗版。"""
    tag = f"{action}:{key}"
    with _warn_lock:
        if tag in _warned:
            return
        _warned.add(tag)
    print(
        f"[ai_settings] WARNING: {action} 失敗 key={key!r}: {exc}"
        "（回退環境變量/默認值）",
        flush=True,
    )


def get_ai_setting(key: str, default: str = "") -> str:
    """讀取設定。DB 有值優先，否則回傳 default（環境變量由呼叫方處理）。"""
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        conn = database.get_db_connection()
        if not conn:
            # get_db_connection() 內部已吞掉連線錯誤並回傳 None；這裡補回警告，
            # 否則 DB 掛掉時 AI 設定會靜默回退，同原本嘅 bug 一樣。
            _warn(key, 'get_ai_setting', RuntimeError('database connection unavailable'))
            return default
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM ai_settings WHERE key = %s", (key,))
            row = cursor.fetchone()
            if row and row.get('value') not in (None, ''):
                value = row['value']
                with _cache_lock:
                    _cache[key] = value
                return value
        finally:
            conn.close()
    except Exception as exc:
        _warn(key, 'get_ai_setting', exc)
    return default


def get_ai_setting_source(key: str) -> str:
    """診斷用：回傳 'db' | 'env' | 'default'，表示目前生效值來自哪一層。"""
    with _cache_lock:
        if key in _cache:
            return 'db'
    try:
        conn = database.get_db_connection()
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT value FROM ai_settings WHERE key = %s", (key,))
                row = cursor.fetchone()
                if row and row.get('value') not in (None, ''):
                    return 'db'
            finally:
                conn.close()
    except Exception as exc:
        _warn(key, 'get_ai_setting_source', exc)
    if str(os.environ.get(key) or '').strip():
        return 'env'
    return 'default'


def set_ai_setting(key: str, value: str) -> bool:
    """寫入設定並更新快取（UPSERT）。失敗會印出警告並回傳 False。"""
    try:
        conn = database.get_db_connection()
        if not conn:
            _warn(key, 'set_ai_setting', RuntimeError('database connection unavailable'))
            return False
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO ai_settings (key, value) VALUES (%s, %s)
                ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
            """, (key, value))
            conn.commit()
        finally:
            conn.close()
        with _cache_lock:
            _cache[key] = value
        return True
    except Exception as exc:
        _warn(key, 'set_ai_setting', exc)
        return False


def clear_cache() -> None:
    """清除記憶體快取（admin 修改後呼叫，確保後續讀取拿新值）。"""
    with _cache_lock:
        _cache.clear()
    with _warn_lock:
        _warned.clear()
