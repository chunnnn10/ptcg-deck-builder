"""
日本 PTCG 官方網站爬蟲
Source: https://www.pokemon-card.com/card-search/
爬取策略: 遍歷 card ID → GET details.php/card/{id}/regu/all → 解析 HTML

對照 official_hk.py 的 dataclass 模式 + crawler.py 的多線程架構
"""
import re
import os
import json
import time
import hashlib
import logging
import threading
import queue
from typing import Optional
from urllib.parse import urljoin
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup, Tag

import config
import database

logger = logging.getLogger(__name__)

# ==========================================
# 常數
# ==========================================
JP_BASE_URL = "https://www.pokemon-card.com"
JP_DETAIL_URL_TEMPLATE = "https://www.pokemon-card.com/card-search/details.php/card/{card_id}/regu/all"
JP_IMAGE_DIR = os.path.join(config.ROOT_DIR, 'data', 'images_jp')

JP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "ja,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
    "Referer": "https://www.pokemon-card.com/card-search/index.php",
}

# CSS class → 英文屬性名
ENERGY_ICON_MAP = {
    "icon-grass": "Grass",
    "icon-fire": "Fire",
    "icon-water": "Water",
    "icon-lightning": "Lightning",
    "icon-psychic": "Psychic",
    "icon-fighting": "Fighting",
    "icon-darkness": "Darkness",
    "icon-metal": "Metal",
    "icon-fairy": "Fairy",
    "icon-colorless": "Colorless",
    "icon-none": "Colorless",  # 無色
    "icon-dragon": "Dragon",
}

# 訓練家子類型關鍵字 (日文 → 英文)
TRAINER_SUBTYPE_MAP = {
    "グッズ": "Item",
    "サポート": "Supporter",
    "スタジアム": "Stadium",
    "ポケモンのどうぐ": "Pokémon Tool",
    "ポケモンの道具": "Pokémon Tool",
    "ACE SPEC": "ACE SPEC",
}

# 進化階段關鍵字 (日文 → 英文)
EVOLUTION_STAGE_MAP = {
    "たね": "Basic",
    "1進化": "Stage 1",
    "2進化": "Stage 2",
}

# ==========================================
# 全域狀態 (供爬蟲 UI 讀取)
# ==========================================
JP_UPDATE_STATE = {
    'running': False,
    'progress': 0,
    'message': '就緒',
    'logs': [],
    'total_tasks': 0,
    'completed_tasks': 0,
}
jp_update_lock = threading.Lock()


def jp_log(msg: str):
    """寫入爬蟲日誌"""
    print(f"[JP Crawler] {msg}")
    with jp_update_lock:
        JP_UPDATE_STATE['message'] = msg
        JP_UPDATE_STATE['logs'].insert(0, msg)
        if len(JP_UPDATE_STATE['logs']) > 300:
            JP_UPDATE_STATE['logs'].pop()


# ==========================================
# 牌庫自動同步狀態 (獨立於手動爬蟲狀態)
# ==========================================
JP_CARD_AUTO_SYNC_STATE = {
    'running': False,
    'enabled': True,
    'progress': 0,
    'total_tasks': 0,
    'completed_tasks': 0,
    'last_run': None,
    'next_run': None,
    'last_summary': None,
    'last_missing_count': 0,
    'last_crawled_count': 0,
    'logs': [],
}
jp_auto_sync_lock = threading.Lock()


def auto_sync_log(msg: str):
    """寫入牌庫自動同步日誌"""
    print(f"[JP Card AutoSync] {msg}")
    with jp_auto_sync_lock:
        JP_CARD_AUTO_SYNC_STATE['logs'].insert(0, msg)
        if len(JP_CARD_AUTO_SYNC_STATE['logs']) > 200:
            JP_CARD_AUTO_SYNC_STATE['logs'].pop()


# ==========================================
# Dataclass
# ==========================================
@dataclass
class JPCardData:
    card_id: int = 0
    name: str = ""
    card_type: str = "Pokémon"
    sub_type: str = "Basic"
    super_type: str = ""
    hp: int = 0
    element_type: str = ""
    skills: list[dict] = field(default_factory=list)
    weakness_type: str = ""
    weakness_value: str = ""
    resistance_type: str = ""
    resistance_value: str = ""
    retreat_cost: int = 0
    regulation_mark: str = ""
    set_number: str = ""       # 收藏編號 (如 "040")
    set_total: str = ""        # 總張數 (如 "081")
    rarity: str = ""
    pokedex_number: str = ""
    pokedex_category: str = ""
    height: str = ""
    weight: str = ""
    flavor_text: str = ""
    artist: str = ""
    evolves_from: str = ""
    set_code: str = ""         # 產品代碼 (從擴充包連結取得)
    set_name: str = ""         # 擴充包名稱
    image_url: str = ""
    description: str = ""      # 訓練家卡效果文字


