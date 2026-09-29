#!/usr/bin/env python3
"""Agent benchmark harness for the PTCG AI assistant.

Run a FIXED question set through the real ``services.ai_assistant.assistant``
agent loop with different (model, prompt-variant) configurations and emit a
side-by-side, human-readable comparison.

Usage
-----
    # fake LLM, no network/cost (harness self-test)
    python backend/eval/agent_bench.py run \
        --db postgresql://ptcg:ptcg_secret@127.0.0.1:5432/ptcg_bench \
        --model fake-model --variant baseline --dry-run

    # real model, real run
    python backend/eval/agent_bench.py run \
        --db postgresql://ptcg:ptcg_secret@127.0.0.1:5432/ptcg_bench \
        --model gpt-4o-mini --variant strict-evidence --limit 5

How the overrides work
----------------------
* ``--db``            : sets ``os.environ["DATABASE_URL"]`` BEFORE importing
                        ``config``/``database``, so the whole process talks to
                        the isolated DB. Refuses ``ptcg_db``.
* ``--model``         : monkeypatches ``ai_settings.get_ai_setting`` so that
                        ``AI_MODEL`` and ``AI_CHAT_MODEL`` resolve to the given
                        model. ``ai_assistant.client._env`` reads settings at
                        call time via that function, so the override takes
                        effect without a restart. ``AI_CHAT_MODEL`` env is also
                        set as a belt-and-braces fallback.
* ``--variant``       : loads the full system prompt from ``variants.py`` and
                        monkeypatches ``assistant.SYSTEM_PROMPT`` for the run.
                        If the module instead exposes a builder function
                        (detected via ``hasattr``), that builder is patched.
                        ``baseline`` (``None``) leaves everything untouched.
                        The original value is always restored afterwards.
* ``--dry-run``       : replaces the LLM call with a scripted fake so the
                        harness logic can be exercised without network or cost.
                        Both ``assistant.chat_message`` and
                        ``assistant.chat_completion`` are patched (assistant.py
                        imported them by value), plus ``client`` for safety.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

EVAL_DIR = Path(__file__).resolve().parent
BACKEND_ROOT = Path(__file__).resolve().parents[1]
QUESTIONS_PATH = EVAL_DIR / "questions.json"
RESULTS_DIR = EVAL_DIR / "results"

# Candidate builder names to look for if a later assistant.py version replaces
# the module-level SYSTEM_PROMPT string with a function.
BUILDER_CANDIDATES = (
    "build_system_prompt",
    "system_prompt_for",
    "get_system_prompt",
)


def _guard_db(url: str) -> None:
    if not url:
        raise SystemExit("--db is required (isolated benchmark DB URL)")
    if "/ptcg_db" in url or url.rstrip("/").endswith("ptcg_db"):
        raise SystemExit(f"REFUSING to run against protected production DB: {url}")


def load_questions(limit: int | None = None) -> list[dict[str, Any]]:
    payload = json.loads(QUESTIONS_PATH.read_text(encoding="utf-8"))
    items = payload.get("questions") if isinstance(payload, dict) else payload
    questions = []
    for index, item in enumerate(items or []):
        if isinstance(item, str):
            questions.append({"id": f"q{index + 1:02d}", "category": "uncategorized", "question": item})
        elif isinstance(item, dict) and item.get("question"):
            questions.append(
                {
                    "id": item.get("id") or f"q{index + 1:02d}",
                    "category": item.get("category") or "uncategorized",
                    "question": str(item["question"]),
                }
            )
    if limit is not None:
        questions = questions[: max(0, limit)]
    return questions


# ---------------------------------------------------------------------------
# Fake LLM (dry-run)
# ---------------------------------------------------------------------------

class ScriptedFakeLLM:
    """Deterministic stand-in for the LLM call.

    First turn returns two tool calls (``list_skills`` + ``analyze_current_deck``)
    so the harness records a real tool trace; every later turn returns a final
    JSON contract object. Both tools are pure (no network, no embeddings) which
    keeps the fake run cheap and stable.
    """

    def __init__(self) -> None:
        self.turn = 0

    def reset(self) -> None:
        self.turn = 0

    def _final_content(self) -> str:
        return json.dumps(
            {
                "skill": "plain_chat",
                "answer": "[dry-run] 呢個係假 LLM 嘅測試答案，用嚟驗證 harness 流程。",
                "cards": [],
                "meta_references": [],
                "decklists": [],
                "deck_actions": [],
                "deck_diff": {"current_total": 0, "projected_total": 0, "additions": [], "removals": [], "warnings": []},
                "matchup_sheet": None,
            },
            ensure_ascii=False,
        )

    def chat_message(self, messages: list[dict[str, Any]], **kwargs: Any) -> dict[str, Any]:
        self.turn += 1
        if self.turn == 1:
            return {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "fake-call-1",
                        "type": "function",
                        "function": {"name": "list_skills", "arguments": "{}"},
                    },
                    {
                        "id": "fake-call-2",
                        "type": "function",
                        "function": {"name": "analyze_current_deck", "arguments": "{}"},
                    },
                ],
            }
        return {"role": "assistant", "content": self._final_content(), "tool_calls": []}

    def chat_completion(self, messages: list[dict[str, Any]], **kwargs: Any) -> str:
        return self._final_content()


# ---------------------------------------------------------------------------
# Monkeypatch helpers
# ---------------------------------------------------------------------------

class ModelOverride:
    """Force AI_MODEL / AI_CHAT_MODEL for the duration of the run."""

    KEYS = ("AI_MODEL", "AI_CHAT_MODEL")

    def __init__(self, ai_settings_module: Any, model: str) -> None:
        self._settings = ai_settings_module
        self._model = model
        self._original = None
        self._env_backup: dict[str, str | None] = {}

    def __enter__(self) -> "ModelOverride":
        self._original = self._settings.get_ai_setting
        original = self._original
        model = self._model

        def _patched(key: str, default: str = "") -> str:
            if key in self.KEYS:
                return model
            return original(key, default)

        self._settings.get_ai_setting = _patched  # type: ignore[assignment]
        for key in self.KEYS:
            self._env_backup[key] = os.environ.get(key)
            os.environ[key] = model
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._original is not None:
            self._settings.get_ai_setting = self._original  # type: ignore[assignment]
        for key, value in self._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class VariantOverride:
    """Swap the assistant system prompt for a run, then restore it."""

    def __init__(self, assistant_module: Any, prompt: str | None) -> None:
        self._assistant = assistant_module
        self._prompt = prompt
        self._restore: list[tuple[str, Any]] = []

    def __enter__(self) -> "VariantOverride":
        if self._prompt is None:
            return self
        module = self._assistant

        # Preferred: patch the module-level SYSTEM_PROMPT string.
        if hasattr(module, "SYSTEM_PROMPT") and isinstance(getattr(module, "SYSTEM_PROMPT"), str):
            self._restore.append(("SYSTEM_PROMPT", module.SYSTEM_PROMPT))
            module.SYSTEM_PROMPT = self._prompt
            return self

        # Fallback: a later version exposes a builder function instead.
        for name in BUILDER_CANDIDATES:
            original = getattr(module, name, None)
            if callable(original):
                self._restore.append((name, original))
                setattr(module, name, lambda *a, _p=self._prompt, **k: _p)
                return self

        print(
            "[agent_bench] WARNING: assistant exposes neither SYSTEM_PROMPT nor a known "
            f"builder {BUILDER_CANDIDATES}; variant '{self._prompt[:24]}...' ignored.",
            flush=True,
        )
        return self

    def __exit__(self, *exc: Any) -> None:
        for name, value in self._restore:
            setattr(self._assistant, name, value)


class FakeLLMOverride:
    """Patch both the assistant-local and client LLM entry points."""

    def __init__(self, assistant_module: Any, client_module: Any, fake: ScriptedFakeLLM) -> None:
        self._assistant = assistant_module
        self._client = client_module
        self._fake = fake
        self._restore: list[tuple[Any, str, Any]] = []

    def __enter__(self) -> "FakeLLMOverride":
        fake = self._fake
        # assistant.py does ``from .client import chat_message, chat_completion``
        # so we must patch the names in BOTH modules.
        for module, name, value in (
            (self._assistant, "chat_message", fake.chat_message),
            (self._assistant, "chat_completion", fake.chat_completion),
            (self._client, "chat_message", fake.chat_message),
            (self._client, "chat_completion", fake.chat_completion),
        ):
            if hasattr(module, name):
                self._restore.append((module, name, getattr(module, name)))
                setattr(module, name, value)
        return self

    def __exit__(self, *exc: Any) -> None:
        for module, name, value in self._restore:
            setattr(module, name, value)


# ---------------------------------------------------------------------------
# Result collection / rendering
# ---------------------------------------------------------------------------

def _summarize_tool_trace(trace: Any) -> list[dict[str, Any]]:
    items = []
    for entry in trace or []:
        if not isinstance(entry, dict):
            continue
        args = entry.get("args") if isinstance(entry.get("args"), dict) else {}
        items.append(
            {
                "tool": entry.get("tool"),
                "args": args,
                "result_count": entry.get("result_count"),
                "error": entry.get("error"),
            }
        )
    return items


def run_one(assistant: Any, question: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    started = time.perf_counter()
    record: dict[str, Any] = {
        "id": question.get("id"),
        "category": question.get("category"),
        "question": question.get("question"),
        "answer": "",
        "skill": "",
        "success": None,
        "tool_trace": [],
        "steps": [],
        "latency_s": None,
        "error": None,
    }
    try:
        messages = [{"role": "user", "content": question["question"]}]
        result = assistant.run_assistant(messages, dict(context)) or {}
        record["answer"] = str(result.get("answer") or "")
        record["skill"] = str(result.get("skill") or "")
        record["success"] = bool(result.get("success"))
        record["tool_trace"] = _summarize_tool_trace(result.get("tool_trace"))
        record["steps"] = len(result.get("steps") or [])
        if result.get("error"):
            record["error"] = str(result.get("error"))
        if result.get("warning"):
            record["warning"] = str(result.get("warning"))
    except Exception as exc:  # noqa: BLE001 - one bad question must not kill the run
        record["success"] = False
        record["error"] = f"{type(exc).__name__}: {exc}"
        record["traceback"] = traceback.format_exc()
    finally:
        record["latency_s"] = round(time.perf_counter() - started, 3)
    return record


def render_markdown(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    lines = [
        f"# Agent benchmark: {summary['model']} / {summary['variant']}",
        "",
        f"- mode: {'dry-run (fake LLM)' if summary['dry_run'] else 'live'}",
        f"- db: {summary['db']}",
        f"- questions: {summary['question_count']}",
        f"- ok / error: {summary['ok']} / {summary['errors']}",
        f"- total latency: {summary['total_latency_s']}s",
        f"- generated at: {summary['timestamp']}",
        "",
        "---",
        "",
    ]
    for index, item in enumerate(results, start=1):
        lines.append(f"## {index}. {item.get('id')} ({item.get('category')})")
        lines.append("")
        lines.append(f"**Q:** {item.get('question')}")
        lines.append("")
        tools = item.get("tool_trace") or []
        if tools:
            rendered = ", ".join(
                f"`{t.get('tool')}({json.dumps(t.get('args') or {}, ensure_ascii=False)})` -> {t.get('result_count')}"
                + (f" ERROR: {t.get('error')}" if t.get("error") else "")
                for t in tools
            )
        else:
            rendered = "(none)"
        lines.append(f"**Tools used:** {rendered}")
        lines.append("")
        lines.append(f"**Latency:** {item.get('latency_s')}s | **steps:** {item.get('steps')} | **skill:** {item.get('skill') or 'n/a'} | **success:** {item.get('success')}")
        if item.get("error"):
            lines.append("")
            lines.append(f"**Error:** {item['error']}")
        lines.append("")
        lines.append("**A:**")
        lines.append("")
        lines.append(item.get("answer") or "_(empty)_")
        lines.append("")
        lines.append("---")
        lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    _guard_db(args.db)

    # Must happen before importing config/database.
    os.environ["DATABASE_URL"] = args.db
    os.environ.setdefault("PTCG_ALLOW_WEAK_SECRETS", "1")
    for flag in (
        "ENABLE_JP_DECK_AUTO_UPDATE",
        "ENABLE_LIMITLESS_AUTO_UPDATE",
        "ENABLE_USER_BACKUP",
        "ENABLE_CARD_DB_AUTO_UPDATE",
    ):
        os.environ.setdefault(flag, "0")

    if str(BACKEND_ROOT) not in sys.path:
        sys.path.insert(0, str(BACKEND_ROOT))

    import ai_settings  # noqa: E402
    from services.ai_assistant import assistant, client  # noqa: E402

    sys.path.insert(0, str(EVAL_DIR))
    from variants import VARIANTS  # noqa: E402

    if args.variant not in VARIANTS:
        raise SystemExit(f"unknown variant '{args.variant}'; available: {sorted(VARIANTS)}")
    prompt = VARIANTS[args.variant]

    questions = load_questions(args.limit)
    if not questions:
        raise SystemExit("no questions loaded")

    fake = ScriptedFakeLLM()
    context = {"language": "tw", "deck": [], "max_agent_steps": args.max_steps}

    results: list[dict[str, Any]] = []
    started = time.perf_counter()
    with ModelOverride(ai_settings, args.model), VariantOverride(assistant, prompt):
        if args.dry_run:
            with FakeLLMOverride(assistant, client, fake):
                for question in questions:
                    fake.reset()
                    results.append(run_one(assistant, question, context))
        else:
            for question in questions:
                results.append(run_one(assistant, question, context))
    total_latency = round(time.perf_counter() - started, 3)

    ok = sum(1 for item in results if item.get("success") and not item.get("error"))
    errors = len(results) - ok

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_model = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in args.model)
    safe_variant = "".join(ch if ch.isalnum() or ch in "-._" else "_" for ch in args.variant)
    stem = f"{stamp}-{safe_model}-{safe_variant}"

    summary = {
        "model": args.model,
        "variant": args.variant,
        "db": args.db,
        "dry_run": bool(args.dry_run),
        "question_count": len(results),
        "ok": ok,
        "errors": errors,
        "total_latency_s": total_latency,
        "timestamp": stamp,
        "max_agent_steps": args.max_steps,
    }

    out_dir = Path(args.out_dir) if args.out_dir else RESULTS_DIR
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"

    json_path.write_text(
        json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    md_path.write_text(render_markdown(summary, results), encoding="utf-8")

    print(f"[agent_bench] wrote {json_path}")
    print(f"[agent_bench] wrote {md_path}")
    print(f"[agent_bench] ok={ok} errors={errors} total_latency={total_latency}s")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the fixed question set through the assistant")
    run.add_argument("--db", required=True, help="Isolated benchmark DB URL (e.g. .../ptcg_bench)")
    run.add_argument("--model", required=True, help="Model name to force for this run")
    run.add_argument("--variant", default="baseline", help="Prompt variant from variants.py (default: baseline)")
    run.add_argument("--limit", type=int, default=None, help="Only run the first N questions")
    run.add_argument("--dry-run", action="store_true", help="Use a scripted fake LLM (no network/cost)")
    run.add_argument("--max-steps", type=int, default=6, help="Max agent tool steps per question (default: 6)")
    run.add_argument("--out-dir", default=None, help=f"Output dir (default: {RESULTS_DIR})")
    run.set_defaults(func=cmd_run)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
