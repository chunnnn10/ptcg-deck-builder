import requests
from bs4 import BeautifulSoup
from flask import Flask, request, jsonify, send_from_directory
import os
import re
import time
import random

# === 設定 ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=BASE_DIR, static_folder=BASE_DIR)

# LimitlessTCG 基礎網址
LIMITLESS_BASE = "https://limitlesstcg.com"

# 模擬瀏覽器 Headers (Limitless 有時會擋無 Header 請求)
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Referer": "https://google.com"
}

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'limitless_lab.html')

def fetch_english_match(jp_set_code, jp_set_number):
    """
    核心爬蟲邏輯：
    1. 構造日版 URL
    2. 抓取頁面
    3. 解析 'Int. Prints' 區塊
    """
    # 1. 處理編號：去除前導零 (例如 '001' -> '1')
    try:
        clean_number = str(int(jp_set_number))
    except:
        clean_number = jp_set_number # 如果無法轉數字就維持原樣

    # 構造目標 URL: https://limitlesstcg.com/cards/jp/MC/1
    # 注意：有些 Set Code 可能需要調整 (例如空格轉 %20)，但通常 Limitless 使用簡寫
    target_url = f"{LIMITLESS_BASE}/cards/jp/{jp_set_code}/{clean_number}"
    
    print(f"🚀 正在爬取: {target_url}")

    try:
        # 隨機延遲，避免對伺服器造成負擔
        time.sleep(random.uniform(0.5, 1.5))
        
        response = requests.get(target_url, headers=HEADERS, timeout=10)
        
        if response.status_code == 404:
            return {"error": "404 Not Found (Limitless 沒有這張日版卡的頁面)", "url": target_url}
        
        if response.status_code != 200:
            return {"error": f"HTTP {response.status_code}", "url": target_url}

        # 解析 HTML
        soup = BeautifulSoup(response.text, 'html.parser')
        
        # 1. 確認這是不是我們找的卡 (抓取日文標題)
        card_name_h1 = soup.select_one(".card-text-title")
        jp_card_name = card_name_h1.get_text(strip=True) if card_name_h1 else "未知"

        # 2. 尋找 'Int. Prints' (國際版/美版) 的連結
        # 策略：在表格中尋找 href 包含 '/cards/en/' 的連結
        # 你的 HTML 範例顯示它在 .card-prints-versions 表格內
        
        en_match = None
        
        # 抓取所有連向英文卡片的連結
        en_links = soup.select('table.card-prints-versions a[href^="/cards/en/"]')
        
        if en_links:
            # 通常第一個就是對應的英文版 (或者找最上面的一個)
            target_link = en_links[0]
            href = target_link.get('href') # 例如: /cards/en/ASC/1
            
            # 解析 href 取得英文 Set Code 和 Number
            # 格式通常是: /cards/en/{SET}/{NUM}
            parts = href.strip('/').split('/')
            if len(parts) >= 4:
                en_set_code = parts[2]
                en_set_number = parts[3]
                
                # 抓取英文 Set Name (連結文字的一部分)
                # HTML: Ascended Heroes <span class="number">#1</span>
                full_text = target_link.get_text(strip=True)
                # 移除 #1 這種編號，只留 Set Name
                en_set_name = re.sub(r'#\d+$', '', full_text).strip()

                en_match = {
                    "en_set_code": en_set_code,
                    "en_set_number": en_set_number,
                    "en_set_name": en_set_name,
                    "link": f"{LIMITLESS_BASE}{href}"
                }

        # 3. 嘗試抓取價格 (USD)
        price_usd = "N/A"
        price_tag = soup.select_one('.card-price.usd')
        if price_tag:
            price_usd = price_tag.get_text(strip=True)

        return {
            "status": "success",
            "url": target_url,
            "jp_name": jp_card_name,
            "english_match": en_match, # 這是你要的核心資料
            "price_usd": price_usd
        }

    except Exception as e:
        return {"error": str(e), "url": target_url}

@app.route('/extract', methods=['POST'])
def extract():
    data = request.json
    set_code = data.get('set_code', '').strip()
    set_number = data.get('set_number', '').strip()

    if not set_code or not set_number:
        return jsonify({"error": "缺少 Set Code 或 Set Number"}), 400

    result = fetch_english_match(set_code, set_number)
    return jsonify(result)

if __name__ == '__main__':
    print("Limitless Explorer 已啟動：http://127.0.0.1:5002")
    app.run(debug=True, port=5002)