# ==========================================
# HTTP Helpers
# ==========================================
def _fetch(url: str, max_retries: int = 3, timeout: int = 15, session=None) -> Optional[BeautifulSoup]:
    """GET 頁面，回傳 BeautifulSoup。可傳入 requests.Session 重用連線。"""
    fetcher = session or requests
    for attempt in range(max_retries):
        try:
            resp = fetcher.get(url, headers=JP_HEADERS, timeout=timeout)
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            resp.encoding = "utf-8"
            # lxml 比 html.parser 快 ~5x
            return BeautifulSoup(resp.text, "lxml")
        except requests.RequestException as e:
            logger.debug(f"Request failed ({attempt + 1}/{max_retries}): {e}")
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
    return None


def _download_image(url: str, save_path: str) -> bool:
    """下載圖片，回傳是否成功"""
    os.makedirs(os.path.dirname(save_path), exist_ok=True)
    if os.path.exists(save_path) and os.path.getsize(save_path) > 1000:
        return True  # 已存在且有效
    try:
        if not url.startswith("http"):
            url = urljoin(JP_BASE_URL, url)
        resp = requests.get(url, headers=JP_HEADERS, timeout=30, stream=True)
        if resp.status_code == 200:
            with open(save_path, 'wb') as f:
                for chunk in resp.iter_content(1024):
                    f.write(chunk)
            return True
    except Exception as e:
        logger.warning(f"Image download failed: {url} → {e}")
    return False


def _text(el: Optional[Tag], strip: bool = True) -> str:
    """安全取得 Tag 文字"""
    if el is None:
        return ""
    return el.get_text(strip=strip)


def _extract_energy_from_class(class_str: str) -> str:
    """從 CSS class 字串提取屬性名"""
    if not class_str:
        return ""
    classes = class_str.split()
    for cls in classes:
        if cls in ENERGY_ICON_MAP:
            return ENERGY_ICON_MAP[cls]
    return ""


def _extract_energy_from_icon_tags(container: Optional[Tag]) -> list[str]:
    """從容器中提取所有能量圖示的屬性"""
    if not container:
        return []
    energies = []
    for span in container.select("span.icon, img"):
        cls = span.get("class", [])
        if isinstance(cls, list):
            cls = " ".join(cls)
        energy = _extract_energy_from_class(cls)
        if energy and energy != "Colorless":  # Colorless 通常不算技能費用
            energies.append(energy)
        elif energy == "Colorless":
            energies.append("Colorless")
    return energies


