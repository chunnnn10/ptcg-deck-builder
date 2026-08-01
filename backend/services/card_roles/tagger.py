"""效果角色標籤（card role tags）：LLM 提取 + 程式碼驗證 + 人工審核。

姊妹層 of ``logic_extractor``：
- logic_extractor：規則式、精確 predicate（Gap A）
- card_roles：粗粒度功能角色，LLM 提取；evidence 必須逐字存在於卡文（verifier 檢查），
  標註結果先入 pending 佇列，人工（admin）批准後才生效。

Anti-hallucination 原則：
- LLM 只負責「提取」：從卡文提取 (role, params, evidence)
- 角色/參數鍵的語義由 ROLE_SCHEMAS（程式碼固定字典）決定
- verify_evidence 以純程式碼檢查 evidence 逐字存在，不存在即丟棄該角色
- 高置信度（>= CARD_ROLE_AUTO_APPROVE_CONFIDENCE）自動批准，其餘進 pending
"""

from __future__ import annotations

import json
import re
import threading
import time
from typing import Any

import config
import database

from services.ai_assistant.client import chat_completion

AUTO_APPROVE_CONFIDENCE = float(getattr(config, "CARD_ROLE_AUTO_APPROVE_CONFIDENCE", 0.9))
MAX_RETRIES = int(getattr(config, "CARD_ROLE_MAX_RETRIES", 3))
DEFAULT_BATCH_SIZE = int(getattr(config, "CARD_ROLE_BATCH_SIZE", 200))
LOW_CONFIDENCE = 0.6

# ==========================================
# 角色字典（固定語義，LLM 不可自創）
# ==========================================
ROLE_SCHEMAS: dict[str, dict[str, Any]] = {
    "draw": {
        "description": "抽牌（從牌庫抽牌）",
        "params": {
            "count": "張數（數字）",
            "source": "牌庫來源（deck/trash）",
            "old_hand": "抽牌前舊手牌的處理（keep=保留 / discard=丟棄 / shuffle_to_deck=洗回牌庫）",
            "reshuffle": "是否重洗牌庫（true/false）",
            "condition": "觸發條件（文字）",
        },
    },
    "discard": {
        "description": "丟棄（棄牌）",
        "params": {
            "target": "丟棄來源（hand=手牌 / deck=牌庫 / bench=備戰區）",
            "count": "張數（數字，全部丟棄用 all）",
            "self_chosen": "是否由自己選擇要丟棄的卡（true/false）",
        },
    },
    "search": {
        "description": "檢索（從牌庫/棄牌區找特定卡）",
        "params": {
            "target_type": "目標卡類型（basic_pokemon/pokemon/evolution_pokemon/trainer/energy/basic_energy/card）",
            "count": "張數（數字）",
            "max_count": "最多張數（數字，卡文寫「最多N張」時用）",
            "destination": "放入位置（hand=手牌 / bench=備戰區）",
            "from": "搜尋來源（deck/trash）",
            "condition": "搜尋條件（文字，如 HP 限制、進化形態限制）",
            "reveal_to_opponent": "是否需給對手看（true/false）",
        },
    },
    "ramp": {
        "description": "能量加速（從牌庫/棄牌區把能量附到寶可夢身上）",
        "params": {
            "from": "能量來源（deck/trash/hand/energy_zone）",
            "to": "附著對象（active/bench/any）",
            "count": "張數（數字）",
            "condition": "觸發條件（文字）",
        },
    },
    "evolve_accel": {
        "description": "進化加速（直接從牌庫/棄牌區把進化卡放到寶可夢身上）",
        "params": {
            "from": "來源（deck/trash/hand）",
            "condition": "觸發條件（文字）",
        },
    },
    "heal": {
        "description": "回復（移除傷害指示物、回復 HP）",
        "params": {
            "target": "對象（active/bench/this/any）",
            "amount": "回復量（數字，以傷害指示物或 HP 數值表示）",
            "condition": "觸發條件（文字）",
        },
    },
    "switch": {
        "description": "換位（交換戰鬥寶可夢與備戰寶可夢）",
        "params": {
            "target": "對象（active/bench）",
            "condition": "觸發條件（文字）",
        },
    },
    "damage": {
        "description": "招式傷害（僅限有條件/有附加機制的傷害；純粹無條件普通攻擊不標）",
        "params": {
            "amount": "傷害量（數字）",
            "target": "對象（active/bench/any）",
            "condition": "條件或附加機制（文字）",
        },
    },
    "condition": {
        "description": "狀態/限制效果（麻痺、睡眠、中毒、灼傷、混亂、無法撤退、特殊規則限制等）",
        "params": {
            "type": "狀態類型（麻痺/睡眠/中毒/灼傷/混亂/限制）",
            "duration": "持續時間（回合數或文字）",
            "condition": "觸發條件（文字）",
        },
    },
    "stall": {
        "description": "拖節奏/防禦（傷害減免、撤退費用變化、自己或對手場上的限制）",
        "params": {
            "mechanism": "機制說明（文字）",
            "condition": "觸發條件（文字）",
        },
    },
}

