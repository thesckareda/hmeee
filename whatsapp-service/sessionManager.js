const { BaileysProvider } = require("./providers/BaileysProvider");
const { CloudApiProvider } = require("./providers/CloudApiProvider");
const { getSession, listResumableSessions } = require("./sessionDb");
const { getSessionByOrgId, ingestMessage } = require("./messageDb");
const { rateLimiter } = require("./rateLimiter");
const { applySendDelay, simulateTyping, trackInbound } = require("./sendPolicy");

const PYTHON_APP_URL = process.env.PYTHON_APP_URL || "http://tinglish-vk-realty:8000";
const INTERNAL_API_SECRET = process.env.WHATSAPP_INTERNAL_SECRET || "";

function buildProvider() {
  const kind = (process.env.WHATSAPP_PROVIDER || "baileys").toLowerCase();
  if (kind === "baileys") return new BaileysProvider();
  if (kind === "cloud_api") return new CloudApiProvider();
  throw new Error(`Unknown WHATSAPP_PROVIDER "${kind}" — use "baileys" or "cloud_api"`);
}

class SessionManager {
  constructor() {
    this._provider = buildProvider();
    this._sessions = new Map();
    this._sessionIds = new Map(); // org_id -> whatsapp_sessions.id
  }

  _setSession(orgId, patch) {
    const prev = this._sessions.get(orgId) || {};
    this._sessions.set(orgId, { ...prev, ...patch, updatedAt: Date.now() });
  }

  getStatus(orgId) {
    return this._sessions.get(orgId) || { status: "not_started" };
  }

  async _resolveSessionId(orgId) {
    if (this._sessionIds.has(orgId)) return this._sessionIds.get(orgId);
    const row = await getSessionByOrgId(orgId);
    if (row?.id) this._sessionIds.set(orgId, row.id);
    return row?.id || null;
  }

  async hydrateStatus(orgId) {
    const mem = this._sessions.get(orgId);
    if (mem) return mem;
    try {
      const row = await getSession(orgId);
      if (!row) return { status: "not_started" };
      if (row.id) this._sessionIds.set(orgId, row.id);
      return {
        id: row.id,
        status: row.status,
        phoneNumber: row.phone_number,
        expiresAt: row.expires_at,
        connectedAt: row.connected_at,
      };
    } catch (err) {
      console.error(`[whatsapp] failed to hydrate session for org ${orgId}:`, err);
      return { status: "not_started" };
    }
  }

  async resumePersistedSessions() {
    try {
      const rows = await listResumableSessions();
      for (const row of rows) {
        if (this._sessions.has(row.org_id)) continue;
        console.log(`[whatsapp] resuming session for org ${row.org_id} (${row.status})`);
        await this.startSession(row.org_id);
      }
    } catch (err) {
      console.error("[whatsapp] failed to resume persisted sessions:", err);
    }
  }

  async startSession(orgId) {
    this._setSession(orgId, { status: "pending", qr: null });

    await this._provider.start(orgId, {
      onQr: (dataUrl) => this._setSession(orgId, { qr: dataUrl }),
      onStatus: (status, extra = {}) => {
        const patch = { status, ...extra };
        if (status === "connected") patch.qr = null;
        this._setSession(orgId, patch);
      },
      onMessage: (message) => this._handleMessage(orgId, message),
    });

    const hydrated = await this.hydrateStatus(orgId);
    return { ...this.getStatus(orgId), id: hydrated.id || null };
  }

  async stopSession(orgId) {
    await this._provider.stop(orgId);
    this._setSession(orgId, { status: "disconnected", qr: null });
  }

  async requestPairingCode(orgId, phoneNumber) {
    return this._provider.requestPairingCode(orgId, phoneNumber);
  }

  /**
   * Outbound send with anti-ban pacing + Postgres persistence.
   * @param {object} options
   * @param {'assistant'|'human_agent'} options.sentBy
   * @param {'reply_inbound'|'thread_reply'|'cold_outbound'} options.sendKind
   * @param {boolean} options.skipPersist
   */
  async sendMessage(orgId, to, text, options = {}) {
    const {
      sentBy = "assistant",
      sendKind = "reply_inbound",
      skipPersist = false,
    } = options;

    if (!rateLimiter.tryConsume(orgId)) {
      throw new Error(`Rate limit exceeded for org ${orgId} — try again shortly`);
    }

    await applySendDelay(orgId, { kind: sendKind, contactJid: to });

    const sock = this._provider.getSocket?.(orgId);
    if (sock) await simulateTyping(sock, text);

    const result = await this._provider.send(orgId, to, text);

    if (!skipPersist) {
      const sessionId = await this._resolveSessionId(orgId);
      if (sessionId) {
        await ingestMessage({
          orgId,
          whatsappNumberId: sessionId,
          contactJid: to,
          direction: "outbound",
          messageType: "text",
          content: text,
          sentBy,
          status: "sent",
          waMessageId: result?.waMessageId,
        });
      }
    }

    return result;
  }

  async _handleMessage(orgId, message) {
    const sessionId = await this._resolveSessionId(orgId);
    if (!sessionId) {
      console.error(`[whatsapp] no session row for org ${orgId} — skipping ingest`);
      return;
    }

    if (message.fromMe) {
      // Outbound echoes are persisted by sendMessage — skip to avoid dupes
      return;
    }

    trackInbound(orgId, message.from);

    try {
      await ingestMessage({
        orgId,
        whatsappNumberId: sessionId,
        contactJid: message.from,
        contactName: message.pushName,
        direction: "inbound",
        messageType: message.messageType || "text",
        content: message.text,
        sentBy: "contact",
        status: "delivered",
        waMessageId: message.waMessageId,
        ts: message.ts,
      });
    } catch (err) {
      console.error(`[whatsapp] failed to ingest inbound for org ${orgId}:`, err);
    }

    // AI auto-reply pipeline (inbound only)
    try {
      const status = this.getStatus(orgId);
      const res = await fetch(`${PYTHON_APP_URL}/api/whatsapp/internal/incoming`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Internal-Secret": INTERNAL_API_SECRET,
        },
        body: JSON.stringify({
          org_id: orgId,
          from: message.from,
          text: message.text,
          ts: message.ts,
          to_number: status.phoneNumber || null,
        }),
      });

      if (!res.ok) {
        console.error(`[whatsapp] reply engine returned ${res.status} for org ${orgId}`);
        return;
      }

      const { reply } = await res.json();
      if (reply) {
        await this.sendMessage(orgId, message.from, reply, {
          sentBy: "assistant",
          sendKind: "reply_inbound",
        });
      }
    } catch (err) {
      console.error(`[whatsapp] failed to route inbound message for org ${orgId}:`, err);
    }
  }
}

module.exports = { sessionManager: new SessionManager() };