# ==========================================
# 核心解析
# ==========================================
def parse_detail_page(card_id: int, session=None) -> Optional[JPCardData]:
    """
    解析單張卡牌詳情頁。
    支援：寶可夢、訓練家（グッズ/サポート/スタジアム/ポケモンのどうぐ）、能量
    """
    url = JP_DETAIL_URL_TEMPLATE.format(card_id=card_id)
    soup = _fetch(url, session=session)
    if not soup:
        return None

    card = JPCardData(card_id=card_id)

    # ── 名稱 ──
    h1 = soup.select_one("h1.Heading1")
    if not h1:
        return None
    card.name = _text(h1)

    # ── 圖片 URL ──
    img_tag = soup.select_one(".LeftBox img.fit")
    if img_tag:
        card.image_url = img_tag.get("src", "")

    # ── 賽季標記 & 收藏編號 & 稀有度 ──
    subtext = soup.select_one(".subtext")
    if subtext:
        reg_img = subtext.select_one("img.img-regulation")
        if reg_img:
            alt = reg_img.get("alt", "")
            src = reg_img.get("src", "")
            # 從 alt 或 src 檔名取
            card.regulation_mark = alt or os.path.splitext(os.path.basename(src))[0]

        # 收藏編號：完整格式 "040/081"（與 TW 格式一致）
        subtext_text = _text(subtext)
        cn_match = re.search(r"(\d+)\s*/\s*(\d+)", subtext_text)
        if cn_match:
            card.set_number = f"{cn_match.group(1)}/{cn_match.group(2)}"
            card.set_total = cn_match.group(2)

        # 稀有度圖示
        rarity_img = subtext.select_one("img[src*='rarity']")
        if rarity_img:
            rarity_src = rarity_img.get("src", "")
            rarity_match = re.search(r"ic_rare_(\w+)\.", rarity_src)
            if rarity_match:
                card.rarity = rarity_match.group(1).upper()

    # ── TopInfo: 進化階段 / HP / 屬性 ──
    top_info = soup.select_one(".TopInfo")
    if top_info:
        # 進化階段
        type_span = top_info.select_one(".type")
        if type_span:
            stage_text = _text(type_span)
            # 標準化 "1 進化" → "1進化" (去除 nbsp 等空白)
            stage_text = re.sub(r'\s+', '', stage_text)
            card.sub_type = EVOLUTION_STAGE_MAP.get(stage_text, stage_text)

        # HP
        hp_num = top_info.select_one(".hp-num")
        if hp_num:
            try:
                card.hp = int(_text(hp_num))
            except ValueError:
                card.hp = 0

        # 屬性 (從 td-r 中的 icon-{type} 判斷)
        td_r = top_info.select_one(".td-r")
        if td_r:
            for span in td_r.select("span[class*='icon']"):
                cls = " ".join(span.get("class", []))
                energy = _extract_energy_from_class(cls)
                if energy:
                    card.element_type = energy
                    break

    # ── 卡片類型判斷 ──
    card.card_type = "Pokémon"  # 預設

    # 檢查右側內容區的 h2 標題
    right_box = soup.select_one(".RightBox-inner")
    trainer_type_detected = None
    energy_type_detected = None
    ability_section = False
    if right_box:
        h2_tags = right_box.select("h2")
        for h2 in h2_tags:
            h2_text = _text(h2)
            # 能量類型
            if "エネルギー" in h2_text:
                card.card_type = "Energy"
                card.sub_type = "Special" if "特殊" in h2_text else "Basic"
                card.element_type = ""
                energy_type_detected = card.sub_type
                break
            # 訓練家子類型
            for jp_kw, en_kw in TRAINER_SUBTYPE_MAP.items():
                if jp_kw in h2_text:
                    card.card_type = "Trainer"
                    card.sub_type = en_kw
                    trainer_type_detected = en_kw
                    break
            if trainer_type_detected:
                break
            # 特性 / ワザ
            if "ワザ" in h2_text:
                pass  # 正常寶可夢
            elif "特性" in h2_text:
                ability_section = True

    # ── 技能 / 招式 ──
    if right_box:
        # 找到 <h2>ワザ</h2> 後的技能
        skill_section_started = False
        ability_section_started = False
        current_skill: dict = {}
        desc_parts: list[str] = []

        for el in right_box.children:
            if not hasattr(el, 'name') or el.name is None:
                continue

            if el.name == 'h2':
                h2_text = _text(el)
                if 'ワザ' in h2_text:
                    skill_section_started = True
                    ability_section_started = False
                    # 結算上一個技能
                    if current_skill and current_skill.get('name'):
                        card.skills.append(current_skill)
                    current_skill = {}
                elif '特性' in h2_text:
                    ability_section_started = True
                    skill_section_started = False
                    if current_skill and current_skill.get('name'):
                        card.skills.append(current_skill)
                    current_skill = {}
                else:
                    skill_section_started = False
                    ability_section_started = False

            elif el.name == 'h4' and (skill_section_started or ability_section_started):
                # 上一個技能結算
                if current_skill and current_skill.get('name'):
                    card.skills.append(current_skill)

                current_skill = {"name": "", "cost": [], "damage": "", "effect": "", "type": "attack"}
                if ability_section_started:
                    current_skill["type"] = "ability"

                # 名稱
                name_span = el.select_one("span.skillName")
                if not name_span:
                    # 直接文字
                    skill_text = _text(el)
                    # 移除右側傷害文字
                    dmg_span = el.select_one("span.f_right, .skillDamage")
                    if dmg_span:
                        dmg = _text(dmg_span)
                        skill_text = skill_text.replace(dmg, "").strip()
                        current_skill["damage"] = dmg
                    current_skill["name"] = skill_text
                else:
                    current_skill["name"] = _text(name_span)
                    dmg_span = el.select_one("span.f_right, .skillDamage")
                    if dmg_span:
                        current_skill["damage"] = _text(dmg_span)

                # 能量費用圖示
                cost_icons = el.select("span.icon:not(.f_right)")
                if not cost_icons:
                    cost_icons = el.select("img")
                for icon in cost_icons:
                    cls = " ".join(icon.get("class", [])) if icon.name == "span" else ""
                    energy = _extract_energy_from_class(cls)
                    if energy:
                        current_skill["cost"].append(energy)

            elif el.name == 'p' and current_skill:
                # 技能效果
                effect = _text(el)
                if effect:
                    current_skill["effect"] = effect

        # 最後一個技能
        if current_skill and current_skill.get('name'):
            card.skills.append(current_skill)

        # ── 訓練家 / 能量描述 ──
        if card.card_type in ("Trainer", "Energy"):
            # 找 h2 之後的第一個 p（不屬於技能的效果文字）
            h2_found = False
            for el in right_box.children:
                if not hasattr(el, 'name') or el.name is None:
                    continue
                if el.name == 'h2':
                    h2_found = True
                    continue
                if h2_found and el.name == 'p':
                    desc = _text(el)
                    if desc and not card.description:
                        card.description = desc
                    break

    # ── 弱點・抵抗力・撤退 ──
    table = soup.select_one("table")
    if table:
        tds = table.select("td")
        if len(tds) >= 1:
            # 弱點
            weak_td = tds[0]
            for span in weak_td.select("span[class*='icon']"):
                cls = " ".join(span.get("class", []))
                energy = _extract_energy_from_class(cls)
                if energy:
                    card.weakness_type = energy
                    break
            weak_text = _text(weak_td)
            wm = re.search(r"[×x]([0-9]+)", weak_text)
            if wm:
                card.weakness_value = f"×{wm.group(1)}"

        if len(tds) >= 2:
            # 抵抗力
            resist_td = tds[1]
            for span in resist_td.select("span[class*='icon']"):
                cls = " ".join(span.get("class", []))
                energy = _extract_energy_from_class(cls)
                if energy:
                    card.resistance_type = energy
                    break
            resist_text = _text(resist_td)
            rm = re.search(r"[-−]([0-9]+)", resist_text)
            if rm:
                card.resistance_value = f"-{rm.group(1)}"

        if len(tds) >= 3:
            # 撤退
            escape_td = tds[2]
            card.retreat_cost = len(escape_td.select("span.icon") or escape_td.select("img"))

    # ── 圖鑑資訊 ──
    card_box = soup.select_one(".card")
    if card_box:
        h4 = card_box.select_one("h4")
        if h4:
            pk_text = _text(h4)
            # "No.056　ぶたざるポケモン"
            pk_num_match = re.search(r"No\.(\d+)", pk_text)
            if pk_num_match:
                card.pokedex_number = pk_num_match.group(1)
            pk_cat = re.sub(r"No\.\d+", "", pk_text).strip()
            if pk_cat:
                card.pokedex_category = pk_cat

        # 身高・體重 (第一個 p)
        ps = card_box.select("p")
        if ps:
            size_text = _text(ps[0])
            h_match = re.search(r"高さ[：:]\s*([\d.]+\s*m)", size_text)
            if h_match:
                card.height = h_match.group(1)
            w_match = re.search(r"重さ[：:]\s*([\d.]+\s*kg)", size_text)
            if w_match:
                card.weight = w_match.group(1)

        # 圖鑑描述 (hr 之後的 p)
        hr = card_box.select_one("hr")
        if hr:
            next_p = hr.find_next("p")
            if next_p:
                card.flavor_text = _text(next_p)

    # ── 繪師 ──
    author = soup.select_one(".author a")
    if author:
        card.artist = _text(author)

    # ── 擴充包資訊 ──
    sub_section = soup.select_one(".SubSection")
    if sub_section:
        exp_link = sub_section.select_one(".List_item a")
        if exp_link:
            card.set_name = _text(exp_link)
            href = exp_link.get("href", "")
            # /ex/m5/ → set_code = m5
            exp_match = re.search(r"/ex/([\w-]+)/", href)
            if exp_match:
                card.set_code = exp_match.group(1).upper()

    # ── 進化鏈 ──
    evo_section = soup.select_one(".evolution")
    if evo_section:
        # JP 站的進化鏈格式可能與 HK 不同，嘗試常見結構
        first_step = evo_section.select_one(".step.active a, .evolutionStep.first .step.active a")
        if first_step:
            card.evolves_from = _text(first_step)

    # ── 跳過無效頁面 ──
    if card.name == "カード検索" or "検索" in card.name:
        return None

    # ── 能量卡最終判定 ──
    # 條件：名含「エネルギー」或右側 h2 是エネルギー、且非訓練家、非寶可夢（無戰鬥數據）
    if card.card_type == "Pokémon" and card.hp == 0 and not card.element_type:
        name_and_skills = card.name + " " + " ".join([s.get("name", "") for s in card.skills])
        if "エネルギー" in name_and_skills or "Energy" in name_and_skills:
            card.card_type = "Energy"
            card.sub_type = "Special" if "特殊" in name_and_skills else "Basic"
            card.element_type = ""
            card.skills = []
    elif card.card_type == "Pokémon" and card.hp == 0 and not card.element_type:
        # 無 HP、無屬性、非訓練家 → 可能是能量或特殊卡
        has_battle_stats = soup.select_one("table") is not None
        if not has_battle_stats:
            card.card_type = "Energy"
            card.sub_type = "Basic"

    return card


