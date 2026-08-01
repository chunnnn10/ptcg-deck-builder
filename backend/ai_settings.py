"""動態 AI 設定：允許 admin 面板在執行期編輯 LLM/Agent 配置。

讀取優先順序：DB（ai_settings 表）→ 環境變量 → 默認值。
修改後透過 set_ai_setting / clear_cache 讓新設定立即生效（無需重啟）。
"""

import threading

import database

_cache: dict[str, str] = {}
_cache_lock = threading.Lock()


def get_ai_setting(key: str, default: str = "") -> str:
    """讀取設定。DB 有值優先，其次環境變量，最後 default。"""
    with _cache_lock:
        if key in _cache:
            return _cache[key]
    try:
        conn = database.get_db_connection()
        if conn:
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
    except Exception:
        pass
    return default


def set_ai_setting(key: str, value: str) -> bool:
    """寫入設定並更新快取（UPSERT）。"""
    try:
        conn = database.get_db_connection()
        if not conn:
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
    except Exception:
        return False


def clear_cache() -> None:
    """清除記憶體快取（admin 修改後呼叫，確保後續讀取拿新值）。"""
    with _cache_lock:
        _cache.clear()
