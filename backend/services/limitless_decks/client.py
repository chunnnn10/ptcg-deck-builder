from __future__ import annotations

import random
import threading
import time
from urllib.parse import urljoin

import requests


BASE_URL = "https://limitlesstcg.com"


class LimitlessFetchError(RuntimeError):
    def __init__(self, url: str, message: str):
        super().__init__(f"{url}: {message}")
        self.url = url
        self.message = message


class LimitlessClient:
    def __init__(self, min_delay: float = 0.3, max_delay: float = 1.0):
        self.base_url = BASE_URL
        self.min_delay = min_delay
        self.max_delay = max(min_delay, max_delay)
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": (
                "PTCG-2.0 Limitless DeckList/1.0 "
                "(https://limitlesstcg.com; respectful crawler)"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        self._lock = threading.Lock()
        self._last_request_at = 0.0

    def absolute_url(self, url_or_path: str) -> str:
        return urljoin(self.base_url, url_or_path)

    def get_text(self, url_or_path: str, params: dict | None = None) -> str:
        url = self.absolute_url(url_or_path)
        self._throttle()
        try:
            response = self.session.get(url, params=params, timeout=25)
        except requests.RequestException as exc:
            raise LimitlessFetchError(url, str(exc)) from exc
        if response.status_code != 200:
            raise LimitlessFetchError(url, f"HTTP {response.status_code}")
        response.encoding = "utf-8"
        return response.text

    def _throttle(self) -> None:
        with self._lock:
            elapsed = time.time() - self._last_request_at
            target = random.uniform(self.min_delay, self.max_delay)
            if elapsed < target:
                time.sleep(target - elapsed)
            self._last_request_at = time.time()