# ==========================================
# 資料庫寫入
# ==========================================
def save_card_to_db(card: JPCardData, skip_images: bool = False, conn=None) -> bool:
    """將解析後的卡牌寫入 jp_cards 表 (UPSERT)。可傳入現有連線重用。"""
    card_id_str = f"jp{card.card_id:06d}"

    # 圖片處理
    img_filename = ""
    if card.image_url and not skip_images:
        ext = os.path.splitext(card.image_url.split("?")[0])[1] or ".jpg"
        img_filename = f"{card_id_str}{ext}"
        img_path = os.path.join(JP_IMAGE_DIR, img_filename)
        _download_image(card.image_url, img_path)
    elif card.image_url:
        img_filename = os.path.basename(card.image_url.split("?")[0])

    own_conn = conn is None
    if own_conn:
        conn = database.get_db_connection()
        if not conn:
            return False

    try:
        cursor = conn.cursor()
        skills_json = json.dumps(card.skills, ensure_ascii=False)

        # 保留舊的 chinese_name
        cursor.execute(
            "SELECT chinese_name FROM jp_cards WHERE card_id = %s",
            (card_id_str,)
        )
        existing = cursor.fetchone()
        chinese_name = existing['chinese_name'] if existing else None

        cursor.execute("""
            INSERT INTO jp_cards (
                card_id, image_file, card_type, name, sub_type,
                hp, element_type, weakness_type, weakness_value,
                resistance_type, resistance_value, retreat_cost,
                skills_json, rarity,
                chinese_name, evolution_stage, evolves_from,
                set_code, set_number, set_total, set_name,
                regulation_flags, regulation_mark,
                description,
                flavor_text, pokedex_number, pokedex_category, height, weight
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s,
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
                set_number = EXCLUDED.set_number,
                set_total = EXCLUDED.set_total,
                set_name = EXCLUDED.set_name,
                regulation_flags = EXCLUDED.regulation_flags,
                regulation_mark = EXCLUDED.regulation_mark,
                description = EXCLUDED.description,
                flavor_text = EXCLUDED.flavor_text,
                pokedex_number = EXCLUDED.pokedex_number,
                pokedex_category = EXCLUDED.pokedex_category,
                height = EXCLUDED.height,
                weight = EXCLUDED.weight
        """, (
            card_id_str, img_filename, card.card_type, card.name, card.sub_type,
            card.hp, card.element_type,
            card.weakness_type, card.weakness_value,
            card.resistance_type, card.resistance_value,
            card.retreat_cost,
            skills_json, card.rarity,
            chinese_name, card.sub_type, card.evolves_from,
            card.set_code, card.set_number, card.set_total, card.set_name,
            "Standard", card.regulation_mark,
            card.description,
            card.flavor_text, card.pokedex_number, card.pokedex_category,
            card.height, card.weight
        ))
        if own_conn:
            conn.commit()
        return True
    except Exception as e:
        if own_conn:
            conn.rollback()
        jp_log(f"DB write error for card {card_id_str}: {e}")
        return False
    finally:
        if own_conn:
            conn.close()


