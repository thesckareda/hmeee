// Simple per-org token bucket for outbound sends. Baileys is an unofficial
// protocol client — sending too fast is the single biggest ban-risk factor,
// so every outbound send (including AI-generated replies) goes through this.
const MAX_TOKENS = Number(process.env.WHATSAPP_MAX_MSGS_PER_MIN || 20);
const REFILL_INTERVAL_MS = 60_000;

class RateLimiter {
  constructor() {
    // org_id -> { tokens, lastRefill }
    this._buckets = new Map();
  }

  _bucket(orgId) {
    let bucket = this._buckets.get(orgId);
    if (!bucket) {
      bucket = { tokens: MAX_TOKENS, lastRefill: Date.now() };
      this._buckets.set(orgId, bucket);
    }
    const elapsed = Date.now() - bucket.lastRefill;
    if (elapsed >= REFILL_INTERVAL_MS) {
      bucket.tokens = MAX_TOKENS;
      bucket.lastRefill = Date.now();
    }
    return bucket;
  }

  // Returns true if the send is allowed (and consumes a token), false if the
  // org has exceeded its per-minute budget.
  tryConsume(orgId) {
    const bucket = this._bucket(orgId);
    if (bucket.tokens <= 0) return false;
    bucket.tokens -= 1;
    return true;
  }
}

module.exports = { rateLimiter: new RateLimiter() };
