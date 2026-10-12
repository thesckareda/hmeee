# VK Realty — GCP Deployment at `dashboard.outpero.com`

Deploy the VK Realty client to Google Cloud. Production URL:

**https://dashboard.outpero.com**

All VK Realty pages live under the **`/vk-realty`** path prefix (landing, login, dashboard). The app connects to shared Layer 1 at `wss://livekit.speaksy.online` — you do **not** run LiveKit, SIP, Redis, or TURN on GCP.

---

## Production URLs

| URL | What it is |
|-----|------------|
| **https://dashboard.outpero.com/vk-realty** | Landing page (scroll animation) + login form (`#login`) |
| **https://dashboard.outpero.com/vk-realty/login** | Redirects → `/vk-realty#login` |
| **https://dashboard.outpero.com/vk-realty/dashboard** | Main VK Realty dashboard (auth required) |
| **https://dashboard.outpero.com/vk-realty/o/{role}/dashboard** | Role workspace dashboard |
| **https://dashboard.outpero.com/health** | Health check (load balancer / monitoring) |
| **https://dashboard.outpero.com/api/…** | REST API (auth required except public routes) |
| **https://dashboard.outpero.com/static/vk-realty/…** | CSS, JS, landing frames |

**Share with users:** `https://dashboard.outpero.com/vk-realty`  
**Bookmark for ops:** `https://dashboard.outpero.com/vk-realty/dashboard`

