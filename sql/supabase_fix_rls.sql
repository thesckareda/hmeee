-- ══════════════════════════════════════════════════════════════════════════════
-- SUPABASE FIX — RLS + schema alignment for anon key usage
-- Run this in Supabase SQL Editor if you use SUPABASE_KEY (anon) instead of
-- SUPABASE_SERVICE_ROLE_KEY in your .env
-- Safe to re-run: DROP POLICY IF EXISTS throughout.
-- ══════════════════════════════════════════════════════════════════════════════

-- 1. call-recordings storage bucket — create if missing, make publicly readable
INSERT INTO storage.buckets (id, name, public)
VALUES ('call-recordings', 'call-recordings', true)
ON CONFLICT (id) DO UPDATE SET public = true;

DROP POLICY IF EXISTS "Public read recordings" ON storage.objects;
CREATE POLICY "Public read recordings"
    ON storage.objects FOR SELECT
    USING (bucket_id = 'call-recordings');

DROP POLICY IF EXISTS "Service role write recordings" ON storage.objects;
CREATE POLICY "Service role write recordings"
    ON storage.objects FOR INSERT
    WITH CHECK (bucket_id = 'call-recordings');

-- 2. call_transcripts — allow anon INSERT + SELECT (required for real-time & backfill)
ALTER TABLE call_transcripts ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow anon insert transcripts" ON call_transcripts;
CREATE POLICY "Allow anon insert transcripts"
    ON call_transcripts FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon select transcripts" ON call_transcripts;
CREATE POLICY "Allow anon select transcripts"
    ON call_transcripts FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS "Allow service_role all transcripts" ON call_transcripts;
CREATE POLICY "Allow service_role all transcripts"
    ON call_transcripts FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 3. active_calls — allow anon ALL (for rate-limit tracking, live status)
ALTER TABLE active_calls ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow anon all active_calls" ON active_calls;
CREATE POLICY "Allow anon all active_calls"
    ON active_calls FOR ALL TO anon USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service_role all active_calls" ON active_calls;
CREATE POLICY "Allow service_role all active_calls"
    ON active_calls FOR ALL TO service_role USING (true) WITH CHECK (true);

-- 4. call_logs — ensure anon SELECT + INSERT work
ALTER TABLE call_logs ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Enable all actions for service role" ON call_logs;
CREATE POLICY "Enable all actions for service role"
    ON call_logs FOR ALL TO anon, service_role USING (true) WITH CHECK (true);

-- 5. agents & agent-related tables — allow anon SELECT (dashboard reads)
ALTER TABLE agents ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversational_flows ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_configurations ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_call_actions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "Allow read anon agents" ON agents;
CREATE POLICY "Allow read anon agents"
    ON agents FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS "Allow read anon conversational_flows" ON conversational_flows;
CREATE POLICY "Allow read anon conversational_flows"
    ON conversational_flows FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS "Allow read anon call_configurations" ON call_configurations;
CREATE POLICY "Allow read anon call_configurations"
    ON call_configurations FOR SELECT TO anon USING (true);

DROP POLICY IF EXISTS "Allow read anon post_call_actions" ON post_call_actions;
CREATE POLICY "Allow read anon post_call_actions"
    ON post_call_actions FOR SELECT TO anon USING (true);

-- Anon write access (dashboard CRUD when SUPABASE_SERVICE_ROLE_KEY is not set)
DROP POLICY IF EXISTS "Allow anon insert agents" ON agents;
CREATE POLICY "Allow anon insert agents"
    ON agents FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon update agents" ON agents;
CREATE POLICY "Allow anon update agents"
    ON agents FOR UPDATE TO anon USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon delete agents" ON agents;
CREATE POLICY "Allow anon delete agents"
    ON agents FOR DELETE TO anon USING (true);

DROP POLICY IF EXISTS "Allow anon insert conversational_flows" ON conversational_flows;
CREATE POLICY "Allow anon insert conversational_flows"
    ON conversational_flows FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon update conversational_flows" ON conversational_flows;
CREATE POLICY "Allow anon update conversational_flows"
    ON conversational_flows FOR UPDATE TO anon USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon delete conversational_flows" ON conversational_flows;
CREATE POLICY "Allow anon delete conversational_flows"
    ON conversational_flows FOR DELETE TO anon USING (true);

DROP POLICY IF EXISTS "Allow anon insert call_configurations" ON call_configurations;
CREATE POLICY "Allow anon insert call_configurations"
    ON call_configurations FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon update call_configurations" ON call_configurations;
CREATE POLICY "Allow anon update call_configurations"
    ON call_configurations FOR UPDATE TO anon USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon delete call_configurations" ON call_configurations;
CREATE POLICY "Allow anon delete call_configurations"
    ON call_configurations FOR DELETE TO anon USING (true);

DROP POLICY IF EXISTS "Allow anon insert post_call_actions" ON post_call_actions;
CREATE POLICY "Allow anon insert post_call_actions"
    ON post_call_actions FOR INSERT TO anon WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon update post_call_actions" ON post_call_actions;
CREATE POLICY "Allow anon update post_call_actions"
    ON post_call_actions FOR UPDATE TO anon USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow anon delete post_call_actions" ON post_call_actions;
CREATE POLICY "Allow anon delete post_call_actions"
    ON post_call_actions FOR DELETE TO anon USING (true);

-- Keep service_role full access for agent tables
DROP POLICY IF EXISTS "Allow service_role all agents" ON agents;
CREATE POLICY "Allow service_role all agents"
    ON agents FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service_role all conversational_flows" ON conversational_flows;
CREATE POLICY "Allow service_role all conversational_flows"
    ON conversational_flows FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service_role all call_configurations" ON call_configurations;
CREATE POLICY "Allow service_role all call_configurations"
    ON call_configurations FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "Allow service_role all post_call_actions" ON post_call_actions;
CREATE POLICY "Allow service_role all post_call_actions"
    ON post_call_actions FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ══════════════════════════════════════════════════════════════════════════════
-- DONE.
-- After running this:
--   • Recordings are publicly readable
--   • Dashboard can create, edit, activate, and delete agents with SUPABASE_KEY (anon)
-- ══════════════════════════════════════════════════════════════════════════════