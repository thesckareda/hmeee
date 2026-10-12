-- ══════════════════════════════════════════════════════════════════════════════
-- SUPABASE MIGRATION v4 — Voice AI Assistant Platform (multi-agent)
-- Run once in Supabase SQL Editor after v2 and v3 migrations.
-- Safe to re-run: IF NOT EXISTS / DROP POLICY IF EXISTS throughout.
-- ══════════════════════════════════════════════════════════════════════════════

-- ─── Helper: auto-update updated_at ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ─── 1. agents (core) ────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS agents (
    id                          UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    name                        TEXT        NOT NULL,
    description                 TEXT,
    purpose                     TEXT,
    system_prompt               TEXT        NOT NULL DEFAULT '',
    languages                   JSONB       NOT NULL DEFAULT '["en"]'::jsonb,
    llm_provider                TEXT        NOT NULL DEFAULT 'gemini',
    llm_model                   TEXT        NOT NULL DEFAULT 'gemini-2.0-flash',
    temperature                 NUMERIC(3,2) NOT NULL DEFAULT 0.70
                                    CHECK (temperature >= 0 AND temperature <= 2),
    stt_provider                TEXT        NOT NULL DEFAULT 'sarvam',
    stt_model                   TEXT,
    tts_provider                TEXT        NOT NULL DEFAULT 'sarvam',
    tts_voice_id                TEXT        NOT NULL DEFAULT 'kavya',
    welcome_message             TEXT,
    max_call_duration_seconds   INTEGER     NOT NULL DEFAULT 1800
                                    CHECK (max_call_duration_seconds > 0),
    is_active                   BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata                    JSONB       NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_agents_is_active   ON agents (is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_agents_created_at  ON agents (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agents_purpose     ON agents (purpose);

DROP TRIGGER IF EXISTS trg_agents_updated_at ON agents;
CREATE TRIGGER trg_agents_updated_at
    BEFORE UPDATE ON agents
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─── 2. conversational_flows ─────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS conversational_flows (
    id          UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_id    UUID        NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    sections    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    version     INTEGER     NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_id)
);

CREATE INDEX IF NOT EXISTS idx_conversational_flows_agent_id ON conversational_flows (agent_id);

DROP TRIGGER IF EXISTS trg_conversational_flows_updated_at ON conversational_flows;
CREATE TRIGGER trg_conversational_flows_updated_at
    BEFORE UPDATE ON conversational_flows
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─── 3. call_configurations ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS call_configurations (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_id            UUID        NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    silence_handling    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    end_call_rules      JSONB       NOT NULL DEFAULT '{}'::jsonb,
    transfer_routing    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    response_behavior   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    ambient_sound       JSONB       NOT NULL DEFAULT '{}'::jsonb,
    advanced_settings   JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_id)
);

CREATE INDEX IF NOT EXISTS idx_call_configurations_agent_id ON call_configurations (agent_id);

DROP TRIGGER IF EXISTS trg_call_configurations_updated_at ON call_configurations;
CREATE TRIGGER trg_call_configurations_updated_at
    BEFORE UPDATE ON call_configurations
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─── 4. post_call_actions ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS post_call_actions (
    id                      UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_id                UUID        NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    delivery_method         TEXT        NOT NULL DEFAULT 'webhook'
                                CHECK (delivery_method IN ('webhook', 'email', 'both', 'none')),
    trigger_statuses        JSONB       NOT NULL DEFAULT '["completed"]'::jsonb,
    include_summary         BOOLEAN     NOT NULL DEFAULT TRUE,
    include_full_transcript BOOLEAN     NOT NULL DEFAULT FALSE,
    include_sentiment       BOOLEAN     NOT NULL DEFAULT TRUE,
    include_extracted_info  BOOLEAN     NOT NULL DEFAULT TRUE,
    extracted_variables     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    webhook_url             TEXT,
    webhook_headers         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    email_recipients        JSONB       NOT NULL DEFAULT '[]'::jsonb,
    created_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_id)
);

CREATE INDEX IF NOT EXISTS idx_post_call_actions_agent_id ON post_call_actions (agent_id);

