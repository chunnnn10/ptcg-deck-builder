import os
import time
import json
import random
import sqlite3
import threading
from collections import deque
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
load_dotenv(os.path.join(ROOT_DIR, '.env'))

DATA_DIR = os.path.join(ROOT_DIR, 'data')
DEFAULT_TOOL_DB = os.path.join(DATA_DIR, 'ptcg_ai_tool.db')

class APIManager:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            with cls._lock:
                if not cls._instance:
                    cls._instance = super(APIManager, cls).__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, db_path=None):
        if self._initialized:
            return
            
        self.db_path = db_path or os.getenv("PATH_TOOL_DB") or DEFAULT_TOOL_DB
        self.blacklist = {} 
        
        # 1. GoModel (Priority 1)
        self.gomodel_key = os.getenv("GOMODEL_KEY")
        self.gomodel_base = os.getenv("GOMODEL_BASE_URL", "https://api.go-model.com/v1")
        self.gomodel_lock = threading.Lock()
        self.gomodel_history = deque()
        
        # 2. OpenRouter (Priority 2)
        self.or_keys = self._load_keys("OPENROUTER_KEY")
        self.or_base = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

        self._init_log_db()
        self._initialized = True
        print(f"[API Manager] Init: GoModel, OpenRouter({len(self.or_keys)})")

    def _load_keys(self, prefix):
        keys = []
        for k, v in os.environ.items():
            if k.startswith(prefix) and v and len(v) > 5:
                keys.append(v)
        return keys

    def _init_log_db(self):
        conn = sqlite3.connect(self.db_path)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS api_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                provider TEXT,
                model TEXT,
                status TEXT,
                duration REAL,
                tokens_in INTEGER,
                tokens_out INTEGER,
                error_msg TEXT
            )
        ''')
        conn.commit()
        conn.close()

    def _check_gomodel_availability(self):
        if self.gomodel_lock.locked():
            return False, "Busy"
        
        now = time.time()
        while self.gomodel_history and self.gomodel_history[0] < now - 60:
            self.gomodel_history.popleft()
        
        if len(self.gomodel_history) >= 8:
            return False, "Rate Limit"
            
        return True, "OK"

    def _get_client_config(self, provider_type):
        if provider_type == "gomodel":
            return self.gomodel_key, self.gomodel_base

        candidates = []
        base_url = ""
        
        if provider_type == "openrouter":
            candidates = self.or_keys
            base_url = self.or_base
        
        valid_keys = []
        now = time.time()
        for k in candidates:
            if k in self.blacklist:
                if now > self.blacklist[k]:
                    del self.blacklist[k]
                    valid_keys.append(k)
            else:
                valid_keys.append(k)
        
        if not valid_keys:
            raise Exception(f"No valid keys for {provider_type}")
            
        return random.choice(valid_keys), base_url

    def chat_completion(self, messages, provider_priority=["gomodel", "openrouter"], model_override=None, temperature=0.3):
        last_error = None

        for provider in provider_priority:
            if provider == "gomodel":
                is_ready, reason = self._check_gomodel_availability()
                if not is_ready:
                    continue 
            
            try:
                api_key, base_url = self._get_client_config(provider)
                
                model = model_override
                if not model:
                    if provider == "gomodel": model = os.getenv("GOMODEL_MODEL_GEN")
                    elif provider == "openrouter": model = os.getenv("OPENROUTER_MODEL_GEN")

                lock_acquired = False
                if provider == "gomodel":
                    self.gomodel_lock.acquire()
                    lock_acquired = True
                    self.gomodel_history.append(time.time())

                try:
                    client = OpenAI(api_key=api_key, base_url=base_url)
                    start_time = time.time()
                    
                    response = client.chat.completions.create(
                        model=model,
                        messages=messages,
                        temperature=temperature,
                        response_format={"type": "json_object"}
                    )
                    
                    duration = time.time() - start_time
                    content = response.choices[0].message.content
                    usage = response.usage
                    
                    self._log(provider, model, "SUCCESS", duration, usage.prompt_tokens, usage.completion_tokens)
                    return json.loads(content)

                finally:
                    if lock_acquired:
                        self.gomodel_lock.release()

            except Exception as e:
                duration = time.time() - start_time if 'start_time' in locals() else 0
                error_msg = str(e)
                self._log(provider, model_override or "unknown", "FAIL", duration, 0, 0, error_msg)
                
                if provider != "gomodel" and ("429" in error_msg or "401" in error_msg):
                    if 'api_key' in locals():
                        self.blacklist[api_key] = time.time() + 3600

                last_error = e
                continue
        
        raise last_error

    def _log(self, provider, model, status, duration, t_in, t_out, error=""):
        try:
            conn = sqlite3.connect(self.db_path)
            c = conn.cursor()
            c.execute("INSERT INTO api_logs (provider, model, status, duration, tokens_in, tokens_out, error_msg) VALUES (?,?,?,?,?,?,?)",
                      (provider, model, status, duration, t_in, t_out, error))
            conn.commit()
            conn.close()
        except:
            pass