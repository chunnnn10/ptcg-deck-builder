import sqlite3
import requests
import os
import json
import time
import re
import threading
import queue
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse

# --- 設定區域 ---
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
DB_FILE = os.path.join(DATA_DIR, 'pokemon_card_database.db')
IMAGE_SAVE_FOLDER = os.path.join(DATA_DIR, 'images')
BASE_URL = "https://asia.pokemon-card.com"
LIST_URL_TEMPLATE = "https://asia.pokemon-card.com/hk/card-search/list/?pageNo={}"

# 設定下載機器人的數量
NUM_WORKERS = 4 

# 偽裝成瀏覽器
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}

TYPE_MAP = {
    "Grass.png": "Grass", "Fire.png": "Fire", "Water.png": "Water",
    "Lightning.png": "Lightning", "Psychic.png": "Psychic", "Fighting.png": "Fighting",
    "Darkness.png": "Darkness", "Metal.png": "Metal", "Fairy.png": "Fairy",
    "Dragon.png": "Dragon", "Colorless.png": "Colorless"
}

# --- 全域變數 ---
task_queue = queue.Queue()
db_lock = threading.Lock()

def init_env():
    """初始化環境與資料庫結構"""
    if not os.path.exists(IMAGE_SAVE_FOLDER):
        try:
            os.makedirs(IMAGE_SAVE_FOLDER)
            print(f"建立圖片資料夾: {os.path.abspath(IMAGE_SAVE_FOLDER)}")
        except Exception as e:
            print(f"[錯誤] 無法建立圖片資料夾: {e}")

    # 自動升級資料庫Schema
    ensure_schema_updates()

def get_db_connection():
    return sqlite3.connect(DB_FILE, check_same_thread=False)