Optional: redirect the domain root to the landing page (see [HTTPS setup](#step-8--https-for-dashboardoutperocom)).

---

## Table of contents

1. [Architecture](#architecture)
2. [Prerequisites](#prerequisites)
3. [GCP resources](#gcp-resources)
4. [Step 1 — GCP project](#step-1--gcp-project)
5. [Step 2 — VM](#step-2--vm)
6. [Step 3 — Firewall](#step-3--firewall)
7. [Step 4 — Docker on VM](#step-4--docker-on-vm)
8. [Step 5 — Upload app](#step-5--upload-app)
9. [Step 6 — `.env` for production](#step-6--env-for-production)
10. [Step 7 — Deploy](#step-7--deploy)
11. [Step 8 — HTTPS for dashboard.outpero.com](#step-8--https-for-dashboardoutperocom)
12. [Step 9 — Verify](#step-9--verify)
13. [Updates & rollback](#updates--rollback)
14. [Monitoring](#monitoring)
15. [Security checklist](#security-checklist)
16. [Troubleshooting](#troubleshooting)

---

## Architecture

```
                    DNS: dashboard.outpero.com → VM static IP
                                    │
                                    ▼
┌───────────────────────────────────────────────────────────────┐
│  GCP Compute Engine VM                                        │
│                                                               │
│  Caddy :443  ──reverse_proxy──►  localhost:8000              │
│  (Let's Encrypt TLS)              │                           │
│                                   ▼                           │
│  ┌─────────────────────────────────────────────────────────┐ │
│  │  Docker: vk-realty-client                                │ │
│  │  supervisord                                               │ │
│  │    ├─ ui_server :8000   /vk-realty, /api, /static       │ │
│  │    └─ agent.py  :8081   LiveKit voice worker            │ │
│  │  volumes: config.json · data/ · knowledge/ · logs/        │ │
│  └─────────────────────────────────────────────────────────┘ │
└────────────────────────────┬──────────────────────────────────┘
                             │ outbound HTTPS / WSS
                             ▼
              livekit.speaksy.online  +  Supabase  +  Gemini  +  Cartesia  +  Sarvam
```

**Why Compute Engine (not Cloud Run)?** Long-running LiveKit worker, persistent `data/`, two processes in one container via supervisord.

---

## Prerequisites

### From Speaksy infrastructure (Layer 1)

| Variable | Notes |
|----------|-------|
| `LIVEKIT_URL` | `wss://livekit.speaksy.online` |
| `LIVEKIT_API_KEY` / `LIVEKIT_API_SECRET` | Dispatch + recordings |
| `LIVEKIT_AGENT_NAME` | e.g. `inbound-voice-demo` |
| `OUTBOUND_TRUNK_ID` | e.g. `ST_…` — required for outbound test calls |

### Your API keys

| Provider | Purpose |
|----------|---------|
| Gemini | Primary LLM |
| Cartesia | TTS (Tinglish / Nivas voice) |
| Sarvam | STT |
| Supabase | Call logs, agents, recording storage |

### DNS (before HTTPS)

Create an **A record**:

```
dashboard.outpero.com  →  <VM static external IP>
```

TTL: 300s while testing, 3600s in production.

---

## GCP resources

| Item | Value |
|------|-------|
| Region | `asia-south1` (Mumbai) |
| Machine | `e2-standard-4` (4 vCPU, 16 GB RAM) |
| Disk | 50 GB SSD |
| OS | Ubuntu 22.04 LTS |
| Static IP | 1 reserved external IP |

---

## Step 1 — GCP project

```bash
gcloud auth login
gcloud projects create outpero-vk-realty --name="OUTPERO VK Realty"
gcloud config set project outpero-vk-realty

gcloud services enable compute.googleapis.com
```

---

## Step 2 — VM

```bash
gcloud compute addresses create outpero-dashboard-ip --region=asia-south1

gcloud compute instances create outpero-dashboard-vm \
  --zone=asia-south1-a \
  --machine-type=e2-standard-4 \
  --image-family=ubuntu-2204-lts \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size=50GB \
  --boot-disk-type=pd-ssd \
  --address=outpero-dashboard-ip \
  --tags=http-server,https-server

# Copy this IP into your DNS A record for dashboard.outpero.com
gcloud compute addresses describe outpero-dashboard-ip \
  --region=asia-south1 --format="get(address)"
```

SSH:

```bash
gcloud compute ssh outpero-dashboard-vm --zone=asia-south1-a
```

---

## Step 3 — Firewall

```bash
gcloud compute firewall-rules create allow-outpero-http \
  --allow=tcp:80 \
  --target-tags=http-server

gcloud compute firewall-rules create allow-outpero-https \
  --allow=tcp:443 \
  --target-tags=https-server

# SSH — restrict to your IP in production
gcloud compute firewall-rules create allow-ssh-office \
  --allow=tcp:22 \
  --source-ranges=YOUR.IP.ADDRESS/32
```

Do **not** expose port 8000 publicly in production — Caddy on 443 proxies to it locally.

---

## Step 4 — Docker on VM

On the VM:

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg

sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | \
  sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg

echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
  https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo $VERSION_CODENAME) stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list > /dev/null

sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
sudo usermod -aG docker $USER
```

Log out and SSH back in. Confirm: `docker compose version`.

---

## Step 5 — Upload app

From your **local machine**:

```bash
cd /path/to/speaksy-clients
tar czf vk-realty-deploy.tgz \
  --exclude='vk-realty/.env' \
  --exclude='vk-realty/__pycache__' \
  --exclude='vk-realty/data/greeting_pcm_cache' \
  --exclude='vk-realty/logs' \
  vk-realty

gcloud compute scp vk-realty-deploy.tgz outpero-dashboard-vm:/tmp/ --zone=asia-south1-a
```

On the VM:

```bash
sudo mkdir -p /opt/clients
sudo chown $USER:$USER /opt/clients
cd /opt/clients
tar xzf /tmp/vk-realty-deploy.tgz
cd vk-realty
mkdir -p logs data/agent_logs data/greeting_pcm_cache configs
```

**Landing frames** (must be real files, not a broken symlink):

```bash
ls frontend/vk-realty/frames/CICD_0.jpg frontend/vk-realty/frames/CICD_113.jpg
# Expected: 114 jpg files
```

---

## Step 6 — `.env` for production

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

### Minimum production `.env`

```bash
# ── LiveKit (Layer 1) ─────────────────────────────────────────
LIVEKIT_URL=wss://livekit.speaksy.online
LIVEKIT_API_KEY=your_livekit_api_key
LIVEKIT_API_SECRET=your_livekit_api_secret
LIVEKIT_AGENT_NAME=inbound-voice-demo
OUTBOUND_TRUNK_ID=ST_your_outbound_trunk_id

# ── LLM + speech ──────────────────────────────────────────────
GEMINI_API_KEY=your_gemini_key
TTS_PROVIDER=cartesia
CARTESIA_API_KEY=your_cartesia_key
CARTESIA_TTS_MODEL=sonic-3.5
SARVAM_API_KEY=your_sarvam_key
STT_PROVIDER=sarvam

# ── Supabase ──────────────────────────────────────────────────
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your_anon_key
SUPABASE_SERVICE_ROLE_KEY=your_service_role_key
SUPABASE_S3_ACCESS_KEY=your_s3_access_key
SUPABASE_S3_SECRET_KEY=your_s3_secret_key
SUPABASE_S3_ENDPOINT=https://your-project.storage.supabase.co/storage/v1/s3
SUPABASE_S3_REGION=ap-northeast-2
SUPABASE_BUCKET=call-recordings

# ── Auth (required for dashboard.outpero.com) ─────────────────
DISABLE_AUTH=false
JWT_SECRET=<openssl rand -hex 32>
DASHBOARD_USERNAME=admin
DASHBOARD_PASSWORD=<strong-password>
API_SETTINGS_PASSWORD=<strong-password>

# ── Domain / client ───────────────────────────────────────────
CLIENT_ID=vk-realty
ENVIRONMENT=production
ALLOWED_ORIGINS=https://dashboard.outpero.com

DEFAULT_AGENT_ID=fbfc5a97-b7d1-4dec-8e32-98bd1a645f5c
```

Generate secrets on the VM:

```bash
openssl rand -hex 32      # JWT_SECRET
openssl rand -base64 24   # passwords
```

**Do not set** on this VM: `REDIS_URL`, `SIP_TRUNK_ID`, `LIVEKIT_SIP_URI`, `VOBIZ_*` — those are Layer 1 only.

With `ENVIRONMENT=production` and HTTPS, auth cookies are set `Secure` automatically.

---

## Step 7 — Deploy

```bash
cd /opt/clients/vk-realty
chmod +x deploy.sh
./deploy.sh
```

Or:

```bash
docker compose build --pull
docker compose up -d
docker compose ps
docker compose logs -f --tail=100
```

Internal health check (on VM):

```bash
curl -s http://localhost:8000/health | python3 -m json.tool
```

Expect `"status": "ok"`.

---

## Step 8 — HTTPS for dashboard.outpero.com

Install Caddy on the **VM host** (outside Docker):

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | \
  sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | \
  sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update && sudo apt install -y caddy
```

Create `/etc/caddy/Caddyfile`:

```caddy
dashboard.outpero.com {

    # Optional: send root visitors to the VK landing/login page
    @root path /
    redir @root /vk-realty permanent

    # Proxy everything else as-is — /vk-realty paths are served by the app
    reverse_proxy localhost:8000
}
```

**Important:** Do **not** strip the `/vk-realty` prefix. The app registers routes at `/vk-realty`, `/vk-realty/dashboard`, etc. Caddy forwards the full path unchanged.

Reload:

```bash
sudo caddy validate --config /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

Caddy obtains a Let's Encrypt certificate automatically once DNS points to the VM.

Confirm TLS:

```bash
curl -I https://dashboard.outpero.com/health
curl -I https://dashboard.outpero.com/vk-realty
```

---

## Step 9 — Verify

Run through this checklist after deploy.

### 1. DNS & TLS

```bash
dig +short dashboard.outpero.com        # → VM IP
curl -sI https://dashboard.outpero.com/health | head -5
```

### 2. Landing + login

Open: **https://dashboard.outpero.com/vk-realty**

- Scroll-scrub animation loads (no “Could not load frames”)
- Login section appears after intro (or go to `#login`)

Direct login alias:

```
https://dashboard.outpero.com/vk-realty/login
→ redirects to /vk-realty#login
```

### 3. Dashboard auth

Open: **https://dashboard.outpero.com/vk-realty/dashboard**

- Without login → redirect to `/vk-realty/login` → `/vk-realty#login`
- After login with `DASHBOARD_USERNAME` / `DASHBOARD_PASSWORD` → dashboard loads

### 4. Static assets

```bash
curl -sI https://dashboard.outpero.com/static/vk-realty/frames/CICD_0.jpg
# HTTP/2 200
```

### 5. Agent running

Dashboard → **System** → agent status = `running`

```bash
curl -s https://dashboard.outpero.com/api/system/agent-status
# (requires auth cookie when DISABLE_AUTH=false)
```

Inside container:

```bash
docker compose exec vk-realty supervisorctl -c /etc/supervisor/conf.d/supervisord.conf status
# agent    RUNNING
# ui_server RUNNING
```

### 6. Outbound test call

Dashboard → place call to a valid `+91` number.

Success JSON includes `dispatch_id` and `room`. If SSL error on dispatch, rebuild image (certifi fix in `livekit_api_client.py`).

### 7. Voice settings

**Settings → API Keys → Cartesia Voice** → enable **Restart agent on save** → **Save & apply**.

---

## Updates & rollback

```bash
cd /opt/clients/vk-realty

# Backup before update
tar czf ~/backup-$(date +%Y%m%d).tgz data/ config.json .env

docker compose build --pull
docker compose up -d
docker compose logs -f --tail=200
```

`data/`, `config.json`, and `.env` are on the host — they survive container rebuilds.

---

## Monitoring

```bash
# App logs
docker compose logs -f vk-realty

# Health (uptime probe target)
curl -s https://dashboard.outpero.com/health

# Prometheus
curl -s https://dashboard.outpero.com/metrics
```

Suggested GCP alerts: VM CPU > 80%, disk > 85%, `/health` not `ok` for 5 minutes.

---

## Security checklist

- [ ] `DISABLE_AUTH=false`
- [ ] `ENVIRONMENT=production`
- [ ] `ALLOWED_ORIGINS=https://dashboard.outpero.com` (no wildcard, no localhost)
- [ ] Strong `JWT_SECRET`, `DASHBOARD_PASSWORD`, `API_SETTINGS_PASSWORD`
- [ ] `.env` mode `600`, never in git
- [ ] Port 8000 **not** open in GCP firewall (only 80/443)
- [ ] SSH restricted to known IPs
- [ ] DNS `dashboard.outpero.com` → correct static IP only
- [ ] Supabase project isolated for this client

---

## Troubleshooting

### Wrong URL / 404 on dashboard

Use the **`/vk-realty` prefix**:

| Wrong | Correct |
|-------|---------|
| `https://dashboard.outpero.com/` | `https://dashboard.outpero.com/vk-realty` |
| `https://dashboard.outpero.com/dashboard` | `https://dashboard.outpero.com/vk-realty/dashboard` |

Add the Caddy root redirect (Step 8) so `/` goes to `/vk-realty`.

### Login loop after sign-in

- Confirm `ALLOWED_ORIGINS=https://dashboard.outpero.com`
- Confirm site is **HTTPS** (secure cookies require it with `ENVIRONMENT=production`)
- Hard-refresh or clear cookies for `dashboard.outpero.com`

### SSL error when placing a call

```
SSLCertVerificationError: certificate verify failed
```

Rebuild and restart (includes certifi CA bundle for LiveKit API):

```bash
docker compose build --no-cache && docker compose up -d
```

### “Could not load frames” on landing

```bash
ls frontend/vk-realty/frames/*.jpg | wc -l   # must be 114
curl -sI https://dashboard.outpero.com/static/vk-realty/frames/CICD_0.jpg
```

### Agent not running

```bash
docker compose exec vk-realty supervisorctl -c /etc/supervisor/conf.d/supervisord.conf restart agent
```

Check `LIVEKIT_URL`, `LIVEKIT_API_KEY`, `LIVEKIT_API_SECRET`, `LIVEKIT_AGENT_NAME`.

### Outbound call — missing trunk

Log: `Missing OUTBOUND_TRUNK_ID`  
Fix: set `OUTBOUND_TRUNK_ID=ST_…` in `.env`, then `docker compose restart`.

### Caddy certificate fails

- DNS must resolve to VM IP before reload
- Ports 80 and 443 open in firewall (Let's Encrypt HTTP-01)
- `sudo journalctl -u caddy -f` for errors

---

## Quick reference

| Production URL | Purpose |
|----------------|---------|
| https://dashboard.outpero.com/vk-realty | Landing + login |
| https://dashboard.outpero.com/vk-realty/dashboard | Main dashboard |
| https://dashboard.outpero.com/health | Health probe |
| https://dashboard.outpero.com/api/call/single | Outbound call API |

| Command | Action |
|---------|--------|
| `./deploy.sh` | Build + start |
| `docker compose ps` | Status |
| `docker compose logs -f` | Live logs |
| `docker compose restart` | Restart container |
| `sudo systemctl reload caddy` | Reload TLS / proxy |

---

*OUTPERO · VK Realty client · Layer 2 · July 2026*