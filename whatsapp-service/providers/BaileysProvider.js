const {
  default: makeWASocket,
  DisconnectReason,
  fetchLatestBaileysVersion,
} = require("@whiskeysockets/baileys");
const QRCode = require("qrcode");
const pino = require("pino");
const { usePostgresAuthState } = require("../authStateAdapter");
const { upsertSession } = require("../sessionDb");
const { WhatsAppProvider } = require("./WhatsAppProvider");

const SESSION_TTL_MS = 48 * 60 * 60 * 1000;

const logger = pino({ level: process.env.WHATSAPP_LOG_LEVEL || "warn" });

function extractMessagePayload(msg) {
  if (!msg?.message) return null;
  const m = msg.message;
  const text =
    m.conversation ||
    m.extendedTextMessage?.text ||
    m.imageMessage?.caption ||
    m.documentMessage?.caption ||
    "";
  let messageType = "text";
  if (m.imageMessage) messageType = "image";
  else if (m.audioMessage) messageType = "audio";
  else if (m.documentMessage) messageType = "document";
  if (!text && messageType === "text") return null;
  return {
    text: text || previewForType(messageType),
    messageType,
    waMessageId: msg.key?.id || null,
    from: msg.key?.remoteJid,
    fromMe: Boolean(msg.key?.fromMe),
    ts: Number(msg.messageTimestamp) * 1000,
    pushName: msg.pushName || null,
  };
}

function previewForType(messageType) {
  if (messageType === "image") return "[Image]";
  if (messageType === "audio") return "[Audio]";
  if (messageType === "document") return "[Document]";
  return "";
}

class BaileysProvider extends WhatsAppProvider {
  constructor() {
    super();
    this._sockets = new Map();
  }

  async start(orgId, handlers) {
    if (this._sockets.has(orgId)) return;

    await upsertSession(orgId, { status: "pending" });

    const { state, saveCreds } = await usePostgresAuthState(orgId, logger);
    const { version } = await fetchLatestBaileysVersion();

    const sock = makeWASocket({
      version,
      auth: state,
      logger,
      printQRInTerminal: false,
    });

    this._sockets.set(orgId, { sock, handlers });

    sock.ev.on("creds.update", saveCreds);

    sock.ev.on("connection.update", async (update) => {
      try {
        const { connection, lastDisconnect, qr } = update;

        if (qr) {
          const dataUrl = await QRCode.toDataURL(qr);
          handlers.onQr(dataUrl);
          handlers.onStatus("pending");
        }

        if (connection === "open") {
          const phoneNumber = sock.user?.id?.split(":")?.[0] || null;
          const connectedAt = new Date().toISOString();
          const expiresAt = new Date(Date.now() + SESSION_TTL_MS).toISOString();
          try {
            await upsertSession(orgId, {
              status: "connected",
              phone_number: phoneNumber,
              connected_at: connectedAt,
              expires_at: expiresAt,
            });
          } catch (err) {
            logger.warn({ err, orgId }, "failed to persist connected WhatsApp session");
          }
          handlers.onStatus("connected", { phoneNumber, expiresAt, connectedAt });
        }

        if (connection === "close") {
          const reason = lastDisconnect?.error?.output?.statusCode;
          const msg = String(lastDisconnect?.error?.message || "").toLowerCase();
          const deviceRemoved =
            msg.includes("device_removed") || msg.includes("conflict");
          this._sockets.delete(orgId);

          const terminal =
            reason === DisconnectReason.loggedOut ||
            reason === DisconnectReason.connectionReplaced ||
            deviceRemoved;

          if (terminal) {
            try {
              await upsertSession(orgId, { status: "disconnected", phone_number: null });
            } catch (err) {
              logger.warn({ err, orgId }, "failed to mark WhatsApp session disconnected");
            }
            handlers.onStatus("disconnected");
            return;
          }

          handlers.onStatus("pending");
          try {
            await this.start(orgId, handlers);
          } catch (err) {
            logger.error({ err, orgId }, "failed to restart WhatsApp session after disconnect");
            handlers.onStatus("disconnected");
          }
        }
      } catch (err) {
        logger.error({ err, orgId }, "connection.update handler error");
      }
    });

    sock.ev.on("messages.upsert", async ({ messages, type }) => {
      try {
        if (type !== "notify") return;
        for (const msg of messages) {
          const payload = extractMessagePayload(msg);
          if (!payload) continue;
          await Promise.resolve(handlers.onMessage(payload));
        }
      } catch (err) {
        logger.error({ err, orgId }, "messages.upsert handler error");
      }
    });
  }

  async stop(orgId) {
    const entry = this._sockets.get(orgId);
    if (!entry) return;
    try {
      await entry.sock.logout();
    } catch {
      /* socket may already be dead */
    }
    this._sockets.delete(orgId);
    try {
      await upsertSession(orgId, { status: "disconnected", phone_number: null });
    } catch (err) {
      logger.warn({ err, orgId }, "failed to mark WhatsApp session stopped");
    }
  }

  async send(orgId, to, text) {
    const entry = this._sockets.get(orgId);
    if (!entry) throw new Error(`No active WhatsApp session for org ${orgId}`);
    try {
      const result = await entry.sock.sendMessage(to, { text });
      return {
        waMessageId: result?.key?.id || null,
        to: result?.key?.remoteJid || to,
      };
    } catch (err) {
      const closed =
        String(err?.message || "").includes("Connection Closed") ||
        err?.output?.statusCode === 428;
      if (closed) {
        this._sockets.delete(orgId);
        throw new Error(
          "WhatsApp connection lost — open Connections and scan QR to reconnect"
        );
      }
      throw err;
    }
  }

  getSocket(orgId) {
    return this._sockets.get(orgId)?.sock || null;
  }

  async requestPairingCode(orgId, phoneNumber) {
    const entry = this._sockets.get(orgId);
    if (!entry) throw new Error(`No active WhatsApp session for org ${orgId}`);
    return entry.sock.requestPairingCode(phoneNumber);
  }
}

module.exports = { BaileysProvider };