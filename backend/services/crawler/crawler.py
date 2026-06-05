import threading
import queue
import time
import requests
import os
import json
import re
from bs4 import BeautifulSoup
from urllib.parse import urljoin, urlparse, parse_qs, urlencode, urlunparse
import config
import database

# ==========================================
# 全域狀態變數
# ==========================================
UPDATE_STATE = {
    'running': False,
    'progress': 0,      # 0-100
    'message': '就緒',
    'logs': [],         # 最近的幾條日誌
    'total_tasks': 0,
    'completed_tasks': 0
}
update_lock = threading.Lock()

TYPE_MAP = {
    "Grass.png": "Grass", "Fire.png": "Fire", "Water.png": "Water",
    "Lightning.png": "Lightning", "Psychic.png": "Psychic", "Fighting.png": "Fighting",
    "Darkness.png": "Darkness", "Metal.png": "Metal", "Fairy.png": "Fairy",
    "Dragon.png": "Dragon", "Colorless.png": "Colorless"
}

# PTCGSP API Config
JP_BASE_DOMAIN = "https://ptcgsp.com"
JP_API_SEARCH_URL = f"{JP_BASE_DOMAIN}/api/cards/"
JP_API_DETAIL_URL = f"{JP_BASE_DOMAIN}/api/cards/"
JP_HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-HK,zh;q=0.9,en-US;q=0.8,en;q=0.7,zh-TW;q=0.6",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://ptcgsp.com/cards", 
}

# ==========================================
# 資料庫與輔助函數
# ==========================================

def log_update(msg):
    """寫入更新日誌"""
    print(f"[Update] {msg}")
    with update_lock:
        UPDATE_STATE['message'] = msg
        UPDATE_STATE['logs'].insert(0, msg)
        if len(UPDATE_STATE['logs']) > 50:
            UPDATE_STATE['logs'].pop()

def load_local_meta():
    if os.path.exists(config.META_FILE_PATH):
        try:
            with open(config.META_FILE_PATH, 'r', encoding='utf-8') as f:
                return json.load(f)
        except: pass
    return {'total_cards': 0, 'last_update': 'N/A'}

def save_local_meta(count):
    try:
        with open(config.META_FILE_PATH, 'w', encoding='utf-8') as f:
            json.dump({
                'total_cards': count, 
                'last_update': time.strftime('%Y-%m-%d %H:%M:%S')
            }, f)
    except Exception as e:
        print(f"Save meta error: {e}")

