-- ══════════════════════════════════════════════════════════════════════════════
-- SPEAKSY — WhatsApp session persistence (migration v7)
-- Run in Supabase SQL Editor (safe to re-run).
--
-- Prerequisite: organizations table (from supabase_migration_v6_orgs.sql).
-- If you haven't run v6 yet, the block below creates it automatically.
-- For a full fresh project, run supabase_schema.sql first, then v6, then v7.
-- ══════════════════════════════════════════════════════════════════════════════

-- ─── Prerequisite: organizations (inline from v6 if missing) ─────────────────

CREATE TABLE IF NOT EXISTS organizations (
    id          UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    name        TEXT        NOT NULL,
    slug        TEXT        NOT NULL UNIQUE,
    status      TEXT        NOT NULL DEFAULT 'active'
                    CHECK (status IN ('active', 'suspended', 'archived')),
    metadata    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

INSERT INTO organizations (id, name, slug, status)
VALUES ('00000000-0000-4000-8000-000000000001', 'Default Workspace', 'default', 'active')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "organizations_service_all" ON organizations;
CREATE POLICY "organizations_service_all" ON organizations
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ─── WhatsApp sessions ───────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS whatsapp_sessions (
    id            UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    org_id        UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    phone_number  TEXT,
    creds_json    JSONB       NOT NULL DEFAULT '{}'::jsonb,
    keys_json     JSONB       NOT NULL DEFAULT '{}'::jsonb,
    status        TEXT        NOT NULL DEFAULT 'pending'
                      CHECK (status IN ('pending', 'connected', 'disconnected', 'expired')),
    connected_at  TIMESTAMPTZ,
    expires_at    TIMESTAMPTZ,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE whatsapp_sessions IS
    'One Baileys WhatsApp Web session per org — auth state (creds/keys) persisted here '
    'instead of filesystem so it survives container restarts and scales across orgs.';

-- Only one active session per org (a fresh /session/start reuses/replaces the row)
CREATE UNIQUE INDEX IF NOT EXISTS idx_whatsapp_sessions_org ON whatsapp_sessions(org_id);

-- ─── RLS — service-role only ─────────────────────────────────────────────────
-- This table holds Baileys auth secrets (creds_json/keys_json). It is never read
-- directly by the browser — only the whatsapp-service Node process (via the
-- Supabase service-role key) and the FastAPI backend touch it.

ALTER TABLE whatsapp_sessions ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "whatsapp_sessions_service_all" ON whatsapp_sessions;
CREATE POLICY "whatsapp_sessions_service_all" ON whatsapp_sessions
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- ══════════════════════════════════════════════════════════════════════════════
-- DONE.
-- After running this:
--   • whatsapp_sessions exists, org-scoped, RLS-locked to service_role only.
--   • call_logs.channel_type already accepts 'whatsapp' (added in v4_agents.sql)
--     — no change needed there to start tagging WhatsApp-channel rows.
-- ══════════════════════════════════════════════════════════════════════════════
