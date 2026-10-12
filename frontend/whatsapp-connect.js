/**
 * Phone WhatsApp QR-connect flow (tinglish-vk-realty pilot).
 * Add Number → pick method → scan QR → poll until connected.
 */
(function () {
  const POLL_MS = 4000;
  const METHODS = [
    {
      id: "phone_whatsapp",
      name: "Phone WhatsApp",
      desc: "Scan a QR code with your phone to link WhatsApp Web. Inbound messages get AI replies using the same knowledge pack as voice calls.",
      live: true,
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="#25D366"/><path d="M14 26l1.5-5.5A8 8 0 1 1 26 14.5 8 8 0 0 1 15.5 20.5L14 26z" fill="#fff"/></svg>`,
    },
    {
      id: "twilio_import",
      name: "Twilio Import",
      desc: "Import an existing Twilio WhatsApp sender into Speaksy.",
      live: false,
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="#F22F46"/><circle cx="20" cy="20" r="8" fill="#fff"/><circle cx="20" cy="20" r="4" fill="#F22F46"/></svg>`,
    },
    {
      id: "cloud_business",
      name: "WhatsApp Cloud Business",
      desc: "Connect via Meta's official WhatsApp Cloud API.",
      live: false,
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="#1877F2"/><path d="M14 26l1.5-5.5A8 8 0 1 1 26 14.5 8 8 0 0 1 15.5 20.5L14 26z" fill="#fff"/></svg>`,
    },
    {
      id: "interakt_import",
      name: "Interakt Import",
      desc: "Migrate an Interakt WhatsApp Business account.",
      live: false,
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="8" fill="#111"/><text x="20" y="25" text-anchor="middle" fill="#63dcb4" font-size="9" font-weight="700" font-family="system-ui,sans-serif">IK</text></svg>`,
    },
  ];

  let pollTimer = null;
  let state = "idle";

  function $(id) {
    return document.getElementById(id);
  }

  function esc(t) {
    return String(t ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function friendlyWhatsAppError(err, fallback) {
    const msg = String(err?.message || "").trim();
    if (msg === "Not Found" || msg.includes("404")) {
      return "WhatsApp API is not available — restart the dashboard server to load the latest code.";
    }
    if (msg.includes("503") || /unavailable/i.test(msg)) {
      return "WhatsApp service is not running. Start it with: cd whatsapp-service && npm start";
    }
    return msg || fallback;
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function setView(view) {
    state = view;
    $("wa-connect-methods")?.toggleAttribute("hidden", view !== "methods");
    $("wa-connect-qr")?.toggleAttribute("hidden", view !== "qr");
    $("wa-connect-connected")?.toggleAttribute("hidden", view !== "connected");
    $("wa-connect-error")?.toggleAttribute("hidden", view !== "error");
    $("wa-connect-disconnected")?.toggleAttribute("hidden", view !== "disconnected");
  }

  function renderMethods() {
    const grid = $("wa-connect-methods-grid");
    if (!grid) return;
    grid.innerHTML = METHODS.map((m) => `
      <article class="integration-card card wa-method-card" data-method="${m.id}">
        <div class="integration-card-top">
          <div class="integration-logo">${m.logo}</div>
          <div class="integration-card-head">
            <h4 class="integration-name">${esc(m.name)}</h4>
            ${m.live ? `<span class="integration-connected-badge">Available</span>` : `<span class="integration-timing integration-timing--post-call"><span class="integration-timing-dot"></span>Coming soon</span>`}
          </div>
        </div>
        <p class="integration-desc muted">${esc(m.desc)}</p>
        <div class="integration-card-foot">
          <button type="button" class="btn btn-primary btn-sm btn-speaksy btn-speaksy--soft wa-method-btn" data-method="${m.id}" ${m.live ? "" : "disabled"}>
            ${m.live ? "Connect" : "Notify me"}
          </button>
        </div>
      </article>
    `).join("");
  }

  async function refreshStatus() {
    try {
      const data = await window.apiJson("/api/whatsapp/session/status");
      const status = data.status || "not_started";

      if (status === "connected") {
        stopPolling();
        const phone = data.phoneNumber || data.phone_number || "Linked";
        const connectedEl = $("wa-connect-connected-phone");
        if (connectedEl) connectedEl.textContent = phone;
        setView("connected");
        window.showToast?.("WhatsApp connected", "success");
        return;
      }

      if (status === "disconnected") {
        stopPolling();
        setView("disconnected");
        return;
      }

      if (status === "expired") {
        stopPolling();
        $("wa-connect-error-msg") && ($("wa-connect-error-msg").textContent = "Session expired — scan again to reconnect.");
        setView("error");
        return;
      }

      const qrImg = $("wa-connect-qr-img");
      if (qrImg && data.qr) {
        qrImg.src = data.qr;
        qrImg.hidden = false;
      }
      const wait = $("wa-connect-qr-wait");
      if (wait) wait.hidden = Boolean(data.qr);
    } catch (err) {
      stopPolling();
      $("wa-connect-error-msg") && ($("wa-connect-error-msg").textContent = friendlyWhatsAppError(err, "Could not reach WhatsApp service"));
      setView("error");
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(refreshStatus, POLL_MS);
    refreshStatus();
  }

  async function startPhoneWhatsApp() {
    setView("qr");
    const qrImg = $("wa-connect-qr-img");
    const wait = $("wa-connect-qr-wait");
    if (qrImg) {
      qrImg.hidden = true;
      qrImg.removeAttribute("src");
    }
    if (wait) wait.hidden = false;

    try {
      await window.apiJson("/api/whatsapp/session/start", { method: "POST" });
      startPolling();
    } catch (err) {
      $("wa-connect-error-msg") && ($("wa-connect-error-msg").textContent = friendlyWhatsAppError(err, "Failed to start WhatsApp session"));
      setView("error");
    }
  }

  async function stopSession() {
    stopPolling();
    try {
      await window.apiJson("/api/whatsapp/session/stop", { method: "POST" });
      window.showToast?.("WhatsApp disconnected", "info");
    } catch (err) {
      window.showToast?.(err.message || "Disconnect failed", "error");
    }
    setView("methods");
  }

  async function checkExistingSession() {
    try {
      const data = await window.apiJson("/api/whatsapp/session/status");
      if (data.status === "connected") {
        const phone = data.phoneNumber || data.phone_number || "Linked";
        const connectedEl = $("wa-connect-connected-phone");
        if (connectedEl) connectedEl.textContent = phone;
        setView("connected");
        return true;
      }
      if (data.status === "disconnected") {
        setView("disconnected");
        return true;
      }
    } catch {
      /* service may be offline — show method picker */
    }
    return false;
  }

  async function openModal() {
    const modal = $("modal-whatsapp-connect");
    if (!modal) return;
    renderMethods();
    const existing = await checkExistingSession();
    if (!existing) setView("methods");
    modal.hidden = false;
    document.body.classList.add("modal-open");
    window.renderLucideIcons?.(modal);
  }

  function closeModal() {
    stopPolling();
    const modal = $("modal-whatsapp-connect");
    if (!modal) return;
    modal.hidden = true;
    document.body.classList.remove("modal-open");
    setView("methods");
  }

  function bindEvents() {
    $("integrations-add-number-btn")?.addEventListener("click", openModal);

    $("modal-whatsapp-connect")?.addEventListener("click", (e) => {
      if (e.target.closest("[data-close-modal]")) closeModal();
    });

    $("wa-connect-methods-grid")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".wa-method-btn");
      if (!btn?.dataset.method) return;
      if (btn.dataset.method === "phone_whatsapp") startPhoneWhatsApp();
      else window.showToast?.("This connection method is coming soon", "info");
    });

    $("wa-connect-retry-btn")?.addEventListener("click", () => {
      setView("methods");
    });

    $("wa-connect-rescan-btn")?.addEventListener("click", () => {
      startPhoneWhatsApp();
    });

    $("wa-connect-disconnect-btn")?.addEventListener("click", stopSession);
    $("wa-connect-done-btn")?.addEventListener("click", closeModal);
  }

  function init() {
    if (!$("modal-whatsapp-connect")) return;
    bindEvents();
  }

  window.WhatsAppConnect = { init, openModal, closeModal, refreshStatus, stopPolling };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();