from __future__ import annotations

import re
from datetime import datetime
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


BASE_URL = "https://limitlesstcg.com"


def absolute_url(href: str | None) -> str:
    return urljoin(BASE_URL, href or "")


def tournament_id_from_url(href: str | None) -> str | None:
    path = urlparse(absolute_url(href)).path
    match = re.search(r"/tournaments/jp/(\d+)", path)
    if match:
        return f"jp-{match.group(1)}"
    match = re.search(r"/tournaments/(\d+)", path)
    if match:
        return match.group(1)
    return None


def deck_id_from_url(href: str | None) -> str | None:
    path = urlparse(absolute_url(href)).path
    match = re.search(r"/decks/list/jp/(\d+)", path)
    if match:
        return f"jp-{match.group(1)}"
    match = re.search(r"/decks/list/(\d+)", path)
    if match:
        return match.group(1)
    return None


def tournament_url_from_id(tournament_id: str) -> str:
    tid = str(tournament_id)
    if tid.startswith("jp-"):
        return absolute_url(f"/tournaments/jp/{tid[3:]}")
    return absolute_url(f"/tournaments/{tid}")


def deck_url_from_id(deck_id: str) -> str:
    did = str(deck_id)
    if did.startswith("jp-"):
        return absolute_url(f"/decks/list/jp/{did[3:]}")
    return absolute_url(f"/decks/list/{did}")


def _int_or_none(value) -> int | None:
    match = re.search(r"\d+", str(value or ""))
    return int(match.group(0)) if match else None


def _clean_text(value: str | None) -> str:
    return " ".join(str(value or "").split())


