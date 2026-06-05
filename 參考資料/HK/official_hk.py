"""
PTCG Hong Kong Official Site Crawler
Source: https://asia.pokemon-card.com/hk/card-search/
"""
import re
import os
import json
import time
import hashlib
import logging
from typing import Optional
from urllib.parse import urljoin
from dataclasses import dataclass, field

import requests
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

BASE_URL = "https://asia.pokemon-card.com"
HK_LIST_URL = "https://asia.pokemon-card.com/hk/card-search/list/"

ENERGY_MAP = {
    "Grass": "Grass", "Fire": "Fire", "Water": "Water",
    "Lightning": "Lightning", "Psychic": "Psychic", "Fighting": "Fighting",
    "Darkness": "Darkness", "Metal": "Metal", "Fairy": "Fairy",
    "Dragon": "Dragon", "Colorless": "Colorless",
}

RARITY_CODES = [
    (1, "C"), (3, "R"), (4, "RR"), (5, "RRR"),
    (6, "PR"), (7, "TR"), (8, "SR"), (9, "HR"),
    (10, "UR"), (12, "K"), (13, "A"), (14, "AR"),
    (15, "SAR"), (16, "S"), (17, "SSR"), (18, "ACE"),
    (19, "BWR"), (20, "MUR"), (21, "MA"), (2, "U"), (11, "-"),
]


@dataclass
class ExpansionInfo:
    code: str
    name: str
    series: str = ""


@dataclass
class CardListEntry:
    detail_url: str
    detail_id: int
    image_url: str


@dataclass
class CardDetail:
    detail_id: int
    collector_number: str = ""
    collector_total: str = ""
    name: str = ""
    card_type: str = "Pokémon"
    sub_type: str = ""
    super_type: str = ""
    hp: str = ""
    element_type: str = ""
    skills: list[dict] = field(default_factory=list)
    weakness_type: str = ""
    weakness_value: str = ""
    resistance_type: str = ""
    resistance_value: str = ""
    retreat_cost: int | None = None
    regulation_mark: str = ""
    expansion_code: str = ""
    expansion_name: str = ""
    artist: str = ""
    pokedex_number: str = ""
    pokedex_category: str = ""
    height: str = ""
    weight: str = ""
    flavor_text: str = ""
    evolves_from: str = ""
    evolves_to: list[str] = field(default_factory=list)
    image_url: str = ""
    rarity: str = ""
    rarity_value: int | None = None


