-- ══════════════════════════════════════════════════════════════════════════════
-- SPEAKSY — COMPLETE SUPABASE SCHEMA (final)
-- ══════════════════════════════════════════════════════════════════════════════
--
-- Run this ONCE in Supabase SQL Editor on a fresh project, or on an existing
-- project to align missing tables/columns/policies.
--
-- Safe to re-run: uses IF NOT EXISTS / DROP … IF EXISTS throughout.
--
-- Covers:
--   • Calls (logs, live transcripts, active calls, recordings storage)
--   • Multi-agent platform (agents, flows, call config, post-call actions)
--   • Outbound campaigns
--   • Leads, CRM actions, property interest, objections
--   • PIM listings catalog + PIN → PIM mapping
--   • Knowledge base documents + per-agent file links
--   • App settings, KV cache, delivery & CRM sync logs
--   • RLS for service_role (production) + anon (dashboard dev)
--
-- After running:
--   1. Set SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY in .env (recommended prod)
--   2. Or SUPABASE_KEY (anon) + this schema's anon policies for local dev
--   3. Verify: SELECT table_name FROM information_schema.tables
--              WHERE table_schema = 'public' ORDER BY 1;
-- ══════════════════════════════════════════════════════════════════════════════

-- ─── Extensions ───────────────────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS "pgcrypto";
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- ─── Helper: auto-update updated_at ──────────────────────────────────────────
CREATE OR REPLACE FUNCTION set_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ─── Helper: ensure only one active agent ────────────────────────────────────
CREATE OR REPLACE FUNCTION enforce_single_active_agent()
RETURNS TRIGGER AS $$
BEGIN
    IF NEW.is_active = TRUE THEN
        UPDATE agents SET is_active = FALSE, updated_at = NOW()
        WHERE id <> NEW.id AND is_active = TRUE;
    END IF;
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- ══════════════════════════════════════════════════════════════════════════════
-- 1. AGENTS PLATFORM
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS agents (
    id                          UUID            DEFAULT gen_random_uuid() PRIMARY KEY,
    name                        TEXT            NOT NULL,
    description                 TEXT,
    purpose                     TEXT,
    system_prompt               TEXT            NOT NULL DEFAULT '',
    languages                   JSONB           NOT NULL DEFAULT '["en"]'::jsonb,
    llm_provider                TEXT            NOT NULL DEFAULT 'gemini',
    llm_model                   TEXT            NOT NULL DEFAULT 'gemini-2.0-flash',
    temperature                 NUMERIC(3,2)    NOT NULL DEFAULT 0.70
                                    CHECK (temperature >= 0 AND temperature <= 2),
    stt_provider                TEXT            NOT NULL DEFAULT 'sarvam',
    stt_model                   TEXT,
    tts_provider                TEXT            NOT NULL DEFAULT 'sarvam',
    tts_voice_id                TEXT            NOT NULL DEFAULT 'kavya',
    welcome_message             TEXT,
    max_call_duration_seconds   INTEGER         NOT NULL DEFAULT 1800
                                    CHECK (max_call_duration_seconds > 0),
    is_active                   BOOLEAN         NOT NULL DEFAULT TRUE,
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    metadata                    JSONB           NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE agents IS 'Voice AI assistants — LLM/STT/TTS config and metadata';
COMMENT ON COLUMN agents.metadata IS 'use_cases, knowledge_base_files_list, search_enabled, integrations, knowledge_base path';

CREATE TABLE IF NOT EXISTS conversational_flows (
    id          UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_id    UUID        NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    sections    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    version     INTEGER     NOT NULL DEFAULT 1 CHECK (version >= 1),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_id)
);

COMMENT ON COLUMN conversational_flows.sections IS '[{id, title, content, enabled, order}]';

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

COMMENT ON COLUMN post_call_actions.extracted_variables IS '[{name, description, how_to_extract}]';

-- ══════════════════════════════════════════════════════════════════════════════
-- 2. OUTBOUND CAMPAIGNS
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS outbound_campaigns (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    name            TEXT        NOT NULL DEFAULT '',
    agent_id        UUID        REFERENCES agents(id) ON DELETE SET NULL,
    status          TEXT        NOT NULL DEFAULT 'draft'
                        CHECK (status IN ('draft', 'running', 'paused', 'completed', 'cancelled')),
    delay_seconds   INTEGER     NOT NULL DEFAULT 0 CHECK (delay_seconds >= 0 AND delay_seconds <= 120),
    max_concurrent  INTEGER     NOT NULL DEFAULT 1 CHECK (max_concurrent >= 1),
    window_start    TEXT        NOT NULL DEFAULT '09:00',
    window_end      TEXT        NOT NULL DEFAULT '20:00',
    timezone        TEXT        NOT NULL DEFAULT 'Asia/Kolkata',
    respect_window  BOOLEAN     NOT NULL DEFAULT TRUE,
    total_numbers   INTEGER     NOT NULL DEFAULT 0,
    dispatched      INTEGER     NOT NULL DEFAULT 0,
    succeeded       INTEGER     NOT NULL DEFAULT 0,
    failed          INTEGER     NOT NULL DEFAULT 0,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ
);

COMMENT ON TABLE outbound_campaigns IS 'Bulk outbound dial campaigns — maps to call_logs.bulk_call_id';

