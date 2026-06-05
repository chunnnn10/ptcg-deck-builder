"""
搜尋加速：重建 deck_search_index（從 card_list + id_mapping + cards）
"""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
import database, json
import requests
from services.deck_importer.card_resolver import resolve_variant

def rebuild():
    conn = database.get_db_connection()
    if not conn:
        print("DB connection failed")
        return
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM deck_search_index")
        conn.commit()

        cursor.execute("SELECT card_id, name FROM cards")
        card_names = {r['card_id']: r['name'] for r in cursor.fetchall()}

        cursor.execute("SELECT id, card_list FROM imported_decks WHERE card_list IS NOT NULL AND card_list != '[]'")
        total = 0
        batch = []
        mapping = {}
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        })
        try:
            for row in cursor.fetchall():
                deck_id = row['id']
                try:
                    cl = json.loads(row['card_list'])
                except:
                    continue
                for item in cl:
                    vid = item.get('id')
                    qty = item.get('c', 1)
                    lid = mapping.get(vid)
                    if not lid:
                        resolved = resolve_variant(cursor, vid, session=session, write_mapping=True)
                        lid = resolved.get('local_card_id')
                        if lid:
                            mapping[vid] = lid
                            if lid not in card_names and resolved.get('card_row'):
                                card_names[lid] = resolved['card_row'].get('name', '')
                    if lid and lid in card_names:
                        batch.append((deck_id, card_names[lid], qty))
                total += 1
                if len(batch) >= 5000:
                    for b in batch:
                        cursor.execute(
                            "INSERT INTO deck_search_index (deck_id, card_name, count) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                            b
                        )
                    conn.commit()
                    batch = []
                if total % 5000 == 0:
                    print(f"  {total} decks...")
            if batch:
                for b in batch:
                    cursor.execute(
                        "INSERT INTO deck_search_index (deck_id, card_name, count) VALUES (%s, %s, %s) ON CONFLICT DO NOTHING",
                        b
                    )
                conn.commit()
        finally:
            session.close()
        print(f"Done: {total} decks indexed")
    except Exception as e:
        conn.rollback()
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()
    finally:
        conn.close()

if __name__ == '__main__':
    rebuild()
