# Deployment Architecture Audit — Monorepo → Client Layer

This audit maps **incorrect infrastructure assumptions** in the main repository under the new two-layer model.

## Architecture recap

| Layer | Path | Contains |
|-------|------|----------|
| **Layer 1 — Infrastructure** | `/opt/infrastructure` | LiveKit, SIP, Egress, Redis, Caddy, Monitoring |
| **Layer 2 — Client** | `/opt/clients/<customer>/` | agent, dashboard, knowledge, configs, logs, `.env` |

Application rule: connect only via `LIVEKIT_URL` + `LIVEKIT_API_KEY` + `LIVEKIT_API_SECRET`.

---

## Findings (monorepo)

### 1. `docker-compose.yml` (root)

| Line | Issue | Fix | Layer |
|------|-------|-----|-------|
| 38–57 | Embeds **nginx** reverse proxy | Remove nginx service; expose `voice-app` port directly or use external Caddy | Infrastructure |
| 22–23 | `extra_hosts: host.docker.internal` for host LiveKit | Remove; agent uses `LIVEKIT_URL` only | Client |
| 46 | Mounts `nginx.conf` | Delete from client compose | Infrastructure |

**Client fix:** Use `scripts/client-template/docker-compose.yml` (single service, no nginx/extra_hosts).

### 2. `docker-compose-kb.yml`

| Line | Issue | Fix | Layer |
|------|-------|-----|-------|
| entire file | Embeds **Qdrant** vector DB | Remove from client; VK live calls use `knowledge_pack.py` JSON path | Optional infra / not needed for VK |

### 3. `nginx.conf`

| Issue | Fix | Layer |
|-------|-----|-------|
| TLS termination + routing in app repo | Move to Caddy on infrastructure VM | Infrastructure |

### 4. `setup_trunk.py`, `setup_inbound_dispatch.py`, `setup_bidirectional_trunk.py`

| Issue | Fix | Layer |
|-------|-----|-------|
| Provision SIP trunks via LiveKit API | Run once on infrastructure VM, not per client | Infrastructure |

**Client pack:** These files are **excluded** from `pack_vk_realty_client.sh`.

### 5. `livekit_turn.py`

| Issue | Fix | Layer |
|-------|-----|-------|
| Client-side TURN credential generation / ICE override | Only needed if SFU mis-advertises ICE; with speaksy.online, leave unset | Infrastructure (TURN server) |

### 6. `.env` / `.env.example` (root)

| Variables | Issue | Fix | Layer |
|-----------|-------|-----|-------|
| `SIP_TRUNK_ID`, `SIP_DISPATCH_RULE_ID`, `LIVEKIT_SIP_URI` | SIP provisioning secrets | Remove from client `.env` | Infrastructure |
| `REDIS_URL` | Embedded Redis | Remove; not used by VK JSON knowledge path | Infrastructure |
| `LIVEKIT_API_URL` (HTTP) | Assumes co-located LiveKit HTTP API | Use SDK with `LIVEKIT_URL` only | Client uses WSS endpoint |
| `VOBIZ_*`, `OUTBOUND_TRUNK_ID` | Telephony provider config | Infrastructure SIP provisioning | Infrastructure |

**Client fix:** `scripts/sanitize_client_env.sh` strips these on pack.

### 7. `knowledge_system_config.json`

| Issue | Fix | Layer |
|-------|-----|-------|
| `localhost:6333` Qdrant endpoint | Not used on VK live call path (`knowledge_pack.py`) | Remove or move to optional infra KB pipeline |

### 8. `make_call.py`

| Issue | Fix | Layer |
|-------|-----|-------|
| Outbound SIP trunk setup assumptions | Ops tool for infrastructure admin | Infrastructure |

### 9. `env_manager.py` (dashboard)

| Issue | Fix | Layer |
|-------|-----|-------|
| Exposes `SIP_TRUNK_ID` in settings UI | Hide infra keys in client dashboard | Client (UI filter) |

### 10. `system_control.py`

| Issue | Fix | Layer |
|-------|-----|-------|
| May restart/manage local services | Client must never start/restart LiveKit | Client — disable or remove |

---

## VK Realty client package (resolved)

Path: `~/Downloads/speaksy-clients/vk-realty/`

| Requirement | Status |
|-------------|--------|
| No nginx / Caddy in compose | ✅ |
| No `extra_hosts` | ✅ |
| No embedded Redis/SIP/TURN | ✅ |
| Tinglish/HME knowledge packs | ✅ `data/company_knowledge/` |
| VK Realty dashboard | ✅ `frontend/vk-realty/` |
| Agent (Arjun) + `ui_server` | ✅ |
| Connects to `wss://livekit.speaksy.online` | ✅ sanitized `.env` |
| Onboard = copy + `.env` + `docker compose up -d` | ✅ `deploy.sh` |

## Onboarding flow (production)

```bash
mkdir -p /opt/clients/vk-realty
# rsync or scp pack contents
cd /opt/clients/vk-realty
cp .env.example .env   # set LIVEKIT_* + provider API keys
./deploy.sh
```

No changes to `/opt/infrastructure` required.