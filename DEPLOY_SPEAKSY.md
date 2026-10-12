# VK Realty HME — Deploy to `dashboard.speaksy.online/realestate`

Production URL for this client:

| Page | URL |
|------|-----|
| Landing + login | **https://dashboard.speaksy.online/realestate** |
| Dashboard | **https://dashboard.speaksy.online/realestate/dashboard** |
| Role workspace | **https://dashboard.speaksy.online/realestate/o/{role}/dashboard** |
| Health | **https://dashboard.speaksy.online/health** |

Voice calls use shared Layer 1 at `wss://livekit.speaksy.online` — you do **not** run LiveKit, SIP, Redis, or TURN on this VM.

---

## Architecture

```
DNS: dashboard.speaksy.online → VM static IP
                │
                ▼
┌─────────────────────────────────────────────────────────┐
│  VM (GCP / any Linux host)                              │
│                                                         │
│  Caddy :443  ──reverse_proxy──►  localhost:8001         │
│  (Let's Encrypt)                  │                     │
│                                   ▼                     │
│  ┌───────────────────────────────────────────────────┐  │
│  │  Docker: vk-realty-hme-client                      │  │
│  │  supervisord                                      │  │
│  │    ├─ ui_server :8000  → /realestate, /api        │  │
│  │    └─ agent.py         → LiveKit worker           │  │
│  │  volumes: config.json · data/ · knowledge/        │  │
│  └───────────────────────────────────────────────────┘  │
└────────────────────────────┬────────────────────────────┘
                             │
                             ▼
        livekit.speaksy.online + Supabase + Gemini + Cartesia + Soniox
```

**Current status:** `dashboard.speaksy.online` returns **502** — Caddy is up but no backend is listening on port 8001 yet. Follow the steps below.

---

## Prerequisites checklist

### From Speaksy Layer 1 (already have)

- [ ] `LIVEKIT_URL=wss://livekit.speaksy.online`
- [ ] `LIVEKIT_API_KEY` + `LIVEKIT_API_SECRET`
- [ ] `LIVEKIT_AGENT_NAME=inbound-voice-demo` (or dedicated HME name)
- [ ] `OUTBOUND_TRUNK_ID` for test/outbound calls
- [ ] LiveKit agent dispatch rule pointing to your agent name

### API keys (HME stack)

- [ ] `GEMINI_API_KEY` — primary LLM
- [ ] `CARTESIA_API_KEY` — TTS (Nivas voice)
- [ ] `SONIOX_API_KEY` — STT (`stt-rt-v5`)
- [ ] Supabase project — call logs, recordings, orgs

### DNS

```
dashboard.speaksy.online  A  →  <VM public IP>
```

---

## Step 1 — Supabase (one-time)

