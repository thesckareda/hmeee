/**
 * Outbound send pacing for Baileys — risk reduction, not a guarantee.
 *
 * - reply_inbound: fast reply in active thread (typing delay only)
 * - thread_reply: human/agent messaging known contact (short randomized pause)
 * - cold_outbound: first message to a new number (30–90s gap + daily cap)
 */
const MIN_REPLY_MS = Number(process.env.WHATSAPP_MIN_REPLY_DELAY_MS || 800);
const MAX_REPLY_MS = Number(process.env.WHATSAPP_MAX_REPLY_DELAY_MS || 3500);
const MIN_THREAD_MS = Number(process.env.WHATSAPP_MIN_THREAD_DELAY_MS || 2000);
const MAX_THREAD_MS = Number(process.env.WHATSAPP_MAX_THREAD_DELAY_MS || 8000);
const MIN_COLD_MS = Number(process.env.WHATSAPP_MIN_COLD_DELAY_MS || 30_000);
const MAX_COLD_MS = Number(process.env.WHATSAPP_MAX_COLD_DELAY_MS || 90_000);
const DAILY_CAP = Number(process.env.WHATSAPP_DAILY_SEND_CAP || 250);
const WORK_START_HOUR = Number(process.env.WHATSAPP_WORK_START_HOUR || 9);
const WORK_END_HOUR = Number(process.env.WHATSAPP_WORK_END_HOUR || 21);
const TZ_OFFSET_MIN = Number(process.env.WHATSAPP_TZ_OFFSET_MIN || 330); // IST default

const _lastSendAt = new Map(); // orgId -> epoch ms
const _dailyCounts = new Map(); // `${orgId}:${YYYY-MM-DD}` -> count
const _contactLastInbound = new Map(); // `${orgId}:${contact}` -> epoch ms

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

function randomBetween(min, max) {
  return min + Math.random() * (max - min);
}

function localHour() {
  const now = new Date();
  const utcMs = now.getTime() + now.getTimezoneOffset() * 60_000;
  const localMs = utcMs + TZ_OFFSET_MIN * 60_000;
  return new Date(localMs).getUTCHours();
}

function todayKey(orgId) {
  const d = new Date();
  return `${orgId}:${d.toISOString().slice(0, 10)}`;
}

function assertWorkingHours() {
  const hour = localHour();
  if (hour < WORK_START_HOUR || hour >= WORK_END_HOUR) {
    throw new Error(
      `Outbound WhatsApp sends are restricted to ${WORK_START_HOUR}:00–${WORK_END_HOUR}:00 (configured timezone). Try again during business hours.`
    );
  }
}

function trackInbound(orgId, contactJid) {
  const key = `${orgId}:${contactJid}`;
  _contactLastInbound.set(key, Date.now());
}

function classifySend(orgId, contactJid, kind) {
  if (kind) return kind;
  const key = `${orgId}:${contactJid}`;
  const lastIn = _contactLastInbound.get(key);
  if (lastIn && Date.now() - lastIn < 24 * 60 * 60_000) return "reply_inbound";
  return "cold_outbound";
}

async function applySendDelay(orgId, { kind, contactJid }) {
  assertWorkingHours();

  const sendKind = classifySend(orgId, contactJid, kind);
  const last = _lastSendAt.get(orgId) || 0;
  const elapsed = Date.now() - last;

  let requiredGap = 0;
  if (sendKind === "cold_outbound") {
    const dayKey = todayKey(orgId);
    const count = _dailyCounts.get(dayKey) || 0;
    if (count >= DAILY_CAP) {
      throw new Error(
        `Daily outbound cap (${DAILY_CAP}) reached for this number — pause until tomorrow or lower volume.`
      );
    }
    _dailyCounts.set(dayKey, count + 1);
    requiredGap = randomBetween(MIN_COLD_MS, MAX_COLD_MS);
  } else if (sendKind === "thread_reply") {
    requiredGap = randomBetween(MIN_THREAD_MS, MAX_THREAD_MS);
  } else {
    requiredGap = randomBetween(MIN_REPLY_MS, MAX_REPLY_MS);
  }

  const wait = Math.max(0, requiredGap - elapsed);
  if (wait > 0) await sleep(wait);
  _lastSendAt.set(orgId, Date.now());
}

/** Proportional typing indicator before send — mimics human compose time. */
async function simulateTyping(sock, text) {
  if (!sock?.sendPresenceUpdate) return;
  const chars = String(text || "").length;
  const typingMs = Math.min(8000, Math.max(600, chars * 35));
  try {
    await sock.sendPresenceUpdate("composing");
    await sleep(typingMs);
    await sock.sendPresenceUpdate("paused");
  } catch {
    /* presence is best-effort */
  }
}

module.exports = {
  applySendDelay,
  simulateTyping,
  trackInbound,
  classifySend,
};