from __future__ import annotations

import json
import os
from typing import Any, Iterator

import requests

import ai_settings

DEFAULT_OPENAI_BASE = "https://api.openai.com/v1"
DEFAULT_ANTHROPIC_BASE = "https://api.anthropic.com"
SUPPORTED_PROVIDERS = ("openai", "anthropic", "deepseek", "minimax")
ANTHROPIC_VERSION = "2023-06-01"


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
        or ""
    )


def _normalize_provider(value: str) -> str:
    provider = str(value or "").strip().lower()
    return provider if provider in SUPPORTED_PROVIDERS else "openai"


def get_ai_config(role: str = "chat") -> dict[str, Any]:
    role = "vision" if role == "vision" else "chat"
    prefix = "AI_VISION_" if role == "vision" else "AI_CHAT_"
    provider = _normalize_provider(_env(f"{prefix}PROVIDER") or _env("AI_PROVIDER") or "openai")
    api_key = _env(f"{prefix}API_KEY") or _shared_fallback_key()
    model = _env(f"{prefix}MODEL") or _shared_fallback_model()
    default_base = DEFAULT_ANTHROPIC_BASE if provider == "anthropic" else DEFAULT_OPENAI_BASE
    if role == "chat":
        base_url = _env(f"{prefix}BASE_URL") or _shared_fallback_base() or default_base
    else:
        base_url = _env(f"{prefix}BASE_URL") or default_base
    if not base_url.startswith(("http://", "https://")):
        base_url = default_base

    # Embeddings 設定獨立於 chat base_url：
    # 只有明確設定 AI_EMBEDDING_BASE_URL 才採用；否則若 embedding provider 是
    # openai 就用官方 base，其他 provider 一律視為未設定，避免把 DeepSeek /
    # OpenRouter 之類的 chat base 誤當 embeddings endpoint（會靜默失敗）。
    embedding_provider = _normalize_provider(_env("AI_EMBEDDING_PROVIDER") or provider)
    embedding_model = _env("AI_EMBEDDING_MODEL", "text-embedding-3-small")
    explicit_embedding_base = _env("AI_EMBEDDING_BASE_URL")
    if explicit_embedding_base.startswith(("http://", "https://")):
        embedding_base_url = explicit_embedding_base
    elif embedding_provider == "openai":
        embedding_base_url = DEFAULT_OPENAI_BASE
    else:
        embedding_base_url = ""
    embedding_api_key = _env("AI_EMBEDDING_API_KEY") or api_key
    return {
        "role": role,
        "provider": provider,
        "base_url": base_url.rstrip("/"),
        "api_key": api_key,
        "model": model,
        "embedding_provider": embedding_provider,
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
    provider = cfg.get("provider") or "openai"
    model = cfg.get("model") or "(unknown model)"
    base_url = cfg.get("base_url") or "(unknown provider)"
    prefix = f"AI provider '{provider}' (model={model}, base_url={base_url})"
    if status_code == 401:
        return f"{prefix} returned HTTP 401. Check the API key."
    if status_code == 402:
        return f"{prefix} returned HTTP 402. Check provider billing or quota."
    if status_code == 429:
        return f"{prefix} returned HTTP 429. The provider rate limit was reached."
    if status_code == 404:
        return f"{prefix} returned HTTP 404: {detail[:400]}"
    return f"{prefix} returned HTTP {status_code}: {detail[:500]}"


def _invalid_response_message(cfg: dict[str, Any]) -> str:
    provider = cfg.get("provider") or "openai"
    model = cfg.get("model") or "(unknown model)"
    return f"AI provider '{provider}' (model={model}) response format is invalid"


def _post(url: str, headers: dict[str, Any], payload: dict[str, Any], timeout: Any, stream: bool = False):
    try:
        return requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=float(timeout or 45),
            stream=stream,
        )
    except requests.RequestException as exc:
        raise AIClientError(f"AI request failed: {exc}") from exc


def _raise_for_status(resp: Any, cfg: dict[str, Any]) -> None:
    if resp.status_code >= 400:
        detail = getattr(resp, "text", "") or ""
        raise AIClientError(_provider_error_message(resp.status_code, detail[:500], cfg))


# ----------------------------------------------------------------------
# Request shaping per provider. Selected by cfg['provider'] ONLY - never by
# substring sniffing the base URL, so unrelated OpenAI-compatible providers
# never receive DeepSeek / MiniMax exotic fields.
# ----------------------------------------------------------------------

