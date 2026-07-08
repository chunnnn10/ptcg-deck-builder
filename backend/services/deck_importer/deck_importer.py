import psycopg2
import psycopg2.extras
import requests
import json
import time
import os
import re
import sys
import unicodedata
import random
import threading

from bs4 import BeautifulSoup
from datetime import datetime, timedelta  # [新增] 用於日期運算
from urllib.parse import unquote

# ── 設定 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_DIR = os.path.abspath(os.path.join(BASE_DIR, '../..'))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import config
import database

OUTPUT_JSON_DIR = config.DECK_JSON_EXPORT_DIR

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36',
    'Referer': 'https://ptcgtw.shop/',
    'Origin': 'https://ptcgtw.shop',
    'x-requested-with': 'XMLHttpRequest'
}

class DeckImporter:
    _db_lock = threading.Lock()

    def __init__(self):
        self.init_storage_db()
        self.name_cache = {}
        self.build_name_cache()

    def get_db_connection(self):
        return database.get_db_connection()

    def init_storage_db(self):
        with self._db_lock:
            conn = self.get_db_connection()
            if not conn: return
            cursor = conn.cursor()
            
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS imported_decks (
                id SERIAL PRIMARY KEY,
                deck_code TEXT UNIQUE,
                name TEXT,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
            
            # 自動遷移：補齊新欄位
            cursor.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'imported_decks'")
            existing_cols = [row['column_name'] for row in cursor.fetchall()]
            new_columns = {
                "deck_date": "TEXT",
                "title": "TEXT",
                "image_url": "TEXT",
                "tags": "TEXT",
                "card_list": "TEXT DEFAULT '[]'"
            }
            
            for col_name, col_type in new_columns.items():
                if col_name not in existing_cols:
                    try:
                        cursor.execute(f"ALTER TABLE imported_decks ADD COLUMN {col_name} {col_type}")
                    except psycopg2.OperationalError: pass

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS deck_cards (
                id SERIAL PRIMARY KEY,
                deck_id INTEGER,
                local_card_id TEXT,
                quantity INTEGER,
                FOREIGN KEY(deck_id) REFERENCES imported_decks(id)
            )
            ''')

            cursor.execute('''
            CREATE TABLE IF NOT EXISTS id_mapping (
                external_variant_id INTEGER PRIMARY KEY,
                local_card_id TEXT
            )
            ''')
            
            conn.commit()
            conn.close()
            
            if not os.path.exists(OUTPUT_JSON_DIR):
                os.makedirs(OUTPUT_JSON_DIR)

    def normalize_string(self, text):
        if not text: return ""
        text = unicodedata.normalize('NFKC', str(text))
        text = re.sub(r'[^\u4e00-\u9fa5a-zA-Z0-9]', '', text)
        return text.lower()

    def build_name_cache(self):
        conn = self.get_db_connection()
        if not conn: return
        cursor = conn.cursor()
        cursor.execute("SELECT card_id, name FROM cards")
        rows = cursor.fetchall()
        for row in rows:
            cid = row['card_id']
            name = row['name']
            norm_name = self.normalize_string(name)
            if norm_name not in self.name_cache:
                self.name_cache[norm_name] = []
            self.name_cache[norm_name].append(cid)
        conn.close()

    def extract_deck_info_from_html(self, html_content):
        """
        [優化] 改用 BeautifulSoup 解析牌組列表
        """
        decks = []
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 找到所有牌組卡片 (article class="deck-card")
        articles = soup.find_all('article', class_='deck-card')
        
        for article in articles:
            try:
                # 1. 提取圖片容器
                img_container = article.find('div', class_='card-image-container')
                if not img_container: continue
                
                # 提取 Code (data-ptcgtw 屬性)
                code = img_container.get('data-ptcgtw', '')
                
                # 提取 Image (data-images 屬性是 JSON 字串，或者直接找內部的 img src)
                # 截圖顯示 data-images="['url']"，我們試著直接解析這個或找 img 標籤
                image_url = ""
                # 優先嘗試找 img 標籤
                img_tag = img_container.find('img')
                if img_tag:
                    image_url = img_tag.get('src', '')
                
                # 2. 提取內容容器
                content_div = article.find('div', class_='card-content')
                if not content_div: continue
                
                # 提取日期
                date_p = content_div.find('p', class_='deck-date')
                deck_date = date_p.get_text(strip=True) if date_p else ""
                
                # 提取標題
                title_h3 = content_div.find('h3', class_='deck-title')
                title = title_h3.get_text(strip=True) if title_h3 else ""
                
                # 提取標籤 (Hashtags)
                # 假設標籤在某個位置，如果截圖沒顯示標籤結構，我們先留空或沿用舊邏輯
                # 舊邏輯是找 class="pokemon-tag"
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
            except Exception as e:
                print(f"解析單一牌組失敗: {e}")
                continue
                
        return decks

    # [新增] 日期解析 Helper
    def parse_date(self, date_str):
        try:
            # 支援 2026.01.11 或 2026/01/11 格式
            clean_date = date_str.strip().replace('/', '.')
            return datetime.strptime(clean_date, "%Y.%m.%d").date()
        except (ValueError, AttributeError):
            return None

    # [新增] 取得本地最新日期
    def get_latest_local_date(self):
        conn = self.get_db_connection()
        if not conn: return None
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT MAX(deck_date) FROM imported_decks")
            result = cursor.fetchone()
            if result and result[0]:
                return self.parse_date(result[0])
            return None
        finally:
            conn.close()

    # [新增] 智慧更新邏輯：自動判斷是否需要更新以及更新範圍
    def crawl_smart_update(self, status_callback=None):
        """
        智慧更新：
        1. 抓取線上最新日期
        2. 比對資料庫最新日期
        3. 如果線上比較新，就執行爬蟲
        """
        # 1. 抓取線上最新日期
        if status_callback: status_callback("正在檢查最新牌組資訊...")
        
        online_date_str = self.get_latest_online_date()
        
        if not online_date_str:
            if status_callback: status_callback("無法解析線上最新日期")
            return []
            
        print(f"[Deck Updater] 線上最新日期: {online_date_str}")

        # 2. 抓取本地資料庫最新日期
        conn = self.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(deck_date) FROM imported_decks")
        row = cursor.fetchone()
        local_date_str = row[0] if row else None
        conn.close()

        print(f"[Deck Updater] 本地最新日期: {local_date_str}")

        # 3. 比對 (如果本地沒資料，或是線上日期 > 本地日期，就更新)
        # 注意：字串比較 "2026-01-11" > "2026-01-10" 是有效的
        if not local_date_str or online_date_str > local_date_str:
            if status_callback: status_callback(f"發現新資料 ({online_date_str})，開始下載...")
            
            # 這裡設定要掃描幾頁，如果有新資料，通常掃描前 2-3 頁就夠了
            # 您也可以改寫成「一直爬到日期等於本地日期為止」的邏輯，但先簡單設定爬 2 頁
            return self.crawl_deck_codes(start_page=1, end_page=2, status_callback=status_callback)
        
        return []

    def crawl_deck_codes(self, start_page=1, end_page=1, status_callback=None):
        """
        爬取並解析頁面
        回傳: 包含完整 Metadata 的物件列表
        """
        base_url = "https://ptcgtw.shop/DeckList_JP.php"
        all_deck_objects = []
        seen_codes = set()

        for page in range(start_page, end_page + 1):
            url = f"{base_url}%spage={page}"
            try:
                if status_callback:
                    status_callback(f"正在掃描第 {page} 頁...")
                
                response = requests.get(url, headers=HEADERS, timeout=15)
                response.raise_for_status()
                
                # 使用我們剛才優化過的 extract_deck_info_from_html
                decks_on_page = self.extract_deck_info_from_html(response.text)
                
                new_count = 0
                for deck in decks_on_page:
                    if deck['code'] not in seen_codes:
                        seen_codes.add(deck['code'])
                        all_deck_objects.append(deck)
                        new_count += 1
                
                # 禮貌性暫停，避免對伺服器造成負擔
                time.sleep(0.5)
                
            except Exception as e:
                if status_callback:
                    status_callback(f"第 {page} 頁爬取失敗: {e}")
        
        return all_deck_objects

    def get_latest_online_date(self):
        """
        [新增] 專門去官網第一頁抓取「最新的一個日期」
        用於比對是否需要更新
        """
        url = "https://ptcgtw.shop/DeckList_JP.php%spage=1"
        try:
            response = requests.get(url, headers=HEADERS, timeout=10)
            if response.status_code != 200:
                print(f"[Deck Updater] 連線失敗: {response.status_code}")
                return None
            
            # 使用 BeautifulSoup 解析 (比 Regex 更穩定)
            soup = BeautifulSoup(response.text, 'html.parser')
            
            # 根據您的截圖：找到第一個 class="deck-date" 的 p 標籤
            # 截圖結構: article.deck-card -> div.card-content -> p.deck-date
            date_tag = soup.find('p', class_='deck-date')
            
            if date_tag:
                date_str = date_tag.get_text(strip=True)
                return date_str
            
            print(f"[Deck Updater] 找不到日期標籤 (.deck-date)")
            return None
            
        except Exception as e:
            print(f"[Deck Updater] 解析日期發生錯誤: {e}")
            return None

    # --- 以下為原有的卡片處理邏輯，保持不變 ---
    def fetch_deck_from_api(self, deck_code):
        url = "https://ptcgtw.shop/index_function/api/23_01_load_deck_ptcgtw_api.php"
        headers = HEADERS.copy()
        headers['Referer'] = f"https://ptcgtw.shop/%ss={deck_code}"
        payload = {'code': deck_code}
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)
            if response.status_code == 200:
                data = response.json()
                if data.get('success'): return data.get('deck', [])
            return None
        except: return None

    def find_best_match(self, ref_conn, ext_card):
        target_name_raw = ext_card['name_tw']
        norm_target_name = self.normalize_string(target_name_raw)
        ext_type = ext_card.get('card_type', '')
        candidate_ids = self.name_cache.get(norm_target_name)
        if not candidate_ids: return None
        placeholders = ','.join(['%s'] * len(candidate_ids))
        query = f"SELECT * FROM cards WHERE card_id IN ({placeholders})"
        cursor = ref_conn.cursor()
        cursor.execute(query, candidate_ids)
        candidates = cursor.fetchall()
        matched_candidates = []
        if ext_type == '寶可夢':
            ext_skills_set = set()
            for attack in ext_card.get('attacks', []):
                if attack.get('n'): ext_skills_set.add(self.normalize_string(attack['n']))
            if ext_card.get('ability_name'): ext_skills_set.add(self.normalize_string(ext_card['ability_name']))
            if not ext_skills_set: matched_candidates = candidates
            else:
                for cand in candidates:
                    try:
                        db_skills = json.loads(cand['skills_json'])
                        db_skills_set = set()
                        for s in db_skills:
                            s_name = s.get('name', '')
                            clean_name = self.normalize_string(s_name.replace("[特性]", ""))
                            if clean_name: db_skills_set.add(clean_name)
                        if not ext_skills_set.isdisjoint(db_skills_set): matched_candidates.append(cand)
                    except: continue
        else: matched_candidates = candidates
        if not matched_candidates: return None
        ext_rarity = ext_card.get('rarity', '')
        rarity_matches = [c for c in matched_candidates if c['rarity'] == ext_rarity]
        final_pool = rarity_matches if rarity_matches else matched_candidates
        best_card = max(final_pool, key=lambda x: x['card_id'])
        return best_card['card_id']

    def generate_unique_id(self):
        timestamp = int(time.time() * 1000)
        random_suffix = ''.join(random.choices('abcdefghijklmnopqrstuvwxyz0123456789', k=10))
        return f"{timestamp}{random_suffix}"

    def export_deck_to_json(self, deck_code, deck_id):
        conn_storage = self.get_db_connection()
        conn_ref = self.get_db_connection()
        try:
            s_cursor = conn_storage.cursor()
            s_cursor.execute("SELECT local_card_id, quantity FROM deck_cards WHERE deck_id = %s", (deck_id,))
            deck_cards = s_cursor.fetchall()
            output_list = []
            for item in deck_cards:
                local_id = item['local_card_id']
                quantity = item['quantity']
                r_cursor = conn_ref.cursor()
                r_cursor.execute("SELECT * FROM cards WHERE card_id = %s", (local_id,))
                card_data = r_cursor.fetchone()
                if not card_data: continue
                for _ in range(quantity):
                    try: skills_obj = json.loads(card_data['skills_json'])
                    except: skills_obj = []
                    logic_obj = database.get_card_logic(card_data['card_id'])
                    card_obj = {
                        "ai_logic_json": json.dumps(logic_obj, ensure_ascii=False) if logic_obj else None,
                        "card_id": card_data['card_id'],
                        "card_type": card_data['card_type'],
                        "element_type": card_data['element_type'],
                        "hp": card_data['hp'],
                        "image_file": card_data['image_file'],
                        "image_url": f"/images/{card_data['image_file']}",
                        "logic": logic_obj,
                        "name": card_data['name'],
                        "processing_status": card_data['processing_status'],
                        "rarity": card_data['rarity'],
                        "resistance_type": card_data['resistance_type'],
                        "resistance_value": card_data['resistance_value'],
                        "retreat_cost": card_data['retreat_cost'],
                        "skills": skills_obj,
                        "skills_json": card_data['skills_json'],
                        "sub_type": card_data['sub_type'],
                        "weakness_type": card_data['weakness_type'],
                        "weakness_value": card_data['weakness_value'],
                        "uniqueId": self.generate_unique_id()
                    }
                    output_list.append(card_obj)
            filename = os.path.join(OUTPUT_JSON_DIR, f"{deck_code}.json")
            with open(filename, 'w', encoding='utf-8') as f:
                json.dump(output_list, f, ensure_ascii=False, indent=2)
        finally:
            conn_storage.close()
            conn_ref.close()

    def process_deck(self, deck_info, status_callback=None):
        deck_code = deck_info['code']
        try:
            ref_conn = self.get_db_connection()
            storage_conn = self.get_db_connection()
            with self._db_lock:
                storage_cursor = storage_conn.cursor()
                storage_cursor.execute("SELECT id FROM imported_decks WHERE deck_code = %s", (deck_code,))
                existing = storage_cursor.fetchone()
                new_deck_id = None
                if existing:
                    new_deck_id = existing['id']
                    storage_cursor.execute('''
                        UPDATE imported_decks SET name=%s, deck_date=%s, title=%s, image_url=%s, tags=%s WHERE id=%s
                    ''', (deck_info['title'], deck_info['date'], deck_info['title'], deck_info['image'], deck_info['tags'], new_deck_id))
                    storage_conn.commit()
                    # 已經存在就不重複下載卡片，但會更新 JSON
                    self.export_deck_to_json(deck_code, new_deck_id)
                    return True
                else:
                    storage_cursor.execute('''
                        INSERT INTO imported_decks (deck_code, name, deck_date, title, image_url, tags) VALUES (%s, %s, %s, %s, %s, %s)
                    ''', (deck_code, deck_info['title'], deck_info['date'], deck_info['title'], deck_info['image'], deck_info['tags']))
                    new_deck_id = storage_cursor.lastrowid
                    storage_conn.commit()
            
            # 下載卡片
            deck_data = self.fetch_deck_from_api(deck_code)
            if not deck_data: return False
            for card in deck_data:
                ext_id = card.get('variant_id')
                quantity = int(card.get('張數', 1))
                storage_cursor = storage_conn.cursor()
                storage_cursor.execute("SELECT local_card_id FROM id_mapping WHERE external_variant_id = %s", (ext_id,))
                mapping = storage_cursor.fetchone()
                local_id = mapping['local_card_id'] if mapping else self.find_best_match(ref_conn, card)
                if local_id and not mapping:
                    with self._db_lock:
                        storage_cursor.execute("SELECT local_card_id FROM id_mapping WHERE external_variant_id = %s", (ext_id,))
                        if not storage_cursor.fetchone():
                            storage_cursor.execute("INSERT INTO id_mapping (external_variant_id, local_card_id) VALUES (%s, %s)", (ext_id, local_id))
                            storage_conn.commit()
                if local_id:
                    with self._db_lock:
                        storage_cursor.execute("INSERT INTO deck_cards (deck_id, local_card_id, quantity) VALUES (%s, %s, %s)", (new_deck_id, local_id, quantity))
                        storage_conn.commit()
            ref_conn.close()
            storage_conn.close()
            self.export_deck_to_json(deck_code, new_deck_id)
            return True
        except Exception as e:
            print(f"處理失敗 {deck_code}: {e}")
            return False
