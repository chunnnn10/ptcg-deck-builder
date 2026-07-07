#!/usr/bin/env python3
"""Evaluate structured card-logic predicate extraction against a golden set."""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


DEFAULT_FIXTURE = Path(__file__).resolve().parent / "fixtures" / "golden_cards.json"

OPERATOR_TOKENS = {
    "以下": "<=",
    "未満": "<",
    "以上": ">=",
    "超過": ">",
    "ちょうど": "==",
}

THRESHOLD_TYPES = {"hp_threshold", "count_threshold"}


@dataclass(frozen=True)
class EvalCase:
    case_id: str
    category: str
    difficulty: str
    source_text: str
    gold_predicates: list[dict[str, Any]]


def load_cases(path: Path) -> list[EvalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = []
    for card in payload.get("cards", []):
        cases.append(
            EvalCase(
                case_id=card["case_id"],
                category=card.get("category", "uncategorized"),
                difficulty=card.get("difficulty", "unknown"),
                source_text=card.get("source_text", ""),
                gold_predicates=card.get("gold_predicates", []),
            )
        )
    return cases


def load_predictions(path: Path | None, use_gold: bool, cases: Iterable[EvalCase]) -> dict[str, list[dict[str, Any]]]:
    if use_gold:
        return {case.case_id: list(case.gold_predicates) for case in cases}
    if path is None:
        return {}

    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict) and "cards" in payload:
        return {
            card["case_id"]: card.get("predicates", card.get("gold_predicates", []))
            for card in payload["cards"]
        }
    if isinstance(payload, dict):
        return {case_id: list(preds) for case_id, preds in payload.items()}
    if isinstance(payload, list):
        return {item["case_id"]: item.get("predicates", []) for item in payload}
    raise ValueError(f"Unsupported predictions format in {path}")


def action_key(predicate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        predicate.get("type"),
        predicate.get("target"),
        predicate.get("destination"),
        predicate.get("effect"),
    )


def threshold_key(predicate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        predicate.get("type"),
        predicate.get("op"),
        predicate.get("value"),
        predicate.get("applies_to") or predicate.get("dim"),
    )


def count_matches(gold: Iterable[tuple[Any, ...]], predicted: Iterable[tuple[Any, ...]]) -> int:
    gold_counts = Counter(gold)
    pred_counts = Counter(predicted)
    return sum(min(count, pred_counts[key]) for key, count in gold_counts.items())


def safe_ratio(numerator: int, denominator: int) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def f1(precision: float | None, recall: float | None) -> float | None:
    if precision is None or recall is None:
        return None
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


def span_is_grounded(source_text: str, predicate: dict[str, Any]) -> bool:
    span = predicate.get("jp_source_span")
    return isinstance(span, str) and bool(span) and span in source_text


def evaluate_cases(cases: Iterable[EvalCase], predictions: dict[str, list[dict[str, Any]]]) -> dict[str, dict[str, Any]]:
    buckets: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))

    def add(bucket: str, metric: str, value: int) -> None:
        buckets[bucket][metric] += value

    for case in cases:
        preds = predictions.get(case.case_id, [])
        gold = case.gold_predicates
        bucket_names = ("overall", f"category:{case.category}", f"difficulty:{case.difficulty}")

        gold_thresholds = [threshold_key(item) for item in gold if item.get("type") in THRESHOLD_TYPES]
        pred_thresholds = [threshold_key(item) for item in preds if item.get("type") in THRESHOLD_TYPES]
        threshold_matches = count_matches(gold_thresholds, pred_thresholds)

        gold_actions = [action_key(item) for item in gold]
        pred_actions = [action_key(item) for item in preds]
        action_matches = count_matches(gold_actions, pred_actions)

        grounded = sum(1 for item in preds if span_is_grounded(case.source_text, item))
        known_op_preds = [
            item for item in preds
            if item.get("type") in THRESHOLD_TYPES and item.get("raw_token") in OPERATOR_TOKENS
        ]
        correct_ops = sum(1 for item in known_op_preds if item.get("op") == OPERATOR_TOKENS[item["raw_token"]])

        for bucket in bucket_names:
            add(bucket, "cases", 1)
            add(bucket, "gold_thresholds", len(gold_thresholds))
            add(bucket, "pred_thresholds", len(pred_thresholds))
            add(bucket, "threshold_matches", threshold_matches)
            add(bucket, "gold_actions", len(gold_actions))
            add(bucket, "pred_actions", len(pred_actions))
            add(bucket, "action_matches", action_matches)
            add(bucket, "predicates", len(preds))
            add(bucket, "grounded_spans", grounded)
            add(bucket, "known_op_predictions", len(known_op_preds))
            add(bucket, "correct_ops", correct_ops)

    return {name: render_metrics(counts) for name, counts in sorted(buckets.items())}


def render_metrics(counts: dict[str, int]) -> dict[str, Any]:
    threshold_precision = safe_ratio(counts["threshold_matches"], counts["pred_thresholds"])
    action_precision = safe_ratio(counts["action_matches"], counts["pred_actions"])
    action_recall = safe_ratio(counts["action_matches"], counts["gold_actions"])
    span_grounding = safe_ratio(counts["grounded_spans"], counts["predicates"])
    op_accuracy = safe_ratio(counts["correct_ops"], counts["known_op_predictions"])
    hallucination_rate = None if span_grounding is None else 1 - span_grounding

    return {
        "cases": counts["cases"],
        "threshold_precision": threshold_precision,
        "op_accuracy": op_accuracy,
        "action_f1": f1(action_precision, action_recall),
        "hallucination_rate": hallucination_rate,
        "span_grounding_rate": span_grounding,
        "counts": dict(counts),
    }


def format_metric(value: Any) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, float):
        return f"{value:.3f}"
    return str(value)


def print_report(results: dict[str, dict[str, Any]]) -> None:
    headers = [
        "bucket",
        "cases",
        "threshold_precision",
        "op_accuracy",
        "action_f1",
        "hallucination_rate",
        "span_grounding_rate",
    ]
    print("\t".join(headers))
    for bucket, metrics in results.items():
        print("\t".join(format_metric(metrics.get(header)) if header != "bucket" else bucket for header in headers))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fixture", type=Path, default=DEFAULT_FIXTURE, help="Golden fixture JSON path")
    parser.add_argument("--predictions", type=Path, help="Predictions JSON path")
    parser.add_argument(
        "--gold-as-predictions",
        action="store_true",
        help="Use gold predicates as predictions for runner smoke tests",
    )
    parser.add_argument("--json", action="store_true", help="Print full JSON metrics")
    args = parser.parse_args()

    cases = load_cases(args.fixture)
    predictions = load_predictions(args.predictions, args.gold_as_predictions, cases)
    results = evaluate_cases(cases, predictions)

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print_report(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
