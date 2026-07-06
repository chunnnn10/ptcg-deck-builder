"""
牌組更新模組 — 支援每日自動更新 + 完整列表更新
並行爬取 ptcgtw 牌組列表，將新牌組匯入 imported_decks。
牌組卡片以 variant_id + count 格式直接存於 imported_decks.card_list 欄位。
載入時透過 id_mapping 解析為本地卡片。
"""
import json
import time
import threading
import requests
from datetime import datetime, date
from concurrent.futures import ThreadPoolExecutor, as_completed
from bs4 import BeautifulSoup

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
import database
from services.deck_importer.card_resolver import resolve_variant

# ── 常數 ──
DECK_LIST_URL = "https://ptcgtw.shop/DeckList_JP.php"
TOTAL_PAGES = 1980

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Referer': 'https://ptcgtw.shop/',
    'Origin': 'https://ptcgtw.shop',
    'x-requested-with': 'XMLHttpRequest'
}

DECK_API_URL = "https://ptcgtw.shop/index_function/api/23_01_load_deck_ptcgtw_api.php"

# 持久化最新更新時間 / 缺漏偵測摘要（重啟後 admin 仍可顯示）
DECK_UPDATE_META_FILE = os.path.join('data', 'deck_update_meta.json')


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
        self.message = "就緒"
        self.start_time = None
        self._lock = threading.Lock()

    def reset(self, mode, total_pages):
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
            self.message = "啟動中..."
            self.start_time = time.time()

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

    def finish(self):
        with self._lock:
            self.running = False
            elapsed = ""
            if self.start_time:
                sec = int(time.time() - self.start_time)
                elapsed = f"{sec // 60}分{sec % 60}秒"
            self.message = f"更新完成（耗時 {elapsed}）"

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
                "message": self.message,
                "elapsed": elapsed,
                "progress": progress,
            }


update_state = UpdateState()


# ── 輔助函數 ──
def ensure_card_list_column():
    """確保 imported_decks 有 card_list 欄位"""
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


def extract_deck_info_from_html(html_content):
    """解析牌組列表頁面"""
    decks = []
    soup = BeautifulSoup(html_content, 'html.parser')
    articles = soup.find_all('article', class_='deck-card')

    for article in articles:
        try:
            img_container = article.find('div', class_='card-image-container')
            if not img_container:
                continue
            code = img_container.get('data-ptcgtw', '')

            image_url = ""
            img_tag = img_container.find('img')
            if img_tag:
                image_url = img_tag.get('src', '')

            content_div = article.find('div', class_='card-content')
            if not content_div:
                continue

            date_p = content_div.find('p', class_='deck-date')
            deck_date = date_p.get_text(strip=True) if date_p else ""

            title_h3 = content_div.find('h3', class_='deck-title')
            title = title_h3.get_text(strip=True) if title_h3 else ""

            tags = []
            for tag_a in article.find_all('a', class_='pokemon-tag'):
                tags.append(tag_a.get_text(strip=True))

            if code:
                decks.append({
                    "code": code,
                    "date": deck_date,
                    "title": title,
                    "image": image_url,
                    "tags": json.dumps(tags, ensure_ascii=False)
                })
        except Exception:
            continue

    return decks


def fetch_deck_from_api(deck_code):
    """從 ptcgtw API 取得牌組卡片列表"""
    headers = HEADERS.copy()
    headers['Referer'] = f"https://ptcgtw.shop/%ss={deck_code}"
    try:
        resp = requests.post(DECK_API_URL, json={'code': deck_code}, headers=headers, timeout=15)
        if resp.status_code == 200:
            data = resp.json()
            if data.get('success'):
                return data.get('deck', [])
    except Exception:
        pass
    return None


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
            qty = int(card.get("張数", card.get("張數", 1)))
            if not vid:
                continue

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


