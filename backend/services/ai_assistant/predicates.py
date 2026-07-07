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


def predicates_match_filter(predicates: list[dict[str, Any]], predicate_filter: Any) -> bool:
    if not predicate_filter:
        return True
    specs = predicate_filter if isinstance(predicate_filter, list) else [predicate_filter]
    specs = [spec for spec in specs if isinstance(spec, dict)]
    if not specs:
        return True
    return any(_predicate_matches_spec(predicate, spec) for predicate in predicates for spec in specs)


def _predicate_matches_spec(predicate: dict[str, Any], spec: dict[str, Any]) -> bool:
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
        if predicate.get(key) != expected:
            return False

    if spec.get("value") not in (None, "") and _to_int(predicate.get("value")) != _to_int(spec.get("value")):
        return False
    if spec.get("min_value") not in (None, "") and _to_int(predicate.get("value")) < _to_int(spec.get("min_value")):
        return False
    if spec.get("max_value") not in (None, "") and _to_int(predicate.get("value")) > _to_int(spec.get("max_value")):
        return False

    return True


def _to_int(value: Any) -> int:
    try:
        return int(value)
    except Exception:
        return 0
