-- Migration v5: Add DeepSeek AI summary column to call_logs
-- Stores the full structured JSON from DeepSeek v4 Flash summariser

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS ai_summary JSONB;
