"""
Limitless TCG 日本卡牌爬蟲
Source: https://limitlesstcg.com/cards/jp/

用法：
  # 測試單卡
  py -c "from backend.services.crawler.limitless_jp_crawler import test_single; test_single('SV8', 1)"

  # 爬取單一系列
  py -c "from backend.services.crawler.limitless_jp_crawler import crawl_set; crawl_set('SV8', 137)"
"""
import re
import json
import time
import os
import sys
import logging
import threading
from typing import Optional
from urllib.parse import urljoin

# 確保 backend/ 在 sys.path 中 (相容於獨立執行和 Flask 內執行)
_backend_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
if _backend_dir not in sys.path:
    sys.path.insert(0, _backend_dir)

import requests
from bs4 import BeautifulSoup

import config
import database

logger = logging.getLogger(__name__)

# ==========================================
# 常數
# ==========================================
LIMITLESS_BASE = "https://limitlesstcg.com"
JP_SETS_URL = f"{LIMITLESS_BASE}/cards/jp"
JP_CARD_URL_TPL = f"{LIMITLESS_BASE}/cards/jp/{{set_code}}/{{number}}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,zh;q=0.9,en;q=0.8",
}

TYPE_KEYWORDS = [
    'Grass', 'Fire', 'Water', 'Lightning', 'Psychic',
    'Fighting', 'Darkness', 'Metal', 'Dragon', 'Colorless', 'Fairy'
]

SYMBOL_MAP = {
    'G': 'Grass', 'R': 'Fire', 'W': 'Water', 'L': 'Lightning',
    'P': 'Psychic', 'F': 'Fighting', 'D': 'Darkness', 'M': 'Metal',
    'N': 'Dragon', 'Y': 'Fairy', 'C': 'Colorless',
}

# ==========================================
# 全域狀態 (供前端輪詢)
# ==========================================
UPDATE_STATE = {
    'running': False,
    'progress': 0,
    'message': '就緒',
    'logs': [],
    'current_set': '',
    'completed_sets': 0,
    'total_sets': 0,
}


def _jp_log(msg: str):
    """寫入爬蟲日誌"""
    print(f"[Limitless JP] {msg}")
    UPDATE_STATE['message'] = msg
    UPDATE_STATE['logs'].insert(0, f"[{__import__('time').strftime('%H:%M:%S')}] {msg}")
    if len(UPDATE_STATE['logs']) > 200:
        UPDATE_STATE['logs'].pop()


# ==========================================
# HTTP
# ==========================================
def _fetch(url: str, max_retries: int = 3, timeout: int = 15) -> Optional[str]:
    """GET 頁面，回傳 HTML 字串。404 回傳 None。"""
    for attempt in range(max_retries):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=timeout)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.text
        except requests.RequestException as e:
            logger.debug(f"Request failed ({attempt + 1}/{max_retries}): {url} → {e}")
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
    return None


# ==========================================
# 系列列表
# ==========================================
def fetch_jp_sets() -> list[dict]:
    """
    從 /cards/jp 列表頁抓取所有日版系列。
    回傳 [{'code': 'SV8', 'name': 'Super Electric Breaker', 'count': 137}, ...]
    """
    html = _fetch(JP_SETS_URL)
    if not html:
        logger.error("無法抓取系列列表")
        return []

    soup = BeautifulSoup(html, 'lxml')
    sets = {}
    seen_codes = set()

    for a in soup.select('a[href^="/cards/jp/"]'):
        href = a.get('href', '')
        # 跳過翻譯連結和帶 query string 的
        if '?' in href:
            continue
        # 提取 set_code (最後一段)
        parts = href.strip('/').split('/')
        if len(parts) < 3:
            continue
        code = parts[-1].strip().upper()

        if code in seen_codes:
            continue
        seen_codes.add(code)

        # 找父層取得完整文字 (含卡牌數量)
        parent = a.find_parent('tr') or a.find_parent('div')
        if not parent:
            continue
        parent_text = parent.get_text(' ', strip=True)

        # 從文字解析: <Name> <Code> <Day> <Month> <Year> <Count> <Pct%>
        # 用 regex 提取 code 後的數字 (跳過日期)
        # 格式: "... M5 22 May 26 81 0.00%" → count = 81
        #        "... MC 19 Dec 25 774 38.50%" → count = 774
        count_match = re.search(
            rf'{re.escape(code)}\s+\d+\s+\w+\s+\d+\s+(\d+)\s+[\d.]+%',
            parent_text
        )
        if count_match:
            card_count = int(count_match.group(1))
            # 提取名稱 (code 之前的文字)
            name = parent_text[:parent_text.index(code)].strip()
            sets[code] = {'code': code, 'name': name, 'card_count': card_count}

    result = list(sets.values())
    logger.info(f"從系列列表取得 {len(result)} 個系列，共 {sum(s['card_count'] for s in result)} 張卡")
    return result


