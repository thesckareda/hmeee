const express = require("express");
const { sessionManager } = require("./sessionManager");

const INTERNAL_API_SECRET = process.env.WHATSAPP_INTERNAL_SECRET || "";

const router = express.Router();

function formatRouteError(err) {
  const code = err?.code || err?.details?.code;
  const message = String(err?.message || err || "unknown error");
  if (code === "42501" || /row-level security/i.test(message)) {
    return {
      status: 503,
      error:
        "Supabase denied access to whatsapp_sessions — set SUPABASE_SERVICE_ROLE_KEY in tinglish-vk-realty/.env (Project Settings → API → service_role secret), restart whatsapp-service, then retry.",
    };
  }
  if (code === "PGRST205") {
    return {
      status: 503,
      error:
        "Database table whatsapp_sessions is missing — run sql/supabase_migration_v7_whatsapp.sql in the Supabase SQL Editor, then retry.",
    };
  }
  return { status: 500, error: message };
}

// This service is only ever reached by ui_server.py's FastAPI proxy over the
// internal Docker network (see whatsapp_routes.py) — never directly by the
// browser. The shared secret is defense-in-depth in case the port is ever
// exposed by mistake, mirroring the reverse Node->Python internal call.
router.use((req, res, next) => {
  if (!INTERNAL_API_SECRET || req.header("X-Internal-Secret") === INTERNAL_API_SECRET) {
    return next();
  }
  res.status(403).json({ error: "forbidden" });
});

router.post("/session/start", express.json(), async (req, res) => {
  const { org_id } = req.body || {};
  if (!org_id) return res.status(400).json({ error: "org_id is required" });
  try {
    const status = await sessionManager.startSession(org_id);
    res.json(status);
  } catch (err) {
    console.error("[whatsapp] session/start failed:", err);
    const { status, error } = formatRouteError(err);
    res.status(status).json({ error });
  }
});

router.get("/session/:orgId/status", async (req, res) => {
  try {
    const status = await sessionManager.hydrateStatus(req.params.orgId);
    res.json(status);
  } catch (err) {
    console.error("[whatsapp] session/status failed:", err);
    const { status, error } = formatRouteError(err);
    res.status(status).json({ error });
  }
});

// Kept distinct from /status per the original spec (GET .../qr) even though
// the QR data lives on the same status object today.
router.get("/session/:orgId/qr", async (req, res) => {
  const status = await sessionManager.hydrateStatus(req.params.orgId);
  res.json({ qr: status.qr || null, status: status.status });
});

router.post("/session/:orgId/stop", async (req, res) => {
  await sessionManager.stopSession(req.params.orgId);
  res.json({ ok: true });
});

router.post("/session/:orgId/send", express.json(), async (req, res) => {
  const { to, text, sent_by, send_kind } = req.body || {};
  if (!to || !text) return res.status(400).json({ error: "to and text are required" });
  try {
    const result = await sessionManager.sendMessage(req.params.orgId, to, text, {
      sentBy: sent_by === "human_agent" ? "human_agent" : "assistant",
      sendKind: send_kind || "thread_reply",
    });
    res.json({ ok: true, wa_message_id: result?.waMessageId || null });
  } catch (err) {
    const msg = String(err.message || err);
    const status = /rate limit|daily outbound|business hours/i.test(msg) ? 429 : 500;
    res.status(status).json({ error: msg });
  }
});

router.post("/session/:orgId/pairing-code", express.json(), async (req, res) => {
  const { phone_number } = req.body || {};
  if (!phone_number) return res.status(400).json({ error: "phone_number is required" });
  const code = await sessionManager.requestPairingCode(req.params.orgId, phone_number);
  res.json({ code });
});

module.exports = router;