ROLE_NAMES = list(ROLE_SCHEMAS)

# 可被 Agent 依參數過濾的參數鍵（防止任意鍵造成查詢面過廣）
FILTERABLE_PARAM_KEYS = {
    "source", "old_hand", "reshuffle", "target", "count", "max_count",
    "destination", "from", "to", "target_type", "type", "condition",
}

# ==========================================
# 全域狀態（供前端輪詢，參照 crawler 的 UPDATE_STATE 模式）
# ==========================================
UPDATE_STATE = {
    "running": False,
    "progress": 0,
    "message": "就緒",
    "logs": [],
    "total": 0,
    "processed": 0,
    "labeled": 0,
    "auto_approved": 0,
    "pending_added": 0,
    "rejected_roles": 0,
    "failed": 0,
    "started_at": "",
    "finished_at": "",
}

state_lock = threading.Lock()


def _log(msg: str) -> None:
    ts = time.strftime("%H:%M:%S")
    with state_lock:
        UPDATE_STATE["message"] = msg
        UPDATE_STATE["logs"].insert(0, f"[{ts}] {msg}")
        if len(UPDATE_STATE["logs"]) > 200:
            UPDATE_STATE["logs"].pop()


def get_status() -> dict[str, Any]:
    with state_lock:
        return dict(UPDATE_STATE)


# ==========================================
# 卡文組合（TW cards 表：description + skills_json）
# ==========================================
def build_tw_card_text(row: dict[str, Any]) -> str:
    """組合 cards 表（TW 卡）的完整效果文字。

    訓練家卡通常存在 description；寶可夢效果在 skills_json 的 effect 欄位。
    """
    parts: list[str] = []
    description = (row.get("description") or "").strip()
    if description:
        parts.append(description)

    for text in _skill_effect_texts(row.get("skills_json")):
        if text and text not in parts:
            parts.append(text)

    return _normalize_text(" ".join(parts))


def _skill_effect_texts(skills_json: Any):
    if not skills_json:
        return []
    skills = skills_json
    if isinstance(skills_json, str):
        try:
            skills = json.loads(skills_json)
        except json.JSONDecodeError:
            return []
    if not isinstance(skills, list):
        return []

    texts: list[str] = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        value = (skill.get("effect") or skill.get("text") or "").strip()
        if value and value not in texts:
            texts.append(value)
    return texts


def _normalize_text(text: str) -> str:
    """去除全部空白（含全形），用於 evidence 逐字比對。"""
    return re.sub(r"\s+", "", text or "")


# ==========================================
# 標註 prompt（TW 卡）
# ==========================================
def _role_schema_lines() -> str:
    lines = []
    for role, schema in ROLE_SCHEMAS.items():
        lines.append(f"- {role}: {schema['description']}")
        if schema.get("params"):
            lines.append(f"  params: " + "；".join(f"{k}({v})" for k, v in schema["params"].items()))
    return "\n".join(lines)


