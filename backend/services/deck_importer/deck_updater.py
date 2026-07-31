"""
牌組更新模組 — 支援每日自動更新 + 完整列表更新 + 增量詳情補齊
資料來源：ptcgtw.shop 列表頁（/DJ 日本、/DE 國際板，SSR HTML）
列表頁：輕量 UPSERT 列表欄位（deck_code 去重），全量 2,060+38 頁約 20-30 分鐘
詳情 API：增量——僅當卡片資料缺失（card_list 空）或超過 N 天未更新才抓取，
          避免對來源站逐筆打 4 萬次請求
牌組卡片以 variant_id + count 格式直接存於 imported_decks.card_list 欄位，
載入時透過 id_mapping 解析為本地卡片。
每次執行結果寫入 auto_update_runs 表（admin 可檢視）。
"""
import json
import os
import re
import sys
import time
import threading
import requests
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
import config
import database
from services.deck_importer.card_resolver import resolve_variant
from services.auto_update_runs import record_run

# ── 常數 ──
BASE_URL = "https://ptcgtw.shop"
# 資料來源：path（列表分頁路徑）/ source（DB 標記）
DECK_SOURCES = [
    {"path": "DJ", "source": "jp"},
    {"path": "DE", "source": "en"},
]
DETAIL_API_URL = "https://ptcgtw.shop/index_function/api/23_01_load_deck_ptcgtw_api.php"

# 每日更新預設掃描頁數（每來源）
DAILY_PAGES_PER_SOURCE = {"DJ": 5, "DE": 2}
# 總頁數偵測失敗時的 fallback（2026-08 實測值）
FALLBACK_PAGES = {"DJ": 2060, "DE": 38}

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://ptcgtw.shop/',
    'Origin': 'https://ptcgtw.shop',
    'x-requested-with': 'XMLHttpRequest'
}

# 請求節流：全域最少間隔（秒），避免打爆來源站
MIN_REQUEST_INTERVAL = 0.5
RETRY_COUNT = 3
REQUEST_TIMEOUT = 20

# 詳情 API 增量策略（可由環境變數覆寫）
DETAIL_REFRESH_DAYS = getattr(config, 'JP_DECK_DETAIL_REFRESH_DAYS', 30)
DETAIL_FETCH_CAP = getattr(config, 'JP_DECK_DETAIL_FETCH_CAP', 500)

# 持久化最新更新時間 / 缺漏偵測摘要（重啟後 admin 仍可顯示）
DECK_UPDATE_META_FILE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), '../../../data/deck_update_meta.json')
)


# ── 全域請求節流 ──
_request_lock = threading.Lock()
_last_request_at = 0.0


def _throttle():
    """全域節流：任何兩次對來源站的請求間隔 >= MIN_REQUEST_INTERVAL 秒。"""
    global _last_request_at
    with _request_lock:
        elapsed = time.time() - _last_request_at
        wait = MIN_REQUEST_INTERVAL - elapsed
        if wait > 0:
            time.sleep(wait)
        _last_request_at = time.time()


