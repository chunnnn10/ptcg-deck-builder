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
        limitless_unmapped = _safe_count(
            cursor,
            """
            SELECT COUNT(*) FROM limitless_deck_cards
            WHERE language = 'jp'
              AND (local_tw_card_id IS NULL OR local_tw_card_id = '')
            """,
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
            "empty_imported_decks": empty_decks,
            "limitless_unmapped": limitless_unmapped,
            "provisional_open": provisional_open,
        }
        report["counts"] = counts
        report["samples"] = {
            "tw_empty_sets": tw_empty_sets[:12],
            "jp_empty_sets": jp_empty_sets[:12],
        }

        issues = []
        if tw_empty_sets:
            issues.append(f"中文系列未收錄卡牌：{len(tw_empty_sets)} 個")
        if tw_incomplete:
            issues.append(f"中文卡資料不完整：{tw_incomplete} 張")
        if jp_empty_sets:
            issues.append(f"日文系列未收錄卡牌：{len(jp_empty_sets)} 個")
        if jp_incomplete:
            issues.append(f"日文卡資料不完整：{jp_incomplete} 張")
        if empty_decks:
            issues.append(f"日本／國際牌組缺詳情：{empty_decks} 副")
        if limitless_unmapped:
            issues.append(f"Limitless 未配中文卡：{limitless_unmapped} 張")
        if provisional_open:
            issues.append(f"未發售臨時卡待處理：{provisional_open} 張")

        report["issues"] = issues
        report["issue_count"] = len(issues)
        report["needs_repair"] = bool(issues)
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
        if counts.get("tw_empty_sets") or counts.get("jp_empty_sets") or counts.get("tw_incomplete_cards"):
            HEALTH_STATE["progress"] = 15
            _log("同步卡牌庫新系列")
            from services.card_db_auto_update import run_daily_sync
            run_daily_sync(skip_images=True)

        if counts.get("empty_imported_decks"):
            HEALTH_STATE["progress"] = 45
            _log("補日本／國際牌組缺漏")
            from services.deck_importer.deck_updater import run_gap_fill_update, run_detail_backfill
            run_gap_fill_update()
            try:
                run_detail_backfill()
            except TypeError:
                run_detail_backfill(worker_count=3)
            except Exception as exc:
                _log(f"牌組詳情補齊略過：{exc}")

        if counts.get("limitless_unmapped"):
            HEALTH_STATE["progress"] = 75
            _log("重跑 Limitless 配對／增量更新")
            try:
                from services.limitless_decks.updater import start_update
                start_update({"mode": "auto-daily"})
            except Exception as exc:
                _log(f"Limitless 更新略過：{exc}")

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
