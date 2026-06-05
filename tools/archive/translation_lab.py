import requests
from flask import Flask, request, jsonify, send_from_directory
import os
import json
import urllib.parse
import time
import re  # 新增正則表達式模組

# === 路徑設定 ===
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
app = Flask(__name__, template_folder=BASE_DIR, static_folder=BASE_DIR)

# === 核心配置 ===
BASE_DOMAIN = "https://ptcgsp.com"
API_SEARCH_URL = f"{BASE_DOMAIN}/api/cards/"
API_DETAIL_URL = f"{BASE_DOMAIN}/api/cards/" 

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "accept-language": "zh-HK,zh;q=0.9,en-US;q=0.8,en;q=0.7,zh-TW;q=0.6",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Referer": "https://ptcgsp.com/cards", 
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-origin"
}

@app.route('/')
def index():
    return send_from_directory(BASE_DIR, 'translation_lab.html')

@app.route('/diagnose', methods=['POST'])
def diagnose():
    target_name = request.json.get('name', '').strip()
    if not target_name: 
        return jsonify({"error": "請輸入名稱"}), 400

    report = {
        "logs": [],
        "method": "API Matcher (Set Code + Number)",
        "api_url": "",
        "raw_response": "",
        "found_cards": [],
        "final_result": None
    }

    def log(msg):
        print(msg)
        report["logs"].append(msg)

    def extract_set_number(img_url):
        """從圖片路徑提取編號 (例如 .../SV2a_F_010.jpg -> 010)"""
        if not img_url:
            return None
        # 匹配邏輯：底線後面的數字 + .jpg 結尾
        # 針對 .../SV2a_F_010.jpg 這種格式
        match = re.search(r'_([0-9]+)\.jpg$', img_url, re.IGNORECASE)
        if match:
            return match.group(1)
        # 備用邏輯：如果沒有底線，嘗試抓最後一組數字
        match_backup = re.search(r'([0-9]+)\.jpg$', img_url, re.IGNORECASE)
        if match_backup:
            return match_backup.group(1)
        return None

    try:
        # === Step 1: 搜尋取得列表 ===
        log(f"🚀 [Step 1] 搜尋關鍵字: {target_name}")
        
        params = {
            "keyword": target_name,
            "page": 1,
            "keywordType": '["n","e"]' 
        }
        report["api_url"] = f"{API_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        
        resp_list = requests.get(API_SEARCH_URL, headers=HEADERS, params=params, timeout=10)
        
        if resp_list.status_code != 200:
            report["final_result"] = f"搜尋請求失敗: {resp_list.status_code}"
            return jsonify(report)

        data_list = resp_list.json()
        report["raw_response"] = json.dumps(data_list, ensure_ascii=False, indent=2)

        cards_summary = data_list.get('data', {}).get('cards', [])
        
        if not cards_summary:
            report["final_result"] = "搜尋結果為空"
            return jsonify(report)

        log(f"✅ 找到 {len(cards_summary)} 張卡片，開始解析配對鍵...")

        # === Step 2: 解析配對鍵並抓取詳情 ===
        # 我們抓取前 5 筆，展示配對邏輯
        target_candidates = cards_summary[:5]
        
        valid_match_count = 0

        for idx, summary in enumerate(target_candidates):
            uid = summary.get('uid')
            
            # --- 解析配對鍵 (Mapping Keys) ---
            # 1. Set Code
            set_info = summary.get('set_f', {})
            raw_set_code = set_info.get('code', 'N/A') if set_info else 'N/A'
            
            # 2. Set Number (從圖片網址提取)
            img_url_tc = summary.get('img_url_f') # 繁中圖片 (通常包含編號)
            
            # === 修正點在這裡 ===
            extracted_number = extract_set_number(img_url_tc) 
            # 原本寫成 extract_number 導致錯誤
            
            # 顯示用的圖片 (優先用繁中，沒有就用日文)
            display_img = img_url_tc or summary.get('img_url_j')
            full_img_url = f"{BASE_DOMAIN}{display_img}" if display_img else ""

            log(f"🧩 [Card {idx+1}] ID:{uid} | Set:{raw_set_code} | No:{extracted_number or '未知'}")

            # 如果沒有繁中圖片網址，通常代表這是日版獨有或資料缺失，無法配對本地繁中資料庫
            if not extracted_number:
                log(f"   ⚠️ 跳過: 無法從圖片網址提取編號 (img_url_f is null)")
                continue

            # --- 抓取詳情 (獲取日文名) ---
            c_name_j = "讀取中..."
            detail_url = f"{API_DETAIL_URL}{uid}"
            
            try:
                # 這裡稍微 sleep 一下禮貌性防擋，實際跑可以拿掉
                # time.sleep(0.2) 
                resp_detail = requests.get(detail_url, headers=HEADERS, timeout=5)
                if resp_detail.status_code == 200:
                    detail_data = resp_detail.json()
                    c_detail = detail_data.get('data', {})
                    c_name_j = c_detail.get('name_j', '未知')
                else:
                    c_name_j = "API Error"
            except:
                c_name_j = "Timeout"

            card_info = {
                "uid": uid,
                "set_code": raw_set_code,       # 用於資料庫配對
                "set_number": extracted_number, # 用於資料庫配對
                "name_j": c_name_j,             # 我們要更新的目標資料
                "image": full_img_url,
                "raw_summary": summary
            }
            report["found_cards"].append(card_info)
            valid_match_count += 1

        if valid_match_count > 0:
            report["final_result"] = f"成功解析 {valid_match_count} 組配對鍵"
        else:
            report["final_result"] = "無有效配對鍵 (缺 img_url_f)"

    except Exception as e:
        log(f"系統錯誤: {e}")
        report["final_result"] = str(e)

    return jsonify(report)

if __name__ == '__main__':
    print("API 配對實驗室已啟動：http://127.0.0.1:5001")
    app.run(debug=True, port=5001)