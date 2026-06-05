import json
import uuid
import psycopg2
import psycopg2.extras
import config
from services.limitless_decks.schema import LIMITLESS_SCHEMA_SQL

# 使用 RealDictCursor 讓查詢結果可以像 dict 一樣用 column name 取值
def get_db_connection():
    try:
        conn = psycopg2.connect(config.DATABASE_URL)
        conn.cursor_factory = psycopg2.extras.RealDictCursor
        conn.autocommit = False
        return conn
    except Exception as e:
        print(f"DB Connection Error: {e}")
        return None

# ==========================================
# 卡牌查詢
# ==========================================

def get_card_logic(card_id):
    if not card_id:
        return None
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT logic_json FROM processed_cards WHERE card_id = %s", (card_id,))
        row = cursor.fetchone()
        if row and row['logic_json']:
            try:
                return json.loads(row['logic_json'])
            except Exception:
                pass
    except Exception:
        pass
    finally:
        conn.close()
    return None

# ==========================================
# 工作區
# ==========================================

def build_tree(items):
    item_map = {item['id']: {**item, 'children': []} for item in items}
    tree = []
    for item in items:
        node = item_map[item['id']]
        parent_id = item.get('parent_id')
        if parent_id and parent_id in item_map:
            item_map[parent_id]['children'].append(node)
        else:
            tree.append(node)
    return tree

def get_user_workspace_tree(user_id):
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, parent_id, item_type, content, sort_order, created_at, updated_at
            FROM user_workspace
            WHERE user_id = %s
            ORDER BY sort_order ASC, created_at ASC
        """, (user_id,))
        rows = cursor.fetchall()
        items = []
        for row in rows:
            item = {
                'id': row['id'],
                'name': row['name'],
                'parent_id': row['parent_id'],
                'type': row['item_type'],
                'sort_order': row['sort_order'],
                'created_at': str(row['created_at']) if row['created_at'] else None,
                'updated_at': str(row['updated_at']) if row['updated_at'] else None
            }
            if row['item_type'] == 'deck':
                try:
                    item['content'] = json.loads(row['content']) if row['content'] else []
                    item['card_count'] = len(item['content'])
                except Exception:
                    item['content'] = []
                    item['card_count'] = 0
            items.append(item)
        return build_tree(items)
    except Exception as e:
        print(f"Get workspace tree error: {e}")
        return []
    finally:
        conn.close()

def create_workspace_item(user_id, name, item_type, parent_id=None, content=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        item_id = str(uuid.uuid4())
        content_json = json.dumps(content or [], ensure_ascii=False)
        cursor.execute("""
            SELECT COALESCE(MAX(sort_order), -1) + 1 as next_order
            FROM user_workspace
            WHERE user_id = %s AND (parent_id = %s OR (parent_id IS NULL AND %s IS NULL))
        """, (user_id, parent_id, parent_id))
        next_order = cursor.fetchone()['next_order']
        cursor.execute("""
            INSERT INTO user_workspace (id, user_id, name, parent_id, item_type, content, sort_order)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (item_id, user_id, name, parent_id, item_type, content_json, next_order))
        conn.commit()
        return {
            'id': item_id, 'name': name, 'type': item_type,
            'parent_id': parent_id, 'content': content or [], 'sort_order': next_order
        }
    except Exception as e:
        conn.rollback()
        print(f"Create workspace item error: {e}")
        return None
    finally:
        conn.close()

def update_workspace_item(item_id, user_id, **kwargs):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM user_workspace WHERE id = %s AND user_id = %s", (item_id, user_id))
        if not cursor.fetchone():
            return False
        updates = []
        values = []
        if 'name' in kwargs:
            updates.append("name = %s")
            values.append(kwargs['name'])
        if 'content' in kwargs:
            updates.append("content = %s")
            values.append(json.dumps(kwargs['content'], ensure_ascii=False))
        if 'parent_id' in kwargs:
            updates.append("parent_id = %s")
            values.append(kwargs['parent_id'])
        if 'sort_order' in kwargs:
            updates.append("sort_order = %s")
            values.append(kwargs['sort_order'])
        if updates:
            updates.append("updated_at = CURRENT_TIMESTAMP")
            sql = f"UPDATE user_workspace SET {', '.join(updates)} WHERE id = %s AND user_id = %s"
            values.extend([item_id, user_id])
            cursor.execute(sql, values)
            conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Update workspace item error: {e}")
        return False
    finally:
        conn.close()

