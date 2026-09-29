"""DB-backed job store for the AI assistant (Phase 2).

Why: the previous registry lived in a process-local dict in assistant.py and was
polled by a separate HTTP request. Under gunicorn with multiple workers behind
round-robin load balancing, roughly half of the polls landed on the wrong worker
and returned "AI job not found", and jobs had no owner binding. This module keeps
job state in Postgres so any worker can serve a poll for any job, and so a job is
only visible to the user that created it.

Interface is intentionally frozen: later rounds (assistant.py / routes.py) consume
these functions verbatim.

  create_job(user_id, message, context=None, kind='chat') -> str
  get_job(job_id, user_id=None) -> dict | None
  set_running(job_id) -> bool
  set_done(job_id, result) -> bool
  set_failed(job_id, error) -> bool
  get_recent_jobs(user_id, limit=20) -> list[dict]
  cleanup_old_jobs(days=7) -> int

Contract notes:
- No function raises. DB problems are logged and reported as None / False / 0.
- Table creation is lazy and idempotent, wrapped in a SAVEPOINT so a schema error
  never forces a rollback that would kill work the caller already did in the same
  transaction.
- ``context`` is accepted for interface compatibility. The row shape is fixed by
  the Phase 2 spec (no context column), so it is not persisted; consumers use the
  context in-process when they start the job.
"""

from __future__ import annotations

import json
import threading
import uuid
from typing import Any

