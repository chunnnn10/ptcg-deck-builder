"""
Limitless JP 解析器 — 本地 HTML 測試
對五張參考 HTML 驗證解析邏輯，不需網路請求。
"""
import re
import json
import os
from bs4 import BeautifulSoup

HTML_DIR = os.path.join(os.path.dirname(__file__), '..', '參考資料', 'JP-Limitless')

TEST_CASES = [
    ("limitless_sv8_1.html",    "SV8",  "1",   "寶可夢 Basic (單招式)"),
    ("limitless_sv9_87.html",   "SV9",  "87",  "寶可夢 Basic (單招式, 有抗性)"),
    ("limitless_sv8a_237.html", "SV8a", "237", "寶可夢 ex (雙招式, Ultra Rare)"),
    ("supporter_card.html",     "M5",   "76",  "訓練家 Supporter"),
    ("item_card.html",          "M5",   "81",  "能量 Special Energy"),
]

# 屬性關鍵字 (與舊 crawler 一致)
TYPE_KEYWORDS = [
    'Grass', 'Fire', 'Water', 'Lightning', 'Psychic',
    'Fighting', 'Darkness', 'Metal', 'Dragon', 'Colorless', 'Fairy'
]

# 能量圖示符號 → 英文屬性
SYMBOL_MAP = {
    'G': 'Grass', 'R': 'Fire', 'W': 'Water', 'L': 'Lightning',
    'P': 'Psychic', 'F': 'Fighting', 'D': 'Darkness', 'M': 'Metal',
    'N': 'Dragon', 'Y': 'Fairy', 'C': 'Colorless',
}


