import json
import random
import string
import threading
import os
import re
import requests
import smtplib
import psycopg2
from functools import wraps  # [新增] 用於裝飾器
from email.mime.text import MIMEText
from bs4 import BeautifulSoup
from flask import Blueprint, render_template, request, jsonify, send_from_directory, url_for
from flask_login import login_user, logout_user, login_required, current_user
from werkzeug.utils import secure_filename
from datetime import datetime
import uuid

import config
import database
from services.crawler import crawler
from models import User
from services.tcgdex.bridge import find_chinese_card, find_japanese_card, get_bridge
from services.tcgdex.client import API_BASE as TCGDEX_API_BASE, get_client as get_tcgdex_client
from services.deck_importer.card_resolver import resolve_variant, card_row_to_payload
from services.ai_assistant.assistant import get_assistant_job, run_assistant, start_assistant_job
from services.logic_extractor.adapter import EXTRACTOR_VERSION as LOGIC_EXTRACTOR_VERSION
from services.logic_extractor.adapter import backfill_gap_a_threshold_only

main_bp = Blueprint('main', __name__)

# ── ptcgtw API 常數（用於 TCGDex fallback） ──
PTCGTW_CARD_API = "https://ptcgtw.shop/index_function/api/mysqli_api_2.php"
PTCGTW_API_PARAMS = "?type=%E5%96%AE%E5%8D%A1%E8%B3%87%E6%96%99&lan=0&format=json&variant_id="


def _tcgdex_to_local_card(card: dict, count: int = 1) -> list[dict]:
    """將 TCGDex 卡牌資料轉換為本地 cards 表相容格式。
    回傳 list[dict]（支援 count > 1 時複製多份）。
    """
    import time as _time, random as _random

    # 稀有度對照
    rarity_map = {
        "Common": "C", "Uncommon": "U", "Rare": "R",
        "Double Rare": "RR", "Ultra Rare": "SR",
        "Illustration Rare": "AR", "Special Illustration Rare": "SAR",
        "Hyper Rare": "HR", "Shiny Rare": "S", "Shiny Ultra Rare": "SSR",
        "ACE SPEC Rare": "ACE", "Promo": "PR",
    }

    # 技能轉換：TCGDex attacks → skills_json
    skills = []
    for atk in card.get("attacks", []):
        skills.append({
            "name": atk.get("name", ""),
            "cost": atk.get("cost", []),
            "damage": str(atk.get("damage", "")),
            "effect": atk.get("effect", ""),
            "type": atk.get("type", "attack"),
        })

    base = {
        "card_id": card.get("id", ""),
        "name": card.get("name", ""),
        "card_type": card.get("category", "Pokémon"),
        "sub_type": card.get("stage", ""),
        "hp": card.get("hp", 0),
        "element_type": card["types"][0] if card.get("types") else "",
        "rarity": rarity_map.get(card.get("rarity", ""), card.get("rarity", "")),
        "skills_json": json.dumps(skills, ensure_ascii=False),
        "skills": skills,
        "weakness_type": card["weaknesses"][0]["type"] if card.get("weaknesses") else "",
        "weakness_value": card["weaknesses"][0]["value"] if card.get("weaknesses") else "",
        "resistance_type": card["resistances"][0]["type"] if card.get("resistances") else "",
        "resistance_value": card["resistances"][0]["value"] if card.get("resistances") else "",
        "retreat_cost": card.get("retreat", 0),
        "regulation_mark": card.get("regulationMark", ""),
        "image_url": card.get("image", ""),
        "image_file": "",
        "set_code": card.get("set", {}).get("id", ""),
        "set_number": str(card.get("localId", "")),
        "processing_status": 0,
        "logic": None,
        "source": "tcgdex",
    }

    result = []
    for _ in range(count):
        card_copy = dict(base)
        card_copy["uniqueId"] = f"{int(_time.time() * 1000)}{_random.randint(100000, 999999)}"
        result.append(card_copy)
    return result


