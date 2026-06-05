import sqlite3
import os
import json

# ==========================================
# 設定
# ==========================================
# 資料庫路徑 (相對路徑，假設此腳本在 python/tools/ 下)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
ROOT_DIR = os.path.abspath(os.path.join(BASE_DIR, '..'))
DB_PATH = os.path.join(ROOT_DIR, 'data', 'pokemon_card_database.db')

# 定義要新增的金卡資料
# 格式: (ID, 名稱, 屬性, 圖片檔名)
# ID 建議從 90000 開始以避免衝突
NEW_CARDS = [
    (90001, "基本草能量 (UR)", "Grass", "custom_gold_grass.png"),
    (90002, "基本火能量 (UR)", "Fire", "custom_gold_fire.png"),
    (90003, "基本水能量 (UR)", "Water", "custom_gold_water.png"),
    (90004, "基本雷能量 (UR)", "Lightning", "custom_gold_lightning.png"),
    (90005, "基本超能量 (UR)", "Psychic", "custom_gold_psychic.png"),
    (90006, "基本鬥能量 (UR)", "Fighting", "custom_gold_fighting.png"),
    (90007, "基本惡能量 (UR)", "Darkness", "custom_gold_darkness.png"),
    (90008, "基本鋼能量 (UR)", "Metal", "custom_gold_metal.png"),
    (90009, "基本妖精能量 (UR)", "Fairy", "custom_gold_fairy.png"),

]

def add_cards():
    print(f"正在連接資料庫: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("❌ 找不到資料庫！請確認路徑。")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("開始新增卡片...")
    
    for card_id, name, elem_type, img_file in NEW_CARDS:
        try:
            # 準備插入的資料
            # 注意：這裏假設你的 table schema 包含這些欄位
            # 如果你的 cards 表沒有 id 欄位作為主鍵，可能需要調整 SQL
            # 這裡我們假設 card_id 對應原本的 id 或 rowid 邏輯，
            # 但為了安全，我們讓 SQLite 自動處理主鍵，我們只插入內容。
            
            # 檢查是否已存在 (避免重複插入)
            cursor.execute("SELECT name FROM cards WHERE image_file = ?", (img_file,))
            if cursor.fetchone():
                print(f"⚠️ 跳過 (已存在): {name}")
                continue

            sql = """
                INSERT INTO cards (
                    name, 
                    card_type, 
                    sub_type, 
                    hp, 
                    element_type, 
                    image_file, 
                    skills_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
            """
            
            # 執行插入
            # HP = 0 或 None, 技能 = 空陣列 []
            cursor.execute(sql, (
                name, 
                "Energy",  # card_type
                "Basic",   # sub_type
                None,      # hp
                elem_type, # element_type
                img_file,  # image_file
                "[]"       # skills_json
            ))
            
            print(f"✅ 成功新增: {name} -> {img_file}")

        except Exception as e:
            print(f"❌ 新增失敗 {name}: {e}")

    conn.commit()
    conn.close()
    print("完成！現在你可以去網頁搜尋 'UR' 或 '能量' 試試看了。")

if __name__ == "__main__":
    add_cards()