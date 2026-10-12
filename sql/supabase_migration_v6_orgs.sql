-- ══════════════════════════════════════════════════════════════════════════════
-- SPEAKSY — Multi-tenant organisations (migration v6)
-- Run in Supabase SQL Editor after supabase_schema.sql
-- Safe to re-run: uses IF NOT EXISTS throughout
-- ══════════════════════════════════════════════════════════════════════════════

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

COMMENT ON TABLE organizations IS 'B2B client workspaces — dashboard tenancy boundary';

-- Default org for existing single-tenant data
INSERT INTO organizations (id, name, slug, status)
VALUES ('00000000-0000-4000-8000-000000000001', 'Default Workspace', 'default', 'active')
ON CONFLICT (id) DO NOTHING;

-- ─── org_id on core tables ───────────────────────────────────────────────────

ALTER TABLE agents ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE CASCADE;
ALTER TABLE call_logs ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE SET NULL;
ALTER TABLE outbound_campaigns ADD COLUMN IF NOT EXISTS org_id UUID REFERENCES organizations(id) ON DELETE CASCADE;

CREATE INDEX IF NOT EXISTS idx_agents_org ON agents(org_id);
CREATE INDEX IF NOT EXISTS idx_call_logs_org ON call_logs(org_id);
CREATE INDEX IF NOT EXISTS idx_outbound_campaigns_org ON outbound_campaigns(org_id);

-- Backfill existing rows into default org
UPDATE agents SET org_id = '00000000-0000-4000-8000-000000000001' WHERE org_id IS NULL;
UPDATE call_logs SET org_id = '00000000-0000-4000-8000-000000000001' WHERE org_id IS NULL;
UPDATE outbound_campaigns SET org_id = '00000000-0000-4000-8000-000000000001' WHERE org_id IS NULL;

-- ─── RLS helpers (optional — service_role bypasses) ──────────────────────────

ALTER TABLE organizations ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "organizations_service_all" ON organizations;
CREATE POLICY "organizations_service_all" ON organizations
    FOR ALL USING (true) WITH CHECK (true);