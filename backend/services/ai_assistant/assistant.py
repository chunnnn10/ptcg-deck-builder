from __future__ import annotations

import json
import re
import threading
import time
import uuid
from collections import Counter
from typing import Any

from . import job_store
from .client import (
    AIClientError,
    AIConfigError,
    chat_completion,
    chat_message,
    check_provider_health,
)
from .matchup_sheet import get_matchup_sheet
from .skills import get_skill, list_skills, skill_catalog_text
from .tools import (
    STANDARD_MARKS,
    analyze_current_deck,
    build_deck_diff,
    get_meta_deck_cards,
    get_card_detail,
    propose_deck_patch,
    search_card_roles,
    search_japanese_decks_by_card,
    search_meta_decks,
    semantic_search_cards,
    summarize_meta_archetype,
)

_JOBS_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}
_JOB_TTL_SECONDS = 20 * 60


SYSTEM_PROMPT = """You are a Pokemon TCG agent for a Traditional Chinese deck builder.

You work like a minimal LangChain agent: look at the skill catalog, pick one skill, then call only the tools that skill needs. Do not run a fixed pipeline.

Rules:
- First decide the skill. Simple questions use plain_chat or card_lookup. Do not search Limitless or H/I/J unless the skill needs it.
- Card text, HP, energy cost, regulation, and tournament facts must come from tools. If missing, say 未驗證.
- You MAY compose numbers already returned by tools (200+130=330, 120 vs 130 HP). Show the formula.
- You MAY use get_matchup_sheet seed rows, but they are verified=false until confirmed by get_card_detail.
- Standard format is H/I/J unless the user asks otherwise.
- Prefer Traditional Chinese.
- Deck edits only as deck_actions/deck_diff; user must confirm.
- Full 60-card lists belong in decklists, never in answer text.
- referenced_tabs / referenced_cards are user-selected context, not a command to search meta.
- Do not invent matchup kill lines. List setup / quirks / breakpoints first, then analysis sentences that point at those rows."""


FINAL_JSON_INSTRUCTIONS = """Return one JSON object only with this shape:
{
  "skill": "plain_chat",
  "answer": "Traditional Chinese concise response",
  "cards": [],
  "meta_references": [],
  "decklists": [],
  "deck_actions": [],
  "deck_diff": {"current_total": 0, "projected_total": 0, "additions": [], "removals": [], "warnings": []},
  "matchup_sheet": null
}

Fill only fields the chosen skill needs. empty arrays are fine.
cards / meta_references / decklists must come from tools.
decklists only for recommend_archetype, or when the user asked for a full list.
deck_actions only for patch_deck.
matchup_sheet only for matchup_analysis / explain_gameplan when a sheet was fetched.
answer must not contain a full decklist or raw JSON."""


TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "semantic_search_cards",
            "description": "Semantic search over H/I/J standard card data using pgvector embeddings with keyword fallback.",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    "filters": {
                        "type": "object",
                        "properties": {
                            "language": {"type": "string", "enum": ["tw", "jp"]},
                            "standard_marks": {"type": "array", "items": {"type": "string"}},
                            "predicate_filter": {
                                "type": "object",
                                "description": "Optional verified structured predicate filter, for example {'type':'hp_threshold','op':'<=','value':90}. Only Gap A threshold predicates are currently reliable.",
                                "properties": {
                                    "type": {"type": "string"},
                                    "types": {"type": "array", "items": {"type": "string"}},
                                    "op": {"type": "string", "enum": ["<=", "<", ">=", ">", "=="]},
                                    "value": {"type": "integer"},
                                    "min_value": {"type": "integer"},
                                    "max_value": {"type": "integer"},
                                    "applies_to": {"type": "string"},
                                    "dim": {"type": "string"},
                                    "target": {"type": "string"},
                                    "destination": {"type": "string"},
                                },
                            },
                        },
                    },
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_card_detail",
            "description": "Get exact H/I/J card details by local card_id.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_id": {"type": "string"},
                    "language": {"type": "string", "enum": ["tw", "jp"]},
                },
                "required": ["card_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_meta_decks",
            "description": "Search recent indexed Limitless meta decks by archetype or strategy query.",
            "parameters": {
                "type": "object",
                "properties": {
                    "archetype_or_query": {"type": "string"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                },
                "required": ["archetype_or_query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_card_roles",
            "description": "Search cards by approved effect-role tags (draw/discard/search/ramp/evolve_accel/heal/switch/damage/condition/stall) with optional mechanism params, e.g. {'role':'draw','params':{'old_hand':'discard'}} finds true filtering draw cards that discard the old hand first. Only admin-approved tags are searchable.",
            "parameters": {
                "type": "object",
                "properties": {
                    "role": {
                        "type": "string",
                        "enum": ["draw", "discard", "search", "ramp", "evolve_accel", "heal", "switch", "damage", "condition", "stall"],
                    },
                    "params": {
                        "type": "object",
                        "description": "Mechanism filter. Usable keys: source, old_hand, reshuffle, target, count, max_count, destination, from, to, target_type, type, condition.",
                    },
                    "query": {"type": "string", "description": "Optional card name keyword filter"},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_japanese_decks_by_card",
            "description": "Search indexed Japanese tournament decks that contain a named card at or above a minimum copy count.",
            "parameters": {
                "type": "object",
                "properties": {
                    "card_name": {"type": "string"},
                    "min_count": {"type": "integer", "minimum": 1, "maximum": 60},
                    "limit": {"type": "integer", "minimum": 1, "maximum": 10},
                    "sort": {"type": "string", "enum": ["count", "date"]},
                },
                "required": ["card_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_meta_deck_cards",
            "description": "Get a concrete Limitless decklist with card counts and image URLs for visual rendering.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deck_id": {"type": "string"},
                    "language": {"type": "string", "enum": ["tw", "jp", "en"]},
                    "mode": {"type": "string", "enum": ["normal", "bling"]},
                },
                "required": ["deck_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "summarize_meta_archetype",
            "description": "Summarize a Limitless archetype with common cards and sample decks.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_current_deck",
            "description": "Analyze current deck count, type counts, regulation marks, and obvious count issues.",
            "parameters": {
                "type": "object",
                "properties": {
                    "deck": {"type": "array", "items": {"type": "object"}},
                },
                "required": ["deck"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "propose_deck_patch",
            "description": "Create a deterministic deck patch draft from user intent, current deck, and retrieved context.",
            "parameters": {
                "type": "object",
                "properties": {
                    "intent": {"type": "string"},
                    "deck": {"type": "array", "items": {"type": "object"}},
                    "retrieved_context": {"type": "object"},
                    "language": {"type": "string", "enum": ["tw", "jp"]},
                },
                "required": ["intent", "deck"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_skills",
            "description": "List available agent skills and which tools each skill may use. Call this when unsure which flow to run.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_skill",
            "description": "Load one skill playbook: when to use, tool order, stop condition, output contract.",
            "parameters": {
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "required": ["name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_format_snapshot",
            "description": "Get last-30-day Limitless combination share percentages and tournament size summary.",
            "parameters": {
                "type": "object",
                "properties": {"days": {"type": "integer", "minimum": 7, "maximum": 120}},
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_format_brief",
            "description": "Get a permanently stored monthly brief for a combo key, including printed damage lines if already analyzed.",
            "parameters": {
                "type": "object",
                "properties": {"combo_key": {"type": "string"}},
                "required": ["combo_key"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_matchup_sheet",
            "description": "Get matchup sheet schema and seed rows (setup / quirks / breakpoints / vs_archetype). Seed rows are unverified user notes.",
            "parameters": {
                "type": "object",
                "properties": {
                    "user_archetype": {"type": "string"},
                    "opponent_archetype": {"type": "string"},
                },
            },
        },
    },
]


def _last_user_message(messages: list[dict[str, Any]]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            return str(msg.get("content") or "").strip()
    return ""


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (dict, list)):
        return value
    text = str(value).strip()
    candidates = [text]

    fenced = re.search(r"```(?:json)?\s*(\{.*?\}|\[.*?\])\s*```", text, re.IGNORECASE | re.DOTALL)
    if fenced:
        candidates.insert(0, fenced.group(1).strip())

    first_obj = text.find("{")
    last_obj = text.rfind("}")
    if first_obj >= 0 and last_obj > first_obj:
        candidates.append(text[first_obj:last_obj + 1])

    first_arr = text.find("[")
    last_arr = text.rfind("]")
    if first_arr >= 0 and last_arr > first_arr:
        candidates.append(text[first_arr:last_arr + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
            if isinstance(parsed, str) and parsed.strip() != candidate:
                nested = _json_loads(parsed, None)
                return nested if nested is not None else parsed
            return parsed
        except Exception:
            continue
    return default


# Row-aware compaction limits. Chosen so a compacted trace stays well under
# the model context while keeping the fields the model actually cites.
_COMPACT_MAX_ROWS = 40
_COMPACT_MAX_FIELDS = 40
_COMPACT_FIELD_CHARS = 600
_COMPACT_DEPTH = 4

# Whitelist of model-useful card fields. Everything else a raw card row carries
# (image_file, image_url, predicates, logic_extractor_version, id, language,
# logic_source_card_id, ...) is noise that used to eat the whole budget.
_SLIM_CARD_FIELDS: tuple[str, ...] = (
    "card_id",
    "name",
    "card_type",
    "sub_type",
    "hp",
    "element_type",
    "weakness_type",
    "weakness_value",
    "resistance_type",
    "resistance_value",
    "retreat_cost",
    "rarity",
    "set_code",
    "set_number",
    "set_name",
    "regulation_mark",
    "evolution_stage",
    "evolves_from",
    "description",
    "japanese_name",
)

# Small annotations some card-shaped results add on top of the plain card row
# (role search, decklist rows). Kept only when actually present, so a plain
# card payload stays identical to the whitelist above.
_SLIM_CARD_ANNOTATIONS: tuple[str, ...] = (
    "role",
    "params",
    "confidence",
    "evidence_span",
    "semantic_score",
    "count",
    "section",
)

# Keys searched (in order) for the record list inside a dict tool result.
_RECORD_LIST_KEYS: tuple[str, ...] = (
    "cards",
    "results",
    "items",
    "sample_decks",
    "meta_references",
    "decklists",
    "search_results",
)


def _compact_scalar(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _COMPACT_FIELD_CHARS else value[:_COMPACT_FIELD_CHARS] + "..."
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return None


def _looks_like_card(value: Any) -> bool:
    """True when a dict carries the identifying keys of a card record.

    Diagnostic objects (``_degraded_note``) and meta/role annotations do not,
    so they fall through to the generic compactor untouched.
    """
    return isinstance(value, dict) and bool(value.get("card_id")) and bool(value.get("name"))


def _slim_cost(cost: Any) -> str:
    """Render an energy cost such as ['雷', '雷', '無色'] as '雷雷無色'."""
    if isinstance(cost, list):
        return "".join(str(item).strip() for item in cost if str(item).strip())
    if isinstance(cost, str):
        return cost if len(cost) <= 40 else cost[:40]
    return ""


def _slim_skill(skill: Any) -> dict[str, Any]:
    if not isinstance(skill, dict):
        text = str(skill or "")
        return {"effect": _compact_scalar(text)} if text else {}
    item: dict[str, Any] = {}
    for key in ("name", "type", "damage"):
        value = skill.get(key)
        if value is None or value == "":
            continue
        item[key] = _compact_scalar(value) if isinstance(value, str) else value
    cost = _slim_cost(skill.get("cost"))
    if cost:
        item["cost"] = cost
    effect = skill.get("effect")
    if effect is not None and effect != "":
        item["effect"] = _compact_scalar(effect)
    return item


def _slim_card(card: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Project a card-like dict to the model-useful whitelist only.

    Drops the ~25 mostly-irrelevant columns a raw card row carries, keeps the
    fields a model actually cites, and never injects a per-card truncation
    marker. Absent, null or empty keys are omitted.
    """
    slim: dict[str, Any] = {}
    for key in _SLIM_CARD_FIELDS:
        value = card.get(key)
        if value is None or value == "":
            continue
        slim[key] = _compact_scalar(value) if isinstance(value, str) else value
    for key in _SLIM_CARD_ANNOTATIONS:
        if key not in card:
            continue
        value = card.get(key)
        if value is None or value in ("", [], {}):
            continue
        if isinstance(value, (list, dict)):
            slim[key] = _compact_value(value, depth + 1)
        else:
            slim[key] = _compact_scalar(value)
    skills = card.get("skills")
    if isinstance(skills, list):
        slim["skills"] = [_slim_skill(skill) for skill in skills[:8]]
    return slim


def _row_note(shown: int, total: int) -> dict[str, Any]:
    return {"note": f"只顯示前 {shown} 筆，共 {total} 筆"}


def _compact_value(value: Any, depth: int = 0) -> Any:
    """Recursively shrink one value without ever producing invalid JSON.

    Card records are projected through ``_slim_card`` first, so they stay whole
    and readable. When rows are dropped the elision is reported with one
    trailing ``{"note": ...}`` object instead of a per-record ``_truncated``
    flag that makes a model believe the data is broken.
    """
    if isinstance(value, str):
        return _compact_scalar(value)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, (list, tuple)):
        items = list(value)
        if not items:
            return []
        card_votes = sum(1 for item in items if _looks_like_card(item))
        if card_votes * 2 >= len(items):
            kept = [
                _slim_card(item, depth + 1) if _looks_like_card(item) else _compact_value(item, depth + 1)
                for item in items[:_COMPACT_MAX_ROWS]
            ]
            if len(items) > _COMPACT_MAX_ROWS:
                kept.append(_row_note(_COMPACT_MAX_ROWS, len(items)))
            return kept
        if depth >= _COMPACT_DEPTH:
            return _row_note(0, len(items))
        rows = [_compact_value(item, depth + 1) for item in items[:_COMPACT_MAX_ROWS]]
        if len(items) > _COMPACT_MAX_ROWS:
            rows.append(_row_note(_COMPACT_MAX_ROWS, len(items)))
        return rows
    if isinstance(value, dict):
        if _looks_like_card(value):
            return _slim_card(value, depth + 1)
        compact: dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= _COMPACT_MAX_FIELDS:
                compact["note"] = f"另有 {len(value) - _COMPACT_MAX_FIELDS} 個欄位已省略"
                break
            compact[str(key)] = _compact_value(item, depth + 1)
        return compact
    return _compact_scalar(value)


def _record_list_key(value: dict[str, Any]) -> str | None:
    """Find the record list to trim inside a dict payload (known keys first)."""
    for key in _RECORD_LIST_KEYS:
        items = value.get(key)
        if isinstance(items, list) and items:
            return key
    best_key: str | None = None
    best_len = 1
    for key, items in value.items():
        if isinstance(items, list) and len(items) > best_len:
            best_key, best_len = key, len(items)
    return best_key


def _row_total(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    key = _record_list_key(value) if isinstance(value, dict) else None
    return len(value[key]) if key else 0


def _with_row_limit(value: Any, count: int) -> Any:
    """Copy of ``value`` holding only the first ``count`` records."""
    if isinstance(value, list):
        return value[:count]
    key = _record_list_key(value) if isinstance(value, dict) else None
    if not key:
        return value
    clone = dict(value)
    clone[key] = value[key][:count]
    return clone


def _render_payload(value: Any, shown: int, total: int) -> str:
    """Compact ``value`` down to ``shown`` records, adding one readable note.

    ``shown`` never exceeds ``_COMPACT_MAX_ROWS``, so the compactor itself does
    not add a second, conflicting note.
    """
    payload = _compact_value(_with_row_limit(value, shown))
    if shown < total:
        if isinstance(payload, list):
            payload = payload + [_row_note(shown, total)]
        elif isinstance(payload, dict):
            payload = {**payload, "note": f"只顯示前 {shown} 筆，共 {total} 筆"}
    return json.dumps(payload, ensure_ascii=False, default=str)


def _budget_envelope(text: str, max_chars: int) -> str:
    """Final fallback: a valid JSON note plus a bounded, clearly-labelled preview."""
    note = "內容過大，只保留部分資料"
    room = max(0, max_chars - 200)
    while room > 0:
        candidate = json.dumps({"note": note, "preview": text[:room]}, ensure_ascii=False)
        if len(candidate) <= max_chars:
            return candidate
        room -= max(1, len(candidate) - max_chars)
    return json.dumps({"note": note}, ensure_ascii=False)


def _compact_tool_result(value: Any, max_chars: int = 14000) -> str:
    """JSON-serialise a tool result with card-aware, always-valid shrinking.

    Cards are projected to a model-useful whitelist instead of being marked
    ``_truncated``, and anything dropped is reported with a single readable
    ``note`` so the model can still use every record it receives.
    """
    try:
        text = json.dumps(_compact_value(value), ensure_ascii=False, default=str)
    except Exception:
        return json.dumps({"error": "unserialisable tool result", "repr": str(value)[:500]}, ensure_ascii=False)
    if len(text) <= max_chars:
        return text

    total = _row_total(value)
    if total > 1:
        # Drop whole trailing records (lowest value first) instead of mangling
        # one record; binary search so the budget is used as fully as possible.
        low, high = 1, min(total, _COMPACT_MAX_ROWS)
        best: str | None = None
        while low <= high:
            mid = (low + high) // 2
            candidate = _render_payload(value, mid, total)
            if len(candidate) <= max_chars:
                best = candidate
                low = mid + 1
            else:
                high = mid - 1
        if best is not None:
            return best

    return _budget_envelope(text, max_chars)


def _context_budget(parts: list[Any], max_chars: int = 24000) -> str:
    """Join already-compacted context parts under a hard total character budget.

    Each part is compacted individually, then the highest-value parts are kept
    whole and the rest are dropped with an explicit marker, so the final
    synthesis prompt can never blow past the budget.
    """
    rendered: list[tuple[str, str]] = []
    for part in parts:
        label = str(part.get("label") if isinstance(part, dict) else "")
        payload = part.get("value") if isinstance(part, dict) else part
        rendered.append((label, _compact_tool_result(payload, max_chars)))

    kept: list[str] = []
    used = 0
    dropped: list[str] = []
    for label, payload in rendered:
        piece = (label + ":\n" if label else "") + payload
        if used + len(piece) > max_chars and kept:
            dropped.append(label or "part")
            continue
        kept.append(piece)
        used += len(piece)
    if dropped:
        kept.append("[省略過長內容] " + ", ".join(dropped))
    return "\n".join(kept)

def _compact_card_context(card: Any) -> dict[str, Any] | None:
    if not isinstance(card, dict):
        return None
    name = str(card.get("name") or card.get("card_name") or card.get("jp_card_name") or "").strip()
    card_id = str(card.get("card_id") or card.get("id") or "").strip()
    if not name and not card_id:
        return None
    return {
        "card_id": card_id,
        "name": name,
        "language": card.get("language") or "",
        "card_type": card.get("card_type") or "",
        "sub_type": card.get("sub_type") or "",
        "set_code": card.get("set_code") or "",
        "set_number": card.get("set_number") or "",
        "regulation_mark": card.get("regulation_mark") or "",
        "description": card.get("description") or "",
        "skills": card.get("skills") if isinstance(card.get("skills"), list) else [],
    }


def _compact_referenced_cards(value: Any, limit: int = 24) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    cards: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in value:
        card = _compact_card_context(item)
        if not card:
            continue
        key = f"{card.get('language')}:{card.get('card_id') or card.get('name')}:{card.get('set_number')}"
        if key in seen:
            continue
        seen.add(key)
        cards.append(card)
        if len(cards) >= limit:
            break
    return cards


def _compact_referenced_deck_cards(value: Any, limit: int = 80) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    grouped: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    for item in value:
        card = _compact_card_context(item)
        if not card:
            continue
        key = f"{card.get('language')}:{card.get('card_id') or card.get('name')}:{card.get('set_number')}"
        if key not in grouped:
            grouped[key] = card
            grouped[key]["count"] = 0
            order.append(key)
        try:
            count = int(item.get("count") or 1) if isinstance(item, dict) else 1
        except Exception:
            count = 1
        grouped[key]["count"] += max(1, count)
    return [grouped[key] for key in order[:limit]]


def _compact_referenced_tabs(value: Any, max_tabs: int = 6, max_cards_per_tab: int = 80) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    tabs: list[dict[str, Any]] = []
    for item in value[:max_tabs]:
        if not isinstance(item, dict):
            continue
        raw_deck = item.get("deck") if isinstance(item.get("deck"), list) else []
        cards = _compact_referenced_deck_cards(raw_deck, max_cards_per_tab)
        try:
            count = int(item.get("count") or len(raw_deck) or sum(int(card.get("count") or 0) for card in cards))
        except Exception:
            count = len(raw_deck) or sum(int(card.get("count") or 0) for card in cards)
        tabs.append({
            "id": str(item.get("id") or ""),
            "title": str(item.get("title") or "Untitled Deck"),
            "source": str(item.get("source") or "scratch"),
            "count": count,
            "cards": cards,
        })
    return tabs


def _card_count(card: dict[str, Any]) -> int:
    try:
        return max(0, int(card.get("count") or 0))
    except Exception:
        return 0


def _card_name(card: dict[str, Any]) -> str:
    return str(card.get("name") or card.get("card_name") or card.get("jp_card_name") or "").strip()


def _card_text(card: dict[str, Any]) -> str:
    parts = [
        str(card.get("name") or ""),
        str(card.get("card_type") or ""),
        str(card.get("sub_type") or ""),
        str(card.get("description") or ""),
    ]
    for skill in card.get("skills") or []:
        if not isinstance(skill, dict):
            continue
        parts.extend([
            str(skill.get("type") or ""),
            str(skill.get("name") or ""),
            " ".join(str(item) for item in (skill.get("cost") or []) if item),
            str(skill.get("damage") or ""),
            str(skill.get("effect") or skill.get("text") or skill.get("description") or ""),
        ])
    return " ".join(part for part in parts if part).strip()


def _card_brief(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": _card_name(card),
        "count": _card_count(card),
        "card_type": card.get("card_type") or "",
        "sub_type": card.get("sub_type") or "",
        "set_code": card.get("set_code") or "",
        "set_number": card.get("set_number") or "",
        "regulation_mark": card.get("regulation_mark") or "",
    }


def _top_cards(cards: list[dict[str, Any]], section: str | None = None, limit: int = 6) -> list[dict[str, Any]]:
    filtered = []
    for card in cards:
        if not isinstance(card, dict) or not _card_name(card):
            continue
        if section and str(card.get("section") or "").lower() != section:
            continue
        filtered.append(card)
    filtered.sort(key=lambda card: (-_card_count(card), _card_name(card)))
    return [_card_brief(card) for card in filtered[:limit]]


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    haystack = str(text or "").lower()
    return any(keyword.lower() in haystack for keyword in keywords)


ENERGY_PLAN_KEYWORDS = (
    "能量",
    "附加",
    "貼附",
    "附於",
    "填能",
    "特填",
    "加速",
    "棄牌區",
    "從牌庫",
    "從手牌",
    "basic energy",
    "attach",
    "attached",
    "energy acceleration",
)

SETUP_KEYWORDS = (
    "牌庫",
    "加入手牌",
    "抽",
    "搜尋",
    "選擇",
    "檢索",
    "search your deck",
    "draw",
    "put into your hand",
)


def _enrich_cards_for_play_analysis(cards: list[dict[str, Any]], language: str) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    detail_budget = 30
    for card in cards:
        if not isinstance(card, dict):
            continue
        item = dict(card)
        needs_detail = not item.get("description") and not item.get("skills")
        card_id = str(item.get("card_id") or item.get("id") or "").strip()
        if needs_detail and card_id and detail_budget > 0:
            detail = get_card_detail(card_id, language if language in ("tw", "jp") else "tw")
            if detail:
                count = item.get("count")
                section = item.get("section")
                item = {**item, **detail}
                item["count"] = count
                item["section"] = section or item.get("section")
            detail_budget -= 1
        enriched.append(item)
    return enriched


def _analysis_card_line(cards: list[dict[str, Any]], fallback: str = "未在牌表文字中明確辨識") -> str:
    names = [f"{card.get('name')} x{card.get('count')}" for card in cards if card.get("name")]
    return "、".join(names) if names else fallback


def _analysis_query_terms(analysis: dict[str, Any]) -> list[str]:
    terms: list[str] = []
    for key in ("key_pokemon", "likely_attackers", "energy_support_cards", "setup_cards"):
        for card in analysis.get(key) or []:
            name = str(card.get("name") or "").strip()
            if name and name not in terms:
                terms.append(name)
    return terms[:10]


def _analyze_deck_play_plan(source: dict[str, Any], source_type: str, language: str = "tw") -> dict[str, Any]:
    raw_cards = source.get("cards") if isinstance(source.get("cards"), list) else []
    cards = _normalize_decklist_cards(raw_cards)
    cards = _enrich_cards_for_play_analysis(cards, language)
    total = sum(_card_count(card) for card in cards)

    section_counts: Counter[str] = Counter()
    for card in cards:
        section_counts[str(card.get("section") or "unknown").lower()] += _card_count(card)

    pokemon = [card for card in cards if str(card.get("section") or "").lower() == "pokemon"]
    trainers = [card for card in cards if str(card.get("section") or "").lower() == "trainer"]
    energy_cards = [card for card in cards if str(card.get("section") or "").lower() == "energy"]
    energy_support = [
        card for card in cards
        if str(card.get("section") or "").lower() != "energy"
        and _contains_any(_card_text(card), ENERGY_PLAN_KEYWORDS)
    ]
    setup_cards = [
        card for card in trainers
        if _contains_any(_card_text(card), SETUP_KEYWORDS)
    ]
    likely_attackers = [
        card for card in pokemon
        if _card_count(card) >= 2
        or "ex" in _card_name(card).lower()
        or any(str(skill.get("damage") or "").strip() for skill in (card.get("skills") or []) if isinstance(skill, dict))
    ]

    likely_attackers.sort(key=lambda card: (-_card_count(card), "ex" not in _card_name(card).lower(), _card_name(card)))
    energy_support.sort(key=lambda card: (-_card_count(card), _card_name(card)))
    setup_cards.sort(key=lambda card: (-_card_count(card), _card_name(card)))

    title = str(source.get("title") or source.get("name") or source.get("deck_id") or "Untitled Deck")
    key_pokemon = _top_cards(cards, "pokemon", 6)
    key_trainers = _top_cards(cards, "trainer", 8)
    energy_briefs = [_card_brief(card) for card in sorted(energy_cards, key=lambda card: (-_card_count(card), _card_name(card)))[:8]]
    energy_support_briefs = [_card_brief(card) for card in energy_support[:8]]
    setup_briefs = [_card_brief(card) for card in setup_cards[:8]]
    attacker_briefs = [_card_brief(card) for card in likely_attackers[:6]]

    if attacker_briefs:
        plan = f"初步判斷是以 {_analysis_card_line(attacker_briefs)} 作為主要進攻或場面核心。"
    elif key_pokemon:
        plan = f"初步判斷核心寶可夢是 {_analysis_card_line(key_pokemon)}。"
    else:
        plan = "目前傳入的牌表未能明確辨識主要寶可夢核心。"

    if energy_support_briefs:
        energy_summary = f"可確認的能量/填能相關牌有 {_analysis_card_line(energy_support_briefs)}。"
    elif energy_briefs:
        energy_summary = "目前只明確看到能量本身，未從已傳入卡文辨識到穩定的特填或加速來源。"
    else:
        energy_summary = "目前牌表中沒有明確能量線，後續會特別檢查是否需要補能量或特填手段。"

    setup_summary = (
        f"穩定性與展開可能依賴 {_analysis_card_line(setup_briefs)}。"
        if setup_briefs else
        "目前未從卡文中明確辨識到主要檢索/抽牌引擎。"
    )
    counts_summary = (
        f"{total} 張，Pokémon {section_counts.get('pokemon', 0)} / "
        f"Trainer {section_counts.get('trainer', 0)} / Energy {section_counts.get('energy', 0)}。"
    )
    visible_summary = "\n".join([
        f"{title}：{counts_summary}",
        plan,
        energy_summary,
        setup_summary,
        "我現在會進行工具調用搜索，搜尋相近牌組以及 H/I/J 可用卡牌，以給出更準確的建議。",
    ])

    return {
        "source_type": source_type,
        "id": source.get("id") or source.get("deck_id"),
        "title": title,
        "total_count": total,
        "section_counts": dict(section_counts),
        "key_pokemon": key_pokemon,
        "likely_attackers": attacker_briefs,
        "key_trainers": key_trainers,
        "energy_cards": energy_briefs,
        "energy_support_cards": energy_support_briefs,
        "setup_cards": setup_briefs,
        "game_plan_summary": plan,
        "energy_summary": energy_summary,
        "setup_summary": setup_summary,
        "user_visible_summary": visible_summary,
        "search_query_terms": [],
    }


def _prefetch_query(user_text: str, context: dict[str, Any]) -> str:
    terms: list[str] = []
    for analysis in context.get("referenced_tab_analysis") or []:
        if isinstance(analysis, dict):
            terms.extend(_analysis_query_terms(analysis))
    cleaned_terms = []
    seen = set()
    for term in terms:
        text = str(term or "").strip()
        if text and text not in seen:
            seen.add(text)
            cleaned_terms.append(text)
    if not cleaned_terms:
        return user_text
    return f"{user_text}\n引用牌組核心：{'、'.join(cleaned_terms[:8])}"


def _analyze_referenced_tabs(context: dict[str, Any]) -> list[dict[str, Any]]:
    tabs = context.get("referenced_tabs") if isinstance(context.get("referenced_tabs"), list) else []
    if not tabs:
        context["referenced_tab_analysis"] = []
        return []

    job_id = context.get("job_id")
    language = str(context.get("language") or "tw")
    analyses: list[dict[str, Any]] = []
    _append_job_step(job_id, {"status": "running", "message": "先讀取 @tab 牌表並分析玩法"})
    for tab in tabs:
        analysis = _analyze_deck_play_plan(tab, "referenced_tab", language)
        analysis["search_query_terms"] = _analysis_query_terms(analysis)
        analyses.append(analysis)
        _append_job_step(
            job_id,
            {
                "status": "running",
                "message": f"已理解 @tab：{analysis.get('title')}",
                "detail": analysis.get("user_visible_summary"),
                "tool": "analyze_referenced_tabs",
                "result_count": analysis.get("total_count"),
            },
        )
    _append_job_step(
        job_id,
        {
            "status": "running",
            "message": "我現在會進行工具調用搜索，搜尋相近牌組以及可用卡牌",
        },
    )
    context["referenced_tab_analysis"] = analyses
    result = {"tabs": analyses, "tab_count": len(analyses)}
    return [
        {
            "tool": "analyze_referenced_tabs",
            "args": {"tab_count": len(analyses)},
            "result": result,
            "result_count": len(analyses),
            "detail": "\n\n".join(analysis.get("user_visible_summary") or "" for analysis in analyses if analysis.get("user_visible_summary")),
        }
    ]


def _collect_play_analysis_from_value(value: Any) -> list[dict[str, Any]]:
    analyses: list[dict[str, Any]] = []
    if isinstance(value, dict):
        if value.get("game_plan_summary") and value.get("energy_summary"):
            analyses.append(value)
        for key in ("play_analysis", "tabs", "deck_analyses", "analyses"):
            nested = value.get(key)
            analyses.extend(_collect_play_analysis_from_value(nested))
    elif isinstance(value, list):
        for item in value:
            analyses.extend(_collect_play_analysis_from_value(item))

    compact: list[dict[str, Any]] = []
    seen = set()
    for analysis in analyses:
        key = analysis.get("id") or analysis.get("title") or json.dumps(analysis, ensure_ascii=False, default=str)[:120]
        if key in seen:
            continue
        seen.add(key)
        compact.append(analysis)
    return compact[:8]


def _result_count(result: Any) -> int:
    if isinstance(result, list):
        return len(result)
    if isinstance(result, dict):
        for key in ("cards", "sample_decks", "meta_references", "deck_actions", "tabs", "analyses", "deck_analyses", "skills", "sheets", "available"):
            if isinstance(result.get(key), list):
                return len(result.get(key) or [])
        return 1 if result else 0
    return 1 if result else 0


def _tool_step_message(item: dict[str, Any]) -> str:
    tool = item.get("tool") or "tool"
    args = item.get("args") if isinstance(item.get("args"), dict) else {}
    count = item.get("result_count", 0)
    if item.get("error"):
        return f"{tool} 失敗：{item.get('error')}"

    if tool == "semantic_search_cards":
        return f"搜尋標準卡池：{args.get('query') or ''}（{count}）"
    if tool == "search_meta_decks":
        return f"搜尋 Limitless Meta：{args.get('archetype_or_query') or ''}（{count}）"
    if tool == "get_meta_deck_cards":
        return f"讀取 Limitless 牌表：{args.get('deck_id') or ''}（{count}）"
    if tool == "summarize_meta_archetype":
        return f"整理 Meta 共通牌：{args.get('query') or ''}（{count}）"
    if tool == "analyze_current_deck":
        return "分析目前牌組結構"
    if tool == "propose_deck_patch":
        return f"產生牌組變更草案（{count}）"
    if tool == "get_card_detail":
        return f"讀取卡牌詳情：{args.get('card_id') or ''}"
    if tool == "analyze_referenced_tabs":
        return f"分析引用 tab 玩法（{count}）"
    if tool == "analyze_meta_deck_play_plan":
        return f"分析 Limitless 樣本玩法：{args.get('deck_id') or ''}"
    if tool == "list_skills":
        return f"讀取 Skill catalog（{count}）"
    if tool == "get_skill":
        return f"讀取 Skill：{args.get('name') or ''}"
    if tool == "get_matchup_sheet":
        return f"讀取對局表：{args.get('user_archetype') or ''} vs {args.get('opponent_archetype') or ''}（{count}）"
    return f"{tool} returned {count} result(s)"


def _tool_step_status(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": "done" if not item.get("error") else "error",
        "message": _tool_step_message(item),
        "tool": item.get("tool"),
        "result_count": item.get("result_count"),
        "error": item.get("error"),
        "detail": item.get("detail"),
    }


def _cleanup_jobs() -> None:
    """Drop stale in-memory fallback jobs only.

    DB-backed jobs are aged out by ``job_store.cleanup_old_jobs``.
    """
    now = time.time()
    with _JOBS_LOCK:
        stale = [job_id for job_id, job in _JOBS.items() if now - float(job.get("updated_at") or 0) > _JOB_TTL_SECONDS]
        for job_id in stale:
            _JOBS.pop(job_id, None)


_UNSET: Any = object()

# The DB store uses its own status vocabulary; the routes/frontend contract uses
# 'queued' / 'finished'. Translate on the way in and out.
_DB_STATUS_IN = {"finished": "done", "queued": "pending"}
_DB_STATUS_OUT = {"done": "finished", "pending": "queued"}


def _db_update_job(
    job_id: str,
    *,
    result: Any = _UNSET,
    message: Any = _UNSET,
    status: Any = _UNSET,
    error: Any = _UNSET,
) -> bool:
    """Persist a partial job update in the DB-backed store.

    ``job_store`` exposes a frozen public interface without a progress/step
    update entry point, so this reuses its SAVEPOINT-safe internal ``_update``
    helper. Any failure returns False; callers treat the in-memory copy as the
    fallback and never raise.
    """
    sets = ["updated_at = CURRENT_TIMESTAMP"]
    params: list[Any] = []
    if result is not _UNSET:
        sets.append("result = %s::jsonb")
        params.append(json.dumps(result if result is not None else {}, ensure_ascii=False, default=str))
    if message is not _UNSET:
        sets.append("message = %s")
        params.append(str(message or ""))
    if status is not _UNSET:
        sets.append("status = %s")
        params.append(str(status or ""))
    if error is not _UNSET:
        sets.append("error = %s")
        params.append(str(error or ""))
    params.append(str(job_id or ""))
    sql = "UPDATE ai_assistant_jobs SET " + ", ".join(sets) + " WHERE id = %s"
    try:
        return bool(job_store._update(str(job_id or ""), sql, tuple(params)))
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[assistant] job update failed: {exc}")
        return False


def _set_job(job_id: str, **updates: Any) -> None:
    if not job_id:
        return
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            job.update(updates)
            job["updated_at"] = time.time()
            return

    status = updates.get("status")
    mapped_status = _DB_STATUS_IN.get(status, status) if status is not None else _UNSET
    result_value = updates.get("result", _UNSET) if "result" in updates else _UNSET
    if result_value is None:
        result_value = _UNSET
    _db_update_job(
        job_id,
        result=result_value,
        message=updates.get("message", _UNSET),
        status=mapped_status,
        error=updates.get("error", _UNSET),
    )


def _append_job_step(job_id: str | None, step: dict[str, Any]) -> None:
    """Append a progress step.

    Steps live inside the persisted ``result`` (``result['steps']``) so a poll
    served by any worker can render progress. In-memory jobs keep the old shape.
    """
    if not job_id:
        return
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
        if job is not None:
            steps = job.setdefault("steps", [])
            if steps and steps[-1].get("status") == "running" and step.get("status") == "running":
                steps[-1]["status"] = "done"
            steps.append(step)
            job["message"] = step.get("message") or job.get("message") or ""
            job["updated_at"] = time.time()
            return

    try:
        row = job_store.get_job(str(job_id))
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[assistant] job step fetch failed: {exc}")
        row = None
    if not row:
        return
    result = row.get("result") if isinstance(row.get("result"), dict) else {}
    steps = result.get("steps") if isinstance(result.get("steps"), list) else []
    if steps and steps[-1].get("status") == "running" and step.get("status") == "running":
        steps[-1]["status"] = "done"
    steps.append(step)
    result["steps"] = steps
    _db_update_job(
        job_id,
        result=result,
        message=step.get("message") or row.get("message") or "",
    )


def _start_job(messages: list[dict[str, Any]], context: dict[str, Any], user_id: str = "") -> dict[str, Any]:
    _cleanup_jobs()
    context = dict(context or {})
    owner = str(user_id or context.get("user_id") or "")
    context["user_id"] = owner
    message_text = _last_user_message(messages)

    job_id = ""
    try:
        job_id = str(job_store.create_job(owner, message_text, context=context, kind="chat") or "")
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[assistant] job_store unavailable, falling back to in-memory jobs: {exc}")
        job_id = ""

    if job_id:
        _db_update_job(
            job_id,
            result={"steps": [{"status": "running", "message": "Agent 已排入工作佇列"}]},
            message="Agent 已排入工作佇列",
            status="pending",
        )
        context["job_id"] = job_id
    else:
        # DB unavailable: keep the legacy single-worker in-memory registry so the
        # feature still works when only one process serves the poll.
        job_id = uuid.uuid4().hex
        now = time.time()
        with _JOBS_LOCK:
            _JOBS[job_id] = {
                "job_id": job_id,
                "user_id": owner,
                "status": "queued",
                "message": "Agent 已排入工作佇列",
                "steps": [{"status": "running", "message": "Agent 已排入工作佇列"}],
                "result": None,
                "error": "",
                "created_at": now,
                "updated_at": now,
            }
        context["job_id"] = job_id

    thread = threading.Thread(target=_run_job, args=(job_id, messages, context), daemon=True)
    thread.start()
    return {"success": True, "job_id": job_id, "status": "queued"}


def _stored_steps(job_id: str) -> list[dict[str, Any]]:
    """Read the persisted step list for a job.

    DB-backed jobs keep steps inside ``result['steps']``; fallback jobs keep them
    in the process-local dict. Returns [] when neither exists.
    """
    with _JOBS_LOCK:
        if job_id in _JOBS:
            return list((_JOBS.get(job_id) or {}).get("steps") or [])
    try:
        row = job_store.get_job(str(job_id))
    except Exception:
        return []
    result = (row or {}).get("result")
    steps = result.get("steps") if isinstance(result, dict) else None
    return list(steps) if isinstance(steps, list) else []


def _run_job(job_id: str, messages: list[dict[str, Any]], context: dict[str, Any]) -> None:
    _set_job(job_id, status="running", message="Agent 正在啟動")
    try:
        result = dict(run_assistant(messages, context | {"job_id": job_id}) or {})
        # The step list produced during the run is authoritative for progress: keep
        # the DB-persisted steps instead of letting the final payload wipe them.
        stored_steps = _stored_steps(job_id)
        if stored_steps:
            result["steps"] = stored_steps
        if not isinstance(result.get("steps"), list):
            result["steps"] = []
        ok = bool(result.get("success"))
        _set_job(
            job_id,
            status="finished" if ok else "failed",
            message="Agent 已完成" if ok else result.get("error") or "Agent 失敗",
            result=result,
            error=result.get("error") or "",
        )
    except Exception as exc:
        _set_job(job_id, status="failed", message=str(exc), error=str(exc))


def start_assistant_job(
    messages: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
    user_id: str = "",
) -> dict[str, Any]:
    return _start_job(messages, context or {}, user_id=user_id)


def get_assistant_job(job_id: str, user_id: str | None = None) -> dict[str, Any]:
    job_id = str(job_id or "")
    _cleanup_jobs()
    with _JOBS_LOCK:
        job = _JOBS.get(job_id)
    if job is not None:
        owner = str(job.get("user_id") or "")
        if user_id is not None and str(user_id) != owner:
            return {"success": False, "error": "AI job not found"}
        return {
            "success": True,
            "job_id": job.get("job_id") or job_id,
            "status": job.get("status"),
            "message": job.get("message") or "",
            "steps": list(job.get("steps") or []),
            "result": job.get("result"),
            "error": job.get("error") or "",
        }

    try:
        row = job_store.get_job(job_id, user_id if user_id is not None else None)
    except Exception as exc:  # pragma: no cover - defensive
        print(f"[assistant] job lookup failed: {exc}")
        row = None
    if not row:
        return {"success": False, "error": "AI job not found"}

    result = row.get("result") if isinstance(row.get("result"), dict) else None
    steps = result.get("steps") if isinstance(result, dict) and isinstance(result.get("steps"), list) else []
    db_status = row.get("status")
    return {
        "success": True,
        "job_id": row.get("id") or job_id,
        "status": _DB_STATUS_OUT.get(db_status, db_status),
        "message": row.get("message") or "",
        "steps": steps,
        "result": result,
        "error": row.get("error") or "",
    }


def _is_deck_request(text: str) -> bool:
    markers = ("牌組", "卡組", "構築", "构筑", "deck", "Deck", "推薦", "推荐", "組一套", "组一套")
    return any(marker in str(text or "") for marker in markers)


def _append_tool_result(tool_results: list[dict[str, Any]], tool: str, args: dict[str, Any], result: Any) -> Any:
    item = {"tool": tool, "args": args, "result": result, "result_count": _result_count(result)}
    tool_results.append(item)
    return result


def _append_tool_error(tool_results: list[dict[str, Any]], tool: str, args: dict[str, Any], error: Exception | str) -> None:
    tool_results.append({"tool": tool, "args": args, "error": str(error), "result_count": 0})


def _publish_new_steps(context: dict[str, Any], tool_results: list[dict[str, Any]], published_count: int) -> int:
    job_id = context.get("job_id")
    for item in tool_results[published_count:]:
        _append_job_step(job_id, _tool_step_status(item))
    return len(tool_results)


def _prefetch_context(user_text: str, context: dict[str, Any], deep_think: bool, deck_request: bool) -> list[dict[str, Any]]:
    """Legacy prefetch. Disabled unless context.force_prefetch is true.

    The agent must choose semantic search / Limitless itself via skills.
    """
    if not user_text:
        return []
    if not context.get("force_prefetch"):
        return []

    language = str(context.get("language") or "tw")
    tool_results: list[dict[str, Any]] = []
    should_prefetch = deep_think or deck_request
    if not should_prefetch:
        return tool_results

    search_text = _prefetch_query(user_text, context)
    published_count = 0
    try:
        _append_job_step(context.get("job_id"), {"status": "running", "message": "搜尋 H/I/J 標準卡池"})
        cards = semantic_search_cards(search_text, 12 if deep_think else 8, {"language": language, "standard_marks": list(STANDARD_MARKS)})
        _append_tool_result(tool_results, "semantic_search_cards", {"query": search_text, "limit": 12 if deep_think else 8}, cards)
    except Exception as exc:
        _append_tool_error(tool_results, "semantic_search_cards", {"query": search_text}, exc)
    published_count = _publish_new_steps(context, tool_results, published_count)

    try:
        _append_job_step(context.get("job_id"), {"status": "running", "message": "搜尋 Limitless Meta 牌組索引"})
        meta = search_meta_decks(search_text, 6 if deep_think else 4)
        _append_tool_result(tool_results, "search_meta_decks", {"archetype_or_query": search_text, "limit": 6 if deep_think else 4}, meta)
    except Exception as exc:
        meta = []
        _append_tool_error(tool_results, "search_meta_decks", {"archetype_or_query": search_text}, exc)
    published_count = _publish_new_steps(context, tool_results, published_count)

    if deep_think:
        try:
            _append_job_step(context.get("job_id"), {"status": "running", "message": "整理 Meta 共通牌與樣本牌表"})
            summary = summarize_meta_archetype(search_text)
            _append_tool_result(tool_results, "summarize_meta_archetype", {"query": search_text}, summary)
        except Exception as exc:
            _append_tool_error(tool_results, "summarize_meta_archetype", {"query": search_text}, exc)
        published_count = _publish_new_steps(context, tool_results, published_count)

    deck_ids: list[str] = []
    for ref in meta or []:
        if isinstance(ref, dict) and ref.get("deck_id"):
            deck_id = str(ref.get("deck_id"))
            if deck_id not in deck_ids:
                deck_ids.append(deck_id)
        for sample in (ref.get("sample_decks") if isinstance(ref, dict) else None) or []:
            deck_id = str(sample.get("deck_id") or "")
            if deck_id and deck_id not in deck_ids:
                deck_ids.append(deck_id)

    for deck_id in deck_ids[: (2 if deep_think else 1)]:
        try:
            _append_job_step(context.get("job_id"), {"status": "running", "message": f"讀取 Limitless 牌表 {deck_id}"})
            decklist = get_meta_deck_cards(deck_id, language, "normal")
            _append_tool_result(tool_results, "get_meta_deck_cards", {"deck_id": deck_id, "language": language, "mode": "normal"}, decklist)
            if decklist.get("success") and decklist.get("cards"):
                analysis = _analyze_deck_play_plan(decklist, "limitless_sample", language)
                analysis["search_query_terms"] = _analysis_query_terms(analysis)
                context.setdefault("meta_deck_analysis", []).append(analysis)
                tool_results.append({
                    "tool": "analyze_meta_deck_play_plan",
                    "args": {"deck_id": deck_id},
                    "result": analysis,
                    "result_count": analysis.get("total_count") or 0,
                    "detail": analysis.get("user_visible_summary"),
                })
        except Exception as exc:
            _append_tool_error(tool_results, "get_meta_deck_cards", {"deck_id": deck_id, "language": language, "mode": "normal"}, exc)
        published_count = _publish_new_steps(context, tool_results, published_count)

    if context.get("deck"):
        try:
            _append_job_step(context.get("job_id"), {"status": "running", "message": "分析目前牌組結構"})
            analysis = analyze_current_deck(context.get("deck") or [])
            _append_tool_result(tool_results, "analyze_current_deck", {"deck": "[current_deck]"}, analysis)
        except Exception as exc:
            _append_tool_error(tool_results, "analyze_current_deck", {"deck": "[current_deck]"}, exc)
        _publish_new_steps(context, tool_results, published_count)

    return tool_results


def _run_tool(name: str, args: dict[str, Any], context: dict[str, Any]) -> Any:
    language = str(args.get("language") or context.get("language") or "tw")
    if language not in ("tw", "jp"):
        language = "tw"
    if name == "semantic_search_cards":
        filters = args.get("filters") if isinstance(args.get("filters"), dict) else {}
        filters.setdefault("language", language)
        filters.setdefault("standard_marks", list(STANDARD_MARKS))
        return semantic_search_cards(str(args.get("query") or ""), int(args.get("limit") or 10), filters)
    if name == "search_card_roles":
        return search_card_roles(
            str(args.get("role") or ""),
            args.get("params") if isinstance(args.get("params"), dict) else None,
            str(args.get("query") or ""),
            int(args.get("limit") or 10),
        )
    if name == "get_card_detail":
        return get_card_detail(str(args.get("card_id") or ""), language)
    if name == "search_meta_decks":
        return search_meta_decks(str(args.get("archetype_or_query") or ""), int(args.get("limit") or 5))
    if name == "search_japanese_decks_by_card":
        return search_japanese_decks_by_card(
            str(args.get("card_name") or ""),
            int(args.get("min_count") or 1),
            int(args.get("limit") or 5),
            str(args.get("sort") or "count"),
        )
    if name == "get_meta_deck_cards":
        return get_meta_deck_cards(
            str(args.get("deck_id") or ""),
            str(args.get("language") or context.get("language") or "tw"),
            str(args.get("mode") or "normal"),
        )
    if name == "summarize_meta_archetype":
        return summarize_meta_archetype(str(args.get("query") or ""))
    if name == "analyze_current_deck":
        deck = args.get("deck") if isinstance(args.get("deck"), list) else context.get("deck") or []
        return analyze_current_deck(deck)
    if name == "propose_deck_patch":
        deck = args.get("deck") if isinstance(args.get("deck"), list) else context.get("deck") or []
        return propose_deck_patch(
            str(args.get("intent") or _last_user_message(context.get("messages") or [])),
            deck,
            args.get("retrieved_context") if isinstance(args.get("retrieved_context"), dict) else {},
            language,
        )
    if name == "list_skills":
        return {"skills": list_skills()}
    if name == "get_skill":
        skill = get_skill(str(args.get("name") or ""))
        if not skill:
            return {"error": "unknown skill", "available": [item["name"] for item in list_skills()]}
        return skill
    if name == "get_matchup_sheet":
        return get_matchup_sheet(str(args.get("user_archetype") or ""), str(args.get("opponent_archetype") or ""))
    if name == "list_format_snapshot":
        from services.limitless_decks.meta_field import build_field_stats
        stats = build_field_stats(days=int(args.get("days") or 30))
        if not stats.get("success"):
            return stats
        return {
            "window": stats.get("window"),
            "tournaments": {
                "count": (stats.get("tournaments") or {}).get("count"),
                "total_players": (stats.get("tournaments") or {}).get("total_players"),
                "median_players": (stats.get("tournaments") or {}).get("median_players"),
            },
            "decks": stats.get("decks"),
            "combinations": (stats.get("combinations") or [])[:20],
        }
    if name == "get_format_brief":
        from services.limitless_decks.meta_field import get_brief
        return get_brief(str(args.get("combo_key") or ""))
    raise ValueError(f"Unknown tool: {name}")


def _collect_cards_from_value(value: Any, seen: set[str] | None = None) -> list[dict[str, Any]]:
    seen = seen or set()
    cards = []
    if isinstance(value, dict):
        if value.get("card_id") and value.get("name"):
            key = f"{value.get('language') or ''}:{value.get('card_id')}"
            if key not in seen:
                seen.add(key)
                cards.append(value)
        for nested in value.values():
            cards.extend(_collect_cards_from_value(nested, seen))
    elif isinstance(value, list):
        for item in value:
            cards.extend(_collect_cards_from_value(item, seen))
    return cards


def _collect_meta_from_value(value: Any) -> list[dict[str, Any]]:
    refs = []
    if isinstance(value, dict):
        if value.get("type") in ("deck", "archetype") or value.get("deck_id") or value.get("sample_decks"):
            refs.append(value)
        for nested in value.values():
            refs.extend(_collect_meta_from_value(nested))
    elif isinstance(value, list):
        for item in value:
            refs.extend(_collect_meta_from_value(item))
    compact = []
    seen = set()
    for ref in refs:
        key = ref.get("deck_id") or ref.get("archetype") or ref.get("title") or json.dumps(ref, ensure_ascii=False, default=str)[:120]
        if key in seen:
            continue
        seen.add(key)
        compact.append(ref)
    return compact[:10]


def _collect_decklists_from_value(value: Any) -> list[dict[str, Any]]:
    decklists = []
    if isinstance(value, dict):
        if value.get("cards") and (value.get("deck") or value.get("deck_id") or value.get("name")):
            deck = value.get("deck") if isinstance(value.get("deck"), dict) else {}
            decklists.append({
                "deck_id": value.get("deck_id") or deck.get("deck_id"),
                "name": value.get("name") or deck.get("archetype_zh") or deck.get("archetype") or deck.get("title") or value.get("deck_id"),
                "source": value.get("source") or "limitless",
                "language": value.get("language") or "tw",
                "cards": _normalize_decklist_cards(value.get("cards") or []),
                "meta": deck,
            })
        for nested in value.values():
            decklists.extend(_collect_decklists_from_value(nested))
    elif isinstance(value, list):
        for item in value:
            decklists.extend(_collect_decklists_from_value(item))
    compact = []
    seen = set()
    for decklist in decklists:
        key = decklist.get("deck_id") or decklist.get("name") or json.dumps(decklist, ensure_ascii=False, default=str)[:120]
        if key in seen:
            continue
        seen.add(key)
        compact.append(decklist)
    return compact[:3]


def _usable_decklist(decklist: Any) -> bool:
    if not isinstance(decklist, dict):
        return False
    cards = _normalize_decklist_cards(decklist.get("cards") or [])
    total = sum(int(card.get("count") or 0) for card in cards)
    return len(cards) >= 10 and total >= 40


def _normalize_output_decklists(decklists: Any) -> list[dict[str, Any]]:
    normalized = []
    if not isinstance(decklists, list):
        return normalized
    for decklist in decklists:
        if not isinstance(decklist, dict):
            continue
        deck = decklist.get("meta") if isinstance(decklist.get("meta"), dict) else {}
        if not deck and isinstance(decklist.get("deck"), dict):
            deck = decklist.get("deck")
        item = dict(decklist)
        item["deck_id"] = item.get("deck_id") or deck.get("deck_id")
        item["name"] = item.get("name") or deck.get("archetype_zh") or deck.get("archetype") or deck.get("title") or item.get("deck_id")
        item["source"] = item.get("source") or "assistant"
        item["language"] = item.get("language") or "tw"
        item["cards"] = _normalize_decklist_cards(item.get("cards") or [])
        item["meta"] = deck
        normalized.append(item)
    return normalized


def _decklist_summary_lines(decklist: dict[str, Any]) -> list[str]:
    cards = _normalize_decklist_cards(decklist.get("cards") or [])
    total = sum(int(card.get("count") or 0) for card in cards)
    if not cards or total < 40:
        return []

    sections = {"pokemon": 0, "trainer": 0, "energy": 0}
    for card in cards:
        section = card.get("section") or "unknown"
        if section in sections:
            sections[section] += int(card.get("count") or 0)

    meta = decklist.get("meta") if isinstance(decklist.get("meta"), dict) else {}
    title = decklist.get("name") or meta.get("archetype_zh") or meta.get("archetype") or decklist.get("deck_id") or "推薦牌表"
    source_parts = [
        meta.get("player_name"),
        f"#{meta.get('placement')}" if meta.get("placement") else "",
        meta.get("tournament_title"),
        meta.get("date"),
    ]
    source = " · ".join(str(part) for part in source_parts if part)

    pokemon_names = [
        str(card.get("name") or "")
        for card in cards
        if card.get("section") == "pokemon" and card.get("name")
    ][:6]

    lines = [
        f"我推薦先參考 **{title}** 這副完整構築。",
    ]
    if source:
        lines.append(f"來源：{source}。")
    lines.append(f"牌表合計 {total} 張：Pokémon {sections['pokemon']} / Trainer {sections['trainer']} / Energy {sections['energy']}。")
    if pokemon_names:
        lines.append("核心寶可夢包含：" + "、".join(dict.fromkeys(pokemon_names)) + "。")
    lines.append("完整 60 張已在下方用卡圖展示；文字區只保留打法與調整重點，避免重複列牌表。")
    return lines


def _strip_visual_decklist_text(answer: str) -> str:
    text = str(answer or "").strip()
    if not text:
        return ""

    cut_markers = (
        "📋 牌組列表",
        "牌組列表（60",
        "完整牌組列表",
        "完整牌表",
        "以下是完整牌表",
        "以下是牌表",
        "#### 🔹 寶可夢",
        "#### 寶可夢",
    )
    cut_positions = [text.find(marker) for marker in cut_markers if marker in text]
    if cut_positions:
        text = text[:min(cut_positions)].strip()

    cleaned_lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            cleaned_lines.append(line)
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            continue
        if re.match(r"^\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)+\|?$", stripped):
            continue
        if re.match(r"^#{2,6}\s*(?:🔹\s*)?(寶可夢|Pokemon|Trainer|訓練家|Energy|能量)", stripped, re.I):
            continue
        if re.match(r"^\s*(?:[-*]\s*)?\d+\s*[xX張]?\s+[^，。]{2,40}\s+(?:[A-Z0-9]{2,8}\s+)?\d{1,3}(?:/\d{1,3})?\s+[A-J]\s*$", stripped):
            continue
        cleaned_lines.append(line)

    text = "\n".join(cleaned_lines)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


_NUMERIC_CLAIM_RE = re.compile(r"(\d{1,4})\s*(HP|hp|Hp|傷害|伤害|打點|打点|張|张|血)")


def _numeric_grounding_terms(
    tool_results: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> set[str]:
    '''Collect every numeric token the agent actually has (tool trace + context).'''
    grounded: list[str] = []
    try:
        grounded.append(json.dumps(tool_results, ensure_ascii=False, default=str)[:400000])
    except Exception:
        pass
    if context:
        for key in ("deck", "referenced_cards", "referenced_tabs", "meta_deck_analysis"):
            value = context.get(key)
            if not value:
                continue
            try:
                grounded.append(json.dumps(value, ensure_ascii=False, default=str))
            except Exception:
                continue
    return set(re.findall(r"\d{1,4}", '\n'.join(grounded)))


def _enforce_numeric_grounding(
    answer: str,
    tool_results: list[dict[str, Any]],
    context: dict[str, Any] | None = None,
) -> str:
    '''Blank numbers in game-meaningful claims (HP / damage points / card counts)
    that never appeared in the tool trace or context.

    Conservative by design: only numbers attached to a unit token are checked, so
    ordinary prose numbers and tool-derived totals survive untouched.
    '''
    text = str(answer or "")
    if not text:
        return text
    trace_numbers = _numeric_grounding_terms(tool_results, context)
    flagged: list[str] = []

    def replace(match: re.Match[str]) -> str:
        number, unit = match.group(1), match.group(2)
        if number in trace_numbers:
            return match.group(0)
        flagged.append(number)
        return f"[未驗證數字]{unit}"

    text = _NUMERIC_CLAIM_RE.sub(replace, text)
    if flagged:
        unique = list(dict.fromkeys(flagged))
        text = text + "\n（已標記未在工具結果中出現的數字：" + ", ".join(unique) + "）"
    return text

def _evidence_terms(cards: list[dict[str, Any]], meta_refs: list[dict[str, Any]], decklists: list[dict[str, Any]]) -> set[str]:
    terms: set[str] = set()

    def add(value: Any) -> None:
        text = str(value or "").strip()
        if len(text) >= 2:
            terms.add(text)

    for card in cards or []:
        if isinstance(card, dict):
            add(card.get("name"))
            add(card.get("card_name"))
            add(card.get("jp_card_name"))
            add(card.get("japanese_name"))

    for ref in meta_refs or []:
        if not isinstance(ref, dict):
            continue
        add(ref.get("archetype"))
        add(ref.get("title"))
        add(ref.get("tournament_title"))
        for card in ref.get("matched_cards") or ref.get("common_cards") or []:
            if isinstance(card, dict):
                add(card.get("name"))
                add(card.get("jp_name"))

    for decklist in decklists or []:
        if not isinstance(decklist, dict):
            continue
        add(decklist.get("name"))
        meta = decklist.get("meta") if isinstance(decklist.get("meta"), dict) else {}
        add(meta.get("archetype"))
        add(meta.get("archetype_zh"))
        add(meta.get("title"))
        add(meta.get("title_zh"))
        for card in decklist.get("cards") or []:
            if isinstance(card, dict):
                add(card.get("name"))
                add(card.get("card_name"))
                add(card.get("jp_card_name"))

    return terms


def _strip_unverified_examples(answer: str, evidence: set[str]) -> str:
    text = str(answer or "")
    if not text or not evidence:
        return text

    def has_evidence(fragment: str) -> bool:
        compact = str(fragment or "").replace(" ", "")
        return any(term and term.replace(" ", "") in compact for term in evidence)

    def replace_parenthetical(match: re.Match[str]) -> str:
        fragment = match.group(1) or ""
        return match.group(0) if has_evidence(fragment) else ""

    text = re.sub(r"[（(]\s*(?:如|例如|像)\s*([^）)]+)\s*[）)]", replace_parenthetical, text)
    text = re.sub(r"\s+([，。；])", r"\1", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def _ensure_deck_recommendation_answer(answer: str, decklists: list[dict[str, Any]], deck_request: bool) -> str:
    if not deck_request or not decklists:
        return answer
    usable = next((decklist for decklist in decklists if _usable_decklist(decklist)), None)
    if not usable:
        return answer

    cards = _normalize_decklist_cards(usable.get("cards") or [])
    total = sum(int(card.get("count") or 0) for card in cards)
    title = str(usable.get("name") or usable.get("deck_id") or "")
    answer_text = _strip_visual_decklist_text(answer)
    already_specific = title and title in answer_text and (str(total) in answer_text or "完整" in answer_text or "牌表" in answer_text)
    summary = "\n".join(_decklist_summary_lines(usable))
    if already_specific:
        return answer_text
    if answer_text:
        return summary + "\n\n" + answer_text
    return summary


def _normalize_decklist_cards(cards: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    if not isinstance(cards, list):
        return normalized
    for card in cards:
        if not isinstance(card, dict):
            continue
        name = str(card.get("name") or card.get("card_name") or card.get("jp_card_name") or "").strip()
        if not name:
            continue
        item = dict(card)
        item["name"] = name
        item["card_name"] = item.get("card_name") or name
        try:
            item["count"] = max(0, int(item.get("count") or 0))
        except Exception:
            item["count"] = 0
        section = str(item.get("section") or "").lower()
        if section not in ("pokemon", "trainer", "energy", "unknown"):
            ctype = str(item.get("card_type") or "").lower()
            if "energy" in ctype:
                section = "energy"
            elif "pok" in ctype:
                section = "pokemon"
            else:
                section = "trainer"
        item["section"] = section
        normalized.append(item)
    return normalized


def _normalize_final(
    final_data: dict[str, Any],
    context: dict[str, Any],
    tool_results: list[dict[str, Any]],
    fallback_answer: str = "",
) -> dict[str, Any]:
    cards = final_data.get("cards") if isinstance(final_data.get("cards"), list) else []
    meta_refs = final_data.get("meta_references") if isinstance(final_data.get("meta_references"), list) else []
    decklists = final_data.get("decklists") if isinstance(final_data.get("decklists"), list) else []
    actions = final_data.get("deck_actions") if isinstance(final_data.get("deck_actions"), list) else []
    diff = final_data.get("deck_diff") if isinstance(final_data.get("deck_diff"), dict) else None

    for action in actions:
        if not isinstance(action, dict):
            continue
        if action.get("type") not in ("add_card", "add") or action.get("card"):
            continue
        name = str(action.get("card_name") or action.get("name") or "").strip()
        if not name:
            continue
        matches = semantic_search_cards(name, 3, {"language": context.get("language") or "tw", "standard_marks": list(STANDARD_MARKS)})
        exact = [card for card in matches if str(card.get("name") or "").strip() == name]
        card = (exact or matches or [None])[0]
        if card:
            action["card"] = card

    seen_cards = set()
    merged_cards = []
    for card in cards + _collect_cards_from_value([item.get("result") for item in tool_results]):
        if not isinstance(card, dict) or not card.get("card_id"):
            continue
        key = f"{card.get('language') or ''}:{card.get('card_id')}"
        if key not in seen_cards:
            seen_cards.add(key)
            merged_cards.append(card)

    merged_meta = meta_refs or _collect_meta_from_value([item.get("result") for item in tool_results])
    tool_decklists = _collect_decklists_from_value([item.get("result") for item in tool_results])
    final_decklists = _normalize_output_decklists(decklists)
    merged_decklists = tool_decklists or [decklist for decklist in final_decklists if _usable_decklist(decklist)]
    deck_request = _is_deck_request(_last_user_message(context.get("messages") or []))
    answer = _strip_unverified_examples(
        str(final_data.get("answer") or fallback_answer or "我已根據標準 H/I/J 卡池與可用資料整理建議。"),
        _evidence_terms(merged_cards, merged_meta, merged_decklists),
    )
    answer = _enforce_numeric_grounding(answer, tool_results, context)
    answer = _ensure_deck_recommendation_answer(
        answer,
        merged_decklists,
        deck_request,
    )
    if actions:
        diff = build_deck_diff(context.get("deck") or [], actions)
    if not diff:
        diff = {"current_total": len(context.get("deck") or []), "projected_total": len(context.get("deck") or []), "additions": [], "removals": [], "warnings": []}

    return {
        "success": True,
        "skill": str(final_data.get("skill") or ""),
        "answer": answer,
        "cards": merged_cards[:20],
        "meta_references": merged_meta[:10],
        "decklists": merged_decklists[:3],
        "deck_actions": actions,
        "deck_diff": diff,
        "matchup_sheet": final_data.get("matchup_sheet") if isinstance(final_data.get("matchup_sheet"), dict) else None,
        "tool_trace": [
            {
                "tool": item.get("tool"),
                "args": item.get("args"),
                "result_count": item.get("result_count"),
                "error": item.get("error"),
                "message": _tool_step_message(item),
                "detail": item.get("detail"),
            }
            for item in tool_results
        ],
        "steps": [
            {
                "status": "done" if not item.get("error") else "error",
                "message": _tool_step_message(item),
                "tool": item.get("tool"),
                "result_count": item.get("result_count"),
                "detail": item.get("detail"),
            }
            for item in tool_results
        ],
    }


def _deterministic_fallback(messages: list[dict[str, Any]], context: dict[str, Any], error: str = "") -> dict[str, Any]:
    user_text = _last_user_message(messages)
    language = str(context.get("language") or "tw")
    cards = semantic_search_cards(user_text, 8, {"language": language, "standard_marks": list(STANDARD_MARKS)})
    answer = "AI 模型暫時無法完成 Agent 流程，我只做咗一次標準卡檢索。"
    if error:
        answer += f" 錯誤：{error}"

    tool_results: list[dict[str, Any]] = []
    _append_tool_result(tool_results, "semantic_search_cards", {"query": user_text}, cards)
    final = {
        "skill": "card_lookup",
        "answer": answer,
        "cards": cards,
        "meta_references": [],
        "deck_actions": [],
        "deck_diff": {},
    }
    result = _normalize_final(final, context, tool_results)
    result["warning"] = error
    return result


_INCOMPLETE_ANSWER_MARKERS = (
    "呼叫工具",
    "呼叫工具",
    "tool_call",
    "tool_calls",
    "需要搜尋",
    "讓我查",
    "我來查",
    "稍等我",
    "請稍等",
)


def _tool_name_tokens() -> tuple[str, ...]:
    names = []
    for schema in TOOL_SCHEMAS:
        fn = schema.get("function") if isinstance(schema, dict) else None
        name = str((fn or {}).get("name") or "").strip()
        if name:
            names.append(name)
    return tuple(names)


def _answer_looks_incomplete(
    final_data: dict[str, Any],
    content: str,
    tool_results: list[dict[str, Any]],
) -> bool:
    """One-shot heuristic: should a no-tool-call answer be repaired once?

    Only fires when the model clearly did not finish (empty answer, tool-name
    references with no executed tool, or text that stops mid-sentence). A normal
    well-formed answer returns False, preserving the existing fast path.
    """
    answer = str(final_data.get("answer") or "").strip() if isinstance(final_data, dict) else ""
    if not answer:
        answer = str(content or "").strip()
    if not answer:
        return True
    lowered = answer.lower()
    if any(marker.lower() in lowered for marker in _INCOMPLETE_ANSWER_MARKERS):
        return True
    if not tool_results:
        for token in _tool_name_tokens():
            if token and token in answer:
                return True
    return False


def run_assistant(messages: list[dict[str, Any]], context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    context["messages"] = messages
    user_text = _last_user_message(messages)
    if not user_text:
        return {
            "success": False,
            "error": "Missing message content",
            "answer": "",
            "cards": [],
            "meta_references": [],
            "decklists": [],
            "deck_actions": [],
            "deck_diff": {},
            "tool_trace": [],
        }

    language = str(context.get("language") or "tw")
    if language not in ("tw", "jp"):
        language = "tw"
    context["language"] = language
    if not isinstance(context.get("deck"), list):
        context["deck"] = []
    context["referenced_tabs"] = _compact_referenced_tabs(context.get("referenced_tabs"))
    context["referenced_cards"] = _compact_referenced_cards(context.get("referenced_cards"))
    if context["referenced_tabs"] or context["referenced_cards"]:
        _append_job_step(
            context.get("job_id"),
            {
                "status": "running",
                "message": f"讀取引用內容：{len(context['referenced_tabs'])} 個 tab / {len(context['referenced_cards'])} 張卡",
            },
        )
    context["standard_marks"] = [mark for mark in context.get("standard_marks") or list(STANDARD_MARKS) if mark in STANDARD_MARKS] or list(STANDARD_MARKS)
    deep_think = bool(context.get("deep_think"))
    is_deck_request = _is_deck_request(user_text)
    tool_results: list[dict[str, Any]] = []
    if context.get("force_prefetch"):
        tool_results.extend(_analyze_referenced_tabs(context))
        tool_results.extend(_prefetch_context(user_text, context, deep_think, is_deck_request))

    agent_messages: list[dict[str, Any]] = [
        {"role": "system", "content": SYSTEM_PROMPT + "\n\n" + skill_catalog_text()},
        *[
            {"role": item.get("role", "user"), "content": str(item.get("content") or "")}
            for item in (messages or [])[-10:]
            if item.get("role") in ("user", "assistant")
        ],
        {
            "role": "user",
            "content": json.dumps(
                {
                    "task": user_text,
                    "current_deck_count": len(context.get("deck") or []),
                    "current_deck": context.get("deck") or [],
                    "referenced_tabs": context.get("referenced_tabs") or [],
                    "referenced_cards": context.get("referenced_cards") or [],
                    "standard_marks": context["standard_marks"],
                    "language": language,
                    "deep_think": deep_think,
                    "instruction": (
                        "Pick one skill. Call list_skills or get_skill if needed. "
                        "Then call tools yourself. Simple questions must not search Limitless. "
                        + (
                            "DeepThink: you may use more tool steps and compare evidence, but still pick tools per skill."
                            if deep_think else
                            "Prefer the shortest tool path."
                        )
                    ),
                    "deck_request": is_deck_request,
                    "final_json_contract": FINAL_JSON_INSTRUCTIONS,
                },
                ensure_ascii=False,
                default=str,
            ),
        },
    ]

    default_steps = 12 if deep_think else 6
    # clamp 用戶輸入，防止 max_agent_steps 被濫用造成無限 LLM 呼叫
    try:
        max_steps = int(context.get("max_agent_steps") or default_steps)
    except (TypeError, ValueError):
        max_steps = default_steps
    max_steps = min(max(1, max_steps), 30)
    repair_used = False
    try:
        for _ in range(max_steps):
            _append_job_step(context.get("job_id"), {"status": "running", "message": "呼叫 AI 模型決定下一步工具"})
            message = chat_message(agent_messages, temperature=0.2, tools=TOOL_SCHEMAS, tool_choice="auto")
            tool_calls = message.get("tool_calls") or []
            if not tool_calls:
                content = str(message.get("content") or "").strip()
                final_data = _json_loads(content, {})
                if not isinstance(final_data, dict) or not final_data:
                    final_data = {"answer": content}
                # One repair pass: a no-tool-call turn that looks unfinished or
                # references tools without running them gets a chance to continue.
                if not repair_used and _answer_looks_incomplete(final_data, content, tool_results):
                    repair_used = True
                    _append_job_step(context.get("job_id"), {"status": "running", "message": "回覆未完成，要求模型補完工具或答案"})
                    agent_messages.append({"role": "assistant", "content": content})
                    agent_messages.append({
                        "role": "user",
                        "content": (
                            "上一則回覆未完成或提到工具但沒有實際呼叫。請立即呼叫缺少的工具取得資料，"
                            "或直接輸出完整的最終 JSON（含 answer）。不要只說要做什麼。"
                        ),
                    })
                    continue
                return _normalize_final(final_data, context, tool_results)

            agent_messages.append({
                "role": "assistant",
                "content": message.get("content") or "",
                "tool_calls": tool_calls,
            })
            for call in tool_calls:
                fn = call.get("function") or {}
                name = str(fn.get("name") or "")
                args = _json_loads(fn.get("arguments"), {}) or {}
                try:
                    _append_job_step(context.get("job_id"), {"status": "running", "message": f"執行工具：{name}"})
                    result = _run_tool(name, args, context)
                    result_count = _result_count(result)
                    item = {"tool": name, "args": args, "result": result, "result_count": result_count}
                    tool_results.append(item)
                    _append_job_step(context.get("job_id"), _tool_step_status(item))
                    content = _compact_tool_result(result)
                except Exception as exc:
                    item = {"tool": name, "args": args, "error": str(exc), "result_count": 0}
                    tool_results.append(item)
                    _append_job_step(context.get("job_id"), _tool_step_status(item))
                    content = json.dumps({"error": str(exc)}, ensure_ascii=False)
                agent_messages.append({
                    "role": "tool",
                    "tool_call_id": call.get("id"),
                    "name": name,
                    "content": content,
                })

        final_prompt = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Conversation and tool trace are complete. "
                    + FINAL_JSON_INSTRUCTIONS
                    + "\nReference context:\n"
                    + _context_budget(
                        [
                            {
                                "label": "reference_context",
                                "value": {
                                    "current_deck_count": len(context.get("deck") or []),
                                    "current_deck": context.get("deck") or [],
                                    "referenced_tabs": context.get("referenced_tabs") or [],
                                    "referenced_cards": context.get("referenced_cards") or [],
                                    "referenced_tab_analysis": context.get("referenced_tab_analysis") or [],
                                    "meta_deck_analysis": context.get("meta_deck_analysis") or [],
                                },
                            }
                        ],
                        8000,
                    )
                    + "\nTool results:\n"
                    + _context_budget([{"label": "tool_results", "value": tool_results}], 16000)
                ),
            },
        ]
        _append_job_step(context.get("job_id"), {"status": "running", "message": "整理最終推薦與可視覺化牌表"})
        content = chat_completion(final_prompt, response_format={"type": "json_object"})
        final_data = _json_loads(content, {}) or {"answer": content}
        return _normalize_final(final_data, context, tool_results)
    except AIConfigError as exc:
        return {"success": False, "error": str(exc), "answer": "", "cards": [], "meta_references": [], "decklists": [], "deck_actions": [], "deck_diff": {}, "tool_trace": []}
    except AIClientError as exc:
        return _deterministic_fallback(messages, context, str(exc))
    except Exception as exc:
        return _deterministic_fallback(messages, context, str(exc))


def assistant_health() -> dict[str, Any]:
    '''Aggregate AI subsystem health for diagnostics routes.

    No network call is made unless the chat provider is configured;
    embeddings checks only read already-indexed rows.
    '''
    degraded: list[str] = []

    try:
        provider_health = check_provider_health('chat') or {}
    except Exception as exc:  # pragma: no cover - defensive
        provider_health = {'configured': False, 'error': str(exc)}
    configured = bool(provider_health.get('configured'))
    provider = str(provider_health.get('provider') or '')
    model = str(provider_health.get('model') or '')
    if not configured:
        degraded.append('chat provider not configured')
    elif provider_health.get('reachable') is False:
        degraded.append('chat provider unreachable: ' + str(provider_health.get('error') or ''))

    embeddings = 0
    embeddings_configured = False
    index_info: dict[str, Any] = {}
    try:
        from .indexer import index_health

        index_info = index_health() or {}
    except Exception as exc:  # pragma: no cover - defensive
        index_info = {'error': 'indexer unavailable: ' + str(exc)}
    try:
        embeddings = int(index_info.get('embeddings') or 0)
    except (TypeError, ValueError):
        embeddings = 0
    embeddings_configured = bool(index_info.get('configured'))
    if not embeddings_configured:
        degraded.append('embeddings not configured')
    if index_info.get('error'):
        degraded.append('index: ' + str(index_info.get('error')))

    return {
        'configured': configured,
        'provider': provider,
        'model': model,
        'embeddings': embeddings,
        'embeddings_configured': embeddings_configured,
        'degraded_reasons': degraded,
    }
