"""
Limitless 缺卡重新對卡 — 用最新匹配邏輯重掃 limitless_deck_cards 入面
local_tw_card_id 係 NULL 嘅行。

設計要點：
- 只補 NULL 綁定，唔會覆蓋任何現有值（persist_local_tw_binding 本身有 WHERE NULL 保護）
- 預設只掃 jp 行（詳情頁「中文牌庫缺卡」同導入都係用 jp 行）；en 行可以參數開
- 分批 transaction（每批 commit），逐行 savepoint，單行失敗唔會拖累成批
- 對卡完全重用 repository.resolve_local_tw_card_row（set+編號 → 名 → limitless mapping → tcgdex）
- tcgdex fallback 預設關（殘餘行可能每卡幾個外部請求，好慢）；開咗只會對 SQL 匹配唔到
  嘅行，而且只有 card_id 確實存在於中文 cards 表先寫綁定，避免寫入 JOIN 唔到的死綁定
"""
from __future__ import annotations

import re
import threading
import time
import traceback

import database

from . import repository

BATCH_SIZE = 200


def _positive_int(value, default=None):
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _normalize_languages(languages) -> list[str]:
    if isinstance(languages, str):
        languages = [languages]
    result = []
    for item in (languages or []):
        lang = str(item).strip().lower()
        if lang in ("jp", "en") and lang not in result:
            result.append(lang)
    return result or ["jp"]


def _normalize_deck_ids(deck_ids) -> list[str] | None:
    if not deck_ids:
        return None
    if isinstance(deck_ids, str):
        parts = re.split(r"[,\s]+", deck_ids.strip())
    else:
        parts = [str(item).strip() for item in deck_ids]
    ids = [part for part in parts if part]
    return ids or None


class RematchState:
    def __init__(self):
        self._lock = threading.Lock()
        self.running = False
        self.total = 0
        self.processed = 0
        self.matched = 0
        self.tcgdex_only = 0
        self.still_missing = 0
        self.errors = 0
        self.message = "idle"
        self.use_tcgdex = False
        self.started_at = None
        self.finished_at = None
        self.stop_requested = False

    def reset(self, total: int, use_tcgdex: bool):
        with self._lock:
            self.running = True
            self.total = total
            self.processed = 0
            self.matched = 0
            self.tcgdex_only = 0
            self.still_missing = 0
            self.errors = 0
            self.message = "Starting"
            self.use_tcgdex = use_tcgdex
            self.started_at = time.time()
            self.finished_at = None
            self.stop_requested = False

    def update(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)

    def request_stop(self):
        with self._lock:
            self.stop_requested = True

    def finish(self, message: str):
        with self._lock:
            self.running = False
            self.message = message
            self.finished_at = time.time()

    def to_dict(self) -> dict:
        with self._lock:
            if self.started_at:
                end = self.finished_at or time.time()
                seconds = max(0, int(end - self.started_at))
                elapsed = f"{seconds // 60}m {seconds % 60}s"
            else:
                elapsed = ""
            if self.total:
                progress = min(100.0, round(self.processed / self.total * 100, 1))
            else:
                progress = 100.0 if not self.running else 0.0
            return {
                "running": self.running,
                "message": self.message,
                "total": self.total,
                "processed": self.processed,
                "matched": self.matched,
                "tcgdex_only": self.tcgdex_only,
                "still_missing": self.still_missing,
                "errors": self.errors,
                "progress": progress,
                "elapsed": elapsed,
                "use_tcgdex": self.use_tcgdex,
            }


rematch_state = RematchState()
_runner_lock = threading.Lock()


def _missing_where(deck_ids: list[str] | None, languages: list[str]) -> tuple[str, list]:
    where = [
        "local_tw_card_id IS NULL",
        "COALESCE(card_name, '') <> ''",
        "language = ANY(%s::text[])",
    ]
    params: list = [languages]
    if deck_ids:
        where.append("deck_id = ANY(%s::text[])")
        params.append(deck_ids)
    return " AND ".join(where), params


def count_missing(deck_ids=None, languages=None) -> int:
    conn = database.get_db_connection()
    if not conn:
        raise RuntimeError("Database unavailable")
    try:
        cursor = conn.cursor()
        where, params = _missing_where(_normalize_deck_ids(deck_ids), _normalize_languages(languages))
        cursor.execute(f"SELECT COUNT(*) AS cnt FROM limitless_deck_cards WHERE {where}", params)
        return int(cursor.fetchone()["cnt"] or 0)
    finally:
        conn.close()