def _fetch_ptcgtw_set_info(variant_id: int) -> dict | None:
    """從 ptcgtw API 取得 variant 的 set_name + set_no。"""
    url = f"{PTCGTW_CARD_API}{PTCGTW_API_PARAMS}{variant_id}"
    try:
        resp = requests.get(url, timeout=10, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if resp.status_code == 200:
            data = resp.json()
            if data.get("success") and data.get("data"):
                card = data["data"]
                return {
                    "set_name": (card.get("set_name") or "").strip(),
                    "set_no": (card.get("set_no") or "").strip(),
                }
    except Exception:
        pass
    return None


def _tcgdex_search_cross_lang(query: str, source_lang: str, target_lang: str,
                               limit: int = 10) -> list[dict]:
    """用 TCGDex 做跨語言卡牌搜尋。
    source_lang: 查詢詞的語言 ('ja', 'zh-tw')
    target_lang: 要取得卡牌資料的語言
    回傳本地格式的卡片列表。
    """
    bridge = get_bridge()
    try:
        tcgdex_cards = bridge.search_and_get_target_only(
            query, source_lang, target_lang, limit=limit
        )
        results = []
        for tc in tcgdex_cards:
            converted = _tcgdex_to_local_card(tc, count=1)
            results.extend(converted)
        return results
    except Exception:
        return []


# ==========================================
# [新增] 權限檢查裝飾器
# ==========================================
def admin_required(f):
    """
    裝飾器：檢查使用者是否為 Admin
    未登入或非 Admin 將返回 403 錯誤
    """
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not current_user.is_authenticated:
            return jsonify({'success': False, 'error': '請先登入'}), 401
        if not current_user.is_admin:
            return jsonify({'success': False, 'error': '權限不足，僅限管理員使用'}), 403
        return f(*args, **kwargs)
    return decorated_function


def _request_bool(value, default=False):
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('true', '1', 'yes', 'on')

# ==========================================
# 郵件輔助函數
# ==========================================
def send_verification_email(user_email, token):
    """
    發送驗證信。
    支援 STARTTLS (587) 與 SSL (465)。
    回傳值: (是否成功, 驗證連結)
    - 如果發信成功，回傳 (True, None)
    - 如果發信失敗(開發模式或錯誤)，回傳 (False, 驗證連結)
    """
    try:
        verify_url = url_for('main.verify_email', token=token, _external=True)
        html_content = f"""
        <p>歡迎加入 Chun Deck Builder！</p>
        <p>請點擊以下連結驗證您的帳號：</p>
        <p><a href="{verify_url}">{verify_url}</a></p>
        <br>
        <p>如果連結無法點擊，請複製並貼上到瀏覽器。</p>
        """
        
        # 開發模式判斷：如果沒有設定 SMTP Server 或沒有密碼，視為開發環境
        is_dev_mode = False
        if not config.MAIL_SERVER or (config.MAIL_SERVER == 'smtp.gmail.com' and not config.MAIL_PASSWORD):
            is_dev_mode = True

        if is_dev_mode:
            print(f"============== DEV MODE (No SMTP Configured) ==============")
            print(f"Verify Link for {user_email}: {verify_url}")
            print(f"===========================================================")
            return False, verify_url

        msg = MIMEText(html_content, 'html')
        msg['Subject'] = "Chun Deck Builder - 帳號驗證"
        msg['From'] = config.MAIL_DEFAULT_SENDER
        msg['To'] = user_email

        # 智能判斷連接模式
        if config.MAIL_PORT == 465:
            # 使用 SSL 連線 (SMTP_SSL)
            with smtplib.SMTP_SSL(config.MAIL_SERVER, config.MAIL_PORT, timeout=10) as server:
                if config.MAIL_PASSWORD:
                    server.login(config.MAIL_USERNAME, config.MAIL_PASSWORD)
                server.send_message(msg)
        else:
            # 使用一般連線 + STARTTLS (SMTP)
            with smtplib.SMTP(config.MAIL_SERVER, config.MAIL_PORT, timeout=10) as server:
                if config.MAIL_USE_TLS:
                    server.starttls()
                if config.MAIL_PASSWORD:
                    server.login(config.MAIL_USERNAME, config.MAIL_PASSWORD)
                server.send_message(msg)
                
        print(f"Verification email sent to {user_email}")
        return True, None
        
    except Exception as e:
        print(f"Failed to send email: {e}")
        # 如果發信失敗，回傳連結供測試
        if 'verify_url' in locals():
            print(f"============== DEV MODE (Send Failed) ==============")
            print(f"Verify Link: {verify_url}")
            print(f"====================================================")
            return False, verify_url
        return False, None

# ==========================================
# 輔助函數
# ==========================================
def generate_unique_id():
    chars = string.ascii_uppercase + string.digits
    return ''.join(random.choices(chars, k=6))

def parse_skills(skills_data):
    if not skills_data:
        return []
    if isinstance(skills_data, list):
        return skills_data
    try:
        return json.loads(skills_data)
    except Exception:
        return []

def normalize_set_number(set_number):
    value = str(set_number or "").strip()
    if "/" in value:
        value = value.split("/", 1)[0].strip()
    return value

def set_number_candidates(set_number):
    value = normalize_set_number(set_number)
    if not value:
        return []
    candidates = [value]
    if value.isdigit():
        stripped = str(int(value))
        padded = value.zfill(3)
        for candidate in (stripped, padded):
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates

def official_tw_image_filename(card_id):
    raw = str(card_id or "").strip()
    if raw.isdigit():
        return f"tw{int(raw):08d}.png"
    return ""

def card_image_url_for(card_data, folder_prefix):
    image_file = str(card_data.get('image_file') or "").strip()
    if image_file:
        if image_file.startswith("http://") or image_file.startswith("https://"):
            return image_file
        if folder_prefix == 'images':
            local_path = os.path.join(config.IMAGE_FOLDER, image_file)
            if os.path.exists(local_path):
                return f"/images/{image_file}"
            return f"https://asia.pokemon-card.com/tw/card-img/{image_file}"
        return f"/{folder_prefix}/{image_file}"

    if folder_prefix == 'images':
        filename = official_tw_image_filename(card_data.get('card_id') or card_data.get('id'))
        if filename:
            return f"https://asia.pokemon-card.com/tw/card-img/{filename}"
    return ""

def card_payload_from_row(row, folder_prefix):
    card_data = dict(row)
    if 'skills_json' in card_data:
        card_data['skills'] = parse_skills(card_data['skills_json'])
    card_data['image_url'] = card_image_url_for(card_data, folder_prefix)
    return card_data


def slim_card_payload_from_row(row, folder_prefix, language='tw'):
    card_data = dict(row)
    payload = {
        'card_id': card_data.get('card_id'),
        'id': card_data.get('card_id'),
        'name': card_data.get('name'),
        'card_type': card_data.get('card_type'),
        'sub_type': card_data.get('sub_type'),
        'hp': card_data.get('hp'),
        'element_type': card_data.get('element_type'),
        'set_code': card_data.get('set_code'),
        'set_number': card_data.get('set_number'),
        'set_name': card_data.get('set_name'),
        'regulation_mark': card_data.get('regulation_mark'),
        'rarity': card_data.get('rarity'),
        'image_file': card_data.get('image_file'),
        'image_url': card_image_url_for(card_data, folder_prefix),
        'language': language,
    }
    if card_data.get('japanese_name'):
        payload['japanese_name'] = card_data.get('japanese_name')
    return payload


def batch_logic_payloads(cursor, card_rows_or_ids):
    lookup_ids = []
    seen = set()
    for item in card_rows_or_ids:
        raw_id = item.get('card_id') if isinstance(item, dict) else item
        cid = str(raw_id or "").strip()
        if not cid:
            continue
        for candidate in (cid, cid.rsplit('.', 1)[0] if '.' in cid else ''):
            if candidate and candidate not in seen:
                seen.add(candidate)
                lookup_ids.append(candidate)
    if not lookup_ids:
        return {}
    cursor.execute(
        """
        SELECT column_name
        FROM information_schema.columns
        WHERE table_schema = 'public'
          AND table_name = 'processed_cards'
          AND column_name IN (
              'logic_json', 'predicates', 'extractor_version', 'source_language',
              'source_card_id', 'source_text_hash', 'validation_errors'
          )
        """
    )
    columns = {row['column_name'] for row in cursor.fetchall()}
    select_fields = [("logic_json", "pc.logic_json" if "logic_json" in columns else "NULL::text")]
    for column in (
        "predicates",
        "extractor_version",
        "source_language",
        "source_card_id",
        "source_text_hash",
        "validation_errors",
    ):
        if column in columns:
            select_fields.append((column, f"pc.{column}"))
        elif column in ("predicates", "validation_errors"):
            select_fields.append((column, "'[]'::jsonb"))
        else:
            select_fields.append((column, "NULL::text"))
    cte_select = ", ".join(f"{expr} AS {name}" for name, expr in select_fields)
    final_select = ", ".join(name for name, _ in select_fields)
    cursor.execute(
        f"""
        WITH candidate_logic AS (
            SELECT pc.card_id AS lookup_id, pc.card_id AS processed_card_id, 0 AS priority, {cte_select}
            FROM processed_cards pc
            WHERE pc.card_id = ANY(%s)
            UNION ALL
            SELECT c.card_id AS lookup_id, pc.card_id AS processed_card_id, 1 AS priority, {cte_select}
            FROM cards c
            JOIN jp_cards j
              ON c.set_code = j.set_code
             AND split_part(c.set_number, '/', 1) ~ '^[0-9]+$'
             AND split_part(j.set_number, '/', 1) ~ '^[0-9]+$'
             AND split_part(c.set_number, '/', 1)::int = split_part(j.set_number, '/', 1)::int
            JOIN processed_cards pc ON pc.card_id = j.card_id
            WHERE c.card_id = ANY(%s)
        )
        SELECT lookup_id, processed_card_id, {final_select}
        FROM candidate_logic
        ORDER BY priority, lookup_id, processed_card_id
        """,
        (lookup_ids, lookup_ids),
    )
    logic_by_id = {}
    for row in cursor.fetchall():
        lookup_id = str(row.get('lookup_id') or row.get('processed_card_id') or '')
        if not lookup_id or lookup_id in logic_by_id:
            continue
        predicates = row.get('predicates') or []
        if isinstance(predicates, str):
            try:
                predicates = json.loads(predicates)
            except Exception:
                predicates = []
        if predicates:
            logic_by_id[lookup_id] = {
                "version": row.get('extractor_version'),
                "scope": row.get('extractor_version'),
                "source_language": row.get('source_language'),
                "source_card_id": row.get('source_card_id') or row.get('processed_card_id'),
                "source_text_hash": row.get('source_text_hash'),
                "predicates": predicates,
                "validation_errors": row.get('validation_errors') or [],
            }
        elif row.get('logic_json'):
            try:
                logic_by_id[lookup_id] = json.loads(row['logic_json'])
            except Exception:
                pass
    return logic_by_id


def tcgdex_image_url(image_value):
    image_value = str(image_value or "").strip()
    if not image_value:
        return ""
    if image_value.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
        return image_value
    return f"{image_value.rstrip('/')}/high.webp"

def tcgdex_card_to_jp_row(card):
    set_info = card.get('set') or {}
    skills = []
    for ability in card.get('abilities') or []:
        effect = ability.get('effect', '') or ''
        skills.append({
            'type': 'ability',
            'name': ability.get('name', '') or '',
            'effect': effect,
            'text': effect,
            'cost': [],
            'damage': '',
        })
    for attack in card.get('attacks') or []:
        effect = attack.get('effect', '') or ''
        damage = attack.get('damage', '')
        skills.append({
            'type': 'attack',
            'name': attack.get('name', '') or '',
            'effect': effect,
            'text': effect,
            'cost': attack.get('cost') or [],
            'damage': '' if damage is None else str(damage),
        })

    category = card.get('category') or 'Pokémon'
    if category == 'Pokemon':
        category = 'Pokémon'
    sub_type = card.get('trainerType') or card.get('energyType') or card.get('stage') or ''
    legal = card.get('legal') or {}
    regulation_flags = []
    if legal.get('standard'):
        regulation_flags.append('Standard')
    if legal.get('expanded'):
        regulation_flags.append('Expanded')

    weaknesses = card.get('weaknesses') or []
    resistances = card.get('resistances') or []
    types = card.get('types') or []
    dex_ids = card.get('dexId') or []
    description = card.get('description') or card.get('effect') or ''

    return {
        'card_id': f"jp{card.get('id', '')}",
        'image_file': tcgdex_image_url(card.get('image', '')),
        'card_type': category,
        'name': card.get('name', '') or '',
        'sub_type': sub_type,
        'hp': int(card.get('hp') or 0),
        'element_type': types[0] if types else '',
        'weakness_type': weaknesses[0].get('type', '') if weaknesses else '',
        'weakness_value': weaknesses[0].get('value', '') if weaknesses else '',
        'resistance_type': resistances[0].get('type', '') if resistances else '',
        'resistance_value': resistances[0].get('value', '') if resistances else '',
        'retreat_cost': int(card.get('retreat') or 0),
        'skills_json': json.dumps(skills, ensure_ascii=False),
        'skills': skills,
        'rarity': card.get('rarity', '') or '',
        'processing_status': 0,
        'chinese_name': None,
        'evolution_stage': sub_type,
        'evolves_from': card.get('evolveFrom', '') or '',
        'set_code': set_info.get('id', '') or '',
        'set_number': str(card.get('localId', '') or ''),
        'set_name': set_info.get('name', '') or '',
        'regulation_flags': ','.join(regulation_flags),
        'regulation_mark': card.get('regulationMark', '') or '',
        'description': description,
        'flavor_text': card.get('description', '') or '',
        'pokedex_number': str(dex_ids[0]) if dex_ids else '',
        'pokedex_category': '',
        'height': '',
        'weight': '',
    }

def save_tcgdex_jp_card(card):
    row = tcgdex_card_to_jp_row(card)
    if not row['card_id'] or row['card_id'] == 'jp':
        return None

    conn = database.get_db_connection()
    if not conn:
        return row
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT chinese_name FROM jp_cards WHERE card_id = %s", (row['card_id'],))
        existing = cursor.fetchone()
        row['chinese_name'] = existing['chinese_name'] if existing else None
        cursor.execute(
            """
            INSERT INTO jp_cards (
                card_id, image_file, card_type, name, sub_type,
                hp, element_type, weakness_type, weakness_value,
                resistance_type, resistance_value, retreat_cost,
                skills_json, rarity, processing_status,
                chinese_name, evolution_stage, evolves_from,
                set_code, set_number, set_name,
                regulation_flags, regulation_mark, description,
                flavor_text, pokedex_number, pokedex_category, height, weight
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
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
                set_name = EXCLUDED.set_name,
                regulation_flags = EXCLUDED.regulation_flags,
                regulation_mark = EXCLUDED.regulation_mark,
                description = EXCLUDED.description,
                flavor_text = EXCLUDED.flavor_text,
                pokedex_number = EXCLUDED.pokedex_number,
                pokedex_category = EXCLUDED.pokedex_category,
                height = EXCLUDED.height,
                weight = EXCLUDED.weight
            """,
            (
                row['card_id'], row['image_file'], row['card_type'], row['name'], row['sub_type'],
                row['hp'], row['element_type'], row['weakness_type'], row['weakness_value'],
                row['resistance_type'], row['resistance_value'], row['retreat_cost'],
                row['skills_json'], row['rarity'], row['processing_status'],
                row['chinese_name'], row['evolution_stage'], row['evolves_from'],
                row['set_code'], row['set_number'], row['set_name'],
                row['regulation_flags'], row['regulation_mark'], row['description'],
                row['flavor_text'], row['pokedex_number'], row['pokedex_category'],
                row['height'], row['weight']
            )
        )
        conn.commit()
    except Exception as exc:
        conn.rollback()
        print(f"TCGdex JP save failed for {row.get('card_id')}: {exc}")
    finally:
        conn.close()
    return row

def fetch_tcgdex_card_by_set(lang, set_code, set_number):
    client = get_tcgdex_client()
    clean_set = str(set_code or "").strip()
    if not clean_set:
        return None
    for candidate in set_number_candidates(set_number):
        url = f"{TCGDEX_API_BASE}/{lang}/sets/{clean_set}/{candidate}"
        try:
            data = client._cached_get(url)
        except Exception:
            data = None
        if data:
            return data
    return None

def fetch_variant_by_set(cursor, table_name, set_code, set_number):
    if table_name not in ('cards', 'jp_cards'):
        return None
    candidates = set_number_candidates(set_number)
    if not set_code or not candidates:
        return None
    placeholders = ','.join(['%s'] * len(candidates))
    cursor.execute(
        f"""
        SELECT card_id, name, image_file, set_code, set_number
        FROM {table_name}
        WHERE set_code = %s
          AND (
              set_number = %s
              OR set_number IN ({placeholders})
              OR split_part(COALESCE(set_number, ''), '/', 1) IN ({placeholders})
          )
        ORDER BY CASE WHEN set_number = %s THEN 0 ELSE 1 END,
                 length(COALESCE(set_number, '')) DESC
        LIMIT 1
        """,
        [set_code, str(set_number or ""), *candidates, *candidates, candidates[0]]
    )
    return cursor.fetchone()

# ==========================================
# 靜態檔案與頁面路由
# ==========================================

@main_bp.route('/static/css/<path:filename>')
def serve_css(filename): return send_from_directory(config.CSS_DIR, filename)

@main_bp.route('/static/js/<path:filename>')
def serve_js(filename): return send_from_directory(config.JS_DIR, filename)

@main_bp.route('/images/<path:filename>')
def serve_image(filename): return send_from_directory(config.IMAGE_FOLDER, filename)

@main_bp.route('/images_jp/<path:filename>')
def serve_jp_image(filename): return send_from_directory(config.JP_IMAGE_FOLDER, filename)

@main_bp.route('/favicon.ico')
def favicon(): return send_from_directory(config.PUBLIC_DIR, 'favicon.ico')

@main_bp.route('/')
def index(): return render_template('index.html')

@main_bp.route('/card/<deck_id>')
def view_deck(deck_id): return render_template('index.html')

@main_bp.route('/battle')
def battle(): return render_template('battle.html')

# ==========================================
# Auth API 接口
# ==========================================

@main_bp.route('/api/auth/register', methods=['POST'])
def register():
    try:
        if not request.is_json:
            return jsonify({'success': False, 'error': 'Invalid Content-Type, expected application/json'}), 400

        data = request.json
        username = data.get('username')
        email = data.get('email')
        password = data.get('password')
        
        if not username or not email or not password:
            return jsonify({'success': False, 'error': '請填寫完整資訊 (帳號、Email、密碼)'}), 400
            
        # 嘗試建立使用者
        user = User.create(username, password, email)
        
        # User.create 回傳 None 表示使用者已存在或其他資料庫錯誤
        if not user:
            return jsonify({'success': False, 'error': '使用者名稱或 Email 已存在，或資料庫錯誤'}), 400
        
        # 發送驗證信
        token = user.get_verification_token()
        success, verify_link = send_verification_email(email, token)
        
        msg = '註冊成功！'
        response_data = {'success': True}

        if success:
            msg += ' 請至信箱收取驗證信以啟用帳號。'
        else:
            if verify_link:
                # 直接回傳驗證連結給前端
                msg += ' (開發模式/發信失敗) 請使用下方連結進行驗證。'
                response_data['verify_link'] = verify_link
            else:
                msg += ' 驗證信發送失敗，請聯繫管理員。'

        response_data['message'] = msg
        return jsonify(response_data)
        
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'伺服器內部錯誤: {str(e)}'}), 500

@main_bp.route('/verify/<token>')
def verify_email(token):
    try:
        email = User.verify_token(token)
        if not email:
            return "<h1>驗證連結無效或已過期</h1>", 400
        
        if User.verify_user(email):
            return "<h1>驗證成功！您現在可以關閉此頁面並登入了。</h1>"
        else:
            return "<h1>驗證失敗，請稍後再試。</h1>", 500
    except Exception as e:
        print(f"Verify Error: {e}")
        return "<h1>驗證過程發生錯誤</h1>", 500

@main_bp.route('/api/auth/login', methods=['POST'])
def login():
    try:
        data = request.json
        identifier = data.get('username') 
        password = data.get('password')
        
        if not identifier or not password:
            return jsonify({'success': False, 'error': '請輸入帳號/Email與密碼'}), 400

        user = User.find_by_username_or_email(identifier)
        
        if user and user.verify_password(password):
            if not user.is_verified:
                return jsonify({'success': False, 'error': '帳號尚未驗證，請檢查您的 Email。'}), 401
                
            login_user(user)
            return jsonify({
                'success': True, 
                'user': {
                    'id': user.id, 
                    'username': user.username, 
                    'role': user.role,
                    'is_admin': user.is_admin  # [新增] 回傳 is_admin
                }
            })
        
        return jsonify({'success': False, 'error': '帳號或密碼錯誤'}), 401
    except Exception as e:
        print(f"Login Error: {e}")
        return jsonify({'success': False, 'error': '登入失敗，請稍後再試'}), 500

@main_bp.route('/api/auth/logout', methods=['POST'])
@login_required
def logout():
    logout_user()
    return jsonify({'success': True})

@main_bp.route('/api/auth/user')
def get_current_user():
    if current_user.is_authenticated:
        return jsonify({
            'is_authenticated': True,
            'user': {
                'id': current_user.id,
                'username': current_user.username,
                'email': getattr(current_user, 'email', ''),
                'role': current_user.role,
                'is_admin': current_user.is_admin
            }
        })
    return jsonify({'is_authenticated': False})

# ==========================================
# 忘記密碼 API
# ==========================================

@main_bp.route('/api/auth/forgot-password', methods=['POST'])
def forgot_password():
    """發送密碼重設郵件"""
    try:
        data = request.json
        email = data.get('email', '').strip()

        if not email:
            return jsonify({'success': False, 'error': '請輸入 Email'}), 400

        user = User.find_by_email(email)
        if not user:
            # 不揭露用戶是否存在，統一回傳成功訊息
            return jsonify({'success': True, 'message': '如果此 Email 已註冊，您將會收到重設密碼的郵件。'})

        token = user.get_password_reset_token()
        success, verify_link = send_password_reset_email(email, token)

        if success:
            return jsonify({'success': True, 'message': '重設密碼郵件已發送，請檢查您的信箱。'})
        elif verify_link:
            return jsonify({
                'success': True,
                'message': '(開發模式) 請使用下方連結重設密碼。',
                'verify_link': verify_link
            })
        else:
            return jsonify({'success': False, 'error': '郵件發送失敗，請稍後再試。'}), 500

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'伺服器錯誤: {str(e)}'}), 500