def crawl_and_process_page(page_num, today_str=None):
    """爬取一頁牌組列表並處理。
    today_str: 若提供則只處理該日期的牌組。
    """
    url = f"{DECK_LIST_URL}?page={page_num}"
    found, new, skipped, failed = 0, 0, 0, 0
    cards_total = 0

    try:
        resp = requests.get(url, headers=HEADERS, timeout=20)
        if resp.status_code != 200:
            update_state.increment(pages_done=1)
            return

        decks = extract_deck_info_from_html(resp.text)
        found = len(decks)

        for deck_info in decks:
            # 日期過濾
            if today_str:
                deck_date = (deck_info.get("date") or "").strip()
                if deck_date != today_str:
                    continue

            code = deck_info["code"]

            # 已完整匯入的牌組跳過；舊資料若缺 card_list 則補齊。
            conn = database.get_db_connection()
            if not conn:
                failed += 1
                continue
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT id, card_list FROM imported_decks WHERE deck_code = %s",
                    (code,),
                )
                exists = cursor.fetchone()
                conn.close()
                if exists and _has_populated_card_list(exists.get("card_list")):
                    skipped += 1
                    continue
            except Exception:
                try:
                    conn.close()
                except Exception:
                    pass
                failed += 1
                continue

            # 取得卡片列表
            deck_cards_api = fetch_deck_from_api(code)
            if not deck_cards_api:
                failed += 1
                continue

            # 寫入 imported_decks（先取得 deck_id）
            conn = database.get_db_connection()
            if not conn:
                failed += 1
                continue
            try:
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT INTO imported_decks (deck_code, name, deck_date, title, image_url, tags)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT (deck_code) DO UPDATE SET
                        name = EXCLUDED.name,
                        deck_date = EXCLUDED.deck_date,
                        title = EXCLUDED.title,
                        image_url = EXCLUDED.image_url,
                        tags = EXCLUDED.tags
                    RETURNING id
                """, (code, deck_info["title"], deck_info["date"],
                      deck_info["title"], deck_info["image"], deck_info["tags"]))
                row = cursor.fetchone()
                deck_id = row["id"]
                conn.commit()

                cursor.execute("DELETE FROM deck_cards WHERE deck_id = %s", (deck_id,))
                cursor.execute("DELETE FROM deck_search_index WHERE deck_id = %s", (deck_id,))

                # 解析卡片 → 寫入 deck_cards + 建立 card_list
                card_list, matched, unmatched = resolve_and_write_deck_cards(
                    cursor, deck_id, deck_cards_api
                )
                cards_total += len(card_list)

                # 寫入 search index
                for item in card_list:
                    vid = item.get('id')
                    qty = item.get('c', 1)
                    lid = None
                    cursor.execute("SELECT local_card_id FROM id_mapping WHERE external_variant_id = %s", (vid,))
                    mr = cursor.fetchone()
                    if mr:
                        lid = mr['local_card_id']
                    if lid:
                        cursor.execute("SELECT name FROM cards WHERE card_id = %s", (lid,))
                        cr = cursor.fetchone()
                        if cr:
                            cursor.execute(
                                "INSERT INTO deck_search_index (deck_id, card_name, count) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                                (deck_id, cr['name'], qty)
                            )

                # 更新 card_list JSON 欄位
                card_list_json = json.dumps(card_list, ensure_ascii=False)
                cursor.execute(
                    "UPDATE imported_decks SET card_list = %s WHERE id = %s",
                    (card_list_json, deck_id)
                )
                conn.commit()
                new += 1
            except Exception as e:
                conn.rollback()
                failed += 1
                print(f"[DeckUpdater] DB error for {code}: {e}")
            finally:
                conn.close()

        update_state.increment(
            pages_done=1, decks_found=found, decks_new=new,
            decks_skipped=skipped, decks_failed=failed, cards_total=cards_total
        )
        update_state.update(message=f"第 {page_num} 頁：{new} 新 / {skipped} 跳過 / {failed} 失敗")

    except Exception as e:
        update_state.increment(pages_done=1, decks_failed=1)
        update_state.update(message=f"第 {page_num} 頁錯誤: {e}")


# ── 公開 API ──
def run_daily_update(worker_count=3):
    """每日更新：掃描最新幾頁，匯入新牌組並補齊缺 card_list 的舊資料"""
    if update_state.running:
        return False, "更新已在進行中"

    ensure_card_list_column()

    pages = min(5, TOTAL_PAGES)
    update_state.reset("daily", pages)
    update_state.update(message=f"每日更新：掃描最新前 {pages} 頁")

    def _run():
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(crawl_and_process_page, p, None): p for p in range(1, pages + 1)}
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    print(f"[DeckUpdater] Worker error: {e}")
        update_state.finish()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True, f"每日更新已啟動（{worker_count} workers，最新 {pages} 頁）"


def run_full_update(worker_count=5):
    """完整更新：掃描全部 ~1980 頁"""
    if update_state.running:
        return False, "更新已在進行中"

    ensure_card_list_column()

    update_state.reset("full", TOTAL_PAGES)
    update_state.update(message=f"完整更新：掃描全部 {TOTAL_PAGES} 頁")

    def _run():
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(crawl_and_process_page, p, None): p for p in range(1, TOTAL_PAGES + 1)}
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    print(f"[DeckUpdater] Worker error: {e}")
        update_state.finish()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True, f"完整更新已啟動（{worker_count} 機器人，{TOTAL_PAGES} 頁）"


def get_total_pages():
    """動態偵測 ptcgtw 牌組列表總頁數（失敗回退 TOTAL_PAGES 常數）。"""
    try:
        import re
        resp = requests.get(f"{DECK_LIST_URL}?page=1", headers=HEADERS, timeout=20)
        if resp.status_code == 200:
            nums = [int(m) for m in re.findall(r'page=(\d+)', resp.text)]
            if nums:
                return max(nums)
    except Exception:
        pass
    return TOTAL_PAGES


def run_gap_fill_update(worker_count=3, pages_per_run=10):
    """輪轉增量缺漏偵測：每日掃描一個以日期為種子的移動窗口（預設 10 頁），
    約 total/pages_per_run 天可覆蓋全部頁面，補齊從未匯入的牌組。
    與每日/完整更新互斥（共用 update_state.running）。
    """
    if update_state.running:
        return False, "更新已在進行中"

    ensure_card_list_column()

    total = get_total_pages()
    pages_per_run = max(1, min(int(pages_per_run), total))
    # 無狀態輪轉：以日期序數為起點，重啟不影響進度
    day_ordinal = (date.today() - date(2024, 1, 1)).days
    start = (day_ordinal * pages_per_run) % total
    pages = [((start + i) % total) + 1 for i in range(pages_per_run)]

    update_state.reset("gap_fill", pages_per_run)
    update_state.update(
        message=f"缺漏偵測：從第 {pages[0]} 頁起掃描 {pages_per_run} 頁（輪轉窗口）"
    )

    def _run():
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(crawl_and_process_page, p, None): p for p in pages}
            for f in as_completed(futures):
                try:
                    f.result()
                except Exception as e:
                    print(f"[DeckUpdater] Gap-fill worker error: {e}")
        update_state.finish()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return True, f"缺漏偵測已啟動（{worker_count} workers，從第 {pages[0]} 頁起 {pages_per_run} 頁）"


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
