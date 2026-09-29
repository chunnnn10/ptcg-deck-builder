from __future__ import annotations

import hashlib
from typing import Any
from urllib.parse import urlparse

import requests

from .client import AIClientError, AIConfigError, _env, get_ai_config

DEFAULT_OPENAI_EMBEDDING_BASE = "https://api.openai.com/v1"
DEFAULT_OPENAI_EMBEDDING_MODEL = "text-embedding-3-small"
DEFAULT_MINIMAX_EMBEDDING_MODEL = "embo-01"
MINIMAX_EMBEDDING_TYPE = "db"


def content_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def _explicit_embedding_provider() -> str:
    """AI_EMBEDDING_PROVIDER as explicitly configured (DB then env), or ''."""
    return _env("AI_EMBEDDING_PROVIDER").strip().lower()


def _infer_provider_from_base_url(base_url: str) -> str:
    """Infer an embedding provider from the embedding base URL host."""
    host = (urlparse(str(base_url or "")).hostname or "").lower()
    if "minimax" in host:
        return "minimax"
    return "openai"


def _resolve_embedding_target() -> dict[str, Any]:
    """Resolve provider/model/base_url/key/dimensions. No network calls.

    Returns a dict with 'configured' plus the resolved fields and an 'error'
    string when the target cannot be used. Never raises.
    """
    cfg = get_ai_config()
    explicit_provider = _explicit_embedding_provider()
    explicit_model = _env("AI_EMBEDDING_MODEL").strip()
    chat_base_url = str(cfg.get("base_url") or "").strip()
    base_url = str(cfg.get("embedding_base_url") or "").strip()
    api_key = cfg.get("embedding_api_key")
    dimensions = cfg.get("embedding_dimensions")

    # Provider: explicit AI_EMBEDDING_PROVIDER wins, else infer from the
    # embedding base URL host, else infer from the chat base URL host (used
    # only to explain/allow explicit embedding-provider inheritance).
    if explicit_provider:
        provider = explicit_provider
    elif base_url:
        provider = _infer_provider_from_base_url(base_url)
    elif chat_base_url:
        provider = _infer_provider_from_base_url(chat_base_url)
    else:
        provider = "openai"

    # Base URL rules (never silently inherit the chat base_url by luck):
    # - an explicit AI_EMBEDDING_BASE_URL already wins via cfg.
    # - openai may fall back to the official API endpoint.
    # - a non-openai provider may inherit the chat base_url ONLY when that
    #   provider is explicitly the configured embedding provider and the chat
    #   host matches it; otherwise embeddings need their own base/model.
    if not base_url:
        if provider == "openai":
            base_url = DEFAULT_OPENAI_EMBEDDING_BASE
        elif (
            explicit_provider
            and chat_base_url.startswith(("http://", "https://"))
            and _infer_provider_from_base_url(chat_base_url) == explicit_provider
        ):
            base_url = chat_base_url

    model = explicit_model or str(cfg.get("embedding_model") or "").strip()
    if provider == "minimax" and not explicit_model:
        model = DEFAULT_MINIMAX_EMBEDDING_MODEL
    elif not model:
        model = DEFAULT_OPENAI_EMBEDDING_MODEL

    label = "provider=%s model=%s base_url=%s" % (
        provider or "(unknown)",
        model or "(unknown)",
        base_url or "(unset)",
    )

    error = ""
    if not model:
        error = "AI embedding is not configured: AI_EMBEDDING_MODEL is empty (%s)." % label
    elif not api_key:
        error = (
            "AI embedding is not configured: set AI_EMBEDDING_API_KEY (or a usable "
            "chat API key) for %s." % label
        )
    elif not base_url:
        error = (
            "AI embedding base URL is not configured for %s. Set "
            "AI_EMBEDDING_BASE_URL to a provider /embeddings endpoint; the chat "
            "base_url is intentionally not reused because this provider has no "
            "usable embeddings route." % label
        )
    elif not base_url.startswith(("http://", "https://")):
        error = "AI embedding base URL is invalid for %s." % label

    return {
        "configured": not error,
        "error": error,
        "provider": provider,
        "model": model,
        "base_url": base_url,
        "api_key": api_key,
        "dimensions": dimensions,
        "timeout": cfg.get("timeout") or 45,
        "chat_cfg": cfg,
    }


