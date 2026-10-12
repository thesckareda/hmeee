/**
 * Integrations marketplace — connect third-party services to Speaksy voice agents.
 */
(function () {
  const STORAGE_KEY = "speaksy-integrations-connected";

  const CATEGORIES = [
    { id: "all", label: "All" },
    { id: "calendar_crm", label: "Calendar & CRM" },
    { id: "messaging", label: "Messaging" },
    { id: "data_sheets", label: "Data & Sheets" },
    { id: "custom_tools", label: "Custom & Tools" },
  ];

  const INTEGRATIONS = [
    {
      id: "calcom",
      name: "Cal.com",
      category: "calendar_crm",
      timing: "during_call",
      description: "Sync your Cal.com calendar to allow voice assistants to schedule meetings on your behalf.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="8" fill="#111"/><text x="20" y="24" text-anchor="middle" fill="#fff" font-size="7" font-weight="700" font-family="system-ui,sans-serif">cal.com</text></svg>`,
    },
    {
      id: "calendly",
      name: "Calendly",
      category: "calendar_crm",
      timing: "during_call",
      description: "Connect your Calendly account to check availability and schedule appointments through your voice assistants.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="#006BFF"/><path d="M14 26V14h4.5v12H14zm11.5 0V14H30v12h-4.5z" fill="#fff"/></svg>`,
    },
    {
      id: "google_calendar",
      name: "Google Calendar",
      category: "calendar_crm",
      timing: "during_call",
      description: "Connect your Google Calendar to check availability and schedule appointments through your voice assistants.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="8" fill="#fff"/><rect x="4" y="10" width="32" height="26" rx="4" fill="#fff" stroke="#4285F4" stroke-width="2"/><rect x="4" y="10" width="32" height="8" fill="#4285F4"/><text x="20" y="30" text-anchor="middle" fill="#4285F4" font-size="14" font-weight="700" font-family="system-ui,sans-serif">31</text></svg>`,
    },
    {
      id: "salesforce",
      name: "Salesforce",
      category: "calendar_crm",
      timing: "post_call",
      description: "Connect your Salesforce CRM to access customer data, manage leads, and update records through your voice assistants.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="#00A1E0"/><path d="M12 24c2-4 5-6 8-6s6 2 8 6c-2 3-5 5-8 5s-6-2-8-5z" fill="#fff" opacity="0.95"/></svg>`,
    },
    {
      id: "hubspot",
      name: "HubSpot",
      category: "calendar_crm",
      timing: "post_call",
      description: "Sync leads and call outcomes to HubSpot CRM — contacts, deals, and activity timeline after each call.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="#FF7A59"/><circle cx="26" cy="16" r="5" fill="#fff"/><circle cx="14" cy="22" r="3" fill="#fff"/><circle cx="22" cy="26" r="2.5" fill="#fff"/></svg>`,
    },
    {
      id: "zoho",
      name: "Zoho CRM",
      category: "calendar_crm",
      timing: "post_call",
      description: "Push call summaries, extracted variables, and lead scores into Zoho CRM automatically after calls end.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="8" fill="#E42527"/><text x="20" y="25" text-anchor="middle" fill="#fff" font-size="11" font-weight="800" font-family="system-ui,sans-serif">Z</text></svg>`,
    },
    {
      id: "whatsapp",
      name: "WhatsApp Notifications",
      category: "messaging",
      timing: "post_call",
      description: "Send listing details, follow-ups, and booking confirmations via WhatsApp after calls (outbound notifications).",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="#25D366"/><path d="M14 26l1.5-5.5A8 8 0 1 1 26 14.5 8 8 0 0 1 15.5 20.5L14 26z" fill="#fff"/></svg>`,
    },
    {
      id: "twilio_sms",
      name: "Twilio SMS",
      category: "messaging",
      timing: "post_call",
      description: "Trigger SMS notifications and two-way text follow-ups from your voice agents using Twilio.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="#F22F46"/><circle cx="20" cy="20" r="8" fill="#fff"/><circle cx="20" cy="20" r="4" fill="#F22F46"/></svg>`,
    },
    {
      id: "slack",
      name: "Slack",
      category: "messaging",
      timing: "post_call",
      description: "Post call summaries, hot-lead alerts, and escalation notices to Slack channels in real time.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="8" fill="#4A154B"/><rect x="10" y="16" width="8" height="8" rx="2" fill="#E01E5A"/><rect x="22" y="10" width="8" height="8" rx="2" fill="#36C5F0"/><rect x="22" y="22" width="8" height="8" rx="2" fill="#2EB67D"/><rect x="10" y="22" width="8" height="8" rx="2" fill="#ECB22E"/></svg>`,
    },
    {
      id: "telegram",
      name: "Telegram",
      category: "messaging",
      timing: "post_call",
      description: "Deliver call transcripts and lead alerts to Telegram bots for your sales team.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><circle cx="20" cy="20" r="20" fill="#26A5E4"/><path d="M12 20l14-6-3 12-4-3-5 4v-7z" fill="#fff"/></svg>`,
    },
    {
      id: "email_smtp",
      name: "Email (SMTP)",
      category: "messaging",
      timing: "post_call",
      description: "Email call summaries and extracted lead data to your team using SMTP or transactional providers.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="8" fill="#6366F1"/><path d="M10 14h20v12H10V14zm0 0l10 8 10-8" fill="none" stroke="#fff" stroke-width="2" stroke-linejoin="round"/></svg>`,
    },
    {
      id: "google_sheets",
      name: "Google Sheets",
      category: "data_sheets",
      timing: "post_call",
      description: "Connect your Google Sheets to read, write, and manage spreadsheet data through your voice assistants.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="8" fill="#0F9D58"/><rect x="12" y="10" width="16" height="20" rx="2" fill="#fff"/><line x1="16" y1="16" x2="24" y2="16" stroke="#0F9D58" stroke-width="1.5"/><line x1="16" y1="20" x2="24" y2="20" stroke="#0F9D58" stroke-width="1.5"/><line x1="16" y1="24" x2="22" y2="24" stroke="#0F9D58" stroke-width="1.5"/></svg>`,
    },
    {
      id: "airtable",
      name: "Airtable",
      category: "data_sheets",
      timing: "post_call",
      description: "Log leads, call notes, and pipeline stages to Airtable bases automatically after each conversation.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><polygon points="20,6 34,14 34,30 20,38 6,30 6,14" fill="#FCB400"/><polygon points="20,6 34,14 20,22 6,14" fill="#18BFFF"/><polygon points="20,22 34,14 34,30 20,38" fill="#F82B60"/><polygon points="20,22 6,14 6,30 20,38" fill="#00D084"/></svg>`,
    },
    {
      id: "custom_api",
      name: "Custom API",
      category: "custom_tools",
      timing: "during_call",
      description: "Connect to any custom API endpoint to extend your assistant's capabilities with external data and services.",
      logo: `<svg viewBox="0 0 40 40" aria-hidden="true"><rect width="40" height="40" rx="8" fill="#1e1e24" stroke="rgba(255,255,255,0.15)" stroke-width="1"/><circle cx="20" cy="16" r="5" fill="none" stroke="#63dcb4" stroke-width="1.5"/><path d="M14 28h12" stroke="#63dcb4" stroke-width="1.5" stroke-linecap="round"/><text x="20" y="27" text-anchor="middle" fill="#fff" font-size="7" font-weight="700" font-family="system-ui,sans-serif">API</text></svg>`,
    },
  ];

  const ui = {
    category: "all",
    tab: "all",
    query: "",
  };

  function $(id) {
    return document.getElementById(id);
  }

  function loadConnected() {
    try {
      return JSON.parse(localStorage.getItem(STORAGE_KEY) || "{}");
    } catch {
      return {};
    }
  }

  function saveConnected(map) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(map));
  }

  function isConnected(id) {
    return Boolean(loadConnected()[id]);
  }

  function setConnected(id, value) {
    const map = loadConnected();
    if (value) map[id] = { connected_at: new Date().toISOString() };
    else delete map[id];
    saveConnected(map);
  }

  function categoryCount(catId) {
    if (catId === "all") return INTEGRATIONS.length;
    return INTEGRATIONS.filter((i) => i.category === catId).length;
  }

  function timingLabel(timing) {
    return timing === "during_call" ? "During Call" : "Post Call";
  }

  function filteredIntegrations() {
    return INTEGRATIONS.filter((item) => {
      if (ui.category !== "all" && item.category !== ui.category) return false;
      if (ui.tab === "connected" && !isConnected(item.id)) return false;
      if (ui.query) {
        const q = ui.query.toLowerCase();
        const hay = `${item.name} ${item.description}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  function renderFilters() {
    const el = $("integrations-filters");
    if (!el) return;
    el.innerHTML = CATEGORIES.map((cat) => {
      const count = categoryCount(cat.id);
      const active = ui.category === cat.id ? " is-active" : "";
      const label = cat.id === "all" ? cat.label : `${cat.label}`;
      return `<button type="button" class="integrations-filter-chip${active}" data-category="${cat.id}">${label} <span class="integrations-filter-count">${count}</span></button>`;
    }).join("");
  }

  function renderGrid() {
    const grid = $("integrations-grid");
    const empty = $("integrations-empty");
    if (!grid) return;

    const items = filteredIntegrations();
    if (!items.length) {
      grid.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    grid.innerHTML = items.map((item) => {
      const connected = isConnected(item.id);
      const timingClass = item.timing === "during_call" ? "during-call" : "post-call";
      return `
        <article class="integration-card card" data-integration-id="${item.id}">
          <div class="integration-card-top">
            <div class="integration-logo">${item.logo}</div>
            <div class="integration-card-head">
              <h4 class="integration-name">${item.name}</h4>
              <span class="integration-timing integration-timing--${timingClass}">
                <span class="integration-timing-dot" aria-hidden="true"></span>
                ${timingLabel(item.timing)}
              </span>
            </div>
          </div>
          <p class="integration-desc muted">${item.description}</p>
          <div class="integration-card-foot">
            <button type="button" class="btn btn-primary btn-sm btn-speaksy btn-speaksy--soft integration-connect-btn" data-id="${item.id}">
              ${connected ? "Manage" : "Connect"}
              <svg class="integration-ext-icon" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5" aria-hidden="true"><path d="M6 3h7v7"/><path d="M13 3 6 10"/></svg>
            </button>
            ${connected ? `<span class="integration-connected-badge">Connected</span>` : ""}
          </div>
        </article>
      `;
    }).join("");
  }

  function renderAll() {
    renderFilters();
    renderGrid();
    document.querySelectorAll(".integrations-tab").forEach((tab) => {
      tab.classList.toggle("is-active", tab.dataset.tab === ui.tab);
    });
  }

  function openConnectModal(item) {
    const connected = isConnected(item.id);
    const title = $("modal-generic-title");
    const body = $("modal-generic-body");
    if (!title || !body) return;

    title.textContent = connected ? `Manage ${item.name}` : `Connect ${item.name}`;
    body.innerHTML = `
      <div class="integration-modal">
        <div class="integration-modal-brand">
          <div class="integration-logo integration-logo--lg">${item.logo}</div>
          <div>
            <strong>${item.name}</strong>
            <span class="integration-timing integration-timing--${item.timing === "during_call" ? "during-call" : "post-call"}">
              <span class="integration-timing-dot"></span>
              ${timingLabel(item.timing)}
            </span>
          </div>
        </div>
        <p class="muted">${item.description}</p>
        <label class="field">
          <span>API key / connection URL</span>
          <input class="input" id="integration-connect-value" placeholder="Paste API key or webhook URL" autocomplete="off">
        </label>
        <label class="field">
          <span>Notes (optional)</span>
          <input class="input" id="integration-connect-notes" placeholder="Workspace, account, or environment">
        </label>
        <div class="integration-modal-actions">
          ${connected ? `<button type="button" class="btn btn-ghost" id="integration-disconnect-btn">Disconnect</button>` : ""}
          <button type="button" class="btn btn-primary btn-speaksy btn-speaksy--soft" id="integration-save-btn">${connected ? "Save changes" : "Connect"}</button>
        </div>
      </div>
    `;

    $("integration-save-btn")?.addEventListener("click", () => {
      setConnected(item.id, true);
      window.showToast?.(`${item.name} connected`, "success");
      closeModal("modal-generic");
      renderAll();
    });

    $("integration-disconnect-btn")?.addEventListener("click", () => {
      setConnected(item.id, false);
      window.showToast?.(`${item.name} disconnected`, "success");
      closeModal("modal-generic");
      renderAll();
    });

    openModal("modal-generic");
  }

  function bindEvents() {
    $("integrations-filters")?.addEventListener("click", (e) => {
      const chip = e.target.closest(".integrations-filter-chip");
      if (!chip?.dataset.category) return;
      ui.category = chip.dataset.category;
      renderAll();
    });

    $("integrations-search")?.addEventListener("input", (e) => {
      ui.query = e.target.value.trim();
      renderGrid();
    });

    document.querySelector(".integrations-tabs")?.addEventListener("click", (e) => {
      const tab = e.target.closest(".integrations-tab");
      if (!tab?.dataset.tab) return;
      ui.tab = tab.dataset.tab;
      renderAll();
    });

    $("integrations-grid")?.addEventListener("click", (e) => {
      const btn = e.target.closest(".integration-connect-btn");
      if (!btn?.dataset.id) return;
      const item = INTEGRATIONS.find((i) => i.id === btn.dataset.id);
      if (item) openConnectModal(item);
    });
  }

  function init() {
    if (!$("integrations-grid")) return;
    bindEvents();
    renderAll();
  }

  window.Integrations = { init, renderAll };
})();