# ==========================================
# 批次爬取
# ==========================================
def crawl_card_range(start_id: int, end_id: int, num_workers: int = 30,
                     skip_images: bool = False, request_delay: float = 0.0):
    """
    爬取指定範圍的 card ID。
    多線程：scanner 線程遍歷 ID，worker 線程解析 + 寫入。
    預設 30 workers，無延遲 — 日站承受能力強，不需冷卻。
    """
    global JP_UPDATE_STATE
    with jp_update_lock:
        JP_UPDATE_STATE['running'] = True
        JP_UPDATE_STATE['progress'] = 0
        JP_UPDATE_STATE['completed_tasks'] = 0
        JP_UPDATE_STATE['total_tasks'] = end_id - start_id + 1
        JP_UPDATE_STATE['logs'] = []

    os.makedirs(JP_IMAGE_DIR, exist_ok=True)

    task_queue: queue.Queue = queue.Queue()
    stats = {"parsed": 0, "skipped": 0, "errors": 0, "total": end_id - start_id + 1}

    def worker(worker_id: int):
        session = requests.Session()
        session.headers.update(JP_HEADERS)
        conn = database.get_db_connection()
        batch_count = 0
        try:
            while True:
                try:
                    card_id = task_queue.get(timeout=3)
                except queue.Empty:
                    break

                try:
                    card = parse_detail_page(card_id, session=session)
                    if card:
                        save_card_to_db(card, skip_images=skip_images, conn=conn)
                        batch_count += 1
                        stats["parsed"] += 1
                        if stats["parsed"] % 50 == 0:
                            pct = stats["parsed"] / stats["total"] * 100 if stats["total"] else 0
                            jp_log(f"[{pct:.1f}%] 已解析 {stats['parsed']}/{stats['total']} 張 (當前 ID: {card_id})")
                    else:
                        stats["skipped"] += 1
                except Exception as e:
                    stats["errors"] += 1
                    if stats["errors"] <= 10:
                        jp_log(f"Worker {worker_id} error at ID {card_id}: {e}")

                with jp_update_lock:
                    JP_UPDATE_STATE['completed_tasks'] += 1
                    if JP_UPDATE_STATE['total_tasks'] > 0:
                        JP_UPDATE_STATE['progress'] = int(
                            JP_UPDATE_STATE['completed_tasks'] / JP_UPDATE_STATE['total_tasks'] * 100
                        )

                if batch_count >= 50:
                    conn.commit()
                    batch_count = 0
                task_queue.task_done()

        finally:
            if batch_count > 0:
                conn.commit()
            conn.close()
            session.close()
        jp_log(f"Worker {worker_id} finished")

    # 啟動 workers
    workers = []
    for i in range(num_workers):
        t = threading.Thread(target=worker, args=(i + 1,), daemon=True)
        t.start()
        workers.append(t)

    # Scanner: 將所有 ID 放入 queue
    for card_id in range(start_id, end_id + 1):
        task_queue.put(card_id)

    jp_log(f"開始爬取 ID 範圍 {start_id} → {end_id}，共 {stats['total']} 個任務")

    # 等待完成
    task_queue.join()

    with jp_update_lock:
        JP_UPDATE_STATE['running'] = False
        JP_UPDATE_STATE['progress'] = 100

    jp_log(f"🎉 爬取完成！解析: {stats['parsed']}, 跳過: {stats['skipped']}, 錯誤: {stats['errors']}")
    return stats


def crawl_all(workers: int = 30, skip_images: bool = False):
    """
    爬取所有已知 ID 範圍 (1 → 52000)。
    建議先用 test_crawl 測試少量 ID 再執行。
    """
    return crawl_card_range(1, 52000, num_workers=workers, skip_images=skip_images)


def test_crawl(count: int = 20, start_id: int = 50250):
    """測試爬取：少量 ID 以驗證解析邏輯"""
    jp_log(f"測試爬取 {count} 張卡牌，從 ID {start_id} 開始")
    return crawl_card_range(start_id, start_id + count, num_workers=3, skip_images=False)


