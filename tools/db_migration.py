import sqlite3
import os

# 設定資料庫路徑 (請根據您的實際路徑修改，這裡預設為 Deck 資料夾下)
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_PATH = os.path.join(ROOT_DIR, 'data', 'pokemon_card_database.db')

def upgrade_database():
    if not os.path.exists(DB_PATH):
        print(f"錯誤: 找不到資料庫檔案 {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"正在連線至資料庫: {DB_PATH}")

    # 要新增的欄位列表 (欄位名稱, 類型)
    new_columns = [
        ('japanese_name', 'TEXT'),      # 日文名稱 (來自 ptcgsp)
        ('evolution_stage', 'TEXT'),    # 進化階段 (基礎, 1階進化...) (來自官網)
        ('evolves_from', 'TEXT'),       # 由誰進化而來 (來自官網)
        ('set_code', 'TEXT'),           # 系列代號 (例如 S8) (來自官網，若已有則忽略)
        ('set_number', 'TEXT')          # 系列編號 (例如 001/100) (來自官網，若已有則忽略)
    ]

    for col_name, col_type in new_columns:
        try:
            # 嘗試新增欄位
            cursor.execute(f"ALTER TABLE cards ADD COLUMN {col_name} {col_type}")
            print(f"✅ 成功新增欄位: {col_name}")
        except sqlite3.OperationalError as e:
            # 如果欄位已存在，SQLite 會報錯，我們忽略它
            if "duplicate column name" in str(e):
                print(f"ℹ️  欄位已存在 (跳過): {col_name}")
            else:
                print(f"❌ 新增欄位失敗 {col_name}: {e}")

    conn.commit()
    conn.close()
    print("\n資料庫結構升級完成！")

if __name__ == '__main__':
    upgrade_database()