1. Open [Supabase SQL Editor](https://supabase.com/dashboard) for your project.
2. Run migrations in order (see `sql/README.md`):
   - `sql/supabase_schema.sql`
   - `sql/supabase_migration_v2.sql` … through `v6_orgs.sql`
   - `sql/supabase_fix_rls.sql` (if using service role)
3. Create Storage bucket `call-recordings` (private).
4. Copy **Project URL**, **anon key**, **service role key**, **S3 keys**.

Verify locally:

```bash
cd vk-realty-hme
python3 scripts/verify_setup.py
```

Target: **24/24** checks (set `SUPABASE_SERVICE_ROLE_KEY` for full pass).

---

## Step 2 — Package & upload to VM

**On your Mac:**

```bash
cd /Users/nivi/Downloads/speaksy-clients
tar czf vk-realty-hme-deploy.tgz \
  --exclude='vk-realty-hme/.env' \
  --exclude='vk-realty-hme/__pycache__' \
  --exclude='vk-realty-hme/**/__pycache__' \
  --exclude='vk-realty-hme/data/greeting_pcm_cache' \
  --exclude='vk-realty-hme/data/agent_logs' \
  vk-realty-hme

# Upload (replace VM name / zone)
gcloud compute scp vk-realty-hme-deploy.tgz YOUR_VM:/tmp/ --zone=asia-south1-a
```

**On the VM:**

```bash
sudo mkdir -p /opt/clients
sudo chown $USER:$USER /opt/clients
cd /opt/clients
tar xzf /tmp/vk-realty-hme-deploy.tgz
cd vk-realty-hme
mkdir -p logs data/agent_logs data/greeting_pcm_cache configs knowledge

# Landing frames (114 JPGs required)
ls frontend/vk-realty-hme/frames/CICD_0.jpg frontend/vk-realty-hme/frames/CICD_113.jpg
```

---

## Step 3 — Production `.env`

```bash
cp .env.speaksy.example .env
chmod 600 .env
nano .env
```

**Critical values:**

```bash
CLIENT_BASE_PATH=/realestate
ALLOWED_ORIGINS=https://dashboard.speaksy.online
DISABLE_AUTH=false
ENVIRONMENT=production
CLIENT_HTTP_PORT=8001

# Generate on VM:
openssl rand -hex 32    # JWT_SECRET
openssl rand -base64 24 # DASHBOARD_PASSWORD
```

Paste your LiveKit, Gemini, Cartesia, Soniox, and Supabase keys from the local `.env` (never commit `.env`).

**Do not set on this VM:** `REDIS_URL`, `VOBIZ_*`, `SIP_TRUNK_ID` — those are Layer 1 only.

---

## Step 4 — Docker deploy

```bash
cd /opt/clients/vk-realty-hme
./deploy.sh
```

Or manually:

```bash
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs -f --tail=100
```

**On VM (internal check):**

```bash
curl -s http://localhost:8001/health | python3 -m json.tool
curl -sI http://localhost:8001/realestate | head -5
curl -sI http://localhost:8001/static/favicon.png | head -3
```

Expect `health` → `"status": "ok"`, `/realestate` → `200`.

**Agent status:**

```bash
docker compose exec vk-realty-hme supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status
# agent     RUNNING
# ui_server RUNNING
```

---

## Step 5 — Caddy (HTTPS)

`dashboard.speaksy.online` already runs Caddy (502 = backend missing). After Docker is up on **8001**, point Caddy at it.

```bash
sudo nano /etc/caddy/Caddyfile
```

Use the snippet from `deploy/caddy-dashboard.speaksy.online.caddyfile`:

```caddy
dashboard.speaksy.online {

	@root path /
	redir @root /realestate permanent

	reverse_proxy 127.0.0.1:8001
}
```

Reload:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Confirm TLS:

```bash
curl -sI https://dashboard.speaksy.online/health
curl -sI https://dashboard.speaksy.online/realestate
curl -sI https://dashboard.speaksy.online/realestate/dashboard
```

---

## Step 6 — Post-deploy verification

| # | Check | Expected |
|---|--------|----------|
| 1 | `https://dashboard.speaksy.online/realestate` | Landing animation + login |
| 2 | `https://dashboard.speaksy.online/realestate/dashboard` | Redirects to login when auth on |
| 3 | Login with `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` | Dashboard loads |
| 4 | System → Agent status | `running` |
| 5 | Settings → Voice (Cartesia + Soniox) | Save + restart agent |
| 6 | Outbound call test | `dispatch_id` returned |
| 7 | Browser tab icon | Speaksy logo (favicon) |
| 8 | `https://dashboard.speaksy.online/vk-realty-hme/dashboard` | 301 → `/realestate/dashboard` |

**HME config highlights** (`config.json`):

- `lang_preset: hme` — Hindi / Marathi / English
- `stt_provider: soniox`, `stt-rt-v5`, 500ms endpointing (clamped)
- `llm_context_prewarm: true`
- `ambient_sound: cicd_1`
- Mumbai / Chandigarh knowledge packs

---

## Step 7 — LiveKit agent registration

The worker registers as `LIVEKIT_AGENT_NAME` from `.env` (default `inbound-voice-demo`).

Confirm in LiveKit Cloud → Agents → worker shows **registered** after container start.

For **inbound** calls, ensure SIP dispatch rule on Layer 1 routes to the same agent name.

---

## Updates & rollback

```bash
cd /opt/clients/vk-realty-hme
tar czf ~/backup-$(date +%Y%m%d).tgz data/ config.json .env

docker compose build --pull
docker compose up -d
docker compose logs -f --tail=200
```

`data/`, `config.json`, and `.env` are on the host — they survive rebuilds.

---

## Security checklist

- [ ] `DISABLE_AUTH=false`
- [ ] `ENVIRONMENT=production`
- [ ] `ALLOWED_ORIGINS=https://dashboard.speaksy.online` (no `*`, no localhost)
- [ ] Strong `JWT_SECRET`, passwords
- [ ] `.env` mode `600`
- [ ] Port **8001 not** in public firewall (only 80/443)
- [ ] SSH restricted to known IPs
- [ ] Separate Supabase project per client (recommended)

---

## Troubleshooting

### 502 Bad Gateway

Backend not running or wrong port:

```bash
docker compose ps
curl http://127.0.0.1:8001/health
sudo journalctl -u caddy -f
```

### 404 on `/realestate`

`CLIENT_BASE_PATH` must be `/realestate` in `.env` **before** container start:

```bash
grep CLIENT_BASE_PATH .env
docker compose up -d --force-recreate
```

### Login loop

- Site must be **HTTPS**
- `ALLOWED_ORIGINS=https://dashboard.speaksy.online`
- Clear cookies for `dashboard.speaksy.online`

### Agent crash on call (Soniox)

`max_endpoint_delay_ms` must be 500–3000. `stt_provider.py` clamps automatically — rebuild image if on old code.

### Outbound call fails

Set `OUTBOUND_TRUNK_ID` in `.env`, restart: `docker compose restart`

---

## Quick reference

| URL | Purpose |
|-----|---------|
| https://dashboard.speaksy.online/realestate | Landing + login |
| https://dashboard.speaksy.online/realestate/dashboard | Main dashboard |
| https://dashboard.speaksy.online/health | Uptime probe |
| https://dashboard.speaksy.online/api/call/single | Outbound API |

| Command | Action |
|---------|--------|
| `./deploy.sh` | Build + start |
| `docker compose logs -f` | Live logs |
| `docker compose restart` | Restart container |
| `sudo systemctl reload caddy` | Reload TLS proxy |

---

*Speaksy Layer 2 · VK Realty HME · July 2026*