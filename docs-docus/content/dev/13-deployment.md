# Deployment

**Source files:** `docker-compose.yml`, `Dockerfile`, `deploy.sh`

## Local Dev

\`\`\`bash
docker-compose up -d
# or: python3 ui_server.py (with Redis on localhost:6379)
\`\`\`

Opens dashboard at `http://localhost:8002`.

## Production

### Docker Compose

```yaml
services:
  speaksy-engine:
    image: speaksy:latest
    environment:
      - REDIS_URL=redis://redis:6379/0
      - GEMINI_API_KEY=${GEMINI_API_KEY}
      - LIVEKIT_URL=${LIVEKIT_URL}
      ...
    depends_on:
      redis:
        condition: service_healthy
  redis:
    image: redis:7-alpine
    command: redis-server --maxmemory 256mb --maxmemory-policy volatile-lru
```

### Environment Variables

\`\`\`bash
REDIS_URL=redis://redis:6379/0
GEMINI_API_KEY=sk_...
LIVEKIT_URL=wss://...
LIVEKIT_API_KEY=...
LIVEKIT_API_SECRET=...
CARTESIA_API_KEY=...
SONIOX_API_KEY=...
\`\`\`

## Related
- [Runbook](./14-runbook)
- [Config Reference](./10-config-reference)
