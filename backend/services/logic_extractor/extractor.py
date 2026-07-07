"""Rule-based Phase 1 extractor for JP canonical card text.

This module is deliberately pure: it does not read from or write to a DB.
"""

from __future__ import annotations

import re
from typing import Any

from services.logic_extractor.schema import ExtractionReport, Predicate
from services.logic_extractor.verifier import verify_predicates


OPERATOR_PATTERN = r"以下|未満|以上|超過|ちょうど"


def extract_card_logic(card_text: str) -> list[Predicate]:
    """Extract verified predicates from JP canonical card text."""

    return extract_card_logic_with_report(card_text)["predicates"]


def extract_card_logic_with_report(card_text: str) -> ExtractionReport:
    """Extract predicates and include verifier diagnostics for development."""

    candidates = _extract_candidates(card_text or "")
    return verify_predicates(card_text or "", candidates)


def _extract_candidates(card_text: str) -> list[dict[str, Any]]:
    predicates: list[dict[str, Any]] = []

    _extract_fixed_conditions(card_text, predicates)
    _extract_hp_thresholds(card_text, predicates)
    _extract_count_thresholds(card_text, predicates)
    _extract_search_deck(card_text, predicates)
    _extract_discard(card_text, predicates)
    _extract_draw(card_text, predicates)
    _extract_damage_counters(card_text, predicates)

    return _dedupe(predicates)


def _extract_fixed_conditions(card_text: str, predicates: list[dict[str, Any]]) -> None:
    fixed_spans = [
        ("自分の番に1回使える", {"type": "condition", "effect": "once_during_own_turn"}),
        ("自分の手札をすべて山札にもどして切る", {"type": "condition", "effect": "shuffle_hand_into_deck"}),
        (
            "このポケモンと、ついているすべてのカードを、山札にもどして切る",
            {"type": "condition", "effect": "shuffle_self_and_attached_cards_into_deck"},
        ),
        (
            "残りのカードは、山札の下にもどす",
            {"type": "condition", "effect": "put_remaining_cards_on_bottom_of_deck"},
        ),
        (
            "にげるためのエネルギーは、すべてなくなる",
            {
                "type": "condition",
                "effect": "retreat_cost_becomes_zero",
                "condition": "attached_pokemon_remaining_hp <= 30",
            },
        ),
    ]
    for span, payload in fixed_spans:
        if span in card_text:
            predicates.append({**payload, "jp_source_span": span})

    for match in re.finditer(r"(にげるためのエネルギーが(\d+)個ぶん少なくなる)", card_text):
        predicates.append(
            {
                "type": "condition",
                "effect": "retreat_cost_reduced",
                "value": int(match.group(2)),
                "jp_source_span": match.group(1),
            }
        )

    for match in re.finditer(r"(自分のサイドの残り枚数が(\d+)枚なら)", card_text):
        predicates.append(
            {
                "type": "condition",
                "effect": "own_prizes_remaining_equals",
                "value": int(match.group(2)),
                "jp_source_span": match.group(1),
            }
        )


def _extract_hp_thresholds(card_text: str, predicates: list[dict[str, Any]]) -> None:
    pattern = re.compile(r"((?:そのポケモンの残り)?HPが「(\d+)」(" + OPERATOR_PATTERN + r")(?:の?(?:たね)?ポケモン)?)")
    for match in pattern.finditer(card_text):
        span = match.group(1)
        if "残りHP" in span:
            applies_to = "attached_pokemon_remaining_hp"
        elif "たねポケモン" in span:
            applies_to = "basic_pokemon"
        else:
            applies_to = "pokemon"

        predicates.append(
            {
                "type": "hp_threshold",
                "applies_to": applies_to,
                "raw_value": match.group(2),
                "raw_token": match.group(3),
                "jp_source_span": span,
            }
        )


