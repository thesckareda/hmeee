-- ══════════════════════════════════════════════════════════════════════════════
-- SPEAKSY — WhatsApp chat history (migration v8)
-- Run after supabase_migration_v7_whatsapp.sql
-- Safe to re-run: IF NOT EXISTS / DROP POLICY IF EXISTS throughout
-- ══════════════════════════════════════════════════════════════════════════════

-- ─── Conversations (one row per contact per linked WhatsApp number) ─────────

CREATE TABLE IF NOT EXISTS whatsapp_conversations (
    id                   UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    org_id               UUID        NOT NULL REFERENCES organizations(id) ON DELETE CASCADE,
    whatsapp_number_id   UUID        NOT NULL REFERENCES whatsapp_sessions(id) ON DELETE CASCADE,
    contact_number       TEXT        NOT NULL,
    contact_name         TEXT,
    last_message_preview TEXT,
    last_message_at      TIMESTAMPTZ,
    unread_count         INTEGER     NOT NULL DEFAULT 0 CHECK (unread_count >= 0),
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (whatsapp_number_id, contact_number)
);

COMMENT ON TABLE whatsapp_conversations IS
    'Inbox threads for a linked WhatsApp number — one row per external contact.';

-- ─── Messages ────────────────────────────────────────────────────────────────

CREATE TABLE IF NOT EXISTS whatsapp_messages (
    id               UUID        DEFAULT gen_random_uuid() PRIMARY KEY,
    conversation_id  UUID        NOT NULL REFERENCES whatsapp_conversations(id) ON DELETE CASCADE,
    direction        TEXT        NOT NULL CHECK (direction IN ('inbound', 'outbound')),
    message_type     TEXT        NOT NULL DEFAULT 'text'
                         CHECK (message_type IN ('text', 'image', 'audio', 'document')),
    content          TEXT,
    media_url        TEXT,
    sent_by          TEXT        NOT NULL DEFAULT 'contact'
                         CHECK (sent_by IN ('contact', 'assistant', 'human_agent')),
    status           TEXT        NOT NULL DEFAULT 'sent'
                         CHECK (status IN ('sent', 'delivered', 'read', 'failed')),
    wa_message_id    TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

COMMENT ON TABLE whatsapp_messages IS
    'Individual WhatsApp messages — deduped on wa_message_id when present.';

CREATE UNIQUE INDEX IF NOT EXISTS idx_whatsapp_messages_wa_id
    ON whatsapp_messages (wa_message_id)
    WHERE wa_message_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_whatsapp_messages_conv_created
    ON whatsapp_messages (conversation_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_whatsapp_conversations_number_last
    ON whatsapp_conversations (whatsapp_number_id, last_message_at DESC NULLS LAST);

CREATE INDEX IF NOT EXISTS idx_whatsapp_conversations_org
    ON whatsapp_conversations (org_id);

-- ─── RLS — service_role only (same as whatsapp_sessions) ─────────────────────

ALTER TABLE whatsapp_conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE whatsapp_messages ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "whatsapp_conversations_service_all" ON whatsapp_conversations;
CREATE POLICY "whatsapp_conversations_service_all" ON whatsapp_conversations
    FOR ALL TO service_role USING (true) WITH CHECK (true);

DROP POLICY IF EXISTS "whatsapp_messages_service_all" ON whatsapp_messages;
CREATE POLICY "whatsapp_messages_service_all" ON whatsapp_messages
    FOR ALL TO service_role USING (true) WITH CHECK (true);

-- Realtime (optional — skip silently if publication unavailable)
DO $$ BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE whatsapp_conversations;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'whatsapp_conversations realtime: %', SQLERRM;
END $$;

DO $$ BEGIN
    ALTER PUBLICATION supabase_realtime ADD TABLE whatsapp_messages;
EXCEPTION WHEN OTHERS THEN
    RAISE NOTICE 'whatsapp_messages realtime: %', SQLERRM;
END $$;

-- ══════════════════════════════════════════════════════════════════════════════
-- DONE.
-- ══════════════════════════════════════════════════════════════════════════════