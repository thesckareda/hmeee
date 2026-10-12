# VK Realty HME — Speaksy AI Voice Client

Isolated **Layer 2** deployment for VK Realty (Arjun agent, **HME preset only** — Hinglish/Marathi/English, white-label dashboard).

This package connects to **shared Layer 1 infrastructure** at `wss://livekit.speaksy.online`. It does **not** run LiveKit, Redis, SIP, Egress, TURN, or Caddy.

## Layout

```
vk-realty-hme/
├── agent.py, ui_server.py, …   → Voice agent + dashboard API
├── frontend/                   → VK Realty dashboard UI
├── sql/                        → Supabase migrations (run in order)
├── scripts/                    → verify_setup, benchmarks, calibration
├── data/                       → tenancy.db, caches, local logs
├── knowledge_data/             → HME property knowledge packs
├── CICD-1.ogg                  → Default custom ambient loop
├── config.json                 → Agent flags + assistant settings
├── .env                        → Secrets (never commit)
├── FEATURES_FINAL.md           → Everything new in this release
├── CONTEXT_ENGINEERING.md      → Context injection reference
├── LATENCY_OPTIMIZATION.md       → Latency playbook
└── docker-compose.yml
```

## Quick start (local or GCP client VM)

```bash
cp .env.example .env   # fill LIVEKIT_URL, API keys
./deploy.sh
```

Open: **http://localhost:8000/vk-realty-hme/dashboard** (or port from your `uvicorn` command)

```bash
python3 scripts/verify_setup.py   # pre-flight checks
python3 -m pytest tests/ -q     # regression tests
```

Default login (when `DISABLE_AUTH=false`): credentials from `.env` (`DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD`).

**New in this release:** caller history injection, zero-API token counter, parallel turn injectors (flag-off by default), CICD-1 ambient, org tenancy — see **FEATURES_FINAL.md**.

**Tinglish variant:** `python3 scripts/create_tinglish_client.py` → sibling folder `tinglish-vk-realty` (Hyderabad / `tinglish` preset, port 8002).

## Production deploy (`/opt/clients/vk-realty`)

On a client VM:

```bash
sudo mkdir -p /opt/clients/vk-realty
sudo chown $USER:$USER /opt/clients/vk-realty
# copy this directory contents
cd /opt/clients/vk-realty
cp .env.example .env && nano .env
docker compose up -d
```

No changes to `/opt/infrastructure` are required when adding this client.

## Environment

| Variable | Required | Notes |
|----------|----------|-------|
| `LIVEKIT_URL` | Yes | `wss://livekit.speaksy.online` |
| `LIVEKIT_API_KEY` | Yes | From infrastructure admin |
| `LIVEKIT_API_SECRET` | Yes | From infrastructure admin |
| `GEMINI_API_KEY` | Yes | LLM (config uses gemini-2.5-flash) |
| `CARTESIA_API_KEY` | Yes | TTS for Arjun voice |
| `SARVAM_API_KEY` | Yes | STT |
| `SUPABASE_URL` / `SUPABASE_KEY` | Yes | Client-isolated DB |
| `SUPABASE_SERVICE_ROLE_KEY` | Recommended | Backend writes; use `sql/supabase_migration_v3.sql` |
| `SUPABASE_RECORDINGS_BUCKET` | Yes | `call-recordings` (see `sql/README.md`) |

**Do not set** `REDIS_URL`, `SIP_TRUNK_ID`, `LIVEKIT_SIP_URI`, or TURN overrides here — those belong to Layer 1.

## Re-pack from monorepo

From the main Speaksy repository:

```bash
./scripts/pack_vk_realty_client.sh ~/Downloads/speaksy-clients/vk-realty
```

## URLs

| Path | Description |
|------|-------------|
| `/vk-realty` | Landing + login |
| `/vk-realty-hme/dashboard` | Main dashboard |
| `/vk-realty-hme/o/{role}/dashboard` | Role workspace |
| `/health` | Health check |