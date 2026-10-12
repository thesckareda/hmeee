/** VK Realty dashboard — branding, auth, and client-centric feature gates */
(function () {
  "use strict";

  const CLIENT_DEFAULT_FEATURES = [
    "overview",
    "leads",
    "hot-leads",
    "crm",
    "call-logs",
    "calendar",
    "reports",
    "settings",
    "integrations",
  ];

  /** Always on for the tinglish client dashboard (e.g. Phone WhatsApp pilot). */
  const CLIENT_ALWAYS_FEATURES = ["integrations"];

  const CLIENT_SETTINGS_TABS = new Set([
    "settings-hours",
    "settings-notifications",
    "settings-phone",
    "settings-knowledge",
  ]);

  const NAV_LABELS = {
    overview: "Today",
    leads: "All Leads",
    "hot-leads": "Priority Leads",
    crm: "CRM",
    "call-logs": "Call History",
    calendar: "Site Visits",
    reports: "Reports",
    settings: "Settings",
    integrations: "Connections",
  };

  document.title = "VK Realty — Dashboard";

  const pageTitle = document.getElementById("page-title");
  if (pageTitle) pageTitle.textContent = "Today";

  const pageSubtitle = document.getElementById("page-subtitle");
  if (pageSubtitle) pageSubtitle.textContent = "Every call Arjun handles — leads, recordings, and site visits";

  window.VK_CLIENT_MODE = true;

  const origFetch = window.fetch;
  window.fetch = function (input, init) {
    const token = localStorage.getItem("speaksy-auth-token");
    if (token) {
      init = init || {};
      init.headers = new Headers(init.headers || {});
      if (!init.headers.has("Authorization")) {
        init.headers.set("Authorization", `Bearer ${token}`);
      }
    }
    return origFetch.call(this, input, init);
  };

  function patchAuthRedirects() {
    window.redirectToLogin = function () {
      localStorage.removeItem("speaksy-auth-token");
      window.location.replace("/tinglish-vk-realty#login");
    };
  }

  function syncSettingsTab(tabId) {
    if (window.Dashboard?.setSettingsTab) {
      window.Dashboard.setSettingsTab(tabId);
      return;
    }
    document.querySelectorAll("#settings-tabs .tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === tabId);
    });
    document.querySelectorAll(".tab-panel-settings").forEach((p) => {
      p.classList.toggle("active", p.id === tabId);
    });
  }

  const SETTINGS_TAB_PAGES = {
    "settings-main-agent": "main-agent",
    "settings-agents": "assistants",
    "settings-hours": "settings",
    "settings-tone": "settings",
    "settings-notifications": "settings",
    "settings-phone": "settings",
    "settings-knowledge": "knowledge",
    "settings-api-keys": "settings",
    "settings-config": "settings",
  };

  function isOpsMode(features) {
    const feats = features || [];
    // integrations is client-visible in the pilot — don't treat it as ops-only mode
    return feats.includes("assistants") || feats.includes("main-agent");
  }

  function resolveFeatures(features) {
    const resolved = Array.isArray(features) && features.length
      ? [...features]
      : [...CLIENT_DEFAULT_FEATURES];
    CLIENT_ALWAYS_FEATURES.forEach((feat) => {
      if (!resolved.includes(feat)) resolved.push(feat);
    });
    return resolved;
  }

  function applyNavLabels(clientMode) {
    if (!clientMode) return;
    document.querySelectorAll(".nav-btn[data-page]").forEach((btn) => {
      const key = btn.dataset.page;
      const label = NAV_LABELS[key];
      if (!label) return;
      const el = btn.querySelector(".nav-label");
      if (el) el.textContent = label;
    });
  }

  function hideEmptyNavGroups() {
    document.querySelectorAll(".sidebar-group-label").forEach((label) => {
      let sib = label.nextElementSibling;
      let anyVisible = false;
      while (sib && !sib.classList.contains("sidebar-group-label")) {
        if (sib.matches(".nav-btn[data-page]") && !sib.hidden && !sib.classList.contains("vk-feature-hidden")) {
          anyVisible = true;
          break;
        }
        sib = sib.nextElementSibling;
      }
      label.hidden = !anyVisible;
      label.style.display = anyVisible ? "" : "none";
    });
  }

  function applyClientChrome(clientMode) {
    const deployBtn = document.getElementById("v2-deploy-btn");
    if (deployBtn) {
      deployBtn.hidden = clientMode;
      deployBtn.style.display = clientMode ? "none" : "";
    }
    document.querySelectorAll(".vk-ops-only").forEach((el) => {
      el.hidden = clientMode;
      el.style.display = clientMode ? "none" : "";
    });
    const refreshBtn = document.getElementById("refresh-btn");
    if (refreshBtn && clientMode) refreshBtn.textContent = "Update";
    const statusText = document.querySelector(".sidebar-status-text");
    if (statusText && clientMode) statusText.textContent = "Arjun is answering calls";
    applyNavLabels(clientMode);
  }

  function applyFeatureGate(features) {
    const resolved = resolveFeatures(features);
    const clientMode = !isOpsMode(resolved);
    window.VK_CLIENT_MODE = clientMode;

    const navSelector =
      ".nav-btn[data-page], .bottom-nav-item[data-page], .settings-card[data-page], #settings-tabs .tab[data-tab]";

    const allowed = new Set([...resolved, "agent-detail"]);
    const org = window.state?.auth?.current_organization;
    const isChild = Boolean(org?.parent_org_id);
    if (!isChild && resolved.includes("organisation")) {
      allowed.add("organisation");
    }
    if (!isChild || resolved.includes("campaigns")) {
      allowed.add("campaigns");
    }

    document.querySelectorAll(navSelector).forEach((el) => {
      const page = el.dataset.page;
      const tab = el.dataset.tab;
      let gateKey = page || (tab ? SETTINGS_TAB_PAGES[tab] || "settings" : null);

      if (clientMode && tab && !CLIENT_SETTINGS_TABS.has(tab) && tab.startsWith("settings-")) {
        gateKey = "__hide__";
      }

      const show =
        gateKey &&
        gateKey !== "__hide__" &&
        (!gateKey || allowed.has(gateKey) || gateKey === "whatsapp-chat");
      el.hidden = !show;
      el.classList.toggle("vk-feature-hidden", !show);
      if (show) {
        el.removeAttribute("aria-hidden");
        el.style.removeProperty("display");
      } else {
        el.setAttribute("aria-hidden", "true");
        el.style.display = "none";
      }
    });

    document.querySelectorAll(".page.vui-page[id^='page-']").forEach((section) => {
      const pageKey = section.id.replace(/^page-/, "");
      const show = allowed.has(pageKey) || pageKey === "agent-detail" || pageKey === "whatsapp-chat";
      section.classList.toggle("vk-feature-hidden", !show);
    });

    applyClientChrome(clientMode);
    hideEmptyNavGroups();

    const current = window.state?.currentPage;
    if (current && !allowed.has(current) && current !== "agent-detail" && current !== "whatsapp-chat") {
      window.setPage?.("overview");
    }
  }

  function humanizeError(message) {
    const raw = String(message || "").trim();
    if (!raw) return "Something went wrong. Please try again.";
    const lower = raw.toLowerCase();
    if (lower.includes("supabase") || lower.includes("service_role") || lower.includes("pgrst")) {
      return "We couldn't load your data. Tap Update, or contact OUTPERO support if this continues.";
    }
    if (lower.includes("row-level security") || lower.includes("rls")) {
      return "Your account doesn't have permission for this action. Contact OUTPERO support.";
    }
    if (lower.includes("authentication required") || lower.includes("not authenticated")) {
      return "Please sign in again to continue.";
    }
    if (lower.includes("twirperror") || lower.includes("outbound_trunk") || lower.includes("sip trunk")) {
      return "The call couldn't connect. OUTPERO will check the phone line setup.";
    }
    if (lower.includes("ssl") || lower.includes("certificate")) {
      return "Secure connection issue. Try again in a moment or contact OUTPERO.";
    }
    if (lower.includes("missing outbound_trunk")) {
      return "Outbound calling isn't set up yet. Contact OUTPERO.";
    }
    return raw;
  }

  function patchShowToast() {
    const orig = window.showToast;
    if (typeof orig !== "function" || orig._vkPatched) return;
    window.showToast = function (message, type) {
      return orig(humanizeError(message), type);
    };
    window.showToast._vkPatched = true;
  }

  function parseWorkspaceSlug() {
    const match = window.location.pathname.match(/\/tinglish-vk-realty\/o\/([^/]+)\/dashboard\/?$/);
    return match ? decodeURIComponent(match[1]) : null;
  }

  async function syncWorkspaceFromUrl() {
    const slug = parseWorkspaceSlug();
    if (!slug) return;

    if (typeof window.loadAuthProfile === "function") {
      await window.loadAuthProfile();
    }

    const auth = window.state?.auth;
    if (!auth) return;

    if (auth.current_organization?.slug === slug) {
      applyFeatureGate(auth.features);
      return;
    }

    const org =
      auth.organizations?.find((o) => o.slug === slug) ||
      (auth.platform_organizations || []).find((o) => o.slug === slug);

    if (!org?.id) {
      window.showToast?.(`Sign in with org code "${slug}" to open this team dashboard`, "info");
      return;
    }

    try {
      const data = await window.apiJson("/api/auth/switch-org", {
        method: "POST",
        body: JSON.stringify({ org_code: slug }),
      });
      window.setAuthToken?.(data.access_token);
      const profile = await window.loadAuthProfile?.();
      if (profile?.features) applyFeatureGate(profile.features);
    } catch (err) {
      console.error("syncWorkspaceFromUrl:", err);
      window.showToast?.(err.message || "Could not open this team dashboard", "error");
    }
  }

  function wireKnowledgeShortcut() {
    document.getElementById("vk-knowledge-go-settings")?.addEventListener("click", () => {
      if (typeof window.setPage === "function") {
        window.setPage("settings");
        syncSettingsTab("settings-knowledge");
      }
    });
  }

  function wireBillingCtas() {
    document.querySelectorAll("#page-billing .pricing-btn").forEach((btn) => {
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        window.showToast?.("Contact OUTPERO to upgrade your plan", "info");
      });
    });
  }

  function patchNavigation() {
    const orig = window.setPage;
    if (typeof orig !== "function") return;

    const CLIENT_PAGE_TITLES = {
      overview: "Today",
      leads: "All Leads",
      "hot-leads": "Priority Leads",
      "call-logs": "Call History",
      calendar: "Site Visits",
      reports: "Reports",
      settings: "Settings",
      integrations: "Connections",
    };

    const CLIENT_PAGE_SUBTITLES = {
      overview: "Calls, priority follow-ups, and site visits at a glance",
      leads: "Search leads, call back, and listen to recordings",
      "hot-leads": "Leads that need your attention soon",
      "call-logs": "Full history of calls Arjun handled",
      calendar: "Site visits and appointments booked on calls",
      reports: "Weekly trends and conversion summary",
      settings: "Business hours, alerts, and property information",
      integrations: "Link WhatsApp and other channels — scan a QR to start receiving AI replies",
    };

    window.setPage = function (page) {
      const feats = resolveFeatures(window.state?.auth?.features);
      if (!feats.includes(page) && page !== "agent-detail" && page !== "whatsapp-chat") {
        window.showToast?.("This section isn't available for your account", "info");
        return;
      }
      if (page === "knowledge") {
        if (!feats.includes("knowledge")) {
          orig.apply(this, ["settings", ...Array.prototype.slice.call(arguments, 1)]);
          syncSettingsTab("settings-knowledge");
          return;
        }
        orig.apply(this, ["settings", ...Array.prototype.slice.call(arguments, 1)]);
        syncSettingsTab("settings-knowledge");
        return;
      }
      orig.apply(this, arguments);
      if (window.VK_CLIENT_MODE) {
        const t = document.getElementById("page-title");
        const s = document.getElementById("page-subtitle");
        if (t && CLIENT_PAGE_TITLES[page]) t.textContent = CLIENT_PAGE_TITLES[page];
        if (s && CLIENT_PAGE_SUBTITLES[page]) s.textContent = CLIENT_PAGE_SUBTITLES[page];
      }
      if (page === "settings" && window.VK_CLIENT_MODE) {
        syncSettingsTab("settings-hours");
      } else if (page === "settings") {
        syncSettingsTab("settings-main-agent");
      }
    };
  }

  function showDataHelpBanner() {
    const main = document.getElementById("content");
    if (!main || document.getElementById("vk-data-help")) return;
    const logs = window.state?.logs;
    const hasData = Array.isArray(logs) && logs.length > 0;
    const token = localStorage.getItem("speaksy-auth-token");
    if (hasData || token) return;

    const el = document.createElement("div");
    el.id = "vk-data-help";
    el.className = "vk-data-help card";
    el.innerHTML = `
      <strong>Sign in to see your calls and leads</strong>
      <p class="muted">Go to <a href="/tinglish-vk-realty#login">Sign in</a> — use the username and password from your OUTPERO onboarding email.</p>
    `;
    main.insertBefore(el, main.firstChild);
  }

  window.vkApplyFeatureGate = applyFeatureGate;

  function ensureLucideIcons() {
    if (typeof window.lucide?.createIcons === "function") {
      window.lucide.createIcons();
    }
  }

  function initVkPortal() {
    patchAuthRedirects();
    patchShowToast();
    wireKnowledgeShortcut();
    wireBillingCtas();
    ensureLucideIcons();
    applyFeatureGate(window.state?.auth?.features);
    showDataHelpBanner();
  }

  patchAuthRedirects();
  patchShowToast();
  document.addEventListener("DOMContentLoaded", initVkPortal);

  function hookOverviewRoles() {
    const dash = window.Dashboard;
    if (!dash || dash._vkRolesHooked) return;
    const orig = dash.onPageChange?.bind(dash);
    dash.onPageChange = async function (page) {
      if (orig) await orig(page);
      if (page === "overview") {
        await window.Organisation?.loadOverviewRoles?.();
        refresh();
      }
    };
    dash._vkRolesHooked = true;
  }

  function hookAuthProfile() {
    const orig = window.loadAuthProfile;
    if (typeof orig !== "function" || orig._vkHooked) return;
    window.loadAuthProfile = async function () {
      const profile = await orig.apply(this, arguments);
      applyFeatureGate(profile?.features);
      document.getElementById("vk-data-help")?.remove();
      if (window.state?.currentPage === "overview" || !window.state?.currentPage) {
        await window.Organisation?.loadOverviewRoles?.();
      }
      return profile;
    };
    window.loadAuthProfile._vkHooked = true;
  }

  function hookBootstrap() {
    const orig = window.bootstrapData;
    if (typeof orig !== "function" || orig._vkHooked) return;
    window.bootstrapData = async function () {
      try {
        await orig.apply(this, arguments);
      } finally {
        document.getElementById("vk-data-help")?.remove();
        if (window.Dashboard?.renderOverviewNeedsAction) {
          window.Dashboard.renderOverviewNeedsAction();
        }
      }
    };
    window.bootstrapData._vkHooked = true;
  }

  window.addEventListener("load", async () => {
    patchNavigation();
    hookOverviewRoles();
    hookAuthProfile();
    hookBootstrap();
    try {
      await syncWorkspaceFromUrl();
      await window.Organisation?.bootstrap?.();
      await window.Organisation?.loadOverviewRoles?.();
      initVkPortal();
    } catch (e) {
      console.error("VK dashboard init:", e);
      initVkPortal();
    }
  });
})();