def build_label_prompt(card: dict[str, Any]) -> tuple[str, str]:
    """回傳 (system_prompt, user_prompt)。card 為 cards 表的一列。"""
    system_prompt = f"""你是一個寶可夢卡牌（PTCG）卡片效果的「功能角色」提取器。你的任務是從卡片效果文字中提取這張卡在牌組構築中的功能角色。

可用的角色清單（只能從此清單選擇，一個角色最多標一次）：

{_role_schema_lines()}

嚴格規則（違反即失敗）：
1. 只能從上面的角色清單選擇；不要自創角色。
2. params 只能使用該角色定義的鍵；不要自創鍵；數值用數字型態（如 count:5），不要用字串。
3. evidence 必須是卡文原文中「逐字」存在的片段（包含標點符號），作為該角色的證據。如果卡文中找不到逐字證據，就不要標這個角色。
4. 同一個效果只標最貼切的一個角色（例如「丟棄手牌後抽牌」就是 draw 並在 old_hand 描述手牌處理，不要另外標 discard）。
5. 純粹造成傷害、沒有條件/附加機制的招式，不要標 damage。
6. 卡上沒有的效果絕對不能標。
7. confidence 為 0~1 的數字，表示你對整張卡標註結果的信心。

輸出必須是單一合法 JSON 物件（不要輸出其他文字），格式：
{{"roles": [{{"role": "draw", "params": {{"count": 5, "source": "deck"}}, "evidence": "從牌庫抽出5張卡"}}], "confidence": 0.95}}"""

    parts = [f"卡片名稱：{card.get('name') or ''}"]
    card_type = card.get("card_type") or ""
    sub_type = card.get("sub_type") or ""
    if card_type or sub_type:
        parts.append(f"卡片類型：{' / '.join(x for x in (card_type, sub_type) if x)}")
    description = (card.get("description") or "").strip()
    if description:
        parts.append(f"卡片效果：{description}")
    for skill in _skill_list(card.get("skills_json")):
        parts.append(f"招式「{skill.get('name') or '未命名'}」（{skill.get('type') or 'unknown'}）：{skill.get('effect') or ''}")
    user_prompt = "\n".join(parts)
    return system_prompt, user_prompt


def _skill_list(skills_json: Any) -> list[dict[str, Any]]:
    if not skills_json:
        return []
    skills = skills_json
    if isinstance(skills_json, str):
        try:
            skills = json.loads(skills_json)
        except json.JSONDecodeError:
            return []
    if not isinstance(skills, list):
        return []
    result = []
    for skill in skills:
        if not isinstance(skill, dict):
            continue
        result.append({
            "name": skill.get("name") or skill.get("ability_name") or "",
            "type": skill.get("type") or skill.get("category") or ("ability" if skill.get("isAbility") else "attack"),
            "effect": skill.get("effect") or skill.get("text") or skill.get("description") or "",
        })
    return result


# ==========================================
# Verifier（純程式碼，anti-hallucination 的守門員）
# ==========================================
def verify_evidence(card_text: str, evidence: Any) -> bool:
    """evidence 必須是卡文（正規化後）的逐字子字串。"""
    if not isinstance(evidence, str) or not evidence.strip():
        return False
    return _normalize_text(evidence) in _normalize_text(card_text)


def validate_role(card_text: str, item: dict[str, Any]) -> list[str]:
    """驗證單一 (role, params, evidence)。回傳錯誤清單；空清單代表通過。"""
    errors: list[str] = []
    role = item.get("role")
    if not isinstance(role, str) or role not in ROLE_SCHEMAS:
        return ["unknown_role"]
    params = item.get("params")
    if not isinstance(params, dict):
        return ["params_not_object"]

    allowed_keys = set(ROLE_SCHEMAS[role]["params"])
    for key in params:
        if key not in allowed_keys:
            errors.append(f"unknown_param:{key}")

    evidence = item.get("evidence")
    if not verify_evidence(card_text, evidence):
        errors.append("evidence_not_found")
    elif len(str(evidence)) > 200:
        errors.append("evidence_too_long")
    return errors


