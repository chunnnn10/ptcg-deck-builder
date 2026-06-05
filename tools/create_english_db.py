import sqlite3
import os

# === 設定資料庫路徑 ===
# 建議將新資料庫放在與中文資料庫相同的目錄下 (例如 'Deck' 資料夾)
# 如果你的專案結構不同，請修改這裡
ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
DB_FOLDER = os.path.join(ROOT_DIR, 'data')
DB_NAME = 'english_card_database.db'

def create_english_database():
    # 1. 確保目錄存在
    if not os.path.exists(DB_FOLDER):
        try:
            os.makedirs(DB_FOLDER)
            print(f"📁 建立目錄: {DB_FOLDER}")
        except OSError as e:
            # 如果是在根目錄執行且不需要 Deck 資料夾，可以忽略
            print(f"⚠️ 無法建立目錄 (可能已存在或無權限)，嘗試在當前路徑建立: {e}")

    db_path = os.path.join(DB_FOLDER, DB_NAME)
    
    # 如果目錄建立失敗，直接建立在當前目錄
    if not os.path.exists(DB_FOLDER):
        db_path = DB_NAME

    print(f"🚀 正在建立/連接資料庫: {db_path} ...")

    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()

        # === 定義 Schema (結構) ===
        # 這完全參照你的 DATABASE_STRUCTURE.md 中的 cards 表格
        # 我們保持欄位名稱一致，方便日後維護
        create_table_sql = """
        CREATE TABLE IF NOT EXISTS cards (
            -- 核心識別
            card_id TEXT PRIMARY KEY,       -- 唯一識別碼
            
            -- 基本卡片資訊
            image_file TEXT,                -- 圖片檔名
            card_type TEXT,                 -- 卡片類型 (Pokemon/Trainer/Energy)
            name TEXT,                      -- 卡片名稱 (這裡是存英文名)
            sub_type TEXT,                  -- 子類型 (Basic/Stage 1/Item...)
            hp INTEGER,                     -- 血量
            element_type TEXT,              -- 屬性
            
            -- 戰鬥數值
            weakness_type TEXT,             -- 弱點屬性
            weakness_value TEXT,            -- 弱點數值
            resistance_type TEXT,           -- 抗性屬性
            resistance_value TEXT,          -- 抗性數值
            retreat_cost INTEGER,           -- 撤退費用
            
            -- 技能與邏輯
            skills_json TEXT,               -- 技能組 (JSON string)
            rarity TEXT,                    -- 稀有度
            processing_status INTEGER DEFAULT 0, -- 處理狀態
            
            -- 跨系統對照欄位 (Bridge/Crawler)
            english_id TEXT,                -- 官方英文 ID (如 sv3pt5-1)
            set_code TEXT,                  -- 系列代號 (如 MC)
            set_number TEXT,                -- 系列編號 (如 001)
            english_name TEXT,              -- 英文名稱 (備用，通常與 name 相同)
            japanese_name TEXT,             -- 對應的日文名稱
            jp_id TEXT,                     -- PTCGSP 的 ID
            evolution_stage TEXT,           -- 進化階段
            evolves_from TEXT               -- 從哪隻進化
        );
        """

        # 執行 SQL
        cursor.execute(create_table_sql)
        conn.commit()

        print("✅ 資料表 'cards' 建立成功！")

        # 檢查一下是否建立成功
        cursor.execute("PRAGMA table_info(cards);")
        columns = cursor.fetchall()
        print(f"📊 目前欄位數: {len(columns)}")
        # for col in columns:
        #     print(f"  - {col[1]} ({col[2]})")

    except sqlite3.Error as e:
        print(f"❌ SQLite 錯誤: {e}")
    finally:
        if conn:
            conn.close()
            print("🔒 資料庫連線已關閉")

if __name__ == "__main__":
    create_english_database()