/* ── Daily briefing — plain-language "what happened & what to do next" ────── */
(function () {
  "use strict";

  const AGENT_NAME = document.title.includes("VK Realty") ? "Arjun" : "Arjun";

  function todayIST() {
    return new Date().toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
  }

  function istHour() {
    return Number(new Date().toLocaleString("en-US", { timeZone: "Asia/Kolkata", hour: "numeric", hour12: false }));
  }

  function isToday(iso) {
    if (!iso) return false;
    try {
      return new Date(iso).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" }) === todayIST();
    } catch { return false; }
  }

  function fmtWhen(d) {
    const now = new Date();
    const opts = { timeZone: "Asia/Kolkata", hour: "numeric", minute: "2-digit", hour12: true };
    const time = d.toLocaleTimeString("en-IN", opts);
    const dayKey = d.toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
    if (dayKey === todayIST()) return `today at ${time}`;
    const tomorrow = new Date(now.getTime() + 86400000).toLocaleDateString("en-CA", { timeZone: "Asia/Kolkata" });
    if (dayKey === tomorrow) return `tomorrow at ${time}`;
    return d.toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata", weekday: "long" }) + ` at ${time}`;
  }

  function renderVkBriefing() {
    const root = document.getElementById("vk-briefing");
    if (!root) return;
    const st = window.state || {};
    const logs = Array.isArray(st.logs) ? st.logs : [];
    const bookings = Array.isArray(st.bookings) ? st.bookings : [];

    const h = istHour();
    const greeting = h < 12 ? "Good morning" : h < 17 ? "Good afternoon" : "Good evening";
    document.getElementById("vk-briefing-greeting").textContent = greeting;

    const todays = logs.filter((r) => isToday(r.created_at));
    const booked = todays.filter((r) => r.was_booked).length;
    const followUps = todays.filter((r) => !r.was_booked && Number(r.duration_seconds) >= 20).length;

    const upcoming = bookings
      .map((b) => {
        const raw = String(b.appointment_time || "").trim();
        const d = raw ? new Date(raw.includes("T") ? raw : raw.replace(" ", "T")) : null;
        return d && !isNaN(d) ? { ...b, _d: d } : null;
      })
      .filter((b) => b && b._d.getTime() >= Date.now())
      .sort((a, b) => a._d - b._d);

    const line = document.getElementById("vk-briefing-line");
    const sub = document.getElementById("vk-briefing-sub");

    if (!todays.length) {
      line.innerHTML = `${AGENT_NAME} is on the line, ready to answer.`;
      sub.textContent = logs.length
        ? "No calls yet today. Everything from earlier is saved under Call History."
        : "When your first call comes in, you’ll see it here.";
    } else {
      const parts = [`${AGENT_NAME} answered <strong>${todays.length} call${todays.length === 1 ? "" : "s"}</strong> today`];
      if (booked) parts.push(`<strong>${booked}</strong> booked a visit`);
      line.innerHTML = parts.join(" — ") + ".";
      const subBits = [];
      if (followUps) subBits.push(`${followUps} ${followUps === 1 ? "person is" : "people are"} worth a call back.`);
      if (upcoming.length) {
        const nx = upcoming[0];
        const who = (nx.caller_name || nx.phone_number || "A visitor").toString().split(" ")[0];
        subBits.push(`Next site visit: ${who}, ${fmtWhen(nx._d)}.`);
      }
      sub.textContent = subBits.join("  ") || "Nothing needs your attention right now.";
    }

    const actions = document.getElementById("vk-briefing-actions");
    const latest = logs.find((r) => String(r.recording_url || "").trim());
    const btns = [];
    if (latest) {
      btns.push(`<button type="button" class="btn btn-primary btn-speaksy btn-speaksy--soft vk-brief-btn" id="vk-brief-listen">
        <span class="vk-brief-btn-icon" aria-hidden="true">&#9654;</span> Listen to the last call</button>`);
    }
    if (followUps) {
      btns.push(`<button type="button" class="btn btn-secondary vk-brief-btn" onclick="navigateTo('hot-leads')">See who to call back</button>`);
    }
    if (upcoming.length) {
      btns.push(`<button type="button" class="btn btn-secondary vk-brief-btn" onclick="navigateTo('calendar')">Today’s site visits</button>`);
    }
    if (!btns.length) {
      btns.push(`<button type="button" class="btn btn-secondary vk-brief-btn" onclick="navigateTo('call-logs')">Open call history</button>`);
    }
    actions.innerHTML = btns.join("");

    const listen = document.getElementById("vk-brief-listen");
    if (listen && latest) {
      listen.addEventListener("click", () => {
        const wrap = document.getElementById("vk-briefing-player");
        const who = latest.caller_name || latest.phone || latest.phone_number || "Unknown caller";
        wrap.innerHTML = `
          <div class="vk-brief-player-card">
            <div class="vk-brief-player-meta">
              <strong>${String(who).replace(/</g, "&lt;")}</strong>
              <span class="muted">${latest.created_at ? new Date(latest.created_at).toLocaleString("en-IN", { timeZone: "Asia/Kolkata", day: "numeric", month: "short", hour: "numeric", minute: "2-digit", hour12: true }) : ""}</span>
            </div>
            <audio controls autoplay src="${String(latest.recording_url).replace(/"/g, "&quot;")}"></audio>
          </div>`;
        wrap.hidden = false;
        listen.disabled = true;
      }, { once: true });
    }
  }

  /* Plain-language metric labels for non-technical owners */
  function humanizeMetricLabels() {
    if (!window.VK_CLIENT_MODE) return;
    const strip = document.querySelector(".metric-strip");
    if (!strip) return;
    const map = { "Calls": "Calls today", "Conversion": "Went well", "Pipeline": "Potential value", "Hot leads": "Call these back" };
    strip.querySelectorAll(".section-label").forEach((el) => {
      const to = map[el.textContent.trim()];
      if (to) el.textContent = to;
    });
  }

  function refresh() {
    try { renderVkBriefing(); humanizeMetricLabels(); } catch (e) { console.error("briefing:", e); }
  }

  window.renderVkBriefing = refresh;

  window.addEventListener("load", () => {
    refresh();
    const origBootstrap = window.bootstrapData;
    if (typeof origBootstrap === "function" && !origBootstrap._vkBriefHooked) {
      window.bootstrapData = async function () {
        try { return await origBootstrap.apply(this, arguments); }
        finally { refresh(); }
      };
      window.bootstrapData._vkBriefHooked = true;
    }
    const origSetPage = window.setPage;
    if (typeof origSetPage === "function" && !origSetPage._vkBriefHooked) {
      window.setPage = function (page) {
        const out = origSetPage.apply(this, arguments);
        if (page === "overview") refresh();
        return out;
      };
      window.setPage._vkBriefHooked = true;
    }
  });
})();
