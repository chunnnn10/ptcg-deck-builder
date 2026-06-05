import json
import re
from typing import Any

from .client import AIClientError, AIConfigError, chat_completion
from .tools import (
    get_card,
    search_cards,
    search_hand_size_damage,
    search_skill_keyword,
    search_skill_terms,
    search_trainer_energy_attach,
)


SYSTEM_PROMPT = """You are a PTCG deck research assistant.
Use only backend tool results as factual source.
If tool results contain matching cards, do not claim the database has no result.
If tool results are partial, say what was searched and what remains uncertain.
For single-card questions or ambiguous card names, default to the newest listed card unless the user specifies a set code or set number.
For single-card questions, include HP, element type, weakness, resistance, retreat cost, and skills when those fields are present.
Before listing card recommendations/search results, summarize regulation marks in the tool results. Clearly highlight H/I/J-marked Pokemon. If there are no H/I/J results, warn that the listed cards may not be current standard-legal.
Reply in Traditional Chinese with concise Markdown. When mentioning a card, include its name, language, set code, set number, and the relevant skill summary."""

SKILL_WORDS = (
    "特性", "技能", "攻擊", "攻击", "招式", "效果",
    "傷害", "伤害", "抽牌", "抽", "檢索", "检索", "搜尋", "搜索",
    "加到手牌", "手牌", "能量", "進化", "进化", "填充能量",
    "加速能量", "能量加速", "貼能", "贴能", "附能", "填能",
)

TRAINER_WORDS = ("支援者", "物品", "道具", "訓練家", "训练家", "Trainer", "Supporter", "Item")

QUESTION_WORDS = ("有哪", "哪張", "哪张", "哪種", "哪种", "哪些", "推薦", "推荐", "找")

PHRASES_TO_STRIP = (
    "是什麼卡", "是什么卡", "這張卡是什麼", "这张卡是什么",
    "請問", "请问", "幫我查", "帮我查", "查一下", "告訴我", "告诉我",
)

CARD_DETAIL_WORDS = (
    "血量", "HP", "hp", "屬性", "属性", "弱點", "弱点", "抵抗",
    "撤退費用", "撤退费用", "撤退", "技能", "招式", "特性", "效果",
    "資料", "资料", "詳情", "详情", "介紹", "介绍", "能量需求", "耗能",
)

CARD_NAME_LEADING_PHRASES = (
    "請介紹一下", "请介绍一下", "請介紹", "请介绍", "介紹一下", "介绍一下",
    "介紹", "介绍", "請問", "请问", "幫我查", "帮我查", "查一下",
    "告訴我", "告诉我", "關於", "关于", "這張", "这张", "這隻", "这只",
)

CARD_NAME_TRAILING_TERMS = (
    "血量", "HP", "hp", "屬性", "属性", "弱點", "弱点", "抵抗",
    "撤退費用", "撤退费用", "撤退", "技能", "招式", "特性", "效果",
    "資料", "资料", "詳情", "详情", "能量需求", "耗能",
)


def _last_user_message(messages: list[dict[str, str]]) -> str:
    for msg in reversed(messages or []):
        if msg.get("role") == "user":
            return str(msg.get("content") or "").strip()
    return ""


def _has_japanese(text: str) -> bool:
    return any(("\u3040" <= ch <= "\u30ff") for ch in text or "")