def parse_card(html: str, expected_set_code: str = "", expected_number: str = "") -> dict:
    """從 Limitless JP HTML 提取完整卡牌資料"""
    soup = BeautifulSoup(html, 'html.parser')
    data = {}

    # ── 卡片 ID (從 HTML comment) ──
    card_id_match = re.search(r'<!-- CARD ID (\d+) -->', html)
    if card_id_match:
        data['_card_id'] = int(card_id_match.group(1))

    # ── 圖片 URL ──
    img_tag = soup.select_one('.card-image img')
    if img_tag:
        data['image_url'] = img_tag.get('src') or img_tag.get('data-src')
    if not data.get('image_url'):
        og_img = soup.select_one('meta[property="og:image"]')
        if og_img:
            data['image_url'] = og_img.get('content', '')

    # ── 名稱 ──
    name_tag = soup.select_one('.card-text-name a')
    data['name'] = name_tag.get_text(strip=True) if name_tag else ""

    # ── 屬性 & HP ──
    title_el = soup.select_one('.card-text-title')
    title_text = title_el.get_text(' ', strip=True) if title_el else ''
    # 移除名稱部分
    if data['name']:
        title_text = title_text.replace(data['name'], '', 1).strip()

    # 找屬性
    data['element_type'] = 'Colorless'
    for t in TYPE_KEYWORDS:
        if f'- {t}' in title_text or title_text.startswith(f'{t}'):
            data['element_type'] = t
            break

    # 找 HP
    hp_match = re.search(r'(\d+)\s*HP', title_text)
    data['hp'] = int(hp_match.group(1)) if hp_match else 0

    # ── 卡片類型 & 子類型 ──
    type_el = soup.select_one('.card-text-type')
    type_text = type_el.get_text(strip=True) if type_el else ''
    if ' - ' in type_text:
        parts = type_text.split(' - ', 1)
        data['card_type'] = parts[0].strip()
        data['sub_type'] = parts[1].strip()
    else:
        data['card_type'] = type_text.strip()
        data['sub_type'] = ''

    # ── 技能 / 特性 ──
    skills = []
    # 攻擊
    for attack_div in soup.select('.card-text-attack'):
        skill = _parse_skill(attack_div, 'attack')
        if skill:
            skills.append(skill)
    # 特性
    for ability_div in soup.select('.card-text-ability'):
        skill = _parse_skill(ability_div, 'ability')
        if skill:
            skills.append(skill)
    data['skills'] = skills

    # ── 訓練家/能量描述 ──
    data['description'] = ''
    if data['card_type'] in ('Trainer', 'Energy'):
        sections = soup.select('.card-text .card-text-section')
        for sec in sections:
            # 跳過：含攻擊/特性、含標題區（卡名+類型）、含 WRR、含繪師
            if sec.select_one('.card-text-attack, .card-text-ability, .card-text-title'):
                continue
            desc_text = sec.get_text(' ', strip=True)
            if desc_text and 'Weakness:' not in desc_text and 'Illustrated by' not in desc_text:
                data['description'] = desc_text
                break

    # ── 弱點 / 抗性 / 撤退 ──
    wrr_el = soup.select_one('.card-text-wrr')
    if wrr_el:
        wrr_text = wrr_el.get_text(' ', strip=True)

        w_match = re.search(r'Weakness:\s*(\w+)', wrr_text)
        if w_match and w_match.group(1).lower() != 'none':
            data['weakness_type'] = w_match.group(1)
            # 預設 ×2，除非有不同值
            w_val = re.search(r'Weakness:\s*\w+\s*([×x+\-]\d+)', wrr_text)
            data['weakness_value'] = w_val.group(1) if w_val else '×2'
        else:
            data['weakness_type'] = ''
            data['weakness_value'] = ''

        r_match = re.search(r'Resistance:\s*(\w+)', wrr_text)
        if r_match and r_match.group(1).lower() != 'none':
            data['resistance_type'] = r_match.group(1)
            r_val = re.search(r'Resistance:\s*\w+\s*([\-−]\d+)', wrr_text)
            data['resistance_value'] = r_val.group(1) if r_val else '-30'
        else:
            data['resistance_type'] = ''
            data['resistance_value'] = ''

        ret_match = re.search(r'Retreat:\s*(\d+)', wrr_text)
        data['retreat_cost'] = int(ret_match.group(1)) if ret_match else 0
    else:
        data['weakness_type'] = ''
        data['weakness_value'] = ''
        data['resistance_type'] = ''
        data['resistance_value'] = ''
        data['retreat_cost'] = 0

    # ── 繪師 ──
    artist_el = soup.select_one('.card-text-artist a')
    data['artist'] = artist_el.get_text(strip=True) if artist_el else ''

    # ── 賽季標記 ──
    reg_el = soup.select_one('.regulation-mark')
    if reg_el:
        reg_text = reg_el.get_text(strip=True)
        reg_match = re.search(r'^(\w+)\s*Regulation Mark', reg_text)
        data['regulation_mark'] = reg_match.group(1) if reg_match else ''
    else:
        data['regulation_mark'] = ''

    # ── 格式合法性 ──
    data['standard_jp_legal'] = False
    data['expanded_jp_legal'] = False
    for item in soup.select('.card-legality-item'):
        label = item.select_one('div:first-child')
        status = item.select_one('.legal, .not-legal')
        if label and status:
            label_text = label.get_text(strip=True)
            is_legal = 'legal' in status.get('class', [])
            if 'Standard (JP)' in label_text:
                data['standard_jp_legal'] = is_legal
            elif 'Expanded (JP)' in label_text:
                data['expanded_jp_legal'] = is_legal

    # ── 系列資訊 (from .card-prints-current) ──
    prints_current = soup.select_one('.card-prints-current')
    if prints_current:
        # set_code 從連結 href 或 img alt
        set_link = prints_current.select_one('a[href^="/cards/jp/"]')
        if set_link:
            href = set_link.get('href', '')
            set_match = re.search(r'/cards/jp/(\w+)', href)
            if set_match:
                data['set_code'] = set_match.group(1)

        # set_name 和稀有度
        text_lg = prints_current.select_one('.text-lg')
        if text_lg:
            set_text = text_lg.get_text(strip=True)
            # "Super Electric Breaker (SV8)" → set_name, set_code
            name_match = re.match(r'^(.+?)\s*\((\w+)\)$', set_text)
            if name_match:
                data['set_name'] = name_match.group(1).strip()
                if not data.get('set_code'):
                    data['set_code'] = name_match.group(2)

        # 編號 & 稀有度
        details_spans = prints_current.select('span:not(.text-lg)')
        for span in details_spans:
            span_text = span.get_text(strip=True)
            # "#1 · Common"
            cn_match = re.match(r'#(\d+)\s*·\s*(.+)', span_text)
            if cn_match:
                data['set_number'] = cn_match.group(1)
                data['rarity'] = cn_match.group(2).strip()

    # 用 expected 補齊
    if not data.get('set_code'):
        data['set_code'] = expected_set_code
    if not data.get('set_number'):
        data['set_number'] = expected_number

    # ── Int. Prints 英文版連結 ──
    en_links = []
    for a in soup.select('table.card-prints-versions a[href^="/cards/en/"]'):
        href = a.get('href', '')
        full_text = a.get_text(strip=True)
        # "Surging Sparks #2" → set_name, set_number
        en_match = re.match(r'^(.+?)\s*#(\d+)$', full_text)
        if en_match:
            en_links.append({
                'set_name': en_match.group(1).strip(),
                'set_number': en_match.group(2),
                'url': f"https://limitlesstcg.com{href}",
            })
    data['en_prints'] = en_links

    return data