def ensure_schema_updates():
    """
    確保資料庫包含所有最新欄位與表格
    對應新的需求：儲存 expansion_sets 以及 card table 的額外資訊
    """
    conn = database.get_db_connection()
    if not conn: return
    cursor = conn.cursor()
    
    # 1. 建立擴充包列表 Table (含 series 欄位)
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS expansion_sets (
        set_code TEXT PRIMARY KEY,
        set_name TEXT,
        series TEXT DEFAULT '',
        last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    # 補齊舊表的 series 欄位
    try:
        cursor.execute("ALTER TABLE expansion_sets ADD COLUMN series TEXT DEFAULT ''")
    except:
        pass

    # 2. 檢查 cards 表的新欄位
    # 注意：regulation_flags 用來儲存 'Standard', 'Expanded' 等標記
    new_columns = [
        ('japanese_name', 'TEXT'),
        ('evolution_stage', 'TEXT'),
        ('evolves_from', 'TEXT'),
        ('set_code', 'TEXT'),      # 官方系列代碼 (如 M3)
        ('set_name', 'TEXT'),      # 官方系列名稱 (如 擴充包「虛無歸零」)
        ('set_number', 'TEXT'),
        ('jp_id', 'TEXT'),
        ('regulation_flags', 'TEXT'),  # Standard / Expanded
        ('regulation_mark', 'TEXT'),   # 賽季字母 (F/G/H/I/J)
        ('description', 'TEXT'),        # 訓練家卡效果文字
    ]
    
    for col_name, col_type in new_columns:
        try:
            cursor.execute(f"ALTER TABLE cards ADD COLUMN {col_name} {col_type}")
        except:
            conn.rollback()  # 修復：交易中斷後必須 rollback，否則後續 SQL 全部報錯
            pass

    # 3. regulation_settings 表
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS regulation_settings (
        mark VARCHAR PRIMARY KEY,
        is_standard BOOLEAN DEFAULT FALSE
    )
    """)
    cursor.execute("""
    INSERT INTO regulation_settings (mark, is_standard)
    VALUES ('F', TRUE), ('G', TRUE), ('H', TRUE), ('I', TRUE), ('J', TRUE)
    ON CONFLICT (mark) DO NOTHING
    """)

    conn.commit()
    conn.close()

def extract_type_from_img(img_tag):
    """從 <img> 標籤的 src 檔名提取能量屬性。防禦: 若非 Tag 物件則返回 None。"""
    if img_tag is None:
        return None
    if not hasattr(img_tag, 'get'):
        return None
    src = img_tag.get('src', '')
    if not src:
        return None
    filename = src.split('/')[-1]
    return TYPE_MAP.get(filename, filename.replace('.png', ''))

def determine_card_type(sub_type_text, hp):
    trainer_keywords = ['物品', '支援者', '競技場', '寶可夢道具', 'Item', 'Supporter', 'Stadium', 'Tool']
    energy_keywords = ['基本能量', '特殊能量', 'Energy']
    if any(k in sub_type_text for k in trainer_keywords): return "Trainer"
    if any(k in sub_type_text for k in energy_keywords): return "Energy"
    if hp > 0: return "Pokémon"
    return "Pokémon"

def download_image(url, filename):
    try:
        if not url: return ""
        if not url.startswith("http"): url = urljoin(config.BASE_URL, url)
        if not os.path.exists(config.IMAGE_FOLDER): os.makedirs(config.IMAGE_FOLDER)
        
        # 檢查檔案是否已存在，若存在且大小正常則跳過
        file_path = os.path.join(config.IMAGE_FOLDER, filename)
        if os.path.exists(file_path) and os.path.getsize(file_path) > 1000:
            return filename

        response = requests.get(url, headers=config.HEADERS, stream=True, timeout=15)
        if response.status_code == 200:
            file_path = os.path.join(config.IMAGE_FOLDER, filename)
            with open(file_path, 'wb') as f:
                for chunk in response.iter_content(1024):
                    f.write(chunk)
            return filename
    except Exception as e:
        log_update(f"圖片下載錯誤 {filename}: {e}")
    return ""

# ==========================================
# 參考 HTML 檔路徑 (fallback)
# ==========================================
_REFERENCE_HTML_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', '..', '..', '參考資料', '擴充包彈窗完整HTML路徑.html'
)

# ==========================================
# 核心爬蟲邏輯 (網站解析)
# ==========================================

def _parse_expansion_modal(soup):
    """
    從 BeautifulSoup 物件解析 #productSelectorModal 或 .modalWindow 中的擴充包列表。
    回傳 list[dict]: [{'code': 'M4', 'name': '擴充包「忍者飛旋」', 'series': '超級進化'}, ...]
    """
    expansions = []
    # 優先找 #productSelectorModal，其次 .modalWindow
    modal = soup.select_one('#productSelectorModal') or soup.select_one('.modalWindow')
    if not modal:
        return expansions

    current_series = ''
    for row in modal.select('.conditionRow'):
        # 系列標籤
        series_label = row.select_one('.conditionLabel')
        if series_label:
            toggle = series_label.select_one('.toggleAccordion')
            raw = series_label.get_text(strip=True)
            if toggle:
                raw = raw.replace(toggle.get_text(strip=True), '').strip()
            if raw and len(raw) < 20:
                current_series = raw

        # 擴充包 checkbox
        for opt in row.select('.options'):
            inp = opt.select_one('input.expansionCode')
            label = opt.select_one('label')
            if inp and label:
                code = inp.get('value', '').strip()
                name = label.get_text(strip=True)
                if code and name:
                    expansions.append({'code': code, 'name': name, 'series': current_series})

    return expansions


def fetch_expansion_meta():
    """
    從 HK 官網動態抓取 #productSelectorModal 中的完整擴充包列表。
    若即時抓取失敗，fallback 到參考 HTML 檔案。
    回傳 dict: { code: {'name': ..., 'series': ...} }
    """
    log_update("正在同步官方擴充包列表 (HK)...")
    soup = None

    # 1) 嘗試即時抓取 HK 站
    url = "https://asia.pokemon-card.com/hk/card-search/list/"
    try:
        response = requests.get(url, headers=config.HEADERS, timeout=15)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            log_update("成功連線至 HK 官網")
        else:
            log_update(f"HK 官網回應異常: {response.status_code}")
    except Exception as e:
        log_update(f"HK 官網連線失敗: {e}")

    # 2) 解析 modal
    expansion_list = _parse_expansion_modal(soup) if soup else []

    # 3) Fallback：若即時抓取結果為空，使用參考 HTML
    if not expansion_list:
        log_update("即時抓取擴充包列表為空，嘗試讀取參考 HTML...")
        try:
            if os.path.exists(_REFERENCE_HTML_PATH):
                with open(_REFERENCE_HTML_PATH, 'r', encoding='utf-8') as f:
                    ref_soup = BeautifulSoup(f.read(), 'html.parser')
                expansion_list = _parse_expansion_modal(ref_soup)
                log_update(f"已從參考 HTML 載入 {len(expansion_list)} 個擴充包")
            else:
                log_update(f"參考 HTML 不存在: {_REFERENCE_HTML_PATH}")
        except Exception as e:
            log_update(f"參考 HTML 讀取失敗: {e}")

    # 4) Fallback 2：仍為空則嘗試 TW 站舊方法
    if not expansion_list:
        log_update("HK modal 解析失敗，嘗試 TW 站舊方法...")
        try:
            tw_url = "https://asia.pokemon-card.com/tw/card-search/list/"
            resp = requests.get(tw_url, headers=config.HEADERS, timeout=15)
            if resp.status_code == 200:
                tw_soup = BeautifulSoup(resp.text, 'html.parser')
                for inp in tw_soup.find_all('input', class_='expansionCode'):
                    code = inp.get('value', '').strip()
                    label_id = inp.get('id')
                    label_tag = tw_soup.find('label', attrs={'for': label_id})
                    name = label_tag.get_text(strip=True) if label_tag else code
                    if code:
                        expansion_list.append({'code': code, 'name': name, 'series': ''})
        except Exception as e:
            log_update(f"TW 站 fallback 也失敗: {e}")

    # 5) 寫入資料庫
    if not expansion_list:
        log_update("無法取得任何擴充包列表！")
        return {}

    conn = database.get_db_connection()
    if not conn:
        return {}

    cursor = conn.cursor()
    expansion_map = {}
    count = 0
    for exp in expansion_list:
        code = exp['code']
        name = exp['name']
        series = exp.get('series', '')
        expansion_map[code] = {'name': name, 'series': series}
        try:
            cursor.execute(
                """INSERT INTO expansion_sets (set_code, set_name, series)
                   VALUES (%s, %s, %s)
                   ON CONFLICT (set_code) DO UPDATE
                   SET set_name = EXCLUDED.set_name,
                       series = EXCLUDED.series,
                       last_updated = CURRENT_TIMESTAMP""",
                (code, name, series)
            )
            count += 1
        except Exception as e:
            log_update(f"寫入擴充包 {code} 失敗: {e}")

    conn.commit()
    conn.close()
    log_update(f"已同步 {count} 個擴充包資訊 (含系列分組)")
    return expansion_map

def parse_detail_page(card_id):
    """
    參照 official_hk.py 的優秀結構重寫。
    重點修復：
    1. 訓練家/競技場卡不再被誤判為 Pokémon Basic
    2. 精準提取 flavor_text、pokedex_number、height、weight
    """
    url = f"https://asia.pokemon-card.com/tw/card-search/detail/{card_id}/"
    try:
        response = requests.get(url, headers=config.HEADERS, timeout=15)
        if response.status_code != 200: return None
        soup = BeautifulSoup(response.text, 'html.parser')

        # ── Name & Stage ──
        card_type = "Pokémon"
        sub_type = "Basic"
        evolution_stage = "Basic"
        name = "Unknown"
        super_type = ""

        header = soup.select_one('h1.pageHeader.cardDetail')
        if header:
            stage_el = header.select_one('span.evolveMarker')
            if stage_el:
                sub_type = stage_el.get_text(strip=True)
                evolution_stage = sub_type
            # 移除 span 文字取得純名稱
            full_name = header.get_text(strip=True)
            for span in header.select('span'):
                full_name = full_name.replace(span.get_text(strip=True), '').strip()
            name = full_name

        # 檢測 Super Type (V, VMAX, VSTAR, ex, ...)
        header_text = header.get_text(strip=True) if header else ''
        if 'V-UNION' in header_text or 'V-UNION' in name:
            super_type = 'V-UNION'
        elif 'VMAX' in header_text or 'VMAX' in sub_type:
            super_type = 'VMAX'
        elif 'VSTAR' in header_text or 'VSTAR' in sub_type:
            super_type = 'VSTAR'
        elif 'ex' in name and ('寶可夢ex' in header_text or 'ex' in sub_type):
            super_type = 'ex'
        elif 'GX' in header_text:
            super_type = 'GX'
        elif 'V' in name and ('寶可夢V' in header_text):
            super_type = 'V'

        # ── Image ──
        img_div = soup.find('div', class_='cardImage')
        img_url = img_div.find('img')['src'] if (img_div and img_div.find('img')) else ''

        # ── HP & Element Type ──
        hp = 0
        hp_span = soup.find('span', class_='number')
        if hp_span:
            try:
                hp = int(hp_span.get_text(strip=True))
            except ValueError:
                hp = 0

        element_type = 'Colorless'
        main_info = soup.find('p', class_='mainInfomation')
        if main_info:
            type_img = main_info.find('img')
            extracted = extract_type_from_img(type_img)
            if extracted:
                element_type = extracted

        # ── Skills ──
        skills = []
        skill_section = soup.find('div', class_='skillInformation')
        if skill_section:
            for skill_div in skill_section.find_all('div', class_='skill'):
                skill_data = {}
                name_span = skill_div.find('span', class_='skillName')
                raw_name = name_span.get_text(strip=True) if name_span else ''

                if raw_name.startswith('[特性]'):
                    skill_data['type'] = 'ability'
                    skill_data['name'] = raw_name.replace('[特性]', '').strip()
                else:
                    skill_data['type'] = 'attack'
                    skill_data['name'] = raw_name

                dmg_span = skill_div.find('span', class_='skillDamage')
                skill_data['damage'] = dmg_span.get_text(strip=True) if dmg_span else ''

                cost_span = skill_div.find('span', class_='skillCost')
                costs = []
                if cost_span:
                    for img in cost_span.find_all('img'):
                        c_type = extract_type_from_img(img)
                        if c_type: costs.append(c_type)
                skill_data['cost'] = costs

                eff_p = skill_div.find('p', class_='skillEffect')
                skill_data['effect'] = eff_p.get_text(strip=True) if eff_p else ''
                skills.append(skill_data)

        # ── 智慧分類：從 <h3 class="commonHeader"> 檢測訓練家子類型 ──
        trainer_type_map = {
            '物品': 'Item', '物品卡': 'Item',
            '支援者': 'Supporter', '支援者卡': 'Supporter',
            '競技場': 'Stadium', '競技場卡': 'Stadium',
            '寶可夢道具': 'Pokémon Tool', '道具': 'Pokémon Tool',
        }
        description = ''  # 訓練家卡的效果文字

        if skill_section:
            header = skill_section.find('h3', class_='commonHeader')
            if header:
                header_text = header.get_text(strip=True)
                for kw, sub in trainer_type_map.items():
                    if kw in header_text:
                        card_type = 'Trainer'
                        sub_type = sub
                        break

        # ── 智慧分類：從技能名稱檢測訓練家子類型（後備） ──
        if card_type == 'Pokémon' and skills:
            all_skill_names = ' '.join([s['name'] for s in skills])
            all_skill_text = all_skill_names + ' ' + ' '.join([s.get('effect', '') for s in skills])

            trainer_kw_map = [
                (['物品卡', '[物品規則]', '物品'], 'Item'),
                (['支援者卡', '[支援者規則]', '支援者'], 'Supporter'),
                (['競技場卡', '[競技場規則]', '競技場'], 'Stadium'),
                (['寶可夢道具', '[寶可夢道具規則]', '寶可夢道具'], 'Pokémon Tool'),
            ]
            for keywords, sub in trainer_kw_map:
                if any(kw in all_skill_names for kw in keywords):
                    card_type = 'Trainer'
                    sub_type = sub
                    break

        # ── 提取訓練家卡描述（分離純描述 vs 真實招式） ──
        # 訓練家卡可能有兩種技能：
        # 1) 純文字效果（skillName 為空、無 damage、無 cost）→ 放 description
        # 2) 真實招式/特性（有名稱或傷害或能量）→ 保留在 skills（例如寶可夢道具的攻擊）
        if card_type == 'Trainer' and skills:
            real_skills = []
            desc_parts = []
            for s in skills:
                has_name = bool(s.get('name', '').strip())
                has_damage = bool(s.get('damage', '').strip())
                has_cost = bool(s.get('cost'))
                if has_name or has_damage or has_cost:
                    real_skills.append(s)
                else:
                    effect = s.get('effect', '').strip()
                    if effect:
                        desc_parts.append(effect)
            skills = real_skills
            if desc_parts and not description:
                description = '\n'.join(desc_parts)

        # ── 後備：從全頁面文本掃描訓練家關鍵字 ──
        page_text = soup.get_text()
        if card_type == 'Pokémon' and not hp:
            for keywords, sub in [
                (['物品卡', '物品規則'], 'Item'),
                (['支援者卡', '支援者規則'], 'Supporter'),
                (['競技場卡', '競技場規則'], 'Stadium'),
                (['寶可夢道具', '寶可夢道具規則'], 'Pokémon Tool'),
            ]:
                if any(kw in page_text for kw in keywords):
                    card_type = 'Trainer'
                    sub_type = sub
                    if skills:
                        real_skills = []
                        desc_parts = []
                        for s in skills:
                            has_name = bool(s.get('name', '').strip())
                            has_damage = bool(s.get('damage', '').strip())
                            has_cost = bool(s.get('cost'))
                            if has_name or has_damage or has_cost:
                                real_skills.append(s)
                            else:
                                effect = s.get('effect', '').strip()
                                if effect:
                                    desc_parts.append(effect)
                        skills = real_skills
                        if desc_parts and not description:
                            description = '\n'.join(desc_parts)
                    break

        # ── Energy 檢測 ──
        has_battle_stats = soup.select_one('.subInformation') is not None
        if card_type == 'Pokémon' and not hp and not has_battle_stats:
            card_name_lower = (name or '').lower()
            if '能量' in name or 'energy' in card_name_lower:
                card_type = 'Energy'
                element_type = ''
                sub_type = 'Basic'
            elif '特殊能量' in page_text or 'special energy' in card_name_lower:
                card_type = 'Energy'
                element_type = ''
                sub_type = 'Special'

        # Final determination: if we have skills and HP/type, it's Pokémon
        if card_type == 'Pokémon' and element_type and (hp > 0 or skills):
            card_type = 'Pokémon'

        # ── Weakness / Resistance / Retreat ──
        weakness_type = ''; weakness_val = ''
        resistance_type = ''; resistance_val = ''
        retreat_cost = 0

        sub_info = soup.select_one('.subInformation')
        if sub_info:
            weak_td = sub_info.select_one('td.weakpoint')
            if weak_td:
                w_img = weak_td.select_one('img[src]')
                if w_img:
                    weakness_type = extract_type_from_img(w_img)
                w_text = weak_td.get_text(strip=True)
                w_match = re.search(r'[×＋＋](\d+)', w_text)
                if w_match:
                    weakness_val = '×' + w_match.group(1)
                elif '×2' in w_text:
                    weakness_val = '×2'

            resist_td = sub_info.select_one('td.resist')
            if resist_td:
                r_img = resist_td.select_one('img[src]')
                if r_img:
                    resistance_type = extract_type_from_img(r_img)
                r_text = resist_td.get_text(strip=True)
                r_match = re.search(r'[-－](\d+)', r_text)
                if r_match:
                    resistance_val = '-' + r_match.group(1)

            escape_td = sub_info.select_one('td.escape')
            if escape_td:
                retreat_cost = len(escape_td.select('img[src]'))

        # ── Evolution ──
        # HTML 結構: <ul.evolutionStep.second> → <li.step><a>呱頭蛙</a></li>
        #                                     → <li> → <ul.evolutionStep.third>
        #                                               → <li.step.active> 超級甲賀忍蛙ex
        # evolves_from = 上一層 ul 中的 li.step 文字
        # evolution_parents = 所有上層祖先的 {name, evolves_from} 列表 (用來級聯寫入)
        evolves_from = None
        evolution_parents = []  # [{name: '呱頭蛙', evolves_from: '呱呱泡蛙'}, ...]
        evo = soup.select_one('.evolution')
        if evo:
            active_step = evo.select_one('.step.active')
            if active_step:
                current_ul = active_step.find_parent('ul')
                while current_ul:
                    wrapper_li = current_ul.find_parent('li')
                    if wrapper_li:
                        upper_ul = wrapper_li.find_parent('ul')
                        if upper_ul:
                            step_li = upper_ul.select_one('li.step')
                            if step_li:
                                step_a = step_li.find('a')
                                if step_a:
                                    step_name = step_a.get_text(strip=True)
                                    if not evolves_from:
                                        evolves_from = step_name
                                    # 找該 step 的上一層
                                    parent_of_step_ul = step_li.find_parent('ul')
                                    if parent_of_step_ul:
                                        parent_wrapper_li = parent_of_step_ul.find_parent('li')
                                        if parent_wrapper_li:
                                            grand_ul = parent_wrapper_li.find_parent('ul')
                                            if grand_ul:
                                                grand_step = grand_ul.select_one('li.step')
                                                if grand_step:
                                                    grand_a = grand_step.find('a')
                                                    if grand_a:
                                                        evolution_parents.append({
                                                            'name': step_name,
                                                            'evolves_from': grand_a.get_text(strip=True)
                                                        })
                            current_ul = upper_ul
                        else:
                            break
                    else:
                        break

        # ── Regulation Mark & Collector Number ──
        regulation_mark = ''
        set_number = ''
        exp_col = soup.select_one('.expansionColumn, .expansionLinkColumn')
        if exp_col:
            # 賽季字母 (.alpha)
            alpha_el = exp_col.select_one('.alpha')
            if alpha_el:
                regulation_mark = alpha_el.get_text(strip=True)
            # 收藏編號
            cn_el = exp_col.select_one('.collectorNumber')
            if cn_el:
                set_number = cn_el.get_text(strip=True)

        if not set_number:
            cn_el = soup.select_one('.collectorNumber')
            if cn_el:
                set_number = cn_el.get_text(strip=True)

        # ── Pokédex Info ──
        pokedex_number = ''
        pokedex_category = ''
        height = ''
        weight = ''
        flavor_text = ''

        extra = soup.select_one('.extraInformation')
        if extra:
            h3 = extra.select_one('h3')
            if h3:
                pk_text = h3.get_text(strip=True)
                pk_num_match = re.search(r'No\.(\d+)', pk_text)
                if pk_num_match:
                    pokedex_number = pk_num_match.group(1)
                pk_cat = re.sub(r'No\.\d+', '', pk_text).strip()
                if pk_cat:
                    pokedex_category = pk_cat

            size = extra.select_one('.size')
            if size:
                values = size.select('.value')
                if len(values) >= 1:
                    height = values[0].get_text(strip=True)
                if len(values) >= 2:
                    weight = values[1].get_text(strip=True)

            disc = extra.select_one('.discription, .description') or extra.select_one('p')
            if disc:
                text = disc.get_text(strip=True)
                if '身高' not in text and len(text) > 5:
                    flavor_text = text

        return {
            'card_id': str(card_id), 'name': name, 'card_type': card_type,
            'sub_type': sub_type, 'super_type': super_type,
            'image_url_source': img_url, 'hp': hp, 'element_type': element_type,
            'skills': skills, 'description': description,
            'weakness_type': weakness_type, 'weakness_value': weakness_val,
            'resistance_type': resistance_type, 'resistance_value': resistance_val,
            'retreat_cost': retreat_cost, 'rarity': '',
            'regulation_mark': regulation_mark,
            'evolution_stage': evolution_stage, 'evolves_from': evolves_from,
            'evolution_parents': evolution_parents,
            'set_number': set_number,
            'pokedex_number': pokedex_number, 'pokedex_category': pokedex_category,
            'height': height, 'weight': weight, 'flavor_text': flavor_text
        }
    except Exception as e:
        log_update(f"解析錯誤 ID {card_id}: {e}")
        return None


def save_card_with_context(data, context):
    """使用 PostgreSQL UPSERT (ON CONFLICT) 寫入卡片"""
    source_url = data['image_url_source']
    img_filename = f"{data['card_id']}_no_image.png"
    if source_url:
        img_filename = os.path.basename(urlparse(source_url).path)

    if context.get('skip_images'):
        img_filename_to_save = img_filename
    else:
        download_res = download_image(source_url, img_filename)
        img_filename_to_save = download_res if download_res else ''

    skills_json = json.dumps(data['skills'], ensure_ascii=False)

    conn = database.get_db_connection()
    if not conn:
        return

    try:
        cursor = conn.cursor()

        # 保留舊的日文資料
        cursor.execute(
            "SELECT japanese_name, jp_id FROM cards WHERE card_id = %s",
            (data['card_id'],)
        )
        existing_row = cursor.fetchone()
        jp_name = existing_row['japanese_name'] if existing_row else None
        jp_id = existing_row['jp_id'] if existing_row else None

        reg_flag = 'Standard' if context.get('regulation') == 1 else 'Expanded'
        if context.get('regulation') == 2:
            reg_flag = 'Expanded'

        reg_flag = 'Standard' if context.get('regulation') == 1 else 'Expanded'
        if context.get('regulation') == 2:
            reg_flag = 'Expanded'
        # 賽季字母與描述
        regulation_mark = data.get('regulation_mark', '')
        description = data.get('description', '')

        cursor.execute("""
            INSERT INTO cards (
                card_id, image_file, card_type, name, sub_type,
                hp, element_type, weakness_type, weakness_value,
                resistance_type, resistance_value, retreat_cost,
                skills_json, rarity, processing_status,
                evolution_stage, evolves_from, set_code, set_name, set_number,
                japanese_name, jp_id, regulation_flags, regulation_mark,
                description,
                flavor_text, pokedex_number, pokedex_category, height, weight
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, 0,
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s,
                %s, %s, %s, %s, %s
            )
            ON CONFLICT (card_id) DO UPDATE SET
                image_file = EXCLUDED.image_file,
                card_type = EXCLUDED.card_type,
                name = EXCLUDED.name,
                sub_type = EXCLUDED.sub_type,
                hp = EXCLUDED.hp,
                element_type = EXCLUDED.element_type,
                weakness_type = EXCLUDED.weakness_type,
                weakness_value = EXCLUDED.weakness_value,
                resistance_type = EXCLUDED.resistance_type,
                resistance_value = EXCLUDED.resistance_value,
                retreat_cost = EXCLUDED.retreat_cost,
                skills_json = EXCLUDED.skills_json,
                rarity = EXCLUDED.rarity,
                evolution_stage = EXCLUDED.evolution_stage,
                evolves_from = EXCLUDED.evolves_from,
                set_code = EXCLUDED.set_code,
                set_name = EXCLUDED.set_name,
                set_number = EXCLUDED.set_number,
                regulation_flags = EXCLUDED.regulation_flags,
                regulation_mark = EXCLUDED.regulation_mark,
                description = EXCLUDED.description,
                flavor_text = EXCLUDED.flavor_text,
                pokedex_number = EXCLUDED.pokedex_number,
                pokedex_category = EXCLUDED.pokedex_category,
                height = EXCLUDED.height,
                weight = EXCLUDED.weight
        """, (
            data['card_id'], img_filename_to_save, data['card_type'], data['name'], data['sub_type'],
            data['hp'], data['element_type'], data['weakness_type'], data['weakness_value'],
            data['resistance_type'], data['resistance_value'], data['retreat_cost'],
            skills_json, data['rarity'],
            data['evolution_stage'], data['evolves_from'],
            context['set_code'], context['set_name'], data['set_number'],
            jp_name, jp_id, reg_flag, regulation_mark,
            description,
            data.get('flavor_text', ''), data.get('pokedex_number', ''),
            data.get('pokedex_category', ''), data.get('height', ''), data.get('weight', '')
        ))
        conn.commit()

        # 級聯寫入進化鏈上的中間卡片
        for parent in data.get('evolution_parents', []):
            if parent.get('name') and parent.get('evolves_from'):
                try:
                    cursor.execute("""
                        INSERT INTO cards (card_id, name, evolves_from, set_code, set_name, card_type)
                        VALUES (%s, %s, %s, %s, %s, 'Pokémon')
                        ON CONFLICT (card_id) DO UPDATE SET
                            evolves_from = EXCLUDED.evolves_from,
                            set_code = EXCLUDED.set_code,
                            set_name = EXCLUDED.set_name
                    """, (
                        f"{context.get('set_code', '')}_{parent['name']}",
                        parent['name'],
                        parent['evolves_from'],
                        context.get('set_code', ''),
                        context.get('set_name', '')
                    ))
                    conn.commit()
                except Exception:
                    conn.rollback()
    except Exception as e:
        conn.rollback()
        log_update(f"DB寫入失敗 {data['name']}: {e}")
    finally:
        conn.close()

def worker_robot(worker_id, q):
    while True:
        try:
            # 任務現在包含上下文信息: {'id': '123', 'set_code': 'M3', 'set_name': '...', 'regulation': 1}
            task = q.get(timeout=3)
            card_id = task['id']

            log_update(f"機器人 #{worker_id} 處理: {card_id} ({task['set_code']})")
            card_data = parse_detail_page(card_id)
            
            if card_data:
                save_card_with_context(card_data, task)
                # log_update(f"✅ {card_data['name']} 更新完成")
            
            with update_lock:
                UPDATE_STATE['completed_tasks'] += 1
                total = UPDATE_STATE['total_tasks']
                if total > 0:
                    # 分配進度條：官網更新佔 80%
                    scan_download_progress = (UPDATE_STATE['completed_tasks'] / total) * 80
                    UPDATE_STATE['progress'] = min(80, scan_download_progress)

            q.task_done()
        except queue.Empty:
            break
        except Exception as e:
            log_update(f"Worker Error: {e}")


def fetch_japanese_data(chinese_name):
    """從 PTCGSP 獲取日文資訊"""
    if not chinese_name: return None, None
    try:
        params = {"keyword": chinese_name, "page": 1, "keywordType": '["n","e"]'}
        resp_list = requests.get(JP_API_SEARCH_URL, headers=JP_HEADERS, params=params, timeout=10)
        
        if resp_list.status_code != 200: return None, None
        cards_summary = resp_list.json().get('data', {}).get('cards', [])
        
        if not cards_summary: return None, None
        
        uid = cards_summary[0].get('uid')
        if not uid: return None, None
        
        resp_detail = requests.get(f"{JP_API_DETAIL_URL}{uid}", headers=JP_HEADERS, timeout=10)
        
        if resp_detail.status_code == 200:
            name_j = resp_detail.json().get('data', {}).get('name_j')
            if name_j and name_j != '未知':
                return name_j, uid
    except:
        pass
    return None, None

def jp_worker_robot(worker_id, q):
    while True:
        try:
            task = q.get(timeout=3)
            card_id, name = task
            
            # log_update(f"日文機器人 #{worker_id}: {name}")
            jp_name, jp_id = fetch_japanese_data(name)
            
            if jp_name or jp_id:
                conn = database.get_db_connection()
                try:
                    conn.execute("UPDATE cards SET japanese_name = %s, jp_id = %s WHERE card_id = %s", (jp_name, jp_id, card_id))
                    conn.commit()
                    conn.close()
                    # log_update(f"🇯🇵 已補完: {name} -> {jp_name}")
                except:
                    if conn: conn.close()
            
            with update_lock:
                UPDATE_STATE['completed_tasks'] += 1
                total = UPDATE_STATE['total_tasks']
                if total > 0:
                     # 官網更新佔前 80%，日文更新是後面的累加
                    current_prog = (UPDATE_STATE['completed_tasks'] / total) * 100
                    UPDATE_STATE['progress'] = min(99, current_prog)
                    
            q.task_done()
        except queue.Empty:
            break

# ==========================================
# 主流程控制
# ==========================================

def construct_filtered_url(base_url, page_no, expansion_code, regulation):
    try:
        parsed = urlparse(base_url)
        query_params = parse_qs(parsed.query)
        query_params['pageNo'] = [str(page_no)]
        
        # 加入關鍵過濾條件
        if expansion_code:
            query_params['expansionCodes'] = [expansion_code]
        if regulation:
            query_params['regulation'] = [str(regulation)]
            
        new_query = urlencode(query_params, doseq=True)
        return urlunparse((parsed.scheme, parsed.netloc, parsed.path, parsed.params, new_query, parsed.fragment))
    except Exception as e:
        return base_url

def run_update_process(target_expansion_codes=None, target_regulations=None, update_japanese=True, skip_images=False):
    """
    Args:
        target_expansion_codes (list): 例如 ['M3', 'AS6b']。如果為 None，則不限制（不建議）。
        target_regulations (list): 例如 [1, 2]。1=標準, 2=開放。
    """
    global UPDATE_STATE
    ensure_schema_updates()
    
    # 1. 確保擁有最新的擴充包代碼對照表
    expansion_map = fetch_expansion_meta()
    
    # 如果沒有指定，預設只更新前幾個最新的（避免全站掃描）
    if not target_expansion_codes:
        log_update("未指定擴充包，將僅更新擴充包列表中前 1 個系列...")
        target_expansion_codes = list(expansion_map.keys())[:1]
    
    if not target_regulations:
        target_regulations = [1, 2] # 預設跑雙賽制掃描

    with update_lock:
        UPDATE_STATE['running'] = True
        UPDATE_STATE['progress'] = 0
        UPDATE_STATE['logs'] = []
        UPDATE_STATE['message'] = "初始化任務..."
        UPDATE_STATE['total_tasks'] = 0
        UPDATE_STATE['completed_tasks'] = 0

    log_update(f"🎯 目標系列: {target_expansion_codes}")
    log_update(f"⚖️ 目標賽制: {target_regulations}")
    
    task_queue = queue.Queue()
    found_ids_in_batch = set()

    # ==========================
    # 階段一：掃描列表
    # ==========================
    
    for exp_code in target_expansion_codes:
        exp_info = expansion_map.get(exp_code, {})
        exp_name = exp_info.get('name', 'Unknown Set') if isinstance(exp_info, dict) else exp_info
        
        for reg in target_regulations:
            if not UPDATE_STATE['running']: break
            
            log_update(f"正在掃描: [{exp_code}] {exp_name} (Regulation {reg})...")
            
            # 偵測該組合的總頁數
            current_total_pages = 1
            first_url = construct_filtered_url(config.DEFAULT_LIST_URL, 1, exp_code, reg)
            try:
                res = requests.get(first_url, headers=config.HEADERS, timeout=10)
                if res.status_code == 200:
                    soup = BeautifulSoup(res.text, 'html.parser')
                    page_tag = soup.find('p', class_='resultTotalPages')
                    if page_tag:
                        txt = page_tag.get_text(strip=True)
                        m = re.search(r'(\d+)', txt)
                        if m: current_total_pages = int(m.group(1))
            except Exception as e:
                log_update(f"頁數偵測失敗: {e}")

            # 開始翻頁
            for page in range(1, current_total_pages + 1):
                if not UPDATE_STATE['running']: break
                
                list_url = construct_filtered_url(config.DEFAULT_LIST_URL, page, exp_code, reg)
                try:
                    resp = requests.get(list_url, headers=config.HEADERS, timeout=10)
                    if resp.status_code == 200:
                        soup = BeautifulSoup(resp.text, 'html.parser')
                        card_items = soup.find_all('li', class_='card')
                        
                        for item in card_items:
                            link = item.find('a')
                            if not link: continue
                            match = re.search(r'/detail/(\d+)/', link['href'])
                            if match:
                                c_id = match.group(1)
                                # 組合唯一任務 ID (避免重複掃描，但如果不同賽制可能需要更新 flag)
                                # 這裡我們簡單做：如果這張卡在這個批次已經加過，就不加了
                                # (或者你可以允許重複，以便更新 regulation flag，看需求)
                                if c_id not in found_ids_in_batch:
                                    found_ids_in_batch.add(c_id)
                                    
                                    # 關鍵：將上下文封裝進任務
                                    task_payload = {
                                        'id': c_id,
                                        'set_code': exp_code,
                                        'set_name': exp_name,
                                        'regulation': reg,
                                        'skip_images': skip_images
                                    }
                                    task_queue.put(task_payload)
                                    with update_lock: UPDATE_STATE['total_tasks'] += 1
                except Exception as e:
                    log_update(f"列表掃描錯誤: {e}")
                    
    log_update(f"掃描完成，共發現 {task_queue.qsize()} 張卡片需處理。")

    # ==========================
    # 階段二：詳情下載與 DB 更新
    # ==========================
    if not task_queue.empty():
        workers = []
        num_workers = 5
        for i in range(num_workers):
            t = threading.Thread(target=worker_robot, args=(i+1, task_queue))
            t.daemon = True
            t.start()
            workers.append(t)
        task_queue.join()

    # ==========================
    # 階段三：日文補完 (依參數 skip)
    # ==========================
    if update_japanese:
        log_update("檢查日文缺漏...")
        jp_queue = queue.Queue()
        conn = database.get_db_connection()
        if conn:
            cursor = conn.cursor()
            # 這裡只檢查本次更新範圍內的卡片，或者全域檢查，看需求
            # 為簡單起見，檢查所有缺資料的
            cursor.execute("SELECT card_id, name FROM cards WHERE (japanese_name IS NULL OR japanese_name = '')")
            jp_missing = cursor.fetchall()
            if jp_missing:
                 with update_lock: UPDATE_STATE['total_tasks'] += len(jp_missing)
                 for row in jp_missing:
                     jp_queue.put((row['card_id'], row['name']))
            conn.close()

        if not jp_queue.empty():
            workers = []
            for i in range(5):
                t = threading.Thread(target=jp_worker_robot, args=(i+1, jp_queue))
                t.daemon = True
                t.start()
                workers.append(t)
            jp_queue.join()
    else:
        log_update("已略過日文補完。")

    with update_lock:
        UPDATE_STATE['running'] = False
        UPDATE_STATE['progress'] = 100
        UPDATE_STATE['message'] = "任務完成"
    log_update("🎉 所有更新已完成！")