class OfficialHKCrawler:
    """Crawler for the official PTCG Hong Kong card database."""

    def __init__(self, image_dir: str = "images", timeout: int = 30):
        self.image_dir = image_dir
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                          "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36",
            "Accept-Language": "zh-HK,zh;q=0.9,en;q=0.8",
        })
        self.request_delay = 0.5  # seconds between requests

    # ─── HTTP Helpers ───────────────────────────────────────────

    def _fetch(self, url: str, max_retries: int = 3) -> BeautifulSoup | None:
        for attempt in range(max_retries):
            try:
                logger.debug(f"Fetching {url} (attempt {attempt + 1})")
                resp = self.session.get(url, timeout=self.timeout)
                resp.raise_for_status()
                resp.encoding = "utf-8"
                return BeautifulSoup(resp.text, "html.parser")
            except requests.RequestException as e:
                logger.warning(f"Request failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2 * (attempt + 1))
        logger.error(f"Failed to fetch {url} after {max_retries} attempts")
        return None

    def _download_image(self, url: str, save_path: str) -> tuple[bool, str | None]:
        """Download image, return (success, checksum)."""
        os.makedirs(os.path.dirname(save_path), exist_ok=True)
        try:
            resp = self.session.get(url, timeout=60)
            resp.raise_for_status()
            checksum = hashlib.sha256(resp.content).hexdigest()
            with open(save_path, "wb") as f:
                f.write(resp.content)
            return True, checksum
        except Exception as e:
            logger.warning(f"Image download failed: {url} -> {e}")
            return False, None

    def _extract_energy(self, img_src: str) -> str:
        """Extract energy type from image filename like .../energy/Grass.png."""
        for energy in ENERGY_MAP:
            if f"/{energy}." in img_src or f"/{energy.lower()}." in img_src:
                return energy
        return ""

    def _text(self, el: Tag | None, strip: bool = True) -> str:
        if el is None:
            return ""
        return el.get_text(strip=strip)

    # ─── Expansion List ─────────────────────────────────────────

    def get_expansion_list(self) -> list[ExpansionInfo]:
        """
        Parse the product selector modal to get all expansion codes + names + series.
        The modal is embedded in the page as #productSelectorModal.
        """
        soup = self._fetch(HK_LIST_URL)
        if not soup:
            return []

        expansions: list[ExpansionInfo] = []
        current_series = ""

        # Target the #productSelectorModal specifically
        modal = soup.select_one("#productSelectorModal")
        if not modal:
            logger.warning("#productSelectorModal not found in page")
            return []

        # Look for condition rows inside the modal window
        for row in modal.select(".conditionRow"):
            # Series label (in the same row as expansion codes)
            series_label = row.select_one(".conditionLabel")
            if series_label:
                series_text = series_label.get_text(strip=True)
                toggle = series_label.select_one(".toggleAccordion")
                if toggle:
                    series_text = series_text.replace(toggle.get_text(strip=True), "").strip()
                if series_text and len(series_text) < 20:
                    current_series = series_text

            # Expansion code checkboxes (same row, don't skip)
            for opt in row.select(".options"):
                inp = opt.select_one("input.expansionCode")
                label = opt.select_one("label")
                if inp and label:
                    code = inp.get("value", "").strip()
                    name = label.get_text(strip=True)
                    if code and name:
                        expansions.append(ExpansionInfo(code=code, name=name, series=current_series))

        logger.info(f"Found {len(expansions)} expansions from #productSelectorModal")
        return expansions

    def get_expansion_list_from_html(self, html_content: str) -> list[ExpansionInfo]:
        """Parse expansion list from a raw HTML string (for use with pre-fetched modal HTML)."""
        soup = BeautifulSoup(html_content, "html.parser")
        expansions: list[ExpansionInfo] = []
        current_series = ""

        for row in soup.select(".conditionRow"):
            series_label = row.select_one(".conditionLabel")
            if series_label:
                toggle = series_label.select_one(".toggleAccordion")
                raw = self._text(series_label)
                if toggle:
                    raw = raw.replace(self._text(toggle), "").strip()
                if raw and len(raw) < 15 and any(kw in raw for kw in ["＆", "&", "進化", "太陽", "月亮"]):
                    current_series = raw
                    continue

            for opt in row.select(".options"):
                inp = opt.select_one("input.expansionCode")
                label = opt.select_one("label")
                if inp and label:
                    code = inp.get("value", "").strip()
                    name = self._text(label)
                    if code and name:
                        expansions.append(ExpansionInfo(code=code, name=name, series=current_series))

        return expansions

    # ─── Card List ──────────────────────────────────────────────

    def crawl_card_list(self, expansion_code: str, page: int = 1) -> tuple[list[CardListEntry], int]:
        """
        Crawl one page of the card list for a specific expansion.
        Returns (card_list, total_pages).
        """
        url = f"{HK_LIST_URL}?expansionCodes={expansion_code}&pageNo={page}"
        soup = self._fetch(url)
        if not soup:
            return [], 0

        # Total pages
        total_pages = 1
        pages_tag = soup.select_one(".resultTotalPages")
        if pages_tag:
            match = re.search(r"(\d+)", self._text(pages_tag))
            if match:
                total_pages = int(match.group(1))

        # Card entries
        entries: list[CardListEntry] = []
        for li in soup.select("li.card"):
            link = li.select_one("a[href]")
            img = li.select_one("img[data-original], img[src]")
            if not link:
                continue

            href = link.get("href", "")
            # Extract detail ID from URL like /hk/card-search/detail/1/
            detail_id = 0
            match = re.search(r"/detail/(\d+)/?", href)
            if match:
                detail_id = int(match.group(1))

            image_url = ""
            if img:
                image_url = img.get("data-original") or img.get("src") or ""

            entries.append(CardListEntry(
                detail_url=urljoin(BASE_URL, href),
                detail_id=detail_id,
                image_url=image_url,
            ))

        time.sleep(self.request_delay)
        return entries, total_pages

    # ─── Card Detail ────────────────────────────────────────────

    def crawl_card_detail(self, detail_url: str, detail_id: int, image_url: str = "") -> CardDetail | None:
        """Parse a single card detail page."""
        soup = self._fetch(detail_url)
        if not soup:
            return None

        card = CardDetail(detail_id=detail_id, image_url=image_url)
        card.source_url = detail_url  # type: ignore

        # ── Name & Stage ──
        header = soup.select_one("h1.pageHeader.cardDetail")
        if header:
            stage_el = header.select_one("span.evolveMarker")
            if stage_el:
                card.sub_type = self._text(stage_el)
            # Remove stage text from name
            full_name = self._text(header)
            for span in header.select("span"):
                full_name = full_name.replace(self._text(span), "").strip()
            card.name = full_name

        # ── HP & Element Type ──
        main_info = soup.select_one(".mainInfomation")
        if main_info:
            hp_num = main_info.select_one(".number")
            if hp_num:
                card.hp = self._text(hp_num)
            energy_img = main_info.select_one("img[src]")
            if energy_img:
                card.element_type = self._extract_energy(energy_img.get("src", ""))

        # Card type inference
        if card.element_type and card.sub_type:
            card.card_type = "Pokémon"
        elif soup.select_one(".skillInformation"):
            card.card_type = "Pokémon"

        # ── Skills ──
        for skill_div in soup.select(".skillInformation .skill"):
            skill: dict = {"name": "", "cost": [], "damage": "", "effect": ""}
            name_el = skill_div.select_one(".skillName")
            if name_el:
                skill["name"] = self._text(name_el)

            for img in skill_div.select(".skillCost img[src]"):
                energy = self._extract_energy(img.get("src", ""))
                if energy:
                    skill["cost"].append(energy)

            dmg_el = skill_div.select_one(".skillDamage")
            if dmg_el:
                skill["damage"] = self._text(dmg_el)

            effect_el = skill_div.select_one(".skillEffect")
            if effect_el:
                skill["effect"] = self._text(effect_el)

            if skill["name"]:
                card.skills.append(skill)

        # Detect card type from skill names and page structure
        if not card.hp and card.skills:
            first = card.skills[0]["name"] if card.skills else ""
            # Trainer subtypes
            if first in ("物品卡", "[物品規則]", "物品"):
                card.card_type = "Trainer"
                card.sub_type = "Item"
                card.skills = []
            elif first in ("支援者卡", "[支援者規則]", "支援者"):
                card.card_type = "Trainer"
                card.sub_type = "Supporter"
                card.skills = []
            elif first in ("競技場卡", "[競技場規則]", "競技場"):
                card.card_type = "Trainer"
                card.sub_type = "Stadium"
                card.skills = []
            elif first in ("寶可夢道具", "[寶可夢道具規則]", "寶可夢道具"):
                card.card_type = "Trainer"
                card.sub_type = "Pokémon Tool"
                card.skills = []

        # Energy detection: no HP, no weakness table, energy in name/skills
        has_battle_stats = soup.select_one(".subInformation") is not None
        if card.card_type == "Pokémon" and not card.hp and not has_battle_stats:
            all_names = " ".join([s["name"] for s in card.skills])
            card_name = card.name or ""
            if "能量" in card_name or "能量" in all_names:
                card.card_type = "Energy"
                card.element_type = ""
            elif any(kw in all_names for kw in ("物品", "支援者", "競技場", "道具", "工具")):
                card.card_type = "Trainer"

        # ── Weakness / Resistance / Retreat ──
        sub_info = soup.select_one(".subInformation")
        if sub_info:
            weak_td = sub_info.select_one("td.weakpoint")
            if weak_td:
                w_img = weak_td.select_one("img[src]")
                if w_img:
                    card.weakness_type = self._extract_energy(w_img.get("src", ""))
                w_text = self._text(weak_td)
                w_match = re.search(r"[×＋＋](\d+)", w_text)
                if w_match:
                    card.weakness_value = "×" + w_match.group(1)
                elif "×2" in w_text:
                    card.weakness_value = "×2"

            resist_td = sub_info.select_one("td.resist")
            if resist_td:
                r_img = resist_td.select_one("img[src]")
                if r_img:
                    card.resistance_type = self._extract_energy(r_img.get("src", ""))
                r_text = self._text(resist_td)
                r_match = re.search(r"[-－](\d+)", r_text)
                if r_match:
                    card.resistance_value = "-" + r_match.group(1)

            escape_td = sub_info.select_one("td.escape")
            if escape_td:
                imgs = escape_td.select("img[src]")
                card.retreat_cost = len(imgs)

        # ── Expansion Info ──
        expansion_section = soup.select_one(".expansionColumn, .expansionLinkColumn")
        if expansion_section:
            # Regulation mark
            alpha_el = expansion_section.select_one(".alpha")
            if alpha_el:
                card.regulation_mark = self._text(alpha_el)

            # Collector number
            cn_el = expansion_section.select_one(".collectorNumber")
            if cn_el:
                cn_text = self._text(cn_el)
                parts = cn_text.split("/")
                if len(parts) == 2:
                    card.collector_number = parts[0].strip()
                    card.collector_total = parts[1].strip()

        # Expansion code from the link
        exp_link = soup.select_one("a[href*='expansionCodes=']")
        if exp_link:
            href = exp_link.get("href", "")
            match = re.search(r"expansionCodes=(\S+)", href)
            if match:
                card.expansion_code = match.group(1).rstrip("&")
            card.expansion_name = self._text(exp_link)

        # ── Artist ──
        illustrator = soup.select_one(".illustrator a")
        if illustrator:
            card.artist = self._text(illustrator)

        # ── Pokédex Info ──
        extra = soup.select_one(".extraInformation")
        if extra:
            h3 = extra.select_one("h3")
            if h3:
                pk_text = self._text(h3)
                pk_match = re.match(r"([\w.]+)\s+(.+)", pk_text)
                if pk_match:
                    card.pokedex_number = pk_match.group(1)
                    card.pokedex_category = pk_match.group(2)

            size = extra.select_one(".size")
            if size:
                values = size.select(".value")
                if len(values) >= 1:
                    card.height = self._text(values[0])
                if len(values) >= 2:
                    card.weight = self._text(values[1])

            disc = extra.select_one(".discription, .description") or extra.select_one("p")
            if disc:
                text = self._text(disc)
                # Skip if it's the size element
                if "身高" not in text and len(text) > 5:
                    card.flavor_text = text

        # ── Evolution ──
        evo = soup.select_one(".evolution")
        if evo:
            first_step = evo.select_one(".evolutionStep.first .step.active a")
            if first_step:
                card.evolves_from = self._text(first_step)

            evo_list: list[str] = []
            for step in evo.select(".evolutionStep.second .step a, .evolutionStep.third .step a"):
                t = self._text(step)
                if t and t != card.evolves_from:
                    evo_list.append(t)
            card.evolves_to = evo_list

        # ── Super type detection ──
        card.super_type = self._detect_super_type(card, soup)

        # ── Rules ──
        card.rules = self._detect_rules(card, soup)  # type: ignore

        time.sleep(self.request_delay)
        return card

    def _detect_super_type(self, card: CardDetail, soup: BeautifulSoup) -> str:
        """Detect special card types like Pokémon V, Pokémon ex, ACE SPEC, etc."""
        # Check the header / title for super type indicators
        text = self._text(soup.select_one("h1")) if soup.select_one("h1") else ""
        if "V-UNION" in text or "V-UNION" in card.name:
            return "V-UNION"
        if "VMAX" in text or "VMAX" in card.sub_type:
            return "VMAX"
        if "VSTAR" in text or "VSTAR" in card.sub_type:
            return "VSTAR"
        if "ex" in card.name and ("寶可夢ex" in text or "ex" in card.sub_type):
            return "ex"
        if "GX" in text:
            return "GX"
        if "光輝" in card.name or "光輝" in text:
            return "Radiant"
        if "V" in card.name and ("寶可夢V" in text or "Pokémon V" in text):
            return "V"
        if "超級進化" in text or "Mega" in text:
            return "Mega"
        if "ACE SPEC" in card.name:
            return "ACE SPEC"
        return ""

    def _detect_rules(self, card: CardDetail, soup: BeautifulSoup) -> list[dict]:
        """Detect rule boxes from card content."""
        rules: list[dict] = []
        for skill in card.skills:
            name = skill.get("name", "")
            effect = skill.get("effect", "")
            combined = f"{name} {effect}"
            for rule_type, keywords in [
                ("ex", ["寶可夢ex", "ポケモンex"]),
                ("V", ["寶可夢V", "ポケモンV"]),
                ("VMAX", ["寶可夢VMAX", "ポケモンVMAX"]),
                ("VSTAR", ["VSTAR"]),
                ("ACE SPEC", ["ACE SPEC"]),
                ("Radiant", ["光輝", "かがやく"]),
            ]:
                for kw in keywords:
                    if kw in combined:
                        rules.append({"type": rule_type, "text": effect or name})
                        break
        return rules

    # ─── Rarity Assignment ──────────────────────────────────────

    def assign_rarity(self, expansion_code: str, card_map: dict[str, int]) -> dict[str, tuple[str, int]]:
        """
        Assign rarity by URL filtering. For each rarity code, fetch the filtered
        card list and match detail URLs against card_map keys.

        Args:
            expansion_code: The expansion to check
            card_map: {source_url: card_db_id} mapping for cards already in DB

        Returns:
            {source_url: (rarity_label, rarity_value)}
        """
        result: dict[str, tuple[str, int]] = {}

        for rarity_val, rarity_label in RARITY_CODES:
            page = 1
            while True:
                url = f"{HK_LIST_URL}?expansionCodes={expansion_code}&rarity%5B%5D={rarity_val}&pageNo={page}"
                soup = self._fetch(url)
                if not soup:
                    break

                found_on_page = False
                for li in soup.select("li.card"):
                    link = li.select_one("a[href]")
                    if not link:
                        continue
                    detail_url = urljoin(BASE_URL, link.get("href", ""))
                    if detail_url in card_map:
                        result[detail_url] = (rarity_label, rarity_val)
                        found_on_page = True

                if not found_on_page:
                    # No more cards in this rarity, try next page
                    # Check if we reached the last page
                    pagination = soup.select(".paginationItem")
                    current_page = 0
                    for p in pagination:
                        if p.select_one("span") and not p.select_one("a"):
                            try:
                                current_page = int(self._text(p))
                            except ValueError:
                                pass
                    if page > current_page and current_page > 0:
                        break

                page += 1
                if page > 10:  # Safety limit
                    break
                time.sleep(self.request_delay)

        logger.info(f"Rarity assigned for {len(result)} cards in {expansion_code}")
        return result

    # ─── Full Expansion Crawl ───────────────────────────────────

    def crawl_expansion(self, expansion: ExpansionInfo) -> dict:
        """
        Crawl all cards in a single expansion. Returns summary dict.
        """
        code = expansion.code
        name = expansion.name
        logger.info(f"=== Crawling expansion: [{code}] {name} ===")

        stats = {"expansion": code, "pages": 0, "cards_found": 0, "cards_parsed": 0, "images_downloaded": 0, "errors": 0}

        # Step 1: Get card list entries
        all_entries: list[CardListEntry] = []
        page = 1
        while True:
            entries, total_pages = self.crawl_card_list(code, page)
            all_entries.extend(entries)
            stats["pages"] = max(stats["pages"], total_pages)
            logger.info(f"  Page {page}/{total_pages}: {len(entries)} cards")
            if page >= total_pages:
                break
            page += 1

        stats["cards_found"] = len(all_entries)
        logger.info(f"Total cards found: {len(all_entries)}")

        # Step 2: Parse each detail page
        cards_data: list[CardDetail] = []
        for entry in all_entries:
            card = self.crawl_card_detail(entry.detail_url, entry.detail_id, entry.image_url)
            if card:
                # Fill in expansion info
                if not card.expansion_code:
                    card.expansion_code = code
                if not card.expansion_name:
                    card.expansion_name = name
                cards_data.append(card)
                stats["cards_parsed"] += 1
            else:
                stats["errors"] += 1
                logger.warning(f"  Failed to parse card {entry.detail_id}")

        # Step 3: Download images
        exp_dir = os.path.join(self.image_dir, code)
        for card in cards_data:
            if not card.image_url or not card.collector_number:
                continue
            fname = f"{card.collector_number}.png"
            path = os.path.join(exp_dir, fname)
            ok, checksum = self._download_image(card.image_url, path)
            if ok:
                card.local_image_path = path  # type: ignore
                card.image_checksum = checksum  # type: ignore
                stats["images_downloaded"] += 1

        stats["cards_data"] = cards_data
        return stats

    # ─── Build card_map for rarity ──────────────────────────────

    def build_source_url_map(self, cards_data: list[CardDetail]) -> dict[str, int]:
        """Build {source_url: index} mapping for rarity assignment."""
        return {c.source_url: i for i, c in enumerate(cards_data) if c.source_url}  # type: ignore


# Singleton instance
crawler = OfficialHKCrawler()