def send_password_reset_email(user_email, token):
    """發送密碼重設郵件 (與驗證信邏輯相同)"""
    try:
        reset_url = url_for('main.reset_password_page', token=token, _external=True)
        html_content = f"""
        <p>我們收到了您的密碼重設請求。</p>
        <p>請點擊以下連結重設密碼（30 分鐘內有效）：</p>
        <p><a href="{reset_url}">{reset_url}</a></p>
        <br>
        <p>如果這不是您本人的操作，請忽略此郵件。</p>
        """

        is_dev_mode = not config.MAIL_SERVER or (config.MAIL_SERVER == 'smtp.gmail.com' and not config.MAIL_PASSWORD)

        if is_dev_mode:
            print(f"============== DEV MODE (Password Reset) ==============")
            print(f"Reset Link for {user_email}: {reset_url}")
            print(f"========================================================")
            return False, reset_url

        msg = MIMEText(html_content, 'html')
        msg['Subject'] = "Chun Deck Builder - 密碼重設"
        msg['From'] = config.MAIL_DEFAULT_SENDER
        msg['To'] = user_email

        if config.MAIL_PORT == 465:
            with smtplib.SMTP_SSL(config.MAIL_SERVER, config.MAIL_PORT, timeout=10) as server:
                if config.MAIL_PASSWORD:
                    server.login(config.MAIL_USERNAME, config.MAIL_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(config.MAIL_SERVER, config.MAIL_PORT, timeout=10) as server:
                if config.MAIL_USE_TLS:
                    server.starttls()
                if config.MAIL_PASSWORD:
                    server.login(config.MAIL_USERNAME, config.MAIL_PASSWORD)
                server.send_message(msg)

        print(f"Password reset email sent to {user_email}")
        return True, None

    except Exception as e:
        print(f"Failed to send password reset email: {e}")
        if 'reset_url' in locals():
            print(f"============== DEV MODE (Reset Send Failed) ==============")
            print(f"Reset Link: {reset_url}")
            print(f"==========================================================")
            return False, reset_url
        return False, None


@main_bp.route('/reset-password/<token>')
def reset_password_page(token):
    """密碼重設頁面"""
    email = User.verify_password_reset_token(token)
    if not email:
        return "<h1>重設連結無效或已過期（30 分鐘內有效）</h1>", 400
    # 回傳帶有 token 的頁面，前端會自動處理
    return render_template('index.html')


@main_bp.route('/api/auth/reset-password', methods=['POST'])
def reset_password():
    """執行密碼重設"""
    try:
        data = request.json
        token = data.get('token', '')
        new_password = data.get('password', '')

        if not token or not new_password:
            return jsonify({'success': False, 'error': '缺少 token 或新密碼'}), 400

        if len(new_password) < 6:
            return jsonify({'success': False, 'error': '密碼長度至少需要 6 個字元'}), 400

        email = User.verify_password_reset_token(token)
        if not email:
            return jsonify({'success': False, 'error': '重設連結無效或已過期'}), 400

        if User.reset_password(email, new_password):
            return jsonify({'success': True, 'message': '密碼重設成功！請使用新密碼登入。'})
        else:
            return jsonify({'success': False, 'error': '密碼重設失敗，請稍後再試。'}), 500

    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': f'伺服器錯誤: {str(e)}'}), 500

# ==========================================
# API 接口 (保持不變)
# ==========================================

@main_bp.route('/api/ai/chat', methods=['POST'])
def ai_chat():
    try:
        data = request.get_json(silent=True) or {}
        messages = data.get('messages') or []
        context = data.get('context') or {}
        if not isinstance(messages, list) or not messages:
            return jsonify({
                'success': False,
                'error': 'Missing messages',
                'answer': '',
                'cards': [],
                'meta_references': [],
                'deck_actions': [],
                'deck_diff': {},
                'tool_trace': [],
                'tool_results': [],
            }), 400
            return jsonify({'success': False, 'error': '缺少 messages', 'answer': '', 'tool_results': [], 'cards': []}), 400
        result = run_assistant(messages, context)
        status = 200 if result.get('success') else 400
        error = result.get('error') or ''
        if error.startswith('AI provider returned') or error.startswith('AI request failed'):
            status = 502
        return jsonify(result), status
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({
            'success': False,
            'error': str(e),
            'answer': '',
            'cards': [],
            'meta_references': [],
            'deck_actions': [],
            'deck_diff': {},
            'tool_trace': [],
            'tool_results': [],
        }), 500
        return jsonify({'success': False, 'error': str(e), 'answer': '', 'tool_results': [], 'cards': []}), 500


@main_bp.route('/api/ai/chat/jobs', methods=['POST'])
def ai_chat_job_start():
    try:
        data = request.get_json(silent=True) or {}
        messages = data.get('messages') or []
        context = data.get('context') or {}
        if not isinstance(messages, list) or not messages:
            return jsonify({
                'success': False,
                'error': 'Missing messages',
                'job_id': None,
                'status': 'failed',
            }), 400
        result = start_assistant_job(messages, context if isinstance(context, dict) else {})
        return jsonify(result), 202
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e), 'job_id': None, 'status': 'failed'}), 500


@main_bp.route('/api/ai/chat/jobs/<job_id>', methods=['GET'])
def ai_chat_job_status(job_id):
    result = get_assistant_job(job_id)
    status = 200 if result.get('success') else 404
    return jsonify(result), status

@main_bp.route('/api/ai/embeddings/status', methods=['GET'])
@admin_required
def ai_embeddings_status():
    from services.ai_assistant.indexer import embedding_status

    result = embedding_status()
    return jsonify(result), 200 if result.get('success') else 500


@main_bp.route('/api/ai/embeddings/rebuild', methods=['POST'])
@admin_required
def ai_embeddings_rebuild():
    from services.ai_assistant.indexer import start_rebuild_embeddings

    data = request.get_json(silent=True) or {}
    success, status = start_rebuild_embeddings(
        source_type=data.get('source_type') or 'all',
        batch_size=int(data.get('batch_size') or 64),
        max_items=data.get('max_items'),
    )
    return jsonify({'success': success, 'status': status}), 202 if success else 409


@main_bp.route('/api/search')
def search_cards():
    query_raw = request.args.get('q', '').strip()
    full_payload = request.args.get('full') in ('1', 'true', 'yes')
    # [新增] 過濾參數
    filter_type = request.args.get('type', '') # Pokémon, Trainer, Energy, Item, Tool, Supporter, Stadium
    filter_element = request.args.get('element', '') # Fire, Water...
    filter_stage = request.args.get('stage', '') # Basic, Stage 1, Stage 2

    # 如果沒有搜尋詞也沒有過濾條件，回傳空
    if not query_raw and not filter_type and not filter_element and not filter_stage: 
        return jsonify([])

    conn = database.get_db_connection()
    if not conn: return jsonify([])
    cursor = conn.cursor()

    # 基礎 SQL。預設搜尋只回列表需要欄位，詳情由 batch/detail API 補取。
    if full_payload:
        sql = "SELECT * FROM cards WHERE 1=1"
    else:
        sql = (
            "SELECT card_id, image_file, card_type, name, sub_type, hp, element_type, "
            "rarity, japanese_name, set_code, set_number, set_name, regulation_mark "
            "FROM cards WHERE 1=1"
        )
    params = []

    # 跨語言搜尋：含日文假名時也查 jp_cards，用 set_code+set_number 映射回 cards
    tw_extra_conditions = ""
    tw_extra_params = []
    has_kana = False

    # 1. 關鍵字搜尋
    if query_raw:
        has_kana = any(('\u3040' <= ch <= '\u309f') or ('\u30a0' <= ch <= '\u30ff') for ch in query_raw)
        if has_kana:
            try:
                conn2 = database.get_db_connection()
                if conn2:
                    c2 = conn2.cursor()
                    jp_search = '%' + query_raw + '%'
                    c2.execute(
                        """SELECT set_code, set_number FROM jp_cards
                           WHERE name LIKE %s AND set_code IS NOT NULL AND set_number IS NOT NULL
                           LIMIT 30""",
                        (jp_search,)
                    )
                    pairs = [(r['set_code'], r['set_number']) for r in c2.fetchall()]
                    conn2.close()
                    if pairs:
                        or_clauses = []
                        for sc, sn in pairs:
                            candidates = set_number_candidates(sn)
                            if not candidates:
                                continue
                            ph = ','.join(['%s'] * len(candidates))
                            or_clauses.append(
                                f"(set_code = %s AND (set_number = %s OR set_number IN ({ph}) OR split_part(COALESCE(set_number, ''), '/', 1) IN ({ph})))"
                            )
                            tw_extra_params.extend([sc, str(sn or ''), *candidates, *candidates])
                        tw_extra_conditions = " OR " + " OR ".join(or_clauses)
            except Exception:
                pass

        sql += " AND (name LIKE %s OR REPLACE(REPLACE(name, '<', ''), '>', '') LIKE %s OR image_file LIKE %s OR card_id LIKE %s"
        search_param = '%' + query_raw + '%'
        params.extend([search_param, search_param, search_param, search_param])
        sql += tw_extra_conditions
        params.extend(tw_extra_params)
        sql += ")"

    # 2. 類型過濾 (處理 Trainer 的細分類)
    if filter_type:
        if filter_type in ['Item', 'Pokémon Tool', 'Supporter', 'Stadium']:
            sql += " AND card_type = 'Trainer' AND sub_type = %s"
            params.append(filter_type)
        else:
            # Pokémon, Trainer, Energy
            sql += " AND card_type = %s"
            params.append(filter_type)

    # 3. 屬性過濾
    if filter_element:
        sql += " AND element_type = %s"
        params.append(filter_element)

    # 4. 進化階段過濾
    if filter_stage:
        sql += " AND sub_type LIKE %s"
        params.append(f'%{filter_stage}%')

    # 5. 標準/開放賽季過濾
    filter_regulation = request.args.get('regulation', '').strip()
    if filter_regulation in ('standard', 'expanded'):
        conn2 = database.get_db_connection()
        if conn2:
            try:
                c2 = conn2.cursor()
                c2.execute("SELECT mark FROM regulation_settings WHERE is_standard = TRUE")
                standard_marks = [r['mark'] for r in c2.fetchall()]
                conn2.close()
            except:
                conn2.close()
                standard_marks = ['F', 'G', 'H', 'I', 'J']

            if standard_marks:
                placeholders = ','.join(['%s'] * len(standard_marks))
                if filter_regulation == 'standard':
                    sql += f" AND regulation_mark IN ({placeholders})"
                else:  # expanded
                    sql += f" AND (regulation_mark NOT IN ({placeholders}) OR regulation_mark IS NULL OR regulation_mark = '')"
                params.extend(standard_marks)

    sql += " LIMIT 50"

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        if full_payload:
            card_data = card_payload_from_row(row, 'images')
            card_data['language'] = 'tw'
        else:
            card_data = slim_card_payload_from_row(row, 'images', 'tw')
        results.append(card_data)

    # ── TCGDex fallback：本地搜尋結果太少，嘗試用 TCGDex 跨語言搜尋 ──
    if query_raw and len(results) < 3 and has_kana:
        tcgdex_cards = _tcgdex_search_cross_lang(query_raw, 'ja', 'zh-tw')
        # 合併結果（去重：以 card_id 判斷）
        existing_ids = {c.get('card_id', '') for c in results}
        for tc in tcgdex_cards:
            if tc.get('card_id', '') not in existing_ids:
                results.append(tc)
                existing_ids.add(tc.get('card_id', ''))

    return jsonify(results)

@main_bp.route('/api/cards/batch', methods=['POST'])
def get_cards_batch():
    data = request.json
    if not data or 'ids' not in data: return jsonify({'error': 'No ids provided'}), 400
    conn = database.get_db_connection()
    if not conn: return jsonify({'error': 'DB Connection Failed'}), 500
    cursor = conn.cursor()
    results = []
    try:
        ids = data['ids']
        unique_ids = list(set(ids))
        if not unique_ids: return jsonify([])
        placeholders = ','.join(['%s'] * len(unique_ids))
        try:
            sql = f"SELECT * FROM cards WHERE card_id IN ({placeholders})"
            cursor.execute(sql, unique_ids)
            rows = cursor.fetchall()
        except: rows = []
        if not rows:
             try:
                sql = f"SELECT * FROM cards WHERE image_file IN ({placeholders})"
                cursor.execute(sql, unique_ids)
                rows = cursor.fetchall()
             except: pass
        logic_by_id = batch_logic_payloads(cursor, rows)
        for row in rows:
            card_data = card_payload_from_row(row, 'images')
            c_id = card_data.get('card_id') or card_data.get('id')
            c_id_str = str(c_id or "")
            card_data['logic'] = logic_by_id.get(c_id_str) or logic_by_id.get(c_id_str.rsplit('.', 1)[0] if '.' in c_id_str else '')
            results.append(card_data)
    except Exception as e: return jsonify({'error': str(e)}), 500
    finally: conn.close()
    return jsonify(results)

# ==========================================
# 日本卡牌 API
# ==========================================

