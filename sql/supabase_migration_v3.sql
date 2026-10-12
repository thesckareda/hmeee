-- ══════════════════════════════════════════════════════════════════════════════
-- SUPABASE MIGRATION v3 — Run once in Supabase SQL Editor
-- Locks down RLS: revokes all anon access from call_transcripts, active_calls,
-- and call_logs, then grants full access to service_role only.
-- Safe to re-run: DROP POLICY IF EXISTS before each CREATE POLICY.
-- ══════════════════════════════════════════════════════════════════════════════

-- 1. call_transcripts — revoke anon, grant service_role only
ALTER TABLE call_transcripts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow anon insert transcripts" ON call_transcripts;
DROP POLICY IF EXISTS "Allow anon select transcripts" ON call_transcripts;

DROP POLICY IF EXISTS "Allow service_role all transcripts" ON call_transcripts;
CREATE POLICY "Allow service_role all transcripts"
    ON call_transcripts FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 2. active_calls — revoke anon, grant service_role only
ALTER TABLE active_calls ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow anon all active_calls" ON active_calls;

DROP POLICY IF EXISTS "Allow service_role all active_calls" ON active_calls;
CREATE POLICY "Allow service_role all active_calls"
    ON active_calls FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 3. call_logs — ensure RLS enabled, grant service_role only
ALTER TABLE call_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow service_role all call_logs" ON call_logs;
CREATE POLICY "Allow service_role all call_logs"
    ON call_logs FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 4. Performance indexes for common query patterns
CREATE INDEX IF NOT EXISTS idx_call_logs_was_booked ON call_logs(was_booked) WHERE was_booked = true;
CREATE INDEX IF NOT EXISTS idx_call_logs_created_at ON call_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_transcripts_call_id ON call_transcripts(call_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_caller_phone ON call_logs(caller_phone);

-- ══════════════════════════════════════════════════════════════════════════════
-- DONE. You can re-run this script safely at any time.
-- Apply in Supabase SQL Editor (https://supabase.com/dashboard/project/_/sql).
-- db.py already uses SUPABASE_SERVICE_ROLE_KEY so all backend queries will
-- continue to work after anon access is revoked.
-- ══════════════════════════════════════════════════════════════════════════════
