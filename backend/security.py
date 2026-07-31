"""簡易安全輔助模組：真實 IP 解析、失敗限流、未登錄查詢每日配額。

生產環境以 gunicorn --workers 1 單進程運行，記憶體計數器在進程內一致；
若未來擴展為多 worker，此處需改為 Redis/DB 儲存。
"""

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

_lock = threading.Lock()
# 失敗紀錄: action -> {ip: [timestamp, ...]}
_failures: dict[str, dict[str, list[float]]] = {}
# 未登錄查詢配額: ip -> {'date': 'YYYY-MM-DD', 'count': int}
_anon_usage: dict[str, dict[str, object]] = {}


def client_ip() -> str:
    """解析真實客戶端 IP。

    Cloudflare 後方使用 CF-Connecting-IP（Caddy 預設會透傳該 header），
    否則退回直接連線的 remote_addr。
    """
    cf_ip = request.headers.get('CF-Connecting-IP', '').strip()
    if cf_ip and ',' in cf_ip:
        cf_ip = cf_ip.split(',')[0].strip()
    return cf_ip or (request.remote_addr or 'unknown')


def record_failure(action: str, ip: str) -> None:
    """記錄一次失敗（登入失敗/註冊衝突/重設請求）。"""
    if action not in _RATE_LIMITS:
        return
    now = time.time()
    window, _ = _RATE_LIMITS[action]
    with _lock:
        bucket = _failures.setdefault(action, {})
        hits = bucket.get(ip, [])
        hits = [t for t in hits if now - t < window]
        hits.append(now)
        bucket[ip] = hits


def is_rate_limited(action: str, ip: str) -> bool:
    """檢查該 action 是否已達限流門檻。"""
    if action not in _RATE_LIMITS:
        return False
    now = time.time()
    window, limit = _RATE_LIMITS[action]
    with _lock:
        bucket = _failures.setdefault(action, {})
        hits = bucket.get(ip, [])
        hits = [t for t in hits if now - t < window]
        bucket[ip] = hits
        return len(hits) >= limit


def clear_failures(action: str, ip: str) -> None:
    """成功時清除失敗紀錄。"""
    with _lock:
        _failures.get(action, {}).pop(ip, None)


def check_anon_quota(ip: str) -> bool:
    """未登錄查詢配額：每次呼叫計數一次，超過每日上限回傳 True。"""
    today = time.strftime('%Y-%m-%d')
    with _lock:
        entry = _anon_usage.get(ip)
        if not entry or entry.get('date') != today:
            _anon_usage[ip] = {'date': today, 'count': 1}
            return False
        entry['count'] = int(entry.get('count') or 0) + 1
        return entry['count'] > ANON_QUERY_DAILY_LIMIT