def _extract_query(text: str) -> str:
    cleaned = str(text or "").strip()
    for phrase in PHRASES_TO_STRIP:
        cleaned = cleaned.replace(phrase, " ")
    cleaned = re.sub(r"[？?！!。,.，]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned or text.strip()


def _has_card_detail_intent(text: str) -> bool:
    return any(word in text for word in CARD_DETAIL_WORDS)


def _strip_card_query_noise(value: str) -> str:
    cleaned = str(value or "").strip()
    cleaned = re.sub(r"[「」『』\"'（）()\[\]【】？?！!。,.，:：]", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    changed = True
    while changed:
        changed = False
        for phrase in CARD_NAME_LEADING_PHRASES:
            if cleaned.startswith(phrase):
                cleaned = cleaned[len(phrase):].strip()
                changed = True

    trailing_terms = "|".join(re.escape(term) for term in CARD_NAME_TRAILING_TERMS)
    trailing_pattern = (
        rf"\s*(?:的)?(?:有什麼|有什么|有哪些|是什麼|是什么|是多少|有多少|幾|几)?"
        rf"(?:{trailing_terms})(?:(?:和|與|与|及|、)(?:{trailing_terms}))*"
        rf"(?:是什麼|是什么|是多少|有哪些|有什麼|有什么|嗎|吗|呢|啊)?$"
    )
    while re.search(trailing_pattern, cleaned):
        cleaned = re.sub(trailing_pattern, "", cleaned).strip()

    cleaned = re.sub(r"^(?:這張|这张|這隻|这只|卡牌|卡片)\s*", "", cleaned).strip()
    cleaned = re.sub(r"\s*(?:這張|这张)?(?:卡牌|卡片)$", "", cleaned).strip()
    return cleaned


def _is_probable_card_query(value: str) -> bool:
    query = str(value or "").strip()
    if len(query) < 2 or len(query) > 40:
        return False
    if any(word in query for word in ("哪", "哪些", "哪張", "哪张", "哪種", "哪种", "推薦", "推荐", "找")):
        return False
    return bool(re.search(r"[\w\u3040-\u30ff\u3400-\u9fff]", query))


def _card_name_candidates(text: str) -> list[str]:
    if not _has_card_detail_intent(text):
        return []

    raw = str(text or "").strip()
    candidates: list[str] = []
    candidates.extend(re.findall(r"[「『\"']([^」』\"']{2,40})[」』\"']", raw))

    cleaned = _extract_query(raw)
    candidates.append(cleaned)

    if "的" in cleaned:
        before, after = cleaned.rsplit("的", 1)
        if any(term in after for term in CARD_NAME_TRAILING_TERMS):
            candidates.append(before)

    normalized: list[str] = []
    seen = set()
    for candidate in candidates:
        query = _strip_card_query_noise(candidate)
        if _is_probable_card_query(query) and query not in seen:
            seen.add(query)
            normalized.append(query)
    return normalized[:3]


def _extract_skill_type(text: str) -> str:
    if "特性" in text:
        return "ability"
    if any(word in text for word in ("攻擊", "攻击", "招式", "傷害", "伤害")):
        return "attack"
    return ""


def _is_skill_search(text: str) -> bool:
    if any(word in text for word in SKILL_WORDS):
        return True
    return any(w in text for w in SKILL_WORDS) and any(w in text for w in QUESTION_WORDS)


def _keyword_for_text(text: str) -> str:
    if "抽" in text:
        return "抽"
    if "手牌" in text:
        return "手牌"
    if "能量" in text:
        return "能量"
    if "傷害" in text or "伤害" in text:
        return "傷害"
    for keyword in SKILL_WORDS:
        if keyword in text:
            return keyword
    return _extract_query(text)


def _plan_searches(text: str, language: str, current_card_id: str = "") -> list[dict[str, Any]]:
    plan: list[dict[str, Any]] = []
    skill_type = _extract_skill_type(text)

    if current_card_id and ("這張" in text or "这张" in text):
        plan.append({"tool": "get_card", "args": {"card_id": current_card_id, "language": language}})
        return plan

    card_queries = _card_name_candidates(text)
    if card_queries:
        for query in card_queries:
            plan.append({"tool": "search_cards", "args": {"query": query, "language": language}})
        return plan

    if _is_skill_search(text):
        if any(word in text for word in TRAINER_WORDS) and "能量" in text and any(word in text for word in ("附", "填", "貼", "贴", "加速")):
            subtypes = []
            if "支援者" in text or "Supporter" in text:
                subtypes.append("Supporter")
            if "物品" in text or "Item" in text:
                subtypes.append("Item")
            if "道具" in text:
                subtypes.append("Pokémon Tool")
            plan.append({"tool": "search_trainer_energy_attach", "args": {"language": language, "subtypes": subtypes}})
            return plan

        if any(word in text for word in ("填充能量", "加速能量", "能量加速", "貼能", "贴能", "附能", "填能")):
            terms = ["能量", "附"]
            if "進化" in text or "进化" in text:
                terms.append("進化")
            if "牌庫" in text or "牌库" in text:
                terms.append("牌庫")
            plan.append({"tool": "search_skill_terms", "args": {"terms": terms, "language": language, "skill_type": skill_type}})
            return plan

        if "手牌" in text and ("傷害" in text or "伤害" in text):
            plan.append({"tool": "search_hand_size_damage", "args": {"language": language}})
            return plan

        keyword = _keyword_for_text(text)
        plan.append({"tool": "search_skill_keyword", "args": {"keyword": keyword, "language": language, "skill_type": skill_type}})
        return plan

    query = _extract_query(text)
    plan.append({"tool": "search_cards", "args": {"query": query, "language": language}})
    return plan


def _run_tool(tool: str, args: dict[str, Any]) -> Any:
    if tool == "get_card":
        return get_card(str(args.get("card_id") or ""), str(args.get("language") or "tw"))
    if tool == "search_cards":
        return search_cards(str(args.get("query") or ""), str(args.get("language") or "tw"), 20)
    if tool == "search_skill_keyword":
        return search_skill_keyword(
            str(args.get("keyword") or ""),
            str(args.get("language") or "tw"),
            20,
            str(args.get("skill_type") or ""),
        )
    if tool == "search_skill_terms":
        return search_skill_terms(
            list(args.get("terms") or []),
            str(args.get("language") or "tw"),
            20,
            str(args.get("skill_type") or ""),
        )
    if tool == "search_hand_size_damage":
        return search_hand_size_damage(str(args.get("language") or "tw"), 20)
    if tool == "search_trainer_energy_attach":
        return search_trainer_energy_attach(
            str(args.get("language") or "tw"),
            20,
            list(args.get("subtypes") or []),
        )
    return None


def _collect_cards(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    cards = []
    seen = set()
    for result in tool_results:
        data = result.get("result")
        items = data if isinstance(data, list) else ([data] if isinstance(data, dict) else [])
        for card in items:
            cid = card.get("card_id") or card.get("id")
            key = f"{card.get('language')}:{cid}"
            if cid and key not in seen:
                seen.add(key)
                cards.append(card)
    return cards


def _relevant_skills(card: dict[str, Any]) -> list[dict[str, Any]]:
    if str(card.get("card_type") or "") == "Trainer" and card.get("description"):
        return [{
            "type": card.get("sub_type") or "Trainer",
            "name": "效果",
            "damage": "",
            "effect": card.get("description"),
        }]
    skills = card.get("skills") or []
    relevant = []
    for skill in skills:
        text = f"{skill.get('name') or ''} {skill.get('damage') or ''} {skill.get('effect') or ''}"
        if any(term in text for term in ("手牌", "張數", "数量", "能量", "進化", "牌庫", "抽", "傷害", "傷害指示物", "增加")):
            relevant.append({
                "type": skill.get("type"),
                "name": skill.get("name"),
                "damage": skill.get("damage"),
                "effect": skill.get("effect"),
            })
    return relevant[:3] or [
        {
            "type": skill.get("type"),
            "name": skill.get("name"),
            "damage": skill.get("damage"),
            "effect": skill.get("effect"),
        }
        for skill in skills[:2]
    ]


def _compact_card(card: dict[str, Any]) -> dict[str, Any]:
    return {
        "card_id": card.get("card_id") or card.get("id"),
        "language": card.get("language"),
        "name": card.get("name"),
        "card_type": card.get("card_type"),
        "sub_type": card.get("sub_type"),
        "hp": card.get("hp"),
        "element_type": card.get("element_type"),
        "weakness_type": card.get("weakness_type"),
        "weakness_value": card.get("weakness_value"),
        "resistance_type": card.get("resistance_type"),
        "resistance_value": card.get("resistance_value"),
        "retreat_cost": card.get("retreat_cost"),
        "set_code": card.get("set_code"),
        "set_number": card.get("set_number"),
        "set_name": card.get("set_name"),
        "regulation_mark": card.get("regulation_mark"),
        "description": card.get("description") if str(card.get("card_type") or "") == "Trainer" else None,
        "skills": _relevant_skills(card),
    }


def _tool_context(tool_results: list[dict[str, Any]]) -> str:
    compact = []
    for item in tool_results:
        data = item.get("result")
        regulation_counts: dict[str, int] = {}
        if isinstance(data, list):
            for card in data:
                mark = str(card.get("regulation_mark") or "無標").strip() or "無標"
                regulation_counts[mark] = regulation_counts.get(mark, 0) + 1
            data = [_compact_card(card) for card in data[:20]]
        elif isinstance(data, dict):
            mark = str(data.get("regulation_mark") or "無標").strip() or "無標"
            regulation_counts[mark] = 1
            data = _compact_card(data)
        compact.append({
            "tool": item.get("tool"),
            "args": item.get("args"),
            "result_count": len(data) if isinstance(data, list) else (1 if data else 0),
            "regulation_counts": regulation_counts,
            "hij_count": sum(regulation_counts.get(mark, 0) for mark in ("H", "I", "J")),
            "result": data,
        })
    return json.dumps(compact, ensure_ascii=False, default=str)


def _public_tool_results(tool_results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    public = []
    for item in tool_results:
        data = item.get("result")
        regulation_counts: dict[str, int] = {}
        if isinstance(data, list):
            for card in data:
                mark = str(card.get("regulation_mark") or "無標").strip() or "無標"
                regulation_counts[mark] = regulation_counts.get(mark, 0) + 1
            result = [_compact_card(card) for card in data[:20]]
        elif isinstance(data, dict):
            mark = str(data.get("regulation_mark") or "無標").strip() or "無標"
            regulation_counts[mark] = 1
            result = _compact_card(data)
        else:
            result = data
        public.append({
            "tool": item.get("tool"),
            "args": item.get("args"),
            "regulation_counts": regulation_counts,
            "hij_count": sum(regulation_counts.get(mark, 0) for mark in ("H", "I", "J")),
            "result": result,
        })
    return public


def _fallback_answer(user_text: str, tool_results: list[dict[str, Any]], error: str = "") -> str:
    cards = _collect_cards(tool_results)
    if not cards:
        return f"AI 模型暫時沒有回應，且工具沒有找到可用卡牌結果。\n\n錯誤：`{error}`" if error else "沒有找到可用卡牌結果。"

    lines = []
    if error:
        lines.append("> AI 模型回應逾時，以下先根據本地資料庫工具結果整理。")
        lines.append("")

    if "手牌" in user_text and ("傷害" in user_text or "伤害" in user_text):
        lines.append("### 根據手牌數量或手牌投入量計算傷害的寶可夢")
    elif "能量" in user_text and ("進化" in user_text or "进化" in user_text):
        lines.append("### 進化時可加速/附加能量的寶可夢")
    else:
        lines.append("### 工具搜尋結果")

    mark_counts: dict[str, int] = {}
    for card in cards:
        mark = str(card.get("regulation_mark") or "無標").strip() or "無標"
        mark_counts[mark] = mark_counts.get(mark, 0) + 1
    mark_summary = "、".join(f"{mark}: {count}" for mark, count in sorted(mark_counts.items()))
    current_count = sum(mark_counts.get(mark, 0) for mark in ("H", "I", "J"))
    lines.append("")
    lines.append(f"**標數統計**：{mark_summary}")
    if current_count:
        lines.append(f"**H/I/J 標結果**：共 {current_count} 張，以下優先參考這些較新的卡。")
    else:
        lines.append("**提醒**：這批結果沒有 H/I/J 標卡，可能不是目前新環境標準可用。")
    lines.append("")

    for card in cards[:15]:
        relevant = _relevant_skills(card)
        if not relevant:
            continue
        skill_summaries = []
        for skill in relevant[:2]:
            name = skill.get("name") or "未命名技能"
            damage = f" {skill.get('damage')}" if skill.get("damage") else ""
            effect = skill.get("effect") or ""
            skill_summaries.append(f"**{name}**{damage}：{effect}")
        lines.append(
            f"- **{card.get('name')}** `{card.get('set_code')} {card.get('set_number')}`"
            f" [{card.get('regulation_mark') or '無標'}]："
            + "；".join(skill_summaries)
        )

    return "\n".join(lines)


def run_assistant(messages: list[dict[str, str]], context: dict[str, Any] | None = None) -> dict[str, Any]:
    context = context or {}
    user_text = _last_user_message(messages)
    if not user_text:
        return {"success": False, "error": "Missing message content", "answer": "", "tool_results": [], "cards": [], "steps": []}

    language = str(context.get("language") or "").strip()
    if language not in ("tw", "jp"):
        language = "jp" if _has_japanese(user_text) else "tw"

    current_card_id = str(context.get("current_card_id") or "").strip()
    search_plan = _plan_searches(user_text, language, current_card_id)
    tool_results: list[dict[str, Any]] = []
    steps = [{"status": "planning", "message": "規劃搜尋流程", "plan": search_plan}]

    for item in search_plan:
        tool = item["tool"]
        args = item["args"]
        steps.append({"status": "running", "message": f"AI 調用工具查找寶可夢中：{tool}", "tool": tool, "args": args})
        result = _run_tool(tool, args)
        tool_results.append({"tool": tool, "args": args, "result": result})
        result_count = len(result) if isinstance(result, list) else (1 if result else 0)
        steps.append({"status": "done", "message": f"{tool} 找到 {result_count} 筆結果", "tool": tool, "result_count": result_count})

    if language == "tw" and _is_skill_search(user_text) and sum(len(r.get("result") or []) for r in tool_results if isinstance(r.get("result"), list)) < 5:
        jp_plan = _plan_searches(user_text, "jp", current_card_id)
        for item in jp_plan:
            tool = item["tool"]
            args = item["args"]
            steps.append({"status": "running", "message": f"中文結果不足，改查日文資料：{tool}", "tool": tool, "args": args})
            result = _run_tool(tool, args)
            tool_results.append({"tool": tool, "args": args, "result": result})
            result_count = len(result) if isinstance(result, list) else (1 if result else 0)
            steps.append({"status": "done", "message": f"{tool} 找到 {result_count} 筆日文結果", "tool": tool, "result_count": result_count})

    cards = _collect_cards(tool_results)
    prompt_messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        *[
            {"role": m.get("role", "user"), "content": str(m.get("content") or "")}
            for m in (messages or [])[-8:]
            if m.get("role") in ("user", "assistant")
        ],
        {
            "role": "user",
            "content": "Search plan:\n"
            + json.dumps(search_plan, ensure_ascii=False)
            + "\nUse only these backend tool results as factual source:\n"
            + _tool_context(tool_results),
        },
    ]

    try:
        answer = chat_completion(prompt_messages)
    except AIConfigError as exc:
        return {"success": False, "error": str(exc), "answer": "", "tool_results": _public_tool_results(tool_results), "cards": cards, "steps": steps}
    except AIClientError as exc:
        error = str(exc)
        if "timed out" in error.lower() or "read timed out" in error.lower():
            answer = _fallback_answer(user_text, tool_results, error)
            steps.append({"status": "done", "message": "AI 模型逾時，已改用本地工具結果回答", "tool": "fallback_answer"})
            return {
                "success": True,
                "warning": error,
                "answer": answer,
                "tool_results": _public_tool_results(tool_results),
                "cards": cards,
                "steps": steps,
            }
        return {"success": False, "error": error, "answer": "", "tool_results": _public_tool_results(tool_results), "cards": cards, "steps": steps}

    return {"success": True, "answer": answer, "tool_results": _public_tool_results(tool_results), "cards": cards, "steps": steps}
