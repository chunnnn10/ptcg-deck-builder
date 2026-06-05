import sqlite3
import json
import requests
import imagehash
from PIL import Image
import os
import time
import sys
from dotenv import load_dotenv

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
load_dotenv(os.path.join(ROOT_DIR, '.env'))

DATA_DIR = os.path.join(ROOT_DIR, 'data')
DB_PATH = os.getenv("PATH_SOURCE_DB") or os.path.join(DATA_DIR, 'pokemon_card_database.db')
IMAGE_DIR = os.path.join(DATA_DIR, 'images')
TRANSLATION_FILE = os.path.join(DATA_DIR, 'pokemon_translations.json')
API_URL = "https://api.pokemontcg.io/v2/cards"

# Force UTF-8 for console output
sys.stdout.reconfigure(encoding='utf-8')

class FingerprintEngine:
    @staticmethod
    def get_numerical_fingerprint(card_row):
        hp = card_row[5] if card_row[5] is not None else 0
        element = card_row[6] if card_row[6] else 'None'
        retreat = card_row[11] if card_row[11] is not None else 0
        
        w_type = card_row[7] if card_row[7] else "None"
        w_val = card_row[8] if card_row[8] else ""
        if "2" in w_val: w_val = "x2"
        weakness = f"{w_type}-{w_val}"
        
        skills_data = []
        try:
            if card_row[12]:
                skills = json.loads(card_row[12])
                for skill in skills:
                    cost = sorted(skill.get('cost', []))
                    cost_str = "".join([c[0].upper() for c in cost])
                    damage = skill.get('damage', '0')
                    if not damage: damage = '0'
                    damage = damage.replace("+", "").replace("x", "").replace("-", "")
                    skills_data.append(f"C:{cost_str}_D:{damage}")
        except:
            pass
        skills_part = "_".join(skills_data)
        
        return f"HP{hp}_T{element}_R{retreat}_W{weakness}_SKILLS[{skills_part}]"

    @staticmethod
    def get_api_card_fingerprint(api_card):
        hp = api_card.get('hp', '0') # API returns string usually
        types = api_card.get('types', ['None'])
        element = types[0] if types else 'None'
        
        retreat = len(api_card.get('retreatCost', []))
        
        weaknesses = api_card.get('weaknesses', [])
        if weaknesses:
            w = weaknesses[0]
            w_val = w.get('value', '')
            w_type = w.get('type', 'None')
            # API value is "×2" (unicode multiply) or "x2"? 
            # Often it's "x2". Let's normalize.
            w_val = w_val.replace("×", "x")
            weakness = f"{w_type}-{w_val}"
        else:
            weakness = "None-"
            
        skills_data = []
        attacks = api_card.get('attacks', [])
        for atk in attacks:
            cost = sorted(atk.get('cost', []))
            cost_str = "".join([c[0].upper() for c in cost])
            damage = atk.get('damage', '0')
            if not damage: damage = '0'
            damage = damage.replace("+", "").replace("x", "").replace("-", "").replace("×", "")
            skills_data.append(f"C:{cost_str}_D:{damage}")
            
        skills_part = "_".join(skills_data)
        
        return f"HP{hp}_T{element}_R{retreat}_W{weakness}_SKILLS[{skills_part}]"

    @staticmethod
    def get_visual_fingerprint(image_path):
        try:
            if not os.path.exists(image_path):
                return None
            img = Image.open(image_path)
            return imagehash.phash(img)
        except Exception as e:
            return None

class BridgeManager:
    def __init__(self):
        self.conn = sqlite3.connect(DB_PATH)
        self.cursor = self.conn.cursor()
        with open(TRANSLATION_FILE, 'r', encoding='utf-8') as f:
            self.translations = json.load(f)
            
    def process_pokemon(self, limit=1):
        # Only process 1 for detailed debugging
        print(f"Processing up to {limit} Pokemon cards...")
        self.cursor.execute("SELECT * FROM cards WHERE card_type = 'Pokémon' AND english_id IS NULL LIMIT ?", (limit,))
        rows = self.cursor.fetchall()
        
        for row in rows:
            chinese_name = row[3]
            english_name = self.translations.get(chinese_name)
            
            if not english_name:
                print(f"Skip: No translation for {chinese_name}")
                continue
                
            print(f"Found translation: {chinese_name} -> {english_name}")
            
            local_fp = FingerprintEngine.get_numerical_fingerprint(row)
            print(f"Local FP: {local_fp}")
            
            try:
                # Add headers to avoid basic blocking
                headers = {'User-Agent': 'Mozilla/5.0'}
                resp = requests.get(API_URL, params={'q': f'name:"{english_name}"', 'pageSize': 10}, headers=headers)
                
                if resp.status_code != 200:
                    print(f"API Error {resp.status_code}: {resp.text}")
                    continue
                    
                data = resp.json()
                candidates = data.get('data', [])
                match_found = False
                
                print(f"Analyzing {len(candidates)} candidates for {english_name}...")
                
                for i, cand in enumerate(candidates):
                    api_fp = FingerprintEngine.get_api_card_fingerprint(cand)
                    if i < 3: # Print first 3 for debug
                        print(f"  Cand {i} ({cand['set']['id']}): {api_fp}")
                        
                    if local_fp == api_fp:
                        print(f"MATCH FOUND! {cand['id']}")
                        self.update_db(row[0], cand)
                        match_found = True
                        break 
                
                if not match_found:
                    print(f"No match found.")
                    
            except Exception as e:
                print(f"Exception: {e}")
            
            time.sleep(1) 

    def update_db(self, card_id, api_data):
        english_id = api_data['id']
        set_code = api_data['set']['id']
        set_number = api_data['number']
        english_name = api_data['name']
        
        self.cursor.execute("""
            UPDATE cards 
            SET english_id = ?, set_code = ?, set_number = ?, english_name = ?
            WHERE card_id = ?
        """, (english_id, set_code, set_number, english_name, card_id))
        self.conn.commit()

if __name__ == "__main__":
    manager = BridgeManager()
    manager.process_pokemon(limit=1)