@main_bp.route('/api/jp/search')
def search_jp_cards():
    """搜尋日本卡牌 (jp_cards 表)"""
    query_raw = request.args.get('q', '').strip()
    full_payload = request.args.get('full') in ('1', 'true', 'yes')
    filter_type = request.args.get('type', '')
    filter_element = request.args.get('element', '')
    filter_stage = request.args.get('stage', '')

    if not query_raw and not filter_type and not filter_element and not filter_stage:
        return jsonify([])

    conn = database.get_db_connection()
    if not conn: return jsonify([])
    cursor = conn.cursor()

    if full_payload:
        sql = "SELECT * FROM jp_cards WHERE 1=1"
    else:
        sql = (
            "SELECT card_id, image_file, card_type, name, sub_type, hp, element_type, "
            "rarity, chinese_name, set_code, set_number, set_name, regulation_mark "
            "FROM jp_cards WHERE 1=1"
        )
    params = []

    # 跨語言搜尋：含中文時也搜 cards 表，用 set_code+set_number 映射回 jp_cards
    jp_extra_conditions = ""
    jp_extra_params = []
    has_cjk = False

    if query_raw:
        has_cjk = any('\u4e00' <= ch <= '\u9fff' or '\u3400' <= ch <= '\u4dbf' for ch in query_raw)
        if has_cjk:
            try:
                conn2 = database.get_db_connection()
                if conn2:
                    c2 = conn2.cursor()
                    tw_search = '%' + query_raw + '%'
                    c2.execute(
                        """SELECT set_code, set_number FROM cards
                           WHERE name LIKE %s AND set_code IS NOT NULL AND set_number IS NOT NULL
                           LIMIT 30""",
                        (tw_search,)
                    )
                    pairs = [(r['set_code'], r['set_number']) for r in c2.fetchall()]
                    conn2.close()
                    if pairs:
                        or_clauses = []
                        for sc, sn in pairs:
                            candidates = set_number_candidates(sn)
                            if not candidates:
                                continue
                            ph = ','.join(['%s'] * len(candidates))
                            or_clauses.append(
                                f"(set_code = %s AND (set_number = %s OR set_number IN ({ph}) OR split_part(COALESCE(set_number, ''), '/', 1) IN ({ph})))"
                            )
                            jp_extra_params.extend([sc, str(sn or ''), *candidates, *candidates])
                        jp_extra_conditions = " OR " + " OR ".join(or_clauses)
            except Exception:
                pass

        sql += " AND (name LIKE %s OR image_file LIKE %s OR card_id LIKE %s"
        search_param = '%' + query_raw + '%'
        params.extend([search_param, search_param, search_param])
        sql += jp_extra_conditions
        params.extend(jp_extra_params)
        sql += ")"

    if filter_type:
        if filter_type in ['Item', 'Pokémon Tool', 'Supporter', 'Stadium']:
            sql += " AND card_type = 'Trainer' AND sub_type = %s"
            params.append(filter_type)
        else:
            sql += " AND card_type = %s"
            params.append(filter_type)

    if filter_element:
        sql += " AND element_type = %s"
        params.append(filter_element)

    if filter_stage:
        sql += " AND sub_type LIKE %s"
        params.append(f'%{filter_stage}%')

    filter_regulation = request.args.get('regulation', '').strip()
    if filter_regulation in ('standard', 'expanded'):
        conn2 = database.get_db_connection()
        if conn2:
            try:
                c2 = conn2.cursor()
                c2.execute("SELECT mark FROM regulation_settings WHERE is_standard = TRUE")
                standard_marks = [r['mark'] for r in c2.fetchall()]
                conn2.close()
            except:
                conn2.close()
                standard_marks = ['F', 'G', 'H', 'I', 'J']

            if standard_marks:
                placeholders = ','.join(['%s'] * len(standard_marks))
                if filter_regulation == 'standard':
                    sql += f" AND regulation_mark IN ({placeholders})"
                else:
                    sql += f" AND (regulation_mark NOT IN ({placeholders}) OR regulation_mark IS NULL OR regulation_mark = '')"
                params.extend(standard_marks)

    sql += " LIMIT 50"

    cursor.execute(sql, params)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for row in rows:
        if full_payload:
            card_data = card_payload_from_row(row, 'images_jp')
            card_data['language'] = 'jp'
        else:
            card_data = slim_card_payload_from_row(row, 'images_jp', 'jp')
        results.append(card_data)

    # ── TCGDex fallback：本地搜尋結果太少，嘗試用 TCGDex 跨語言搜尋 ──
    if query_raw and len(results) < 3 and has_cjk:
        tcgdex_cards = _tcgdex_search_cross_lang(query_raw, 'zh-tw', 'ja')
        existing_ids = {c.get('card_id', '') for c in results}
        for tc in tcgdex_cards:
            if tc.get('card_id', '') not in existing_ids:
                tc['language'] = 'jp'
                results.append(tc)
                existing_ids.add(tc.get('card_id', ''))

    return jsonify(results)


@main_bp.route('/api/jp/cards/batch', methods=['POST'])
def get_jp_cards_batch():
    """批次查詢日本卡牌"""
    data = request.json
    if not data or 'ids' not in data:
        return jsonify({'error': 'No ids provided'}), 400
    conn = database.get_db_connection()
    if not conn:
        return jsonify({'error': 'DB Connection Failed'}), 500
    cursor = conn.cursor()
    results = []
    try:
        ids = data['ids']
        unique_ids = list(set(ids))
        if not unique_ids:
            return jsonify([])
        placeholders = ','.join(['%s'] * len(unique_ids))
        try:
            sql = f"SELECT * FROM jp_cards WHERE card_id IN ({placeholders})"
            cursor.execute(sql, unique_ids)
            rows = cursor.fetchall()
        except:
            rows = []
        if not rows:
            try:
                sql = f"SELECT * FROM jp_cards WHERE image_file IN ({placeholders})"
                cursor.execute(sql, unique_ids)
                rows = cursor.fetchall()
            except:
                pass
        for row in rows:
            card_data = card_payload_from_row(row, 'images_jp')
            card_data['language'] = 'jp'
            results.append(card_data)
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        conn.close()
    return jsonify(results)


@main_bp.route('/api/card/variants/<card_id>')
def get_card_variants(card_id):
    """
    查詢一張卡牌的跨語言版本。
    透過 set_code + set_number 匹配對應的 tw / jp 卡牌。
    回傳: { tw: {card_id, name, image_url}, jp: {card_id, name, image_url} }
    """
    conn = database.get_db_connection()
    if not conn:
        return jsonify({'error': 'DB Connection Failed'}), 500

    cursor = conn.cursor()
    result = {'tw': None, 'jp': None}

    try:
        # 判斷來源：從哪張表查
        source_table = 'jp_cards' if card_id.startswith('jp') else 'cards'

        # 取得來源卡片的 set_code + set_number
        cursor.execute(
            f"SELECT card_id, name, image_file, set_code, set_number FROM {source_table} WHERE card_id = %s",
            (card_id,)
        )
        source_row = cursor.fetchone()
        if not source_row:
            conn.close()
            return jsonify(result)

        sc = source_row['set_code']
        sn = source_row['set_number']

        # 填入來源語言
        src_lang = 'jp' if source_table == 'jp_cards' else 'tw'
        img_prefix = 'images_jp' if src_lang == 'jp' else 'images'
        result[src_lang] = {
            'card_id': source_row['card_id'],
            'name': source_row['name'],
            'image_url': card_image_url_for(source_row, img_prefix)
        }

        # 查詢另一語言的版本
        other_lang = 'tw' if source_table == 'jp_cards' else 'jp'
        other_img_prefix = 'images' if other_lang == 'tw' else 'images_jp'

        if sc and sn:
            other_table = 'cards' if source_table == 'jp_cards' else 'jp_cards'

            other_row = fetch_variant_by_set(cursor, other_table, sc, sn)
            if other_row:
                result[other_lang] = {
                    'card_id': other_row['card_id'],
                    'name': other_row['name'],
                    'image_url': card_image_url_for(other_row, other_img_prefix)
                }

            # ── TCGDex fallback：本地 DB 找不到時嘗試 ──
            if not result[other_lang]:
                tcgdex_card = None
                if source_table == 'jp_cards':
                    tcgdex_card = find_chinese_card(sc, sn)
                else:
                    tcgdex_card = find_japanese_card(sc, sn)
                    if not tcgdex_card:
                        tcgdex_card = fetch_tcgdex_card_by_set('ja', sc, sn)
                if tcgdex_card:
                    if other_lang == 'jp':
                        saved_row = save_tcgdex_jp_card(tcgdex_card)
                        if saved_row:
                            result[other_lang] = {
                                'card_id': saved_row.get('card_id', ''),
                                'name': saved_row.get('name', ''),
                                'image_url': card_image_url_for(saved_row, other_img_prefix),
                            }
                    else:
                        result[other_lang] = {
                            'card_id': tcgdex_card.get('id', ''),
                            'name': tcgdex_card.get('name', ''),
                            'image_url': tcgdex_image_url(tcgdex_card.get('image', '')),
                        }

        conn.close()
        return jsonify(result)

    except Exception as e:
        conn.close()
        return jsonify({'error': str(e)}), 500


@main_bp.route('/api/card/refresh/<card_id>', methods=['POST'])
def refresh_card_detail(card_id):
    """Refresh one Traditional Chinese card detail from the official page."""
    card_id = str(card_id or '').strip()
    if not card_id or card_id.startswith('jp'):
        return jsonify({'success': False, 'error': 'Only Traditional Chinese cards can be refreshed'}), 400

    conn = database.get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': 'DB Connection Failed'}), 500

    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT set_code, set_name, regulation_flags FROM cards WHERE card_id = %s",
            (card_id,)
        )
        existing = cursor.fetchone()
        conn.close()

        parsed = crawler.parse_detail_page(card_id)
        if not parsed:
            return jsonify({'success': False, 'error': 'Unable to refresh card detail'}), 404

        regulation = 1
        if existing and str(existing.get('regulation_flags') or '').lower() == 'expanded':
            regulation = 2

        crawler.save_card_with_context(parsed, {
            'set_code': existing.get('set_code') if existing else '',
            'set_name': existing.get('set_name') if existing else '',
            'regulation': regulation,
            'skip_images': True,
        })

        conn = database.get_db_connection()
        if not conn:
            return jsonify({'success': False, 'error': 'DB Connection Failed'}), 500
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM cards WHERE card_id = %s", (card_id,))
        row = cursor.fetchone()
        conn.close()
        if not row:
            return jsonify({'success': False, 'error': 'Card not found after refresh'}), 404

        card_data = card_payload_from_row(row, 'images')
        card_data['language'] = 'tw'
        return jsonify({'success': True, 'card': card_data})

    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/deck/save', methods=['POST'])
def save_deck():
    data = request.json
    name = data.get('name', '未命名牌組')
    content = json.dumps(data.get('deck', []))
    is_public = 1 if data.get('is_public', False) else 0
    
    # 獲取當前使用者的 ID (如果已登入)
    user_id = current_user.id if current_user.is_authenticated else None
    
    conn = database.get_db_connection()
    cursor = conn.cursor()
    new_id = generate_unique_id()
    try:
        cursor.execute("INSERT INTO decks (id, name, content, is_public, user_id) VALUES (%s, %s, %s, %s, %s)", (new_id, name, content, is_public, user_id))
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': new_id})
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/deck/<deck_id>')
def get_deck(deck_id):
    conn = database.get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM decks WHERE id = %s", (deck_id,))
    row = cursor.fetchone()
    conn.close()
    if row: 
        return jsonify({
            'success': True, 
            'id': row['id'], 
            'name': row['name'], 
            'deck': json.loads(row['content']), 
            'is_public': bool(row['is_public']),
            'owner_id': row['user_id'] if 'user_id' in row.keys() else None
        })
    return jsonify({'success': False, 'error': 'Deck not found'}), 404

@main_bp.route('/api/decks/public')
def get_public_decks():
    search_query = request.args.get('q', '').strip()
    conn = database.get_db_connection()
    cursor = conn.cursor()
    sql = "SELECT id, name, content, created_at FROM decks WHERE is_public = 1 ORDER BY created_at DESC LIMIT 20"
    if search_query:
        sql = "SELECT id, name, content, created_at FROM decks WHERE is_public = 1 AND (name LIKE %s OR content LIKE %s) ORDER BY created_at DESC LIMIT 50"
        cursor.execute(sql, ('%'+search_query+'%', '%'+search_query+'%'))
    else: cursor.execute(sql)
    rows = cursor.fetchall()
    conn.close()
    results = []
    for row in rows:
        try:
            content = json.loads(row['content'])
            preview_images = [c.get('image_url', '') for c in content[:4]]
            results.append({'id': row['id'], 'name': row['name'], 'count': len(content), 'preview_images': preview_images, 'created_at': row['created_at']})
        except: continue
    return jsonify(results)

# ==========================================
# [修改] 新增卡牌 - 需要 Admin 權限
# ==========================================
@main_bp.route('/api/card/add', methods=['POST'])
@admin_required  # [新增] 權限檢查
def add_card():
    try:
        if 'image' not in request.files: return jsonify({'success': False, 'error': 'No image file uploaded'}), 400
        file = request.files['image']
        card_id = request.form.get('card_id', '').strip()
        if file.filename == '' or not card_id: return jsonify({'success': False, 'error': 'Missing filename or card_id'}), 400
        
        ext = os.path.splitext(file.filename)[1]
        if not ext: ext = ".png"
        new_filename = secure_filename(f"{card_id}{ext}")
        save_path = os.path.join(config.IMAGE_FOLDER, new_filename)
        os.makedirs(config.IMAGE_FOLDER, exist_ok=True)
        file.save(save_path)
        
        mode = request.form.get('mode')
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        if mode == 'variant':
            source_id = request.form.get('source_card_id')
            if not source_id: return jsonify({'success': False, 'error': 'Missing source card id'}), 400
            cursor.execute("SELECT * FROM cards WHERE card_id = %s LIMIT 1", (source_id,))
            source_card = cursor.fetchone()
            if not source_card: return jsonify({'success': False, 'error': 'Source card not found'}), 404
            sql = "INSERT INTO cards (name, card_id, image_file, hp, element_type, card_type, sub_type, skills_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            cursor.execute(sql, (source_card['name'], card_id, new_filename, source_card['hp'], source_card['element_type'], source_card['card_type'], source_card['sub_type'], source_card['skills_json']))
        else:
            name = request.form.get('name')
            hp = request.form.get('hp')
            element_type = request.form.get('element_type')
            card_type = request.form.get('card_type')
            sub_type = request.form.get('sub_type')
            
            skills_data_str = request.form.get('skills_data')
            if skills_data_str:
                skills_json = skills_data_str 
            else:
                skill_text = request.form.get('skill_text', '')
                skills_json = json.dumps([{"name": "效果", "text": skill_text}], ensure_ascii=False)
            
            sql = "INSERT INTO cards (name, card_id, image_file, hp, element_type, card_type, sub_type, skills_json) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
            cursor.execute(sql, (name, card_id, new_filename, hp, element_type, card_type, sub_type, skills_json))
        
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': 'Card added successfully', 'image_url': f"/images/{new_filename}"})
    except Exception as e: return jsonify({'success': False, 'error': str(e)}), 500

