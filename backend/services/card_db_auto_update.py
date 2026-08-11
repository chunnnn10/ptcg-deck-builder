"""
單卡庫（cards / jp_cards）每日自動同步服務。

職責：
1. 同步官網擴充包列表（中/日文 → expansion_sets / jp_expansion_sets）。確保 admin 更新頁面
   的系列 dropdown 能即時反映官網推出的新系列。
2. 偵測「資料庫尚未收錄任何卡牌」的新系列，背景執行 crawler.run_update_process /
   jp_crawler.crawl_by_expansions 增量抓取，避免一次跑全站。
3. 把每次執行結果寫入 auto_update_runs 表（service='card_db'），供 admin 檢視。

設計原則：
- 不阻擋服務主循環：每個階段都在獨立 thread 內執行，失敗只記錄不中斷。
- 輕量級：每日只同步新系列（最多 MAX_NEW_SETS 個），不重爬整個資料庫。
- 與既有 jp_decks / limitless 牌組更新服務並存，互不衝突。
"""
import threading
import time
import traceback

import config
import database
from services import auto_update_runs
from services.crawler import crawler, jp_crawler


SERVICE_NAME = "card_db"


def get_status():
    """取得 TW 卡牌庫更新狀態（供 admin UI 輪詢）。"""
    return dict(crawler.UPDATE_STATE)


def get_jp_status():
    """取得 JP 卡牌庫更新狀態。"""
    return dict(jp_crawler.JP_UPDATE_STATE)


def sync_expansion_meta():
    """同步中/日文官網擴充包列表。
    回傳 (tw_count, jp_count)。
    """
    tw_count = 0
    jp_count = 0

    try:
        tw_map = crawler.fetch_expansion_meta()
        tw_count = len(tw_map) if tw_map else 0
    except Exception as e:
        print(f">>> [Card DB Auto Update] TW meta sync failed: {e}", flush=True)

    try:
        jp_list = jp_crawler.fetch_jp_expansion_meta(persist=True)
        jp_count = len(jp_list) if jp_list else 0
    except Exception as e:
        print(f">>> [Card DB Auto Update] JP meta sync failed: {e}", flush=True)

    return tw_count, jp_count