def delete_workspace_item(item_id, user_id):
    conn = get_db_connection()
    if not conn:
        return False
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, item_type FROM user_workspace WHERE id = %s AND user_id = %s", (item_id, user_id))
        if not cursor.fetchone():
            return False
        def _delete_children(pid):
            cursor.execute("SELECT id FROM user_workspace WHERE parent_id = %s AND user_id = %s", (pid, user_id))
            for child in cursor.fetchall():
                _delete_children(child['id'])
                cursor.execute("DELETE FROM user_workspace WHERE id = %s", (child['id'],))
        _delete_children(item_id)
        cursor.execute("DELETE FROM user_workspace WHERE id = %s AND user_id = %s", (item_id, user_id))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        print(f"Delete workspace item error: {e}")
        return False
    finally:
        conn.close()

def get_workspace_item(item_id, user_id):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, name, parent_id, item_type, content, sort_order, created_at, updated_at
            FROM user_workspace WHERE id = %s AND user_id = %s
        """, (item_id, user_id))
        row = cursor.fetchone()
        if not row:
            return None
        item = {
            'id': row['id'], 'name': row['name'], 'parent_id': row['parent_id'],
            'type': row['item_type'], 'sort_order': row['sort_order'],
            'created_at': str(row['created_at']) if row['created_at'] else None,
            'updated_at': str(row['updated_at']) if row['updated_at'] else None
        }
        if row['item_type'] == 'deck':
            try:
                item['content'] = json.loads(row['content']) if row['content'] else []
            except Exception:
                item['content'] = []
        return item
    except Exception as e:
        print(f"Get workspace item error: {e}")
        return None
    finally:
        conn.close()

# ==========================================
# 初始化 (delegates to init_db.py for schema)
# ==========================================

def _workspace_card_count(content):
    if isinstance(content, list):
        return len(content)
    return 0

def _workspace_timeline_row(row):
    if not row:
        return None
    return {
        'id': row['id'],
        'item_id': row['item_id'],
        'action': row['action'],
        'source': row['source'],
        'card_count': row['card_count'] or 0,
        'created_at': str(row['created_at']) if row['created_at'] else None,
    }

def _insert_workspace_timeline(cursor, item_id, user_id, action, source, content):
    timeline_id = str(uuid.uuid4())
    content = content if isinstance(content, list) else []
    content_json = json.dumps(content, ensure_ascii=False)
    cursor.execute("""
        INSERT INTO user_workspace_timeline
            (id, item_id, user_id, action, source, content_json, card_count)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        RETURNING id, item_id, action, source, card_count, created_at
    """, (
        timeline_id,
        item_id,
        user_id,
        (action or '編輯牌組')[:120],
        (source or 'editor')[:80],
        content_json,
        _workspace_card_count(content),
    ))
    row = cursor.fetchone()
    cursor.execute("""
        DELETE FROM user_workspace_timeline
        WHERE item_id = %s
          AND user_id = %s
          AND id NOT IN (
              SELECT id
              FROM user_workspace_timeline
              WHERE item_id = %s AND user_id = %s
              ORDER BY created_at DESC
              LIMIT 50
          )
    """, (item_id, user_id, item_id, user_id))
    return _workspace_timeline_row(row)

def create_workspace_timeline(item_id, user_id, action='編輯牌組', source='editor', content=None):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, item_type, content
            FROM user_workspace
            WHERE id = %s AND user_id = %s
        """, (item_id, user_id))
        item = cursor.fetchone()
        if not item or item['item_type'] != 'deck':
            return None
        if content is None:
            try:
                content = json.loads(item['content']) if item['content'] else []
            except Exception:
                content = []
        timeline = _insert_workspace_timeline(cursor, item_id, user_id, action, source, content)
        conn.commit()
        return timeline
    except Exception as e:
        conn.rollback()
        print(f"Create workspace timeline error: {e}")
        return None
    finally:
        conn.close()

