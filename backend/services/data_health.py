"""
重啟／手動資料健康檢查。

只做唯讀對帳，唔會喺 startup 自動開爬蟲。
發現缺漏後寫入 data_health_reports，等 admin 確認先背景修復。
"""
from __future__ import annotations

import json
import threading
import time
import traceback

import database

SERVICE_NAME = "data_health"

HEALTH_STATE = {
    "running": False,
    "phase": "idle",
    "progress": 0.0,
    "message": "就緒",
    "repairing": False,
    "report": None,
    "logs": [],
}


def _log(message: str) -> None:
    HEALTH_STATE["message"] = message
    logs = HEALTH_STATE.get("logs") or []
    logs.append({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "message": message})
    HEALTH_STATE["logs"] = logs[-80:]
    print(f">>> [Data Health] {message}", flush=True)


def get_status() -> dict:
    return dict(HEALTH_STATE)


def _safe_count(cursor, sql: str, params=None) -> int:
    try:
        cursor.execute(sql, params or ())
        row = cursor.fetchone()
        if not row:
            return 0
        if isinstance(row, dict):
            return int(next(iter(row.values())) or 0)
        return int(row[0] or 0)
    except Exception:
        try:
            cursor.connection.rollback()
        except Exception:
            pass
        return 0


def _safe_rows(cursor, sql: str, params=None) -> list[dict]:
    try:
        cursor.execute(sql, params or ())
        return [dict(row) for row in cursor.fetchall()]
    except Exception:
        try:
            cursor.connection.rollback()
        except Exception:
            pass
        return []


