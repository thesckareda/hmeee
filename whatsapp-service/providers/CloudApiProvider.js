const { WhatsAppProvider } = require("./WhatsAppProvider");

/** Stub for a future Meta WhatsApp Cloud API implementation. */
class CloudApiProvider extends WhatsAppProvider {
  async start() {
    throw new Error("WhatsApp Cloud API provider is not implemented yet — set WHATSAPP_PROVIDER=baileys");
  }

  async stop() {
    throw new Error("WhatsApp Cloud API provider is not implemented yet");
  }

  async send() {
    throw new Error("WhatsApp Cloud API provider is not implemented yet");
  }

  async requestPairingCode() {
    throw new Error("WhatsApp Cloud API provider is not implemented yet");
  }
}

module.exports = { CloudApiProvider };