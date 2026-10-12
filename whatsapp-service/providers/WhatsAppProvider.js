// Interface every WhatsApp channel implementation must satisfy. sessionManager.js
// only ever talks to this shape — swapping `baileys` for a future `cloud_api`
// implementation (Meta's official WhatsApp Cloud API) means writing a new class
// here and switching the `whatsapp_provider` config flag; nothing else changes.
class WhatsAppProvider {
  // start(orgId, handlers) — begin (or resume) a session for orgId.
  // handlers: { onQr(dataUrl), onStatus(status, extra), onMessage({from, text, ts}) }
  async start(_orgId, _handlers) {
    throw new Error("start() not implemented");
  }

  async stop(_orgId) {
    throw new Error("stop() not implemented");
  }

  async send(_orgId, _to, _text) {
    throw new Error("send() not implemented");
  }

  async requestPairingCode(_orgId, _phoneNumber) {
    throw new Error("requestPairingCode() not implemented");
  }
}

module.exports = { WhatsAppProvider };
