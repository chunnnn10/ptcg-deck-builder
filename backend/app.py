import sys
import os
import time
from flask import Flask
from flask_login import LoginManager
import config
import database
from routes import main_bp
from models import User
import threading

app = Flask(__name__, static_folder='../frontend', template_folder='../frontend/html')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024

app.secret_key = config.SECRET_KEY
app.config['SESSION_COOKIE_HTTPONLY'] = True
app.config['SESSION_COOKIE_SAMESITE'] = config.SESSION_COOKIE_SAMESITE
app.config['SESSION_COOKIE_SECURE'] = config.SESSION_COOKIE_SECURE
app.config['PREFERRED_URL_SCHEME'] = config.PREFERRED_URL_SCHEME
if config.SERVER_NAME:
    app.config['SERVER_NAME'] = config.SERVER_NAME

login_manager = LoginManager()
login_manager.init_app(app)
login_manager.login_view = 'main.index'

# 將 backend/ 加入 sys.path 以便所有其他模組可用
backend_dir = os.path.dirname(os.path.abspath(__file__))
if backend_dir not in sys.path:
    sys.path.insert(0, backend_dir)

# 匯入 DeckImporter (從 services/deck_importer/)
from services.deck_importer.deck_importer import DeckImporter
print(f">>> 成功載入 DeckImporter")

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

def run_deck_updater_service():
    print(">>> [Deck Updater] 背景服務已啟動", flush=True)
    try:
        importer = DeckImporter()
    except Exception as e:
        print(f">>> [Deck Updater] 初始化失敗: {e}", flush=True)
        return

    while True:
        try:
            print(f">>> [Deck Updater] {time.strftime('%Y-%m-%d %H:%M:%S')} 開始每日檢查...", flush=True)
            new_decks = importer.crawl_smart_update(
                status_callback=lambda msg: print(f"[Deck Updater] {msg}")
            )
            if new_decks:
                print(f">>> [Deck Updater] 發現 {len(new_decks)} 個新牌組，開始處理...")
                for deck in new_decks:
                    importer.process_deck(deck)
                print(">>> [Deck Updater] 更新完成！", flush=True)
            else:
                print(">>> [Deck Updater] 無需更新。", flush=True)
        except Exception as e:
            print(f">>> [Deck Updater] 執行錯誤: {e}", flush=True)
        time.sleep(86400)

def start_updater_thread():
    if not config.ENABLE_DECK_UPDATER:
        print(">>> [Deck Updater] disabled by ENABLE_DECK_UPDATER", flush=True)
        return
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        updater_thread = threading.Thread(target=run_deck_updater_service, daemon=True)
        updater_thread.start()
    else:
        print(">>> [Deck Updater] 略過監控程序的背景任務啟動 (等待 Worker 啟動...)", flush=True)

database.init_db()
app.register_blueprint(main_bp)
start_updater_thread()

if __name__ == '__main__':
    app.run(debug=config.FLASK_DEBUG, port=5000, host='0.0.0.0')
