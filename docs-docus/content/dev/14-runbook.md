# Runbook (Ops Procedures)

## Agent Doesn't Respond

**Detect:** Call goes silent, caller hears nothing.

**Cause:** LLM timeout or API failure.

**Fix:**
1. Check logs: `tail -f logs/app.log | grep -i "llm\|error"`
2. Verify API key and endpoint
3. Try a simpler LLM model (faster = less timeout)

**Prevent:** Monitor LLM response times; alert if p95 > 5s.

## Redis Down

**Detect:** Memory hit rate drops to 0, fallback to SQLite.

**Cause:** Redis service crashed or out of memory.

**Fix:**
1. \`redis-cli ping\` → should return PONG
2. If down: \`docker-compose up -d redis\`
3. Check memory: \`redis-cli INFO memory\`
4. If OOM: increase maxmemory in docker-compose.yml

**Prevent:** Set alert: Redis MAXMEMORY 80% full.

## Circuit Breaker (Redis Cooldown)

After 3 consecutive Redis failures, engine waits 30s before retry.

**Detect:** High latency spikes (fallback is slower).

**Why:** Prevents thundering herd if Redis is struggling.

**Resume:** Automatic after 30s, or manual restart.

## Related
- [Deployment](./13-deployment)
- [Memory System](./05-memory-system)
