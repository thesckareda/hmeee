-- Lead CTA action log (dashboard hot-lead actions)
CREATE TABLE IF NOT EXISTS lead_actions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  call_id TEXT NOT NULL,
  action TEXT NOT NULL,
  performed_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  performed_by TEXT,
  notes TEXT DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_lead_actions_call_id ON lead_actions(call_id);
CREATE INDEX IF NOT EXISTS idx_lead_actions_performed_at ON lead_actions(performed_at DESC);

ALTER TABLE lead_actions ENABLE ROW LEVEL SECURITY;