# ==========================================
# 卡片解析
# ==========================================
def parse_jp_card(html: str, set_code: str = "", number: str = "") -> Optional[dict]:
    """從 Limitless JP 詳情頁 HTML 提取完整卡牌資料。解析失敗回傳 None。"""
    soup = BeautifulSoup(html, 'lxml')
    data = {}

    # ── 卡片 ID (從 HTML comment) ──
    cid_match = re.search(r'<!-- CARD ID (\d+) -->', html)
    data['_card_id'] = int(cid_match.group(1)) if cid_match else 0

    # ── 圖片 URL ──
    img_tag = soup.select_one('.card-image img')
    if img_tag:
        data['image_url'] = img_tag.get('src') or img_tag.get('data-src', '')
    if not data.get('image_url'):
        og_img = soup.select_one('meta[property="og:image"]')
        if og_img:
            data['image_url'] = og_img.get('content', '')

    # ── 名稱 ──
    name_tag = soup.select_one('.card-text-name a')
    if not name_tag:
        return None
    data['name'] = name_tag.get_text(strip=True)

    # ── 屬性 & HP ──
    title_el = soup.select_one('.card-text-title')
    title_text = title_el.get_text(' ', strip=True) if title_el else ''

    data['element_type'] = 'Colorless'
    for t in TYPE_KEYWORDS:
        if f'- {t}' in title_text or title_text.startswith(f'{t}'):
            data['element_type'] = t
            break

    hp_match = re.search(r'(\d+)\s*HP', title_text)
    data['hp'] = int(hp_match.group(1)) if hp_match else 0

    # ── 卡片類型 & 子類型 & 進化鏈 ──
    type_el = soup.select_one('.card-text-type')
    raw_text = type_el.get_text(' ', strip=True) if type_el else ''
    # 壓縮多餘空白字元
    type_text = re.sub(r'\s+', ' ', raw_text).strip()
    data['evolves_from'] = ''

    if type_text:
        # 檢查是否有進化資訊
        if 'Evolves from' in type_text:
            # 提取 <a> 中的進化來源名稱
            evo_link = type_el.select_one('a')
            if evo_link:
                data['evolves_from'] = evo_link.get_text(strip=True)
            # 截取 sub_type（Evolves from 前面的部分）
            evo_idx = type_text.index('Evolves from')
            type_text = type_text[:evo_idx].strip()
            # 去除尾部殘留的 " -"
            type_text = re.sub(r'\s*-\s*$', '', type_text).strip()

        if ' - ' in type_text:
            parts = type_text.split(' - ', 1)
            data['card_type'] = parts[0].strip()
            data['sub_type'] = parts[1].strip()
        else:
            data['card_type'] = type_text.strip()
            data['sub_type'] = ''
    else:
        data['card_type'] = ''
        data['sub_type'] = ''

    # ── 技能 ──
    skills = []
    for attack_div in soup.select('.card-text-attack'):
        skill = _parse_skill(attack_div, 'attack')
        if skill:
            skills.append(skill)
    for ability_div in soup.select('.card-text-ability'):
        skill = _parse_skill(ability_div, 'ability')
        if skill:
            skills.append(skill)
    data['skills'] = skills

    # ── 訓練家/能量描述 ──
    data['description'] = ''
    if data['card_type'] in ('Trainer', 'Energy'):
        for sec in soup.select('.card-text .card-text-section'):
            if sec.select_one('.card-text-attack, .card-text-ability, .card-text-title'):
                continue
            desc_text = sec.get_text(' ', strip=True)
            if desc_text and 'Weakness:' not in desc_text and 'Illustrated by' not in desc_text:
                data['description'] = desc_text
                break

    # ── 弱點 / 抗性 / 撤退 ──
    wrr_el = soup.select_one('.card-text-wrr')
    if wrr_el:
        wrr_text = wrr_el.get_text(' ', strip=True)

        w_match = re.search(r'Weakness:\s*(\w+)', wrr_text)
        if w_match and w_match.group(1).lower() != 'none':
            data['weakness_type'] = w_match.group(1)
            w_val = re.search(r'Weakness:\s*\w+\s*([×x+\-]\d+)', wrr_text)
            data['weakness_value'] = w_val.group(1) if w_val else '×2'
        else:
            data['weakness_type'] = ''
            data['weakness_value'] = ''

        r_match = re.search(r'Resistance:\s*(\w+)', wrr_text)
        if r_match and r_match.group(1).lower() != 'none':
            data['resistance_type'] = r_match.group(1)
            r_val = re.search(r'Resistance:\s*\w+\s*([\-−]\d+)', wrr_text)
            data['resistance_value'] = r_val.group(1) if r_val else '-30'
        else:
            data['resistance_type'] = ''
            data['resistance_value'] = ''

        ret_match = re.search(r'Retreat:\s*(\d+)', wrr_text)
        data['retreat_cost'] = int(ret_match.group(1)) if ret_match else 0
    else:
        data['weakness_type'] = ''
        data['weakness_value'] = ''
        data['resistance_type'] = ''
        data['resistance_value'] = ''
        data['retreat_cost'] = 0

    # ── 繪師 ──
    artist_el = soup.select_one('.card-text-artist a')
    data['artist'] = artist_el.get_text(strip=True) if artist_el else ''

    # ── 賽季標記 ──
    reg_el = soup.select_one('.regulation-mark')
    if reg_el:
        reg_text = reg_el.get_text(strip=True)
        reg_match = re.search(r'^(\w+)\s*Regulation Mark', reg_text)
        data['regulation_mark'] = reg_match.group(1) if reg_match else ''
    else:
        data['regulation_mark'] = ''

    # ── 格式合法性 ──
    data['standard_jp_legal'] = False
    data['expanded_jp_legal'] = False
    for item in soup.select('.card-legality-item'):
        label = item.select_one('div:first-child')
        status = item.select_one('.legal, .not-legal')
        if label and status:
            label_text = label.get_text(strip=True)
            is_legal = 'legal' in status.get('class', [])
            if 'Standard (JP)' in label_text:
                data['standard_jp_legal'] = is_legal
            elif 'Expanded (JP)' in label_text:
                data['expanded_jp_legal'] = is_legal

    # ── 系列資訊 ──
    data['set_code'] = set_code
    data['set_number'] = number
    data['set_name'] = ''
    data['rarity'] = ''

    prints_current = soup.select_one('.card-prints-current')
    if prints_current:
        set_link = prints_current.select_one('a[href^="/cards/jp/"]')
        if set_link:
            href = set_link.get('href', '')
            code_match = re.search(r'/cards/jp/(\w+)', href)
            if code_match:
                data['set_code'] = code_match.group(1)

        text_lg = prints_current.select_one('.text-lg')
        if text_lg:
            set_text = text_lg.get_text(strip=True)
            name_match = re.match(r'^(.+?)\s*\((\w+)\)$', set_text)
            if name_match:
                data['set_name'] = name_match.group(1).strip()
                if not data.get('set_code'):
                    data['set_code'] = name_match.group(2)

        for span in prints_current.select('span:not(.text-lg)'):
            span_text = span.get_text(strip=True)
            cn_match = re.match(r'#(\d+)\s*·\s*(.+)', span_text)
            if cn_match:
                data['set_number'] = cn_match.group(1)
                data['rarity'] = cn_match.group(2).strip()

    # ── Int. Prints 英文版連結 (供後續對照) ──
    en_prints = []
    for a in soup.select('table.card-prints-versions a[href^="/cards/en/"]'):
        href = a.get('href', '')
        full_text = a.get_text(strip=True)
        en_match = re.match(r'^(.+?)\s*#(\d+)$', full_text)
        if en_match:
            en_prints.append({
                'set_name': en_match.group(1).strip(),
                'set_number': en_match.group(2),
                'url': f"{LIMITLESS_BASE}{href}",
            })
    data['en_prints'] = en_prints

    return data


