import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
import threading
import queue
import time
import sys
import os

BACKEND_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', 'backend'))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)
from services.deck_importer.deck_importer import DeckImporter

class PokemonDeckGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("寶可夢牌組自動匯入器 (全自動守護版)") # 改個標題
        self.root.geometry("1000x700")
        
        # 核心組件
        self.importer = DeckImporter()
        self.deck_queue = queue.Queue()
        self.is_running = False
        self.workers = []
        self.worker_statuses = [] 
        
        self.total_decks_found = 0
        self.processed_count = 0
        self.success_count = 0
        
        self.setup_ui()
        
        # [新增] 啟動自動排程
        self.start_auto_scheduler()

    def setup_ui(self):
        # ... (這裡的 UI 代碼完全不用動，保持原樣) ...
        # --- 為了節省篇幅，請保留原有的 control_frame, progress_frame 等程式碼 ---
        # --- 只要複製原本 setup_ui 的內容即可 ---
        control_frame = ttk.LabelFrame(self.root, text="爬取設定", padding=10)
        control_frame.pack(fill="x", padx=10, pady=5)
        
        # 頁數設定
        ttk.Label(control_frame, text="起始頁:").grid(row=0, column=0, padx=5)
        self.entry_start_page = ttk.Entry(control_frame, width=8)
        self.entry_start_page.insert(0, "1")
        self.entry_start_page.grid(row=0, column=1, padx=5)
        
        ttk.Label(control_frame, text="結束頁:").grid(row=0, column=2, padx=5)
        self.entry_end_page = ttk.Entry(control_frame, width=8)
        self.entry_end_page.insert(0, "1105") 
        self.entry_end_page.grid(row=0, column=3, padx=5)
        
        ttk.Label(control_frame, text="(最大約 1105)").grid(row=0, column=4, padx=5)
        
        # 線程設定
        ttk.Label(control_frame, text="Worker 數量:").grid(row=0, column=5, padx=5)
        self.entry_threads = ttk.Entry(control_frame, width=5)
        self.entry_threads.insert(0, "10") 
        self.entry_threads.grid(row=0, column=6, padx=5)
        
        # 按鈕
        self.btn_start = ttk.Button(control_frame, text="手動開始", command=self.start_process) # 改名手動
        self.btn_start.grid(row=0, column=7, padx=20)
        
        self.btn_stop = ttk.Button(control_frame, text="停止", command=self.stop_process, state="disabled")
        self.btn_stop.grid(row=0, column=8, padx=5)

        # --- 進度區 ---
        progress_frame = ttk.LabelFrame(self.root, text="總體進度", padding=10)
        progress_frame.pack(fill="x", padx=10, pady=5)
        
        self.lbl_status = ttk.Label(progress_frame, text="系統待命中...", font=("Arial", 10, "bold"))
        self.lbl_status.pack(anchor="w")
        
        self.progress_bar = ttk.Progressbar(progress_frame, orient="horizontal", mode="indeterminate")
        self.progress_bar.pack(fill="x", pady=5)
        
        self.lbl_stats = ttk.Label(progress_frame, text="已掃描任務: 0 | 已處理: 0 | 成功: 0 | 失敗: 0")
        self.lbl_stats.pack(anchor="w")

        # --- Worker 監控區 ---
        self.worker_frame = ttk.LabelFrame(self.root, text="Worker 狀態監控", padding=10)
        self.worker_frame.pack(fill="both", expand=True, padx=10, pady=5)
        
        self.worker_canvas = tk.Canvas(self.worker_frame)
        scrollbar = ttk.Scrollbar(self.worker_frame, orient="vertical", command=self.worker_canvas.yview)
        self.scrollable_frame = ttk.Frame(self.worker_canvas)

        self.scrollable_frame.bind(
            "<Configure>",
            lambda e: self.worker_canvas.configure(scrollregion=self.worker_canvas.bbox("all"))
        )

        self.worker_canvas.create_window((0, 0), window=self.scrollable_frame, anchor="nw")
        self.worker_canvas.configure(yscrollcommand=scrollbar.set)

        self.worker_canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        # --- Log 區 ---
        log_frame = ttk.LabelFrame(self.root, text="系統日誌", padding=10)
        log_frame.pack(fill="both", padx=10, pady=5, ipady=30)
        
        self.txt_log = scrolledtext.ScrolledText(log_frame, height=8, state='disabled')
        self.txt_log.pack(fill="both", expand=True)

    # ... (log, update_worker_status, create_worker_widgets 保持不變) ...
    def log(self, msg):
        self.txt_log.config(state='normal')
        self.txt_log.insert(tk.END, msg + "\n")
        self.txt_log.see(tk.END)
        self.txt_log.config(state='disabled')

    def update_worker_status(self, worker_id, msg):
        if worker_id < len(self.worker_statuses):
            self.worker_statuses[worker_id].set(f"Worker #{worker_id+1}: {msg}")

    def create_worker_widgets(self, num_workers):
        for widget in self.scrollable_frame.winfo_children():
            widget.destroy()
        self.worker_statuses.clear()
        
        cols = 3 if num_workers > 10 else 2
        for i in range(num_workers):
            var = tk.StringVar(value=f"Worker #{i+1}: 待命")
            self.worker_statuses.append(var)
            lbl = ttk.Label(self.scrollable_frame, textvariable=var, relief="sunken", padding=5, width=35)
            lbl.grid(row=i // cols, column=i % cols, padx=5, pady=2, sticky="w")

    # [新增] 自動排程器
    def start_auto_scheduler(self):
        self.log("=== 自動排程系統已啟動 ===")
        # 1. 立即執行一次檢查 (延遲 1秒 讓 UI 先出來)
        self.root.after(1000, self.run_daily_check)
        
        # 2. 設定每 24 小時 (86400000 毫秒) 執行一次
        # 注意：這裡是一個簡單的週期循環。如果要精確在「每天凌晨」執行，邏輯會更複雜，
        # 但考慮到這是一個個人工具，間隔 24 小時執行通常已經足夠。
        self.root.after(86400000, self.schedule_next_run)

    def schedule_next_run(self):
        self.run_daily_check()
        self.root.after(86400000, self.schedule_next_run)

    def run_daily_check(self):
        if self.is_running:
            self.log("警告：當前有任務正在執行，跳過本次自動檢查。")
            return
            
        self.log(">>> 開始執行每日自動檢查...")
        
        # 設定為自動模式，預設 5 個 Worker 就夠了
        num_workers = 5 
        
        # 啟動並行任務，但這次我們使用一個特殊的 flag 或方法來指示這是「智慧更新」
        self.is_running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        
        self.processed_count = 0
        self.success_count = 0
        self.total_decks_found = 0
        
        # 清空佇列
        while not self.deck_queue.empty():
            try: self.deck_queue.get_nowait()
            except queue.Empty: break

        self.create_worker_widgets(num_workers)
        self.progress_bar.config(mode="determinate", maximum=100, value=0)
        
        # 啟動線程，這次 target 是 run_smart_pipeline
        threading.Thread(target=self.run_smart_pipeline, args=(num_workers,), daemon=True).start()

    # [新增] 智慧更新流程 (取代原有的 run_parallel_pipeline)
    def run_smart_pipeline(self, num_workers):
        # 1. 啟動 Worker
        self.workers = []
        for i in range(num_workers):
            t = threading.Thread(target=self.worker_task, args=(i,), daemon=True)
            t.start()
            self.workers.append(t)
            
        self.root.after(0, lambda: self.log(f"自動檢查中... (Workers: {num_workers})"))
        
        try:
            # 2. 呼叫新的智慧爬取方法
            # 注意：這裡會回傳「需要更新的牌組列表」，如果日期一樣則回傳空
            new_decks = self.importer.crawl_smart_update(
                status_callback=lambda msg: self.root.after(0, lambda: self.log(msg))
            )
            
            if not new_decks:
                self.root.after(0, lambda: self.log(">>> 檢查完畢：目前已是最新狀態，無需更新。"))
            else:
                count = len(new_decks)
                self.total_decks_found = count
                self.root.after(0, lambda: self.log(f"發現 {count} 個新/更新牌組，開始處理..."))
                
                for deck_info in new_decks:
                    self.deck_queue.put(deck_info)
                
                self.root.after(0, self.update_progress_ui)
        
        except Exception as e:
            self.root.after(0, lambda: self.log(f"自動檢查發生錯誤: {e}"))
        
        # 3. 等待佇列處理完畢
        # 如果 new_decks 是空的，這裡會很快過去
        if self.total_decks_found == 0:
            pass # 沒事做
        else:
            self.root.after(0, lambda: self.log("等待 Worker 處理下載任務..."))
            self.deck_queue.join()

        # 4. 結束 Worker
        for _ in range(num_workers):
            self.deck_queue.put(None)
            
        self.root.after(0, lambda: self.log("=== 自動檢查/更新任務結束 ==="))
        self.reset_ui_state()

    # 手動開始按鈕的邏輯 (保留原有的「依頁數」功能，萬一你想強制爬歷史資料)
    def start_process(self):
        # ... (保留原有的 start_process 邏輯，不做變更) ...
        # 這邊可以直接複製你原本的代碼
        try:
            start_page = int(self.entry_start_page.get())
            end_page = int(self.entry_end_page.get())
            num_workers = int(self.entry_threads.get())
            
            if start_page > end_page:
                messagebox.showerror("錯誤", "起始頁不能大於結束頁")
                return
            if num_workers < 1:
                messagebox.showwarning("警告", "至少需要 1 個 Worker")
                return
        except ValueError:
            messagebox.showerror("錯誤", "請輸入有效的數字")
            return

        self.is_running = True
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        
        self.processed_count = 0
        self.success_count = 0
        self.total_decks_found = 0
        
        while not self.deck_queue.empty():
            try: self.deck_queue.get_nowait()
            except queue.Empty: break

        self.create_worker_widgets(num_workers)
        self.progress_bar.config(mode="determinate", maximum=100, value=0)
        
        self.log(f"=== 手動任務啟動: 頁數 {start_page}-{end_page} ===")
        # 注意：手動模式還是呼叫原本的 run_parallel_pipeline
        threading.Thread(target=self.run_parallel_pipeline, args=(start_page, end_page, num_workers), daemon=True).start()

    # 原有的手動爬取 pipeline (必須保留，供手動按鈕使用)
    def run_parallel_pipeline(self, start_page, end_page, num_workers):
        # ... (保留你原本的 run_parallel_pipeline 代碼) ...
        self.workers = []
        for i in range(num_workers):
            t = threading.Thread(target=self.worker_task, args=(i,), daemon=True)
            t.start()
            self.workers.append(t)
            
        self.root.after(0, lambda: self.log(f"已啟動 {num_workers} 個 Worker..."))
        
        for page in range(start_page, end_page + 1):
            if not self.is_running: break
            self.root.after(0, lambda p=page: self.lbl_status.config(text=f"正在掃描第 {p} 頁..."))
            try:
                new_decks = self.importer.crawl_deck_codes(page, page, status_callback=None)
                count = 0
                for deck_info in new_decks:
                    self.deck_queue.put(deck_info)
                    count += 1
                self.total_decks_found += count
                self.root.after(0, lambda p=page, c=count: self.log(f"第 {p} 頁加入 {c} 個任務"))
                self.root.after(0, self.update_progress_ui)
            except Exception as e:
                self.root.after(0, lambda e=e: self.log(f"掃描錯誤: {e}"))
        
        self.root.after(0, lambda: self.log("頁面掃描結束，等待 Worker..."))
        self.deck_queue.join()
        for _ in range(num_workers): self.deck_queue.put(None)
        self.root.after(0, lambda: self.log("=== 手動任務已完成 ==="))
        self.reset_ui_state()

    def stop_process(self):
        self.is_running = False
        self.log("正在停止...")
        self.lbl_status.config(text="正在停止...")
        self.btn_stop.config(state="disabled")

    def worker_task(self, worker_id):
        # ... (保留原本的 worker_task 代碼) ...
        while True:
            try:
                deck_info = self.deck_queue.get(timeout=1)
            except queue.Empty:
                if self.is_running:
                    self.update_worker_status(worker_id, "等待任務中...")
                    continue
                else:
                    break
            if deck_info is None:
                self.deck_queue.task_done()
                break
            if not self.is_running:
                self.deck_queue.task_done()
                break
            
            deck_code = deck_info['code']
            self.root.after(0, lambda: self.update_worker_status(worker_id, f"處理: {deck_code}"))
            
            try:
                success = self.importer.process_deck(deck_info, status_callback=None)
                if success: self.success_count += 1
            except Exception: pass 

            self.processed_count += 1
            self.root.after(0, self.update_progress_ui)
            self.deck_queue.task_done()
        self.root.after(0, lambda: self.update_worker_status(worker_id, "已停止"))

    def update_progress_ui(self):
        # ... (保留) ...
        if self.total_decks_found > 0:
            pct = (self.processed_count / self.total_decks_found) * 100
            self.progress_bar['value'] = pct
            self.lbl_stats.config(text=f"任務: {self.total_decks_found} | 已處理: {self.processed_count} | 成功: {self.success_count}")
        else:
            self.lbl_stats.config(text=f"正在掃描... | 已處理: {self.processed_count}")

    def reset_ui_state(self):
        self.root.after(0, lambda: self.btn_start.config(state="normal"))
        self.root.after(0, lambda: self.btn_stop.config(state="disabled"))
        self.root.after(0, lambda: self.lbl_status.config(text="任務待命"))

if __name__ == "__main__":
    root = tk.Tk()
    app = PokemonDeckGUI(root)
    root.mainloop()