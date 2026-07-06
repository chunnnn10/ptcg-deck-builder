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
SECRET_KEY = os.environ.get('SECRET_KEY') or 'dev-secret-key-change-this-in-prod-123456'
SECURITY_PASSWORD_SALT = os.environ.get('SECURITY_PASSWORD_SALT') or 'my-precious-salt'
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

# ── 日本牌庫（卡片 jp_cards）自動同步 ──
ENABLE_JP_CARD_AUTO_UPDATE = _env_bool('ENABLE_JP_CARD_AUTO_UPDATE', True)
JP_CARD_AUTO_UPDATE_INTERVAL_SECONDS = _env_int('JP_CARD_AUTO_UPDATE_INTERVAL_SECONDS', 86400)
JP_CARD_AUTO_UPDATE_WORKERS = max(1, _env_int('JP_CARD_AUTO_UPDATE_WORKERS', 20))
JP_CARD_AUTO_UPDATE_SKIP_IMAGES = _env_bool('JP_CARD_AUTO_UPDATE_SKIP_IMAGES', False)
JP_CARD_AUTO_UPDATE_MAX_MISSING_PER_RUN = _env_int('JP_CARD_AUTO_UPDATE_MAX_MISSING_PER_RUN', 2000)

AI_BASE_URL = os.environ.get('AI_BASE_URL') or 'https://api.openai.com/v1'
AI_API_KEY = os.environ.get('AI_API_KEY') or ''
AI_MODEL = os.environ.get('AI_MODEL') or ''
AI_EMBEDDING_MODEL = os.environ.get('AI_EMBEDDING_MODEL') or 'text-embedding-3-small'
AI_EMBEDDING_DIMENSIONS = int(os.environ.get('AI_EMBEDDING_DIMENSIONS') or 1536)
AI_TIMEOUT = _env_int('AI_TIMEOUT', 45)

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
