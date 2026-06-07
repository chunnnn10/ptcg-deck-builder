from __future__ import annotations

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
    embedding_model = _env("AI_EMBEDDING_MODEL", "text-embedding-3-small")
    base_url = (
        _env("AI_BASE_URL")
        or _env("DEEPSEEK_BASE_URL")
        or _env("OPENROUTER_BASE_URL")
        or _env("GOMODEL_BASE_URL")
        or "https://api.openai.com/v1"
    )
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://api.openai.com/v1"
    embedding_base_url = _env("AI_EMBEDDING_BASE_URL") or base_url
    if not embedding_base_url.startswith(("http://", "https://")):
        embedding_base_url = base_url
    embedding_api_key = _env("AI_EMBEDDING_API_KEY") or api_key
    return {
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "embedding_base_url": embedding_base_url.rstrip("/"),
        "embedding_api_key": embedding_api_key,
        "embedding_model": embedding_model,
        "embedding_dimensions": int(_env("AI_EMBEDDING_DIMENSIONS", "1536") or 1536),
        "timeout": float(_env("AI_TIMEOUT", "45") or 45),
        "thinking_enabled": _env("AI_THINKING_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
        "reasoning_effort": _env("AI_REASONING_EFFORT", "high").lower(),
    }


def ensure_chat_configured() -> dict[str, Any]:
    cfg = get_ai_config()
    if not cfg["api_key"] or not cfg["model"]:
        raise AIConfigError("AI chat is not configured. Set AI_API_KEY and AI_MODEL.")
    return cfg


def ensure_configured() -> dict[str, Any]:
    return ensure_chat_configured()


def _provider_error_message(status_code: int, detail: str, cfg: dict[str, Any]) -> str:
    model = cfg.get("model") or "(unknown model)"
    base_url = cfg.get("base_url") or "(unknown provider)"
    if status_code == 401:
        return f"AI provider returned HTTP 401 while using model {model} on {base_url}. Check the API key."
    if status_code == 402:
        return f"AI provider returned HTTP 402 while using model {model} on {base_url}. Check provider billing or quota."
    if status_code == 429:
        return f"AI provider returned HTTP 429 while using model {model} on {base_url}. The provider rate limit was reached."
    if status_code == 404:
        return f"AI provider returned HTTP 404 while using model {model} on {base_url}: {detail[:400]}"
    return f"AI provider returned HTTP {status_code} while using model {model} on {base_url}: {detail[:500]}"


def chat_message(
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
) -> dict[str, Any]:
    cfg = ensure_chat_configured()
    url = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
    if response_format:
        payload["response_format"] = response_format
    if cfg["thinking_enabled"] and "deepseek" in cfg["base_url"]:
        effort = cfg["reasoning_effort"]
        payload["thinking"] = {"type": "enabled"}
        payload["reasoning_effort"] = effort if effort in ("high", "max") else "high"
    else:
        payload["temperature"] = temperature

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=cfg["timeout"])
    except requests.RequestException as exc:
        raise AIClientError(f"AI request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise AIClientError(_provider_error_message(resp.status_code, resp.text[:500], cfg))

    try:
        data = resp.json()
        return data["choices"][0]["message"] or {}
    except Exception as exc:
        raise AIClientError("AI provider response format is invalid") from exc


def chat_completion(
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
) -> str:
    message = chat_message(messages, temperature=temperature, response_format=response_format)
    return str(message.get("content") or "")