def _parse_skill(div, skill_type: str) -> Optional[dict]:
    """解析單個攻擊或特性"""
    skill = {'type': skill_type, 'name': '', 'cost': [], 'damage': '', 'effect': ''}

    if skill_type == 'ability':
        info_tag = div.select_one('.card-text-ability-info')
        if info_tag:
            raw = info_tag.get_text(strip=True)
            skill['name'] = raw.replace('Ability:', '').strip()
    else:
        info_tag = div.select_one('.card-text-attack-info')
        if not info_tag:
            return None

        for symbol in info_tag.select('.ptcg-symbol'):
            sym_text = symbol.get_text(strip=True)
            for ch in sym_text:
                if ch in SYMBOL_MAP:
                    skill['cost'].append(SYMBOL_MAP[ch])

        full_text = info_tag.get_text(' ', strip=True)
        for symbol in info_tag.select('.ptcg-symbol'):
            full_text = full_text.replace(symbol.get_text(strip=True), '', 1)
        full_text = full_text.strip()

        dmg_match = re.search(r'(\d+[×x+\-]?\s*\d*)\s*$', full_text)
        if dmg_match:
            skill['damage'] = dmg_match.group(1).strip()
            skill['name'] = full_text[:dmg_match.start()].strip()
        else:
            skill['damage'] = ''
            skill['name'] = full_text.strip()

    effect_tag = div.select_one('.card-text-attack-effect, .card-text-ability-effect')
    if effect_tag:
        skill['effect'] = effect_tag.get_text(strip=True)

    return skill if skill['name'] else None


