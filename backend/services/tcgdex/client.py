"""
TCGDex REST API 客戶端
Base URL: https://api.tcgdex.net/v2
無需 API key，無 rate limit。

用法:
    client = TCGDexClient()
    card = client.get_card('ja', 'SV2a-025')       # 日文皮卡丘
    card = client.get_card('zh-tw', 'SV2a-025')    # 繁中皮卡丘
    cards = client.search_cards('zh-tw', '皮卡丘')  # 名稱搜尋
    sets = client.list_sets('ja')                   # 日文系列列表
"""
import time
import logging
from typing import Optional
from urllib.parse import quote

import requests

logger = logging.getLogger(__name__)

API_BASE = "https://api.tcgdex.net/v2"
REQUEST_TIMEOUT = 10  # seconds
MAX_RETRIES = 3
RETRY_BACKOFF = 0.5  # seconds base


class TCGDexClient:
    """TCGDex REST API 封裝，含簡易快取和重試。"""

    def __init__(self):
        self._session = requests.Session()
        self._session.headers.update({
            "User-Agent": "ChunDeckBuilder/2.0",
            "Accept": "application/json",
        })
        # 簡易記憶體快取
        self._cache: dict[str, Optional[dict]] = {}

    # ── 核心請求 ──

    def _get(self, url: str) -> Optional[dict]:
        """發送 GET 請求，含重試。"""
        for attempt in range(MAX_RETRIES):
            try:
                resp = self._session.get(url, timeout=REQUEST_TIMEOUT)
                if resp.status_code == 404:
                    return None
                if resp.status_code == 200:
                    return resp.json()
                logger.warning(f"TCGDex HTTP {resp.status_code} for {url}")
            except requests.RequestException as e:
                logger.warning(f"TCGDex request failed (attempt {attempt + 1}): {e}")
            if attempt < MAX_RETRIES - 1:
                time.sleep(RETRY_BACKOFF * (2 ** attempt))
        return None

    def _cached_get(self, url: str) -> Optional[dict]:
        """帶快取的 GET。"""
        if url not in self._cache:
            self._cache[url] = self._get(url)
        return self._cache[url]

    # ── Card ──

    def get_card(self, lang: str, card_id: str) -> Optional[dict]:
        """取得單張卡牌完整資料。
        lang: 'en', 'ja', 'zh-tw', ...
        card_id: 'SV2a-025', 'swsh3-136', ...
        """
        url = f"{API_BASE}/{lang}/cards/{card_id}"
        return self._cached_get(url)

    # ── Search ──

    def search_cards(self, lang: str, query: str) -> list[dict]:
        """按名稱搜尋卡牌（精簡列表，不含 attacks 等細節）。
        回傳 list[dict]，每個 item 含 id, localId, name, image。
        """
        url = f"{API_BASE}/{lang}/cards?name={quote(query)}"
        result = self._cached_get(url)
        if isinstance(result, list):
            return result
        return []

    def search_cards_full(self, lang: str, query: str) -> list[dict]:
        """按名稱搜尋並回傳完整卡牌資料。
        先調 search_cards 取得 ID 列表，再逐張 get_card。
        """
        summaries = self.search_cards(lang, query)
        cards = []
        for s in summaries[:20]:  # 限制 20 張避免過載
            card = self.get_card(lang, s["id"])
            if card:
                cards.append(card)
        return cards

    # ── Set ──

    def get_set(self, lang: str, set_id: str) -> Optional[dict]:
        """取得系列資訊（含 cards 列表）。"""
        url = f"{API_BASE}/{lang}/sets/{set_id}"
        return self._cached_get(url)

    def list_sets(self, lang: str) -> list[dict]:
        """列出某語言所有系列（摘要）。"""
        url = f"{API_BASE}/{lang}/sets"
        result = self._cached_get(url)
        if isinstance(result, list):
            return result
        return []

    # ── Cross-language ──

    def get_cross_lang_card(self, card_id: str, source_lang: str,
                             target_lang: str) -> Optional[dict]:
        """用相同 card_id 在不同語言間查詢（SV 系列工作良好）。
        source_lang / target_lang: 'ja', 'zh-tw', 'en', ...
        """
        return self.get_card(target_lang, card_id)

    # ── 工具 ──

    def card_id_exists(self, lang: str, card_id: str) -> bool:
        """快速檢查 card_id 是否存在（只發 HEAD 或輕量請求）。"""
        return self.get_card(lang, card_id) is not None

    def clear_cache(self):
        """清除快取。"""
        self._cache.clear()


# 全域單例（Django/Flask 中通常模組層級單例是安全的）
_tcgdex_instance: Optional[TCGDexClient] = None


def get_client() -> TCGDexClient:
    global _tcgdex_instance
    if _tcgdex_instance is None:
        _tcgdex_instance = TCGDexClient()
    return _tcgdex_instance
