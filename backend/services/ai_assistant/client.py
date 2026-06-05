import os
from typing import Any

import requests


class AIConfigError(RuntimeError):
    pass


class AIClientError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    return str(os.environ.get(name) or default).strip()


def get_ai_config() -> dict[str, Any]:
    api_key = (
        _env("AI_API_KEY")
        or _env("DEEPSEEK_API_KEY")
        or _env("OPENROUTER_KEY_MAIN_1")
        or _env("OPENROUTER_KEY_FREE_1")
        or _env("OPENROUTER_KEY_FREE_2")
        or _env("GOMODEL_KEY")
    )
    model = (
        _env("AI_MODEL")
        or _env("DEEPSEEK_MODEL")
        or _env("OPENROUTER_MODEL_GEN")
        or _env("GOMODEL_MODEL_GEN")
    )
    base_url = (
        _env("AI_BASE_URL")
        or _env("DEEPSEEK_BASE_URL")
        or _env("OPENROUTER_BASE_URL")
        or _env("GOMODEL_BASE_URL")
        or "https://api.openai.com/v1"
    )
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://api.openai.com/v1"
    return {
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "timeout": float(_env("AI_TIMEOUT", "45") or 45),
        "thinking_enabled": _env("AI_THINKING_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
        "reasoning_effort": _env("AI_REASONING_EFFORT", "high").lower(),
    }


def ensure_configured() -> dict[str, Any]:
    cfg = get_ai_config()
    if not cfg["api_key"] or not cfg["model"]:
        raise AIConfigError(
            "AI 尚未設定，請設定 AI_API_KEY 與 AI_MODEL，或 DEEPSEEK_API_KEY 與 DEEPSEEK_MODEL"
        )
    return cfg


def _provider_error_message(status_code: int, detail: str, cfg: dict[str, Any]) -> str:
    model = cfg.get("model") or "(unknown model)"
    base_url = cfg.get("base_url") or "(unknown provider)"
    lower_detail = (detail or "").lower()

    if status_code == 401:
        return f"AI provider returned HTTP 401 while using model {model} on {base_url}. 請檢查 API key 是否正確。"

    if status_code == 402:
        return f"AI provider returned HTTP 402 while using model {model} on {base_url}. 請檢查 provider 帳戶餘額或付費狀態。"

    if status_code == 429:
        return (
            f"AI provider returned HTTP 429 while using model {model} on {base_url}. "
            "這通常是模型暫時限流或 provider 上游額度用完。請稍後再試，或切換另一個可用模型。"
        )

    if status_code == 404:
        if "deprecated" in lower_detail:
            return (
                f"AI provider returned HTTP 404 while using model {model} on {base_url}. "
                f"Provider 表示模型已下架或停用：{detail[:400]}"
            )
        return (
            f"AI provider returned HTTP 404 while using model {model} on {base_url}. "
            f"請確認模型名稱存在，且此 API key 可使用該模型：{detail[:400]}"
        )

    return (
        f"AI provider returned HTTP {status_code} while using model {model} on {base_url}: "
        f"{detail[:500]}"
    )


def chat_completion(messages: list[dict[str, str]], temperature: float = 0.2) -> str:
    cfg = ensure_configured()
    url = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
    }
    if cfg["thinking_enabled"] and "deepseek" in cfg["base_url"]:
        effort = cfg["reasoning_effort"]
        if effort not in ("high", "max"):
            effort = "high"
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = effort
    else:
        payload["temperature"] = temperature
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=cfg["timeout"])
    except requests.RequestException as exc:
        raise AIClientError(f"AI request failed: {exc}") from exc

    if resp.status_code >= 400:
        detail = resp.text[:500]
        raise AIClientError(_provider_error_message(resp.status_code, detail, cfg))

    try:
        data = resp.json()
        return data["choices"][0]["message"]["content"] or ""
    except Exception as exc:
        raise AIClientError("AI provider response format is invalid") from exc