# ==========================================
# 資料庫寫入 (jp_cards)
# ==========================================
def save_to_jp_cards(card: dict, conn=None) -> bool:
    """將解析後的卡牌寫入 jp_cards 表 (UPSERT)。
    自動查找既有中文名、保留進化資訊、存入完整編號格式。"""
    set_code = card.get('set_code', 'XX')
    number = card.get('set_number', '0')
    set_total = card.get('set_total', '')
    card_id = f"jp_{set_code}_{number}"

    own_conn = conn is None
    if own_conn:
        conn = database.get_db_connection()
        if not conn:
            return False

    try:
        cursor = conn.cursor()
        skills_json = json.dumps(card.get('skills', []), ensure_ascii=False)

        # ── 嘗試復用既有中文名 + 進化資訊 ──
        chinese_name = card.get('chinese_name') or None  # 解析階段可能已有
        evolves_from = card.get('evolves_from', '')
        evolution_stage = card.get('sub_type', '')

        # 標準化 set_number（去除 /total，嘗試補零）
        num_raw = str(number).split('/')[0]
        # 補前導零: 3 → 003, 12 → 012
        num_padded = num_raw.zfill(3)

        # 1) 從舊 jp_cards 查找既有中文名
        if not chinese_name:
            cursor.execute(
                "SELECT chinese_name FROM jp_cards "
                "WHERE set_code = %s AND (set_number LIKE %s OR set_number LIKE %s) "
                "AND chinese_name IS NOT NULL "
                "LIMIT 1",
                (set_code, f"{num_raw}/%", f"{num_padded}/%")
            )
            old = cursor.fetchone()
            if old and old.get('chinese_name'):
                chinese_name = old['chinese_name']

        # 2) 從 cards 表查找中文名（支援有/無前導零、有/無總數）
        if not chinese_name:
            cursor.execute(
                "SELECT name FROM cards "
                "WHERE set_code = %s AND ("
                "  set_number = %s OR set_number = %s OR "
                "  set_number LIKE %s OR set_number LIKE %s"
                ") LIMIT 1",
                (set_code, num_raw, num_padded, f"{num_raw}/%", f"{num_padded}/%")
            )
            cn_match = cursor.fetchone()
            if cn_match and cn_match.get('name'):
                chinese_name = cn_match['name']

        # 3) 從 cards 表補進化資訊
        if not evolves_from:
            cursor.execute(
                "SELECT evolves_from FROM cards "
                "WHERE set_code = %s AND ("
                "  set_number = %s OR set_number = %s OR "
                "  set_number LIKE %s OR set_number LIKE %s"
                ") AND evolves_from IS NOT NULL AND evolves_from != '' "
                "LIMIT 1",
                (set_code, num_raw, num_padded, f"{num_raw}/%", f"{num_padded}/%")
            )
            evo_match = cursor.fetchone()
            if evo_match and evo_match.get('evolves_from'):
                evolves_from = evo_match['evolves_from']

        # ── 寫入 ──
        cursor.execute("""
            INSERT INTO jp_cards (
                card_id, image_file, card_type, name, sub_type,
                hp, element_type, weakness_type, weakness_value,
                resistance_type, resistance_value, retreat_cost,
                skills_json, rarity,
                chinese_name, evolution_stage, evolves_from,
                set_code, set_number, set_total, set_name,
                regulation_flags, regulation_mark,
                description
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
                %s
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
                chinese_name = COALESCE(EXCLUDED.chinese_name, jp_cards.chinese_name),
                evolution_stage = EXCLUDED.evolution_stage,
                evolves_from = COALESCE(NULLIF(EXCLUDED.evolves_from, ''), jp_cards.evolves_from),
                set_code = EXCLUDED.set_code,
                set_number = EXCLUDED.set_number,
                set_total = EXCLUDED.set_total,
                set_name = EXCLUDED.set_name,
                regulation_flags = EXCLUDED.regulation_flags,
                regulation_mark = EXCLUDED.regulation_mark,
                description = EXCLUDED.description
        """, (
            card_id,
            card.get('image_url', ''),
            card.get('card_type', 'Pokémon'),
            card.get('name', ''),
            card.get('sub_type', ''),
            card.get('hp', 0),
            card.get('element_type', ''),
            card.get('weakness_type', ''),
            card.get('weakness_value', ''),
            card.get('resistance_type', ''),
            card.get('resistance_value', ''),
            card.get('retreat_cost', 0),
            skills_json,
            card.get('rarity', ''),
            chinese_name,
            evolution_stage,
            evolves_from,
            set_code,
            str(number),
            set_total,
            card.get('set_name', ''),
            'Standard' if card.get('standard_jp_legal') else 'Expanded',
            card.get('regulation_mark', ''),
            card.get('description', ''),
        ))

        conn.commit()
        return True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        logger.error(f"DB write error for {card.get('name', '?')}: {e}")
        return False


