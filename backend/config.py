import os
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.normpath(os.path.join(BASE_DIR, '..'))
load_dotenv(os.path.join(ROOT_DIR, '.env'))

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
FLASK_DEBUG = os.environ.get('FLASK_DEBUG', 'false').lower() in ['true', 'on', '1']
SESSION_COOKIE_SECURE = os.environ.get('SESSION_COOKIE_SECURE', 'false').lower() in ['true', 'on', '1']
SESSION_COOKIE_SAMESITE = os.environ.get('SESSION_COOKIE_SAMESITE', 'Lax')
PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'https')
SERVER_NAME = os.environ.get('SERVER_NAME') or None
ENABLE_DECK_UPDATER = os.environ.get('ENABLE_DECK_UPDATER', 'true').lower() in ['true', 'on', '1']
AI_BASE_URL = os.environ.get('AI_BASE_URL') or 'https://api.openai.com/v1'
AI_API_KEY = os.environ.get('AI_API_KEY') or ''
AI_MODEL = os.environ.get('AI_MODEL') or ''
AI_EMBEDDING_MODEL = os.environ.get('AI_EMBEDDING_MODEL') or 'text-embedding-3-small'
AI_EMBEDDING_DIMENSIONS = int(os.environ.get('AI_EMBEDDING_DIMENSIONS') or 1536)
AI_TIMEOUT = int(os.environ.get('AI_TIMEOUT') or 45)

# ── SMTP ──
MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
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