def scan_database(trigger: str = "manual") -> dict:
    started = time.time()
    HEALTH_STATE.update({
        "running": True,
        "phase": "scanning",
        "progress": 5,
        "repairing": False,
        "logs": HEALTH_STATE.get("logs") or [],
    })
    _log(f"開始資料健康檢查 ({trigger})")

    report = {
        "trigger": trigger,
        "started_at": time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(started)),
        "issues": [],
        "counts": {},
        "needs_repair": False,
        "issue_count": 0,
    }

    conn = database.get_db_connection()
    if not conn:
        report["error"] = "Database unavailable"
        HEALTH_STATE.update({"running": False, "phase": "error", "progress": 0, "report": report})
        return report

    try:
        cursor = conn.cursor()
        HEALTH_STATE["progress"] = 20

        tw_empty_sets = _safe_rows(
            cursor,
            """
            SELECT s.set_code, s.set_name
            FROM expansion_sets s
            LEFT JOIN cards c ON c.set_code = s.set_code
            WHERE c.card_id IS NULL
            ORDER BY s.last_updated DESC NULLS LAST
            LIMIT 50
            """,
        )
        tw_incomplete = _safe_count(
            cursor,
            """
            SELECT COUNT(*) FROM cards
            WHERE COALESCE(name, '') = ''
               OR COALESCE(image_file, '') = ''
               OR COALESCE(set_code, '') = ''
            """,
        )
        jp_empty_sets = _safe_rows(
            cursor,
            """
            SELECT s.set_code, s.set_name
            FROM jp_expansion_sets s
            LEFT JOIN jp_cards c ON c.set_code = s.set_code
            WHERE c.card_id IS NULL
            ORDER BY s.last_updated DESC NULLS LAST
            LIMIT 50
            """,
        )
        jp_incomplete = _safe_count(
            cursor,
            """
            SELECT COUNT(*) FROM jp_cards
            WHERE COALESCE(name, '') = ''
               OR COALESCE(image_file, '') = ''
               OR COALESCE(set_code, '') = ''
            """,
        )
        jp_pages = 0
        jp_source_est = 0
        jp_missing_newest = []
        jp_page1_count = 0
        jp_last_count = 0
        try:
            from services.deck_importer.deck_updater import count_source_decks, BASE_URL, _fetch_with_retry, parse_deck_articles
            snapshot = count_source_decks("DJ")
            jp_pages = int(snapshot.get("pages") or 0)
            jp_page1_count = int(snapshot.get("per_page") or 0)
            jp_last_count = int(snapshot.get("last_page_count") or 0)
            jp_source_est = int(snapshot.get("total") or 0)
            page1_html = _fetch_with_retry("GET", f"{BASE_URL}/DJ?path=DJ&page=1")
            page1_decks = parse_deck_articles(page1_html.text) if page1_html else []
            newest_codes = [item.get("code") for item in page1_decks if item.get("code")]
            if newest_codes:
                cursor.execute(
                    "SELECT deck_code FROM imported_decks WHERE deck_code = ANY(%s)",
                    (newest_codes,),
                )
                have = {
                    row["deck_code"] if isinstance(row, dict) else row[0]
                    for row in cursor.fetchall()
                }
                jp_missing_newest = [code for code in newest_codes if code not in have]
        except Exception as exc:
            _log(f"讀取日文牌組來源頁數失敗：{exc}")

        HEALTH_STATE["progress"] = 55
        empty_decks = _safe_count(
            cursor,
            """
            SELECT COUNT(*) FROM imported_decks
            WHERE card_list IS NULL
               OR card_list = ''
               OR card_list = '[]'
            """,
        )
        jp_deck_total = _safe_count(cursor, "SELECT COUNT(*) FROM imported_decks")
        jp_deck_local = _safe_count(cursor, "SELECT COUNT(*) FROM imported_decks WHERE COALESCE(source,'jp') IN ('jp','DJ','dj')")
        if not jp_deck_local:
            jp_deck_local = jp_deck_total
        limitless_deck_total = _safe_count(cursor, "SELECT COUNT(*) FROM limitless_decks")
        limitless_tournament_total = _safe_count(cursor, "SELECT COUNT(*) FROM limitless_tournaments")
        limitless_empty_decks = _safe_count(
            cursor,
            """
            SELECT COUNT(*) FROM limitless_decks d
            WHERE NOT EXISTS (
                SELECT 1 FROM limitless_deck_cards c WHERE c.deck_id = d.deck_id
            )
            """,
        )
        limitless_unmapped_decks = _safe_count(
            cursor,
            """
            SELECT COUNT(*) FROM (
                SELECT c.deck_id
                FROM limitless_deck_cards c
                WHERE c.language = 'jp'
                GROUP BY c.deck_id
                HAVING COUNT(*) FILTER (
                    WHERE c.local_tw_card_id IS NULL OR c.local_tw_card_id = ''
                ) >= 3
            ) missing_decks
            """,
        )
        limitless_unmapped = _safe_count(
            cursor,
            """
            SELECT COUNT(*) FROM limitless_deck_cards
            WHERE language = 'jp'
              AND (local_tw_card_id IS NULL OR local_tw_card_id = '')
            """,
        )
        pending_bindings = _safe_count(
            cursor,
            "SELECT COUNT(*) FROM card_locale_bindings WHERE status = 'pending'",
        )
        provisional_open = _safe_count(
            cursor,
            """
            SELECT COUNT(*) FROM provisional_cards
            WHERE status IN ('pending', 'approved')
            """,
        )

        counts = {
            "tw_empty_sets": len(tw_empty_sets),
            "tw_incomplete_cards": tw_incomplete,
            "jp_empty_sets": len(jp_empty_sets),
            "jp_incomplete_cards": jp_incomplete,
            "jp_deck_total": jp_deck_total,
            "jp_deck_local": jp_deck_local,
            "jp_source_pages": jp_pages,
            "jp_source_est": jp_source_est,
            "jp_page1_count": jp_page1_count,
            "jp_last_count": jp_last_count,
            "jp_missing_newest": len(jp_missing_newest),
            "empty_imported_decks": empty_decks,
            "limitless_deck_total": limitless_deck_total,
            "limitless_tournament_total": limitless_tournament_total,
            "limitless_empty_decks": limitless_empty_decks,
            "limitless_unmapped_decks": limitless_unmapped_decks,
            "limitless_unmapped": limitless_unmapped,
            "pending_bindings": pending_bindings,
            "provisional_open": provisional_open,
        }
        report["counts"] = counts
        report["samples"] = {
            "tw_empty_sets": tw_empty_sets[:12],
            "jp_empty_sets": jp_empty_sets[:12],
        }

        issues = []
        jp_lag = max(0, (jp_source_est - jp_deck_local) if jp_source_est else 0)
        if jp_missing_newest:
            issues.append(
                f"日文來源最新頁有 {len(jp_missing_newest)} 副未入庫（例如 {', '.join(jp_missing_newest[:5])}）"
            )
        if jp_source_est and jp_deck_local < jp_source_est:
            issues.append(
                f"日文牌組庫少於來源：本地 {jp_deck_local} 副，來源 {jp_source_est} 副（{jp_pages} 頁 × 首頁 {jp_page1_count} + 末頁 {jp_last_count}）"
            )
        if empty_decks:
            issues.append(f"日文牌組缺詳情／空牌表：{empty_decks} / {jp_deck_total} 副")
        if limitless_empty_decks:
            issues.append(f"Limitless 牌組沒有牌表：{limitless_empty_decks} / {limitless_deck_total} 副")
        if pending_bindings:
            issues.append(f"自動對卡待批准：{pending_bindings} 筆")
        if tw_empty_sets:
            issues.append(f"中文系列未收錄卡牌：{len(tw_empty_sets)} 個（參考，唔會單靠呢項觸發狂爬）")
        if jp_empty_sets:
            issues.append(f"日文系列未收錄卡牌：{len(jp_empty_sets)} 個（參考，唔會單靠呢項觸發狂爬）")
        if provisional_open:
            issues.append(f"未發售臨時卡待處理：{provisional_open} 張")

        report["issues"] = issues
        report["issue_count"] = len(issues)
        report["needs_repair"] = bool(jp_lag or empty_decks or limitless_empty_decks)
        report["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())

        cursor.execute(
            """
            INSERT INTO data_health_reports
                (trigger, started_at, finished_at, summary_json, needs_repair, acknowledged, repair_started)
            VALUES (%s, %s, %s, %s, %s, FALSE, FALSE)
            RETURNING id
            """,
            (
                trigger,
                report["started_at"],
                report["finished_at"],
                json.dumps(report, ensure_ascii=False),
                report["needs_repair"],
            ),
        )
        row = cursor.fetchone()
        report["id"] = row["id"] if isinstance(row, dict) else row[0]
        conn.commit()
        _log(f"檢查完成：{report['issue_count']} 類缺漏" if issues else "檢查完成：未發現缺漏")
    except Exception as exc:
        try:
            conn.rollback()
        except Exception:
            pass
        report["error"] = str(exc)
        _log(f"檢查失敗：{exc}")
        traceback.print_exc()
    finally:
        conn.close()

    HEALTH_STATE.update({
        "running": False,
        "phase": "ready",
        "progress": 100,
        "report": report,
    })
    return report


def latest_report() -> dict | None:
    if HEALTH_STATE.get("report"):
        return HEALTH_STATE["report"]
    conn = database.get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT * FROM data_health_reports
            ORDER BY started_at DESC NULLS LAST
            LIMIT 1
            """
        )
        row = cursor.fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            summary = json.loads(data.get("summary_json") or "{}")
        except Exception:
            summary = {}
        summary["id"] = data.get("id")
        summary["acknowledged"] = bool(data.get("acknowledged"))
        summary["repair_started"] = bool(data.get("repair_started"))
        HEALTH_STATE["report"] = summary
        return summary
    except Exception:
        return None
    finally:
        conn.close()


def acknowledge(report_id: int | None = None) -> None:
    conn = database.get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        if report_id:
            cursor.execute(
                "UPDATE data_health_reports SET acknowledged = TRUE WHERE id = %s",
                (report_id,),
            )
        else:
            cursor.execute(
                """
                UPDATE data_health_reports
                SET acknowledged = TRUE
                WHERE id = (
                    SELECT id FROM data_health_reports
                    ORDER BY started_at DESC NULLS LAST LIMIT 1
                )
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()
    report = HEALTH_STATE.get("report") or {}
    report["acknowledged"] = True
    HEALTH_STATE["report"] = report


def _mark_repair_started(report_id: int | None) -> None:
    conn = database.get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        if report_id:
            cursor.execute(
                "UPDATE data_health_reports SET repair_started = TRUE WHERE id = %s",
                (report_id,),
            )
        else:
            cursor.execute(
                """
                UPDATE data_health_reports
                SET repair_started = TRUE
                WHERE id = (
                    SELECT id FROM data_health_reports
                    ORDER BY started_at DESC NULLS LAST LIMIT 1
                )
                """
            )
        conn.commit()
    except Exception:
        conn.rollback()
    finally:
        conn.close()


def _sync_jp_newest_pages(max_pages: int = 60) -> int:
    from services.deck_importer.deck_updater import crawl_list_page, detect_total_pages
    total_pages = min(max_pages, int(detect_total_pages("DJ") or max_pages))
    added = 0
    conn = database.get_db_connection()
    if not conn:
        return 0
    try:
        cursor = conn.cursor()
        for page in range(1, total_pages + 1):
            HEALTH_STATE["progress"] = 20 + min(30, page)
            _log(f"同步日文列表第 {page} 頁")
            before = _safe_count(cursor, "SELECT COUNT(*) FROM imported_decks")
            crawl_list_page("DJ", page)
            after = _safe_count(cursor, "SELECT COUNT(*) FROM imported_decks")
            gained = max(0, after - before)
            added += gained
            if page >= 3 and gained == 0:
                _log(f"第 {page} 頁已無新日文牌組，停止往後掃")
                break
        return added
    finally:
        conn.close()


def _fill_limitless_empty_decks(limit: int = 80) -> tuple[int, int]:
    conn = database.get_db_connection()
    if not conn:
        return 0, 0
    try:
        cursor = conn.cursor()
        rows = _safe_rows(
            cursor,
            """
            SELECT d.deck_id
            FROM limitless_decks d
            WHERE NOT EXISTS (
                SELECT 1 FROM limitless_deck_cards c WHERE c.deck_id = d.deck_id
            )
            ORDER BY d.updated_at DESC NULLS LAST
            LIMIT %s
            """,
            (limit,),
        )
        if not rows:
            rows = _safe_rows(
                cursor,
                """
                SELECT d.deck_id
                FROM limitless_decks d
                WHERE NOT EXISTS (
                    SELECT 1 FROM limitless_deck_cards c WHERE c.deck_id = d.deck_id
                )
                LIMIT %s
                """,
                (limit,),
            )
    finally:
        conn.close()

    if not rows:
        return 0, 0
    from services.limitless_decks.updater import update_deck
    filled = 0
    failed = 0
    for index, row in enumerate(rows, start=1):
        deck_id = row.get("deck_id")
        HEALTH_STATE["progress"] = 55 + min(20, index)
        _log(f"補 Limitless 牌表 {index}/{len(rows)}：{deck_id}")
        try:
            update_deck(deck_id)
            filled += 1
        except Exception as exc:
            failed += 1
            _log(f"{deck_id} 失敗：{exc}")
    return filled, failed


def start_repair(report: dict | None = None) -> tuple[bool, str]:
    if HEALTH_STATE.get("repairing") or HEALTH_STATE.get("running"):
        return False, "已有健康檢查或修復任務進行中"
    report = report or HEALTH_STATE.get("report") or latest_report() or {}
    thread = threading.Thread(target=_run_repair, args=(report,), daemon=True)
    thread.start()
    return True, "已開始背景修復"


def _run_repair(report: dict) -> None:
    HEALTH_STATE.update({
        "running": True,
        "repairing": True,
        "phase": "repairing",
        "progress": 1,
    })
    _log("Admin 已確認，開始背景修復")
    _mark_repair_started(report.get("id"))
    counts = (report or {}).get("counts") or {}

    try:
        HEALTH_STATE["progress"] = 20
        _log("啟動牌組列表完整更新（探測最後一頁後由第 1 頁掃到尾）")
        from services.deck_importer.deck_updater import run_full_update, get_update_status
        try:
            ok, message = run_full_update()
            _log(str(message))
            if ok:
                for _ in range(240):
                    status = get_update_status() or {}
                    HEALTH_STATE["message"] = status.get("message") or "牌組完整更新進行中"
                    if not status.get("running"):
                        break
                    time.sleep(5)
        except Exception as exc:
            _log(f"牌組完整更新失敗：{exc}")

        HEALTH_STATE["progress"] = 70
        filled, failed = _fill_limitless_empty_decks(limit=80)
        _log(f"Limitless 空牌表補完：成功 {filled}，失敗 {failed}")

        HEALTH_STATE["progress"] = 95
        scan_database(trigger="post_repair")
        _log("背景修復流程結束")
    except Exception as exc:
        _log(f"修復失敗：{exc}")
        traceback.print_exc()
        HEALTH_STATE["phase"] = "error"
    finally:
        HEALTH_STATE["repairing"] = False
        HEALTH_STATE["running"] = False
        if HEALTH_STATE.get("phase") != "error":
            HEALTH_STATE["phase"] = "ready"
        HEALTH_STATE["progress"] = 100


def run_startup_scan() -> None:
    time.sleep(8)
    try:
        scan_database(trigger="startup")
    except Exception as exc:
        print(f">>> [Data Health] startup scan failed: {exc}", flush=True)
        traceback.print_exc()