def get_embedding_config() -> dict[str, Any]:
    """Return chat-independent embedding config, or raise AIConfigError.

    Embeddings must NOT silently reuse the chat base_url: MiniMax / DeepSeek /
    OpenRouter chat endpoints do not expose a usable /embeddings route, so
    inheriting them made RAG fail silently. The embedding provider is resolved
    explicitly (AI_EMBEDDING_PROVIDER) or from the embedding base URL host, and
    a non-OpenAI provider without its own base URL is rejected.
    """
    target = _resolve_embedding_target()
    if not target["configured"]:
        raise AIConfigError(target["error"])

    cfg = dict(target["chat_cfg"])
    cfg["embedding_provider"] = target["provider"]
    cfg["embedding_model"] = target["model"]
    cfg["embedding_base_url"] = str(target["base_url"]).rstrip("/")
    cfg["embedding_api_key"] = target["api_key"]
    if target["dimensions"] is not None:
        cfg["embedding_dimensions"] = target["dimensions"]
    cfg["timeout"] = target["timeout"]
    return cfg


def embedding_health() -> dict[str, Any]:
    """Report embedding configuration state without any network call."""
    target = _resolve_embedding_target()
    return {
        "configured": bool(target["configured"]),
        "provider": target["provider"],
        "model": target["model"],
        "base_url": target["base_url"],
        "dimensions": target["dimensions"],
        "last_error": target["error"] or "",
    }


def _embedding_error(cfg: dict[str, Any], message: str) -> str:
    provider = cfg.get("embedding_provider") or "(unknown)"
    model = cfg.get("embedding_model") or "(unknown model)"
    base_url = cfg.get("embedding_base_url") or "(unknown base_url)"
    return f"[embedding provider={provider} model={model} base_url={base_url}] {message}"


def _parse_openai_embeddings(data: dict[str, Any]) -> list[Any]:
    items = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
    return [item.get("embedding") for item in items]


def _parse_minimax_embeddings(data: dict[str, Any], cfg: dict[str, Any]) -> list[Any]:
    base_resp = data.get("base_resp")
    if isinstance(base_resp, dict):
        status_code = base_resp.get("status_code")
        if status_code not in (None, 0):
            status_msg = (
                base_resp.get("status_msg")
                or base_resp.get("status_message")
                or "unknown error"
            )
            raise AIClientError(
                _embedding_error(
                    cfg,
                    f"provider returned error status_code={status_code}: {status_msg}",
                )
            )
    vectors = data.get("vectors")
    if vectors is None:
        raise AIClientError(
            _embedding_error(cfg, "response contained no 'vectors' field")
        )
    return list(vectors)


def _validate_embeddings(embeddings: list[Any], count: int, cfg: dict[str, Any]) -> list[list[float]]:
    if len(embeddings) != count:
        raise AIClientError(
            _embedding_error(cfg, f"returned {len(embeddings)} vectors for {count} inputs")
        )
    cleaned: list[list[float]] = []
    for vec in embeddings:
        if not isinstance(vec, (list, tuple)) or not vec:
            raise AIClientError(
                _embedding_error(cfg, "returned an empty or non-list embedding vector")
            )
        try:
            cleaned.append([float(value) for value in vec])
        except (TypeError, ValueError) as exc:
            raise AIClientError(
                _embedding_error(cfg, "returned an embedding vector with non-numeric values")
            ) from exc
    return cleaned


def embed_texts(texts: list[str]) -> list[list[float]]:
    texts = [str(text or "").strip() for text in texts]
    if not texts:
        return []

    cfg = get_embedding_config()
    provider = cfg.get("embedding_provider") or "openai"
    url = f"{cfg['embedding_base_url']}/embeddings"
    headers = {
        "Authorization": f"Bearer {cfg['embedding_api_key']}",
        "Content-Type": "application/json",
    }

    if provider == "minimax":
        payload: dict[str, Any] = {
            "model": cfg["embedding_model"] or DEFAULT_MINIMAX_EMBEDDING_MODEL,
            "texts": texts,
            "type": MINIMAX_EMBEDDING_TYPE,
        }
    else:
        payload = {
            "model": cfg["embedding_model"],
            "input": texts,
        }
        dimensions = cfg.get("embedding_dimensions")
        if dimensions:
            payload["dimensions"] = int(dimensions)

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=cfg["timeout"])
    except requests.RequestException as exc:
        raise AIClientError(_embedding_error(cfg, f"request failed: {exc}")) from exc

    if resp.status_code >= 400:
        raise AIClientError(
            _embedding_error(cfg, f"returned HTTP {resp.status_code}: {resp.text[:500]}")
        )

    try:
        data = resp.json()
    except Exception as exc:
        raise AIClientError(_embedding_error(cfg, "response is not valid JSON")) from exc
    if not isinstance(data, dict):
        raise AIClientError(_embedding_error(cfg, "response is not a JSON object"))

    if provider == "minimax":
        embeddings = _parse_minimax_embeddings(data, cfg)
    else:
        embeddings = _parse_openai_embeddings(data)

    return _validate_embeddings(embeddings, len(texts), cfg)


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in values) + "]"
