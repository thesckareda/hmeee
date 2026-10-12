# Switching Supabase account

See the step-by-step guide in the team wiki or conversation export. Summary:

1. Create new Supabase project.
2. Run SQL files in `sql/` (see `sql/README.md`).
3. Create `call-recordings` bucket + S3 keys.
4. Update `.env`: `SUPABASE_URL`, `SUPABASE_KEY`, `SUPABASE_SERVICE_ROLE_KEY`, S3 vars, `SUPABASE_RECORDINGS_BUCKET=call-recordings`.
5. Align `DEFAULT_AGENT_ID` with a row in `agents` (or re-activate agent in dashboard).
6. Restart agent + UI server.
7. Test call → `call_logs` row + recording in storage.

**Not migrated:** `data/tenancy.db` (local org users), `~/.hermes/re-voice-memory/` (lead memory on disk).