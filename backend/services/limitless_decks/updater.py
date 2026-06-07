from __future__ import annotations

import threading
import time
import traceback
from collections.abc import Callable

import database

from .client import LimitlessClient
from . import parser
from .repository import (
    create_mappings_for_deck,
    deck_needs_fetch,
    ensure_schema,
    log_event,
    recent_logs,
    save_decklist,
    upsert_deck_metadata,
    upsert_tournament_deck_entry,
    upsert_tournament,
)


class UpdateState:
    def __init__(self):
        self.running = False
        self.mode = ""
        self.message = "idle"
        self.tournaments_found = 0
        self.tournaments_done = 0
        self.decks_found = 0
        self.decks_fetched = 0
        self.decks_skipped = 0
        self.decks_failed = 0
        self.progress = 0
        self.started_at = None
        self.finished_at = None
        self._lock = threading.Lock()

    def reset(self, mode: str):
        with self._lock:
            self.running = True
            self.mode = mode
            self.message = "Starting"
            self.tournaments_found = 0
            self.tournaments_done = 0
            self.decks_found = 0
            self.decks_fetched = 0
            self.decks_skipped = 0
            self.decks_failed = 0
            self.progress = 0
            self.started_at = time.time()
            self.finished_at = None

    def update(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, value)
            if self.decks_found:
                done = self.decks_fetched + self.decks_skipped + self.decks_failed
                self.progress = min(100, round(done / self.decks_found * 100, 1))

    def increment(self, **kwargs):
        with self._lock:
            for key, value in kwargs.items():
                if hasattr(self, key):
                    setattr(self, key, getattr(self, key) + value)
            if self.decks_found:
                done = self.decks_fetched + self.decks_skipped + self.decks_failed
                self.progress = min(100, round(done / self.decks_found * 100, 1))

    def finish(self, message: str = "Finished"):
        with self._lock:
            self.running = False
            self.progress = 100 if self.decks_found else self.progress
            self.message = message
            self.finished_at = time.time()

    def to_dict(self):
        with self._lock:
            elapsed = int(time.time() - self.started_at) if self.started_at else 0
            return {
                "running": self.running,
                "mode": self.mode,
                "message": self.message,
                "tournaments_found": self.tournaments_found,
                "tournaments_done": self.tournaments_done,
                "decks_found": self.decks_found,
                "decks_fetched": self.decks_fetched,
                "decks_skipped": self.decks_skipped,
                "decks_failed": self.decks_failed,
                "progress": self.progress,
                "elapsed": f"{elapsed // 60}m {elapsed % 60}s",
                "logs": recent_logs(20),
            }


update_state = UpdateState()
_runner_lock = threading.Lock()
_INDEX_PAGE_SIZE = 500
_MAX_INDEX_PAGES_PER_REGION = 100


def _positive_int(value, default: int | None = None) -> int | None:
    if value in (None, ""):
        return default
    try:
        parsed = int(value)
        return parsed if parsed > 0 else default
    except (TypeError, ValueError):
        return default


def _fetch_index_pages(
    client: LimitlessClient,
    path: str,
    parse_func: Callable[[str], list[dict]],
    max_pages: int = _MAX_INDEX_PAGES_PER_REGION,
) -> list[dict]:
    tournaments = []
    seen_ids = set()
    for page in range(1, max_pages + 1):
        params = {"show": "all"}
        if page > 1:
            params["page"] = page
        html = client.get_text(path, params=params)
        page_tournaments = parse_func(html)
        new_count = 0
        for tournament in page_tournaments:
            tournament_id = tournament.get("tournament_id")
            if not tournament_id or tournament_id in seen_ids:
                continue
            seen_ids.add(tournament_id)
            tournaments.append(tournament)
            new_count += 1
        if not page_tournaments or new_count == 0 or len(page_tournaments) < _INDEX_PAGE_SIZE:
            break
    else:
        log_event(
            "warning",
            path,
            f"Stopped Limitless index scan at {max_pages} pages",
            "Increase _MAX_INDEX_PAGES_PER_REGION if Limitless adds more pages.",
        )
    return tournaments