# ==========================================
# 擴充包列表抓取
# ==========================================
def fetch_jp_expansion_meta() -> list[dict]:
    """
    從 JP 搜尋頁的 JS 資料中提取擴充包列表。
    回傳: [{'code': '954', 'name': '拡張パック「アビスアイ」', 'series': ''}, ...]
    """
    url = "https://www.pokemon-card.com/card-search/index.php"
    jp_log("正在同步 JP 擴充包列表...")
    try:
        resp = requests.get(url, headers=JP_HEADERS, timeout=15)
        if resp.status_code != 200:
            jp_log(f"JP 搜尋頁回應異常: {resp.status_code}")
            return []
        html = resp.text
    except Exception as e:
        # 搜尋頁可能被擋 (403)，不影響卡牌詳情頁爬取
        logger.debug(f"JP search page fetch failed (non-critical): {e}")
        return []

    expansions = []
    # 匹配 JS 中的擴充包資料: { name: "pg", value: "954", label: "拡張パック「アビスアイ」" }
    # 也支援字母代碼如 "SV-P", "M-P"
    pattern = r'\{\s*name:\s*"pg",\s*value:\s*"([^"]+)",\s*(?:group:\s*"[^"]*",\s*)?label:\s*"([^"]+)"\s*\}'
    matches = re.findall(pattern, html)

    seen = set()
    for code, name in matches:
        if code and code not in seen and len(name) > 2:
            seen.add(code)
            expansions.append({'code': code, 'name': name, 'series': ''})

    if not expansions:
        # Fallback
        pattern2 = r'"value":"([^"]+)","label":"([^"]+)"'
        for code, name in re.findall(pattern2, html):
            if code and code not in seen and len(name) > 2:
                seen.add(code)
                expansions.append({'code': code, 'name': name, 'series': ''})

    jp_log(f"從 JP 搜尋頁找到 {len(expansions)} 個擴充包")
    return expansions


def crawl_expansion_card_ids(expansion_code: str, max_pages: int = 20) -> list[int]:
    """
    從搜尋結果頁解析指定擴充包的所有卡牌 ID。
    URL 格式: index.php?pg={code}&regulation_sidebar_form=all&sm_and_keyword=true&page={n}
    回傳 card_id 列表 (整數)。
    """
    card_ids = []
    for page in range(1, max_pages + 1):
        url = (f"https://www.pokemon-card.com/card-search/index.php"
               f"?keyword=&se_ta=&regulation_sidebar_form=all"
               f"&pg={expansion_code}&illust=&sm_and_keyword=true&page={page}")
        soup = _fetch(url)
        if not soup:
            break

        # 解析卡牌圖片 URL，提取 card ID
        # <img data-src="/assets/images/card_images/large/M5/050220_P_TOROPIUSU.jpg">
        found_any = False
        for img in soup.select("img[data-src], img[src]"):
            src = img.get("data-src") or img.get("src") or ""
            # 從路徑提取 6 位數字 ID: /large/XX/050220_...
            match = re.search(r"/(\d{6})_", src)
            if match:
                card_id = int(match.group(1))
                if card_id not in card_ids:
                    card_ids.append(card_id)
                    found_any = True

        if not found_any:
            break  # 沒有更多卡牌，結束分頁

        jp_log(f"  擴充包 {expansion_code} 第 {page} 頁: 找到 {len(card_ids)} 個 ID")
        time.sleep(0.1)

    return card_ids


def crawl_by_expansions(expansion_codes: list[str], num_workers: int = 30,
                        skip_images: bool = False) -> dict:
    """
    按擴充包爬取：先從搜尋頁取得卡牌 ID 列表，再抓詳情頁。
    比遍歷 ID 快得多（只爬有效的卡）。
    """
    all_card_ids = []
    for code in expansion_codes:
        jp_log(f"正在掃描擴充包: {code}")
        ids = crawl_expansion_card_ids(code)
        jp_log(f"  擴充包 {code}: 共 {len(ids)} 張卡牌")
        all_card_ids.extend(ids)

    # 去重
    unique_ids = sorted(set(all_card_ids))
    jp_log(f"所有擴充包共 {len(unique_ids)} 張不重複卡牌，開始爬取詳情...")

    # 用現有的多線程爬取
    return _crawl_id_list(unique_ids, num_workers=num_workers, skip_images=skip_images)


