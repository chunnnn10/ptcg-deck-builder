from __future__ import annotations

import hashlib
from typing import Any

import requests

from .client import AIClientError, AIConfigError, get_ai_config

DEFAULT_OPENAI_EMBEDDING_BASE = "https://api.openai.com/v1"


def content_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def get_embedding_config() -> dict[str, Any]:
    """Return chat-independent embedding config, or raise AIConfigError.

    Embeddings must NOT silently reuse the chat base_url: DeepSeek / OpenRouter
    chat endpoints have no /embeddings route, so inheriting them made RAG fail
    silently. We therefore require an explicit embedding base URL unless the
    embedding provider is openai (official API).
    """
    cfg = get_ai_config()
    provider = str(cfg.get("embedding_provider") or cfg.get("provider") or "openai")
    model = str(cfg.get("embedding_model") or "").strip()
    base_url = str(cfg.get("embedding_base_url") or "").strip()
    api_key = cfg.get("embedding_api_key")

    if not model:
        raise AIConfigError(
            "AI embedding is not configured: AI_EMBEDDING_MODEL is empty "
            "(provider=%s)." % provider
        )
    if not api_key:
        raise AIConfigError(
            "AI embedding is not configured: set AI_EMBEDDING_API_KEY (or a chat "
            "API key) for provider=%s, model=%s." % (provider, model)
        )
    if not base_url:
        raise AIConfigError(
            "AI embedding base URL is not configured for provider=%s, model=%s. "
            "Set AI_EMBEDDING_BASE_URL to an OpenAI-compatible /embeddings endpoint "
            "(chat base_url is intentionally not reused: %s)"
            % (provider, model, cfg.get("base_url") or "(unset)")
        )
    if not base_url.startswith(("http://", "https://")):
        raise AIConfigError(
            "AI embedding base URL is invalid for provider=%s, model=%s: %r"
            % (provider, model, base_url)
        )
    return cfg


def _embedding_error(cfg: dict[str, Any], message: str) -> str:
    provider = cfg.get("embedding_provider") or cfg.get("provider") or "openai"
    model = cfg.get("embedding_model") or "(unknown model)"
    base_url = cfg.get("embedding_base_url") or "(unknown base_url)"
    return f"[embedding provider={provider} model={model} base_url={base_url}] {message}"


def embed_texts(texts: list[str]) -> list[list[float]]:
    texts = [str(text or "").strip() for text in texts]
    if not texts:
        return []

    cfg = get_embedding_config()
    url = f"{cfg['embedding_base_url']}/embeddings"
    headers = {
        "Authorization": f"Bearer {cfg['embedding_api_key']}",
        "Content-Type": "application/json",
    }
    payload: dict[str, Any] = {
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
        items = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
        embeddings = [item["embedding"] for item in items]
    except Exception as exc:
        raise AIClientError(_embedding_error(cfg, "response format is invalid")) from exc

    if len(embeddings) != len(texts):
        raise AIClientError(
            _embedding_error(
                cfg,
                f"returned {len(embeddings)} vectors for {len(texts)} inputs",
            )
        )
    return embeddings


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in values) + "]"