# ==========================================
# 單卡測試
# ==========================================
def test_single(set_code: str, number, print_result: bool = True):
    """測試單卡解析 — 從網路抓取並印出結果。"""
    url = JP_CARD_URL_TPL.format(set_code=set_code.upper(), number=number)
    if print_result:
        print(f"🌐 抓取: {url}")

    html = _fetch(url)
    if not html:
        print(f"❌ 404 或連線失敗")
        return None

    card = parse_jp_card(html, set_code.upper(), str(number))
    if not card:
        print(f"❌ 解析失敗")
        return None

    if print_result:
        print(f"  名稱:     {card['name']}")
        print(f"  類型:     {card['card_type']} - {card['sub_type']}")
        print(f"  屬性:     {card['element_type']}  HP: {card['hp']}")
        print(f"  弱點:     {card['weakness_type']} {card['weakness_value']}")
        print(f"  抗性:     {card['resistance_type']} {card['resistance_value']}")
        print(f"  撤退:     {card['retreat_cost']}")
        print(f"  賽季:     {card['regulation_mark']}")
        print(f"  Std JP:   {'✅' if card['standard_jp_legal'] else '❌'}")
        print(f"  系列:     {card['set_name']} ({card['set_code']}) #{card['set_number']}")
        print(f"  稀有度:   {card['rarity']}")
        print(f"  繪師:     {card['artist']}")
        if card['skills']:
            for i, sk in enumerate(card['skills']):
                cost = ''.join(c[0] for c in sk['cost']) if sk['cost'] else '-'
                print(f"  技能[{i}]:  [{sk['type']}] {sk['name']} [{cost}] {sk['damage']}")
                if sk['effect']:
                    print(f"           ↳ {sk['effect'][:120]}")
        if card.get('description'):
            print(f"  描述:     {card['description'][:120]}...")
        print(f"  ✅ 解析成功！")
    return card