def detect_new_tw_expansion_codes(max_new: int) -> list[str]:
    """找出 expansion_sets 表中、cards 表尚未收錄任何卡牌的系列代碼。
    用 last_updated DESC 排序，優先同步官網最新推出的系列。
    """
    conn = database.get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.set_code
            FROM expansion_sets s
            LEFT JOIN cards c ON c.set_code = s.set_code
            WHERE c.card_id IS NULL
            ORDER BY s.last_updated DESC
            LIMIT %s
            """,
            (int(max_new),),
        )
        rows = cursor.fetchall()
        return [r['set_code'] for r in rows if r.get('set_code')]
    except Exception as e:
        print(f">>> [Card DB Auto Update] detect_new_tw failed: {e}", flush=True)
        return []
    finally:
        conn.close()


def _wait_while_running(get_status_fn, service_label, poll_seconds=15, max_wait_seconds=None):
    """等待背景爬蟲完成，避免循環卡死。"""
    if max_wait_seconds is None:
        max_wait_seconds = max(60, int(config.AUTO_UPDATE_MAX_WAIT_SECONDS))
    deadline = time.time() + max_wait_seconds
    while True:
        try:
            status = get_status_fn()
            if not status.get('running'):
                return
        except Exception:
            return
        if time.time() >= deadline:
            print(
                f">>> [Card DB Auto Update] {service_label} still running after "
                f"{max_wait_seconds}s, skipping wait",
                flush=True,
            )
            return
        time.sleep(poll_seconds)


def run_daily_sync(max_new_tw: int = None, max_new_jp: int = None,
                   update_japanese: bool = True, skip_images: bool = True,
                   jp_workers: int = 6):
    """執行一次每日同步。回傳 dict 統計資料。

    Args:
        max_new_tw: 本次最多抓的中文新系列數量（None → 取 config 預設）。
        max_new_jp: 本次最多抓的日文新系列數量（None → 取 config 預設）。
        update_japanese: 是否在中文爬蟲後跑 日文缺漏補完。
        skip_images: 每日同步預設不下載圖片以節省頻寬（卡牌圖檔通常已快取）。
        jp_workers: JP 爬蟲線程數。
    """
    if max_new_tw is None:
        max_new_tw = max(1, int(getattr(config, 'CARD_DB_AUTO_UPDATE_MAX_NEW_SETS', 3)))
    if max_new_jp is None:
        max_new_jp = max(1, int(getattr(config, 'CARD_DB_AUTO_UPDATE_MAX_NEW_JP_SETS', 3)))

    started = time.time()
    stats = {
        'tw_meta_count': 0,
        'jp_meta_count': 0,
        'tw_new_codes': [],
        'jp_new_codes': [],
        'tw_started': False,
        'jp_started': False,
        'skip_reason': None,
    }

    # 1) 同步擴充包列表（順便刷新 admin 更新頁 dropdown）
    tw_meta_n, jp_meta_n = sync_expansion_meta()
    stats['tw_meta_count'] = tw_meta_n
    stats['jp_meta_count'] = jp_meta_n

    # 2) 偵測新系列
    new_tw_codes = detect_new_tw_expansion_codes(max_new_tw)
    new_jp_codes = []
    try:
        new_jp_codes = jp_crawler.detect_new_jp_expansion_codes(max_new_jp)
    except Exception as e:
        print(f">>> [Card DB Auto Update] detect_new_jp failed: {e}", flush=True)

    stats['tw_new_codes'] = new_tw_codes
    stats['jp_new_codes'] = new_jp_codes

    if not new_tw_codes and not new_jp_codes:
        stats['skip_reason'] = 'no_new_sets'
        auto_update_runs.record_run(
            service=SERVICE_NAME,
            kind='daily',
            success=True,
            message='No new expansion sets to crawl',
            stats=stats,
            started_at=started,
            finished_at=time.time(),
        )
        print(">>> [Card DB Auto Update] no new sets; skipped", flush=True)
        return stats

    # 3) 中文新系列：背景執行 run_update_process
    if new_tw_codes:
        if crawler.UPDATE_STATE.get('running'):
            stats['tw_started'] = False
            stats['skip_reason'] = 'tw_busy'
            print(">>> [Card DB Auto Update] TW crawl already running, skipped", flush=True)
        else:
            stats['tw_started'] = True
            print(
                f">>> [Card DB Auto Update] starting TW crawl for {new_tw_codes}",
                flush=True,
            )
            t = threading.Thread(
                target=crawler.run_update_process,
                args=(new_tw_codes, [1, 2]),
                kwargs={'update_japanese': update_japanese, 'skip_images': skip_images},
                daemon=True,
            )
            t.start()
            _wait_while_running(get_status, 'TW crawl', poll_seconds=15)

    # 4) 日文新系列：背景執行 crawl_by_expansions
    if new_jp_codes:
        if jp_crawler.JP_UPDATE_STATE.get('running'):
            stats['jp_started'] = False
            print(">>> [Card DB Auto Update] JP crawl already running, skipped", flush=True)
        else:
            stats['jp_started'] = True
            print(
                f">>> [Card DB Auto Update] starting JP crawl for {new_jp_codes}",
                flush=True,
            )
            t = threading.Thread(
                target=jp_crawler.crawl_by_expansions,
                args=(new_jp_codes,),
                kwargs={'num_workers': jp_workers, 'skip_images': skip_images},
                daemon=True,
            )
            t.start()
            _wait_while_running(get_jp_status, 'JP crawl', poll_seconds=20)

    auto_update_runs.record_run(
        service=SERVICE_NAME,
        kind='daily',
        success=True,
        message=(
            f"TW new={new_tw_codes} started={stats['tw_started']}, "
            f"JP new={new_jp_codes} started={stats['jp_started']}"
        ),
        stats=stats,
        started_at=started,
        finished_at=time.time(),
    )
    print(">>> [Card DB Auto Update] daily sync finished", flush=True)
    return stats


def run_card_db_auto_update_service():
    """背景服務主循環：每 N 秒執行一次每日同步。"""
    print(">>> [Card DB Auto Update] background service enabled", flush=True)

    # 與其他自動更新服務一致的初始延遲，避免啟動時一起打官網
    delay = max(0, int(getattr(config, 'CARD_DB_AUTO_UPDATE_INITIAL_DELAY_SECONDS', 60) or 0))
    if delay:
        time.sleep(delay)

    while True:
        try:
            print(
                f">>> [Card DB Auto Update] {time.strftime('%Y-%m-%d %H:%M:%S')} "
                f"starting daily sync",
                flush=True,
            )
            run_daily_sync()
        except Exception as e:
            print(f">>> [Card DB Auto Update] error: {e}", flush=True)
            traceback.print_exc()
            try:
                auto_update_runs.record_run(
                    service=SERVICE_NAME,
                    kind='daily',
                    success=False,
                    message=f'exception: {e}',
                    started_at=time.time(),
                    finished_at=time.time(),
                )
            except Exception:
                pass

        interval = max(
            60,
            int(getattr(config, 'CARD_DB_AUTO_UPDATE_INTERVAL_SECONDS', 86400) or 86400),
        )
        time.sleep(interval)