# ==========================================
# [修改] 更新功能 API - 需要 Admin 權限
# ==========================================

@main_bp.route('/api/admin/check_version', methods=['GET'])
@admin_required  # [新增] 權限檢查
def check_version():
    """檢查官網與本地的版本差異"""
    local_meta = crawler.load_local_meta()
    list_url = crawler.construct_filtered_url(config.DEFAULT_LIST_URL, 1, None, None)
    official_count = 0
    official_pages = 0
    
    try:
        response = requests.get(list_url, headers=config.HEADERS, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            num_tag = soup.find('p', class_='resultNumber')
            if num_tag:
                official_count = int(num_tag.get_text(strip=True))
            page_tag = soup.find('p', class_='resultTotalPages')
            if page_tag:
                txt = page_tag.get_text(strip=True)
                m = re.search(r'(\d+)', txt)
                if m: official_pages = int(m.group(1))
    except Exception as e:
        print(f"Version check error: {e}")
        return jsonify({'success': False, 'error': str(e)})

    return jsonify({
        'success': True,
        'local': local_meta,
        'official': {
            'total_cards': official_count,
            'total_pages': official_pages
        },
        'needs_update': official_count > local_meta.get('total_cards', 0)
    })

@main_bp.route('/api/crawler/start', methods=['POST'])
@admin_required
def start_update():
    """啟動後台更新任務 (支援指定擴充包與賽制)"""
    if crawler.UPDATE_STATE['running']:
        return jsonify({'success': False, 'message': '更新已在進行中'})
    
    data = request.json
    # 支援新的參數格式
    target_expansion_codes = data.get('target_expansion_codes', []) # e.g. ['M3', 'AS6b']
    target_regulations = data.get('target_regulations', [1, 2])     # e.g. [1, 2]
    update_japanese = data.get('update_japanese', False)            # default False
    skip_images = data.get('skip_images', False)                    # default False (update images)

    # 啟動執行緒
    t = threading.Thread(target=crawler.run_update_process, args=(target_expansion_codes, target_regulations, update_japanese, skip_images))
    t.daemon = True
    t.start()
    
    return jsonify({'success': True, 'message': '更新任務已啟動'})


# ==========================================
# JP 爬蟲控制 API
# ==========================================
from services.crawler import jp_crawler

@main_bp.route('/api/jp/crawler/status')
def jp_crawler_status():
    """取得 JP 爬蟲狀態"""
    return jsonify(jp_crawler.JP_UPDATE_STATE)


@main_bp.route('/api/jp/crawler/expansions')
def jp_expansion_list():
    """取得 JP 擴充包列表 (從搜尋頁 JS 資料解析)"""
    expansions = jp_crawler.fetch_jp_expansion_meta()
    return jsonify(expansions)


@main_bp.route('/api/jp/crawler/start', methods=['POST'])
@admin_required
def start_jp_update():
    """啟動 JP 爬蟲"""
    if jp_crawler.JP_UPDATE_STATE['running']:
        return jsonify({'success': False, 'message': 'JP 更新已在進行中'})

    data = request.json or {}
    expansion_codes = data.get('expansions', [])  # 優先：按系列爬取
    start_id = data.get('start_id', None)
    end_id = data.get('end_id', None)
    num_workers = data.get('workers', 30)
    skip_images = data.get('skip_images', False)

    if expansion_codes:
        # JP 搜尋頁是 Vue SPA，無法直接解析。改用 ID 範圍。
        # 系列選擇僅作為 UI 參考，背後仍用 ID 範圍爬取。
        sid = start_id if start_id is not None else 1
        eid = end_id if end_id is not None else 52000
        t = threading.Thread(
            target=jp_crawler.crawl_card_range,
            args=(sid, eid),
            kwargs={'num_workers': num_workers, 'skip_images': skip_images}
        )
        msg = f'JP 更新已啟動 ({len(expansion_codes)} 個系列, ID {sid}→{eid})'
    else:
        sid = start_id if start_id is not None else 1
        eid = end_id if end_id is not None else 52000
        t = threading.Thread(
            target=jp_crawler.crawl_card_range,
            args=(sid, eid),
            kwargs={'num_workers': num_workers, 'skip_images': skip_images}
        )
        msg = f'JP 更新已啟動 (ID {sid} → {eid})'

    t.daemon = True
    t.start()

    return jsonify({'success': True, 'message': msg})


# ==========================================
# Limitless JP 爬蟲 API
# ==========================================
from services.crawler import limitless_jp_crawler as ljp

@main_bp.route('/api/limitless-jp/test', methods=['POST'])
def limitless_jp_test():
    """測試單卡解析"""
    data = request.json or {}
    set_code = data.get('set_code', 'SV8').strip().upper()
    number = data.get('number', '1')
    html = ljp._fetch(ljp.JP_CARD_URL_TPL.format(set_code=set_code, number=number))
    if not html:
        return jsonify({"status": "error", "msg": "404 或連線失敗"})
    card = ljp.parse_jp_card(html, set_code, str(number))
    if card:
        return jsonify({"status": "success", "card": {
            "name": card["name"],
            "card_type": card["card_type"],
            "sub_type": card["sub_type"],
            "hp": card["hp"],
            "element_type": card["element_type"],
            "weakness_type": card["weakness_type"],
            "weakness_value": card["weakness_value"],
            "resistance_type": card["resistance_type"],
            "resistance_value": card["resistance_value"],
            "retreat_cost": card["retreat_cost"],
            "rarity": card.get("rarity", ""),
            "regulation_mark": card.get("regulation_mark", ""),
            "set_name": card.get("set_name", ""),
            "set_code": card.get("set_code", ""),
            "set_number": card.get("set_number", ""),
            "artist": card.get("artist", ""),
            "skills": card.get("skills", []),
            "description": card.get("description", ""),
            "en_prints": card.get("en_prints", []),
        }})
    return jsonify({"status": "error", "msg": "解析失敗"})


@main_bp.route('/api/limitless-jp/sets', methods=['GET'])
def limitless_jp_sets():
    """取得所有日版系列列表"""
    sets = ljp.fetch_jp_sets()
    return jsonify({"status": "success", "count": len(sets), "sets": sets})


@main_bp.route('/api/limitless-jp/start', methods=['POST'])
@admin_required
def limitless_jp_start():
    """啟動 Limitless JP 批量爬取
    支援格式: {target: 'all'} 或 {target: 'SV8', card_count: 137}
    或 {targets: ['SV8', 'SV9'], card_counts: {'SV8':137, 'SV9':132}}
    """
    data = request.json or {}
    target = data.get('target', 'all')
    targets = data.get('targets', [])
    workers = int(data.get('workers', 5))
    delay = float(data.get('delay', 0.3))
    card_count = int(data.get('card_count', 999))
    card_counts = data.get('card_counts', {})
    
    def _run():
        if target == 'all':
            ljp.crawl_all(workers=workers, delay=delay)
        elif targets:
            for code in targets:
                cnt = card_counts.get(code, 999) if isinstance(card_counts, dict) else 999
                ljp.crawl_set(code, cnt, num_workers=workers, delay=delay, save=True)
        else:
            ljp.crawl_set(target, card_count, num_workers=workers, delay=delay, save=True)
    
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    
    desc = target if not targets else f"{len(targets)} 個系列 ({targets[0]}...)"
    return jsonify({"status": "success", "msg": f"任務已啟動: {desc}"})


@main_bp.route('/api/limitless-jp/status', methods=['GET'])
def limitless_jp_status():
    """取得 Limitless JP 爬蟲進度"""
    return jsonify({
        "is_running": ljp.UPDATE_STATE['running'],
        "progress": ljp.UPDATE_STATE['progress'],
        "message": ljp.UPDATE_STATE['message'],
        "current_set": ljp.UPDATE_STATE['current_set'],
        "completed_sets": ljp.UPDATE_STATE['completed_sets'],
        "total_sets": ljp.UPDATE_STATE['total_sets'],
        "logs": ljp.UPDATE_STATE['logs'],
    })


# ==========================================
# [新增] 賽季設定 API - 需要 Admin 權限
# ==========================================

@main_bp.route('/api/admin/regulation-settings', methods=['GET'])
@admin_required
def get_regulation_settings():
    """取得賽季設定（哪些字母為標準賽季）"""
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT mark, is_standard FROM regulation_settings ORDER BY mark")
        rows = cursor.fetchall()
        conn.close()

        all_marks = []
        standard_marks = []
        for r in rows:
            all_marks.append({'mark': r['mark'], 'is_standard': r['is_standard']})
            if r['is_standard']:
                standard_marks.append(r['mark'])

        return jsonify({
            'success': True,
            'marks': all_marks,
            'standard_marks': standard_marks
        })
    except Exception as e:
        return jsonify({
            'success': True,
            'marks': [],
            'standard_marks': ['F', 'G', 'H', 'I', 'J']
        })


@main_bp.route('/api/admin/regulation-settings', methods=['PUT'])
@admin_required
def update_regulation_settings():
    """更新賽季設定"""
    try:
        data = request.json
        standard_marks = data.get('standard_marks', [])

        conn = database.get_db_connection()
        cursor = conn.cursor()

        for letter in 'ABCDEFGHIJKLMNOPQRSTUVWXYZ':
            is_std = letter in standard_marks
            cursor.execute(
                """INSERT INTO regulation_settings (mark, is_standard)
                   VALUES (%s, %s)
                   ON CONFLICT (mark) DO UPDATE SET is_standard = EXCLUDED.is_standard""",
                (letter, is_std)
            )

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '賽季設定已更新'})
    except Exception as e:
        print(f"Update regulation settings error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/crawler/expansions', methods=['GET'])
@admin_required
def get_expansions():
    """取得擴充包列表 (用於更新選擇器)，按系列分組"""
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()

        # 確保 expansion_sets 表存在
        try:
            cursor.execute("SELECT set_code, set_name, series FROM expansion_sets ORDER BY last_updated DESC")
            rows = cursor.fetchall()
        except psycopg2.OperationalError:
            crawler.ensure_schema_updates()
            rows = []

        # 如果資料庫是空的，嘗試自動同步一次 meta
        if not rows:
            print("擴充包列表為空，正在同步...")
            crawler.fetch_expansion_meta()
            cursor.execute("SELECT set_code, set_name, series FROM expansion_sets ORDER BY last_updated DESC")
            rows = cursor.fetchall()

        conn.close()

        # 按系列分組
        series_order = []         # 保持系列出現順序
        series_groups = {}        # series_name -> [expansions]
        flat_list = []            # 向後相容的扁平列表

        for r in rows:
            code = r['set_code']
            name = r['set_name']
            series = r.get('series') or '其他'

            flat_list.append({'code': code, 'name': name, 'series': series})

            if series not in series_groups:
                series_groups[series] = []
                series_order.append(series)
            series_groups[series].append({'code': code, 'name': name})

        grouped = [{'series': s, 'expansions': series_groups[s]} for s in series_order]

        return jsonify({
            'success': True,
            'expansions': flat_list,       # 向後相容
            'grouped': grouped             # 新版分組格式
        })

    except Exception as e:
        print(f"Get expansions error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/crawler/status', methods=['GET'])
@admin_required  # [新增] 權限檢查
def get_update_status():
    """獲取當前更新狀態"""
    return jsonify(crawler.UPDATE_STATE)


# ==========================================
# [新增] 牌組對照表管理 API
# ==========================================

@main_bp.route('/api/admin/deck-mapping/start', methods=['POST'])
@admin_required
def start_deck_mapping():
    """啟動卡牌對照表批次更新（ptcgtw variant_id → local card_id）"""
    from services.deck_importer.card_mapping import run_mapping, get_mapping_status

    data = request.json or {}
    bot_count = int(data.get('bot_count', 5))
    bot_count = max(1, bot_count)  # 最少 1，上限自行決定

    success, message = run_mapping(worker_count=bot_count)
    return jsonify({
        'success': success,
        'message': message,
        'status': get_mapping_status()
    })


@main_bp.route('/api/admin/deck-mapping/status', methods=['GET'])
@admin_required
def get_deck_mapping_status():
    """查詢卡牌對照表更新進度"""
    from services.deck_importer.card_mapping import get_mapping_status
    return jsonify({
        'success': True,
        'status': get_mapping_status()
    })


@main_bp.route('/api/admin/deck-mapping/stats', methods=['GET'])
@admin_required
def get_deck_mapping_stats():
    """查詢對照表與牌組資料庫統計"""
    conn = database.get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '資料庫錯誤'}), 500
    try:
        cursor = conn.cursor()
        # id_mapping 統計
        cursor.execute("SELECT COUNT(*) as total FROM id_mapping")
        mapping_total = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) as total FROM id_mapping WHERE confidence = 'HIGH'")
        mapping_high = cursor.fetchone()["total"]
        # imported_decks 統計
        cursor.execute("SELECT COUNT(*) as total FROM imported_decks")
        decks_total = cursor.fetchone()["total"]
        cursor.execute("SELECT COUNT(*) as total FROM imported_decks WHERE card_list IS NOT NULL AND card_list != '[]'")
        decks_with_cards = cursor.fetchone()["total"]
        cursor.execute("SELECT MAX(deck_date) as latest FROM imported_decks")
        latest_date = cursor.fetchone()["latest"] or "無"
        # cards 表統計
        cursor.execute("SELECT COUNT(*) as total FROM cards")
        cards_total = cursor.fetchone()["total"]
        conn.close()
        return jsonify({
            'success': True,
            'stats': {
                'mapping_total': mapping_total,
                'mapping_high': mapping_high,
                'decks_total': decks_total,
                'decks_with_cards': decks_with_cards,
                'latest_deck_date': latest_date,
                'cards_total': cards_total
            }
        })
    except Exception as e:
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
# [新增] 牌組更新 API（每日 + 完整）
# ==========================================