def validate_payload(card_text: str, payload: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """驗證 LLM 輸出。回傳 (valid_roles, card_level_errors)。"""
    if not isinstance(payload, dict):
        return [], ["payload_not_object"]
    raw_roles = payload.get("roles")
    if not isinstance(raw_roles, list):
        return [], ["roles_not_list"]
    if not raw_roles:
        return [], []

    confidence = payload.get("confidence")
    try:
        confidence = float(confidence)
    except (TypeError, ValueError):
        confidence = 0.0
    if not (0.0 <= confidence <= 1.0):
        confidence = 0.0

    valid: list[dict[str, Any]] = []
    errors: list[str] = []
    for item in raw_roles:
        if not isinstance(item, dict):
            errors.append("role_item_not_object")
            continue
        role_errors = validate_role(card_text, item)
        if role_errors:
            role = item.get("role")
            errors.append(f"role:{role} -> {'/'.join(role_errors)}")
            continue
        params = {k: v for k, v in item.get("params", {}).items() if v is not None}
        valid.append({
            "role": item["role"],
            "params": params,
            "evidence": str(item.get("evidence") or "").strip(),
            "confidence": confidence,
        })
    return valid, errors


# ==========================================
# 標註執行
# ==========================================
def _fetch_pending_cards(conn: Any, limit: int) -> list[dict[str, Any]]:
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT c.card_id, c.name, c.card_type, c.sub_type, c.description, c.skills_json, c.japanese_name
        FROM cards c
        LEFT JOIN card_role_label_progress p ON p.card_id = c.card_id
        WHERE p.card_id IS NULL
          AND (
                COALESCE(c.description, '') <> ''
                OR (
                    jsonb_typeof(c.skills_json) = 'array'
                    AND jsonb_array_length(c.skills_json) > 0
                )
              )
        ORDER BY c.card_id
        LIMIT %s
        """,
        (limit,),
    )
    return [dict(row) for row in cursor.fetchall()]


def _insert_tags(conn: Any, card_id: str, roles: list[dict[str, Any]]) -> dict[str, int]:
    """寫入標籤；高置信度自動批准，其餘 pending。回傳 (auto_approved, pending_added)。"""
    auto_approved = 0
    pending_added = 0
    cursor = conn.cursor()
    for role in roles:
        confidence = float(role.get("confidence") or 0.0)
        status = "approved" if confidence >= AUTO_APPROVE_CONFIDENCE else "pending"
        validation_errors: list[str] = []
        if confidence < LOW_CONFIDENCE:
            validation_errors.append(f"low_confidence:{confidence:.2f}")
        cursor.execute(
            """
            INSERT INTO card_role_tags (card_id, role, params, evidence_span, source, confidence, status, validation_errors)
            VALUES (%s, %s, %s, %s, 'llm', %s, %s, %s)
            ON CONFLICT (card_id, role, params) DO NOTHING
            """,
            (
                card_id,
                role["role"],
                json.dumps(role["params"], ensure_ascii=False),
                role["evidence"],
                confidence,
                status,
                validation_errors,
            ),
        )
        if status == "approved":
            auto_approved += 1
        else:
            pending_added += 1
    cursor.execute(
        """
        INSERT INTO card_role_label_progress (card_id, processed_at, attempt_count)
        VALUES (%s, CURRENT_TIMESTAMP, 1)
        ON CONFLICT (card_id) DO NOTHING
        """,
        (card_id,),
    )
    conn.commit()
    return {"auto_approved": auto_approved, "pending_added": pending_added}


def _label_one_card(card: dict[str, Any]) -> dict[str, Any]:
    """標註單卡（最多重試 MAX_RETRIES 次）。回傳摘要；重試耗盡後拋出例外。"""
    card_text = build_tw_card_text(card)
    if not card_text:
        return {"skipped": True, "roles": [], "errors": []}

    system_prompt, user_prompt = build_label_prompt(card)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": user_prompt},
    ]
    last_error: Exception | None = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            raw = chat_completion(messages, temperature=0.0, response_format={"type": "json_object"})
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"LLM 回傳非 JSON: {raw[:200]}") from exc
            valid_roles, errors = validate_payload(card_text, payload)
            return {"skipped": False, "roles": valid_roles, "errors": errors}
        except Exception as exc:
            last_error = exc
            _log(f"[retry] {card.get('name')} 第 {attempt}/{MAX_RETRIES} 次失敗: {exc}")
            time.sleep(2 * attempt)
    raise last_error or RuntimeError("LLM 標註失敗")


def _mark_processed(conn: Any, card_id: str) -> None:
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO card_role_label_progress (card_id, processed_at, attempt_count)
        VALUES (%s, CURRENT_TIMESTAMP, 1)
        ON CONFLICT (card_id) DO NOTHING
        """,
        (card_id,),
    )
    conn.commit()


