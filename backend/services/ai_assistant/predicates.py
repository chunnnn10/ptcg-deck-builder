from __future__ import annotations

import json
from typing import Any


def parse_predicates(value: Any) -> list[dict[str, Any]]:
    if not value:
        return []
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    if isinstance(value, str):
        try:
            return parse_predicates(json.loads(value))
        except Exception:
            return []
    return []


def predicate_lines(predicates: list[dict[str, Any]]) -> list[str]:
    lines = []
    for predicate in predicates:
        predicate_type = str(predicate.get("type") or "").strip()
        if not predicate_type:
            continue
        pieces = [predicate_type]
        for key in (
            "target",
            "applies_to",
            "dim",
            "op",
            "value",
            "count",
            "max_count",
            "look_count",
            "choose_count",
        ):
            value = predicate.get(key)
            if value not in (None, "", []):
                pieces.append(f"{key}={value}")
        span = str(predicate.get("jp_source_span") or "").strip()
        if span:
            pieces.append(f"span={span}")
        lines.append(" | ".join(pieces))
    return lines


# ===========================================================================
# Structured filter matching
#
# A filter spec can describe two different things:
#   * extracted card-text logic (``predicates``), e.g. {"type": "search_deck"}
#   * plain card attributes (raw fields), e.g. {"type": "hp_threshold",
#     "op": "<=", "value": 90} meaning "this card's own HP is <= 90".
#
# Filtering must consider BOTH: otherwise cards whose predicates are empty
# (extractor not run yet) and filters about raw fields (hp / card_type /
# sub_type) return [] even when matching cards exist.
# ===========================================================================

_OPERATOR_ALIASES = {
    "<=": "<=",
    "≤": "<=",
    "≦": "<=",
    "以下": "<=",
    "lte": "<=",
    "le": "<=",
    "max": "<=",
    "<": "<",
    "未満": "<",
    "lt": "<",
    "less_than": "<",
    ">=": ">=",
    "≥": ">=",
    "≧": ">=",
    "以上": ">=",
    "gte": ">=",
    "ge": ">=",
    "min": ">=",
    ">": ">",
    "超過": ">",
    "gt": ">",
    "greater_than": ">",
    "==": "==",
    "=": "==",
    "===": "==",
    "ちょうど": "==",
    "eq": "==",
    "equals": "==",
}

_CARD_TYPE_ALIASES = {
    "pokemon": "pokemon",
    "pokemons": "pokemon",
    "ポケモン": "pokemon",
    "寶可夢": "pokemon",
    "宝可梦": "pokemon",
    "神奇寶貝": "pokemon",
    "神奇宝贝": "pokemon",
    "trainer": "trainer",
    "trainers": "trainer",
    "トレーナー": "trainer",
    "訓練家": "trainer",
    "训练家": "trainer",
    "energy": "energy",
    "energies": "energy",
    "エネルギー": "energy",
    "能量": "energy",
}

_APPLIES_TO_ALIASES = {
    "pokemon": "pokemon",
    "ポケモン": "pokemon",
    "寶可夢": "pokemon",
    "宝可梦": "pokemon",
    "basic_pokemon": "basic_pokemon",
    "basicpokemon": "basic_pokemon",
    "basic": "basic_pokemon",
    "たね": "basic_pokemon",
    "たねポケモン": "basic_pokemon",
    "基礎": "basic_pokemon",
    "基础": "basic_pokemon",
    "基礎寶可夢": "basic_pokemon",
    "基础宝可梦": "basic_pokemon",
    "attached_pokemon_remaining_hp": "attached_pokemon_remaining_hp",
    "remaining_hp": "attached_pokemon_remaining_hp",
    "殘餘hp": "attached_pokemon_remaining_hp",
    "残りhp": "attached_pokemon_remaining_hp",
}

_BASIC_SUB_TYPE_TOKENS = ("基礎", "基本", "basic", "たね")


def _normalize_text(value: Any) -> str:
    text = str(value or "").strip().lower()
    for source, target in (("é", "e"), ("è", "e"), ("ê", "e"), ("á", "a")):
        text = text.replace(source, target)
    return text


def normalize_op(value: Any) -> str | None:
    text = _normalize_text(value).replace(" ", "")
    if not text:
        return None
    return _OPERATOR_ALIASES.get(text, text)


def normalize_card_type(value: Any) -> str | None:
    return _CARD_TYPE_ALIASES.get(_normalize_text(value))


