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

@login_manager.user_loader
def load_user(user_id):
    return User.get(user_id)

def _should_start_background_workers():
    return not config.FLASK_DEBUG or os.environ.get('WERKZEUG_RUN_MAIN') == 'true'


def _sleep_interval(seconds, minimum=60):
    try:
        seconds = int(seconds)
    except (TypeError, ValueError):
        seconds = minimum
    time.sleep(max(minimum, seconds))


def _initial_delay(extra_seconds=0):
    delay = max(0, int(config.DECK_AUTO_UPDATE_INITIAL_DELAY_SECONDS or 0) + extra_seconds)
    if delay:
        time.sleep(delay)


def _wait_for_async_update(get_status, service_name, poll_seconds=15):
    while True:
        try:
            status = get_status()
            if not status.get('running'):
                print(
                    f">>> [{service_name}] completed: {status.get('message', 'finished')}",
                    flush=True,
                )
                return
        except Exception as exc:
            print(f">>> [{service_name}] status check failed: {exc}", flush=True)
            return
        time.sleep(poll_seconds)


def run_jp_deck_auto_update_service():
    print(">>> [JP Deck Auto Update] background service enabled", flush=True)
    _initial_delay()

    while True:
        try:
            from services.deck_importer.deck_updater import get_update_status, run_daily_update

            print(
                f">>> [JP Deck Auto Update] {time.strftime('%Y-%m-%d %H:%M:%S')} starting daily sync",
                flush=True,
            )
            success, message = run_daily_update(worker_count=config.JP_DECK_AUTO_UPDATE_WORKERS)
            print(f">>> [JP Deck Auto Update] {message}", flush=True)
            if success:
                _wait_for_async_update(get_update_status, "JP Deck Auto Update", poll_seconds=15)
        except Exception as e:
            print(f">>> [JP Deck Auto Update] error: {e}", flush=True)
        _sleep_interval(config.JP_DECK_AUTO_UPDATE_INTERVAL_SECONDS)


def run_limitless_auto_update_service():
    print(">>> [Limitless Auto Update] background service enabled", flush=True)
    _initial_delay(extra_seconds=60)

    while True:
        try:
            from services.limitless_decks.updater import get_status, start_update

            options = {
                "mode": "auto-daily",
                "include_bling": config.LIMITLESS_AUTO_UPDATE_INCLUDE_BLING,
                "regions": config.LIMITLESS_AUTO_UPDATE_REGIONS,
                "stale_hours": config.LIMITLESS_AUTO_UPDATE_STALE_HOURS,
                "max_index_pages_per_region": config.LIMITLESS_AUTO_UPDATE_MAX_INDEX_PAGES_PER_REGION,
                "max_tournaments_per_region": config.LIMITLESS_AUTO_UPDATE_MAX_TOURNAMENTS_PER_REGION,
                "max_decks": config.LIMITLESS_AUTO_UPDATE_MAX_DECKS,
            }
            print(
                f">>> [Limitless Auto Update] {time.strftime('%Y-%m-%d %H:%M:%S')} starting daily sync",
                flush=True,
            )
            success, message = start_update(options)
            print(f">>> [Limitless Auto Update] {message}", flush=True)
            if success:
                _wait_for_async_update(get_status, "Limitless Auto Update", poll_seconds=30)
            else:
                status = get_status()
                if status.get('running'):
                    _wait_for_async_update(get_status, "Limitless Auto Update", poll_seconds=30)
        except Exception as e:
            print(f">>> [Limitless Auto Update] error: {e}", flush=True)
        _sleep_interval(config.LIMITLESS_AUTO_UPDATE_INTERVAL_SECONDS)


def start_background_update_threads():
    if not _should_start_background_workers():
        print(">>> [Auto Update] skipped in Flask reloader monitor process", flush=True)
        return

    if config.ENABLE_JP_DECK_AUTO_UPDATE:
        threading.Thread(target=run_jp_deck_auto_update_service, daemon=True).start()
    else:
        print(">>> [JP Deck Auto Update] disabled by ENABLE_JP_DECK_AUTO_UPDATE", flush=True)

    if config.ENABLE_LIMITLESS_AUTO_UPDATE:
        threading.Thread(target=run_limitless_auto_update_service, daemon=True).start()
    else:
        print(">>> [Limitless Auto Update] disabled by ENABLE_LIMITLESS_AUTO_UPDATE", flush=True)

database.init_db()
app.register_blueprint(main_bp)
start_background_update_threads()

if __name__ == '__main__':
    app.run(debug=config.FLASK_DEBUG, port=5000, host='0.0.0.0')