def _build_chat_payload(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    temperature: float = 0.2,
    max_tokens: int | None = None,
    thinking: bool | None = None,
) -> dict[str, Any]:
    provider = _normalize_provider(cfg.get("provider"))
    payload: dict[str, Any] = {
        "model": cfg.get("model"),
        "messages": messages,
    }
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = tool_choice or "auto"
    if response_format:
        payload["response_format"] = response_format
    if max_tokens:
        payload["max_tokens"] = int(max_tokens)

    use_thinking = bool(cfg.get("thinking_enabled", True)) if thinking is None else bool(thinking)
    effort = str(cfg.get("reasoning_effort") or "high").lower()

    if provider == "deepseek":
        if use_thinking:
            payload["thinking"] = {"type": "enabled"}
            payload["reasoning_effort"] = effort if effort in ("high", "max") else "high"
            # thinking 模式下 DeepSeek 不接受 temperature
        else:
            payload["thinking"] = {"type": "disabled"}
            payload["reasoning_split"] = True
            payload["temperature"] = temperature
    elif provider == "minimax":
        if use_thinking:
            payload["thinking"] = {"type": "enabled"}
        else:
            payload["thinking"] = {"type": "disabled"}
            payload["temperature"] = temperature
        payload["reasoning_split"] = True
    else:
        # plain OpenAI-compatible: clean payload, standard temperature
        payload["temperature"] = temperature
    return payload


def _normalize_openai_tool_calls(raw: Any) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for call in raw or []:
        if not isinstance(call, dict):
            continue
        fn = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = str(fn.get("name") or call.get("name") or "").strip()
        if not name:
            continue
        raw_args = fn.get("arguments")
        if isinstance(raw_args, str):
            arguments = raw_args
        elif raw_args is None:
            arguments = "{}"
        else:
            arguments = json.dumps(raw_args, ensure_ascii=False, default=str)
        normalized.append({
            "id": str(call.get("id") or ""),
            "type": "function",
            "function": {"name": name, "arguments": arguments},
        })
    return normalized


def _openai_assistant_message(data: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        choices = data.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
    except Exception as exc:
        raise AIClientError(_invalid_response_message(cfg)) from exc
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        )
    result: dict[str, Any] = {
        "role": "assistant",
        "content": content if isinstance(content, str) else str(content or ""),
    }
    tool_calls = _normalize_openai_tool_calls(message.get("tool_calls"))
    if tool_calls:
        result["tool_calls"] = tool_calls
    return result


def chat_message(
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    role: str = "chat",
    thinking: bool | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
) -> dict[str, Any]:
    """Return an OpenAI-style assistant message in ALL cases.

    Hard contract consumed by assistant.py:
        {'role': 'assistant', 'content': str,
         'tool_calls': [{'id': str, 'type': 'function',
                         'function': {'name': str, 'arguments': str(JSON)}}]}
    tool_calls is omitted when there are none.
    """
    cfg = ensure_chat_configured(role)
    if cfg.get("provider") == "anthropic":
        return _chat_anthropic(
            cfg,
            messages,
            temperature,
            tools=tools,
            tool_choice=tool_choice,
            response_format=response_format,
            timeout=timeout,
            max_tokens=max_tokens,
            thinking=thinking,
        )
    payload = _build_chat_payload(
        cfg,
        messages,
        tools=tools,
        tool_choice=tool_choice,
        response_format=response_format,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking=thinking,
    )
    url = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    resp = _post(url, headers, payload, timeout or cfg["timeout"])
    _raise_for_status(resp, cfg)
    try:
        data = resp.json()
    except Exception as exc:
        raise AIClientError(_invalid_response_message(cfg)) from exc
    return _openai_assistant_message(data, cfg)


# ----------------------------------------------------------------------
# Anthropic adapter
# ----------------------------------------------------------------------

def _anthropic_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if content is None:
        return ""
    if not isinstance(content, list):
        return str(content)
    converted = []
    for part in content:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            converted.append({"type": "text", "text": part.get("text") or ""})
        elif ptype == "image_url":
            url = ((part.get("image_url") or {}).get("url") or "")
            if url.startswith("data:") and ";base64," in url:
                header, data = url.split(";base64,", 1)
                mime = header.replace("data:", "") or "image/jpeg"
                converted.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": mime, "data": data},
                })
        elif ptype in ("tool_result", "image"):
            converted.append(part)
    return converted or ""


