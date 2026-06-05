import sqlite3
import os
import requests
import time
import random
import re
import json
from bs4 import BeautifulSoup

# === 設定區 ===
# 資料庫路徑
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
CHINESE_DB_PATH = os.path.join(ROOT_DIR, 'data', 'pokemon_card_database.db')
ENGLISH_DB_PATH = os.path.join(ROOT_DIR, 'data', 'english_card_database.db')

# 模擬瀏覽器 Headers (這是必須的，否則 Limitless 會拒絕存取)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": "https://google.com"
}

def get_db_connection(db_path):
    """連接資料庫"""
    try:
        return sqlite3.connect(db_path)
    except sqlite3.Error as e:
        print(f"❌ 無法連接資料庫 {db_path}: {e}")
        return None

def init_english_db():
    """確保英文資料庫表格存在"""
    conn = get_db_connection(ENGLISH_DB_PATH)
    if not conn: return
    cursor = conn.cursor()
    # 建立與 create_english_db.py 相同的結構
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        card_id TEXT PRIMARY KEY,
        image_file TEXT, card_type TEXT, name TEXT, sub_type TEXT, hp INTEGER, element_type TEXT,
        weakness_type TEXT, weakness_value TEXT, resistance_type TEXT, resistance_value TEXT, retreat_cost INTEGER,
        skills_json TEXT, rarity TEXT, processing_status INTEGER DEFAULT 0,
        english_id TEXT, set_code TEXT, set_number TEXT, english_name TEXT, japanese_name TEXT,
        jp_id TEXT, evolution_stage TEXT, evolves_from TEXT
    )
    """)
    conn.commit()
    conn.close()

def clean_number_for_url(set_number):
    """
    將資料庫的編號轉換為 Limitless URL 格式
    1. '001' -> '1' (去除前導零)
    2. '001/100' -> '1' (去除總數)
    """
    if not set_number: return "0"
    
    try:
        # 如果包含 '/' (例如 001/100)，只取前面部分
        clean_str = str(set_number).split('/')[0]
        # 去除非數字字符 (以防萬一有 A01 之類的) 並轉整數去除前導零
        # 這裡假設編號主要是數字，如果包含字母 (如 TG01) 可能需要不同處理，但 Limitless 通常轉為純數字或特定格式
        # 對於標準數字編號：
        number_match = re.search(r'(\d+)', clean_str)
        if number_match:
            return str(int(number_match.group(1)))
        return clean_str
    except (ValueError, TypeError):
        return set_number

def fetch_html(url):
    """抓取網頁 HTML"""
    print(f"☁️ 正在下載: {url}")
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        if response.status_code == 200:
            return response.text
        elif response.status_code == 404:
            print(f"   ⚠️ 404 Not Found (頁面不存在)")
            return None
        else:
            print(f"   ⚠️ HTTP {response.status_code} Error")
            return None
    except Exception as e:
        print(f"   ❌ 請求失敗: {e}")
        return None

def extract_en_url_from_jp(jp_html):
    """從日版頁面中解析出英文版 (Int. Prints) 的連結"""
    if not jp_html: return None
    soup = BeautifulSoup(jp_html, 'html.parser')
    
    # 尋找版本表格中指向 /cards/en/ 的連結
    # 通常在 class="card-prints-versions" 的表格內
    # 我們直接找該表格內 href 開頭為 /cards/en/ 的連結
    target_link = soup.select_one('.card-prints-versions a[href^="/cards/en/"]')
    
    if target_link:
        href = target_link.get('href')
        return f"https://limitlesstcg.com{href}"
    
    return None

def parse_card_data(html, set_code, set_number):
    """
    解析 HTML 提取資料 (基於 limitless_full_parser.py 的邏輯)
    """
    if not html: return None
    soup = BeautifulSoup(html, 'html.parser')
    data = {}

    try:
        text_section = soup.select_one('.card-text')
        if not text_section: return None

        # 1. 名稱
        name_tag = text_section.select_one('.card-text-name a')
        data['name'] = name_tag.get_text(strip=True) if name_tag else "Unknown"

        # 2. 屬性與 HP
        title_text = text_section.select_one('.card-text-title').get_text(" ", strip=True)
        types = ['Grass', 'Fire', 'Water', 'Lightning', 'Psychic', 'Fighting', 'Darkness', 'Metal', 'Dragon', 'Colorless', 'Fairy']
        data['element_type'] = next((t for t in types if f"- {t}" in title_text), "Colorless")
        hp_match = re.search(r'(\d+)\s*HP', title_text)
        data['hp'] = int(hp_match.group(1)) if hp_match else 0

        # 3. 類型
        type_line = text_section.select_one('.card-text-type').get_text(strip=True)
        if ' - ' in type_line:
            parts = type_line.split(' - ')
            data['card_type'] = parts[0]
            data['sub_type'] = parts[1]
        else:
            data['card_type'] = type_line
            data['sub_type'] = None

        # 4. 技能
        skills = []
        for attack_div in text_section.select('.card-text-attack, .card-text-ability'):
            skill = {}
            if 'card-text-ability' in attack_div.get('class', []):
                name_tag = attack_div.select_one('.card-text-ability-info')
                skill['name'] = name_tag.get_text(strip=True).replace('Ability:', '').strip()
                skill['cost'] = []
                skill['damage'] = ""
            else:
                info_tag = attack_div.select_one('.card-text-attack-info')
                full_info = info_tag.get_text(" ", strip=True)
                cost_symbols = [s.get_text(strip=True) for s in info_tag.select('.ptcg-symbol')]
                skill['cost'] = cost_symbols
                
                text_content = full_info
                for c in cost_symbols: text_content = text_content.replace(c, "", 1)
                text_content = text_content.strip()
                
                damage_match = re.search(r'(\d+)[+-]?$', text_content)
                if damage_match:
                    skill['damage'] = damage_match.group(0)
                    skill['name'] = text_content[:damage_match.start()].strip()
                else:
                    skill['damage'] = ""
                    skill['name'] = text_content

            effect_tag = attack_div.select_one('.card-text-attack-effect, .card-text-ability-effect')
            skill['effect'] = effect_tag.get_text(strip=True) if effect_tag else ""
            skills.append(skill)
        
        data['skills_json'] = json.dumps(skills, ensure_ascii=False)

        # 5. 弱點/抗性/撤退
        wrr_section = text_section.select_one('.card-text-wrr')
        if wrr_section:
            wrr_text = wrr_section.get_text(" ", strip=True)
            w_match = re.search(r'Weakness:\s*([^\s]+)', wrr_text)
            data['weakness_type'] = w_match.group(1).replace('x2', '').strip() if w_match and 'none' not in w_match.group(1).lower() else None
            data['weakness_value'] = "x2" if data['weakness_type'] else None
            
            r_match = re.search(r'Resistance:\s*([^\s]+)', wrr_text)
            data['resistance_type'] = r_match.group(1).replace('-30', '').strip() if r_match and 'none' not in r_match.group(1).lower() else None
            data['resistance_value'] = "-30" if data['resistance_type'] else None
            
            ret_match = re.search(r'Retreat:\s*(\d+)', wrr_text)
            data['retreat_cost'] = int(ret_match.group(1)) if ret_match else 0

        # 6. 圖片
        img_tag = soup.select_one('.card-image img')
        data['image_file'] = img_tag.get('src') or img_tag.get('data-src') if img_tag else None

        # 寫回原始的 Set Code / Number (確保資料一致)
        data['set_code'] = set_code
        data['set_number'] = set_number

        return data

    except Exception as e:
        print(f"   ❌ 解析錯誤: {e}")
        return None

def save_to_english_db(card_id, data, chinese_name):
    """儲存至英文資料庫"""
    conn = get_db_connection(ENGLISH_DB_PATH)
    cursor = conn.cursor()

    sql = """
    INSERT OR REPLACE INTO cards (
        card_id, name, element_type, hp, card_type, sub_type,
        skills_json, weakness_type, weakness_value, resistance_type, resistance_value, retreat_cost,
        image_file, set_code, set_number, english_name, japanese_name
    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """
    
    values = (
        card_id,            # 使用中文資料庫的 card_id
        data['name'],
        data['element_type'],
        data['hp'],
        data['card_type'],
        data['sub_type'],
        data['skills_json'],
        data['weakness_type'],
        data['weakness_value'],
        data['resistance_type'],
        data['resistance_value'],
        data['retreat_cost'],
        data['image_file'],
        data['set_code'],
        data['set_number'],
        data['name'],       # english_name
        chinese_name        # 用中文名當作備註存入 japanese_name 欄位 (或反之)
    )

    try:
        cursor.execute(sql, values)
        conn.commit()
        print(f"   💾 成功儲存: {data['name']} (ID: {card_id})")
    except sqlite3.Error as e:
        print(f"   ❌ 資料庫寫入失敗: {e}")
    finally:
        conn.close()

def main():
    print("🚀 啟動 Limitless 資料庫驅動爬蟲 (JP -> EN 模式)...")
    init_english_db()

    # 1. 讀取中文資料庫中的目標卡片
    cn_conn = get_db_connection(CHINESE_DB_PATH)
    if not cn_conn: return
    cn_cursor = cn_conn.cursor()

    # 這裡只讀取有 Set Code 和 Set Number 的卡片
    cn_cursor.execute("""
        SELECT card_id, name, set_code, set_number 
        FROM cards 
        WHERE set_code IS NOT NULL AND set_number IS NOT NULL
        ORDER BY set_code, set_number
    """)
    cards_to_process = cn_cursor.fetchall()
    cn_conn.close()

    print(f"📋 共找到 {len(cards_to_process)} 張卡片待處理")

    # 2. 開始迴圈
    for idx, (card_id, cn_name, set_code, set_number) in enumerate(cards_to_process):
        print(f"\n[{idx+1}/{len(cards_to_process)}] 處理中: {cn_name} ({set_code} #{set_number})")
        
        # 1. 修正編號格式 (去除前導零，去除 /100)
        url_number = clean_number_for_url(set_number)
        
        # 2. 構建 JP 網址 (Limitless JP 格式)
        jp_url = f"https://limitlesstcg.com/cards/jp/{set_code.strip().upper()}/{url_number}"
        
        # 3. 抓取 JP 頁面
        jp_html = fetch_html(jp_url)
        
        if jp_html:
            # 4. 從 JP 頁面尋找英文版連結 (跳板)
            en_url = extract_en_url_from_jp(jp_html)
            
            if en_url:
                print(f"   🔗 找到英文連結，正在跳轉: {en_url}")
                # 5. 抓取 EN 頁面
                en_html = fetch_html(en_url)
                if en_html:
                    # 6. 解析 EN 資料
                    parsed_data = parse_card_data(en_html, set_code, set_number)
                    if parsed_data:
                        save_to_english_db(card_id, parsed_data, cn_name)
                    else:
                        print("   ⚠️ EN 頁面解析失敗")
            else:
                print(f"   ⚠️ 此日版卡片在 Limitless 上未找到對應的英文版連結 (Int. Prints)")
        else:
            print(f"   ⚠️ 無法存取日版頁面 (404 或連線錯誤)")
        
        # 禮貌性延遲
        delay = random.uniform(0.1, 0.2)
        print(f"   ⏳ 等待 {delay:.1f} 秒...")
        time.sleep(delay)

    print("\n✅ 所有任務完成！")

if __name__ == "__main__":
    main()