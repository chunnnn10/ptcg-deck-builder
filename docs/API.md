# API Reference

Base URL for local development:

```text
http://localhost:5000
```

## Auth

| Method | Route | Description | Auth |
|---|---|---|---|
| `POST` | `/api/auth/register` | Register with `username`, `email`, `password` | No |
| `POST` | `/api/auth/login` | Login with `username`, `password` | No |
| `POST` | `/api/auth/logout` | Logout current session | Session |
| `GET` | `/api/auth/user` | Get current user | No |
| `GET` | `/verify/<token>` | Email verification link | No |

## Cards

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/search?q=&type=&element=&stage=` | Search cards with filters |
| `POST` | `/api/cards/batch` | Batch card lookup with `{"ids": [...]}` |
| `POST` | `/api/card/add` | Add a card with multipart form data |

Common search filters:

- `q`: name, ID, or image filename keyword
- `type`: `Pokemon`, `Trainer`, `Energy`, `Item`, `Pokemon Tool`, `Supporter`, `Stadium`
- `element`: `Grass`, `Fire`, `Water`, `Lightning`, `Psychic`, `Fighting`, `Darkness`, `Metal`, `Dragon`, `Colorless`
- `stage`: `Basic`, `Stage 1`, `Stage 2`, `VMAX`, `VSTAR`

## AI Assistant

| Method | Route | Description |
|---|---|---|
| `POST` | `/api/ai/chat` | Ask the PTCG Agent with `messages` and optional deck/context |

The assistant returns structured data for H/I/J standard cards and proposed deck changes. The frontend must show `deck_diff` and ask for confirmation before applying `deck_actions`.

Request body:

```json
{
  "messages": [{"role": "user", "content": "幫我把兩張博士的研究換成裁判"}],
  "context": {
    "deck": [],
    "workspace_item_id": null,
    "language": "tw",
    "standard_marks": ["H", "I", "J"]
  }
}
```

Response fields include `answer`, `cards`, `meta_references`, `deck_actions`, `deck_diff`, `tool_trace`, and `error`.

Admin embedding routes:

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/ai/embeddings/status` | Get pgvector embedding index status |
| `POST` | `/api/ai/embeddings/rebuild` | Rebuild card/meta embeddings with `source_type`, `batch_size`, and optional `max_items` |

## Decks

| Method | Route | Description |
|---|---|---|
| `POST` | `/api/deck/save` | Save a deck with `name`, `deck`, `is_public` |
| `GET` | `/api/deck/<id>` | Get deck content |
| `GET` | `/api/decks/public?q=` | List public decks |

## Workspace

Requires login.

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/workspace` | Get the user's workspace tree |
| `POST` | `/api/workspace/item` | Create a folder or deck item |
| `GET` | `/api/workspace/item/<id>` | Get item details |
| `PUT` | `/api/workspace/item/<id>` | Update item name, content, parent, or order |
| `DELETE` | `/api/workspace/item/<id>` | Delete an item |
| `POST` | `/api/workspace/item/<id>/move` | Move an item |
| `POST` | `/api/workspace/item/<id>/publish` | Publish a workspace deck |

## Imported Tournament Decks

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/decks/japanese/list?q=&sort=&page=` | List imported Japanese tournament decks |
| `GET` | `/api/decks/japanese/<code>` | Get imported deck detail |

`sort` can be `match_count` or `date`.

## Admin

Requires admin role.

| Method | Route | Description |
|---|---|---|
| `GET` | `/api/admin/check_version` | Compare official and local card data |
| `GET` | `/api/crawler/expansions` | List expansion sets |
| `POST` | `/api/crawler/start` | Start card data update |
| `GET` | `/api/crawler/status` | Get crawler progress |
| `GET` | `/api/admin/users` | List users |
| `POST` | `/api/admin/users/role` | Change user role |
| `POST` | `/api/admin/users/verify` | Manually verify a user |
| `POST` | `/api/admin/users/delete` | Delete a user |
| `GET` | `/api/admin/decks?q=&all=` | List decks for moderation |
| `PUT` | `/api/admin/deck/<id>` | Update a deck |
| `DELETE` | `/api/admin/deck/<id>` | Delete a deck |

## Tools

| Method | Route | Description |
|---|---|---|
| `POST` | `/api/tools/convert-live` | Convert a deck to PTCG Live import text |