CREATE TABLE IF NOT EXISTS campaign_numbers (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    campaign_id     UUID        NOT NULL REFERENCES outbound_campaigns(id) ON DELETE CASCADE,
    phone           TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'dispatched', 'completed', 'failed', 'skipped')),
    dispatch_id     TEXT,
    error_message   TEXT,
    call_log_id     BIGINT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (campaign_id, phone)
);

-- ══════════════════════════════════════════════════════════════════════════════
-- 3. PIM LISTINGS + PIN → PIM MAPPING
-- ══════════════════════════════════════════════════════════════════════════════
--
-- PIM  = Product Information Management catalog (canonical listing records)
-- PIN  = Short external reference (IVR code, builder portal ID, verbal shorthand)
--        mapped to exactly one PIM listing for voice agent lookup.
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS listings (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    listing_id      TEXT        NOT NULL UNIQUE,
    pim_sku         TEXT        UNIQUE,
    title           TEXT        NOT NULL DEFAULT '',
    area            TEXT,
    city            TEXT        DEFAULT 'Hyderabad',
    property_type   TEXT,
    bedrooms        INTEGER,
    bathrooms       INTEGER,
    sqft_min        INTEGER,
    sqft_max        INTEGER,
    price_min       BIGINT,
    price_max       BIGINT,
    currency        TEXT        NOT NULL DEFAULT 'INR',
    status          TEXT        NOT NULL DEFAULT 'active'
                        CHECK (status IN ('active', 'reserved', 'sold', 'inactive', 'draft')),
    facts           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    amenities       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    tags            TEXT[]      NOT NULL DEFAULT '{}',
    source          TEXT,
    external_url    TEXT,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE listings IS 'PIM property catalog — canonical listing records for voice + CRM';
COMMENT ON COLUMN listings.listing_id IS 'Human-readable ID e.g. HYD-KOK-001, MUM-042';
COMMENT ON COLUMN listings.facts IS 'Price history, facing, floor, possession, builder name, etc.';

CREATE TABLE IF NOT EXISTS pin_to_pim_mappings (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    pin             TEXT        NOT NULL,
    pim_listing_id  TEXT        NOT NULL REFERENCES listings(listing_id) ON UPDATE CASCADE ON DELETE CASCADE,
    source          TEXT        NOT NULL DEFAULT 'manual'
                        CHECK (source IN ('manual', 'ivr', 'builder_portal', 'crm', 'csv_import', 'api')),
    label           TEXT,
    description     TEXT,
    is_active       BOOLEAN     NOT NULL DEFAULT TRUE,
    priority        INTEGER     NOT NULL DEFAULT 0,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (pin, source)
);

COMMENT ON TABLE pin_to_pim_mappings IS 'Maps short PIN codes to canonical PIM listing_id for agent/IVR resolution';
COMMENT ON COLUMN pin_to_pim_mappings.pin IS 'Short code: numeric (4521), alpha (KOK3), or portal ref';

CREATE TABLE IF NOT EXISTS campaign_listings (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    campaign_id     UUID        NOT NULL REFERENCES outbound_campaigns(id) ON DELETE CASCADE,
    listing_id      TEXT        NOT NULL REFERENCES listings(listing_id) ON UPDATE CASCADE ON DELETE CASCADE,
    sort_order      INTEGER     NOT NULL DEFAULT 0,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (campaign_id, listing_id)
);

COMMENT ON TABLE campaign_listings IS 'Listings attached to an outbound campaign (replaces kv campaign:{id}:listings)';

-- ══════════════════════════════════════════════════════════════════════════════
-- 4. CALLS
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS call_logs (
    id                  BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT timezone('utc'::text, now()),
    phone_number        TEXT,
    caller_phone        TEXT,
    caller_name         TEXT,
    caller_email        TEXT,
    duration_seconds    INTEGER,
    transcript          TEXT,
    summary             TEXT,
    recording_url       TEXT,
    sentiment           TEXT,
    estimated_cost_usd  NUMERIC(10,5),
    call_date           DATE,
    call_hour           INTEGER CHECK (call_hour IS NULL OR (call_hour >= 0 AND call_hour <= 23)),
    call_day_of_week    TEXT,
    was_booked          BOOLEAN NOT NULL DEFAULT FALSE,
    interrupt_count     INTEGER NOT NULL DEFAULT 0,
    audio_codec         TEXT,
    call_purpose        TEXT,
    call_summary        TEXT,
    appointment_time    TIMESTAMPTZ,
    tts_voice           TEXT,
    agent_id            UUID REFERENCES agents(id) ON DELETE SET NULL,
    call_status         TEXT NOT NULL DEFAULT 'completed'
                            CHECK (call_status IN (
                                'ringing', 'active', 'completed', 'failed', 'no_answer',
                                'voicemail', 'transferred', 'booked', 'archived', 'cancelled'
                            )),
    call_direction      TEXT NOT NULL DEFAULT 'outbound'
                            CHECK (call_direction IN ('inbound', 'outbound')),
    channel_type        TEXT NOT NULL DEFAULT 'phone'
                            CHECK (channel_type IN ('phone', 'web', 'sip', 'whatsapp')),
    call_transferred    BOOLEAN NOT NULL DEFAULT FALSE,
    ended_by            TEXT CHECK (ended_by IS NULL OR ended_by IN ('caller', 'assistant', 'system', 'timeout')),
    room_id             TEXT,
    from_number         TEXT,
    to_number           TEXT,
    bulk_call_id        UUID REFERENCES outbound_campaigns(id) ON DELETE SET NULL,
    extracted_info      JSONB NOT NULL DEFAULT '{}'::jsonb,
    ai_summary          JSONB,
    migrated_to_re_memory BOOLEAN,
    metadata            JSONB NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE call_logs IS 'Completed call records — primary analytics + CRM source';
COMMENT ON COLUMN call_logs.extracted_info IS 'Post-call variable extraction keyed by variable name';
COMMENT ON COLUMN call_logs.ai_summary IS 'DeepSeek structured summary JSON';
COMMENT ON COLUMN call_logs.bulk_call_id IS 'FK to outbound_campaigns.id';

CREATE TABLE IF NOT EXISTS call_transcripts (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    call_room_id    TEXT        NOT NULL,
    call_id         BIGINT      REFERENCES call_logs(id) ON DELETE SET NULL,
    phone           TEXT,
    role            TEXT        CHECK (role IN ('user', 'assistant')),
    content         TEXT,
    sequence_num    INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE call_transcripts IS 'Streaming transcript chunks during live calls';

CREATE TABLE IF NOT EXISTS active_calls (
    room_id         TEXT        PRIMARY KEY,
    phone           TEXT,
    caller_name     TEXT,
    status          TEXT        NOT NULL DEFAULT 'ringing'
                        CHECK (status IN ('ringing', 'active', 'completed', 'failed')),
    agent_id        UUID        REFERENCES agents(id) ON DELETE SET NULL,
    campaign_id     UUID        REFERENCES outbound_campaigns(id) ON DELETE SET NULL,
    from_number     TEXT,
    to_number       TEXT,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb
);

COMMENT ON TABLE active_calls IS 'Live call tracking for rate limits and dashboard status';

CREATE TABLE IF NOT EXISTS call_listing_mentions (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    call_id         BIGINT      NOT NULL REFERENCES call_logs(id) ON DELETE CASCADE,
    listing_id      TEXT        NOT NULL REFERENCES listings(listing_id) ON UPDATE CASCADE ON DELETE CASCADE,
    mention_type    TEXT        NOT NULL DEFAULT 'discussed'
                        CHECK (mention_type IN ('discussed', 'sent', 'viewed', 'offered', 'rejected', 'booked')),
    pin_used        TEXT,
    context         TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE call_listing_mentions IS 'Which listings were discussed/sent during a call';

-- ══════════════════════════════════════════════════════════════════════════════
-- 5. LEADS & CRM
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS leads (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    lead_id             UUID        NOT NULL UNIQUE,
    phone               TEXT        NOT NULL UNIQUE,
    stable_name         TEXT        NOT NULL DEFAULT '',
    role                TEXT        NOT NULL DEFAULT 'buyer'
                            CHECK (role IN ('buyer', 'seller', 'renter', 'investor')),
    budget_min          INTEGER,
    budget_max          INTEGER,
    preferred_areas     JSONB       NOT NULL DEFAULT '[]'::jsonb,
    excluded_areas      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    must_haves          JSONB       NOT NULL DEFAULT '[]'::jsonb,
    deal_breakers       JSONB       NOT NULL DEFAULT '[]'::jsonb,
    timeline_weeks      INTEGER,
    loan_status         TEXT        NOT NULL DEFAULT 'unknown'
                            CHECK (loan_status IN ('pre_approved', 'exploring', 'cash', 'unknown')),
    decision_factors    JSONB       NOT NULL DEFAULT '[]'::jsonb,
    intent_score        NUMERIC(4,2) NOT NULL DEFAULT 5.0,
    deal_stage          TEXT        NOT NULL DEFAULT 'cold'
                            CHECK (deal_stage IN ('cold', 'aware', 'interested', 'viewing', 'hot', 'offer', 'closed', 'lost')),
    assigned_agent      TEXT,
    assigned_agent_id   UUID        REFERENCES agents(id) ON DELETE SET NULL,
    call_count          INTEGER     NOT NULL DEFAULT 0,
    last_contact_at     TIMESTAMPTZ,
    language_pref       TEXT        NOT NULL DEFAULT 'en'
                            CHECK (language_pref IN ('en', 'hi', 'hinglish', 'tamil', 'telugu')),
    notes               TEXT        NOT NULL DEFAULT '',
    metadata            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE leads IS 'Normalized lead profiles (migrates from re_voice_memory filesystem)';

CREATE TABLE IF NOT EXISTS lead_property_signals (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    lead_id             UUID        NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    phone               TEXT        NOT NULL,
    listing_id          TEXT        NOT NULL REFERENCES listings(listing_id) ON UPDATE CASCADE ON DELETE CASCADE,
    signal              TEXT        NOT NULL
                            CHECK (signal IN ('liked', 'rejected', 'viewed', 'offered', 'sent', 'enquired')),
    price_reaction      TEXT        NOT NULL DEFAULT 'no_reaction'
                            CHECK (price_reaction IN ('positive', 'negative', 'neutral', 'no_reaction')),
    features_liked      JSONB       NOT NULL DEFAULT '[]'::jsonb,
    objections_raised   JSONB       NOT NULL DEFAULT '[]'::jsonb,
    price_at_signal     INTEGER,
    call_id             BIGINT      REFERENCES call_logs(id) ON DELETE SET NULL,
    call_date           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE lead_property_signals IS 'Per-lead property interest graph (re_voice_memory/property_graph)';

CREATE TABLE IF NOT EXISTS lead_objections (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    lead_id             UUID        NOT NULL REFERENCES leads(id) ON DELETE CASCADE,
    phone               TEXT        NOT NULL,
    objection_type      TEXT        NOT NULL
                            CHECK (objection_type IN ('price', 'location', 'size', 'amenity', 'timing', 'agent_trust', 'process', 'other')),
    listing_id          TEXT        REFERENCES listings(listing_id) ON UPDATE CASCADE ON DELETE SET NULL,
    raw_phrase          TEXT        NOT NULL DEFAULT '',
    script_used         TEXT        NOT NULL DEFAULT '',
    outcome             TEXT        NOT NULL DEFAULT 'unresolved'
                            CHECK (outcome IN ('resolved', 'unresolved', 'escalated', 'deferred')),
    call_id             BIGINT      REFERENCES call_logs(id) ON DELETE SET NULL,
    call_count_at_time  INTEGER     NOT NULL DEFAULT 0,
    call_date           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS lead_actions (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    call_id         TEXT        NOT NULL,
    call_log_id     BIGINT      REFERENCES call_logs(id) ON DELETE SET NULL,
    lead_id         UUID        REFERENCES leads(id) ON DELETE SET NULL,
    action          TEXT        NOT NULL
                        CHECK (action IN (
                            'send_whatsapp', 'schedule_site_visit', 'mark_booked',
                            'escalate_human', 'archive'
                        )),
    performed_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    performed_by    TEXT,
    notes           TEXT        NOT NULL DEFAULT ''
);

COMMENT ON TABLE lead_actions IS 'Dashboard CTA actions on hot leads';

-- ══════════════════════════════════════════════════════════════════════════════
-- 6. KNOWLEDGE BASE
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS knowledge_documents (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    document_id         TEXT        NOT NULL UNIQUE,
    doc_type            TEXT        NOT NULL DEFAULT 'faq'
                            CHECK (doc_type IN (
                                'company_info', 'office_info', 'property_listing', 'faq',
                                'process', 'policy', 'script', 'other'
                            )),
    title               TEXT        NOT NULL,
    content             TEXT        NOT NULL DEFAULT '',
    department          TEXT,
    language            TEXT        NOT NULL DEFAULT 'en',
    tags                JSONB       NOT NULL DEFAULT '[]'::jsonb,
    metadata            JSONB       NOT NULL DEFAULT '{}'::jsonb,
    embedding_id        TEXT,
    is_active           BOOLEAN     NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE knowledge_documents IS 'Grounding documents — sync from knowledge_data/*.json or uploads';

CREATE TABLE IF NOT EXISTS agent_knowledge_files (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_id        UUID        NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    file_name       TEXT        NOT NULL,
    file_path       TEXT,
    storage_path    TEXT,
    file_size       BIGINT,
    mime_type       TEXT,
    config_preset   TEXT        DEFAULT 'config.json',
    search_enabled  BOOLEAN     NOT NULL DEFAULT FALSE,
    status          TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'processing', 'indexed', 'failed')),
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    added_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE agent_knowledge_files IS 'Per-agent uploaded knowledge files (UI Knowledge Base tab)';

CREATE TABLE IF NOT EXISTS agent_knowledge_assignments (
    id                  UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    agent_id            UUID        NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    document_id         TEXT        NOT NULL REFERENCES knowledge_documents(document_id) ON UPDATE CASCADE ON DELETE CASCADE,
    knowledge_data_path TEXT,
    config_preset       TEXT        DEFAULT 'config.json',
    loaded_at           TIMESTAMPTZ,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (agent_id, document_id)
);

-- ══════════════════════════════════════════════════════════════════════════════
-- 7. APP INFRASTRUCTURE
-- ══════════════════════════════════════════════════════════════════════════════

CREATE TABLE IF NOT EXISTS app_settings (
    key             TEXT        PRIMARY KEY,
    value           JSONB       NOT NULL DEFAULT '{}'::jsonb,
    description     TEXT,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by      TEXT
);

COMMENT ON TABLE app_settings IS 'Dashboard settings: business_hours, tone_preset, feature flags';

CREATE TABLE IF NOT EXISTS app_kv_store (
    key             TEXT        PRIMARY KEY,
    value           JSONB       NOT NULL,
    expires_at      TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE app_kv_store IS 'Server-side KV (replaces Redis/SQLite): lead:{phone}:*, listing:{id}:facts, campaign:{id}:listings';

CREATE TABLE IF NOT EXISTS post_call_deliveries (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    call_id         BIGINT      NOT NULL REFERENCES call_logs(id) ON DELETE CASCADE,
    agent_id        UUID        REFERENCES agents(id) ON DELETE SET NULL,
    delivery_method TEXT        NOT NULL,
    destination     TEXT,
    status          TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'sent', 'failed', 'skipped')),
    http_status     INTEGER,
    response_body   TEXT,
    payload         JSONB       NOT NULL DEFAULT '{}'::jsonb,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    delivered_at    TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS crm_sync_log (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    lead_id         UUID        REFERENCES leads(id) ON DELETE SET NULL,
    phone           TEXT,
    call_id         BIGINT      REFERENCES call_logs(id) ON DELETE SET NULL,
    crm_type        TEXT        NOT NULL DEFAULT 'webhook'
                        CHECK (crm_type IN ('hubspot', 'salesforce', 'webhook', 'none')),
    action          TEXT        NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'success', 'failed')),
    request_payload JSONB       NOT NULL DEFAULT '{}'::jsonb,
    response_payload JSONB,
    error_message   TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS calendar_bookings (
    id              UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    call_id         BIGINT      REFERENCES call_logs(id) ON DELETE SET NULL,
    lead_id         UUID        REFERENCES leads(id) ON DELETE SET NULL,
    phone           TEXT        NOT NULL,
    listing_id      TEXT        REFERENCES listings(listing_id) ON UPDATE CASCADE ON DELETE SET NULL,
    scheduled_at    TIMESTAMPTZ NOT NULL,
    provider        TEXT        NOT NULL DEFAULT 'cal.com'
                        CHECK (provider IN ('cal.com', 'google_calendar', 'manual')),
    external_id     TEXT,
    status          TEXT        NOT NULL DEFAULT 'scheduled'
                        CHECK (status IN ('scheduled', 'confirmed', 'cancelled', 'completed', 'no_show')),
    notes           TEXT,
    metadata        JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ══════════════════════════════════════════════════════════════════════════════
-- 7b. MIGRATION ALIGNMENT (existing projects — add columns before indexes)
-- ══════════════════════════════════════════════════════════════════════════════
-- Legacy projects often have call_logs from supabase_setup.sql with fewer columns.
-- Indexes in section 8 reference these — add them here first (safe to re-run).

ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS caller_phone        TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS caller_email        TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS sentiment           TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS estimated_cost_usd  NUMERIC(10,5);
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_date           DATE;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_hour           INTEGER;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_day_of_week    TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS was_booked          BOOLEAN DEFAULT FALSE;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS interrupt_count     INTEGER DEFAULT 0;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS audio_codec         TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_purpose        TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_summary        TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS appointment_time    TIMESTAMPTZ;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS tts_voice           TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS agent_id            UUID;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_status         TEXT DEFAULT 'completed';
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_direction      TEXT DEFAULT 'outbound';
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS channel_type        TEXT DEFAULT 'phone';
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS call_transferred    BOOLEAN DEFAULT FALSE;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS ended_by            TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS room_id             TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS from_number         TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS to_number           TEXT;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS bulk_call_id        UUID;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS extracted_info      JSONB DEFAULT '{}'::jsonb;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS ai_summary          JSONB;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS migrated_to_re_memory BOOLEAN;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS metadata            JSONB DEFAULT '{}'::jsonb;

ALTER TABLE call_transcripts ADD COLUMN IF NOT EXISTS call_id      BIGINT;
ALTER TABLE call_transcripts ADD COLUMN IF NOT EXISTS sequence_num INTEGER;

ALTER TABLE active_calls ADD COLUMN IF NOT EXISTS agent_id         UUID;
ALTER TABLE active_calls ADD COLUMN IF NOT EXISTS campaign_id      UUID;
ALTER TABLE active_calls ADD COLUMN IF NOT EXISTS from_number      TEXT;
ALTER TABLE active_calls ADD COLUMN IF NOT EXISTS to_number        TEXT;
ALTER TABLE active_calls ADD COLUMN IF NOT EXISTS metadata         JSONB DEFAULT '{}'::jsonb;

ALTER TABLE lead_actions ADD COLUMN IF NOT EXISTS call_log_id      BIGINT;
ALTER TABLE lead_actions ADD COLUMN IF NOT EXISTS lead_id          UUID;

DO $$ BEGIN
    ALTER TABLE call_logs
        ADD CONSTRAINT fk_call_logs_agent
        FOREIGN KEY (agent_id) REFERENCES agents(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
    ALTER TABLE call_logs
        ADD CONSTRAINT fk_call_logs_campaign
        FOREIGN KEY (bulk_call_id) REFERENCES outbound_campaigns(id) ON DELETE SET NULL;
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════
-- 8. INDEXES
-- ══════════════════════════════════════════════════════════════════════════════

-- agents
CREATE INDEX IF NOT EXISTS idx_agents_is_active    ON agents (is_active) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_agents_created_at   ON agents (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_agents_purpose      ON agents (purpose);

-- agent children
CREATE INDEX IF NOT EXISTS idx_conversational_flows_agent_id ON conversational_flows (agent_id);
CREATE INDEX IF NOT EXISTS idx_call_configurations_agent_id  ON call_configurations (agent_id);
CREATE INDEX IF NOT EXISTS idx_post_call_actions_agent_id    ON post_call_actions (agent_id);

-- campaigns
CREATE INDEX IF NOT EXISTS idx_outbound_campaigns_status     ON outbound_campaigns (status);
CREATE INDEX IF NOT EXISTS idx_outbound_campaigns_agent_id   ON outbound_campaigns (agent_id);
CREATE INDEX IF NOT EXISTS idx_outbound_campaigns_created_at ON outbound_campaigns (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_campaign_numbers_campaign_id  ON campaign_numbers (campaign_id);
CREATE INDEX IF NOT EXISTS idx_campaign_numbers_phone        ON campaign_numbers (phone);
CREATE INDEX IF NOT EXISTS idx_campaign_listings_campaign_id ON campaign_listings (campaign_id);

-- listings + PIN mapping
CREATE INDEX IF NOT EXISTS idx_listings_listing_id   ON listings (listing_id);
CREATE INDEX IF NOT EXISTS idx_listings_area         ON listings (area);
CREATE INDEX IF NOT EXISTS idx_listings_status       ON listings (status) WHERE status = 'active';
CREATE INDEX IF NOT EXISTS idx_pin_to_pim_pin        ON pin_to_pim_mappings (pin) WHERE is_active = TRUE;
CREATE INDEX IF NOT EXISTS idx_pin_to_pim_pim        ON pin_to_pim_mappings (pim_listing_id);

-- call_logs
CREATE INDEX IF NOT EXISTS idx_call_logs_phone           ON call_logs (phone_number);
CREATE INDEX IF NOT EXISTS idx_call_logs_caller_phone    ON call_logs (caller_phone);
CREATE INDEX IF NOT EXISTS idx_call_logs_created_at      ON call_logs (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_call_logs_was_booked      ON call_logs (was_booked) WHERE was_booked = TRUE;
CREATE INDEX IF NOT EXISTS idx_call_logs_agent_id        ON call_logs (agent_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_call_status     ON call_logs (call_status);
CREATE INDEX IF NOT EXISTS idx_call_logs_call_direction  ON call_logs (call_direction);
CREATE INDEX IF NOT EXISTS idx_call_logs_room_id         ON call_logs (room_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_bulk_call_id    ON call_logs (bulk_call_id) WHERE bulk_call_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_call_logs_from_number     ON call_logs (from_number);
CREATE INDEX IF NOT EXISTS idx_call_logs_to_number       ON call_logs (to_number);
CREATE INDEX IF NOT EXISTS idx_call_logs_migrated        ON call_logs (migrated_to_re_memory) WHERE migrated_to_re_memory IS NULL;

-- transcripts + active
CREATE INDEX IF NOT EXISTS idx_call_transcripts_room     ON call_transcripts (call_room_id);
CREATE INDEX IF NOT EXISTS idx_call_transcripts_phone    ON call_transcripts (phone);
CREATE INDEX IF NOT EXISTS idx_call_transcripts_call_id  ON call_transcripts (call_id);
CREATE INDEX IF NOT EXISTS idx_active_calls_status       ON active_calls (status);
CREATE INDEX IF NOT EXISTS idx_call_listing_mentions_call ON call_listing_mentions (call_id);

-- leads
CREATE INDEX IF NOT EXISTS idx_leads_phone               ON leads (phone);
CREATE INDEX IF NOT EXISTS idx_leads_deal_stage          ON leads (deal_stage);
CREATE INDEX IF NOT EXISTS idx_leads_intent_score        ON leads (intent_score DESC);
CREATE INDEX IF NOT EXISTS idx_lead_property_signals_lead ON lead_property_signals (lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_objections_lead      ON lead_objections (lead_id);
CREATE INDEX IF NOT EXISTS idx_lead_actions_call_id      ON lead_actions (call_id);
CREATE INDEX IF NOT EXISTS idx_lead_actions_performed_at ON lead_actions (performed_at DESC);

-- knowledge
CREATE INDEX IF NOT EXISTS idx_knowledge_documents_type  ON knowledge_documents (doc_type);
CREATE INDEX IF NOT EXISTS idx_agent_knowledge_files_agent ON agent_knowledge_files (agent_id);

-- infra
CREATE INDEX IF NOT EXISTS idx_app_kv_store_expires      ON app_kv_store (expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_post_call_deliveries_call ON post_call_deliveries (call_id);
CREATE INDEX IF NOT EXISTS idx_crm_sync_log_phone        ON crm_sync_log (phone);
CREATE INDEX IF NOT EXISTS idx_calendar_bookings_phone   ON calendar_bookings (phone);
CREATE INDEX IF NOT EXISTS idx_calendar_bookings_time    ON calendar_bookings (scheduled_at);

-- ══════════════════════════════════════════════════════════════════════════════
-- 9. TRIGGERS
-- ══════════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    t TEXT;
BEGIN
    FOREACH t IN ARRAY ARRAY[
        'agents', 'conversational_flows', 'call_configurations', 'post_call_actions',
        'outbound_campaigns', 'campaign_numbers', 'listings', 'pin_to_pim_mappings',
        'leads', 'knowledge_documents', 'agent_knowledge_files', 'calendar_bookings'
    ]
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS trg_%s_updated_at ON %I', t, t);
        EXECUTE format(
            'CREATE TRIGGER trg_%s_updated_at BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION set_updated_at()',
            t, t
        );
    END LOOP;
END $$;

DROP TRIGGER IF EXISTS trg_agents_single_active ON agents;
CREATE TRIGGER trg_agents_single_active
    AFTER INSERT OR UPDATE OF is_active ON agents
    FOR EACH ROW
    WHEN (NEW.is_active = TRUE)
    EXECUTE FUNCTION enforce_single_active_agent();

-- ══════════════════════════════════════════════════════════════════════════════
-- 10. ROW LEVEL SECURITY
-- ══════════════════════════════════════════════════════════════════════════════
-- Production: backend uses SUPABASE_SERVICE_ROLE_KEY (bypasses RLS).
-- Dev/dashboard with anon key: policies below allow dashboard CRUD.
-- ══════════════════════════════════════════════════════════════════════════════

DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'agents', 'conversational_flows', 'call_configurations', 'post_call_actions',
        'outbound_campaigns', 'campaign_numbers', 'campaign_listings',
        'listings', 'pin_to_pim_mappings',
        'call_logs', 'call_transcripts', 'active_calls', 'call_listing_mentions',
        'leads', 'lead_property_signals', 'lead_objections', 'lead_actions',
        'knowledge_documents', 'agent_knowledge_files', 'agent_knowledge_assignments',
        'app_settings', 'app_kv_store', 'post_call_deliveries', 'crm_sync_log',
        'calendar_bookings'
    ]
    LOOP
        EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', tbl);
    END LOOP;
END $$;

-- Service role: full access on all tables
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'agents', 'conversational_flows', 'call_configurations', 'post_call_actions',
        'outbound_campaigns', 'campaign_numbers', 'campaign_listings',
        'listings', 'pin_to_pim_mappings',
        'call_logs', 'call_transcripts', 'active_calls', 'call_listing_mentions',
        'leads', 'lead_property_signals', 'lead_objections', 'lead_actions',
        'knowledge_documents', 'agent_knowledge_files', 'agent_knowledge_assignments',
        'app_settings', 'app_kv_store', 'post_call_deliveries', 'crm_sync_log',
        'calendar_bookings'
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS "service_role_all_%s" ON %I', tbl, tbl);
        EXECUTE format(
            'CREATE POLICY "service_role_all_%s" ON %I FOR ALL TO service_role USING (true) WITH CHECK (true)',
            tbl, tbl
        );
    END LOOP;
END $$;

-- Anon: dashboard dev access (mirror supabase_fix_rls.sql + new tables)
DO $$
DECLARE
    tbl TEXT;
BEGIN
    FOREACH tbl IN ARRAY ARRAY[
        'agents', 'conversational_flows', 'call_configurations', 'post_call_actions',
        'outbound_campaigns', 'campaign_numbers', 'campaign_listings',
        'listings', 'pin_to_pim_mappings',
        'call_logs', 'call_transcripts', 'active_calls', 'call_listing_mentions',
        'leads', 'lead_property_signals', 'lead_objections', 'lead_actions',
        'knowledge_documents', 'agent_knowledge_files', 'agent_knowledge_assignments',
        'app_settings', 'app_kv_store', 'post_call_deliveries', 'crm_sync_log',
        'calendar_bookings'
    ]
    LOOP
        EXECUTE format('DROP POLICY IF EXISTS "anon_all_%s" ON %I', tbl, tbl);
        EXECUTE format(
            'CREATE POLICY "anon_all_%s" ON %I FOR ALL TO anon USING (true) WITH CHECK (true)',
            tbl, tbl
        );
    END LOOP;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════
-- 11. STORAGE BUCKETS
-- ══════════════════════════════════════════════════════════════════════════════

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'call-recordings',
    'call-recordings',
    true,
    104857600,
    ARRAY['audio/ogg', 'audio/mpeg', 'audio/wav', 'audio/webm', 'video/mp4']
)
ON CONFLICT (id) DO UPDATE SET
    public = EXCLUDED.public,
    file_size_limit = EXCLUDED.file_size_limit,
    allowed_mime_types = EXCLUDED.allowed_mime_types;

INSERT INTO storage.buckets (id, name, public, file_size_limit, allowed_mime_types)
VALUES (
    'knowledge-files',
    'knowledge-files',
    false,
    52428800,
    ARRAY['application/pdf', 'text/plain', 'text/markdown', 'application/msword',
          'application/vnd.openxmlformats-officedocument.wordprocessingml.document']
)
ON CONFLICT (id) DO NOTHING;

DROP POLICY IF EXISTS "Public read recordings" ON storage.objects;
CREATE POLICY "Public read recordings"
    ON storage.objects FOR SELECT
    USING (bucket_id = 'call-recordings');

DROP POLICY IF EXISTS "Service role write recordings" ON storage.objects;
CREATE POLICY "Service role write recordings"
    ON storage.objects FOR INSERT
    WITH CHECK (bucket_id = 'call-recordings');

DROP POLICY IF EXISTS "Service role write knowledge files" ON storage.objects;
CREATE POLICY "Service role write knowledge files"
    ON storage.objects FOR INSERT
    WITH CHECK (bucket_id = 'knowledge-files');

DROP POLICY IF EXISTS "Service role read knowledge files" ON storage.objects;
CREATE POLICY "Service role read knowledge files"
    ON storage.objects FOR SELECT
    USING (bucket_id = 'knowledge-files');

-- ══════════════════════════════════════════════════════════════════════════════
-- 12. SEED DATA
-- ══════════════════════════════════════════════════════════════════════════════

-- Default admissions agent
INSERT INTO agents (
    id, name, description, purpose, system_prompt, languages,
    llm_provider, llm_model, temperature, stt_provider, stt_model,
    tts_provider, tts_voice_id, welcome_message, max_call_duration_seconds,
    is_active, metadata
) VALUES (
    'a0000000-0000-4000-8000-000000000001',
    'MGM Admissions Counselor',
    'Friendly admissions counselor for MGM College — answers program questions, eligibility, fees, and campus visit scheduling.',
    'admissions',
    E'You are Priya, a warm and knowledgeable admissions counselor at MGM College of Engineering and Technology.\n\nYour goals:\n1. Welcome prospective students and parents with empathy and enthusiasm.\n2. Explain undergraduate and postgraduate programs (CSE, ECE, Mechanical, MBA, etc.).\n3. Clarify eligibility (12th percentage, entrance exams, lateral entry).\n4. Share fee structure, scholarships, and hostel facilities at a high level.\n5. Capture lead details: name, phone, city, program of interest, preferred intake year.\n6. Offer to schedule a campus tour or callback with a senior counselor.\n\nStyle:\n- Speak clearly in English or Hindi based on caller preference.\n- Keep answers concise (2–3 sentences) unless the caller asks for detail.\n- Never invent exact fee figures — say you will share the official brochure or arrange a callback.\n- If unsure, offer to connect them with the admissions desk.\n- End positively and thank them for considering MGM College.',
    '["en", "hi"]'::jsonb,
    'gemini', 'gemini-2.0-flash', 0.65,
    'sarvam', 'saarika', 'sarvam', 'kavya',
    'Namaste! Welcome to MGM College admissions. I am Priya, your admissions counselor. How may I help you today — programs, eligibility, fees, or a campus visit?',
    1200, TRUE,
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

INSERT INTO call_configurations (agent_id, silence_handling, end_call_rules, transfer_routing, response_behavior, ambient_sound, advanced_settings)
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

INSERT INTO post_call_actions (agent_id, delivery_method, trigger_statuses, include_summary, include_full_transcript, include_sentiment, include_extracted_info, extracted_variables, webhook_url, email_recipients)
SELECT
    'a0000000-0000-4000-8000-000000000001',
    'webhook',
    '["completed", "transferred", "voicemail"]'::jsonb,
    TRUE, FALSE, TRUE, TRUE,
    '[
        {"name": "caller_name", "description": "Full name of the prospective student or parent", "how_to_extract": "From conversation when they introduce themselves or when asked"},
        {"name": "program_interest", "description": "Branch or program they are interested in", "how_to_extract": "Infer from programs discussed — CSE, ECE, MBA, etc."},
        {"name": "intake_year", "description": "Target admission year", "how_to_extract": "Ask or infer from phrases like next year or 2026 batch"},
        {"name": "city", "description": "City or state of the caller", "how_to_extract": "Ask during lead capture section"},
        {"name": "callback_requested", "description": "Whether they want a human callback", "how_to_extract": "true if they ask to speak to someone or schedule a tour"}
    ]'::jsonb,
    NULL, '[]'::jsonb
WHERE EXISTS (SELECT 1 FROM agents WHERE id = 'a0000000-0000-4000-8000-000000000001')
ON CONFLICT (agent_id) DO NOTHING;

-- Sample PIM listings + PIN mappings (VK Realty / real estate use case)
INSERT INTO listings (listing_id, pim_sku, title, area, property_type, bedrooms, sqft_min, sqft_max, price_min, price_max, facts, tags)
VALUES
    ('HYD-KOK-001', 'PIM-KOK-3BHK-01', 'Kokapet 3BHK Premium Gated', 'Kokapet', 'apartment', 3, 1800, 2200, 12000000, 18000000,
     '{"builder": "VK Realty", "possession": "ready", "facing": "east"}'::jsonb, ARRAY['kokapet', '3bhk', 'premium']),
    ('HYD-GAC-012', 'PIM-GAC-2BHK-12', 'Gachibowli 2BHK near Wipro', 'Gachibowli', 'apartment', 2, 1100, 1400, 7500000, 9500000,
     '{"builder": "VK Realty", "possession": "2026-Q4"}'::jsonb, ARRAY['gachibowli', '2bhk', 'it-hub']),
    ('HYD-NAR-005', 'PIM-NAR-PLOT-05', 'Narsingi Residential Plot 200sqyd', 'Narsingi', 'plot', NULL, NULL, NULL, 4500000, 5500000,
     '{"plot_size_sqyd": 200, "approval": "HMDA"}'::jsonb, ARRAY['narsingi', 'plot'])
ON CONFLICT (listing_id) DO NOTHING;

INSERT INTO pin_to_pim_mappings (pin, pim_listing_id, source, label, description)
VALUES
    ('4521', 'HYD-KOK-001', 'ivr', 'Kokapet 3BHK', 'Press 1 / say PIN 4521 for Kokapet premium 3BHK'),
    ('8832', 'HYD-GAC-012', 'builder_portal', 'Gachibowli 2BHK', 'Builder portal short ref'),
    ('7105', 'HYD-NAR-005', 'manual', 'Narsingi plot', 'Verbal shorthand for Narsingi plot listing')
ON CONFLICT (pin, source) DO NOTHING;

-- Default app settings
INSERT INTO app_settings (key, value, description)
VALUES
    ('business_hours', '{"days": ["Monday","Tuesday","Wednesday","Thursday","Friday","Saturday"], "start": "09:00", "end": "19:00", "timezone": "Asia/Kolkata"}'::jsonb, 'Office / calling window'),
    ('tone_preset', '"professional"'::jsonb, 'Default agent tone preset')
ON CONFLICT (key) DO NOTHING;

-- ══════════════════════════════════════════════════════════════════════════════
-- 13. HELPER VIEW — resolve PIN to listing in one query
-- ══════════════════════════════════════════════════════════════════════════════

CREATE OR REPLACE VIEW v_pin_pim_resolved AS
SELECT
    m.pin,
    m.source,
    m.label,
    m.is_active,
    l.listing_id,
    l.pim_sku,
    l.title,
    l.area,
    l.property_type,
    l.bedrooms,
    l.price_min,
    l.price_max,
    l.facts,
    l.status
FROM pin_to_pim_mappings m
JOIN listings l ON l.listing_id = m.pim_listing_id
WHERE m.is_active = TRUE AND l.status = 'active';

COMMENT ON VIEW v_pin_pim_resolved IS 'Active PIN → PIM listing lookup for voice agent tools';

-- ══════════════════════════════════════════════════════════════════════════════
-- DONE — verify:
--
--   SELECT table_name FROM information_schema.tables
--   WHERE table_schema = 'public' AND table_type = 'BASE TABLE'
--   ORDER BY 1;
--
--   SELECT * FROM v_pin_pim_resolved;
--   SELECT id, name, purpose, is_active FROM agents;
-- ══════════════════════════════════════════════════════════════════════════════