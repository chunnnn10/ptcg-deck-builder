from __future__ import annotations

import hashlib
from typing import Any

import requests

from .client import AIClientError, AIConfigError, get_ai_config


def content_hash(value: str) -> str:
    return hashlib.sha256(str(value or "").encode("utf-8")).hexdigest()


def get_embedding_config() -> dict[str, Any]:
    cfg = get_ai_config()
    model = cfg.get("embedding_model") or ""
    if not cfg.get("embedding_api_key") or not model:
        raise AIConfigError("AI embedding is not configured. Set AI_EMBEDDING_API_KEY and AI_EMBEDDING_MODEL, or use a chat provider that supports embeddings.")
    return cfg


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
        raise AIClientError(f"AI embedding request failed: {exc}") from exc

    if resp.status_code >= 400:
        raise AIClientError(f"AI embedding provider returned HTTP {resp.status_code}: {resp.text[:500]}")

    try:
        data = resp.json()
        items = sorted(data.get("data") or [], key=lambda item: item.get("index", 0))
        embeddings = [item["embedding"] for item in items]
    except Exception as exc:
        raise AIClientError("AI embedding provider response format is invalid") from exc

    if len(embeddings) != len(texts):
        raise AIClientError("AI embedding provider returned an unexpected number of vectors")
    return embeddings


def vector_literal(values: list[float]) -> str:
    return "[" + ",".join(str(float(v)) for v in values) + "]"