def _parse_skill(div, skill_type: str) -> dict | None:
    """解析單個攻擊或特性"""
    skill = {'type': skill_type, 'name': '', 'cost': [], 'damage': '', 'effect': ''}

    if skill_type == 'ability':
        info_tag = div.select_one('.card-text-ability-info')
        if info_tag:
            raw = info_tag.get_text(strip=True)
            skill['name'] = raw.replace('Ability:', '').strip()
    else:
        info_tag = div.select_one('.card-text-attack-info')
        if not info_tag:
            return None

        # 能量費用符號
        for symbol in info_tag.select('.ptcg-symbol'):
            sym_text = symbol.get_text(strip=True)
            # 可能是單字母或多字母 (如 "GWL")
            for ch in sym_text:
                if ch in SYMBOL_MAP:
                    skill['cost'].append(SYMBOL_MAP[ch])

        # 名稱 + 傷害
        full_text = info_tag.get_text(' ', strip=True)
        # 移除已處理的符號文字
        for symbol in info_tag.select('.ptcg-symbol'):
            full_text = full_text.replace(symbol.get_text(strip=True), '', 1)
        full_text = full_text.strip()

        # 提取傷害 (末端的數字，可能含 ×、+、-)
        dmg_match = re.search(r'(\d+[×x+\-]?\s*\d*)\s*$', full_text)
        if dmg_match:
            skill['damage'] = dmg_match.group(1).strip()
            skill['name'] = full_text[:dmg_match.start()].strip()
        else:
            skill['damage'] = ''
            skill['name'] = full_text.strip()

    # 效果
    effect_tag = div.select_one('.card-text-attack-effect, .card-text-ability-effect')
    if effect_tag:
        skill['effect'] = effect_tag.get_text(strip=True)

    return skill if skill['name'] else None


# ==========================================
# 主測試
# ==========================================
def main():
    print("=" * 70)
    print("Limitless JP 解析器 — 本地 HTML 測試")
    print("=" * 70)

    for filename, exp_set, exp_num, desc in TEST_CASES:
        filepath = os.path.join(HTML_DIR, filename)
        if not os.path.exists(filepath):
            print(f"\n❌ 找不到檔案: {filepath}")
            continue

        with open(filepath, 'r', encoding='utf-8') as f:
            html = f.read()

        data = parse_card(html, exp_set, exp_num)

        print(f"\n{'─' * 70}")
        print(f"📋 {filename}  [{desc}]")
        print(f"{'─' * 70}")
        print(f"  名稱:           {data['name']}")
        print(f"  類型:           {data['card_type']} - {data['sub_type']}")
        print(f"  屬性:           {data['element_type']}")
        print(f"  HP:             {data['hp']}")
        print(f"  弱點:           {data['weakness_type']} {data['weakness_value']}")
        print(f"  抗性:           {data['resistance_type']} {data['resistance_value']}")
        print(f"  撤退:           {data['retreat_cost']}")
        print(f"  賽季標記:       {data['regulation_mark']}")
        print(f"  Standard JP:    {'✅' if data['standard_jp_legal'] else '❌'}")
        print(f"  Expanded JP:    {'✅' if data['expanded_jp_legal'] else '❌'}")
        print(f"  系列:           {data.get('set_name','')} ({data.get('set_code','')})")
        print(f"  編號:           #{data.get('set_number','')}")
        print(f"  稀有度:         {data.get('rarity','')}")
        print(f"  繪師:           {data['artist']}")
        print(f"  圖片:           {data.get('image_url','')[:80]}...")

        if data['skills']:
            print(f"  技能 ({len(data['skills'])}):")
            for i, sk in enumerate(data['skills']):
                cost_str = ''.join(c[0] for c in sk['cost']) if sk['cost'] else '-'
                print(f"    [{sk['type']}] {sk['name']}  [{cost_str}]  {sk['damage']}")
                if sk['effect']:
                    print(f"      ↳ {sk['effect'][:100]}")
        else:
            print(f"  技能:           (無)")

        if data['description']:
            desc_preview = data['description'][:120].replace('\n', ' ')
            print(f"  描述:           {desc_preview}...")

        if data['en_prints']:
            print(f"  英文對照 ({len(data['en_prints'])}):")
            for ep in data['en_prints']:
                print(f"    → {ep['set_name']} #{ep['set_number']}")

        if data.get('_card_id'):
            print(f"  Card ID:        {data['_card_id']}")

    print(f"\n{'=' * 70}")
    print("測試完成！請確認以上輸出是否正確。")
    print("=" * 70)


if __name__ == '__main__':
    main()
