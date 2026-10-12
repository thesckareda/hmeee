/**
 * Connected WhatsApp numbers on the Connections page — Chats link per number.
 */
(function () {
  function $(id) {
    return document.getElementById(id);
  }

  function esc(t) {
    return String(t ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function chatHref(numberId) {
    const base = window.location.pathname.includes("/tinglish-vk-realty")
      ? "/tinglish-vk-realty"
      : "";
    return `${base}/whatsapp/chat/${numberId}`;
  }

  async function renderConnectedNumbers() {
    const mount = $("whatsapp-numbers-panel");
    if (!mount) return;

    try {
      const data = await window.apiJson("/api/whatsapp/numbers");
      const items = (data.items || []).filter((n) => n.status === "connected" || n.phone_number);

      if (!items.length) {
        mount.hidden = true;
        return;
      }

      mount.hidden = false;
      mount.innerHTML = `
        <header class="wa-numbers-head">
          <h3>Connected numbers</h3>
          <p class="muted">Open chat history for messages on each linked WhatsApp line.</p>
        </header>
        <div class="wa-numbers-grid">
          ${items
            .map(
              (n) => `
            <article class="card wa-number-card">
              <div class="wa-number-card-top">
                <span class="wa-number-status wa-number-status--${esc(n.status)}">${esc(n.status || "unknown")}</span>
                <strong class="wa-number-phone">${esc(n.phone_number || "Linking…")}</strong>
              </div>
              <div class="wa-number-card-foot">
                <a class="btn btn-ghost btn-sm wa-number-chats-link" href="${chatHref(n.id)}" data-number-id="${n.id}">Chats</a>
                <button type="button" class="btn btn-primary btn-sm btn-speaksy btn-speaksy--soft" data-wa-manage="${n.id}">Manage</button>
              </div>
            </article>`
            )
            .join("")}
        </div>`;

      mount.querySelectorAll(".wa-number-chats-link").forEach((link) => {
        link.addEventListener("click", (e) => {
          e.preventDefault();
          const id = link.dataset.numberId;
          if (!id) return;
          const url = chatHref(id);
          history.pushState({}, "", url);
          window.WhatsAppChat?.open(id);
        });
      });

      mount.querySelectorAll("[data-wa-manage]").forEach((btn) => {
        btn.addEventListener("click", () => window.WhatsAppConnect?.openModal?.());
      });
    } catch {
      mount.hidden = true;
    }
  }

  function init() {
    if (!$("whatsapp-numbers-panel")) return;
    renderConnectedNumbers();
    document.addEventListener("speaksy:page-integrations", renderConnectedNumbers);
  }

  window.WhatsAppNumbers = { renderConnectedNumbers, init };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();