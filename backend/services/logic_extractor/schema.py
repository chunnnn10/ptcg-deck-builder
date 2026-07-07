"""Shared schema constants for structured card-logic predicates."""

from __future__ import annotations

from typing import Any, Literal, TypedDict


PredicateType = Literal[
    "hp_threshold",
    "count_threshold",
    "draw",
    "discard",
    "search_deck",
    "switch",
    "heal",
    "attach_energy",
    "place_damage_counters",
    "evolve",
    "condition",
]

OPERATOR_TOKENS = {
    "以下": "<=",
    "未満": "<",
    "以上": ">=",
    "超過": ">",
    "ちょうど": "==",
}

THRESHOLD_TYPES = {"hp_threshold", "count_threshold"}


class Predicate(TypedDict, total=False):
    type: PredicateType
    jp_source_span: str
    raw_token: str
    raw_value: str
    op: str
    value: int
    applies_to: str
    dim: str
    count: int
    max_count: int
    look_count: int
    choose_count: int
    target: str
    destination: str
    source: str
    scope: str
    effect: str
    condition: str
    reveal_to_opponent: bool
    distribution: str


class ValidationError(TypedDict):
    index: int
    code: str
    message: str
    predicate: dict[str, Any]


class ExtractionReport(TypedDict):
    predicates: list[Predicate]
    validation_errors: list[ValidationError]
