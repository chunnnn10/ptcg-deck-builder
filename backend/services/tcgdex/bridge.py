"""
TCGDex 跨語言卡牌橋接
利用 SV 系列（2023+）日文和繁中共享相同 set_id 的特性，
用同一 card_id 在不同語言間查詢。

核心方法:
    bridge.find_chinese_card(jp_set_code, jp_set_number)
        → 從日文 set_code + set_number 找出繁中卡片資料
    bridge.find_japanese_card(tw_set_code, tw_set_number)
        → 反向：從繁中找出日文卡片資料
    bridge.search_and_map(query, source_lang, target_lang)
        → 用名稱搜尋並取回目標語言的完整資料
"""
import logging
from typing import Optional

from services.tcgdex.client import get_client, TCGDexClient

logger = logging.getLogger(__name__)


class TCGDexBridge:
    """中日文卡牌跨語言查詢。"""

    def __init__(self, client: Optional[TCGDexClient] = None):
        self._client = client or get_client()

    # ── 核心對照 ──

    def find_chinese_card(self, jp_set_code: str, jp_set_number: str) -> Optional[dict]:
        """用日文的 set_code + set_number 查對應的繁中卡。
        set_number 支援 "040" 或 "040/081" 兩種格式。
        """
        if not jp_set_code or not jp_set_number:
            return None

        # 處理 set_number：去掉 "/081" 後綴，TCGDex localId 通常是純數字
        local_id = self._normalize_set_number(jp_set_number)
        card_id = f"{jp_set_code}-{local_id}"

        # 同 ID 查繁中
        card = self._client.get_card("zh-tw", card_id)
        if card:
            logger.debug(f"TCGDex bridge: {card_id} → zh-tw: {card['name']}")
        return card

    def find_japanese_card(self, tw_set_code: str, tw_set_number: str) -> Optional[dict]:
        """從繁中的 set_code + set_number 查對應的日文卡。"""
        if not tw_set_code or not tw_set_number:
            return None

        local_id = self._normalize_set_number(tw_set_number)
        card_id = f"{tw_set_code}-{local_id}"

        card = self._client.get_card("ja", card_id)
        if card:
            logger.debug(f"TCGDex bridge: {card_id} → ja: {card['name']}")
        return card

    def find_card_both_langs(self, set_code: str, set_number: str) -> dict:
        """查同一張卡的日文和繁中版本。回傳 {'ja': ..., 'zh-tw': ...}。"""
        result = {"ja": None, "zh-tw": None}
        local_id = self._normalize_set_number(set_number)
        card_id = f"{set_code}-{local_id}"

        result["ja"] = self._client.get_card("ja", card_id)
        result["zh-tw"] = self._client.get_card("zh-tw", card_id)
        return result

    # ── 名稱搜尋對照 ──

    def search_and_map(self, query: str, source_lang: str, target_lang: str,
                       limit: int = 10) -> list[dict]:
        """在 source_lang 中搜尋卡名，對每張結果取得 target_lang 的完整資料。
        source_lang / target_lang: 'ja', 'zh-tw', 'en', ...
        回傳: [{source: {...}, target: {...}}, ...]
        """
        # 先取來源語言精簡列表
        summaries = self._client.search_cards(source_lang, query)
        results = []
        for s in summaries[:limit]:
            card_id = s["id"]
            target = self._client.get_card(target_lang, card_id)
            if target:
                source_card = self._client.get_card(source_lang, card_id)
                results.append({
                    "card_id": card_id,
                    "source": source_card,
                    "target": target,
                })
        return results

    def search_and_get_target_only(self, query: str, source_lang: str,
                                    target_lang: str, limit: int = 10) -> list[dict]:
        """簡化版：只回傳目標語言的完整卡牌資料列表。"""
        summaries = self._client.search_cards(source_lang, query)
        cards = []
        for s in summaries[:limit]:
            card = self._client.get_card(target_lang, s["id"])
            if card:
                cards.append(card)
        return cards

    # ── 卡片存在性檢查 ──

    def exists_in_lang(self, set_code: str, set_number: str, lang: str) -> bool:
        """檢查某張卡在指定語言中是否存在。"""
        local_id = self._normalize_set_number(set_number)
        card_id = f"{set_code}-{local_id}"
        return self._client.card_id_exists(lang, card_id)

    # ── 輔助 ──

    @staticmethod
    def _normalize_set_number(set_number: str) -> str:
        """將 "040/081" 或 "040" → "40"（整數化，去掉前導零）。
        TCGDex 的 localId 同時支援 "040"、"40"、"040/081" 等格式，
        但我們統一轉成去掉前導零的格式以確保匹配。
        """
        if not set_number:
            return ""
        # 取 "/" 前的部分
        num = set_number.split("/")[0].strip()
        # 嘗試轉整數去掉前導零
        try:
            return str(int(num))
        except ValueError:
            return num


# 全域單例
_bridge_instance: Optional[TCGDexBridge] = None


def get_bridge() -> TCGDexBridge:
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = TCGDexBridge()
    return _bridge_instance


# ── 便捷函數（直接 import 使用） ──

def find_chinese_card(jp_set_code: str, jp_set_number: str) -> Optional[dict]:
    return get_bridge().find_chinese_card(jp_set_code, jp_set_number)


def find_japanese_card(tw_set_code: str, tw_set_number: str) -> Optional[dict]:
    return get_bridge().find_japanese_card(tw_set_code, tw_set_number)
