-- 002_ai_access_default.sql
-- 任務一：AI 使用權放寬（2026-08-01）
-- 語義：users.ai_enabled = 1 預設可用（白名單反轉為黑名單的等價做法：
--       預設全開，admin 可在面板個別設 0 停用）
--
-- 執行方式（VPS）：
--   docker exec -i ptcg_db psql -U ptcg -d ptcg_db -f - < backend/migrations/002_ai_access_default.sql

-- 1) 現有用戶全部設為可用（若沿用既有 0 值，語義改動後會誤殺所有現有用戶）
UPDATE users SET ai_enabled = 1 WHERE ai_enabled = 0;

-- 2) 欄位預設改為 1：任何未指定 ai_enabled 的直接 INSERT 也預設可用
ALTER TABLE users ALTER COLUMN ai_enabled SET DEFAULT 1;
