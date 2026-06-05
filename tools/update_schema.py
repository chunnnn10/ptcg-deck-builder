import sqlite3
import os

ROOT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
DATA_DIR = os.path.join(ROOT_DIR, 'data')
db_path = os.path.join(DATA_DIR, 'pokemon_card_database.db')
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

# Check if columns exist, if not add them
columns_to_add = [
    ("english_id", "TEXT"),
    ("set_code", "TEXT"),
    ("set_number", "TEXT"),
    ("english_name", "TEXT")
]

cursor.execute("PRAGMA table_info(cards)")
existing_columns = [col[1] for col in cursor.fetchall()]

for col_name, col_type in columns_to_add:
    if col_name not in existing_columns:
        print(f"Adding column: {col_name}")
        cursor.execute(f"ALTER TABLE cards ADD COLUMN {col_name} {col_type}")
    else:
        print(f"Column {col_name} already exists.")

conn.commit()
conn.close()
print("Database schema updated.")