def ensure_schema_updates():
    """確保資料庫包含所有最新欄位"""
    print("正在檢查資料庫結構...")
    new_columns = [
        ('japanese_name', 'TEXT'),
        ('evolution_stage', 'TEXT'),
        ('evolves_from', 'TEXT'),
        ('set_code', 'TEXT'),
        ('set_number', 'TEXT')
    ]
    
    conn = get_db_connection()
    cursor = conn.cursor()
    
    # 建立表格（若不存在）
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS cards (
            card_id TEXT PRIMARY KEY,
            image_file TEXT,
            card_type TEXT,
            name TEXT,
            sub_type TEXT,
            hp INTEGER,
            element_type TEXT,
            weakness_type TEXT,
            weakness_value TEXT,
            resistance_type TEXT,
            resistance_value TEXT,
            retreat_cost INTEGER,
            skills_json TEXT,
            rarity TEXT,
            processing_status INTEGER DEFAULT 0,
            ai_logic_json TEXT
        )
    ''')
    
    # 新增欄位
    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE cards ADD COLUMN {col_name} {col_type}")
            print(f"✅ 成功新增欄位: {col_name}")
        except sqlite3.OperationalError:
            pass # 欄位已存在
            
    conn.commit()
    conn.close()

def check_card_exists(card_id):
    """檢查 ID 是否已存在"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT card_id FROM cards WHERE card_id = ?", (str(card_id),))
        data = cursor.fetchone()
        return data is not None
    except Exception as e:
        print(f"[DB Check Error] {e}")
        return False
    finally:
        conn.close()

def download_image(url, filename):
    """下載圖片"""
    try:
        if not url: return ""
        if not url.startswith("http"):
            url = urljoin(BASE_URL, url)

        response = requests.get(url, headers=HEADERS, stream=True, timeout=15)
        if response.status_code == 200:
            file_path = os.path.join(IMAGE_SAVE_FOLDER, filename)
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            return filename
        return ""
    except Exception as e:
        print(f"  [Img Error] {filename}: {e}")
        return ""

def extract_type_from_img(img_tag):
    if not img_tag or not img_tag.get('src'): return None
    src = img_tag['src']
    filename = src.split('/')[-1]
    return TYPE_MAP.get(filename, filename.replace('.png', ''))

def determine_card_type(sub_type_text, hp):
    """根據子類型文字和血量判斷卡牌種類"""
    trainer_keywords = ['物品', '支援者', '競技場', '寶可夢道具', 'Item', 'Supporter', 'Stadium', 'Tool']
    energy_keywords = ['基本能量', '特殊能量', 'Energy']
    
    if any(k in sub_type_text for k in trainer_keywords):
        return "Trainer"
    if any(k in sub_type_text for k in energy_keywords):
        return "Energy"
    if hp > 0:
        return "Pokémon"
    return "Pokémon"

def parse_detail_page(card_id):
    """解析詳情頁"""
    url = f"https://asia.pokemon-card.com/hk/card-search/detail/{card_id}/"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code != 200:
            return None

        soup = BeautifulSoup(response.text, 'html.parser')
        
        # --- 1. 標題、類型、階段 ---
        h1 = soup.find('h1', class_='pageHeader cardDetail')
        sub_type = "Basic"
        card_type = "Pokémon"
        name = "Unknown"
        evolution_stage = "Basic" # default

        if h1:
            span_tag = h1.find('span', class_='evolveMarker')
            if span_tag:
                sub_type = span_tag.get_text(strip=True)
                evolution_stage = sub_type # 這個通常就是階段 (例如：1階進化)
                span_tag.decompose()
            name = h1.get_text(strip=True)

        # --- 2. 圖片 ---
        img_div = soup.find('div', class_='cardImage')
        img_url = ""
        if img_div and img_div.find('img'):
            img_url = img_div.find('img')['src']

        # --- 3. 數值 (HP) ---
        hp_span = soup.find('span', class_='number')
        hp = int(hp_span.get_text(strip=True)) if hp_span else 0

        # --- 4. 判斷 card_type ---
        card_type = determine_card_type(sub_type, hp)

        # --- 5. 屬性 ---
        main_info = soup.find('p', class_='mainInfomation')
        element_type = "Colorless"
        if main_info:
            type_img = main_info.find('img')
            extracted = extract_type_from_img(type_img)
            if extracted: element_type = extracted

        # --- 6. 技能 ---
        skills = []
        skill_section = soup.find('div', class_='skillInformation')
        if skill_section:
            for skill_div in skill_section.find_all('div', class_='skill'):
                skill_data = {}
                name_span = skill_div.find('span', class_='skillName')
                skill_data['name'] = name_span.get_text(strip=True) if name_span else ""
                
                dmg_span = skill_div.find('span', class_='skillDamage')
                skill_data['damage'] = dmg_span.get_text(strip=True) if dmg_span else ""
                
                cost_span = skill_div.find('span', class_='skillCost')
                costs = []
                if cost_span:
                    for img in cost_span.find_all('img'):
                        c_type = extract_type_from_img(img)
                        if c_type: costs.append(c_type)
                skill_data['cost'] = costs
                
                eff_p = skill_div.find('p', class_='skillEffect')
                skill_data['effect'] = eff_p.get_text(strip=True) if eff_p else ""
                skills.append(skill_data)

        # --- 7. 弱點/抵抗力/撤退 ---
        weakness_type = ""
        weakness_val = ""
        resistance_type = ""
        resistance_val = ""
        retreat_cost = 0

        weak_td = soup.find('td', class_='weakpoint')
        if weak_td and weak_td.find('img'):
            weakness_type = extract_type_from_img(weak_td.find('img'))
            weakness_val = weak_td.get_text(strip=True)

        resist_td = soup.find('td', class_='resist')
        if resist_td:
            if resist_td.find('img'):
                resistance_type = extract_type_from_img(resist_td.find('img'))
            resistance_val = resist_td.get_text(strip=True)

        escape_td = soup.find('td', class_='escape')
        if escape_td:
            retreat_cost = len(escape_td.find_all('img'))

        # --- 8. 新增欄位解析 (參考 crawler_app.py) ---
        evolves_from = None
        set_code = ""
        set_number = ""
        
        # 進化來源
        active = soup.select_one('.evolution .step.active')
        if active:
            parent = active.find_parent('ul')
            if parent and ('second' in parent.get('class', []) or 'third' in parent.get('class', [])):
                grandparent = parent.find_parent('li')
                if grandparent:
                    link = grandparent.find('a')
                    if link: evolves_from = link.get_text(strip=True)

        # 擴充包資訊
        exp_col = soup.select_one('.expansionColumn')
        if exp_col:
            img = exp_col.select_one('.expansionSymbol img')
            if img and img.get('src'): set_code = img['src'].split('/')[-1].split('_')[0]
            num = exp_col.select_one('.collectorNumber')
            if num: set_number = num.get_text(strip=True)
        
        # NOTE: Japanese name fetching Removed (use update_japanese_name.py instead)

        return {
            'card_id': str(card_id),
            'name': name,
            'card_type': card_type,
            'sub_type': sub_type,
            'image_url_source': img_url,
            'hp': hp,
            'element_type': element_type,
            'skills': skills,
            'weakness_type': weakness_type,
            'weakness_value': weakness_val,
            'resistance_type': resistance_type,
            'resistance_value': resistance_val,
            'retreat_cost': retreat_cost,
            'rarity': "",
            # New Fields
            'evolution_stage': evolution_stage,
            'evolves_from': evolves_from,
            'set_code': set_code,
            'set_number': set_number,
            'japanese_name': None # Placeholder, will be filled by separate script
        }

    except Exception as e:
        print(f"  [Parse Error] ID {card_id}: {e}")
        return None

def save_card_to_db_safe(data):
    """執行緒安全的資料庫寫入"""
    
    source_url = data['image_url_source']
    if source_url:
        img_filename = os.path.basename(urlparse(source_url).path)
    else:
        img_filename = f"{data['card_id']}.png"

    download_res = download_image(source_url, img_filename)
    
    skills_json = json.dumps(data['skills'], ensure_ascii=False)
    ai_logic_json = "{}"
    
    # Updated SQL with new columns
    sql = """
    INSERT INTO cards (
        card_id, image_file, card_type, name, sub_type, 
        hp, element_type, weakness_type, weakness_value, 
        resistance_type, resistance_value, retreat_cost, 
        skills_json, rarity, ai_logic_json, processing_status,
        evolution_stage, evolves_from, set_code, set_number
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    values = (
        data['card_id'],
        download_res,
        data['card_type'],
        data['name'],
        data['sub_type'],
        data['hp'],
        data['element_type'],
        data['weakness_type'],
        data['weakness_value'],
        data['resistance_type'],
        data['resistance_value'],
        data['retreat_cost'],
        skills_json,
        data['rarity'],
        ai_logic_json,
        0,
        data.get('evolution_stage'),
        data.get('evolves_from'),
        data.get('set_code'),
        data.get('set_number')
    )
    
    with db_lock:
        conn = get_db_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(sql, values)
            conn.commit()
            print(f"  [完成] {data['name']} (ID:{data['card_id']}) 已存檔。")
        except sqlite3.Error as e:
            # 如果是 Duplicate Key，嘗試 Update 新欄位 (不含 japanese_name)
            if "UNIQUE constraint failed" in str(e):
                try:
                    update_sql = """
                        UPDATE cards SET
                        evolution_stage = ?, evolves_from = ?, set_code = ?, set_number = ?
                        WHERE card_id = ?
                    """
                    cursor.execute(update_sql, (
                        data.get('evolution_stage'),
                        data.get('evolves_from'),
                        data.get('set_code'),
                        data.get('set_number'),
                        data['card_id']
                    ))
                    conn.commit()
                    print(f"  [更新] {data['name']} (ID:{data['card_id']}) 已更新額外資訊。")
                except Exception as update_e:
                    print(f"  [Update失敗] {update_e}")
            else:
                print(f"  [DB寫入失敗] {e}")
        finally:
            conn.close()

# --- 機器人邏輯 ---

def scanner_robot(start_page, end_page):
    print(f"🤖 掃描機器人啟動 (範圍: {start_page}~{end_page} 頁)")
    for page in range(start_page, end_page + 1):
        list_url = LIST_URL_TEMPLATE.format(page)
        try:
            response = requests.get(list_url, headers=HEADERS, timeout=10)
            if response.status_code != 200: continue
                
            soup = BeautifulSoup(response.text, 'html.parser')
            card_items = soup.find_all('li', class_='card')
            
            for item in card_items:
                link_tag = item.find('a')
                if not link_tag: continue
                
                href = link_tag['href']
                match = re.search(r'/detail/(\d+)/', href)
                if not match: continue
                
                card_id = match.group(1)
                
                # Check logic: Process if new, or if existing but missing set_code (incomplete metadata)
                if should_process(card_id):
                    task_queue.put(card_id)

        except Exception as e:
            print(f"❌ 掃描第 {page} 頁時發生錯誤: {e}")
    print("✅ 掃描機器人任務完成！")

def should_process(card_id):
    """判斷是否需要處理 (若已存在且有新欄位資料則跳過)"""
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # 檢查是否存在且 set_code 是否有值
        cursor.execute("SELECT set_code FROM cards WHERE card_id = ?", (str(card_id),))
        row = cursor.fetchone()
        if row is None:
            return True # 不存在，要下載
        if row[0] is None or row[0] == "":
            return True # 存在但沒新資料，要更新
        return False # 已有完整資料，跳過
    except:
        return True
    finally:
        conn.close()

def worker_robot(worker_id):
    print(f"🔧 下載機器人 #{worker_id} 就緒")
    while True:
        try:
            card_id = task_queue.get(timeout=5)
        except queue.Empty:
            continue
        
        if card_id is None: break
            
        print(f"🔧 機器人 #{worker_id} 正在處理 ID: {card_id}")
        card_data = parse_detail_page(card_id)
        if card_data:
            save_card_to_db_safe(card_data)
        
        task_queue.task_done()

def main(start_page=1, end_page=673):
    init_env()
    
    workers = []
    for i in range(NUM_WORKERS):
        t = threading.Thread(target=worker_robot, args=(i+1,))
        t.daemon = True
        t.start()
        workers.append(t)
    
    scanner = threading.Thread(target=scanner_robot, args=(start_page, end_page))
    scanner.start()
    scanner.join()
    
    print("⏳ 掃描結束，等待下載機器人處理剩餘任務...")
    task_queue.join()
    print("🎉 所有任務執行完畢！")

if __name__ == "__main__":
    main(start_page=1, end_page=673)