_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS ai_assistant_jobs (
    id TEXT PRIMARY KEY,
    user_id TEXT,
    kind TEXT NOT NULL DEFAULT 'chat',
    status TEXT NOT NULL DEFAULT 'pending',
    message TEXT DEFAULT '',
    result JSONB,
    error TEXT DEFAULT '',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_ai_assistant_jobs_user ON ai_assistant_jobs(user_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_ai_assistant_jobs_status ON ai_assistant_jobs(status);
CREATE INDEX IF NOT EXISTS idx_ai_assistant_jobs_updated ON ai_assistant_jobs(updated_at);
"""

_COLUMNS = "id, user_id, kind, status, message, result, error, created_at, updated_at"

_schema_lock = threading.Lock()
_schema_ready = False


def _connect():
    """Open a connection using the project database helper (imported lazily to
    avoid an import cycle: database -> ai_assistant.schema, not job_store)."""
    try:
        import database

        return database.get_db_connection()
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[job_store] database connection failed: {exc}")
        return None


def _ensure_table(conn) -> bool:
    """Lazily create the job table. Idempotent and SAVEPOINT-safe."""
    global _schema_ready
    if _schema_ready:
        return True
    with _schema_lock:
        if _schema_ready:
            return True
        try:
            cursor = conn.cursor()
        except Exception as exc:
            print(f"[job_store] cursor unavailable: {exc}")
            return False
        try:
            cursor.execute("SAVEPOINT job_store_schema")
        except Exception:
            pass
        try:
            cursor.execute(_TABLE_SQL)
            try:
                cursor.execute("RELEASE SAVEPOINT job_store_schema")
            except Exception:
                pass
            _schema_ready = True
            return True
        except Exception as exc:
            print(f"[job_store] schema ensure failed: {exc}")
            try:
                cursor.execute("ROLLBACK TO SAVEPOINT job_store_schema")
            except Exception:
                pass
            return False


def _row_to_dict(row: Any) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    result = data.get("result")
    if isinstance(result, (str, bytes, bytearray)):
        try:
            result = json.loads(result)
        except Exception:
            result = None
    data["result"] = result
    data["error"] = data.get("error") or ""
    for key in ("created_at", "updated_at"):
        value = data.get(key)
        if value is not None and hasattr(value, "isoformat"):
            data[key] = value.isoformat()
    return {
        "id": data.get("id"),
        "user_id": data.get("user_id"),
        "kind": data.get("kind"),
        "status": data.get("status"),
        "message": data.get("message"),
        "result": data.get("result"),
        "error": data.get("error"),
        "created_at": data.get("created_at"),
        "updated_at": data.get("updated_at"),
    }


def create_job(user_id: str, message: str, context: dict | None = None, kind: str = "chat") -> str:
    """Create a pending job. Returns the job_id (uuid4 text), or '' on failure."""
    job_id = str(uuid.uuid4())
    conn = _connect()
    if not conn:
        return ""
    try:
        if not _ensure_table(conn):
            return ""
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO ai_assistant_jobs (id, user_id, kind, status, message, result, error, updated_at)
            VALUES (%s, %s, %s, 'pending', %s, NULL, '', CURRENT_TIMESTAMP)
            """,
            (job_id, str(user_id or ""), str(kind or "chat"), str(message or "")),
        )
        conn.commit()
        return job_id
    except Exception as exc:
        print(f"[job_store] create_job failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return ""
    finally:
        try:
            conn.close()
        except Exception:
            pass


def get_job(job_id: str, user_id: str | None = None) -> dict | None:
    """Fetch one job. When user_id is given it must match, otherwise None."""
    conn = _connect()
    if not conn:
        return None
    try:
        if not _ensure_table(conn):
            return None
        cursor = conn.cursor()
        if user_id is None:
            cursor.execute(f"SELECT {_COLUMNS} FROM ai_assistant_jobs WHERE id = %s", (str(job_id or ""),))
        else:
            cursor.execute(
                f"SELECT {_COLUMNS} FROM ai_assistant_jobs WHERE id = %s AND user_id = %s",
                (str(job_id or ""), str(user_id)),
            )
        row = cursor.fetchone()
        conn.commit()
        return _row_to_dict(row)
    except Exception as exc:
        print(f"[job_store] get_job failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return None
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _update(job_id: str, sql: str, params: tuple) -> bool:
    conn = _connect()
    if not conn:
        return False
    try:
        if not _ensure_table(conn):
            return False
        cursor = conn.cursor()
        cursor.execute(sql, params)
        updated = cursor.rowcount > 0
        conn.commit()
        return bool(updated)
    except Exception as exc:
        print(f"[job_store] update failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return False
    finally:
        try:
            conn.close()
        except Exception:
            pass


def set_running(job_id: str) -> bool:
    return _update(
        job_id,
        "UPDATE ai_assistant_jobs SET status = 'running', updated_at = CURRENT_TIMESTAMP WHERE id = %s",
        (str(job_id or ""),),
    )


def set_done(job_id: str, result: dict) -> bool:
    payload = json.dumps(result if result is not None else {}, ensure_ascii=False, default=str)
    return _update(
        job_id,
        """
        UPDATE ai_assistant_jobs
        SET status = 'done', result = %s::jsonb, error = '', updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (payload, str(job_id or "")),
    )


def set_failed(job_id: str, error: str) -> bool:
    return _update(
        job_id,
        """
        UPDATE ai_assistant_jobs
        SET status = 'failed', error = %s, updated_at = CURRENT_TIMESTAMP
        WHERE id = %s
        """,
        (str(error or ""), str(job_id or "")),
    )


def get_recent_jobs(user_id: str, limit: int = 20) -> list[dict]:
    conn = _connect()
    if not conn:
        return []
    try:
        if not _ensure_table(conn):
            return []
        limit = max(1, min(int(limit or 20), 200))
        cursor = conn.cursor()
        cursor.execute(
            f"SELECT {_COLUMNS} FROM ai_assistant_jobs WHERE user_id = %s ORDER BY created_at DESC, id DESC LIMIT %s",
            (str(user_id or ""), limit),
        )
        rows = cursor.fetchall() or []
        conn.commit()
        return [item for item in (_row_to_dict(row) for row in rows) if item]
    except Exception as exc:
        print(f"[job_store] get_recent_jobs failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return []
    finally:
        try:
            conn.close()
        except Exception:
            pass


def cleanup_old_jobs(days: int = 7) -> int:
    """Delete jobs not touched for N days. Returns the number of rows removed."""
    conn = _connect()
    if not conn:
        return 0
    try:
        if not _ensure_table(conn):
            return 0
        days = 7 if days is None else max(0, int(days))
        cursor = conn.cursor()
        cursor.execute(
            "DELETE FROM ai_assistant_jobs WHERE updated_at < (CURRENT_TIMESTAMP - (%s::int * INTERVAL '1 day'))",
            (days,),
        )
        deleted = cursor.rowcount or 0
        conn.commit()
        return int(deleted)
    except Exception as exc:
        print(f"[job_store] cleanup_old_jobs failed: {exc}")
        try:
            conn.rollback()
        except Exception:
            pass
        return 0
    finally:
        try:
            conn.close()
        except Exception:
            pass
