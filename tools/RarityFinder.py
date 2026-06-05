import os
import re
import time
import requests
import json
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin
from tqdm import tqdm

# --- 參數設定 ---
BASE_URL = "https://asia.pokemon-card.com/tw/card-search/list/"
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
}
OUTPUT_FILE = "rarity_map.json"

# --- 程式主體 ---

def get_rarity_list(soup):
    """從搜尋頁面抓取所有稀有度選項"""
    rarities = []
    options = soup.select("div.rarities div.rarityOption")
    for opt in options:
        input_tag = opt.select_one("input[name='rarity[]']")
        label_tag = opt.select_one("label")
        if input_tag and label_tag:
            rarity_id = input_tag.get('value')
            rarity_name = clean_text(label_tag.get_text())
            if rarity_id and rarity_name:
                rarities.append({'id': rarity_id, 'name': rarity_name})
    print(f"找到 {len(rarities)} 種稀有度選項。")
    return rarities

def clean_text(text):
    if text is None: return ""
    return re.sub(r'\s+', ' ', text).strip()

def main():
    print(f"正在訪問搜尋頁面以獲取稀有度列表...")
    try:
        response = requests.get(BASE_URL, headers=HEADERS, timeout=15)
        response.raise_for_status()
        soup = BeautifulSoup(response.text, 'html.parser')
    except requests.exceptions.RequestException as e:
        print(f"訪問 {BASE_URL} 失敗: {e}")
        return

    rarity_list = get_rarity_list(soup)
    if not rarity_list:
        print("未能在頁面上找到稀有度選項。")
        return

    card_rarity_map = {} # 最終的對應表
    
    # 使用 tqdm 遍歷每種稀有度
    for rarity in tqdm(rarity_list, desc="正在爬取各種稀有度"):
        rarity_id = rarity['id']
        rarity_name = rarity['name']
        page_num = 1
        
        # 【已移除】移除了內部的 tqdm 進度條 (pbar_page)
        
        while True:
            # 1. 組合搜尋 URL
            search_url = f"{BASE_URL}?rarity[]={rarity_id}&pageNo={page_num}"
            
            try:
                time.sleep(0.05) # 禮貌性暫停
                page_response = requests.get(search_url, headers=HEADERS, timeout=10)
                if page_response.status_code != 200:
                    break # 頁面請求失敗
                    
                page_soup = BeautifulSoup(page_response.text, 'html.parser')
                
                # 2. 抓取該頁所有卡片的連結
                # --- 【錯誤修正】---
                # 舊的選擇器是錯的: "div.imageContainer a[href*='/detail/']"
                # 新的選擇器 (根據 li.card > a) 才正確
                card_links = page_soup.select("li.card > a[href*='/detail/']")
                
                if not card_links:
                    # 如果 li.card > a 找不到 (也許頁面結構變了)，嘗試備用方案
                    # 備用方案：找到 imageContainer，然後往上找它的 'a' 標籤
                    containers = page_soup.select("div.imageContainer")
                    card_links = []
                    for c in containers:
                        parent_a = c.find_parent("a", href=re.compile(r'/detail/'))
                        if parent_a:
                            card_links.append(parent_a)

                if not card_links:
                # --- ---------------- ---
                    break # 找不到卡片，代表這是最後一頁

                # 3. 從連結中提取 card_id
                for link in card_links:
                    href = link.get('href')
                    match = re.search(r'/detail/(\d+)/', href)
                    if match:
                        card_id = match.group(1).lstrip('0')
                        if not card_id: card_id = "0"
                        
                        # 儲存到 map 中
                        # 備註：一張卡 (如花舞鳥ex) 可能同時是 'RR' 和 'SR'
                        # 這裡的邏輯是，後來的會覆蓋先前的 (通常稀有度高的會覆蓋低的)
                        # 如果需要，我們可以改成儲存一個列表
                        card_rarity_map[card_id] = rarity_name
                
                # --- 【新功能】印出可視化的進度 ---
                # 使用 tqdm.write() 可以安全地在進度條下方印出訊息
                tqdm.write(f"  -> {rarity_name}: 已完成第 {page_num} 頁 (找到 {len(card_links)} 張卡)")
                
                page_num += 1

            except requests.exceptions.RequestException:
                tqdm.write(f"爬取 {rarity_name} 第 {page_num} 頁時失敗，跳過...")
                break
        
        # 【已移除】移除 pbar_page.close()

    # 4. 儲存到 JSON 檔案
    print(f"\n所有稀有度爬取完畢！總共映射了 {len(card_rarity_map)} 張獨立卡片。")
    print(f"正在儲存到 {OUTPUT_FILE} ...")
    try:
        with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
            json.dump(card_rarity_map, f, ensure_ascii=False, indent=4)
        print("="*30)
        print("稀有度地圖 (rarity_map.json) 產生成功！")
        print("="*30)
    except IOError as e:
        print(f"儲存檔案失敗: {e}")

if __name__ == "__main__":
    # 確保已安裝: pip install requests beautifulsoup4 tqdm
    main()