const fs = require('fs');
let routes = fs.readFileSync('backend/routes.py', 'utf8');

// Find the entire search section and replace it
const marker1 = "like_clauses = []";
const marker2 = "has_full_match = any";

const idx1 = routes.indexOf(marker1);
const idx2 = routes.indexOf(marker2, idx1);

if (idx1 < 0 || idx2 < 0) {
    console.log('ERROR: cannot find search section');
    process.exit(1);
}

// Find the end of the search block (before the hashtag section)
const blockStart = routes.lastIndexOf('if search_terms:', idx1 - 200);
const blockEnd = routes.indexOf('# Hashtag:', idx2);

const newSearchBlock = `if search_terms:
            # 多詞搜索：直接查 deck_search_index
            where_parts = []
            where_params = []
            for term in search_terms:
                where_parts.append("card_name LIKE %s")
                where_params.append(f"%{term}%")
            where_clause = " OR ".join(where_parts)

            # 總數
            count_sql = f"SELECT COUNT(DISTINCT deck_id) as cnt FROM deck_search_index WHERE {where_clause}"
            cursor.execute(count_sql, where_params)
            total_count = cursor.fetchone()['cnt']

            # match_count: 每個搜索詞獨立計數，加總 = 命中幾個詞
            case_parts = []
            all_params = list(where_params)  # copy for WHERE
            for i, term in enumerate(search_terms):
                case_parts.append(f"COUNT(DISTINCT CASE WHEN dsi.card_name LIKE %s THEN 1 END)")
                all_params.append(f"%{term}%")
            match_expr = " + ".join(case_parts)

            order_clause = "match_count DESC, d.deck_date DESC"
            if sort_mode == 'date':
                order_clause = "d.deck_date DESC, match_count DESC"

            search_sql = f"""
                SELECT d.id, d.deck_code, d.title, d.deck_date, d.image_url, d.card_list, d.tags,
                       ({match_expr}) as match_count
                FROM deck_search_index dsi
                JOIN imported_decks d ON d.id = dsi.deck_id
                WHERE {where_clause}
                GROUP BY d.id
                ORDER BY {order_clause}
                LIMIT %s OFFSET %s
            """
            all_params.extend([per_page, (page - 1) * per_page])
            cursor.execute(search_sql, all_params)
            deck_rows = cursor.fetchall()

            has_full_match = any(r['match_count'] >= len(search_terms) for r in deck_rows)
`;

if (blockStart >= 0 && blockEnd > blockStart) {
    routes = routes.substring(0, blockStart) + newSearchBlock + routes.substring(blockEnd);
    fs.writeFileSync('backend/routes.py', routes);
    console.log('OK: search rewritten with correct param ordering');
} else {
    console.log('blockStart:', blockStart, 'blockEnd:', blockEnd);
}
