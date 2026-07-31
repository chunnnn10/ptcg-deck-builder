"""
自動更新執行歷史 — 將每次背景更新（JP 牌組 / Limitless）的執行結果寫入
auto_update_runs 表，供 admin 檢視與排錯。時間統一以 UTC 字串記錄。
"""
import json
import time

import database


def _utc_now_str(epoch=None):
    return time.strftime('%Y-%m-%d %H:%M:%S', time.gmtime(epoch if epoch is not None else time.time()))


def record_run(service, kind, success, message, stats=None, failure_count=0,
               started_at=None, finished_at=None):
    """記錄一次自動更新的執行結果。
    service: 'jp_decks' / 'limitless'
    started_at / finished_at: epoch 秒（None 表示取當前 UTC 時間）
    """
    conn = database.get_db_connection()
    if not conn:
        print(f"[auto_update_runs] record_run failed: no DB connection", flush=True)
        return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO auto_update_runs
                (service, kind, started_at, finished_at, success, message, stats_json, failure_count)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            service,
            kind or '',
            _utc_now_str(started_at) if started_at else None,
            _utc_now_str(finished_at) if finished_at else None,
            bool(success),
            message or '',
            json.dumps(stats or {}, ensure_ascii=False),
            int(failure_count or 0),
        ))
        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"[auto_update_runs] record_run failed: {e}", flush=True)
    finally:
        conn.close()


def list_runs(service=None, limit=50):
    """列出最近的執行記錄（時間為 UTC）。"""
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        if service:
            cursor.execute(
                "SELECT * FROM auto_update_runs WHERE service = %s ORDER BY started_at DESC NULLS LAST LIMIT %s",
                (service, limit),
            )
        else:
            cursor.execute(
                "SELECT * FROM auto_update_runs ORDER BY started_at DESC NULLS LAST LIMIT %s",
                (limit,),
            )
        rows = cursor.fetchall()
        result = []
        for row in rows:
            stats = {}
            try:
                stats = json.loads(row.get('stats_json') or '{}')
            except Exception:
                pass
            result.append({
                'id': row['id'],
                'service': row['service'],
                'kind': row['kind'],
                'started_at': row['started_at'],
                'finished_at': row['finished_at'],
                'success': row['success'],
                'message': row['message'],
                'failure_count': row['failure_count'],
                'stats': stats,
            })
        return result
    except Exception as e:
        print(f"[auto_update_runs] list_runs failed: {e}", flush=True)
        return []
    finally:
        conn.close()
