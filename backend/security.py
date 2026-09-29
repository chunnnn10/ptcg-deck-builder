"""簡易安全輔助模組：真實 IP 解析、失敗限流、未登錄查詢每日配額。

計數器存放於 PostgreSQL（跨 gunicorn worker 共享）；資料庫不可用時自動
退回進程內記憶體計數，確保登入流程永遠不會因 DB 問題中斷或拋錯。
"""

import ipaddress
import os
import threading
import time

from flask import request

# 未登錄查詢每日配額（超過即要求登錄）
ANON_QUERY_DAILY_LIMIT = 5

# 失敗限流參數: action -> (時間窗秒數, 允許次數)
_RATE_LIMITS = {
    'login': (15 * 60, 5),
    'register': (60 * 60, 3),
    'forgot': (60 * 60, 5),
}

# ── 信任反向代理 ──
# 只有當「直接連線的 peer」（request.remote_addr）落在這份清單時，
# 才會信任 CF-Connecting-IP；否則一律改用 remote_addr，避免任何人
# 偽造 header 輪換限流 key。
# 可用環境變數 PTCG_TRUSTED_PROXIES 覆蓋（逗號分隔，例如 '127.0.0.1,10.0.0.5'）。
_TRUSTED_PROXY_ENV = 'PTCG_TRUSTED_PROXIES'


def _parse_proxy_ips(raw) -> set:
    """把逗號分隔字串解析成信任代理集合。"""
    return {item.strip() for item in str(raw or '').split(',') if item.strip()}


# 模組常數：預設由環境變數載入（未設置時為空集合，即不信任任何 header）
TRUSTED_PROXY_IPS = _parse_proxy_ips(os.environ.get(_TRUSTED_PROXY_ENV))


def _trusted_proxies() -> set:
    """回傳目前生效的信任代理清單。

    env 有設置（且解析出非空集合）時以 env 為準，否則用模組常數；
    如此測試或部署可在進程啟動後調整 PTCG_TRUSTED_PROXIES。
    """
    raw = os.environ.get(_TRUSTED_PROXY_ENV)
    if raw is None:
        return TRUSTED_PROXY_IPS
    return _parse_proxy_ips(raw) or TRUSTED_PROXY_IPS


_lock = threading.Lock()
# 失敗紀錄（記憶體後備）: action -> {ip: [timestamp, ...]}
_failures: dict[str, dict[str, list[float]]] = {}
# 未登錄查詢配額（記憶體後備）: ip -> {'date': 'YYYY-MM-DD', 'count': int}
_anon_usage: dict[str, dict[str, object]] = {}

# ── 共享（PostgreSQL）計數器 ──
# 時間窗最大的那個 window，用於機會性清理過期事件。
_MAX_WINDOW = max(window for window, _limit in _RATE_LIMITS.values())

_RATE_LIMIT_TABLE = 'rate_limit_events'
_ANON_QUOTA_TABLE = 'anon_quota'

_schema_ready = False
_schema_lock = threading.Lock()


def _is_trusted_peer(remote: str) -> bool:
    """判斷直接連線的 peer 是否為可信任代理。

    1) 明確列在 TRUSTED_PROXY_IPS / PTCG_TRUSTED_PROXIES；或
    2) remote_addr 屬私有 / loopback 位址——本項目生產環境係
       Caddy + docker network（ptcg-internal / proxy），反向代理永遠由
       私有網段連入，所以要信私有 peer 先唔會令所有匿名用戶共用同一個
       代理 IP（否則每日 5 次匿名配額會變成全站共用）。
    外部用戶直連時 remote_addr 係公網 IP，唔會落入此分支，header 無法偽造。
    """
    if not remote:
        return False
    if remote in _trusted_proxies():
        return True
    try:
        addr = ipaddress.ip_address(remote)
    except (ValueError, TypeError):
        return False
    return addr.is_private or addr.is_loopback or addr.is_link_local


def client_ip() -> str:
    """解析真實客戶端 IP。

    只有當直接連線的 remote_addr 係可信任代理（明確 allow-list 或私有網段）時，
    才採用 CF-Connecting-IP / X-Forwarded-For（取第一個值並驗證為合法 IP）；
    否則一律回傳 remote_addr，避免 header 被任意偽造。
    """
    remote = (request.remote_addr or '').strip()
    if _is_trusted_peer(remote):
        forwarded = (request.headers.get('CF-Connecting-IP', '') or '').strip()
        if not forwarded:
            forwarded = (request.headers.get('X-Forwarded-For', '') or '').strip()
        if ',' in forwarded:
            forwarded = forwarded.split(',')[0].strip()
        if forwarded and _is_valid_ip(forwarded):
            return forwarded
    return remote or 'unknown'


def _is_valid_ip(value: str) -> bool:
    """寬鬆驗證 header 值為 IPv4/IPv6，避免垃圾值污染限流 key。"""
    try:
        ipaddress.ip_address(value)
        return True
    except (ValueError, TypeError):
        return False


# ── DB 存取輔助（永不拋錯）──

def _db_connection():
    """取得資料庫連線；database 模組或連線不可用時回傳 None。"""
    try:
        import database  # 延遲 import，避免循環 import
    except Exception:
        return None
    try:
        return database.get_db_connection()
    except Exception:
        return None


