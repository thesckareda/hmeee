const express = require("express");
const whatsappRoutes = require("./routes");
const { sessionManager } = require("./sessionManager");

// Baileys can throw async errors on disconnect — never crash the whole service.
process.on("unhandledRejection", (reason) => {
  console.error("[whatsapp-service] unhandled rejection (continuing):", reason);
});
process.on("uncaughtException", (err) => {
  console.error("[whatsapp-service] uncaught exception (continuing):", err);
});

const app = express();
const PORT = process.env.PORT || 4001;

app.use(express.json({ limit: "1mb" }));
app.get("/health", (_req, res) => res.json({ ok: true }));
app.use("/whatsapp", whatsappRoutes);

app.listen(PORT, () => {
  console.log(`[whatsapp-service] listening on :${PORT}`);
  sessionManager.resumePersistedSessions().catch((err) => {
    console.error("[whatsapp-service] resumePersistedSessions failed:", err);
  });
});