@main_bp.route('/api/admin/deck-update/daily', methods=['POST'])
@admin_required
def start_daily_update():
    """啟動每日牌組更新（只匯入今日牌組）"""
    from services.deck_importer.deck_updater import run_daily_update, get_update_status
    data = request.json or {}
    bot_count = int(data.get('bot_count', 3))
    bot_count = max(1, bot_count)  # 最少 1，上限自行決定
    success, message = run_daily_update(worker_count=bot_count)
    return jsonify({'success': success, 'message': message, 'status': get_update_status()})


@main_bp.route('/api/admin/deck-update/full', methods=['POST'])
@admin_required
def start_full_update():
    """啟動完整牌組列表更新（掃描全部 ~1980 頁）"""
    from services.deck_importer.deck_updater import run_full_update, get_update_status
    data = request.json or {}
    bot_count = int(data.get('bot_count', 5))
    bot_count = max(1, bot_count)  # 最少 1，上限自行決定
    success, message = run_full_update(worker_count=bot_count)
    return jsonify({'success': success, 'message': message, 'status': get_update_status()})


@main_bp.route('/api/admin/deck-update/status', methods=['GET'])
@admin_required
def get_deck_update_status():
    """查詢牌組更新進度"""
    from services.deck_importer.deck_updater import get_update_status
    return jsonify({'success': True, 'status': get_update_status()})


@main_bp.route('/api/admin/deck-update/gap-fill', methods=['POST'])
@admin_required
def start_deck_gap_fill():
    """手動觸發輪轉增量缺漏偵測（預設掃 10 頁，補齊從未匯入的牌組）"""
    from services.deck_importer.deck_updater import run_gap_fill_update, get_update_status
    data = request.json or {}
    bot_count = int(data.get('bot_count', config.JP_DECK_AUTO_UPDATE_WORKERS))
    pages = int(data.get('pages', config.JP_DECK_GAP_FILL_PAGES))
    success, message = run_gap_fill_update(worker_count=bot_count, pages_per_run=pages)
    return jsonify({'success': success, 'message': message, 'status': get_update_status()})


@main_bp.route('/api/admin/deck-update/clear', methods=['POST'])
@admin_required
def clear_all_imported_decks():
    """刪除所有已匯入的牌組資料（imported_decks + deck_cards + id_mapping）"""
    conn = database.get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '資料庫錯誤'}), 500
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM deck_cards")
        cursor.execute("DELETE FROM id_mapping")
        cursor.execute("DELETE FROM imported_decks")
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '已清除所有日本牌組資料（deck_cards + id_mapping + imported_decks）'})
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
# [新增] 日本牌組推薦 API
# ==========================================

# ==========================================
# Limitless DeckList API
# ==========================================

@main_bp.route('/api/limitless-tournaments/list')
def get_limitless_tournaments():
    from services.limitless_decks.repository import list_tournaments

    result = list_tournaments(
        q=request.args.get('q', '').strip(),
        page=int(request.args.get('page', 1) or 1),
        region=request.args.get('region', '').strip(),
        fmt=request.args.get('format', '').strip(),
    )
    return jsonify(result), 200 if result.get('success') else 500


@main_bp.route('/api/limitless-tournaments/<path:tournament_id>/decks')
def get_limitless_tournament_decks(tournament_id):
    from services.limitless_decks.repository import list_tournament_decks

    result = list_tournament_decks(tournament_id, q=request.args.get('q', '').strip())
    if not result.get('success') and result.get('error') == 'Tournament not found':
        return jsonify(result), 404
    return jsonify(result), 200 if result.get('success') else 500


@main_bp.route('/api/limitless-decks/list')
def get_limitless_decks():
    from services.limitless_decks.repository import list_decks

    result = list_decks(
        q=request.args.get('q', '').strip(),
        page=int(request.args.get('page', 1) or 1),
        sort=request.args.get('sort', 'date'),
        region=request.args.get('region', '').strip(),
        fmt=request.args.get('format', '').strip(),
    )
    return jsonify(result), 200 if result.get('success') else 500


@main_bp.route('/api/limitless-decks/<path:deck_id>')
def get_limitless_deck(deck_id):
    from services.limitless_decks.repository import get_deck_detail, get_deck_metadata

    if request.args.get('full') in ('1', 'true', 'yes'):
        result = get_deck_detail(deck_id)
    else:
        result = get_deck_metadata(deck_id)
    if not result.get('success') and result.get('error') == 'Deck not found':
        return jsonify(result), 404
    return jsonify(result), 200 if result.get('success') else 500


@main_bp.route('/api/limitless-decks/<path:deck_id>/cards')
def get_limitless_deck_cards(deck_id):
    from services.limitless_decks.repository import get_deck_cards

    result = get_deck_cards(
        deck_id,
        language=request.args.get('language', 'tw'),
        mode=request.args.get('mode', 'normal'),
        include_debug=request.args.get('include_debug') in ('1', 'true', 'yes'),
    )
    if not result.get('success') and result.get('error') == 'Deck not found':
        return jsonify(result), 404
    return jsonify(result), 200 if result.get('success') else 500


@main_bp.route('/api/limitless-decks/<path:deck_id>/import', methods=['POST'])
def import_limitless_deck(deck_id):
    from services.limitless_decks.repository import import_deck

    data = request.json or {}
    result = import_deck(
        deck_id,
        language=data.get('language', 'tw'),
        mode=data.get('mode', 'normal'),
    )
    return jsonify(result), 200 if result.get('success') else 400


@main_bp.route('/api/admin/limitless/update/start', methods=['POST'])
@admin_required
def start_limitless_update():
    from services.limitless_decks.updater import get_status, start_update

    success, message = start_update(request.json or {})
    return jsonify({'success': success, 'message': message, 'status': get_status()})


@main_bp.route('/api/admin/limitless/update/status', methods=['GET'])
@admin_required
def get_limitless_update_status():
    from services.limitless_decks.updater import get_status

    return jsonify({'success': True, 'status': get_status()})


@main_bp.route('/api/admin/limitless/update/indexes', methods=['POST'])
@admin_required
def refresh_limitless_indexes():
    from services.limitless_decks.updater import refresh_indexes

    data = request.json or {}
    try:
        result = refresh_indexes(
            regions=data.get('regions') or ['global', 'jp'],
            max_tournaments=data.get('max_tournaments'),
        )
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/admin/limitless/update/tournament/<path:tournament_id>', methods=['POST'])
@admin_required
def update_limitless_tournament(tournament_id):
    from services.limitless_decks.updater import update_tournament

    data = request.json or {}
    try:
        result = update_tournament(
            tournament_id,
            include_bling=bool(data.get('include_bling', False)),
            stale_hours=int(data.get('stale_hours', 0) or 0),
            max_decks=data.get('max_decks'),
        )
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/admin/limitless/update/deck/<path:deck_id>', methods=['POST'])
@admin_required
def update_limitless_deck(deck_id):
    from services.limitless_decks.updater import update_deck

    data = request.json or {}
    try:
        result = update_deck(deck_id, include_bling=bool(data.get('include_bling', False)))
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/decks/japanese/list')
def get_japanese_decks():
    """
    日本牌組列表 — 高速版：使用 deck_search_index 預計算表
    - 多詞搜索：空白分隔 → COUNT DISTINCT card_name → 關聯度排序
    - 動態 Hashtag：從 card_list + mapping_cache 解析
    """
    search_query = request.args.get('q', '').strip()
    sort_mode = request.args.get('sort', 'match_count')
    page = int(request.args.get('page', 1))
    per_page = 20

    conn = database.get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '資料庫未找到'}), 404

    try:
        cursor = conn.cursor()
        search_terms = [t for t in re.split(r'[\s\u3000]+', search_query) if t] if search_query else []

        if search_terms:
            # 多詞搜索：用 Python 格式化 LIKE（避免 psycopg2 %s 參數順序地獄）
            safe_terms = [t.replace("%", "").replace("_", "") for t in search_terms]
            like_terms = [f"%{t}%" for t in safe_terms if t]
            if not like_terms:
                return jsonify({'success': True, 'decks': [], 'total': 0, 'page': page, 'pages': 1, 'suggestion': None})
            where_clause = " OR ".join(["dsi.card_name ILIKE %s"] * len(like_terms))
            match_parts = ["MAX(CASE WHEN dsi.card_name ILIKE %s THEN 1 ELSE 0 END)" for _ in like_terms]
            match_expr = " + ".join(match_parts)

            order_clause = "matched_card_count DESC, match_count DESC, d.deck_date DESC"
            if sort_mode == 'date':
                order_clause = "d.deck_date DESC, matched_card_count DESC, match_count DESC"

            # 總數
            count_sql = f"SELECT COUNT(DISTINCT dsi.deck_id) as cnt FROM deck_search_index dsi WHERE {where_clause}"
            cursor.execute(count_sql, like_terms)
            total_count = cursor.fetchone()['cnt']

            # 主查詢
            search_sql = f"""
                WITH matched_decks AS (
                    SELECT dsi.deck_id,
                           ({match_expr}) as match_count,
                           COALESCE(SUM(dsi.count), 0) as matched_card_count
                    FROM deck_search_index dsi
                    WHERE {where_clause}
                    GROUP BY dsi.deck_id
                )
                SELECT d.id, d.deck_code, d.title, d.deck_date, d.image_url, d.card_list, d.tags,
                       matched_decks.match_count,
                       matched_decks.matched_card_count
                FROM matched_decks
                JOIN imported_decks d ON d.id = matched_decks.deck_id
                ORDER BY {order_clause}
                LIMIT %s OFFSET %s
            """
            cursor.execute(search_sql, like_terms + like_terms + [per_page, (page - 1) * per_page])
            deck_rows = cursor.fetchall()

            has_full_match = any(r['match_count'] >= len(search_terms) for r in deck_rows)
        else:
            # 無搜索：顯示最新牌組
            cursor.execute("SELECT COUNT(*) as cnt FROM imported_decks")
            total_count = cursor.fetchone()['cnt']
            cursor.execute("""
                SELECT id, deck_code, title, deck_date, image_url, card_list, tags
                FROM imported_decks ORDER BY deck_date DESC LIMIT %s OFFSET %s
            """, (per_page, (page - 1) * per_page))
            deck_rows = cursor.fetchall()
            has_full_match = True

        # Hashtag: 用 mapping_cache + name_cache（同之前邏輯）
        mapping_cache = {}
        parsed_decks = []
        variant_ids = set()
        for row in deck_rows:
            try:
                cl = json.loads(row['card_list']) if row.get('card_list') else []
            except Exception:
                cl = []
            parsed_decks.append((row, cl))
            for item in cl:
                vid = item.get('id')
                if vid is not None and str(vid).isdigit():
                    variant_ids.add(int(vid))

        if variant_ids:
            variants = list(variant_ids)
            ph = ','.join(['%s'] * len(variants))
            cursor.execute(
                f"SELECT external_variant_id, local_card_id FROM id_mapping WHERE external_variant_id IN ({ph})",
                variants
            )
            for m in cursor.fetchall():
                mapping_cache[str(m['external_variant_id'])] = m['local_card_id']

        name_cache = {}
        if mapping_cache:
            lids = list({lid for lid in mapping_cache.values() if lid})
            if lids:
                ph = ','.join(['%s'] * len(lids))
                cursor.execute(f"SELECT card_id, name, card_type FROM cards WHERE card_id IN ({ph})", lids)
                for c in cursor.fetchall():
                    name_cache[c['card_id']] = (c['name'], c['card_type'])

        results = []
        for row, cl in parsed_decks:
            dynamic_tags = []
            poke_counts = {}
            matched_names = set()
            for item in cl:
                vid = item.get('id')
                try:
                    qty = int(item.get('c', 1) or 1)
                except (TypeError, ValueError):
                    qty = 1
                lid = mapping_cache.get(str(vid))
                if lid and lid in name_cache:
                    cname, ctype = name_cache[lid]
                    if 'Pokémon' in ctype or 'Pokemon' in ctype:
                        poke_counts[cname] = poke_counts.get(cname, 0) + qty
                    # 標記搜索匹配的卡片
                    for term in search_terms:
                        if term in cname:
                            matched_names.add(cname)

            # 排序：搜索匹配的卡片優先顯示，然後按數量
            def tag_sort_key(item):
                name, cnt = item
                return (0 if name in matched_names else 1, -cnt)
            top3 = sorted(poke_counts.items(), key=tag_sort_key)[:3]
            dynamic_tags = [f"{name}:{cnt}" for name, cnt in top3]
            if not dynamic_tags and row.get('tags'):
                try:
                    dynamic_tags = json.loads(row['tags'])
                except:
                    dynamic_tags = []

            results.append({
                'code': row['deck_code'],
                'title': row['title'],
                'date': row['deck_date'],
                'image': row.get('image_url', ''),
                'tags': dynamic_tags,
                'match_count': row.get('match_count', 0),
                'matched_card_count': int(row.get('matched_card_count') or 0),
            })

        suggestion = None
        if search_terms and not has_full_match:
            suggestion = {
                'message': f"沒有同時包含「{'」和「'.join(search_terms)}」的牌組",
                'terms': search_terms,
            }

        return jsonify({
            'success': True,
            'decks': results,
            'total': total_count,
            'page': page,
            'pages': max(1, (total_count + per_page - 1) // per_page),
            'suggestion': suggestion,
        })
    except Exception as e:
        print(f"Get JP decks error: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

@main_bp.route('/api/decks/japanese/<deck_code>')
def get_japanese_deck_content(deck_code):
    """獲取特定日本牌組內容 — 從 card_list + id_mapping 即時解析"""
    conn = database.get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '資料庫錯誤'}), 500

    try:
        cursor = conn.cursor()

        # 取得牌組 metadata + card_list
        cursor.execute(
            "SELECT title, card_list FROM imported_decks WHERE deck_code = %s",
            (deck_code,)
        )
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': '找不到牌組'}), 404

        deck_title = row['title'] or deck_code
        card_list = []
        try:
            card_list = json.loads(row['card_list']) if row['card_list'] else []
        except json.JSONDecodeError:
            card_list = []

        output_cards = []

        missing_cards = []
        fallback_count = 0
        resolver_session = requests.Session()
        resolver_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        try:
            for item in card_list:
                variant_id = item.get('id')
                count = item.get('c', 1)
                if not variant_id:
                    continue

                resolved = resolve_variant(
                    cursor,
                    variant_id,
                    session=resolver_session,
                    write_mapping=True,
                )
                if resolved.get('card_row'):
                    if resolved.get('source') != 'id_mapping':
                        fallback_count += 1
                    output_cards.extend(card_row_to_payload(
                        resolved['card_row'],
                        count=count,
                        include_logic=True,
                        logic_loader=database.get_card_logic,
                    ))
                elif resolved.get('missing'):
                    missing = dict(resolved['missing'])
                    missing['count'] = count
                    missing_cards.append(missing)
            conn.commit()
        finally:
            resolver_session.close()

        conn.close()

        return jsonify({
            'success': True,
            'name': deck_title,
            'deck': output_cards,
            'missing_cards': missing_cards,
            'fallback_count': fallback_count,
        })
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

