# Project Database Structure

This document details the structure of the SQLite databases used in the project, including tables, schemas, and JSON data formats.

## 1. Main Card Database (`Deck/pokemon_card_database.db`)

Stores the core card data and user-created decks.

### Table: `cards`
Stores detailed information for each Pokemon card. The `english_id`, `set_code`, `set_number`, and `english_name` fields are populated by `AI_Tools/bridge_manager.py` by matching cards with the official Pokemon TCG API.

`japanese_name`, `evolution_stage`, `evolves_from`, `set_code`, and `set_number` are also updated by `python/update_pokemon.py` (enhanced crawler).

| Column | Type | Description |
| :--- | :--- | :--- |
| `card_id` | TEXT (PK) | Unique identifier for the card. |
| `image_file` | TEXT | Filename of the card image (e.g., `tw00000002.png`). |
| `card_type` | TEXT | Type of card (e.g., `Pokémon`, `Trainer`). |
| `name` | TEXT | Name of the card. |
| `sub_type` | TEXT | Sub-type (e.g., `Basic`, `Stage 1`). |
| `hp` | INTEGER | Health points (for Pokémon). |
| `element_type` | TEXT | Element type (e.g., `Grass`, `Fire`). |
| `weakness_type` | TEXT | Weakness element. |
| `weakness_value` | TEXT | Weakness multiplier (e.g., `x2`). |
| `resistance_type` | TEXT | Resistance element. |
| `resistance_value` | TEXT | Resistance value. |
| `retreat_cost` | INTEGER | Energy cost to retreat. |
| `skills_json` | TEXT (JSON) | Array of skills/attacks. |
| `rarity` | TEXT | Rarity code. |
| `processing_status` | INTEGER | Status flag (default 0). |
| `english_id` | TEXT | Official English card ID (e.g., `sv3pt5-1`). Populated by `bridge_manager.py`. |
| `set_code` | TEXT | Official set code (e.g., `sv3pt5`). Populated by `bridge_manager.py` or crawler. |
| `set_number` | TEXT | Card number in the set. Populated by `bridge_manager.py` or crawler. |
| `english_name` | TEXT | English card name. Populated by `bridge_manager.py`. |
| `japanese_name` | TEXT | Japanese card name (from PTCGSP). Populated by crawler. |
| `jp_id` | TEXT | Unique ID from PTCGSP (e.g., `12345`). Populated by `update_japanese_name.py`. |
| `evolution_stage` | TEXT | Evolution stage (e.g., `1階進化`). Populated by crawler. |
| `evolves_from` | TEXT | Name of the pre-evolution Pokemon. Populated by crawler. |

**JSON Structure: `skills_json`**
```json
[
  {
    "name": "Skill Name",
    "cost": ["Colorless", "Grass"],
    "damage": "10",
    "effect": "Description of the skill effect."
  }
]
```

### Table: `decks`
Stores user-created decks.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | TEXT (PK) | Unique deck ID. |
| `name` | TEXT | Deck name. |
| `content` | TEXT (JSON) | JSON string defining the deck content. |
| `created_at` | TIMESTAMP | Creation timestamp. |
| `is_public` | INTEGER | Boolean flag (0/1). |
| `user_id` | TEXT | ID of the user who owns the deck. |

---

## 2. Imported Decks Database (`Deck/imported_decks.db`)

Stores decks imported from external sources (e.g., competitive deck lists). Managed by `Deck/deck_importer.py`.

### Table: `imported_decks`
Metadata for imported decks.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER (PK) | Auto-increment ID. |
| `deck_code` | TEXT (Unique) | External deck code (e.g., `HC10492`). |
| `name` | TEXT | Name of the deck. |
| `imported_at` | TIMESTAMP | Import timestamp. |
| `deck_date` | TEXT | Date associated with the deck (e.g., `2026-01-10`). |
| `title` | TEXT | Full title of the deck entry. |
| `image_url` | TEXT | URL to the deck's cover image. |
| `tags` | TEXT (JSON) | List of tags/keywords. |