# ==========================================
# 批量爬取
# ==========================================
def crawl_set(set_code: str, card_count: int, num_workers: int = 5,
              delay: float = 0.3, save: bool = True):
    """
    爬取單一系列全部卡片。
    set_code: 如 'SV8'
    card_count: 該系列總卡數 (從系列列表取得)
    """
    import threading
    import queue

    global UPDATE_STATE
    UPDATE_STATE['running'] = True
    UPDATE_STATE['progress'] = 0
    UPDATE_STATE['current_set'] = set_code
    UPDATE_STATE['completed_sets'] = 0
    UPDATE_STATE['total_sets'] = 1
    UPDATE_STATE['logs'] = []  # 清空舊日誌

    set_code = set_code.upper()
    _jp_log(f"開始爬取: {set_code} (共 {card_count} 張)")
    print(f"   Workers: {num_workers}, 延遲: {delay}s, 寫入DB: {save}")

    task_queue = queue.Queue()
    stats = {"ok": 0, "404": 0, "error": 0}
    lock = threading.Lock()

    def worker(wid: int):
        session = requests.Session()
        session.headers.update(HEADERS)

        while True:
            try:
                num = task_queue.get(timeout=3)
            except queue.Empty:
                break

            url = JP_CARD_URL_TPL.format(set_code=set_code, number=num)
            try:
                resp = session.get(url, timeout=15)
                if resp.status_code == 404:
                    with lock:
                        stats["404"] += 1
                    task_queue.task_done()
                    continue
                resp.raise_for_status()

                card = parse_jp_card(resp.text, set_code, str(num))
                if card:
                    if save:
                        card['set_total'] = str(card_count)  # 系列總張數
                        save_to_jp_cards(card)
                    with lock:
                        stats["ok"] += 1
                        total_done = stats["ok"] + stats["404"] + stats["error"]
                        if stats["ok"] % 20 == 0:
                            pct = int(total_done / card_count * 100) if card_count else 0
                            UPDATE_STATE['progress'] = pct
                            _jp_log(f"[{set_code}] {total_done}/{card_count} ({pct}%)")
                else:
                    with lock:
                        stats["error"] += 1
            except Exception as e:
                with lock:
                    stats["error"] += 1
                    if stats["error"] <= 5:
                        _jp_log(f"⚠️ Worker {wid} #{num}: {e}")

            task_queue.task_done()
            time.sleep(delay)

        session.close()

    # 放入佇列
    for n in range(1, card_count + 1):
        task_queue.put(n)

    # 啟動 workers
    workers = []
    for i in range(num_workers):
        t = threading.Thread(target=worker, args=(i + 1,), daemon=True)
        t.start()
        workers.append(t)

    task_queue.join()
    UPDATE_STATE['running'] = False
    UPDATE_STATE['progress'] = 100
    _jp_log(f"✅ {set_code} 完成！成功: {stats['ok']}, 404: {stats['404']}, 錯誤: {stats['error']}")
    return stats


def crawl_all(workers: int = 10, delay: float = 0.3):
    """爬取所有日版系列。先取得系列列表，再逐一爬取。"""
    global UPDATE_STATE
    UPDATE_STATE['running'] = True
    UPDATE_STATE['progress'] = 0
    UPDATE_STATE['logs'] = []  # 清空舊日誌

    sets = fetch_jp_sets()
    if not sets:
        UPDATE_STATE['running'] = False
        _jp_log("❌ 無法取得系列列表")
        return

    total_cards = sum(s['card_count'] for s in sets)
    UPDATE_STATE['total_sets'] = len(sets)
    UPDATE_STATE['completed_sets'] = 0
    _jp_log(f"共 {len(sets)} 個系列，{total_cards} 張卡")

    grand_stats = {"ok": 0, "404": 0, "error": 0}
    for i, s in enumerate(sets):
        UPDATE_STATE['current_set'] = s['code']
        UPDATE_STATE['completed_sets'] = i
        UPDATE_STATE['progress'] = int(i / len(sets) * 100) if len(sets) else 0
        _jp_log(f"[{i + 1}/{len(sets)}] {s['code']} {s['name']} ({s['card_count']} 張)")
        st = crawl_set(s['code'], s['card_count'], num_workers=workers,
                       delay=delay, save=True)
        grand_stats["ok"] += st["ok"]
        grand_stats["404"] += st["404"]
        grand_stats["error"] += st["error"]

    UPDATE_STATE['running'] = False
    UPDATE_STATE['progress'] = 100
    _jp_log(f"🎉 全系列完成！成功: {grand_stats['ok']}, "
            f"404: {grand_stats['404']}, 錯誤: {grand_stats['error']}")
    return grand_stats
