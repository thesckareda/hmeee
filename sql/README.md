# Supabase migrations — VK Realty HME

Run in **Supabase SQL Editor** on a **new** project, in order.

## Fresh project (recommended path)

| Step | File | Notes |
|------|------|-------|
| 1 | `supabase_schema.sql` | Full base schema |
| 2 | `supabase_migration_v2.sql` | Analytics + transcripts + active_calls |
| 3 | `supabase_migration_v4_agents.sql` | Multi-agent dashboard tables |
| 4 | `supabase_migration_v5_deepseek_summary.sql` | `ai_summary` JSONB |
| 5 | `supabase_migration_lead_actions.sql` | Hot-lead CTA log |
| 6 | `supabase_migration_v6_orgs.sql` | `organizations` + `org_id` |
| 7a | `supabase_fix_rls.sql` | **If using anon key** (`SUPABASE_KEY` only) |
| 7b | `supabase_migration_v3.sql` | **If using service role** (`SUPABASE_SERVICE_ROLE_KEY`) — skip 7a |

## Storage

1. Create bucket **`call-recordings`** (public read for playback URLs).
2. **Project Settings → Storage → S3 Access Keys** → generate keys for `.env`.

## Verify

```bash
cd vk-realty-hme
python3 scripts/verify_setup.py
```

See **SUPABASE_MIGRATION.md** in the repo root for switching accounts end-to-end.