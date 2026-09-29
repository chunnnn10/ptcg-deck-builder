# Agent benchmark harness

Compare **(model, prompt-variant)** configurations of the real PTCG AI assistant
against a FIXED question set, then read a human-readable comparison.

This lives entirely under `backend/eval/`. It never edits the assistant itself:
the harness monkeypatches `assistant.SYSTEM_PROMPT`, `ai_settings.get_ai_setting`
and (only in `--dry-run`) the LLM call, and restores them afterwards.

## Files

| File | Purpose |
| --- | --- |
| `agent_bench.py` | CLI harness: runs the question set through `run_assistant` and writes `.json` + `.md` results. |
| `questions.json` | 16 fixed Traditional-Chinese user questions across card lookup, effect search, deck building, meta, mixed JP/EN, out-of-rotation, ambiguous and unanswerable. |
| `variants.py` | `VARIANTS` dict: `baseline` (unchanged), `strict-evidence`, `concise-clarify` — each non-baseline value is a FULL system prompt. |
| `seed_bench.py` | Idempotently seeds ~60 deterministic Chinese cards into an ISOLATED DB. |
| `runner.py` | (pre-existing) unrelated structured-logic extractor evaluation. |
| `results/` | Generated `<timestamp>-<model>-<variant>.{json,md}` outputs. |

## 1. Seed an isolated DB

Never seed or run against `ptcg_db`. Create and seed a throwaway DB:

```bash
psql "postgresql://ptcg:ptcg_secret@127.0.0.1:5432/postgres" -c "DROP DATABASE IF EXISTS ptcg_bench;"
psql "postgresql://ptcg:ptcg_secret@127.0.0.1:5432/postgres" -c "CREATE DATABASE ptcg_bench;"

/opt/venv/bin/python backend/eval/seed_bench.py \
  --db postgresql://ptcg:ptcg_secret@127.0.0.1:5432/ptcg_bench
```

`seed_bench.py` runs `database.init_db()`, then upserts cards whose `card_id`
starts with `BENCH-`. Re-running it is safe (same rows). It refuses any URL
containing `/ptcg_db`.

## 2. Run the harness (fake LLM, no cost)

```bash
cd /a0/usr/projects/ptcg
/opt/venv/bin/python backend/eval/agent_bench.py run \
  --db postgresql://ptcg:ptcg_secret@127.0.0.1:5432/ptcg_bench \
  --model fake-model --variant baseline --dry-run
```

`--dry-run` swaps the LLM for a scripted fake that emits two real tool calls on
turn 1 (`list_skills`, `analyze_current_deck`) and a final JSON contract on turn
2. This exercises the whole loop with **no network and no cost**.

## 3. Run against a real model

```bash
export AI_BASE_URL="https://api.openai.com/v1"
export AI_API_KEY="sk-..."

/opt/venv/bin/python backend/eval/agent_bench.py run \
  --db postgresql://ptcg:ptcg_secret@127.0.0.1:5432/ptcg_bench \
  --model gpt-4o-mini --variant strict-evidence

# limit locally while iterating
/opt/venv/bin/python backend/eval/agent_bench.py run \
  --db postgresql://ptcg:ptcg_secret@127.0.0.1:5432/ptcg_bench \
  --model gpt-4o-mini --variant concise-clarify --limit 5
```

Compare the same model under different variants by changing `--variant`, or
different models by changing `--model`. Each run writes a timestamped pair:

* `results/<ts>-<model>-<variant>.json` — machine-readable, one result object per
  question with `question`, `answer`, `tool_trace` (tool name + args +
  result_count), `steps`, `latency_s`, `error`.
* `results/<ts>-<model>-<variant>.md` — one section per question with the answer,
  tools used, latency and any error.

## Override mechanics (how `--model` / `--variant` work)

* `--db` sets `DATABASE_URL` **before** importing `config`/`database`, so all
  schema + query traffic goes to the isolated DB. `ptcg_db` is rejected.
* `--model` monkeypatches `ai_settings.get_ai_setting` so `AI_MODEL` and
  `AI_CHAT_MODEL` resolve to the given model. `client._env` calls that function
  at request time, so the override applies without a restart. `AI_CHAT_MODEL`
  is also set in `os.environ` as a fallback; both are restored on exit.
* `--variant` patches `assistant.SYSTEM_PROMPT` (a `str` in the current code).
  If a later version replaces it with a builder function, the harness detects it
  with `hasattr` and patches the builder instead. The original is restored.
* `--dry-run` patches `chat_message` / `chat_completion` on both
  `assistant` and `client` (assistant imports them by value), restored after.

## Environment

Local runs need the weak-secret escape hatch and background services disabled
(the harness sets these defaults itself, but you can export them too):

```bash
export PTCG_ALLOW_WEAK_SECRETS=1
export ENABLE_JP_DECK_AUTO_UPDATE=0
export ENABLE_LIMITLESS_AUTO_UPDATE=0
export ENABLE_USER_BACKUP=0
export ENABLE_CARD_DB_AUTO_UPDATE=0
```

## Cleanup

```bash
psql "postgresql://ptcg:ptcg_secret@127.0.0.1:5432/postgres" -c "DROP DATABASE IF EXISTS ptcg_bench;"
```

`results/` is kept so runs remain comparable; delete it manually if you do not
want the artifacts.