def _limit_region_tournaments(tournaments: list[dict], max_tournaments: int | None) -> list[dict]:
    if not max_tournaments:
        return tournaments
    return tournaments[:max_tournaments]


def _fetch_indexes(
    client: LimitlessClient,
    regions: list[str],
    max_pages_per_region: int | None = None,
    max_tournaments_per_region: int | None = None,
) -> list[dict]:
    tournaments = []
    max_pages = _positive_int(max_pages_per_region, _MAX_INDEX_PAGES_PER_REGION)
    max_tournaments = _positive_int(max_tournaments_per_region)
    if "global" in regions:
        region_tournaments = _fetch_index_pages(
            client,
            "/tournaments",
            parser.parse_global_tournament_index,
            max_pages=max_pages,
        )
        tournaments.extend(_limit_region_tournaments(region_tournaments, max_tournaments))
    if "jp" in regions:
        region_tournaments = _fetch_index_pages(
            client,
            "/tournaments/jp",
            parser.parse_jp_tournament_index,
            max_pages=max_pages,
        )
        tournaments.extend(_limit_region_tournaments(region_tournaments, max_tournaments))
    return tournaments


def refresh_indexes(regions: list[str] | None = None, max_tournaments: int | None = None,
                    client: LimitlessClient | None = None,
                    max_pages_per_region: int | None = None,
                    max_tournaments_per_region: int | None = None) -> dict:
    ensure_schema()
    client = client or LimitlessClient()
    regions = regions or ["global", "jp"]
    tournaments = _fetch_indexes(
        client,
        regions,
        max_pages_per_region=max_pages_per_region,
        max_tournaments_per_region=max_tournaments_per_region,
    )
    if max_tournaments:
        tournaments = tournaments[:int(max_tournaments)]
    conn = database.get_db_connection()
    if not conn:
        raise RuntimeError("Database unavailable")
    try:
        cursor = conn.cursor()
        for tournament in tournaments:
            upsert_tournament(cursor, tournament)
        conn.commit()
        return {"tournaments": len(tournaments)}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def update_tournament(tournament_id: str, include_bling: bool = False, stale_hours: int = 24,
                      max_decks: int | None = None, client: LimitlessClient | None = None) -> dict:
    ensure_schema()
    client = client or LimitlessClient()
    stale_hours = int(stale_hours or 0)
    max_decks = int(max_decks) if max_decks else None
    url = parser.tournament_url_from_id(tournament_id)
    html = client.get_text(url)
    parsed = parser.parse_tournament_detail(html, url)
    tournament = parsed["tournament"]
    decks = parsed["decks"]
    if tournament.get("tournament_id") and tournament["tournament_id"] != tournament_id:
        tournament_id = tournament["tournament_id"]

    conn = database.get_db_connection()
    if not conn:
        raise RuntimeError("Database unavailable")
    try:
        cursor = conn.cursor()
        upsert_tournament(cursor, tournament, raw_html=html)
        for entry_order, deck in enumerate(decks, start=1):
            upsert_deck_metadata(cursor, deck)
            upsert_tournament_deck_entry(cursor, deck, entry_order)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    unique_decks = {}
    for deck in decks:
        unique_decks.setdefault(deck["deck_id"], deck)
    decks_to_fetch = list(unique_decks.values())

    fetched = skipped = failed = 0
    for deck in decks_to_fetch[:max_decks or len(decks_to_fetch)]:
        conn = database.get_db_connection()
        try:
            cursor = conn.cursor()
            if not deck_needs_fetch(cursor, deck["deck_id"], stale_hours=stale_hours):
                skipped += 1
                continue
        finally:
            if conn:
                conn.close()
        try:
            update_deck(deck["deck_id"], include_bling=include_bling, client=client)
            fetched += 1
        except Exception as exc:
            failed += 1
            log_event("error", deck["deck_id"], "Deck update failed", traceback.format_exc())
            update_state.update(message=f"Failed {deck['deck_id']}: {exc}")
    return {
        "tournament": tournament,
        "entries": len(decks),
        "decks": len(decks_to_fetch),
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
    }