def missing_stats() -> dict:
    repository.ensure_schema()
    conn = database.get_db_connection()
    if not conn:
        raise RuntimeError("Database unavailable")
    try:
        cursor = conn.cursor()
        base_where, params = _missing_where(None, ["jp", "en"])
        cursor.execute(
            f"SELECT COUNT(*) AS cnt, COUNT(DISTINCT deck_id) AS decks "
            f"FROM limitless_deck_cards WHERE {base_where}",
            params,
        )
        row = cursor.fetchone()
        cursor.execute(
            f"SELECT language, mode, COUNT(*) AS cnt FROM limitless_deck_cards "
            f"WHERE {base_where} GROUP BY language, mode ORDER BY cnt DESC",
            params,
        )
        by_language_mode = [
            {"language": r["language"], "mode": r["mode"], "cnt": int(r["cnt"] or 0)}
            for r in cursor.fetchall()
        ]
        cursor.execute(
            f"SELECT COALESCE(NULLIF(set_code, ''), '(no set)') AS set_code, COUNT(*) AS cnt "
            f"FROM limitless_deck_cards WHERE {base_where} GROUP BY 1 ORDER BY cnt DESC LIMIT 20",
            params,
        )
        top_sets = [{"set_code": r["set_code"], "cnt": int(r["cnt"] or 0)} for r in cursor.fetchall()]
        cursor.execute(
            f"""
            SELECT COALESCE(NULLIF(set_code, ''), '(no set)') AS set_code,
                   COALESCE(NULLIF(set_number, ''), '-') AS set_number,
                   card_name,
                   COALESCE(section, '-') AS section,
                   COUNT(*) AS cnt
            FROM limitless_deck_cards
            WHERE {base_where}
            GROUP BY 1, 2, 3, 4
            ORDER BY cnt DESC
            LIMIT 50
            """,
            params,
        )
        top_missing_cards = []
        for r in cursor.fetchall():
            top_missing_cards.append({
                "set_code": r["set_code"],
                "set_number": r["set_number"],
                "card_name": r["card_name"],
                "section": r["section"],
                "cnt": int(r["cnt"] or 0),
            })
        return {
            "total_missing": int(row["cnt"] or 0),
            "affected_decks": int(row["decks"] or 0),
            "by_language_mode": by_language_mode,
            "top_sets": top_sets,
            "top_missing_cards": top_missing_cards,
        }
    finally:
        conn.close()


def _fetch_missing_batch(cursor, last_id: int, deck_ids, languages, batch_size: int) -> list[dict]:
    where, params = _missing_where(deck_ids, languages)
    params.append(last_id)
    params.append(batch_size)
    cursor.execute(
        f"""
        SELECT id, deck_id, language, mode, section, card_name, set_code, set_number
        FROM limitless_deck_cards
        WHERE {where} AND id > %s
        ORDER BY id
        LIMIT %s
        """,
        params,
    )
    return [dict(row) for row in cursor.fetchall()]


def _rematch_single_row(cursor, row: dict, use_tcgdex: bool) -> str:
    """對一行缺卡，回傳 'matched' | 'tcgdex_only' | 'missing'。"""
    tw_row = repository.resolve_local_tw_card_row(cursor, row)
    if tw_row and tw_row.get("card_id"):
        repository.persist_local_tw_binding(cursor, row, tw_row["card_id"])
        return "matched"
    if not use_tcgdex:
        return "missing"
    tw_card = repository._find_tw_by_tcgdex(cursor, row)
    card_id = str((tw_card or {}).get("card_id") or "")
    if not card_id:
        return "missing"
    cursor.execute("SELECT 1 FROM cards WHERE card_id = %s", (card_id,))
    if not cursor.fetchone():
        return "tcgdex_only"
    repository.persist_local_tw_binding(cursor, row, card_id)
    return "matched"