def _run_batch(conn: Any, cards: list[dict[str, Any]], worker_index: int) -> dict[str, Any]:
    summary = {"processed": 0, "labeled": 0, "auto_approved": 0, "pending_added": 0, "rejected_roles": 0, "failed": 0}
    for card in cards:
        card_id = str(card.get("card_id") or "")
        try:
            result = _label_one_card(card)
        except Exception as exc:
            summary["failed"] += 1
            _log(f"[W{worker_index}] {card.get('name')} LLM 失敗（已留待下次續跑）: {exc}")
            _advance_state({"processed": 1, "failed": 1})
            continue
        if result.get("skipped"):
            # 無文字卡（如基本能量）：直接標記為已處理
            _mark_processed(conn, card_id)
            _advance_state({"processed": 1})
            continue
        roles = result.get("roles") or []
        rejected = len(result.get("errors") or [])
        if rejected:
            _log(f"[W{worker_index}] {card.get('name')} 角色被 verifier 拒絕: {'; '.join(result['errors'][:5])}")
        if roles:
            counts = _insert_tags(conn, card_id, roles)
            summary["labeled"] += len(roles)
            summary["auto_approved"] += counts["auto_approved"]
            summary["pending_added"] += counts["pending_added"]
            _advance_state({
                "processed": 1,
                "labeled": len(roles),
                "auto_approved": counts["auto_approved"],
                "pending_added": counts["pending_added"],
                "rejected_roles": rejected,
            })
        else:
            # LLM 判定無角色（如純傷害卡）：仍標記為已處理，避免重複花費
            _mark_processed(conn, card_id)
            _advance_state({"processed": 1, "rejected_roles": rejected})
        summary["processed"] += 1
        summary["rejected_roles"] += rejected
    return summary


def _advance_state(delta: dict[str, int]) -> None:
    with state_lock:
        for key, value in delta.items():
            UPDATE_STATE[key] += value
        UPDATE_STATE["progress"] = round(100.0 * UPDATE_STATE["processed"] / max(1, UPDATE_STATE["total"]))


def label_cards(limit: int | None = None, worker_count: int = 1, source: str = "cards") -> tuple[bool, str]:
    """背景執行緒分批標註 TW 卡（cards 表）。回傳 (啟動成功與否, 訊息)。"""
    with state_lock:
        if UPDATE_STATE["running"]:
            return False, "標註任務已在執行中"
        UPDATE_STATE["running"] = True
        UPDATE_STATE["progress"] = 0
        UPDATE_STATE["message"] = "準備中..."
        UPDATE_STATE["logs"] = []
        UPDATE_STATE["total"] = 0
        UPDATE_STATE["processed"] = 0
        UPDATE_STATE["labeled"] = 0
        UPDATE_STATE["auto_approved"] = 0
        UPDATE_STATE["pending_added"] = 0
        UPDATE_STATE["rejected_roles"] = 0
        UPDATE_STATE["failed"] = 0
        UPDATE_STATE["started_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
        UPDATE_STATE["finished_at"] = ""

    if source not in ("cards",):
        source = "cards"
    batch = max(1, min(int(limit or DEFAULT_BATCH_SIZE), 5000))
    workers = max(1, min(int(worker_count or 1), 4))

    def worker_main() -> None:
        conn = database.get_db_connection()
        if not conn:
            _log("資料庫連線失敗")
            with state_lock:
                UPDATE_STATE["running"] = False
            return
        try:
            cards = _fetch_pending_cards(conn, batch)
            with state_lock:
                UPDATE_STATE["total"] = len(cards)
            _log(f"本批取出 {len(cards)} 張未標註卡片，{workers} 個 worker")
            if not cards:
                _log("沒有待標註的卡片（已全部處理完畢）")
                return

            chunks: list[list[dict[str, Any]]] = [[] for _ in range(workers)]
            for index, card in enumerate(cards):
                chunks[index % workers].append(card)

            threads = []
            for index, chunk in enumerate(chunks):
                if not chunk:
                    continue
                t = threading.Thread(
                    target=lambda c=chunk, i=index: _worker_chunk(c, i),
                    daemon=True,
                )
                t.start()
                threads.append(t)
            for t in threads:
                t.join()
        finally:
            conn.close()
            with state_lock:
                UPDATE_STATE["running"] = False
                UPDATE_STATE["progress"] = 100
                UPDATE_STATE["finished_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
                UPDATE_STATE["message"] = "標註完成" if UPDATE_STATE["total"] else "沒有待標註的卡片"
            _log("標註任務結束")

    def _worker_chunk(chunk: list[dict[str, Any]], worker_index: int) -> None:
        conn = database.get_db_connection()
        if not conn:
            _log(f"[W{worker_index}] 資料庫連線失敗")
            return
        try:
            _run_batch(conn, chunk, worker_index)
        except Exception as exc:
            _log(f"[W{worker_index}] worker 異常: {exc}")
        finally:
            conn.close()

    t = threading.Thread(target=worker_main, daemon=True)
    t.start()
    return True, "標註任務已啟動"