# ==========================================
# [新增] PTCG Live 轉換工具 API
# ==========================================

@main_bp.route('/api/tools/convert-live', methods=['POST'])
def convert_to_live():
    """
    將牌組列表轉換為 PTCG Live 格式
    接收: { "deck": [ { "card_id": "...", "name": "...", "count": 1 }, ... ] }
    流程:
    1. 遍歷牌組，先嘗試用 local_card_id 在 id_mapping 找對應外部 ID。
    2. 如果找不到，則在主資料庫搜尋「同名」的其他卡片 ID，再回 id_mapping 找。
    3. 組合 payload 發送給外部 API。
    """
    try:
        data = request.json
        deck_list = data.get('deck', [])
        
        if not deck_list:
            return jsonify({'success': False, 'error': '牌組為空'}), 400

        # 1. 準備資料庫連線
        # 連線到 ID 映射庫 (imported_decks.db)
        conn_map = database.get_db_connection()
        if not conn_map:
            return jsonify({'success': False, 'error': '無法連線至映射資料庫 (imported_decks.db)'}), 500
        
        # 連線到主卡片庫 (pokemon_card_database.db) 用於同名搜尋
        conn_main = database.get_db_connection()
        
        payload_items = []
        not_found_list = []

        cursor_map = conn_map.cursor()
        cursor_main = conn_main.cursor() if conn_main else None

        # 為了效能，先將牌組整理為 { card_id: {count, name} }
        # 注意：前端傳來的可能是展開的牌組，需合併數量
        consolidated_deck = {}
        for card in deck_list:
            cid = card.get('card_id') or card.get('id')
            # 有些卡片可能是用 image_file 當 ID (例如舊資料)，需處理
            if not cid and card.get('image_file'):
                cid = card['image_file'].split('.')[0]
                
            if not cid: continue
            
            if cid not in consolidated_deck:
                consolidated_deck[cid] = {'count': 0, 'name': card.get('name', ''), 'id': cid}
            consolidated_deck[cid]['count'] += 1

        # 開始轉換
        for cid, info in consolidated_deck.items():
            external_id = None
            
            # 策略 A: 直接查詢 ID
            cursor_map.execute("SELECT external_variant_id FROM id_mapping WHERE local_card_id = %s", (cid,))
            row = cursor_map.fetchone()
            
            if row:
                external_id = row['external_variant_id']
            
            # 策略 B: 如果找不到且有名稱，嘗試搜尋同名卡
            elif cursor_main and info['name']:
                # 找出所有同名卡的 ID
                cursor_main.execute("SELECT card_id FROM cards WHERE name = %s", (info['name'],))
                same_name_rows = cursor_main.fetchall()
                
                # 檢查這些同名 ID 是否有映射資料
                for sn_row in same_name_rows:
                    other_id = sn_row['card_id']
                    cursor_map.execute("SELECT external_variant_id FROM id_mapping WHERE local_card_id = %s", (other_id,))
                    row_backup = cursor_map.fetchone()
                    if row_backup:
                        external_id = row_backup['external_variant_id']
                        break # 找到一個能用的就行
            
            if external_id:
                # 外部 API 要求格式: variant_id 需要 "v_" 前綴 (如果是純數字的話)
                # 觀察範例: "v_21681"
                v_id_str = str(external_id)
                if not v_id_str.startswith('v_'):
                    v_id_str = f"v_{v_id_str}"
                
                payload_items.append({
                    "variant_id": v_id_str,
                    "copies": info['count'],
                    "debug_name": info['name']
                })
            else:
                not_found_list.append(f"{info['name']} ({cid})")

        # 關閉連線
        conn_map.close()
        if conn_main: conn_main.close()

        if not payload_items:
             return jsonify({
                'success': True, 
                'deck_string': "", 
                'not_found': not_found_list,
                'message': '沒有找到任何可對應的卡片'
            })

        # 2. 呼叫外部 API
        target_url = "https://ptcgtw.shop/index_function/api/19_ptcgtw_to_live_api.php"
        
        # 模擬瀏覽器 Header，避免被擋
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
            "Content-Type": "application/json"
        }
        
        external_res = requests.post(target_url, json=payload_items, headers=headers, timeout=10)
        
        if external_res.status_code == 200:
            result = external_res.json()
            # 外部 API 成功，回傳結果
            # 我們將外部的 not_found (英文版缺失) 與我們內部的 not_found (映射缺失) 合併顯示
            
            final_deck_string = result.get('deck_string', '')
            api_not_found = result.get('not_found', [])
            
            # 簡單處理一下 API 回傳的 not_found 格式，讓前端好顯示
            for item in api_not_found:
                if isinstance(item, dict):
                    not_found_list.append(f"{item.get('name_tw', 'Unknown')} (無英文版)")
            
            return jsonify({
                'success': True,
                'deck_string': final_deck_string,
                'not_found': not_found_list,
                'debug_log': result.get('debug_log', [])
            })
        else:
            return jsonify({'success': False, 'error': f"外部 API 錯誤: {external_res.status_code}"}), 502

    except Exception as e:
        print(f"Live convert error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==========================================
# [新增] 用戶管理 API - 需要 Admin 權限
# ==========================================

@main_bp.route('/api/admin/users', methods=['GET'])
@admin_required
def get_all_users():
    """獲取所有用戶列表"""
    try:
        conn = User._get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT id, username, email, role, is_verified, created_at FROM users ORDER BY created_at DESC")
        rows = cursor.fetchall()
        conn.close()
        
        users = []
        for row in rows:
            users.append({
                'id': row['id'],
                'username': row['username'],
                'email': row['email'],
                'role': row['role'],
                'is_verified': bool(row['is_verified']),
                'created_at': row['created_at']
            })
        
        return jsonify({'success': True, 'users': users})
    except Exception as e:
        print(f"Get users error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/admin/users/role', methods=['POST'])
@admin_required
def update_user_role():
    """更新用戶角色"""
    try:
        data = request.json
        user_id = data.get('user_id')
        new_role = data.get('role')
        
        if not user_id or not new_role:
            return jsonify({'success': False, 'error': '缺少必要參數'}), 400
        
        if new_role not in ['admin', 'user']:
            return jsonify({'success': False, 'error': '無效的角色'}), 400
        
        # 不能修改自己的角色
        if user_id == current_user.id:
            return jsonify({'success': False, 'error': '無法修改自己的角色'}), 400
        
        conn = User._get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET role = %s WHERE id = %s", (new_role, user_id))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'用戶角色已更新為 {new_role}'})
    except Exception as e:
        print(f"Update role error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/admin/users/verify', methods=['POST'])
@admin_required
def admin_verify_user():
    """手動驗證用戶"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': '缺少用戶 ID'}), 400
        
        conn = User._get_db()
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET is_verified = 1 WHERE id = %s", (user_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': '用戶已驗證'})
    except Exception as e:
        print(f"Verify user error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/admin/users/delete', methods=['POST'])
@admin_required
def delete_user():
    """刪除用戶"""
    try:
        data = request.json
        user_id = data.get('user_id')
        
        if not user_id:
            return jsonify({'success': False, 'error': '缺少用戶 ID'}), 400
        
        # 不能刪除自己
        if user_id == current_user.id:
            return jsonify({'success': False, 'error': '無法刪除自己的帳號'}), 400
        
        conn = User._get_db()
        cursor = conn.cursor()
        
        # 檢查用戶是否存在
        cursor.execute("SELECT username FROM users WHERE id = %s", (user_id,))
        user = cursor.fetchone()
        if not user:
            conn.close()
            return jsonify({'success': False, 'error': '用戶不存在'}), 404
        
        # 刪除用戶
        cursor.execute("DELETE FROM users WHERE id = %s", (user_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'用戶已刪除'})
    except Exception as e:
        print(f"Delete user error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@main_bp.route('/api/admin/users/<user_id>', methods=['PUT'])
@admin_required
def admin_update_user(user_id):
    """管理員編輯用戶資料（用戶名、Email、密碼）"""
    try:
        data = request.json

        # 不能編輯自己
        if user_id == current_user.id:
            return jsonify({'success': False, 'error': '無法編輯自己的帳號，請至個人設定修改。'}), 400

        kwargs = {}
        if 'username' in data and data['username']:
            kwargs['username'] = data['username'].strip()
        if 'email' in data and data['email']:
            kwargs['email'] = data['email'].strip()
        if 'password' in data and data['password']:
            kwargs['password'] = data['password'].strip()

        success, message = User.update_profile(user_id, **kwargs)
        if success:
            return jsonify({'success': True, 'message': message})
        else:
            return jsonify({'success': False, 'error': message}), 400

    except Exception as e:
        print(f"Admin update user error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ==========================================
# 結構化邏輯抽取 API - 需要 Admin 權限
# ==========================================

@main_bp.route('/api/admin/logic-extractor/gap-a/status', methods=['GET'])
@admin_required
def get_gap_a_logic_status():
    """讀取 Gap A threshold-only 抽取層狀態。"""
    conn = database.get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '資料庫連線失敗'}), 500

    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public'
              AND table_name = 'processed_cards'
              AND column_name IN ('predicates', 'extractor_version', 'source_text_hash')
        """)
        columns = {row['column_name'] for row in cursor.fetchall()}
        migration_ready = {'predicates', 'extractor_version', 'source_text_hash'}.issubset(columns)

        processed_count = 0
        if migration_ready:
            cursor.execute(
                "SELECT COUNT(*) AS count FROM processed_cards WHERE extractor_version = %s",
                (LOGIC_EXTRACTOR_VERSION,)
            )
            row = cursor.fetchone()
            processed_count = row['count'] if row else 0

        return jsonify({
            'success': True,
            'extractor_version': LOGIC_EXTRACTOR_VERSION,
            'scope': 'gap_a_threshold_only',
            'migration_ready': migration_ready,
            'available_columns': sorted(columns),
            'processed_count': processed_count,
            'note': 'Missing action predicates do not mean a card has no action effect.'
        })
    except Exception as e:
        print(f"Gap A logic status error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()


@main_bp.route('/api/admin/logic-extractor/gap-a/backfill', methods=['POST'])
@admin_required
def run_gap_a_logic_backfill():
    """執行 Gap A threshold-only 抽取回填。預設 dry-run，不寫 DB。"""
    data = request.get_json(silent=True) or {}
    dry_run = _request_bool(data.get('dry_run'), True)
    confirm = _request_bool(data.get('confirm'), False)

    if not dry_run and not confirm:
        return jsonify({
            'success': False,
            'error': '實際寫入 processed_cards 需要 dry_run=false 且 confirm=true'
        }), 400

    try:
        limit = data.get('limit')
        offset = int(data.get('offset') or 0)
        skip_empty = _request_bool(data.get('skip_empty'), True)
        limit = int(limit) if limit not in (None, '') else None
        if limit is not None and limit <= 0:
            return jsonify({'success': False, 'error': 'limit 必須大於 0'}), 400
        if offset < 0:
            return jsonify({'success': False, 'error': 'offset 不能小於 0'}), 400
    except (TypeError, ValueError):
        return jsonify({'success': False, 'error': 'limit/offset 參數格式錯誤'}), 400

    conn = database.get_db_connection()
    if not conn:
        return jsonify({'success': False, 'error': '資料庫連線失敗'}), 500

    try:
        summary = backfill_gap_a_threshold_only(
            conn,
            limit=limit,
            offset=offset,
            dry_run=dry_run,
            skip_empty=skip_empty,
        )
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        return jsonify({'success': True, 'summary': summary})
    except Exception as e:
        conn.rollback()
        print(f"Gap A logic backfill error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
    finally:
        conn.close()

# ==========================================
# [新增] 推薦列表(公開牌組)管理 API - 需要 Admin 權限
# ==========================================

@main_bp.route('/api/admin/decks', methods=['GET'])
@admin_required
def get_all_decks():
    """獲取所有牌組列表（管理員用）"""
    try:
        search_query = request.args.get('q', '').strip()
        show_all = request.args.get('all', 'false').lower() == 'true'
        
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        if show_all:
            # 顯示所有牌組
            if search_query:
                sql = """SELECT id, name, content, is_public, user_id, created_at 
                         FROM decks 
                         WHERE name LIKE %s OR content LIKE %s 
                         ORDER BY created_at DESC LIMIT 100"""
                cursor.execute(sql, ('%'+search_query+'%', '%'+search_query+'%'))
            else:
                sql = """SELECT id, name, content, is_public, user_id, created_at 
                         FROM decks 
                         ORDER BY created_at DESC LIMIT 100"""
                cursor.execute(sql)
        else:
            # 只顯示公開牌組
            if search_query:
                sql = """SELECT id, name, content, is_public, user_id, created_at 
                         FROM decks 
                         WHERE is_public = 1 AND (name LIKE %s OR content LIKE %s)
                         ORDER BY created_at DESC LIMIT 100"""
                cursor.execute(sql, ('%'+search_query+'%', '%'+search_query+'%'))
            else:
                sql = """SELECT id, name, content, is_public, user_id, created_at 
                         FROM decks 
                         WHERE is_public = 1 
                         ORDER BY created_at DESC LIMIT 100"""
                cursor.execute(sql)
        
        rows = cursor.fetchall()
        conn.close()
        
        results = []
        for row in rows:
            try:
                content = json.loads(row['content'])
                preview_images = [c.get('image_url', '') for c in content[:4] if c.get('image_url')]
                results.append({
                    'id': row['id'],
                    'name': row['name'],
                    'count': len(content),
                    'is_public': bool(row['is_public']),
                    'user_id': row['user_id'],
                    'preview_images': preview_images,
                    'created_at': row['created_at']
                })
            except:
                continue
        
        return jsonify({'success': True, 'decks': results})
    except Exception as e:
        print(f"Get all decks error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/admin/deck/<deck_id>', methods=['GET'])
@admin_required
def get_deck_detail(deck_id):
    """獲取單個牌組詳情"""
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM decks WHERE id = %s", (deck_id,))
        row = cursor.fetchone()
        conn.close()
        
        if not row:
            return jsonify({'success': False, 'error': '牌組不存在'}), 404
        
        content = json.loads(row['content']) if row['content'] else []
        
        return jsonify({
            'success': True,
            'deck': {
                'id': row['id'],
                'name': row['name'],
                'content': content,
                'is_public': bool(row['is_public']),
                'user_id': row['user_id'] if 'user_id' in row.keys() else None,
                'created_at': row['created_at']
            }
        })
    except Exception as e:
        print(f"Get deck detail error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/admin/deck/<deck_id>', methods=['PUT'])
@admin_required
def update_deck(deck_id):
    """更新牌組（名稱、公開狀態）"""
    try:
        data = request.json
        
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        # 檢查牌組是否存在
        cursor.execute("SELECT id FROM decks WHERE id = %s", (deck_id,))
        if not cursor.fetchone():
            conn.close()
            return jsonify({'success': False, 'error': '牌組不存在'}), 404
        
        # 構建更新語句
        updates = []
        values = []
        
        if 'name' in data:
            updates.append("name = %s")
            values.append(data['name'].strip())
        
        if 'is_public' in data:
            updates.append("is_public = %s")
            values.append(1 if data['is_public'] else 0)
        
        if 'content' in data:
            updates.append("content = %s")
            values.append(json.dumps(data['content'], ensure_ascii=False))
        
        if not updates:
            conn.close()
            return jsonify({'success': False, 'error': '沒有要更新的內容'}), 400
        
        sql = f"UPDATE decks SET {', '.join(updates)} WHERE id = %s"
        values.append(deck_id)
        cursor.execute(sql, values)
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': '牌組已更新'})
    except Exception as e:
        print(f"Update deck error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/admin/deck/<deck_id>', methods=['DELETE'])
@admin_required
def delete_deck(deck_id):
    """刪除牌組"""
    try:
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        # 檢查牌組是否存在
        cursor.execute("SELECT name FROM decks WHERE id = %s", (deck_id,))
        row = cursor.fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'error': '牌組不存在'}), 404
        
        deck_name = row['name']
        
        # 刪除牌組
        cursor.execute("DELETE FROM decks WHERE id = %s", (deck_id,))
        conn.commit()
        conn.close()
        
        return jsonify({'success': True, 'message': f'牌組 "{deck_name}" 已刪除'})
    except Exception as e:
        print(f"Delete deck error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ==========================================
# [新增] 工作區 API - 需要登入
# ==========================================

@main_bp.route('/api/workspace', methods=['GET'])
@login_required
def get_workspace():
    """獲取用戶的工作區樹狀結構"""
    try:
        tree = database.get_user_workspace_tree(current_user.id)
        return jsonify({'success': True, 'workspace': tree})
    except Exception as e:
        print(f"Get workspace error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/workspace/item', methods=['POST'])
@login_required
def create_workspace_item():
    """建立工作區項目（資料夾或牌組）"""
    try:
        data = request.json
        name = data.get('name', '').strip()
        item_type = data.get('type', 'deck')  # 'folder' 或 'deck'
        parent_id = data.get('parent_id')  # 可選，放在哪個資料夾下
        content = data.get('content', [])  # 牌組內容（僅 deck 類型）
        
        if not name:
            return jsonify({'success': False, 'error': '請輸入名稱'}), 400
        
        if item_type not in ['folder', 'deck']:
            return jsonify({'success': False, 'error': '無效的類型'}), 400
        
        result = database.create_workspace_item(
            user_id=current_user.id,
            name=name,
            item_type=item_type,
            parent_id=parent_id,
            content=content if item_type == 'deck' else None
        )
        
        if result:
            return jsonify({'success': True, 'item': result})
        else:
            return jsonify({'success': False, 'error': '建立失敗'}), 500
    except Exception as e:
        print(f"Create workspace item error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/workspace/item/<item_id>', methods=['GET'])
@login_required
def get_workspace_item(item_id):
    """獲取單個工作區項目"""
    try:
        item = database.get_workspace_item(item_id, current_user.id)
        if item:
            return jsonify({'success': True, 'item': item})
        else:
            return jsonify({'success': False, 'error': '項目不存在'}), 404
    except Exception as e:
        print(f"Get workspace item error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/workspace/item/<item_id>', methods=['PUT'])
@login_required
def update_workspace_item(item_id):
    """更新工作區項目"""
    try:
        data = request.json
        
        # 構建更新參數
        update_kwargs = {}
        if 'name' in data:
            update_kwargs['name'] = data['name'].strip()
        if 'content' in data:
            update_kwargs['content'] = data['content']
        if 'parent_id' in data:
            update_kwargs['parent_id'] = data['parent_id']
        if 'sort_order' in data:
            update_kwargs['sort_order'] = data['sort_order']
        
        if not update_kwargs:
            return jsonify({'success': False, 'error': '沒有要更新的內容'}), 400
        
        result = database.update_workspace_item(item_id, current_user.id, **update_kwargs)
        
        if result:
            timeline = None
            if 'content' in update_kwargs and data.get('timeline_action'):
                timeline = database.create_workspace_timeline(
                    item_id,
                    current_user.id,
                    action=data.get('timeline_action') or '編輯牌組',
                    source=data.get('timeline_source') or 'editor',
                    content=update_kwargs['content'],
                )
            return jsonify({'success': True, 'message': '更新成功', 'timeline': timeline})
        else:
            return jsonify({'success': False, 'error': '更新失敗或項目不存在'}), 404
    except Exception as e:
        print(f"Update workspace item error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/workspace/item/<item_id>/timeline', methods=['GET'])
@login_required
def get_workspace_item_timeline(item_id):
    try:
        entries = database.get_workspace_timeline(item_id, current_user.id, limit=50)
        return jsonify({'success': True, 'timeline': entries})
    except Exception as e:
        print(f"Get workspace timeline error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/workspace/item/<item_id>/timeline', methods=['POST'])
@login_required
def create_workspace_item_timeline(item_id):
    try:
        data = request.json or {}
        timeline = database.create_workspace_timeline(
            item_id,
            current_user.id,
            action=data.get('action') or '編輯牌組',
            source=data.get('source') or 'editor',
            content=data.get('content'),
        )
        if not timeline:
            return jsonify({'success': False, 'error': 'timeline create failed'}), 404
        return jsonify({'success': True, 'timeline': timeline})
    except Exception as e:
        print(f"Create workspace timeline error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/workspace/item/<item_id>/timeline/<timeline_id>/restore', methods=['POST'])
@login_required
def restore_workspace_item_timeline(item_id, timeline_id):
    try:
        restored = database.restore_workspace_timeline(item_id, current_user.id, timeline_id)
        if not restored:
            return jsonify({'success': False, 'error': 'timeline snapshot not found'}), 404
        return jsonify({'success': True, **restored})
    except Exception as e:
        print(f"Restore workspace timeline error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/workspace/item/<item_id>', methods=['DELETE'])
@login_required
def delete_workspace_item(item_id):
    """刪除工作區項目"""
    try:
        result = database.delete_workspace_item(item_id, current_user.id)
        
        if result:
            return jsonify({'success': True, 'message': '刪除成功'})
        else:
            return jsonify({'success': False, 'error': '刪除失敗或項目不存在'}), 404
    except Exception as e:
        print(f"Delete workspace item error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/workspace/item/<item_id>/move', methods=['POST'])
@login_required
def move_workspace_item(item_id):
    """移動工作區項目到另一個資料夾"""
    try:
        data = request.json
        new_parent_id = data.get('parent_id')  # None 表示移動到根目錄
        
        # 不能把資料夾移動到自己或子資料夾中
        if new_parent_id:
            # 檢查目標是否為自己的子項目
            def is_descendant(parent_id, target_id):
                if parent_id == target_id:
                    return True
                item = database.get_workspace_item(parent_id, current_user.id)
                if item and item.get('type') == 'folder':
                    conn = database.get_workspace_db()
                    cursor = conn.cursor()
                    cursor.execute("SELECT id FROM user_workspace WHERE parent_id = %s AND user_id = %s", 
                                   (parent_id, current_user.id))
                    children = cursor.fetchall()
                    conn.close()
                    for child in children:
                        if is_descendant(child['id'], target_id):
                            return True
                return False
            
            if item_id == new_parent_id or is_descendant(item_id, new_parent_id):
                return jsonify({'success': False, 'error': '無法移動到自己或子資料夾中'}), 400
        
        result = database.update_workspace_item(item_id, current_user.id, parent_id=new_parent_id)
        
        if result:
            return jsonify({'success': True, 'message': '移動成功'})
        else:
            return jsonify({'success': False, 'error': '移動失敗'}), 500
    except Exception as e:
        print(f"Move workspace item error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

@main_bp.route('/api/workspace/item/<item_id>/publish', methods=['POST'])
@login_required
def publish_workspace_deck(item_id):
    """將工作區牌組公開分享（複製到公開牌組列表）"""
    try:
        item = database.get_workspace_item(item_id, current_user.id)
        
        if not item:
            return jsonify({'success': False, 'error': '項目不存在'}), 404
        
        if item['type'] != 'deck':
            return jsonify({'success': False, 'error': '只能分享牌組'}), 400
        
        # 建立公開分享連結（使用原有的 decks 表）
        conn = database.get_db_connection()
        cursor = conn.cursor()
        
        new_id = generate_unique_id()
        content_json = json.dumps(item.get('content', []))
        
        cursor.execute(
            "INSERT INTO decks (id, name, content, is_public, user_id) VALUES (%s, %s, %s, 1, %s)",
            (new_id, item['name'], content_json, current_user.id)
        )
        conn.commit()
        conn.close()
        
        share_url = f"{request.host_url}card/{new_id}"
        
        return jsonify({
            'success': True, 
            'share_id': new_id,
            'share_url': share_url,
            'message': '牌組已公開分享'
        })
    except Exception as e:
        print(f"Publish deck error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
