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
FONTS_DIR = os.path.join(FRONTEND_DIR, 'fonts')
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
# 密鑰驗證分兩層：
#   1) 佔位符（placeholder）檢查：任何模式都拒絕，避免以可預測密鑰簽發 session cookie。
#   2) 強度檢查（長度 / 不同字元數）：只在「非本地開發」強制；生產環境未達標直接拒絕啟動。
#
# 本地開發逃生門（生產環境請保持關閉）：
#   FLASK_DEBUG=true 或 PTCG_ALLOW_WEAK_SECRETS=1    → 放行短/低熵密鑰（僅本機測試）
#   FLASK_DEBUG=true 或 PTCG_ALLOW_INSECURE_COOKIES=1 → 放行非 Secure 的 session cookie
FLASK_DEBUG = _env_bool('FLASK_DEBUG', False)
ALLOW_WEAK_SECRETS = bool(FLASK_DEBUG) or _env_bool('PTCG_ALLOW_WEAK_SECRETS', False)
ALLOW_INSECURE_COOKIES = bool(FLASK_DEBUG) or _env_bool('PTCG_ALLOW_INSECURE_COOKIES', False)

MIN_SECRET_LENGTH = 32
MIN_SECRET_DISTINCT_CHARS = 16


def _require_secret(name: str, forbidden: tuple, allow_weak: bool = False,
                    min_length: int = MIN_SECRET_LENGTH,
                    min_distinct: int = MIN_SECRET_DISTINCT_CHARS) -> str:
    """讀取並驗證密鑰；不合格時 raise RuntimeError（import 階段 fail fast）。

    allow_weak=True 時只做佔位符檢查，跳過強度檢查（限本地開發/測試使用）。
    """
    value = (os.environ.get(name) or '').strip()
    if not value:
        raise RuntimeError(
            f'{name} 未設置。請在 .env 設置一組隨機密鑰（建議 >= {min_length} 字元）後再啟動。'
        )
    lowered = value.lower()
    for token in forbidden:
        if token in lowered:
            raise RuntimeError(
                f'{name} 仍為開發佔位值（包含 "{token}"）。請改用隨機密鑰後再啟動。'
            )
    if allow_weak:
        return value

    problems = []
    if len(value) < min_length:
        problems.append(f'長度 {len(value)} < {min_length}')
    distinct_chars = len(set(value))
    if distinct_chars < min_distinct:
        problems.append(f'不同字元數 {distinct_chars} < {min_distinct}')
    if problems:
        message = (
            f'{name} 強度不足（{"；".join(problems)}）。請改用隨機值，例如：'
            f'python -c "import secrets; print(secrets.token_urlsafe(48))"。'
        )
        # 預設只警告唔中止：生產環境可能已存在一組長度較短但已在用的密鑰，
        # 若在此 raise 會令整個容器啟動即崩潰（crash-loop），風險高於弱密鑰本身。
        # 需要嚴格模式時設定 PTCG_STRICT_SECRETS=1，屆時不合格即拒絕啟動。
        if _env_bool('PTCG_STRICT_SECRETS', False):
            raise RuntimeError(message + '（PTCG_STRICT_SECRETS=1 已啟用嚴格模式）')
        print(f'>>> [security] WARNING: {message}（已放行，建議盡快更換）', flush=True)
    return value


SECRET_KEY = _require_secret(
    'SECRET_KEY', ('change-this', 'dev-secret-key'), allow_weak=ALLOW_WEAK_SECRETS
)
SECURITY_PASSWORD_SALT = _require_secret(
    'SECURITY_PASSWORD_SALT', ('change-this', 'my-precious-salt'), allow_weak=ALLOW_WEAK_SECRETS
)

# Session cookie 安全：生產預設 Secure=True（cookie 只經 HTTPS 傳輸，避免明文竊取）。
# 只有 FLASK_DEBUG=true 或 PTCG_ALLOW_INSECURE_COOKIES=1 才允許降級；
# 生產模式下即使有人設 SESSION_COOKIE_SECURE=0 也不降級（明確以程式碼強制）。
if ALLOW_INSECURE_COOKIES:
    SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', False)
else:
    SESSION_COOKIE_SECURE = True

# SameSite 維持 Lax：跨站「導轉」（外部連結回到本站）仍會帶 cookie，
# 但跨站 POST / iframe 不會帶，是相容性與 CSRF 防護的平衡點（比 None 安全，比 Strict 相容）。
# 只接受合法值；且 Secure=False 時瀏覽器會丟棄 SameSite=None 的 cookie，故強制回退 Lax。
SESSION_COOKIE_SAMESITE = {
    'lax': 'Lax', 'strict': 'Strict', 'none': 'None',
}.get((os.environ.get('SESSION_COOKIE_SAMESITE') or 'Lax').strip().lower(), 'Lax')
if not SESSION_COOKIE_SECURE and SESSION_COOKIE_SAMESITE == 'None':
    SESSION_COOKIE_SAMESITE = 'Lax'

PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'https')

# ── 對外基底 URL（防 Host header injection）──
# APP_BASE_URL（建議，例如 https://ptcg.example.com）優先；
# 其次沿用 SERVER_NAME（Flask 舊寫法，只含 host[:port]，這裡自動補 scheme）。
# 未設定時回傳 None，呼叫方需自行決定 fallback（切勿直接信任 request.host）。
APP_BASE_URL = (os.environ.get('APP_BASE_URL') or '').strip() or None
SERVER_NAME = os.environ.get('SERVER_NAME') or None


def external_base_url():
    """回傳固定的對外基底 URL（無結尾斜線）；未設定時回傳 None。

    用於產生驗證信 / 重設密碼等對外連結，避免採用 request.host 造成
    Host header injection（攻擊者可控制連結指向的網域）。
    """
    for raw in (APP_BASE_URL, SERVER_NAME):
        if not raw:
            continue
        candidate = raw.strip().rstrip('/')
        if '://' in candidate:
            return candidate
        return f'{PREFERRED_URL_SCHEME or "https"}://{candidate}'
    return None
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
