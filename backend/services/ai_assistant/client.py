from __future__ import annotations

import os
from typing import Any

import requests

import ai_settings


class AIConfigError(RuntimeError):
    pass


class AIClientError(RuntimeError):
    pass


def _env(name: str, default: str = "") -> str:
    # 動態設定（admin 面板可編輯，存於 DB）優先，其次環境變量，最後 default
    db_val = ai_settings.get_ai_setting(name, "")
    if db_val:
        return str(db_val).strip()
    return str(os.environ.get(name) or default).strip()


def _shared_fallback_key() -> str:
    return (
        _env("AI_API_KEY")
        or _env("DEEPSEEK_API_KEY")
        or _env("OPENROUTER_KEY_MAIN_1")
        or _env("OPENROUTER_KEY_FREE_1")
        or _env("OPENROUTER_KEY_FREE_2")
        or _env("GOMODEL_KEY")
    )


def _shared_fallback_model() -> str:
    return (
        _env("AI_MODEL")
        or _env("DEEPSEEK_MODEL")
        or _env("OPENROUTER_MODEL_GEN")
        or _env("GOMODEL_MODEL_GEN")
    )


def _shared_fallback_base() -> str:
    return (
        _env("AI_BASE_URL")
        or _env("DEEPSEEK_BASE_URL")
        or _env("OPENROUTER_BASE_URL")
        or _env("GOMODEL_BASE_URL")
        or "https://api.openai.com/v1"
    )


def get_ai_config(role: str = "chat") -> dict[str, Any]:
    role = "vision" if role == "vision" else "chat"
    prefix = "AI_VISION_" if role == "vision" else "AI_CHAT_"
    provider = (_env(f"{prefix}PROVIDER") or _env("AI_PROVIDER") or "openai").strip().lower()
    if provider not in ("openai", "anthropic"):
        provider = "openai"
    api_key = _env(f"{prefix}API_KEY") or _shared_fallback_key()
    model = _env(f"{prefix}MODEL") or _shared_fallback_model()
    default_base = "https://api.anthropic.com" if provider == "anthropic" else "https://api.openai.com/v1"
    base_url = _env(f"{prefix}BASE_URL") or (_shared_fallback_base() if role == "chat" else default_base) or default_base
    if not base_url.startswith(("http://", "https://")):
        base_url = default_base
    embedding_model = _env("AI_EMBEDDING_MODEL", "text-embedding-3-small")
    embedding_base_url = _env("AI_EMBEDDING_BASE_URL") or _shared_fallback_base()
    if not embedding_base_url.startswith(("http://", "https://")):
        embedding_base_url = base_url
    embedding_api_key = _env("AI_EMBEDDING_API_KEY") or api_key
    return {
        "role": role,
        "provider": provider,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "embedding_base_url": embedding_base_url.rstrip("/"),
        "embedding_api_key": embedding_api_key,
        "embedding_model": embedding_model,
        "embedding_dimensions": int(_env("AI_EMBEDDING_DIMENSIONS", "1536") or 1536),
        "timeout": float(_env(f"{prefix}TIMEOUT") or _env("AI_TIMEOUT", "45") or 45),
        "thinking_enabled": _env("AI_THINKING_ENABLED", "true").lower() in ("1", "true", "yes", "on"),
        "reasoning_effort": _env("AI_REASONING_EFFORT", "high").lower(),
    }


def ensure_chat_configured(role: str = "chat") -> dict[str, Any]:
    cfg = get_ai_config(role)
    if not cfg["api_key"] or not cfg["model"]:
        label = "Admin 讀圖 AI" if role == "vision" else "用戶詢問 AI"
        raise AIConfigError(f"{label} 未設定。請在 AI 設定頁填 API Key 同模型。")
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


def _anthropic_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    converted = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            converted.append({"type": "text", "text": part.get("text") or ""})
        elif part.get("type") == "image_url":
            url = ((part.get("image_url") or {}).get("url") or "")
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(";base64,", 1)
                mime = header.replace("data:", "") or "image/jpeg"
                converted.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": data},
                })
    return converted or ""


def chat_message(
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    role: str = "chat",
) -> dict[str, Any]:
    cfg = ensure_chat_configured(role)
    if cfg.get("provider") == "anthropic":
        return _chat_anthropic(cfg, messages, temperature)
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


def _chat_anthropic(cfg: dict[str, Any], messages: list[dict[str, Any]], temperature: float) -> dict[str, Any]:
    base = cfg["base_url"].rstrip("/")
    url = base if base.endswith("/messages") else f"{base}/v1/messages"
    if base.endswith("/v1"):
        url = f"{base}/messages"
    system = ""
    converted = []
    for message in messages:
        role = message.get("role") or "user"
        if role == "system":
            system = str(message.get("content") or "")
            continue
        if role not in ("user", "assistant"):
            role = "user"
        converted.append({"role": role, "content": _anthropic_content(message.get("content"))})
    payload = {
        "model": cfg["model"],
        "max_tokens": 2048,
        "temperature": temperature,
        "messages": converted,
    }
    if system:
        payload["system"] = system
    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=cfg["timeout"])
    except requests.RequestException as exc:
        raise AIClientError(f"AI request failed: {exc}") from exc
    if resp.status_code >= 400:
        raise AIClientError(_provider_error_message(resp.status_code, resp.text[:500], cfg))
    try:
        data = resp.json()
        parts = data.get("content") or []
        text = "".join(part.get("text") or "" for part in parts if isinstance(part, dict))
        return {"role": "assistant", "content": text}
    except Exception as exc:
        raise AIClientError("AI provider response format is invalid") from exc


def chat_completion(
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
    role: str = "chat",
) -> str:
    message = chat_message(messages, temperature=temperature, response_format=response_format, role=role)
    return str(message.get("content") or "")