def _anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    converted = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        fn = tool.get("function") if isinstance(tool.get("function"), dict) else tool
        name = str(fn.get("name") or "").strip()
        if not name:
            continue
        schema = fn.get("parameters")
        if not isinstance(schema, dict):
            schema = {"type": "object", "properties": {}}
        converted.append({
            "name": name,
            "description": str(fn.get("description") or ""),
            "input_schema": schema,
        })
    return converted


def _anthropic_tool_result_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False, default=str)
    except Exception:
        return str(content or "")


def _anthropic_assistant_args(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
            return parsed if isinstance(parsed, dict) else {"_raw": text}
        except Exception:
            return {"_raw": text}
    return {}


def _anthropic_messages(messages: list[dict[str, Any]]) -> tuple[str, list[dict[str, Any]]]:
    """Convert OpenAI-style history into Anthropic (system, messages).

    - system messages collapse into the top-level `system` string
    - OpenAI {role: 'tool', tool_call_id, content} -> user content `tool_result` block
    - assistant tool_calls -> `tool_use` blocks
    - consecutive tool results are grouped into a single user message
    """
    system_parts: list[str] = []
    converted: list[dict[str, Any]] = []
    pending_tool_results: list[dict[str, Any]] = []

    def flush_tool_results() -> None:
        if pending_tool_results:
            converted.append({"role": "user", "content": list(pending_tool_results)})
            pending_tool_results.clear()

    for message in messages or []:
        if not isinstance(message, dict):
            continue
        role = message.get("role") or "user"
        if role == "system":
            text = message.get("content")
            if isinstance(text, str) and text.strip():
                system_parts.append(text.strip())
            elif text:
                system_parts.append(str(text))
            continue
        if role == "tool":
            pending_tool_results.append({
                "type": "tool_result",
                "tool_use_id": str(message.get("tool_call_id") or message.get("id") or ""),
                "content": _anthropic_tool_result_content(message.get("content")),
            })
            continue
        flush_tool_results()
        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            content = message.get("content")
            if isinstance(content, str) and content:
                blocks.append({"type": "text", "text": content})
            elif isinstance(content, list):
                converted_content = _anthropic_content(content)
                if isinstance(converted_content, list):
                    blocks.extend(converted_content)
                elif converted_content:
                    blocks.append({"type": "text", "text": str(converted_content)})
            for call in message.get("tool_calls") or []:
                if not isinstance(call, dict):
                    continue
                fn = call.get("function") if isinstance(call.get("function"), dict) else {}
                name = str(fn.get("name") or "").strip()
                if not name:
                    continue
                blocks.append({
                    "type": "tool_use",
                    "id": str(call.get("id") or ""),
                    "name": name,
                    "input": _anthropic_assistant_args(fn.get("arguments")),
                })
            if not blocks:
                blocks = [{"type": "text", "text": str(content or "")}]
            converted.append({"role": "assistant", "content": blocks})
        else:
            converted.append({"role": "user", "content": _anthropic_content(message.get("content"))})
    flush_tool_results()
    return "\n\n".join(system_parts), converted


def _anthropic_assistant_message(data: Any, cfg: dict[str, Any]) -> dict[str, Any]:
    try:
        parts = data.get("content") or []
    except Exception as exc:
        raise AIClientError(_invalid_response_message(cfg)) from exc
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        ptype = part.get("type")
        if ptype == "text":
            text_parts.append(str(part.get("text") or ""))
        elif ptype == "tool_use":
            tool_calls.append({
                "id": str(part.get("id") or ""),
                "type": "function",
                "function": {
                    "name": str(part.get("name") or ""),
                    "arguments": json.dumps(part.get("input") or {}, ensure_ascii=False, default=str),
                },
            })
    message: dict[str, Any] = {"role": "assistant", "content": "".join(text_parts)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return message


def _chat_anthropic(
    cfg: dict[str, Any],
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    response_format: dict[str, Any] | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
    thinking: bool | None = None,
) -> dict[str, Any]:
    base = str(cfg["base_url"] or "").rstrip("/")
    if base.endswith("/messages"):
        url = base
    elif base.endswith("/v1"):
        url = f"{base}/messages"
    else:
        url = f"{base}/v1/messages"

    system, converted = _anthropic_messages(messages)
    payload: dict[str, Any] = {
        "model": cfg["model"],
        "max_tokens": int(max_tokens) if max_tokens else 2048,
        "messages": converted,
    }
    if system:
        payload["system"] = system
    anthropic_tools = _anthropic_tools(tools)
    if anthropic_tools:
        payload["tools"] = anthropic_tools
        if isinstance(tool_choice, dict):
            fn = tool_choice.get("function") if isinstance(tool_choice.get("function"), dict) else {}
            name = str(fn.get("name") or "").strip()
            payload["tool_choice"] = {"type": "tool", "name": name} if name else {"type": "auto"}
        else:
            payload["tool_choice"] = {"type": "auto"}
    # Anthropic always accepts temperature (thinking mode is opt-in via `thinking`).
    payload["temperature"] = temperature

    headers = {
        "x-api-key": cfg["api_key"],
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    resp = _post(url, headers, payload, timeout or cfg["timeout"])
    _raise_for_status(resp, cfg)
    try:
        data = resp.json()
    except Exception as exc:
        raise AIClientError(_invalid_response_message(cfg)) from exc
    return _anthropic_assistant_message(data, cfg)


# ----------------------------------------------------------------------
# Optional streaming (OpenAI-compatible providers only). Not wired anywhere yet.
# ----------------------------------------------------------------------

def stream_chat_message(
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    tools: list[dict[str, Any]] | None = None,
    role: str = "chat",
    thinking: bool | None = None,
    timeout: float | None = None,
    max_tokens: int | None = None,
) -> Iterator[str]:
    """Yield text deltas from an OpenAI-compatible streaming endpoint.

    Raises NotImplementedError for providers without a compatible stream. This is
    intentionally not wired into any route yet.
    """
    cfg = ensure_chat_configured(role)
    if cfg.get("provider") == "anthropic":
        raise NotImplementedError("stream_chat_message does not support provider 'anthropic'")
    payload = _build_chat_payload(
        cfg,
        messages,
        tools=tools,
        temperature=temperature,
        max_tokens=max_tokens,
        thinking=thinking,
    )
    payload["stream"] = True
    url = f"{cfg['base_url']}/chat/completions"
    headers = {
        "Authorization": f"Bearer {cfg['api_key']}",
        "Content-Type": "application/json",
    }
    resp = _post(url, headers, payload, timeout or cfg["timeout"], stream=True)
    _raise_for_status(resp, cfg)
    try:
        for line in resp.iter_lines(decode_unicode=True):
            if not line:
                continue
            text = line.strip()
            if text.startswith("data:"):
                text = text[5:].strip()
            if not text or text == "[DONE]":
                if text == "[DONE]":
                    break
                continue
            if not text.startswith("{"):
                continue
            try:
                event = json.loads(text)
            except Exception:
                continue
            choices = event.get("choices") or []
            if not choices:
                continue
            delta = choices[0].get("delta") or {}
            piece = delta.get("content")
            if piece:
                yield str(piece)
    finally:
        try:
            resp.close()
        except Exception:
            pass


def chat_completion(
    messages: list[dict[str, Any]],
    temperature: float = 0.2,
    response_format: dict[str, Any] | None = None,
    role: str = "chat",
) -> str:
    message = chat_message(messages, temperature=temperature, response_format=response_format, role=role)
    return str(message.get("content") or "")


def check_provider_health(role: str = "chat") -> dict[str, Any]:
    """Diagnostic helper for a future /api/ai/health route.

    Never performs a network call unless both an API key and a model are set.
    """
    cfg = get_ai_config(role)
    configured = bool(cfg.get("api_key") and cfg.get("model"))
    result: dict[str, Any] = {
        "configured": configured,
        "provider": cfg.get("provider"),
        "model": cfg.get("model"),
        "base_url": cfg.get("base_url"),
        "reachable": None,
        "error": "",
    }
    if not configured:
        return result
    try:
        if cfg.get("provider") == "anthropic":
            _chat_anthropic(
                cfg,
                [{"role": "user", "content": "ping"}],
                0.0,
                max_tokens=1,
            )
        else:
            payload = _build_chat_payload(
                cfg,
                [{"role": "user", "content": "ping"}],
                temperature=0.0,
                max_tokens=1,
            )
            url = f"{cfg['base_url']}/chat/completions"
            headers = {
                "Authorization": f"Bearer {cfg['api_key']}",
                "Content-Type": "application/json",
            }
            resp = _post(url, headers, payload, cfg["timeout"])
            _raise_for_status(resp, cfg)
        result["reachable"] = True
    except Exception as exc:
        result["reachable"] = False
        result["error"] = str(exc)
    return result