DROP TRIGGER IF EXISTS trg_post_call_actions_updated_at ON post_call_actions;
CREATE TRIGGER trg_post_call_actions_updated_at
    BEFORE UPDATE ON post_call_actions
    FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ─── 5. Extend call_logs for multi-agent + advanced filtering ────────────────
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS agent_id          UUID REFERENCES agents(id) ON DELETE SET NULL;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_status       TEXT DEFAULT 'completed';
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_direction    TEXT DEFAULT 'outbound';
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS channel_type      TEXT DEFAULT 'phone';
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_transferred  BOOLEAN DEFAULT FALSE;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS ended_by          TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS room_id           TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS from_number       TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS to_number         TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS bulk_call_id        UUID;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS extracted_info      JSONB DEFAULT '{}'::jsonb;

-- Optional columns referenced by v3 indexes (no-op if already present)
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS caller_phone      TEXT;
ALTER TABLE call_transcripts ADD COLUMN IF NOT EXISTS call_id    UUID;

CREATE INDEX IF NOT EXISTS idx_call_logs_agent_id        ON call_logs (agent_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_call_status     ON call_logs (call_status);
CREATE INDEX IF NOT EXISTS idx_call_logs_call_direction  ON call_logs (call_direction);
CREATE INDEX IF NOT EXISTS idx_call_logs_room_id         ON call_logs (room_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_bulk_call_id    ON call_logs (bulk_call_id) WHERE bulk_call_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_call_logs_from_number     ON call_logs (from_number);
CREATE INDEX IF NOT EXISTS idx_call_logs_to_number       ON call_logs (to_number);

-- ─── 6. Row Level Security (service_role only — matches v3) ─────────────────
ALTER TABLE agents               ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversational_flows ENABLE ROW LEVEL SECURITY;
ALTER TABLE call_configurations  ENABLE ROW LEVEL SECURITY;
ALTER TABLE post_call_actions    ENABLE ROW LEVEL SECURITY;

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

-- ─── 7. Seed: MGM College Admissions Counselor ───────────────────────────────
-- Fixed UUID so downstream code/tests can reference a stable default agent.
INSERT INTO agents (
    id,
    name,
    description,
    purpose,
    system_prompt,
    languages,
    llm_provider,
    llm_model,
    temperature,
    stt_provider,
    stt_model,
    tts_provider,
    tts_voice_id,
    welcome_message,
    max_call_duration_seconds,
    is_active,
    metadata
) VALUES (
    'a0000000-0000-4000-8000-000000000001',
    'MGM Admissions Counselor',
    'Friendly admissions counselor for MGM College — answers program questions, eligibility, fees, and campus visit scheduling.',
    'admissions',
    E'You are Priya, a warm and knowledgeable admissions counselor at MGM College of Engineering and Technology.\n\nYour goals:\n1. Welcome prospective students and parents with empathy and enthusiasm.\n2. Explain undergraduate and postgraduate programs (CSE, ECE, Mechanical, MBA, etc.).\n3. Clarify eligibility (12th percentage, entrance exams, lateral entry).\n4. Share fee structure, scholarships, and hostel facilities at a high level.\n5. Capture lead details: name, phone, city, program of interest, preferred intake year.\n6. Offer to schedule a campus tour or callback with a senior counselor.\n\nStyle:\n- Speak clearly in English or Hindi based on caller preference.\n- Keep answers concise (2–3 sentences) unless the caller asks for detail.\n- Never invent exact fee figures — say you will share the official brochure or arrange a callback.\n- If unsure, offer to connect them with the admissions desk.\n- End positively and thank them for considering MGM College.',
    '["en", "hi"]'::jsonb,
    'gemini',
    'gemini-2.0-flash',
    0.65,
    'sarvam',
    'saarika',
    'sarvam',
    'kavya',
    'Namaste! Welcome to MGM College admissions. I am Priya, your admissions counselor. How may I help you today — programs, eligibility, fees, or a campus visit?',
    1200,
    TRUE,
    '{"use_cases": ["admissions", "lead_generation"], "knowledge_base_files": 0, "search_enabled": false}'::jsonb
)
ON CONFLICT (id) DO NOTHING;

INSERT INTO conversational_flows (agent_id, sections, version)
SELECT
    'a0000000-0000-4000-8000-000000000001',
    '[
        {"id": "intro", "title": "Introduction & Rapport", "content": "Greet warmly, ask their name and whether they are a student or parent. Confirm which program or field they are exploring.", "enabled": true, "order": 1},
        {"id": "programs", "title": "Programs Overview", "content": "Describe relevant UG/PG programs, duration, and key highlights. Ask which branch interests them most.", "enabled": true, "order": 2},
        {"id": "eligibility", "title": "Eligibility & Entrance", "content": "Explain minimum marks, accepted entrance exams (JEE/state), and lateral entry options if applicable.", "enabled": true, "order": 3},
        {"id": "fees", "title": "Fees & Scholarships", "content": "Give ballpark fee ranges and mention merit scholarships. Offer to email the official fee structure.", "enabled": true, "order": 4},
        {"id": "campus", "title": "Campus & Hostel", "content": "Briefly describe campus facilities, labs, placements snapshot, and hostel availability.", "enabled": true, "order": 5},
        {"id": "lead_capture", "title": "Lead Capture", "content": "Collect full name, phone, city, program interest, and preferred intake year. Confirm details back to the caller.", "enabled": true, "order": 6},
        {"id": "next_steps", "title": "Next Steps & Close", "content": "Offer campus tour booking or senior counselor callback. Thank them and summarize next steps.", "enabled": true, "order": 7}
    ]'::jsonb,
    1
WHERE EXISTS (SELECT 1 FROM agents WHERE id = 'a0000000-0000-4000-8000-000000000001')
ON CONFLICT (agent_id) DO NOTHING;

INSERT INTO call_configurations (
    agent_id,
    silence_handling,
    end_call_rules,
    transfer_routing,
    response_behavior,
    ambient_sound,
    advanced_settings
)
SELECT
    'a0000000-0000-4000-8000-000000000001',
    '{"enabled": true, "timeout_seconds": 8, "max_prompts": 2, "prompt_message": "Are you still there? I am happy to help with admissions questions.", "end_call_after_max_prompts": false}'::jsonb,
    '{"max_duration_seconds": 1200, "end_phrases": ["goodbye", "thank you bye", "that is all"], "voicemail_detection": true, "voicemail_message": "Thank you for calling MGM College admissions. We will call you back shortly."}'::jsonb,
    '{"enabled": true, "transfer_number": "", "transfer_phrases": ["speak to a human", "transfer me", "admissions desk"], "warm_transfer": false}'::jsonb,
    '{"interruption_sensitivity": "medium", "max_response_sentences": 3, "filler_phrases_enabled": true, "backchannel_enabled": true}'::jsonb,
    '{"enabled": false, "sound_type": "office_ambient", "volume": 0.15}'::jsonb,
    '{"record_calls": true, "noise_cancellation": true, "language_auto_detect": true}'::jsonb
WHERE EXISTS (SELECT 1 FROM agents WHERE id = 'a0000000-0000-4000-8000-000000000001')
ON CONFLICT (agent_id) DO NOTHING;

INSERT INTO post_call_actions (
    agent_id,
    delivery_method,
    trigger_statuses,
    include_summary,
    include_full_transcript,
    include_sentiment,
    include_extracted_info,
    extracted_variables,
    webhook_url,
    email_recipients
)
SELECT
    'a0000000-0000-4000-8000-000000000001',
    'webhook',
    '["completed", "transferred", "voicemail"]'::jsonb,
    TRUE,
    FALSE,
    TRUE,
    TRUE,
    '[
        {"name": "caller_name", "description": "Full name of the prospective student or parent", "how_to_extract": "From conversation when they introduce themselves or when asked"},
        {"name": "program_interest", "description": "Branch or program they are interested in", "how_to_extract": "Infer from programs discussed — CSE, ECE, MBA, etc."},
        {"name": "intake_year", "description": "Target admission year", "how_to_extract": "Ask or infer from phrases like next year or 2026 batch"},
        {"name": "city", "description": "City or state of the caller", "how_to_extract": "Ask during lead capture section"},
        {"name": "callback_requested", "description": "Whether they want a human callback", "how_to_extract": "true if they ask to speak to someone or schedule a tour"}
    ]'::jsonb,
    NULL,
    '[]'::jsonb
WHERE EXISTS (SELECT 1 FROM agents WHERE id = 'a0000000-0000-4000-8000-000000000001')
ON CONFLICT (agent_id) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- DONE. Verify with:
--   SELECT table_name FROM information_schema.tables
--   WHERE table_schema = 'public'
--     AND table_name IN ('agents','conversational_flows','call_configurations','post_call_actions');
--   SELECT id, name, purpose FROM agents;
-- ══════════════════════════════════════════════════════════════════════════════