def _fetch_with_retry(method, url, payload=None):
    """帶節流與重試的請求。成功回傳 response，失敗回傳 None。"""
    for attempt in range(1, RETRY_COUNT + 1):
        _throttle()
        try:
            if method == 'GET':
                resp = requests.get(url, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            else:
                resp = requests.post(url, json=payload, headers=HEADERS, timeout=REQUEST_TIMEOUT)
            if resp.status_code == 200:
                return resp
            if resp.status_code in (429, 503, 502):
                time.sleep(2 * attempt)
                continue
            return None
        except requests.RequestException:
            time.sleep(2 * attempt)
    return None


# ── 狀態追蹤 ──
class UpdateState:
    def __init__(self):
        self.running = False
        self.mode = ""
        self.total_pages = 0
        self.pages_done = 0
        self.decks_found = 0
        self.decks_new = 0
        self.decks_skipped = 0
        self.decks_failed = 0
        self.cards_total = 0
        self.total_details = 0
        self.details_done = 0
        self.details_failed = 0
        self.message = "就緒"
        self.start_time = None
        self.failed_pages = []
        self._lock = threading.Lock()

    def reset(self, mode, total_pages, total_details=0):
        with self._lock:
            self.running = True
            self.mode = mode
            self.total_pages = total_pages
            self.pages_done = 0
            self.decks_found = 0
            self.decks_new = 0
            self.decks_skipped = 0
            self.decks_failed = 0
            self.cards_total = 0
            self.total_details = total_details
            self.details_done = 0
            self.details_failed = 0
            self.message = "啟動中..."
            self.start_time = time.time()
            self.failed_pages = []

    def update(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, v)

    def increment(self, **kwargs):
        with self._lock:
            for k, v in kwargs.items():
                if hasattr(self, k):
                    setattr(self, k, getattr(self, k) + v)

    def add_failed_page(self, page_label):
        with self._lock:
            self.failed_pages.append(page_label)
            if len(self.failed_pages) > 100:
                self.failed_pages = self.failed_pages[-100:]

    def finish(self, message=None):
        with self._lock:
            self.running = False
            elapsed = ""
            if self.start_time:
                sec = int(time.time() - self.start_time)
                elapsed = f"{sec // 60}分{sec % 60}秒"
            self.message = message or f"更新完成（耗時 {elapsed}）"

    def to_dict(self):
        with self._lock:
            elapsed = ""
            if self.start_time:
                sec = int(time.time() - self.start_time)
                elapsed = f"{sec // 60}分{sec % 60}秒"
            progress = round(self.pages_done / self.total_pages * 100, 1) if self.total_pages > 0 else 0
            return {
                "running": self.running,
                "mode": self.mode,
                "total_pages": self.total_pages,
                "pages_done": self.pages_done,
                "decks_found": self.decks_found,
                "decks_new": self.decks_new,
                "decks_skipped": self.decks_skipped,
                "decks_failed": self.decks_failed,
                "cards_total": self.cards_total,
                "total_details": self.total_details,
                "details_done": self.details_done,
                "details_failed": self.details_failed,
                "failed_pages": list(self.failed_pages),
                "message": self.message,
                "elapsed": elapsed,
                "progress": progress,
            }


update_state = UpdateState()


# ── 輔助函數 ──
def ensure_card_list_column():
    """確保 imported_decks 有 card_list 欄位（舊庫相容）"""
    conn = database.get_db_connection()
    if not conn:
        return
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT column_name FROM information_schema.columns
            WHERE table_name = 'imported_decks'
        """)
        existing = {row['column_name'] for row in cursor.fetchall()}
        if 'card_list' not in existing:
            cursor.execute("ALTER TABLE imported_decks ADD COLUMN card_list TEXT DEFAULT '[]'")
            conn.commit()
            print("[Migration] Added imported_decks.card_list")
    except Exception as e:
        conn.rollback()
        print(f"[Migration] Error: {e}")
    finally:
        conn.close()


def _has_populated_card_list(value):
    if not value:
        return False
    try:
        cards = json.loads(value) if isinstance(value, str) else value
    except Exception:
        return False
    return isinstance(cards, list) and len(cards) > 0


# ── 列表頁解析 ──
def parse_deck_articles(html_content):
    """解析 ptcgtw.shop 列表頁（/DJ、/DE 共用結構）。
    回傳列表 dict：code / date / title / source_url / deck_name / event_name /
    report_text / images / tags
    """
    decks = []
    soup = BeautifulSoup(html_content, 'html.parser')

    for article in soup.find_all('article', class_='deck-card'):
        try:
            img_wrap = article.find('div', class_='deck-image-wrap')
            if not img_wrap:
                continue
            code = (img_wrap.get('data-ptcgtw') or '').strip()
            if not code:
                continue

            images = []
            try:
                images = json.loads(img_wrap.get('data-images') or '[]')
            except Exception:
                pass
            image_url = images[0] if images else ""

            body = article.find('div', class_='deck-body')
            if not body:
                continue

            date_span = None
            meta_div = body.find('div', class_='deck-meta')
            if meta_div:
                date_span = meta_div.find('span')
            deck_date = date_span.get_text(strip=True) if date_span else ""

            title_el = body.find('h2', class_='deck-title')
            title = title_el.get_text(strip=True) if title_el else ""
            source_url = ""
            if title_el:
                a = title_el.find('a', href=True)
                if a:
                    source_url = a.get('href', '')

            name_el = body.find('p', class_='deck-name')
            deck_name = name_el.get_text(strip=True) if name_el else ""

            event_el = body.find('p', class_='deck-event')
            event_name = event_el.get_text(strip=True) if event_el else ""

            content_el = body.find('p', class_='deck-content')
            report_text = content_el.get_text(strip=True) if content_el else ""

            tags = []
            tags_div = article.find('div', class_='deck-tags')
            if tags_div:
                tags = [a.get_text(strip=True) for a in tags_div.find_all('a')]

            decks.append({
                "code": code,
                "date": deck_date,
                "title": title,
                "source_url": source_url,
                "deck_name": deck_name,
                "event_name": event_name,
                "report_text": report_text,
                "images": images,
                "image_url": image_url,
                "tags": tags,
            })
        except Exception:
            continue

    return decks


def detect_total_pages(source_path):
    """動態偵測某來源（DJ/DE）的總頁數，失敗回退 fallback 常數。"""
    try:
        resp = _fetch_with_retry('GET', f"{BASE_URL}/{source_path}?path={source_path}&page=1")
        if resp:
            nums = [int(m) for m in re.findall(r'page=(\d+)', resp.text)]
            if nums:
                return max(nums)
    except Exception:
        pass
    return FALLBACK_PAGES.get(source_path, 2060)


# ── 列表頁 UPSERT（輕量） ──
def upsert_deck_from_list(cursor, deck_info, source):
    """列表欄位 UPSERT，回傳 deck_id；例外由呼叫端處理。"""
    cursor.execute("""
        INSERT INTO imported_decks
            (deck_code, name, deck_date, title, image_url, tags, source,
             source_url, deck_name, event_name, report_text, images_json)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON CONFLICT (deck_code) DO UPDATE SET
            name = EXCLUDED.name,
            deck_date = EXCLUDED.deck_date,
            title = EXCLUDED.title,
            image_url = EXCLUDED.image_url,
            tags = EXCLUDED.tags,
            source = EXCLUDED.source,
            source_url = EXCLUDED.source_url,
            deck_name = EXCLUDED.deck_name,
            event_name = EXCLUDED.event_name,
            report_text = EXCLUDED.report_text,
            images_json = EXCLUDED.images_json
        RETURNING id
    """, (
        deck_info["code"], deck_info["title"], deck_info["date"],
        deck_info["title"], deck_info["image_url"],
        json.dumps(deck_info["tags"], ensure_ascii=False),
        source,
        deck_info["source_url"], deck_info["deck_name"],
        deck_info["event_name"], deck_info["report_text"],
        json.dumps(deck_info["images"], ensure_ascii=False),
    ))
    row = cursor.fetchone()
    return row["id"] if row else None


def crawl_list_page(source_path, page_num, code_collector=None):
    """爬取一頁列表並做輕量 UPSERT（不呼叫詳情 API）。
    回傳 (found, new, failed)。"""
    page_label = f"{source_path}#{page_num}"
    url = f"{BASE_URL}/{source_path}?path={source_path}&page={page_num}"
    resp = _fetch_with_retry('GET', url)
    if not resp:
        update_state.increment(pages_done=1, decks_failed=1)
        update_state.add_failed_page(page_label)
        update_state.update(message=f"第 {page_num} 頁（{source_path}）請求失敗")
        return 0, 0, 1

    decks = parse_deck_articles(resp.text)
    found, new, failed = len(decks), 0, 0

    conn = database.get_db_connection()
    if not conn:
        update_state.increment(pages_done=1, decks_failed=max(found, 1))
        update_state.add_failed_page(page_label)
        update_state.update(message=f"第 {page_num} 頁（{source_path}）資料庫錯誤")
        return 0, 0, found or 1
    try:
        cursor = conn.cursor()
        for deck_info in decks:
            try:
                deck_id = upsert_deck_from_list(cursor, deck_info, source_path.lower())
                if deck_id and code_collector is not None:
                    code_collector.append(deck_info["code"])
                if deck_id:
                    new += 1
            except Exception as e:
                conn.rollback()
                failed += 1
                print(f"[DeckUpdater] list upsert error {deck_info.get('code')}: {e}", flush=True)
        conn.commit()
    except Exception as e:
        conn.rollback()
        failed = max(failed, found)
        update_state.add_failed_page(page_label)
        print(f"[DeckUpdater] list page {page_num} DB error: {e}", flush=True)
    finally:
        conn.close()

    update_state.increment(
        pages_done=1, decks_found=found, decks_new=new, decks_failed=failed
    )
    update_state.update(message=f"第 {page_num} 頁（{source_path}）：{new} 新 / {failed} 失敗")
    return found, new, failed


# ── 詳情 API（增量） ──
def fetch_deck_from_api(deck_code):
    """從 ptcgtw 詳情 API 取得牌組卡片列表。"""
    resp = _fetch_with_retry('POST', DETAIL_API_URL, payload={'code': deck_code})
    if resp is None:
        return None
    try:
        data = resp.json()
        if data.get('success'):
            return data.get('deck', [])
    except Exception:
        pass
    return None


def deck_needs_detail_refresh(cursor, deck_id):
    """增量策略：卡片資料缺失（card_list 空）或超過 DETAIL_REFRESH_DAYS
    天未更新 → 需要重抓詳情。"""
    cursor.execute(
        "SELECT card_list, updated_at FROM imported_decks WHERE id = %s",
        (deck_id,),
    )
    row = cursor.fetchone()
    if not row:
        return True
    if not _has_populated_card_list(row.get('card_list')):
        return True
    updated_at = row.get('updated_at')
    if updated_at is None:
        return True
    try:
        cursor.execute(
            "SELECT (now() AT TIME ZONE 'UTC' - %s) > INTERVAL '%s days'",
            (updated_at, int(DETAIL_REFRESH_DAYS)),
        )
        return bool(cursor.fetchone()['?column?'])
    except Exception:
        return False


def process_deck_detail(deck_code):
    """抓取一份牌組詳情並寫入 deck_cards / card_list / search index。
    回傳 'new' / 'failed'。"""
    deck_cards_api = fetch_deck_from_api(deck_code)
    if not deck_cards_api:
        update_state.increment(details_failed=1)
        update_state.update(message=f"詳情失敗：{deck_code}")
        return 'failed'

    conn = database.get_db_connection()
    if not conn:
        update_state.increment(details_failed=1)
        return 'failed'
    try:
        cursor = conn.cursor()
        # 確保列表列存在（若詳情先於列表處理）
        cursor.execute(
            "INSERT INTO imported_decks (deck_code) VALUES (%s) ON CONFLICT (deck_code) DO NOTHING",
            (deck_code,),
        )
        cursor.execute("SELECT id FROM imported_decks WHERE deck_code = %s", (deck_code,))
        row = cursor.fetchone()
        if not row:
            update_state.increment(details_failed=1)
            return 'failed'
        deck_id = row['id']

        cursor.execute("DELETE FROM deck_cards WHERE deck_id = %s", (deck_id,))
        cursor.execute("DELETE FROM deck_search_index WHERE deck_id = %s", (deck_id,))

        card_list, matched, unmatched = resolve_and_write_deck_cards(
            cursor, deck_id, deck_cards_api
        )

        # 重建搜尋索引（deck_search_index）：card_list → id_mapping → cards.name
        for item in card_list:
            vid = item.get('id')
            qty = item.get('c', 1)
            cursor.execute(
                "SELECT local_card_id FROM id_mapping WHERE external_variant_id = %s", (vid,)
            )
            mr = cursor.fetchone()
            if not mr or not mr.get('local_card_id'):
                continue
            cursor.execute("SELECT name FROM cards WHERE card_id = %s", (mr['local_card_id'],))
            cr = cursor.fetchone()
            if cr and cr.get('name'):
                cursor.execute(
                    "INSERT INTO deck_search_index (deck_id, card_name, count) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                    (deck_id, cr['name'], qty)
                )

        card_list_json = json.dumps(card_list, ensure_ascii=False)
        cursor.execute(
            "UPDATE imported_decks SET card_list = %s, updated_at = (now() AT TIME ZONE 'UTC') WHERE id = %s",
            (card_list_json, deck_id),
        )
        conn.commit()
        update_state.increment(details_done=1, cards_total=len(card_list))
        return 'new'
    except Exception as e:
        conn.rollback()
        update_state.increment(details_failed=1)
        print(f"[DeckUpdater] detail DB error for {deck_code}: {e}", flush=True)
        return 'failed'
    finally:
        conn.close()


def resolve_and_write_deck_cards(cursor, deck_id, deck_cards_api):
    """解析 ptcgtw 卡片列表，透過 id_mapping 查 local_card_id，寫入 deck_cards。
    同時回傳 card_list（用於 imported_decks.card_list 欄位）。
    回傳: (card_list, matched_count, unmatched_count)
    """
    card_list = []
    matched = 0
    unmatched = 0
    session = requests.Session()
    session.headers.update(HEADERS)

    try:
        for card in deck_cards_api:
            vid = card.get("variant_id")
            # 部分牌組會混入圖片 URL 等非數值 variant_id，直接跳過
            if vid is None or not str(vid).isdigit():
                unmatched += 1
                continue
            # 「張數」（繁中）與「張数」（舊日文 key）兼容
            qty = int(card.get("張數", card.get("張数", 1)))

            card_list.append({"id": vid, "c": qty})
            resolved = resolve_variant(cursor, vid, session=session, write_mapping=True)
            local_id = resolved.get("local_card_id")

            if local_id:
                cursor.execute(
                    "INSERT INTO deck_cards (deck_id, local_card_id, quantity) VALUES (%s, %s, %s)",
                    (deck_id, local_id, qty)
                )
                matched += 1
            else:
                unmatched += 1
    finally:
        session.close()

    return card_list, matched, unmatched


# ── 更新執行（背景執行緒） ──
def _collect_detail_codes(cursor, codes, cap):
    """從已掃描頁面的 code 中挑出需要抓詳情的（增量策略 + cap 上限）。"""
    needed = []
    for code in codes:
        if len(needed) >= cap:
            break
        cursor.execute("SELECT id FROM imported_decks WHERE deck_code = %s", (code,))
        row = cursor.fetchone()
        if row and deck_needs_detail_refresh(cursor, row['id']):
            needed.append(code)
    return needed


def _execute(pages, collector, worker_count, explicit_codes=None):
    """執行列表爬取 + 詳情抓取。pages: [(source_path, page_num), ...]
    explicit_codes: 直接指定要抓詳情的 code（詳情補齊模式）。
    回傳 (start_epoch, finish_epoch)。"""
    start_epoch = time.time()

    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        futures = {
            executor.submit(crawl_list_page, path, page, collector): (path, page)
            for path, page in pages
        }
        for f in as_completed(futures):
            try:
                f.result()
            except Exception as e:
                print(f"[DeckUpdater] List worker error: {e}", flush=True)
                update_state.increment(pages_done=1, decks_failed=1)

    detail_codes = explicit_codes or []
    if not detail_codes and collector:
        conn = database.get_db_connection()
        try:
            cursor = conn.cursor()
            detail_codes = _collect_detail_codes(cursor, collector, DETAIL_FETCH_CAP)
        except Exception as e:
            print(f"[DeckUpdater] collect detail codes failed: {e}", flush=True)
        finally:
            if conn:
                conn.close()

    if detail_codes:
        update_state.update(total_details=len(detail_codes))
        update_state.update(message=f"詳情抓取：{len(detail_codes)} 份（增量）")
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(process_deck_detail, code): code for code in detail_codes}
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    print(f"[DeckUpdater] Detail worker error: {e}", flush=True)
                    update_state.increment(details_failed=1)

    return start_epoch, time.time()


def _finish_run(start_epoch, finish_epoch):
    """更新完成：快照狀態、標記結束、寫入 auto_update_runs。"""
    state = update_state.to_dict()
    success = (
        state["pages_done"] >= state["total_pages"]
        and state["decks_failed"] == 0
        and state["details_failed"] == 0
    )
    update_state.finish()
    record_run(
        service='jp_decks',
        kind=update_state.mode,
        success=success,
        message=update_state.message,
        stats=state,
        failure_count=state["decks_failed"] + state["details_failed"],
        started_at=start_epoch,
        finished_at=finish_epoch,
    )


# ── 公開 API ──
def run_daily_update(worker_count=3, pages_per_source=None):
    """每日更新：掃描各來源最新幾頁（列表 UPSERT）+ 對頁內牌組做詳情增量抓取。"""
    if update_state.running:
        return False, "更新已在進行中"

    ensure_card_list_column()
    pages_per_source = pages_per_source or DAILY_PAGES_PER_SOURCE

    pages = []
    for src in DECK_SOURCES:
        path = src["path"]
        n = int(pages_per_source.get(path, DAILY_PAGES_PER_SOURCE.get(path, 5)))
        for p in range(1, n + 1):
            pages.append((path, p))

    update_state.reset("daily", len(pages))
    update_state.update(message=f"每日更新：掃描最新 {len(pages)} 頁")

    def _run():
        collector = []
        start, finish = _execute(pages, collector, worker_count)
        _finish_run(start, finish)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True, f"每日更新已啟動（{worker_count} workers，最新 {len(pages)} 頁）"


def run_full_update(worker_count=4):
    """完整更新：掃描全部來源所有頁面（輕量列表 UPSERT），
    對頁內缺卡片的牌組做詳情增量抓取（cap 上限）。"""
    if update_state.running:
        return False, "更新已在進行中"

    ensure_card_list_column()

    pages = []
    for src in DECK_SOURCES:
        total = detect_total_pages(src["path"])
        for p in range(1, total + 1):
            pages.append((src["path"], p))

    update_state.reset("full", len(pages))
    update_state.update(message=f"完整更新：掃描全部 {len(pages)} 頁（詳情走增量）")

    def _run():
        collector = []
        start, finish = _execute(pages, collector, worker_count)
        _finish_run(start, finish)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    eta_minutes = max(1, int(len(pages) * MIN_REQUEST_INTERVAL / 60))
    return True, f"完整更新已啟動（{worker_count} workers，{len(pages)} 頁，約 {eta_minutes} 分鐘）"


def run_gap_fill_update(worker_count=3, pages_per_run=10):
    """輪轉增量缺漏偵測：每日掃描一個以日期為種子的移動窗口，
    補齊從未掃描的頁面（列表 UPSERT；詳情仍走增量）。"""
    if update_state.running:
        return False, "更新已在進行中"

    ensure_card_list_column()

    # 將兩個來源的頁面空間合併為一維，以日期序數輪轉
    source_pages = []
    for src in DECK_SOURCES:
        total = detect_total_pages(src["path"])
        source_pages.append((src["path"], total))

    grand_total = sum(t for _, t in source_pages)
    pages_per_run = max(1, min(int(pages_per_run), grand_total))
    day_ordinal = (date.today() - date(2024, 1, 1)).days
    start = (day_ordinal * pages_per_run) % grand_total

    pages = []
    for i in range(pages_per_run):
        idx = (start + i) % grand_total
        for path, total in source_pages:
            if idx < total:
                pages.append((path, idx + 1))
                break
            idx -= total

    update_state.reset("gap_fill", len(pages))
    update_state.update(
        message=f"缺漏偵測：掃描 {len(pages)} 頁（輪轉窗口，從第 {pages[0][1]} 頁 {pages[0][0]} 起）"
    )

    def _run():
        collector = []
        start, finish = _execute(pages, collector, worker_count)
        _finish_run(start, finish)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True, f"缺漏偵測已啟動（{worker_count} workers，{len(pages)} 頁）"


def run_detail_backfill(worker_count=3, max_decks=None, stale_days=None):
    """詳情補齊：對 DB 中卡片資料缺失（或超過 stale_days 天未更新）的
    牌組抓詳情，上限 max_decks。"""
    if update_state.running:
        return False, "更新已在進行中"

    ensure_card_list_column()
    max_decks = DETAIL_FETCH_CAP if max_decks is None else max_decks
    max_decks = max(1, int(max_decks))

    conn = database.get_db_connection()
    if not conn:
        return False, "資料庫錯誤"
    try:
        cursor = conn.cursor()
        if stale_days is not None:
            stale_days = max(1, int(stale_days))
            cursor.execute("""
                SELECT deck_code FROM imported_decks
                WHERE card_list IS NULL OR card_list = '' OR card_list = '[]'
                   OR updated_at < (now() AT TIME ZONE 'UTC') - INTERVAL '%s days'
                ORDER BY updated_at ASC NULLS FIRST
                LIMIT %s
            """, (stale_days, max_decks))
        else:
            cursor.execute("""
                SELECT deck_code FROM imported_decks
                WHERE card_list IS NULL OR card_list = '' OR card_list = '[]'
                ORDER BY updated_at ASC NULLS FIRST
                LIMIT %s
            """, (max_decks,))
        codes = [r['deck_code'] for r in cursor.fetchall()]
    except Exception as e:
        print(f"[DeckUpdater] detail backfill query failed: {e}", flush=True)
        return False, f"查詢失敗: {e}"
    finally:
        conn.close()

    if not codes:
        return False, "沒有需要補齊詳情的牌組"

    update_state.reset("detail_backfill", 0, total_details=len(codes))
    update_state.update(message=f"詳情補齊：{len(codes)} 份（增量策略）")

    def _run():
        start, finish = _execute([], [], worker_count, explicit_codes=codes)
        _finish_run(start, finish)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True, f"詳情補齊已啟動（{worker_count} workers，{len(codes)} 份）"


# ── 最新更新時間 / 摘要持久化 ──
def _load_meta():
    try:
        with open(DECK_UPDATE_META_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def _save_meta(meta):
    try:
        os.makedirs(os.path.dirname(DECK_UPDATE_META_FILE), exist_ok=True)
        with open(DECK_UPDATE_META_FILE, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[DeckUpdater] save meta failed: {e}")


def save_run_meta(kind, next_run=None):
    """在某次更新完成後呼叫：記錄 last_run + 當下 update_state 快照。
    kind: 'daily' 或 'gap_fill'。"""
    meta = _load_meta()
    meta.setdefault(kind, {})['last_run'] = time.strftime('%Y-%m-%d %H:%M:%S')
    meta[kind]['summary'] = update_state.to_dict()
    if next_run:
        meta['next_run'] = next_run
    _save_meta(meta)


def get_update_status():
    status = update_state.to_dict()
    meta = _load_meta()
    daily = meta.get('daily', {})
    status['last_run'] = daily.get('last_run')
    status['last_summary'] = daily.get('summary')
    status['next_run'] = meta.get('next_run')
    status['gap_fill'] = meta.get('gap_fill', {})
    return status