def update_deck(deck_id: str, include_bling: bool = False, client: LimitlessClient | None = None) -> dict:
    ensure_schema()
    client = client or LimitlessClient()
    base_url = parser.deck_url_from_id(deck_id)
    modes = [("normal", None)]
    if include_bling:
        modes.append(("bling", "bling"))

    conn = database.get_db_connection()
    if not conn:
        raise RuntimeError("Database unavailable")
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO limitless_decks (deck_id, deck_url, source_region)
            VALUES (%s, %s, %s)
            ON CONFLICT (deck_id) DO UPDATE SET
                deck_url = COALESCE(limitless_decks.deck_url, EXCLUDED.deck_url),
                source_region = COALESCE(limitless_decks.source_region, EXCLUDED.source_region)
            """,
            (deck_id, base_url, "jp" if str(deck_id).startswith("jp-") else "global"),
        )
        conn.commit()

        parsed_count = 0
        for mode, mode_param in modes:
            for language in ("jp", "en"):
                params = {"lang": language}
                if mode_param:
                    params["mode"] = mode_param
                html = client.get_text(base_url, params=params)
                parsed = parser.parse_decklist(html, language=language, mode=mode)
                if not parsed.get("cards"):
                    raise RuntimeError(f"No cards parsed for {deck_id} {language} {mode}")
                save_decklist(cursor, deck_id, parsed)
                parsed_count += len(parsed.get("cards", []))
            create_mappings_for_deck(cursor, deck_id, mode)
        conn.commit()
        return {"deck_id": deck_id, "cards": parsed_count}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def _run_update(options: dict):
    client = LimitlessClient()
    include_bling = bool(options.get("include_bling", False))
    regions = options.get("regions") or ["global", "jp"]
    stale_hours = int(options.get("stale_hours") or 24)
    max_tournaments = options.get("max_tournaments")
    max_index_pages_per_region = options.get("max_index_pages_per_region")
    max_tournaments_per_region = options.get("max_tournaments_per_region")
    max_decks = options.get("max_decks")
    max_tournaments = _positive_int(max_tournaments)
    max_index_pages_per_region = _positive_int(max_index_pages_per_region)
    max_tournaments_per_region = _positive_int(max_tournaments_per_region)
    max_decks = _positive_int(max_decks)

    try:
        update_state.update(message="Fetching Limitless tournament indexes")
        tournaments = _fetch_indexes(
            client,
            regions,
            max_pages_per_region=max_index_pages_per_region,
            max_tournaments_per_region=max_tournaments_per_region,
        )
        if max_tournaments:
            tournaments = tournaments[:max_tournaments]
        update_state.update(
            tournaments_found=len(tournaments),
            message=f"Found {len(tournaments)} tournaments; fetching details and decklists",
        )

        conn = database.get_db_connection()
        if not conn:
            raise RuntimeError("Database unavailable")
        try:
            cursor = conn.cursor()
            for tournament in tournaments:
                upsert_tournament(cursor, tournament)
            conn.commit()
        finally:
            conn.close()

        for tournament in tournaments:
            update_state.update(message=f"Updating {tournament['title']}")
            try:
                result = update_tournament(
                    tournament["tournament_id"],
                    include_bling=include_bling,
                    stale_hours=stale_hours,
                    max_decks=max_decks,
                    client=client,
                )
                update_state.increment(
                    tournaments_done=1,
                    decks_found=result["decks"],
                    decks_fetched=result["fetched"],
                    decks_skipped=result["skipped"],
                    decks_failed=result["failed"],
                )
            except Exception:
                log_event("error", tournament["tournament_id"], "Tournament update failed", traceback.format_exc())
                update_state.increment(tournaments_done=1, decks_failed=1)
        update_state.finish("Limitless update finished")
    except Exception:
        log_event("error", "limitless-update", "Update failed", traceback.format_exc())
        update_state.finish("Limitless update failed")


def start_update(options: dict | None = None) -> tuple[bool, str]:
    options = options or {}
    with _runner_lock:
        if update_state.running:
            return False, "Limitless update is already running"
        ensure_schema()
        update_state.reset(options.get("mode") or "daily")
        thread = threading.Thread(target=_run_update, args=(options,), daemon=True)
        thread.start()
    return True, "Limitless update started"


def get_status() -> dict:
    return update_state.to_dict()
