import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(BASE_DIR, '..'))
load_dotenv(os.path.join(ROOT_DIR, '.env'))


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None:
        return default
    return str(value).lower() in ['true', 'on', '1', 'yes']


def _env_int(name, default):
    value = os.environ.get(name)
    if value in (None, ''):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _env_optional_int(name, default=None):
    value = os.environ.get(name)
    if value in (None, ''):
        return default
    try:
        parsed = int(value)
        return parsed if parsed > 0 else None
    except (TypeError, ValueError):
        return default


def _env_csv(name, default):
    value = os.environ.get(name)
    if value in (None, ''):
        return default
    items = [item.strip() for item in str(value).split(',') if item.strip()]
    return items or default

# ── 前端資源 ──
FRONTEND_DIR = os.path.normpath(os.path.join(BASE_DIR, '..', 'frontend'))
TEMPLATE_DIR = os.path.join(FRONTEND_DIR, 'html')
CSS_DIR = os.path.join(FRONTEND_DIR, 'css')
JS_DIR = os.path.join(FRONTEND_DIR, 'js')
PUBLIC_DIR = FRONTEND_DIR
IMAGE_FOLDER = os.path.normpath(os.path.join(BASE_DIR, '..', 'data', 'images'))
JP_IMAGE_FOLDER = os.path.normpath(os.path.join(BASE_DIR, '..', 'data', 'images_jp'))
DECK_JSON_EXPORT_DIR = os.path.join(ROOT_DIR, 'data', 'deck_json_exports')

if not os.path.exists(DECK_JSON_EXPORT_DIR):
    os.makedirs(DECK_JSON_EXPORT_DIR)

# ── PostgreSQL 資料庫 ──
DATABASE_URL = os.environ.get('DATABASE_URL',
    'postgresql://ptcg:ptcg_secret@localhost:5432/ptcg_db')

# ── Flask 安全 ──
# 不再提供開發默認值：未設置或仍為佔位符時直接拒絕啟動，
# 避免生產環境意外使用可預測密鑰簽發 session cookie。
def _require_secret(name: str, forbidden: tuple) -> str:
    value = os.environ.get(name) or ''
    if not value or any(token in value for token in forbidden):
        raise RuntimeError(
            f'{name} 未設置或仍為開發佔位值。請在 .env 設置一組隨機密鑰後再啟動。'
        )
    return value

SECRET_KEY = _require_secret('SECRET_KEY', ('change-this', 'dev-secret-key'))
SECURITY_PASSWORD_SALT = _require_secret('SECURITY_PASSWORD_SALT', ('change-this', 'my-precious-salt'))
FLASK_DEBUG = _env_bool('FLASK_DEBUG', False)
SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', False)
SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'https')
SERVER_NAME = os.environ.get('SERVER_NAME') or None
ENABLE_JP_DECK_AUTO_UPDATE = _env_bool('ENABLE_JP_DECK_AUTO_UPDATE', True)
JP_DECK_AUTO_UPDATE_INTERVAL_SECONDS = _env_int('JP_DECK_AUTO_UPDATE_INTERVAL_SECONDS', 86400)
JP_DECK_AUTO_UPDATE_WORKERS = max(1, _env_int('JP_DECK_AUTO_UPDATE_WORKERS', 3))
ENABLE_LIMITLESS_AUTO_UPDATE = _env_bool('ENABLE_LIMITLESS_AUTO_UPDATE', True)
LIMITLESS_AUTO_UPDATE_INTERVAL_SECONDS = _env_int('LIMITLESS_AUTO_UPDATE_INTERVAL_SECONDS', 86400)
LIMITLESS_AUTO_UPDATE_REGIONS = _env_csv('LIMITLESS_AUTO_UPDATE_REGIONS', ['global', 'jp'])
LIMITLESS_AUTO_UPDATE_STALE_HOURS = _env_int('LIMITLESS_AUTO_UPDATE_STALE_HOURS', 20)
LIMITLESS_AUTO_UPDATE_MAX_INDEX_PAGES_PER_REGION = _env_optional_int('LIMITLESS_AUTO_UPDATE_MAX_INDEX_PAGES_PER_REGION', 1)
LIMITLESS_AUTO_UPDATE_MAX_TOURNAMENTS_PER_REGION = _env_optional_int('LIMITLESS_AUTO_UPDATE_MAX_TOURNAMENTS_PER_REGION', 20)
LIMITLESS_AUTO_UPDATE_MAX_DECKS = _env_optional_int('LIMITLESS_AUTO_UPDATE_MAX_DECKS', None)
LIMITLESS_AUTO_UPDATE_INCLUDE_BLING = _env_bool('LIMITLESS_AUTO_UPDATE_INCLUDE_BLING', False)
DECK_AUTO_UPDATE_INITIAL_DELAY_SECONDS = _env_int('DECK_AUTO_UPDATE_INITIAL_DELAY_SECONDS', 30)

