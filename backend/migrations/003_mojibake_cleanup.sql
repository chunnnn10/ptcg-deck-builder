-- 003_mojibake_cleanup.sql
-- 任務三：「Pok矇mon」mojibake 清理（2026-08-01）
-- 前端 overlay.html / deck_manager.js 已修正為正確的 Pokémon；
-- 此檔為 DB 污染清理紀錄。2026-08-01 檢查生產庫：cards / jp_cards 污染數均為 0，
-- 無需執行 UPDATE（CHECK 約束擋下了非法值）。
--
-- 若日後再次發現污染，執行以下語句：
UPDATE cards SET card_type = 'Pokémon' WHERE card_type LIKE '%Pok矇mon%';
UPDATE jp_cards SET card_type = 'Pokémon' WHERE card_type LIKE '%Pok矇mon%';