def _crawl_id_list(card_ids: list[int], num_workers: int = 30,
                   skip_images: bool = False,
                   state: dict = None, lock=None, log_fn=None) -> dict:
    """
    爬取指定 ID 列表（複用 crawl_card_range 的多線程邏輯但只爬指定 ID）。

    state/lock/log_fn 可傳入自訂物件，供「牌庫自動同步」使用獨立狀態，
    避免與手動爬蟲的 JP_UPDATE_STATE 互相干擾；不傳則沿用預設全域狀態。
    """
    global JP_UPDATE_STATE
    if state is None:
        state = JP_UPDATE_STATE
    if lock is None:
        lock = jp_update_lock
    if log_fn is None:
        log_fn = jp_log
    with lock:
        state['running'] = True
        state['progress'] = 0
        state['completed_tasks'] = 0
        state['total_tasks'] = len(card_ids)
        state['logs'] = []

    os.makedirs(JP_IMAGE_DIR, exist_ok=True)

    task_queue: queue.Queue = queue.Queue()
    stats = {"parsed": 0, "skipped": 0, "errors": 0, "total": len(card_ids)}

    def worker(worker_id: int):
        session = requests.Session()
        session.headers.update(JP_HEADERS)
        conn = database.get_db_connection()
        batch_count = 0
        try:
            while True:
                try:
                    card_id = task_queue.get(timeout=3)
                except queue.Empty:
                    break
                try:
                    card = parse_detail_page(card_id, session=session)
                    if card:
                        save_card_to_db(card, skip_images=skip_images, conn=conn)
                        batch_count += 1
                        stats["parsed"] += 1
                        if stats["parsed"] % 50 == 0:
                            pct = stats["parsed"] / stats["total"] * 100 if stats["total"] else 0
                            log_fn(f"[{pct:.1f}%] 已解析 {stats['parsed']}/{stats['total']} 張 (當前 ID: {card_id})")
                    else:
                        stats["skipped"] += 1
                except Exception as e:
                    stats["errors"] += 1
                    if stats["errors"] <= 10:
                        log_fn(f"Worker {worker_id} error at ID {card_id}: {e}")

                with lock:
                    state['completed_tasks'] += 1
                    if state['total_tasks'] > 0:
                        state['progress'] = int(
                            state['completed_tasks'] / state['total_tasks'] * 100
                        )
                if batch_count >= 50:
                    conn.commit()
                    batch_count = 0
                task_queue.task_done()
        finally:
            if batch_count > 0:
                conn.commit()
            conn.close()
            session.close()

    workers = []
    for i in range(num_workers):
        t = threading.Thread(target=worker, args=(i + 1,), daemon=True)
        t.start()
        workers.append(t)

    for cid in card_ids:
        task_queue.put(cid)

    log_fn(f"開始爬取 {len(card_ids)} 個指定 ID，{num_workers} workers")

    task_queue.join()

    with lock:
        state['running'] = False
        state['progress'] = 100

    log_fn(f"🎉 爬取完成！解析: {stats['parsed']}, 跳過: {stats['skipped']}, 錯誤: {stats['errors']}")
    return stats


# ==========================================
# ID 範圍偵測 (可選：自動偵測最大 ID)
# ==========================================
def detect_max_card_id(sample_step: int = 500) -> int:
    """
    二分搜尋最大有效 card ID。
    日站卡牌 ID 不是連續的，此為粗略估計。
    """
    low, high = 1, 60000
    while low < high:
        mid = (low + high) // 2
        url = JP_DETAIL_URL_TEMPLATE.format(card_id=mid)
        try:
            resp = requests.get(url, headers=JP_HEADERS, timeout=10)
            if resp.status_code == 200 and "マンキー" not in resp.text[:200]:
                # 粗略判斷：200 OK 且有內容
                low = mid + 1
            else:
                high = mid
        except Exception:
            high = mid
        time.sleep(0.1)
    jp_log(f"估計最大 card ID: {low}")
    return low


# ==========================================
# 牌庫每日自動同步 (偵測缺漏卡牌並補爬)
# ==========================================
def _get_db_card_ids() -> set[int]:
    """取得 jp_cards 表中已存在的 card ID（整數集合）。"""
    ids: set[int] = set()
    conn = database.get_db_connection()
    if not conn:
        auto_sync_log("無法取得 DB 連線")
        return ids
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT card_id FROM jp_cards")
        for row in cursor.fetchall():
            # 相容 dict-row 與 tuple-row
            cid = row['card_id'] if isinstance(row, dict) else row[0]
            s = str(cid)
            if s.startswith('jp'):
                s = s[2:]
            try:
                ids.add(int(s))
            except ValueError:
                continue
    except Exception as e:
        auto_sync_log(f"讀取 DB card_id 失敗: {e}")
    finally:
        conn.close()
    return ids


def _collect_site_card_ids(max_expansions: int = None) -> set[int]:
    """
    從 JP 官網擴充包列表彙整所有已發行卡牌 ID（精準）。
    回傳整數集合；若搜尋頁被擋無法取得擴充包則回傳空集合。
    """
    expansions = fetch_jp_expansion_meta()
    if not expansions:
        auto_sync_log("無法取得擴充包列表（搜尋頁可能被擋），將改用 ID 上限偵測")
        return set()
    auto_sync_log(f"共 {len(expansions)} 個擴充包，開始彙整卡牌 ID...")
    site_ids: set[int] = set()
    for idx, exp in enumerate(expansions, 1):
        code = exp.get('code')
        if not code:
            continue
        try:
            ids = crawl_expansion_card_ids(code)
            site_ids.update(ids)
        except Exception as e:
            auto_sync_log(f"擴充包 {code} ID 抓取失敗: {e}")
        if max_expansions and idx >= max_expansions:
            auto_sync_log(f"已達單次擴充包上限 {max_expansions}，其餘下次再補")
            break
        time.sleep(0.05)
    auto_sync_log(f"官網共彙整 {len(site_ids)} 個不重複卡牌 ID")
    return site_ids