**JSON Structure: `tags`**
```json
[
  "Charizard ex",
  "City League",
  "Winner"
]
```

### Table: `deck_cards`
Link table associating cards with imported decks.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER (PK) | Auto-increment ID. |
| `deck_id` | INTEGER (FK) | References `imported_decks(id)`. |
| `local_card_id` | TEXT | Corresponding ID in `pokemon_card_database.db`. |
| `quantity` | INTEGER | Number of copies in the deck. |

### Table: `id_mapping`
Maps external variant IDs to local card IDs to handle version differences. Used to remember manual or automatic matches found during import.

| Column | Type | Description |
| :--- | :--- | :--- |
| `external_variant_id` | INTEGER (PK)| External system's ID. |
| `local_card_id` | TEXT | Corresponding ID in `pokemon_card_database.db`. |

---

## 3. AI Tool Database (`python/ptcg_ai_tool.db` or `AI_Tools/ptcg_ai_tool.db`)

Stores AI generation logs and processed card logic. Managed by `AI_Tools/db_logic.py` and `AI_Tools/api_manager.py`.

### Table: `processed_cards`
Stores the AI-generated Game Logic for each card.

| Column | Type | Description |
| :--- | :--- | :--- |
| `card_id` | TEXT (PK) | Corresponds to `card_id` in `pokemon_card_database.db`. |
| `card_name` | TEXT | Name of the card. |
| `original_text` | TEXT (JSON) | Original `skills_json` from the source card. |
| `logic_json` | TEXT (JSON) | AI-generated structured game logic. |
| `status` | TEXT | Processing status (e.g., `APPROVED`, `PROCESSING`, `QUARANTINE`). |
| `attempts` | INTEGER | Number of generation attempts. |
| `last_updated` | DATETIME | Timestamp of last update. |

**JSON Structure: `logic_json`**
```json
[
  {
    "name": "Skill Name",
    "cost": ["Colorless"],
    "damage": null,
    "effect": [
      {
        "action": "SEARCH_DECK_TO_BENCH",
        "name": "Target Pokemon Name",
        "count": 1
      },
      {
        "action": "SHUFFLE_DECK"
      }
    ]
  },
  {
    "name": "Attack Name",
    "cost": ["Grass"],
    "damage": {
      "action": "DEAL_DAMAGE",
      "amount": 10
    },
    "effect": null
  }
]
```

### Table: `schema_changes`
Tracks changes to the AI logic schema over time. When the AI suggests a new action (e.g., `SEARCH_DECK_TO_BENCH`), it's recorded here.

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER (PK) | Auto-increment ID. |
| `card_id` | TEXT | ID of the card that triggered the change. |
| `change_json` | TEXT (JSON) | Definition of the new action/schema added. |
| `reason` | TEXT | AI's reasoning for the change. |
| `status` | TEXT | Status (e.g., `AUTO_MERGED`). |
| `timestamp` | DATETIME | Timestamp. |

**JSON Structure: `change_json`**
```json
{
  "action_name": "SEARCH_DECK_TO_BENCH",
  "definition_name": "params",
  "definition": {
    "name": { "type": "string" },
    "count": { "type": "number", "default": 1 }
  }
}
```

### Table: `api_logs`
Logs interactions with LLM providers (GoModel, OpenRouter, etc.).

| Column | Type | Description |
| :--- | :--- | :--- |
| `id` | INTEGER (PK) | Auto-increment ID. |
| `timestamp` | DATETIME | Time of request. |
| `provider` | TEXT | AI Provider (e.g., `gomodel`). |
| `model` | TEXT | Model name (e.g., `claude-3-opus`). |
| `status` | TEXT | `SUCCESS` or `FAIL`. |
| `duration` | REAL | Request duration in seconds. |
| `tokens_in` | INTEGER | Input token count. |
| `tokens_out` | INTEGER | Output token count. |
| `error_msg` | TEXT | Error message if failed. |