# ── 日本牌庫（ptcgtw.shop 牌組）輪轉增量缺漏偵測 ──
ENABLE_JP_DECK_GAP_FILL = _env_bool('ENABLE_JP_DECK_GAP_FILL', True)
JP_DECK_GAP_FILL_PAGES = _env_int('JP_DECK_GAP_FILL_PAGES', 10)
# 詳情 API 增量策略：卡片資料缺失或超過 N 天未更新才重抓（避免打爆來源站）
JP_DECK_DETAIL_REFRESH_DAYS = _env_int('JP_DECK_DETAIL_REFRESH_DAYS', 30)
# 單次更新最多抓多少份牌組詳情（詳情為逐筆 API 請求，需設上限）
JP_DECK_DETAIL_FETCH_CAP = _env_int('JP_DECK_DETAIL_FETCH_CAP', 500)
# 自動更新服務等待背景任務完成的整體超時（秒）；逾時不再等，避免循環卡死
AUTO_UPDATE_MAX_WAIT_SECONDS = _env_int('AUTO_UPDATE_MAX_WAIT_SECONDS', 7200)

# ── 單卡庫每日自動更新（cards / jp_cards）──
# 同步官網擴充包列表 + 偵測並增量抓取新系列，避免 admin 更新頁 dropdown 停滯在舊版本。
ENABLE_CARD_DB_AUTO_UPDATE = _env_bool('ENABLE_CARD_DB_AUTO_UPDATE', True)
CARD_DB_AUTO_UPDATE_INTERVAL_SECONDS = _env_int('CARD_DB_AUTO_UPDATE_INTERVAL_SECONDS', 86400)
CARD_DB_AUTO_UPDATE_INITIAL_DELAY_SECONDS = _env_int('CARD_DB_AUTO_UPDATE_INITIAL_DELAY_SECONDS', 60)
# 每次同步最多抓多少個「尚未收錄任何卡牌」的新系列（避免一次跑太多打爆來源站）
CARD_DB_AUTO_UPDATE_MAX_NEW_SETS = max(1, _env_int('CARD_DB_AUTO_UPDATE_MAX_NEW_SETS', 3))
CARD_DB_AUTO_UPDATE_MAX_NEW_JP_SETS = max(1, _env_int('CARD_DB_AUTO_UPDATE_MAX_NEW_JP_SETS', 3))
# 每日同步預設不下載卡圖（圖檔通常已快取），節省頻寬；admin 手動更新可另外選擇
CARD_DB_AUTO_UPDATE_SKIP_IMAGES = _env_bool('CARD_DB_AUTO_UPDATE_SKIP_IMAGES', True)
# /api/crawler/expansions 快取時間（秒）：超過則自動重新同步官網 dropdown 列表
EXPANSION_META_RESYNC_INTERVAL_SECONDS = _env_int('EXPANSION_META_RESYNC_INTERVAL_SECONDS', 86400)

# ── 每日備份（僅用戶資料，容器內排程，UTC 04:17）──
ENABLE_USER_BACKUP = _env_bool('ENABLE_USER_BACKUP', True)
AI_BASE_URL = os.environ.get('AI_BASE_URL') or 'https://api.openai.com/v1'
AI_API_KEY = os.environ.get('AI_API_KEY') or ''
AI_MODEL = os.environ.get('AI_MODEL') or ''
AI_EMBEDDING_MODEL = os.environ.get('AI_EMBEDDING_MODEL') or 'text-embedding-3-small'
AI_EMBEDDING_DIMENSIONS = int(os.environ.get('AI_EMBEDDING_DIMENSIONS') or 1536)
AI_TIMEOUT = _env_int('AI_TIMEOUT', 45)

# ── 效果角色標籤（card_roles）──
CARD_ROLE_BATCH_SIZE = _env_int('CARD_ROLE_BATCH_SIZE', 200)      # 每批標註張數（LLM 成本控制）
CARD_ROLE_AUTO_APPROVE_CONFIDENCE = float(os.environ.get('CARD_ROLE_AUTO_APPROVE_CONFIDENCE') or 0.9)
CARD_ROLE_MAX_RETRIES = _env_int('CARD_ROLE_MAX_RETRIES', 3)      # 每卡最多 LLM 呼叫次數

# ── SMTP ──
MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
MAIL_PORT = _env_int('MAIL_PORT', 587)
MAIL_USE_TLS = _env_bool('MAIL_USE_TLS', True)
MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
MAIL_DEFAULT_SENDER = os.environ.get('MAIL_DEFAULT_SENDER')

# ── Server Meta ──
META_FILE_PATH = os.path.join(BASE_DIR, 'server_meta.json')

# ── 爬蟲 ──
BASE_URL = "https://asia.pokemon-card.com"
DEFAULT_LIST_URL = "https://asia.pokemon-card.com/tw/card-search/list/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
}