def detect_missing_cards(max_expansions: int = None) -> dict:
    """
    偵測 DB 缺漏的卡牌 ID。

    優先用擴充包彙整（精準，能抓到所有缺口）；
    若搜尋頁被擋則退回 detect_max_card_id 範圍偵測（只補 DB 最大 ID 之後的新卡）。

    回傳:
      {'missing': [int...], 'source': 'expansion'|'range',
       'db_count': int, 'site_count': int|None}
    """
    db_ids = _get_db_card_ids()
    auto_sync_log(f"DB 現有 {len(db_ids)} 張卡牌")

    site_ids = _collect_site_card_ids(max_expansions=max_expansions)
    if site_ids:
        missing = sorted(site_ids - db_ids)
        return {
            'missing': missing,
            'source': 'expansion',
            'db_count': len(db_ids),
            'site_count': len(site_ids),
        }

    # Fallback：只用 ID 上限偵測 DB 最大 ID 之後的新卡
    auto_sync_log("擴充包彙整失敗，改用 ID 上限範圍偵測（僅補新卡）")
    max_db = max(db_ids) if db_ids else 0
    site_max = detect_max_card_id()
    auto_sync_log(f"DB 最大 ID={max_db}，官網估計最大 ID={site_max}")
    candidate = set(range(max_db + 1, site_max + 1))
    missing = sorted(candidate - db_ids)
    return {
        'missing': missing,
        'source': 'range',
        'db_count': len(db_ids),
        'site_count': None,
    }


def run_daily_card_sync(num_workers: int = 20, skip_images: bool = False,
                        max_missing_per_run: int = 2000) -> dict:
    """
    每日牌庫自動同步：
      1) 偵測 DB 缺漏的卡牌 ID
      2) 補爬缺漏（單次上限 max_missing_per_run，超過則分批於後續每日消化）
      3) 回傳摘要

    與手動爬蟲互斥：若 JP_UPDATE_STATE['running']（手動爬蟲中）則跳過本次。
    """
    with jp_auto_sync_lock:
        if JP_CARD_AUTO_SYNC_STATE['running']:
            auto_sync_log("上次同步仍在執行中，跳過本次")
            return {'status': 'skipped', 'reason': 'auto_sync_already_running'}
        if JP_UPDATE_STATE['running']:
            auto_sync_log("手動爬蟲進行中，跳過本次自動同步")
            return {'status': 'skipped', 'reason': 'manual_crawl_running'}
        JP_CARD_AUTO_SYNC_STATE['running'] = True
        JP_CARD_AUTO_SYNC_STATE['progress'] = 0
        JP_CARD_AUTO_SYNC_STATE['last_run'] = time.strftime('%Y-%m-%d %H:%M:%S')

    try:
        result = detect_missing_cards()
        missing = result['missing']
        JP_CARD_AUTO_SYNC_STATE['last_missing_count'] = len(missing)

        if not missing:
            auto_sync_log("✅ 牌庫已是最新，無缺漏卡牌")
            summary = {
                'status': 'up_to_date',
                'source': result['source'],
                'db_count': result['db_count'],
                'site_count': result['site_count'],
                'missing': 0,
                'crawled': 0,
                'remaining': 0,
            }
        else:
            auto_sync_log(f"偵測到 {len(missing)} 張缺漏卡牌（來源: {result['source']}）")
            batch = missing[:max_missing_per_run]
            if len(missing) > max_missing_per_run:
                auto_sync_log(
                    f"缺漏數超過單次上限 {max_missing_per_run}，本次先補前 {len(batch)} 張，"
                    f"其餘 {len(missing) - len(batch)} 張下次續補"
                )
            stats = _crawl_id_list(
                batch,
                num_workers=num_workers,
                skip_images=skip_images,
                state=JP_CARD_AUTO_SYNC_STATE,
                lock=jp_auto_sync_lock,
                log_fn=auto_sync_log,
            )
            summary = {
                'status': 'crawled',
                'source': result['source'],
                'db_count': result['db_count'],
                'site_count': result['site_count'],
                'missing': len(missing),
                'crawled': stats.get('parsed', 0),
                'skipped': stats.get('skipped', 0),
                'errors': stats.get('errors', 0),
                'remaining': max(0, len(missing) - len(batch)),
            }
        auto_sync_log(f"同步完成: {summary}")
        JP_CARD_AUTO_SYNC_STATE['last_summary'] = summary
        JP_CARD_AUTO_SYNC_STATE['last_crawled_count'] = summary.get('crawled', 0)
        return summary
    except Exception as e:
        auto_sync_log(f"❌ 牌庫自動同步失敗: {e}")
        logger.exception("JP card auto sync failed")
        return {'status': 'error', 'error': str(e)}
    finally:
        with jp_auto_sync_lock:
            JP_CARD_AUTO_SYNC_STATE['running'] = False
            JP_CARD_AUTO_SYNC_STATE['progress'] = 100


def get_auto_sync_status() -> dict:
    """供 API 讀取牌庫自動同步狀態（淺拷貍避免外部修改）。"""
    with jp_auto_sync_lock:
        return dict(JP_CARD_AUTO_SYNC_STATE)
