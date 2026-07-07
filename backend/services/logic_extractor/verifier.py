"""Deterministic verifier for extracted JP card-logic predicates."""

from __future__ import annotations

from typing import Any

from services.logic_extractor.schema import OPERATOR_TOKENS, THRESHOLD_TYPES, ExtractionReport, Predicate


def _error(index: int, code: str, message: str, predicate: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": index,
        "code": code,
        "message": message,
        "predicate": dict(predicate),
    }


def verify_predicates(source_text: str, predicates: list[dict[str, Any]]) -> ExtractionReport:
    """Ground predicates in source spans and deterministically map threshold ops.

    Invalid predicates are omitted from the verified output. In particular,
    threshold operators are always derived from raw_token; any supplied op is
    overwritten after raw_token/raw_value pass literal span checks.
    """

    verified: list[Predicate] = []
    validation_errors = []

    for index, predicate in enumerate(predicates):
        current: dict[str, Any] = dict(predicate)
        span = current.get("jp_source_span")
        if not isinstance(span, str) or not span:
            validation_errors.append(_error(index, "missing_span", "jp_source_span is required", current))
            continue
        if span not in source_text:
            validation_errors.append(_error(index, "ungrounded_span", "jp_source_span is not in source_text", current))
            continue

        if current.get("type") in THRESHOLD_TYPES:
            raw_token = current.get("raw_token")
            raw_value = current.get("raw_value")
            if raw_token not in OPERATOR_TOKENS:
                validation_errors.append(_error(index, "unknown_operator_token", "raw_token is not supported", current))
                continue
            if raw_token not in span:
                validation_errors.append(_error(index, "operator_token_not_in_span", "raw_token is not in jp_source_span", current))
                continue
            if raw_value is None or str(raw_value) not in span:
                validation_errors.append(_error(index, "raw_value_not_in_span", "raw_value is not in jp_source_span", current))
                continue

            try:
                current["value"] = int(str(raw_value))
            except ValueError:
                validation_errors.append(_error(index, "invalid_raw_value", "raw_value must be an integer", current))
                continue
            current["raw_value"] = str(raw_value)
            current["op"] = OPERATOR_TOKENS[raw_token]

        verified.append(current)  # type: ignore[arg-type]

    return {
        "predicates": verified,
        "validation_errors": validation_errors,
    }
