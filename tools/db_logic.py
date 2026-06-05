import sqlite3
import json
import os
import threading
import time
from dotenv import load_dotenv
from api_manager import APIManager

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
load_dotenv(os.path.join(ROOT_DIR, '.env'))

DATA_DIR = os.path.join(ROOT_DIR, 'data')
SRC_DB = os.getenv("PATH_SOURCE_DB") or os.path.join(DATA_DIR, 'pokemon_card_database.db')
TOOL_DB = os.getenv("PATH_TOOL_DB") or os.path.join(DATA_DIR, 'ptcg_ai_tool.db')
MASTER_SCHEMA_PATH = os.path.join(DATA_DIR, 'master_schema.json')

class DBLogic:
    _schema_lock = threading.Lock() 

    def __init__(self):
        self.api = APIManager(TOOL_DB)
        self.init_tool_db()
        self.reload_schema()

    def reload_schema(self):
        try:
            with open(MASTER_SCHEMA_PATH, "r", encoding="utf-8") as f:
                self.current_schema = json.load(f)
        except:
            self.current_schema = {"definitions": {}, "known_actions": []}

    def init_tool_db(self):
        conn = sqlite3.connect(TOOL_DB)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS processed_cards (
                card_id TEXT PRIMARY KEY,
                card_name TEXT,
                original_text TEXT,
                logic_json TEXT,
                status TEXT, 
                attempts INTEGER DEFAULT 0,
                last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        c.execute('''
            CREATE TABLE IF NOT EXISTS schema_changes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_id TEXT,
                change_json TEXT,
                reason TEXT,
                status TEXT, 
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def get_unprocessed_card(self):
        conn_tool = sqlite3.connect(TOOL_DB)
        cursor_tool = conn_tool.cursor()
        
        processed_ids = {row[0] for row in cursor_tool.execute(
            "SELECT card_id FROM processed_cards WHERE status IN ('APPROVED', 'QUARANTINE', 'PROCESSING')"
        )}
        
        # 嘗試連接來源資料庫
        if not os.path.exists(SRC_DB):
            conn_tool.close()
            return None # 檔案不存在

        conn_src = sqlite3.connect(SRC_DB)
        conn_src.row_factory = sqlite3.Row
        cursor_src = conn_src.cursor()
        
        # 這裡可能會因為 table 不存在報錯，worker 會捕捉到
        try:
            query = "SELECT card_id, name, skills_json FROM cards WHERE skills_json IS NOT NULL AND skills_json != ''"
            cursor_src.execute(query)
            
            target_card = None
            for row in cursor_src:
                if str(row['card_id']) not in processed_ids:
                    target_card = dict(row)
                    break
        except Exception:
            conn_src.close()
            conn_tool.close()
            raise Exception("Table 'cards' missing in source DB")

        conn_src.close()
        
        if target_card:
            try:
                cursor_tool.execute('''
                    INSERT INTO processed_cards (card_id, card_name, original_text, logic_json, status, attempts)
                    VALUES (?, ?, ?, ?, ?, ?)
                ''', (str(target_card['card_id']), target_card['name'], target_card['skills_json'], "{}", "PROCESSING", 0))
                conn_tool.commit()
            except sqlite3.IntegrityError:
                conn_tool.close()
                return None

        conn_tool.close()
        return target_card

    def save_result(self, card_id, name, orig_text, logic, status, attempts):
        conn = sqlite3.connect(TOOL_DB)
        c = conn.cursor()
        c.execute('''
            INSERT OR REPLACE INTO processed_cards (card_id, card_name, original_text, logic_json, status, attempts)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (str(card_id), name, orig_text, json.dumps(logic, ensure_ascii=False), status, attempts))
        conn.commit()
        conn.close()

    def auto_merge_schema(self, card_id, patch, reason):
        with DBLogic._schema_lock:
            try:
                with open("master_schema.json", "r", encoding="utf-8") as f:
                    schema = json.load(f)
                
                if isinstance(patch, dict):
                    if "action_name" in patch:
                        action = patch["action_name"]
                        if "known_actions" not in schema: schema["known_actions"] = []
                        if action not in schema["known_actions"]:
                            schema["known_actions"].append(action)
                    
                    if "definition" in patch and "definition_name" in patch:
                         if "definitions" not in schema: schema["definitions"] = {}
                         schema["definitions"][patch["definition_name"]] = patch["definition"]

                with open("master_schema.json", "w", encoding="utf-8") as f:
                    json.dump(schema, f, indent=2, ensure_ascii=False)
                
                conn = sqlite3.connect(TOOL_DB)
                conn.execute("INSERT INTO schema_changes (card_id, change_json, reason, status) VALUES (?,?,?,?)",
                             (str(card_id), json.dumps(patch, ensure_ascii=False), reason, "AUTO_MERGED"))
                conn.commit()
                conn.close()
                
                self.current_schema = schema
                return True
                
            except Exception as e:
                print(f"[Schema Merge Error] {e}")
                return False

    # --- AI 邏輯 ---

    def generate_logic(self, card_data):
        self.reload_schema() 
        schema_str = json.dumps(self.current_schema.get('known_actions', []), ensure_ascii=False)
        
        prompt = f"""
        Role: Architect AI (Pokemon TCG)
        Task: Convert text to JSON Logic.
        
        Existing Actions: {schema_str}
        
        Card: {card_data['name']}
        Text: {card_data['skills_json']}

        Rules:
        1. Use existing actions if possible.
        2. If IMPOSSIBLE, propose a NEW action in 'schema_patch'.
        3. 'schema_patch' format: {{ "action_name": "NEW_ACTION_NAME", "definition_name": "optional_def_key", "definition": {{...}} }}
        
        Output JSON:
        {{
            "logic": {{...}},
            "schema_patch": ...|null,
            "reasoning": "繁體中文解釋"
        }}
        """
        
        response = self.api.chat_completion(
            [{"role": "user", "content": prompt}],
            provider_priority=["gomodel", "openrouter"]
        )
        return response, prompt

    def validate_logic(self, card_data, generated_result):
        prompt = f"""
        Role: Lead Developer AI (Validator)
        Task: Audit the proposed JSON and Schema Patch.
        
        Card: {card_data['name']}
        Original Text: {card_data['skills_json']}
        
        Proposed Logic: {json.dumps(generated_result.get('logic'), ensure_ascii=False)}
        Proposed Patch: {json.dumps(generated_result.get('schema_patch'), ensure_ascii=False)}
        
        Critical Checks:
        1. Does the logic match the card text?
        2. IF PATCH EXISTS: Is it generic enough? Is it safe to merge into master_schema?
        3. If you approve, the patch will be AUTO-MERGED. Be careful.

        Output JSON:
        {{
            "approved": boolean,
            "rejection_reason": "繁體中文 (if rejected)",
            "severity": "MINOR|CRITICAL"
        }}
        """
        
        response = self.api.chat_completion(
            [{"role": "user", "content": prompt}],
            provider_priority=["gomodel", "openrouter"]
        )
        return response, prompt

# Worker (改進版：更清楚的狀態回報)
def worker_thread(thread_name, log_queue, status_dict, debug_info):
    logic_engine = DBLogic()
    log_queue.put(f"[{thread_name}] 🟢 Thread Started")
    
    idle_counter = 0

    while True:
        try:
            card = logic_engine.get_unprocessed_card()
            
            if not card:
                # 為了避免 Log 洗版，我們只在剛進入 Idle 時通知一次
                if "Idle" not in status_dict[thread_name]:
                    msg = "Idle (Source DB empty or all Done)"
                    status_dict[thread_name] = msg
                    log_queue.put(f"[{thread_name}] 💤 {msg}")
                else:
                    status_dict[thread_name] = "Idle (Waiting for cards...)"
                
                time.sleep(5) # 沒事做就睡久一點
                continue
            
            # Reset idle status
            idle_counter = 0
            card_name = card['name']
            card_id = card['card_id']
            status_dict[thread_name] = f"Processing: {card_name}"
            
            attempts = 0
            max_retries = 2
            
            while attempts < max_retries:
                attempts += 1
                
                # Gen
                log_queue.put(f"[{thread_name}] 🏗️ Gen {card_name}...")
                debug_info[thread_name]["action"] = "Generating"
                debug_info[thread_name]["prompt"] = "Generating..."
                
                gen_res, gen_prompt = logic_engine.generate_logic(card)
                
                debug_info[thread_name]["prompt"] = gen_prompt
                debug_info[thread_name]["output"] = gen_res
                
                # Val
                log_queue.put(f"[{thread_name}] 🔍 Val {card_name}...")
                debug_info[thread_name]["action"] = "Validating"
                
                val_res, val_prompt = logic_engine.validate_logic(card, gen_res)
                
                debug_info[thread_name]["prompt"] = val_prompt
                debug_info[thread_name]["output"] = val_res # 這裡會覆蓋 Gen 的輸出，顯示 Val 的結果
                
                if val_res.get('approved'):
                    # --- 自動合併邏輯 ---
                    patch = gen_res.get('schema_patch')
                    if patch:
                        log_queue.put(f"[{thread_name}] ⚡ Auto-Merging Schema Patch...")
                        success = logic_engine.auto_merge_schema(card_id, patch, gen_res.get('reasoning'))
                        if success:
                            log_queue.put(f"[{thread_name}] ✅ Schema Updated!")
                        else:
                            log_queue.put(f"[{thread_name}] ⚠️ Merge Failed (File Error)")
                    
                    logic_engine.save_result(card_id, card_name, card['skills_json'], gen_res['logic'], "APPROVED", attempts)
                    log_queue.put(f"[{thread_name}] 🎉 Finished: {card_name}")
                    break
                else:
                    log_queue.put(f"[{thread_name}] ❌ Rejected: {val_res.get('rejection_reason')}")
            
            if not val_res.get('approved'):
                logic_engine.save_result(card_id, card_name, card['skills_json'], {}, "QUARANTINE", attempts)
                log_queue.put(f"[{thread_name}] -> Quarantine")

        except Exception as e:
            status_dict[thread_name] = "Error"
            log_queue.put(f"[{thread_name}] 🔥 Error: {str(e)}")
            time.sleep(5)