def normalize_applies_to(value: Any) -> str | None:
    text = _normalize_text(value).replace("-", "_").replace(" ", "_")
    if not text:
        return None
    return _APPLIES_TO_ALIASES.get(text, text)


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0


def _to_int_or_none(value: Any) -> int | None:
    if value in (None, "", []):
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _bounds_for(op: str | None, value: int) -> tuple[int | None, int | None]:
    if op in (None, "", "=="):
        return value, value
    if op == "<=":
        return None, value
    if op == "<":
        return None, value - 1
    if op == ">=":
        return value, None
    if op == ">":
        return value + 1, None
    return value, value


def hp_bounds_from_spec(spec: Any) -> dict[str, Any] | None:
    """Derive inclusive HP bounds from a filter spec, or None if the spec is not about HP.

    Shared by in-memory card matching and the SQL candidate path so both
    interpret a spec identically.
    """
    if not isinstance(spec, dict):
        return None
    keys = set(spec)
    predicate_type = _normalize_text(spec.get("type"))
    target = _normalize_text(spec.get("target"))
    explicit_hp = bool({"hp", "min_hp", "max_hp"} & keys)
    if predicate_type != "hp_threshold" and target != "hp" and not explicit_hp:
        return None

    low: int | None = None
    high: int | None = None

    value = _to_int_or_none(spec.get("value"))
    if value is not None:
        op = normalize_op(spec.get("op"))
        if not op and predicate_type == "hp_threshold":
            # hp_threshold 本身就係「界線以下」嘅意思；冇寫 op 時當 <= ，
            # 避免 LLM 少寫 op 就靜默回 []。
            low, high = None, value
        else:
            low, high = _bounds_for(op, value)

    min_value = _to_int_or_none(spec.get("min_value"))
    if min_value is not None:
        low = min_value if low is None else max(low, min_value)
    max_value = _to_int_or_none(spec.get("max_value"))
    if max_value is not None:
        high = max_value if high is None else min(high, max_value)

    hp_value = _to_int_or_none(spec.get("hp"))
    if hp_value is not None:
        low = hp_value if low is None else max(low, hp_value)
        high = hp_value if high is None else min(high, hp_value)
    min_hp = _to_int_or_none(spec.get("min_hp"))
    if min_hp is not None:
        low = min_hp if low is None else max(low, min_hp)
    max_hp = _to_int_or_none(spec.get("max_hp"))
    if max_hp is not None:
        high = max_hp if high is None else min(high, max_hp)

    return {"min": low, "max": high, "applies_to": normalize_applies_to(spec.get("applies_to"))}


def _normalize_filter_specs(predicate_filter: Any) -> list[dict[str, Any]]:
    specs = predicate_filter if isinstance(predicate_filter, list) else [predicate_filter]
    return [spec for spec in specs if isinstance(spec, dict)]


def _card_is_basic(card: dict[str, Any]) -> bool:
    sub_type = _normalize_text(card.get("sub_type"))
    if any(token in sub_type for token in _BASIC_SUB_TYPE_TOKENS):
        return True
    stage = _normalize_text(card.get("evolution_stage"))
    if stage in ("基礎", "basic", "たね"):
        return not _normalize_text(card.get("evolves_from"))
    return False


def _sub_type_matches(actual: Any, expected: Any) -> bool:
    if isinstance(expected, (list, tuple, set)):
        return any(_sub_type_matches(actual, item) for item in expected)
    actual_text = _normalize_text(actual)
    expected_text = _normalize_text(expected)
    if not expected_text:
        return True
    if normalize_applies_to(expected_text) == "basic_pokemon":
        return any(token in actual_text for token in _BASIC_SUB_TYPE_TOKENS)
    return expected_text in actual_text or actual_text in expected_text