def _extract_count_thresholds(card_text: str, predicates: list[dict[str, Any]]) -> None:
    threshold_pattern = re.compile(r"((相手|自分)のサイドの残り枚数が(\d+)枚(" + OPERATOR_PATTERN + r"))")
    for match in threshold_pattern.finditer(card_text):
        side = match.group(2)
        dim = "opponent_prizes_remaining" if side == "相手" else "own_prizes_remaining"
        predicates.append(
            {
                "type": "count_threshold",
                "dim": dim,
                "raw_value": match.group(3),
                "raw_token": match.group(4),
                "jp_source_span": match.group(1),
            }
        )


def _extract_search_deck(card_text: str, predicates: list[dict[str, Any]]) -> None:
    hp_search_pattern = re.compile(
        r"(?P<span>自分の山札から、HPが「\d+」(?:"
        + OPERATOR_PATTERN
        + r")の?(?P<basic>たね)?ポケモンを(?P<count>\d+)枚(?P<upto>まで)?選び、(?:相手に見せて(?:から)?、)?(?P<destination>ベンチに出す|手札に加える))"
    )
    for match in hp_search_pattern.finditer(card_text):
        span = match.group("span")
        destination = "bench" if match.group("destination") == "ベンチに出す" else "hand"
        predicate: dict[str, Any] = {
            "type": "search_deck",
            "target": "basic_pokemon" if match.group("basic") else "pokemon",
            "destination": destination,
            "jp_source_span": span,
        }
        count = int(match.group("count"))
        if match.group("upto"):
            predicate["max_count"] = count
        else:
            predicate["count"] = count
        if "相手に見せ" in span:
            predicate["reveal_to_opponent"] = True
        predicates.append(predicate)

    top_cards_pattern = re.compile(r"(自分の山札を上から(\d+)枚見て、どちらか(\d+)枚を選び、手札に加える)")
    for match in top_cards_pattern.finditer(card_text):
        predicates.append(
            {
                "type": "search_deck",
                "target": "top_cards",
                "look_count": int(match.group(2)),
                "choose_count": int(match.group(3)),
                "destination": "hand",
                "jp_source_span": match.group(1),
            }
        )


def _extract_discard(card_text: str, predicates: list[dict[str, Any]]) -> None:
    span = "自分の手札をすべてトラッシュ"
    if span in card_text:
        predicates.append(
            {
                "type": "discard",
                "source": "hand",
                "scope": "all",
                "jp_source_span": span,
            }
        )


def _extract_draw(card_text: str, predicates: list[dict[str, Any]]) -> None:
    for match in re.finditer(r"((?:自分の)?山札を(\d+)枚引く)", card_text):
        predicates.append(
            {
                "type": "draw",
                "count": int(match.group(2)),
                "jp_source_span": match.group(1),
            }
        )

    for match in re.finditer(r"(引く枚数は(\d+)枚になる)", card_text):
        condition = _conditional_draw_context(card_text, match.start())
        predicate: dict[str, Any] = {
            "type": "draw",
            "count": int(match.group(2)),
            "jp_source_span": match.group(1),
        }
        if condition:
            predicate["condition"] = condition
        predicates.append(predicate)


def _conditional_draw_context(card_text: str, draw_start: int) -> str | None:
    prefix = card_text[:draw_start]
    if re.search(r"相手のサイドの残り枚数が3枚以下なら、?$", prefix):
        return "opponent_prizes_remaining <= 3"
    if re.search(r"自分のサイドの残り枚数が6枚なら、?$", prefix):
        return "own_prizes_remaining == 6"
    return None


def _extract_damage_counters(card_text: str, predicates: list[dict[str, Any]]) -> None:
    for match in re.finditer(r"(ダメカン(\d+)個を、相手のベンチポケモンに好きなようにのせる)", card_text):
        predicates.append(
            {
                "type": "place_damage_counters",
                "count": int(match.group(2)),
                "target": "opponent_bench_pokemon",
                "distribution": "as_you_like",
                "jp_source_span": match.group(1),
            }
        )


def _dedupe(predicates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    unique = []
    for predicate in predicates:
        key = (
            predicate.get("type"),
            predicate.get("jp_source_span"),
            predicate.get("target"),
            predicate.get("destination"),
            predicate.get("effect"),
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(predicate)
    return unique