def _parse_date(value: str | None) -> str | None:
    value = _clean_text(value)
    if not value:
        return None
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    value = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", value)
    for fmt in ("%d %B %Y", "%d %b %y", "%d %b %Y"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def _parse_infobox(soup: BeautifulSoup) -> dict:
    title_el = soup.select_one(".infobox-heading")
    line_el = soup.select_one(".infobox-line")
    flag_el = title_el.select_one("img.flag[alt]") if title_el else None
    line = _clean_text(line_el.get_text(" ", strip=True) if line_el else "")
    parts = [p.strip() for p in re.split(r"\s*(?:\u2022|\u2027|\||-)\s*", line) if p.strip()]
    players = None
    date_value = None
    location = flag_el.get("alt") if flag_el else None
    for part in parts:
        if "Player" in part:
            players = _int_or_none(part)
        elif not date_value:
            date_value = _parse_date(part)
        elif not location and not re.search(r"\b[A-Z0-9]{2,5}\s*-\s*[A-Z0-9]{2,5}\b", part):
            location = part
    return {
        "title": _clean_text(title_el.get_text(" ", strip=True) if title_el else ""),
        "date": date_value,
        "location": location,
        "players": players,
    }


def parse_global_tournament_index(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tournaments = []
    for row in soup.select("tr[data-date][data-name]"):
        link = row.select_one('a[href^="/tournaments/"]')
        tournament_id = tournament_id_from_url(link.get("href") if link else None)
        if not tournament_id:
            continue
        tournaments.append({
            "tournament_id": tournament_id,
            "source_region": "global",
            "title": row.get("data-name") or _clean_text(link.get_text(" ", strip=True) if link else ""),
            "date": _parse_date(row.get("data-date")),
            "location": row.get("data-country") or "",
            "format": row.get("data-format") or "",
            "players": _int_or_none(row.get("data-players")),
            "url": absolute_url(link.get("href")),
        })
    return tournaments


def parse_jp_tournament_index(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    tournaments = []
    for row in soup.select("table.data-table tr[data-date][data-shop]"):
        link = row.select_one('a[href*="/tournaments/jp/"]')
        tournament_id = tournament_id_from_url(link.get("href") if link else None)
        if not tournament_id:
            continue
        city = row.get("data-city") or ""
        shop = row.get("data-shop") or ""
        title = f"City League {city}".strip()
        tournaments.append({
            "tournament_id": tournament_id,
            "source_region": "jp",
            "title": title,
            "date": _parse_date(row.get("data-date")),
            "location": shop,
            "format": "standard-jp",
            "players": None,
            "url": absolute_url(link.get("href")),
        })
    return tournaments


def parse_tournament_detail(html: str, url: str, source_region: str | None = None) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    tournament_id = tournament_id_from_url(url)
    source_region = source_region or ("jp" if str(tournament_id or "").startswith("jp-") else "global")
    info = _parse_infobox(soup)
    decks = []

    for row in soup.select("tr[data-rank]"):
        link = row.select_one('a[href^="/decks/list"], a[href*="/decks/list"]')
        deck_id = deck_id_from_url(link.get("href") if link else None)
        if not deck_id:
            continue
        tags = [img.get("alt") for img in row.select("img.pokemon") if img.get("alt")]
        decks.append({
            "deck_id": deck_id,
            "tournament_id": tournament_id,
            "player_name": row.get("data-name") or "",
            "placement": _int_or_none(row.get("data-rank")),
            "archetype": row.get("data-deck") or " ".join(tags),
            "title": row.get("data-deck") or "",
            "tags": tags,
            "deck_url": absolute_url(link.get("href")),
            "source_region": source_region,
        })

    if not decks:
        for row in soup.select("table.data-table tr"):
            cells = row.select("td")
            if len(cells) < 4:
                continue
            link = row.select_one('a[href*="/decks/list"]')
            deck_id = deck_id_from_url(link.get("href") if link else None)
            if not deck_id:
                continue
            tags = [img.get("alt") for img in row.select("img.pokemon") if img.get("alt")]
            player_link = cells[1].select_one("a")
            decks.append({
                "deck_id": deck_id,
                "tournament_id": tournament_id,
                "player_name": _clean_text(player_link.get_text(" ", strip=True) if player_link else cells[1].get_text(" ", strip=True)),
                "placement": _int_or_none(cells[0].get_text(" ", strip=True)),
                "archetype": " ".join(tags),
                "title": " ".join(tags),
                "tags": tags,
                "deck_url": absolute_url(link.get("href")),
                "source_region": source_region,
            })

    return {
        "tournament": {
            "tournament_id": tournament_id,
            "source_region": source_region,
            "title": info["title"],
            "date": info["date"],
            "location": info["location"],
            "format": None,
            "players": info["players"],
            "url": absolute_url(url),
        },
        "decks": decks,
    }


def _section_from_heading(text: str | None) -> str:
    value = _clean_text(text).split("(", 1)[0].strip().lower()
    if value.startswith("pok"):
        return "pokemon"
    if value.startswith("trainer"):
        return "trainer"
    if value.startswith("energy"):
        return "energy"
    return "unknown"


def _strip_price_from_title(text: str) -> str:
    text = re.sub(r"\s+\d[\d,]*(?:\.\d+)?(?:\$|\u20ac|\u00a3|\u00a5).*$", "", text or "")
    return _clean_text(text)


def _image_map_from_visual_cards(soup: BeautifulSoup) -> dict[tuple[str, str], str]:
    images = {}
    for img in soup.select("img.card-picture.card, img.card.card-picture"):
        parent_link = img.find_parent("a")
        href = parent_link.get("href") if parent_link else ""
        match = re.search(r"/cards/(?:jp|en)/([^/]+)/([^/?#]+)", href or "")
        if not match:
            continue
        src = img.get("data-src") or img.get("src") or ""
        if src:
            images[(match.group(1), match.group(2))] = absolute_url(src)
    return images


def parse_decklist(html: str, language: str, mode: str) -> dict:
    soup = BeautifulSoup(html, "html.parser")
    title_el = soup.select_one(".decklist-title")
    title = _strip_price_from_title(title_el.get_text(" ", strip=True) if title_el else "")
    image_map = _image_map_from_visual_cards(soup)
    cards = []
    line_order = 0

    for column in soup.select(".decklist-column"):
        heading = column.select_one(".decklist-column-heading")
        section = _section_from_heading(heading.get_text(" ", strip=True) if heading else "")
        for link in column.select("a.card-link"):
            count_el = link.select_one(".card-count")
            name_el = link.select_one(".card-name")
            href = link.get("href") or ""
            match = re.search(r"/cards/(?:jp|en)/([^/]+)/([^/?#]+)", href)
            set_img = link.select_one("img.set")
            set_code = match.group(1) if match else (set_img.get("alt") if set_img else "")
            set_number = match.group(2) if match else ""
            line_order += 1
            cards.append({
                "language": language,
                "mode": mode,
                "section": section,
                "line_order": line_order,
                "count": _int_or_none(count_el.get_text(strip=True) if count_el else "") or 0,
                "card_name": _clean_text(name_el.get_text(" ", strip=True) if name_el else ""),
                "set_code": set_code,
                "set_number": set_number,
                "limitless_card_url": absolute_url(href) if href else "",
                "limitless_image_url": image_map.get((set_code, set_number), ""),
            })

    return {
        "title": title,
        "language": language,
        "mode": mode,
        "cards": cards,
        "raw_text": build_raw_text(cards),
    }


def build_raw_text(cards: list[dict]) -> str:
    labels = [("pokemon", "Pokemon"), ("trainer", "Trainer"), ("energy", "Energy")]
    lines = []
    for section, label in labels:
        section_cards = [c for c in cards if c.get("section") == section]
        if not section_cards:
            continue
        lines.append(f"{label}: {sum(int(c.get('count') or 0) for c in section_cards)}")
        for card in section_cards:
            lines.append(
                f"{card.get('count')} {card.get('card_name')} "
                f"{card.get('set_code')} {card.get('set_number')}".strip()
            )
    return "\n".join(lines)