def _raw_fields_match_spec(card: dict[str, Any], spec: dict[str, Any]) -> bool:
    """Match a spec against a card's RAW fields (hp / card_type / sub_type / ...).

    All recognized raw aspects in one spec must hold together, so a combined
    spec such as ``{'type': 'hp_threshold', 'value': 90, 'sub_type': '基礎'}``
    behaves like a conjunction rather than ignoring the non-HP keys.
    """
    if not isinstance(card, dict) or not isinstance(spec, dict):
        return False

    matched = False

    hp_spec = hp_bounds_from_spec(spec)
    if hp_spec is not None and hp_spec.get("applies_to") != "attached_pokemon_remaining_hp":
        low = hp_spec.get("min")
        high = hp_spec.get("max")
        if low is not None or high is not None:
            matched = True
            raw_hp = _to_int_or_none(card.get("hp"))
            if raw_hp is None:
                return False
            applies_to = hp_spec.get("applies_to")
            if applies_to in ("pokemon", "basic_pokemon"):
                if normalize_card_type(card.get("card_type")) != "pokemon":
                    return False
                if applies_to == "basic_pokemon" and not _card_is_basic(card):
                    return False
            if low is not None and raw_hp < low:
                return False
            if high is not None and raw_hp > high:
                return False

    expected_card_type = normalize_card_type(spec.get("card_type"))
    if expected_card_type:
        if normalize_card_type(card.get("card_type")) != expected_card_type:
            return False
        matched = True

    if spec.get("sub_type") not in (None, "", []):
        if not _sub_type_matches(card.get("sub_type"), spec.get("sub_type")):
            return False
        matched = True

    if spec.get("element_type") not in (None, "", []):
        if _normalize_text(card.get("element_type")) != _normalize_text(spec.get("element_type")):
            return False
        matched = True

    if spec.get("name") not in (None, ""):
        if _normalize_text(spec.get("name")) not in _normalize_text(card.get("name")):
            return False
        matched = True

    expected_mark = str(spec.get("regulation_mark") or "").strip().upper()
    if expected_mark:
        if str(card.get("regulation_mark") or "").strip().upper() != expected_mark:
            return False
        matched = True

    for key in ("set_code", "set_number", "rarity"):
        expected = spec.get(key)
        if expected in (None, "", []):
            continue
        if _normalize_text(expected) not in _normalize_text(card.get(key)):
            return False
        matched = True

    return matched


def predicates_match_filter(predicates: list[dict[str, Any]], predicate_filter: Any) -> bool:
    """Predicate-only matching (kept for backwards compatibility with older callers)."""
    if not predicate_filter:
        return True
    specs = _normalize_filter_specs(predicate_filter)
    if not specs:
        return True
    return any(
        _predicate_matches_spec(predicate, spec)
        for predicate in predicates or []
        for spec in specs
    )


def card_matches_filter(card: dict[str, Any], predicate_filter: Any) -> bool:
    """True when a card payload satisfies the structured filter.

    A spec is satisfied by EITHER the card's verified ``predicates`` OR the
    card's raw fields. Raw matching keeps filters working for cards whose
    predicate extraction has not run (empty ``predicates``) and for filters
    that describe plain card attributes such as
    ``{'type': 'hp_threshold', 'op': '<=', 'value': 90}``.
    """
    if not predicate_filter:
        return True
    specs = _normalize_filter_specs(predicate_filter)
    if not specs:
        return True
    if not isinstance(card, dict):
        return False

    predicates = card.get("predicates")
    predicates = [item for item in predicates if isinstance(item, dict)] if isinstance(predicates, list) else []

    for spec in specs:
        if _raw_fields_match_spec(card, spec):
            return True
        if any(_predicate_matches_spec(predicate, spec) for predicate in predicates):
            return True
    return False


def _predicate_matches_spec(predicate: dict[str, Any], spec: dict[str, Any]) -> bool:
    if not isinstance(predicate, dict) or not isinstance(spec, dict):
        return False

    expected_types = spec.get("types") or spec.get("type")
    if expected_types:
        if isinstance(expected_types, str):
            expected_types = [expected_types]
        if predicate.get("type") not in expected_types:
            return False

    for key in ("op", "applies_to", "dim", "target", "destination", "source", "scope"):
        expected = spec.get(key)
        if expected in (None, "", []):
            continue
        if key == "op":
            if normalize_op(predicate.get(key)) != normalize_op(expected):
                return False
        elif key == "applies_to":
            if normalize_applies_to(predicate.get(key)) != normalize_applies_to(expected):
                return False
        elif predicate.get(key) != expected:
            return False

    for key in ("count", "max_count", "look_count", "choose_count"):
        expected_count = spec.get(key)
        if expected_count in (None, "", []):
            continue
        if _to_int(predicate.get(key)) != _to_int(expected_count):
            return False

    if spec.get("value") not in (None, "") and _to_int(predicate.get("value")) != _to_int(spec.get("value")):
        return False
    if spec.get("min_value") not in (None, "") and _to_int(predicate.get("value")) < _to_int(spec.get("min_value")):
        return False
    if spec.get("max_value") not in (None, "") and _to_int(predicate.get("value")) > _to_int(spec.get("max_value")):
        return False

    return True