def _ensure_schema(cursor) -> None:
    """冪等建立共享計數器資料表（可安全並發／重複執行）。"""
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_RATE_LIMIT_TABLE} (
            action VARCHAR(64),
            ip VARCHAR(64),
            ts DOUBLE PRECISION
        )
        """
    )
    cursor.execute(
        f"""
        CREATE INDEX IF NOT EXISTS idx_{_RATE_LIMIT_TABLE}_action_ip_ts
        ON {_RATE_LIMIT_TABLE} (action, ip, ts)
        """
    )
    cursor.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {_ANON_QUOTA_TABLE} (
            ip VARCHAR(64) PRIMARY KEY,
            day VARCHAR(16),
            count INTEGER DEFAULT 0
        )
        """
    )


def _run_db(fn):
    """在單一交易內執行 fn(cursor)。

    回傳 (True, result) 表示 DB 成功處理；任何失敗（連線、SQL、交易）
    都回傳 (False, None)，呼叫端據此退回記憶體計數。
    """
    global _schema_ready
    conn = _db_connection()
    if not conn:
        return False, None
    try:
        cursor = conn.cursor()
        if not _schema_ready:
            _ensure_schema(cursor)
        result = fn(cursor)
        conn.commit()
        if not _schema_ready:
            with _schema_lock:
                _schema_ready = True
        return True, result
    except Exception:
        try:
            conn.rollback()
        except Exception:
            pass
        return False, None
    finally:
        try:
            conn.close()
        except Exception:
            pass


# ── 失敗限流 ──

def record_failure(action: str, ip: str) -> None:
    """記錄一次失敗（登入失敗/註冊衝突/重設請求）。"""
    if action not in _RATE_LIMITS:
        return
    now = time.time()

    def _write(cursor):
        cursor.execute(
            f"INSERT INTO {_RATE_LIMIT_TABLE} (action, ip, ts) VALUES (%s, %s, %s)",
            (action, ip, now),
        )
        # 機會性清理：刪走比最大時間窗更舊的事件
        cursor.execute(
            f"DELETE FROM {_RATE_LIMIT_TABLE} WHERE ts < %s",
            (now - _MAX_WINDOW,),
        )

    ok, _ = _run_db(_write)
    if ok:
        return

    # 記憶體後備
    window, _limit = _RATE_LIMITS[action]
    with _lock:
        bucket = _failures.setdefault(action, {})
        hits = [t for t in bucket.get(ip, []) if now - t < window]
        hits.append(now)
        bucket[ip] = hits


def is_rate_limited(action: str, ip: str) -> bool:
    """檢查該 action 是否已達限流門檻。"""
    if action not in _RATE_LIMITS:
        return False
    now = time.time()
    window, limit = _RATE_LIMITS[action]

    def _count(cursor):
        cursor.execute(
            f"""
            SELECT COUNT(*) AS n
            FROM {_RATE_LIMIT_TABLE}
            WHERE action = %s AND ip = %s AND ts >= %s
            """,
            (action, ip, now - window),
        )
        row = cursor.fetchone()
        if not row:
            return 0
        try:
            return int(row['n'])
        except (KeyError, TypeError):
            return int(row[0])

    ok, count = _run_db(_count)
    if ok:
        return count >= limit

    # 記憶體後備
    with _lock:
        bucket = _failures.setdefault(action, {})
        hits = [t for t in bucket.get(ip, []) if now - t < window]
        bucket[ip] = hits
        return len(hits) >= limit


def clear_failures(action: str, ip: str) -> None:
    """成功時清除失敗紀錄（同時清 DB 與記憶體殘留）。"""
    def _clear(cursor):
        cursor.execute(
            f"DELETE FROM {_RATE_LIMIT_TABLE} WHERE action = %s AND ip = %s",
            (action, ip),
        )

    _run_db(_clear)
    with _lock:
        _failures.get(action, {}).pop(ip, None)


# ── 未登錄查詢配額 ──

def check_anon_quota(ip: str) -> bool:
    """未登錄查詢配額：每次呼叫計數一次，超過每日上限回傳 True。"""
    today = time.strftime('%Y-%m-%d')

    def _bump(cursor):
        # 單一 upsert + RETURNING：同一 IP 的並發計數會被 row lock 串行化
        cursor.execute(
            f"""
            INSERT INTO {_ANON_QUOTA_TABLE} (ip, day, count)
            VALUES (%s, %s, 1)
            ON CONFLICT (ip) DO UPDATE
                SET count = CASE
                        WHEN {_ANON_QUOTA_TABLE}.day = EXCLUDED.day
                        THEN {_ANON_QUOTA_TABLE}.count + 1
                        ELSE 1
                    END,
                    day = EXCLUDED.day
            RETURNING count
            """,
            (ip, today),
        )
        row = cursor.fetchone()
        if not row:
            return 1
        try:
            return int(row['count'])
        except (KeyError, TypeError):
            return int(row[0])

    ok, count = _run_db(_bump)
    if ok:
        return count > ANON_QUERY_DAILY_LIMIT

    # 記憶體後備
    with _lock:
        entry = _anon_usage.get(ip)
        if not entry or entry.get('date') != today:
            _anon_usage[ip] = {'date': today, 'count': 1}
            return False
        entry['count'] = int(entry.get('count') or 0) + 1
        return entry['count'] > ANON_QUERY_DAILY_LIMIT