def _run_rematch(options: dict) -> None:
    deck_ids = options.get("deck_ids")
    limit = options.get("limit")
    use_tcgdex = bool(options.get("use_tcgdex"))
    languages = options.get("languages") or ["jp"]
    last_error = ""
    try:
        repository.ensure_schema()
        total = count_missing(deck_ids=deck_ids, languages=languages)
        rematch_state.reset(total=total, use_tcgdex=use_tcgdex)
        repository.log_event(
            "info", "limitless-rematch",
            f"Rematch started (total={total}, languages={'+'.join(languages)}, "
            f"tcgdex={'on' if use_tcgdex else 'off'}, limit={limit or 'all'})",
        )
        processed = matched = tcgdex_only = still_missing = errors = 0
        last_id = 0
        consecutive_failures = 0
        stopped = False
        while True:
            if rematch_state.stop_requested:
                stopped = True
                break
            if limit and processed >= limit:
                break
            conn = database.get_db_connection()
            if not conn:
                raise RuntimeError("Database unavailable")
            rows = None
            outcomes: list[str] = []
            try:
                cursor = conn.cursor()
                remaining = (limit - processed) if limit else BATCH_SIZE
                rows = _fetch_missing_batch(
                    cursor, last_id, deck_ids, languages, min(BATCH_SIZE, max(1, remaining)),
                )
                if not rows:
                    break
                for row in rows:
                    outcome = "error"
                    try:
                        cursor.execute("SAVEPOINT rematch_row")
                        outcome = _rematch_single_row(cursor, row, use_tcgdex)
                        cursor.execute("RELEASE SAVEPOINT rematch_row")
                    except Exception:
                        outcome = "error"
                        try:
                            cursor.execute("ROLLBACK TO SAVEPOINT rematch_row")
                        except Exception:
                            pass
                    outcomes.append(outcome)
                conn.commit()
                processed += len(rows)
                matched += outcomes.count("matched")
                tcgdex_only += outcomes.count("tcgdex_only")
                still_missing += outcomes.count("missing")
                errors += outcomes.count("error")
                last_id = rows[-1]["id"]
                consecutive_failures = 0
                rematch_state.update(
                    processed=processed, matched=matched, tcgdex_only=tcgdex_only,
                    still_missing=still_missing, errors=errors,
                    message=f"處理中：{processed}/{total}（修好 {matched}，仍缺 {still_missing}）",
                )
            except Exception:
                try:
                    conn.rollback()
                except Exception:
                    pass
                if rows:
                    processed += len(rows)
                    errors += len(rows)
                    last_id = rows[-1]["id"]
                consecutive_failures += 1
                last_error = traceback.format_exc()
                repository.log_event("error", "limitless-rematch", "Batch failed; skipped", last_error[:2000])
                if rows is None and consecutive_failures >= 3:
                    raise RuntimeError(f"Missing-batch query failed repeatedly: {last_error}")
            finally:
                conn.close()
        summary = (
            f"Rematch finished: processed={processed} matched={matched} "
            f"tcgdex_only={tcgdex_only} still_missing={still_missing} errors={errors}"
            + (" (stopped)" if stopped else "")
        )
        prefix = "已停止" if stopped else "完成"
        rematch_state.finish(
            f"{prefix}：處理 {processed}，修好 {matched}，仍缺 {still_missing}，錯誤 {errors}"
        )
        repository.log_event(
            "info" if errors == 0 else "warning",
            "limitless-rematch", summary,
            last_error[:2000] if last_error else None,
        )
        try:
            from services.auto_update_runs import record_run
            record_run(
                service='limitless',
                kind='rematch',
                success=(not stopped) and errors == 0,
                message=summary,
                stats=rematch_state.to_dict(),
                failure_count=errors,
                started_at=rematch_state.started_at,
                finished_at=rematch_state.finished_at,
            )
        except Exception:
            pass
    except Exception as exc:
        rematch_state.finish(f"重新對卡失敗：{exc}")
        repository.log_event("error", "limitless-rematch", "Rematch failed", traceback.format_exc())


def start_rematch(deck_ids=None, limit=None, use_tcgdex=False, languages=None) -> tuple[bool, str, dict]:
    with _runner_lock:
        if rematch_state.running:
            return False, "缺卡重新對卡已在執行中", get_status()
        try:
            from .updater import get_status as updater_get_status
            if updater_get_status().get("running"):
                return False, "Limitless 更新執行中，請等佢完成先再重新對卡", get_status()
        except Exception:
            pass
        options = {
            "deck_ids": _normalize_deck_ids(deck_ids),
            "limit": _positive_int(limit),
            "use_tcgdex": bool(use_tcgdex),
            "languages": _normalize_languages(languages),
        }
        rematch_state.reset(total=0, use_tcgdex=options["use_tcgdex"])
        thread = threading.Thread(target=_run_rematch, args=(options,), daemon=True)
        thread.start()
    return True, "缺卡重新對卡已開始", get_status()


def stop_rematch() -> tuple[bool, str]:
    if not rematch_state.running:
        return False, "冇執行中的重新對卡"
    rematch_state.request_stop()
    return True, "已要求停止，完成當前批次後退出"


def is_running() -> bool:
    return rematch_state.running


def get_status() -> dict:
    return rematch_state.to_dict()