def get_workspace_timeline(item_id, user_id, limit=50):
    conn = get_db_connection()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id
            FROM user_workspace
            WHERE id = %s AND user_id = %s AND item_type = 'deck'
        """, (item_id, user_id))
        if not cursor.fetchone():
            return []
        cursor.execute("""
            SELECT id, item_id, action, source, card_count, created_at
            FROM user_workspace_timeline
            WHERE item_id = %s AND user_id = %s
            ORDER BY created_at DESC
            LIMIT %s
        """, (item_id, user_id, min(int(limit or 50), 50)))
        return [_workspace_timeline_row(row) for row in cursor.fetchall()]
    except Exception as e:
        print(f"Get workspace timeline error: {e}")
        return []
    finally:
        conn.close()

def restore_workspace_timeline(item_id, user_id, timeline_id):
    conn = get_db_connection()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT id, item_type
            FROM user_workspace
            WHERE id = %s AND user_id = %s
            FOR UPDATE
        """, (item_id, user_id))
        item = cursor.fetchone()
        if not item or item['item_type'] != 'deck':
            return None
        cursor.execute("""
            SELECT content_json, created_at
            FROM user_workspace_timeline
            WHERE id = %s AND item_id = %s AND user_id = %s
        """, (timeline_id, item_id, user_id))
        snapshot = cursor.fetchone()
        if not snapshot:
            return None
        try:
            content = json.loads(snapshot['content_json']) if snapshot['content_json'] else []
        except Exception:
            content = []
        content_json = json.dumps(content, ensure_ascii=False)
        cursor.execute("""
            UPDATE user_workspace
            SET content = %s, updated_at = CURRENT_TIMESTAMP
            WHERE id = %s AND user_id = %s
        """, (content_json, item_id, user_id))
        timeline = _insert_workspace_timeline(
            cursor,
            item_id,
            user_id,
            '還原版本',
            'timeline_restore',
            content,
        )
        conn.commit()
        return {'content': content, 'timeline': timeline}
    except Exception as e:
        conn.rollback()
        print(f"Restore workspace timeline error: {e}")
        return None
    finally:
        conn.close()

def init_db():
    """Called by app.py on startup — ensure tables exist."""
    conn = get_db_connection()
    if not conn:
        print("Cannot connect to DB for init.")
        return
    try:
        cursor = conn.cursor()

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS cards (
            card_id VARCHAR PRIMARY KEY,
            image_file TEXT,
            card_type VARCHAR NOT NULL CHECK (card_type IN ('Pokémon', 'Trainer', 'Energy')),
            name TEXT NOT NULL,
            sub_type VARCHAR,
            hp INTEGER,
            element_type VARCHAR,
            weakness_type VARCHAR,
            weakness_value VARCHAR,
            resistance_type VARCHAR,
            resistance_value VARCHAR,
            retreat_cost INTEGER,
            skills_json JSONB,
            rarity VARCHAR,
            processing_status INTEGER DEFAULT 0,
            japanese_name VARCHAR,
            evolution_stage VARCHAR,
            evolves_from VARCHAR,
            set_code VARCHAR,
            set_number VARCHAR,
            set_name VARCHAR,
            jp_id VARCHAR,
            regulation_flags VARCHAR,
            regulation_mark VARCHAR DEFAULT '',
            description TEXT DEFAULT '',
            flavor_text TEXT,
            pokedex_number VARCHAR,
            pokedex_category VARCHAR,
            height VARCHAR,
            weight VARCHAR
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_cards_set_code_number ON cards(set_code, set_number)
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS decks (
            id VARCHAR PRIMARY KEY,
            name TEXT,
            content TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            is_public INTEGER DEFAULT 0,
            user_id VARCHAR
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id VARCHAR PRIMARY KEY,
            username VARCHAR UNIQUE NOT NULL,
            email VARCHAR UNIQUE,
            password_hash VARCHAR NOT NULL,
            role VARCHAR DEFAULT 'user',
            is_verified INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_workspace (
            id VARCHAR PRIMARY KEY,
            user_id VARCHAR NOT NULL,
            name VARCHAR NOT NULL,
            parent_id VARCHAR DEFAULT NULL,
            item_type VARCHAR NOT NULL CHECK (item_type IN ('folder', 'deck')),
            content TEXT DEFAULT '[]',
            sort_order INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
        )
        """)
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS user_workspace_timeline (
            id VARCHAR PRIMARY KEY,
            item_id VARCHAR NOT NULL REFERENCES user_workspace(id) ON DELETE CASCADE,
            user_id VARCHAR NOT NULL,
            action TEXT,
            source TEXT,
            content_json TEXT NOT NULL,
            card_count INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_workspace_timeline_item_created
        ON user_workspace_timeline(item_id, created_at DESC)
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS expansion_sets (
            set_code VARCHAR PRIMARY KEY,
            set_name VARCHAR,
            series VARCHAR DEFAULT '',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS jp_cards (
            card_id VARCHAR PRIMARY KEY,
            image_file TEXT,
            card_type VARCHAR NOT NULL CHECK (card_type IN ('Pokémon', 'Trainer', 'Energy')),
            name TEXT NOT NULL,
            sub_type VARCHAR,
            hp INTEGER,
            element_type VARCHAR,
            weakness_type VARCHAR,
            weakness_value VARCHAR,
            resistance_type VARCHAR,
            resistance_value VARCHAR,
            retreat_cost INTEGER,
            skills_json JSONB,
            rarity VARCHAR,
            processing_status INTEGER DEFAULT 0,
            chinese_name VARCHAR,
            evolution_stage VARCHAR,
            evolves_from VARCHAR,
            set_code VARCHAR,
            set_number VARCHAR,
            set_name VARCHAR,
            regulation_flags VARCHAR,
            regulation_mark VARCHAR DEFAULT '',
            description TEXT DEFAULT '',
            flavor_text TEXT,
            pokedex_number VARCHAR,
            pokedex_category VARCHAR,
            height VARCHAR,
            weight VARCHAR
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_jp_cards_name ON jp_cards(name)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_jp_cards_set_code ON jp_cards(set_code)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_jp_cards_set_code_number ON jp_cards(set_code, set_number)
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS jp_expansion_sets (
            set_code VARCHAR PRIMARY KEY,
            set_name VARCHAR,
            series VARCHAR DEFAULT '',
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS imported_decks (
            id SERIAL PRIMARY KEY,
            deck_code VARCHAR UNIQUE,
            name VARCHAR,
            imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            deck_date VARCHAR,
            title VARCHAR,
            image_url TEXT,
            tags TEXT
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_imported_decks_deck_date_desc ON imported_decks(deck_date DESC)
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS deck_cards (
            id SERIAL PRIMARY KEY,
            deck_id INTEGER REFERENCES imported_decks(id),
            local_card_id VARCHAR,
            quantity INTEGER
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS id_mapping (
            external_variant_id INTEGER PRIMARY KEY,
            local_card_id VARCHAR
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS processed_cards (
            card_id VARCHAR PRIMARY KEY,
            card_name VARCHAR,
            original_text TEXT,
            logic_json TEXT,
            status VARCHAR,
            attempts INTEGER DEFAULT 0,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS schema_changes (
            id SERIAL PRIMARY KEY,
            card_id VARCHAR,
            change_json TEXT,
            reason TEXT,
            status VARCHAR,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS api_logs (
            id SERIAL PRIMARY KEY,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            provider VARCHAR,
            model VARCHAR,
            status VARCHAR,
            duration REAL,
            tokens_in INTEGER,
            tokens_out INTEGER,
            error_msg TEXT
        )
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS deck_search_index (
            deck_id INTEGER REFERENCES imported_decks(id) ON DELETE CASCADE,
            card_name TEXT NOT NULL,
            count INTEGER DEFAULT 1
        )
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_dsi_deck ON deck_search_index(deck_id)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_dsi_name ON deck_search_index(card_name)
        """)
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_dsi_deck_card_name ON deck_search_index(deck_id, card_name)
        """)

        cursor.execute("""
        CREATE TABLE IF NOT EXISTS regulation_settings (
            mark VARCHAR PRIMARY KEY,
            is_standard BOOLEAN DEFAULT FALSE
        )
        """)
        # 預設標準賽季字母 (F, G, H, I, J)
        cursor.execute("""
        INSERT INTO regulation_settings (mark, is_standard)
        VALUES ('F', TRUE), ('G', TRUE), ('H', TRUE), ('I', TRUE), ('J', TRUE)
        ON CONFLICT (mark) DO NOTHING
        """)

        cursor.execute(LIMITLESS_SCHEMA_SQL)

        conn.commit()
        print("Database initialized successfully.")
    except Exception as e:
        conn.rollback()
        print(f"Init DB Error: {e}")
    finally:
        conn.close()
