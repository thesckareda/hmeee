window.addEventListener("error", (e) => {
  console.error("Uncaught error:", e.error);
});
window.addEventListener("unhandledrejection", (e) => {
  console.error("Unhandled rejection:", e.reason);
});

const state = {
  logs: [],
  contacts: [],
  analytics: null,
  bookings: [],
  agents: [],
  siteVisitProperties: [],
  siteVisits: [],
  crmView: "all",
  crmSiteVisitTab: "booked",
  calendarSelectedDate: "",
  currentPage: "overview",
  currentAgentId: null,
  currentAgentTab: "assistant-details",
  selectedUseCases: new Set(),
  creatingAssistant: false,
  crmSelectedPhone: "",
  auth: null,
};

const FALLBACK_SITE_VISIT_PROPERTIES = [
  { key: "godrej", label: "Godrej" },
  { key: "myhome", label: "My Home Udyan" },
  { key: "trilight", label: "The Trilight" },
  { key: "signature", label: "Signature" },
  { key: "trident", label: "Trident" },
  { key: "housing", label: "Housing" },
  { key: "indroyal", label: "Ind Royal" },
  { key: "tricolt", label: "Tricolt" },
];

const PAGE_TITLES = {
  assistants: "Agents",
  overview: "Today's Summary",
  leads: "My Leads & Calls",
  "hot-leads": "Hot Leads",
  performance: "Performance Overview",
  "listing-performance": "Campaign & Listings",
  reports: "Reports",
  "call-logs": "Call Logs",
  crm: "CRM",
  analytics: "Analytics",
  calendar: "Calendar",
  knowledge: "Knowledge Base",
  integrations: "Integrations",
  campaigns: "Outbound Campaigns",
  settings: "Settings",
  billing: "Billing",
  "agent-detail": "Agent Configuration",
  "main-agent": "VK Realty Main Agent",
  "lang-presets": "Language Presets",
  clients: "Clients & Teams",
  organisation: "Organisation",
};

const PAGE_SUBTITLES = {
  assistants: "Design, run, and manage voice agents",
  overview: "Calls, conversions, and pipeline impact today",
  leads: "Search leads, play recordings, read transcripts",
  "hot-leads": "High-intent leads needing immediate action",
  performance: "Weekly conversion, site visits, and best call times",
  "listing-performance": "Listing interest, areas, and A/B tone results",
  reports: "Weekly and monthly summaries, one-click exports",
  "call-logs": "Search, filter, export, and replay conversations",
  crm: "Lead pipeline and caller insights from voice calls",
  analytics: "Call volume, outcomes, and performance charts",
  calendar: "Site visits and appointments booked by your voice agents",
  knowledge: "Global knowledge management",
  integrations: "Connect your Speaksy account with other services to enhance your workflow.",
  campaigns: "Bulk outbound calling campaigns",
  settings: "Agents, tone, hours, notifications, and config",
  billing: "Usage and subscription management",
  "agent-detail": "Configure assistant behavior and call settings",
  "main-agent": "Configure voice, language, and greeting — changes apply instantly",
  "lang-presets": "Edit Telugish, Hinglish, and English packs — activate live on calls",
  clients: "Manage client workspaces and team access",
  organisation: "Role dashboards, team access, and feature permissions",
};

const AUTH_TOKEN_KEY = "speaksy-auth-token";

/* ── Auth & API ── */

function getAuthToken() {
  return localStorage.getItem(AUTH_TOKEN_KEY) || "";
}

function setAuthToken(token) {
  if (token) localStorage.setItem(AUTH_TOKEN_KEY, token);
  else localStorage.removeItem(AUTH_TOKEN_KEY);
}
window.setAuthToken = setAuthToken;

async function loadAuthProfile() {
  try {
    const profile = await apiJson("/api/auth/me");
    state.auth = profile;
    renderOrgSwitcher();
    updateClientsNavVisibility();
    if (typeof window.vkApplyFeatureGate === "function" && Array.isArray(profile.features) && profile.features.length) {
      window.vkApplyFeatureGate(profile.features);
    }
    return profile;
  } catch {
    return null;
  }
}
window.loadAuthProfile = loadAuthProfile;

function updateClientsNavVisibility() {
  const auth = state.auth;
  const showClients = auth && (auth.is_superadmin || auth.can_manage_org);
  document.querySelectorAll('[data-page="clients"]').forEach((el) => {
    el.hidden = !showClients;
    el.style.display = showClients ? "" : "none";
  });
  const isChild = Boolean(auth?.current_organization?.parent_org_id);
  const showOrg =
    auth &&
    !isChild &&
    (auth.is_superadmin || auth.can_manage_org || (auth.features || []).includes("organisation"));
  document.querySelectorAll('[data-page="organisation"]').forEach((el) => {
    el.hidden = !showOrg;
    el.style.display = showOrg ? "" : "none";
  });
}

async function switchOrganization(orgId) {
  const data = await apiJson("/api/auth/switch-org", {
    method: "POST",
    body: JSON.stringify({ org_id: orgId }),
  });
  setAuthToken(data.access_token);
  await loadAuthProfile();
  showToast(`Switched to ${data.organization?.name || "workspace"}`, "success");
  await bootstrapData();
  if (state.currentPage === "clients") window.Clients?.init?.();
}

function renderOrgSwitcher() {
  const select = document.getElementById("org-switcher");
  if (!select) return;
  const auth = state.auth;
  if (!auth?.organizations?.length) {
    select.hidden = true;
    return;
  }
  select.hidden = false;
  select.innerHTML = auth.organizations
    .map(
      (org) =>
        `<option value="${org.id}" ${org.id === auth.org_id ? "selected" : ""}>${org.name}</option>`
    )
    .join("");
  select.onchange = async () => {
    const next = select.value;
    if (next && next !== auth.org_id) {
      try {
        await switchOrganization(next);
      } catch (err) {
        showToast(err.message, "error");
        select.value = auth.org_id;
      }
    }
  };
}
window.renderOrgSwitcher = renderOrgSwitcher;

async function apiFetch(url, options = {}) {
  const headers = { ...(options.headers || {}) };
  const token = getAuthToken();
  if (token) headers.Authorization = `Bearer ${token}`;
  if (options.body && !headers["Content-Type"]) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(url, { ...options, headers, credentials: "same-origin" });
  if (res.status === 401) {
    redirectToLogin();
    throw new Error("Authentication required");
  }
  return res;
}

function redirectToLogin() {
  setAuthToken("");
  if (!window.location.pathname.startsWith("/login")) {
    window.location.replace("/login");
  }
}

async function logout() {
  try {
    await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  } catch {}
  redirectToLogin();
}

function parseApiError(errBody, fallback = "Request failed") {
  if (!errBody) return fallback;
  const detail = errBody.detail ?? errBody;
  if (typeof detail === "string") return detail;
  if (detail?.error) return String(detail.error);
  if (detail?.message) return String(detail.message);
  if (Array.isArray(detail)) return detail.map((d) => d.msg || JSON.stringify(d)).join("; ");
  return fallback;
}

async function apiJson(url, options = {}) {
  const res = await apiFetch(url, options);
  if (!res.ok) {
    let detail = `Request failed (${res.status})`;
    try {
      const err = await res.json();
      detail = parseApiError(err, detail);
    } catch {}
    const error = new Error(detail);
    if (detail.includes("row-level security") || detail.includes("rls_denied")) {
      error.code = "rls_denied";
    }
    throw error;
  }
  return res.json();
}

/* ── UI helpers ── */

function showToast(message, type = "info") {
  const root = document.getElementById("toast-root");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => el.remove(), 4200);
}

function openModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.hidden = false;
}

function closeModal(id) {
  const modal = document.getElementById(id);
  if (modal) modal.hidden = true;
}

function closeAllModals() {
  window.CallLogs?.stopAudio?.();
  document.querySelectorAll(".modal").forEach((m) => { m.hidden = true; });
}

/* ── Date / display utils (preserved from original) ── */

function safeDate(value) {
  if (!value) return "—";
  const d = new Date(value);
  return Number.isNaN(d.getTime()) ? "—" : d.toLocaleString("en-IN");
}

function formatDateKeyIST(dateValue) {
  const parts = new Intl.DateTimeFormat("en-CA", {
    timeZone: "Asia/Kolkata",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
  }).formatToParts(dateValue);
  const year = parts.find((p) => p.type === "year")?.value;
  const month = parts.find((p) => p.type === "month")?.value;
  const day = parts.find((p) => p.type === "day")?.value;
  if (!year || !month || !day) return "";
  return `${year}-${month}-${day}`;
}

function parseAppointmentDate(value) {
  if (!value) return null;
  const raw = String(value).trim();
  if (!raw) return null;

  const matched = raw.match(
    /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(Z|[+\-]\d{2}:?\d{2})?$/i,
  );
  if (matched) {
    const [, y, mo, da, hh, mm, ss = "00", zone = ""] = matched;
    if (zone) {
      const zoneNorm = zone === "Z" ? "Z" : (zone.includes(":") ? zone : `${zone.slice(0, 3)}:${zone.slice(3)}`);
      const zonedDate = new Date(`${y}-${mo}-${da}T${hh}:${mm}:${ss}${zoneNorm}`);
      if (!Number.isNaN(zonedDate.getTime())) return zonedDate;
    } else {
      const utcMs = Date.UTC(Number(y), Number(mo) - 1, Number(da), Number(hh), Number(mm), Number(ss)) - (330 * 60 * 1000);
      return new Date(utcMs);
    }
  }

  const normalized = raw.replace(" ", "T");
  const d = new Date(normalized);
  return Number.isNaN(d.getTime()) ? null : d;
}

function appointmentDateKeyIST(value) {
  const d = parseAppointmentDate(value);
  if (!d) return "";
  return formatDateKeyIST(d);
}

function safeAppointmentDate(value) {
  if (!value) return "Appointment time unavailable";
  const d = parseAppointmentDate(value);
  if (!d) return "Appointment time unavailable";
  const formatted = new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    day: "2-digit",
    month: "short",
    year: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  }).format(d);
  return `${formatted} IST`;
}

function safeAppointmentTime(value) {
  if (!value) return "Time unavailable";
  const d = parseAppointmentDate(value);
  if (!d) return "Time unavailable";
  return `${new Intl.DateTimeFormat("en-IN", {
    timeZone: "Asia/Kolkata",
    hour: "2-digit",
    minute: "2-digit",
    hour12: true,
  }).format(d)} IST`;
}

function humanizeSummary(text) {
  const raw = String(text || "").trim();
  if (!raw) return "Call completed.";

  const tryJsonExtract = (source) => {
    const first = source.indexOf("{");
    const last = source.lastIndexOf("}");
    if (first >= 0 && last > first) {
      try { return JSON.parse(source.slice(first, last + 1)); } catch { return null; }
    }
    return null;
  };

  const asJson = tryJsonExtract(raw);
  if (asJson && typeof asJson === "object") {
    const msg = asJson.message || asJson.error || asJson.detail || "";
    if (msg) return String(msg).replace(/_/g, " ");
  }
  return raw.replace(/\{"statusCode":[^}]+\}/g, "").replace(/\s{2,}/g, " ").trim();
}

function tag(text, klass = "") {
  return `<span class="tag ${klass}">${text}</span>`;
}

function displayPhone(phone) {
  const value = String(phone || "").trim().replace(/\s+/g, "");
  const lowered = value.toLowerCase();
  if (["unknown", "unknown caller", "caller", "demo"].includes(lowered)) return "—";
  return value || "—";
}

function cleanCallerName(name) {
  const value = String(name || "").trim();
  if (!value) return "";
  const lowered = value.toLowerCase();
  if (["unknown", "unknown caller", "caller"].includes(lowered)) return "";
  return value;
}

function displayCallerName(name, phone) {
  const cleaned = cleanCallerName(name);
  const resolvedPhone = displayPhone(phone);
  if (cleaned) return cleaned;
  if (resolvedPhone !== "—") return resolvedPhone;
  return "Unknown Caller";
}

function callStatusBadge(row) {
  const summary = String(row.summary || "").toLowerCase();
  if (row.was_booked || summary.includes("confirm")) return tag("Booked", "ok");
  if (summary.includes("cancel")) return tag("Cancelled", "warn");
  if ((row.duration_seconds || 0) > 0) return tag("Completed", "ok");
  return tag("Unknown", "warn");
}

/* ── Navigation ── */

const SPEAKSY_TAB_LOADER_MIN_MS = 280;
let speaksyTabNavToken = 0;

function prefersReducedMotion() {
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

function asPageLoadPromise(fn) {
  if (!fn) return Promise.resolve();
  try {
    return Promise.resolve(typeof fn === "function" ? fn() : fn);
  } catch (err) {
    return Promise.reject(err);
  }
}

function resolveSpeaksyLoaderTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function showSpeaksyTabLoader() {
  const el = document.getElementById("speaksy-tab-loader");
  if (!el) return;
  const theme = resolveSpeaksyLoaderTheme();
  const pill = el.querySelector(".speaksy-loader-pill");
  el.dataset.theme = theme;
  if (pill) {
    pill.classList.toggle("dark", theme === "dark");
    pill.classList.toggle("light", theme === "light");
  }
  el.classList.add("is-active");
  el.setAttribute("aria-hidden", "false");
}

function hideSpeaksyTabLoader() {
  const el = document.getElementById("speaksy-tab-loader");
  if (!el) return;
  el.classList.remove("is-active");
  el.setAttribute("aria-hidden", "true");
}

async function runWithSpeaksyTabLoader(callback, navToken) {
  const el = document.getElementById("speaksy-tab-loader");

  if (!el) {
    await callback();
    return;
  }

  const main = document.getElementById("content");
  const minMs = prefersReducedMotion() ? 0 : SPEAKSY_TAB_LOADER_MIN_MS;

  showSpeaksyTabLoader();
  main?.classList.add("page-swapping");

  try {
    await Promise.all([
      Promise.resolve().then(() => callback()),
      minMs ? new Promise((resolve) => setTimeout(resolve, minMs)) : Promise.resolve(),
    ]);
  } catch (err) {
    console.error("[Speaksy] Tab navigation failed:", err);
  } finally {
    if (navToken === speaksyTabNavToken) {
      main?.classList.remove("page-swapping");
      hideSpeaksyTabLoader();
    }
  }
}

function syncNavActive(page) {
  const navPage = page === "agent-detail" ? "assistants" : page;
  document.querySelectorAll(".nav-item, .bottom-nav-item").forEach((el) => {
    if (!el.dataset.page) return;
    const isActive = el.dataset.page === navPage;
    el.classList.toggle("active", isActive);
    el.classList.toggle("btn-speaksy", isActive);
    el.classList.toggle("btn-speaksy--soft", isActive);
  });
}

async function setPage(page, options = {}) {
  const { skipLoader = false } = options;
  if (!skipLoader && page !== state.currentPage) {
    const navToken = ++speaksyTabNavToken;
    await runWithSpeaksyTabLoader(() => applyPage(page, navToken), navToken);
    return;
  }
  await applyPage(page);
}

async function applyPage(page, navToken) {
  if (navToken != null && navToken !== speaksyTabNavToken) return;

  window.CallLogs?.stopAudio?.();
  state.currentPage = page;
  syncNavActive(page);
  document.querySelectorAll(".page").forEach((el) => {
    const isAgent = page === "agent-detail";
    const match = isAgent
      ? el.id === "page-agent-detail"
      : el.id === `page-${page}`;
    el.classList.toggle("active", match);
  });
  document.body.setAttribute("data-current-page", page);
  document.getElementById("page-title").textContent = PAGE_TITLES[page] || page;
  const subtitle = document.getElementById("page-subtitle");
  if (subtitle) subtitle.textContent = PAGE_SUBTITLES[page] || "";

  const loads = [];

  if (page === "assistants") loads.push(asPageLoadPromise(() => loadAssistants()));
  if (page === "main-agent") {
    loads.push(asPageLoadPromise(() => window.AssistantSettings?.load?.()));
    loads.push(asPageLoadPromise(() => window.AgentControl?.refreshStatus?.()));
    window.AgentControl?.startPolling?.();
  } else {
    window.AgentControl?.stopPolling?.();
  }
  if (page === "lang-presets") loads.push(asPageLoadPromise(() => window.LangPresets?.load?.()));
  if (page === "settings") {
    loads.push(asPageLoadPromise(() => loadPlatformConfig()));
    populateAgentSelect("knowledge-agent-select");
  }
  if (page === "campaigns") loads.push(asPageLoadPromise(() => loadCampaignsPage()));
  if (page === "clients") loads.push(asPageLoadPromise(() => window.Clients?.init?.()));
  if (page === "organisation") loads.push(asPageLoadPromise(() => window.Organisation?.init?.()));
  if (page === "integrations" && window.Integrations) {
    loads.push(asPageLoadPromise(() => {
      window.Integrations.renderAll();
      window.WhatsAppConnect?.init?.();
      window.WhatsAppNumbers?.renderConnectedNumbers?.();
    }));
  }
  if (page === "call-logs" && window.CallLogs) loads.push(asPageLoadPromise(() => window.CallLogs.init()));
  if (page === "analytics") loads.push(asPageLoadPromise(() => renderAnalytics()));
  if (page === "crm") {
    loads.push(asPageLoadPromise(() => loadSiteVisitProperties()));
    loads.push(asPageLoadPromise(() => loadSiteVisits()));
  }
  if (window.Dashboard?.onPageChange) {
    loads.push(asPageLoadPromise(() => window.Dashboard.onPageChange(page)));
  }

  await Promise.allSettled(loads);

  if (navToken != null && navToken !== speaksyTabNavToken) return;

  if (window.innerWidth <= 900) closeMobileSidebar();
}

async function openAgentDetail(agentId, name = "Agent", purpose = "") {
  state.currentAgentId = agentId;
  document.getElementById("agent-detail-name").textContent = name;
  document.getElementById("agent-detail-purpose").textContent = (purpose || "Voice AI Assistant").replace(/_/g, " ");
  setAgentTab(state.currentAgentTab || "assistant-details", { skipLoader: true });
  const navToken = ++speaksyTabNavToken;
  await runWithSpeaksyTabLoader(async () => {
    await applyPage("agent-detail", navToken);
    if (navToken !== speaksyTabNavToken) return;
    if (window.AgentDetail) await window.AgentDetail.load(agentId);
  }, navToken);
}

async function setAgentTab(tabId, options = {}) {
  const { skipLoader = false } = options;
  if (tabId === state.currentAgentTab) return;

  const apply = async () => {
    state.currentAgentTab = tabId;
    document.querySelectorAll("#agent-tabs .tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === tabId);
    });
    document.querySelectorAll("#agent-tab-panels .tab-panel").forEach((p) => {
      p.classList.toggle("active", p.id === `tab-${tabId}`);
    });
    if (window.AgentDetail) {
      await asPageLoadPromise(() => window.AgentDetail.onTabChange(tabId));
    }
  };

  if (!skipLoader && state.currentPage === "agent-detail") {
    const navToken = ++speaksyTabNavToken;
    await runWithSpeaksyTabLoader(() => apply(), navToken);
    return;
  }
  await apply();
}

/* ── Renderers (preserved + extended) ── */

function renderOverview() {
  const fiveMinAgo = Date.now() - 5 * 60 * 1000;
  const inProgress = state.logs.filter((r) => {
    const created = new Date(r.created_at || 0).getTime();
    const duration = Number(r.duration_seconds) || 0;
    return duration === 0 && created >= fiveMinAgo && !isNaN(created);
  }).length;
  const pill = document.getElementById("active-calls-pill");
  if (pill) {
    pill.textContent = inProgress ? `${inProgress} in progress` : "All clear";
    pill.classList.toggle("pill-live", inProgress > 0);
  }

  const appBody = document.getElementById("overview-appointments");
  if (!appBody) return;
  const now = new Date();
  const topBookings = state.bookings
    .map((b) => ({ ...b, parsedAppointment: parseAppointmentDate(b.appointment_time) }))
    .filter((b) => b.parsedAppointment && b.parsedAppointment.getTime() >= now.getTime())
    .sort((a, b) => a.parsedAppointment.getTime() - b.parsedAppointment.getTime())
    .slice(0, 6);

  appBody.innerHTML = topBookings.length
    ? topBookings.map((b) => `
      <tr>
        <td>${displayCallerName(b.caller_name, b.phone_number)}</td>
        <td>${safeAppointmentDate(b.appointment_time)}</td>
        <td>${tag("Confirmed", "ok")}</td>
      </tr>
    `).join("")
    : `<tr><td colspan="3">No appointments yet.</td></tr>`;

  if (window.Dashboard?.refreshOverview && state.currentPage === "overview") {
    window.Dashboard.refreshOverview();
  }
}

function renderCallLogs() {
  if (state.currentPage === "call-logs" && window.CallLogs) return;
}

function crmStatusTag(contact) {
  const status = contact.crm_pipeline_status
    || (contact.is_completed ? "completed" : (contact.is_booked ? "booked" : "pending"));
  if (status === "completed") return tag("Completed", "ok");
  if (status === "booked") return tag("Booked", "ok");
  return tag("Pending", "warn");
}

function filterContactsByCrmView(contacts) {
  const view = state.crmView || "all";
  if (view === "booked") {
    return contacts.filter((c) => c.crm_pipeline_status === "booked" || (c.is_booked && !c.is_completed));
  }
  if (view === "completed") {
    return contacts.filter((c) => c.crm_pipeline_status === "completed" || c.is_completed);
  }
  return contacts;
}

function updateCrmToolbarStats(rows) {
  const el = document.getElementById("crm-toolbar-stats");
  if (!el) return;
  const all = state.contacts.length;
  const booked = state.contacts.filter((c) => c.crm_pipeline_status === "booked" || (c.is_booked && !c.is_completed)).length;
  const completed = state.contacts.filter((c) => c.crm_pipeline_status === "completed" || c.is_completed).length;
  const showing = rows.length;
  el.innerHTML = `
    <span class="crm-stat-pill">${showing} shown</span>
    <span class="crm-stat-pill">${booked} booked</span>
    <span class="crm-stat-pill">${completed} completed</span>
    <span class="crm-stat-pill muted-pill">${all} total leads</span>
  `;
}

function renderCRM(filtered) {
  const base = filtered ?? state.contacts;
  const rows = filterContactsByCrmView(base);
  const tbody = document.getElementById("crm-body");
  updateCrmToolbarStats(rows);
  if (!rows.length) {
    const emptyMsg = state.crmView === "booked"
      ? "No booked leads yet."
      : state.crmView === "completed"
        ? "No completed visits yet — mark visits complete from the Site visits table."
        : "No CRM contacts found.";
    tbody.innerHTML = `<tr><td colspan="5">${emptyMsg}</td></tr>`;
    const emptyDetails = document.getElementById("crm-details");
    if (emptyDetails) {
      emptyDetails.className = "crm-insights-body crm-details-empty";
      emptyDetails.innerHTML = `
        <div class="crm-insights-placeholder">
          <div class="crm-insights-placeholder-icon" aria-hidden="true">◎</div>
          <p>No CRM data available yet.</p>
        </div>
      `;
    }
    return;
  }
  tbody.innerHTML = rows.map((c) => `
    <tr class="crm-row" data-phone="${c.phone_number || ""}">
      <td>${displayCallerName(c.caller_name, c.phone_number)}</td>
      <td>${displayPhone(c.phone_number)}</td>
      <td>${c.total_calls || 0}</td>
      <td>${safeDate(c.last_seen)}</td>
      <td>${crmStatusTag(c)}</td>
    </tr>
  `).join("");
}

async function loadSiteVisits(force = false) {
  if (!force && state.siteVisits.length) {
    renderCrmSiteVisits();
    return state.siteVisits;
  }
  try {
    const visits = await fetchJson("/api/site-visits");
    state.siteVisits = Array.isArray(visits) ? visits : [];
  } catch {
    state.siteVisits = (state.bookings || []).filter((b) => b.booking_type === "site_visit");
  }
  renderCrmSiteVisits();
  return state.siteVisits;
}

function siteVisitStatusTag(status) {
  const key = String(status || "confirmed").toLowerCase();
  if (key === "completed") return tag("Completed", "ok");
  if (key === "cancelled" || key === "no_show") return tag(key === "no_show" ? "No show" : "Cancelled", "warn");
  return tag("Booked", "ok");
}

function renderCrmSiteVisits() {
  const tbody = document.getElementById("crm-site-visits-body");
  if (!tbody) return;

  const tab = state.crmSiteVisitTab || "booked";
  const visits = (state.siteVisits || []).filter((v) => {
    const status = String(v.status || v.visit_status || "confirmed").toLowerCase();
    if (tab === "completed") return status === "completed";
    return status === "confirmed" || status === "booked";
  });

  document.querySelectorAll("#crm-site-visit-tabs .crm-site-visit-tab").forEach((btn) => {
    const active = btn.dataset.svTab === tab;
    btn.classList.toggle("is-active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });

  if (!visits.length) {
    tbody.innerHTML = `<tr><td colspan="6">${tab === "completed" ? "No completed site visits yet." : "No upcoming booked site visits."}</td></tr>`;
    return;
  }

  tbody.innerHTML = visits.map((v) => {
    const status = String(v.status || v.visit_status || "confirmed").toLowerCase();
    const canComplete = status === "confirmed" || status === "booked";
    return `
      <tr class="crm-sv-row" data-visit-id="${escHtml(v.id || "")}" data-phone="${escHtml(v.phone_number || "")}">
        <td>${escHtml(displayCallerName(v.caller_name, v.phone_number))}</td>
        <td>${escHtml(displayPhone(v.phone_number))}</td>
        <td>${escHtml(v.property_name || v.listing_id || "—")}</td>
        <td>${escHtml(safeAppointmentDate(v.appointment_time))}</td>
        <td>${siteVisitStatusTag(status)}</td>
        <td class="crm-sv-actions">
          ${canComplete ? `<button type="button" class="btn btn-ghost btn-sm crm-sv-complete-btn" data-visit-id="${escHtml(v.id || "")}">Mark completed</button>` : ""}
          <button type="button" class="btn btn-ghost btn-sm crm-sv-view-btn" data-phone="${escHtml(v.phone_number || "")}">View</button>
        </td>
      </tr>
    `;
  }).join("");

  tbody.querySelectorAll(".crm-sv-complete-btn").forEach((btn) => {
    btn.addEventListener("click", () => markSiteVisitCompleted(btn.dataset.visitId));
  });
  tbody.querySelectorAll(".crm-sv-view-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (btn.dataset.phone) renderCRMDetails(btn.dataset.phone);
    });
  });
}

async function markSiteVisitCompleted(visitId) {
  if (!visitId) return;
  try {
    await apiJson(`/api/site-visits/${encodeURIComponent(visitId)}`, {
      method: "PATCH",
      body: JSON.stringify({ status: "completed" }),
    });
    showToast("Site visit marked completed", "success");
    await loadSiteVisits(true);
    await bootstrapData();
    const phone = (state.siteVisits.find((v) => String(v.id) === String(visitId)) || {}).phone_number;
    if (phone) renderCRMDetails(phone);
  } catch (e) {
    showToast(e.message || "Could not update visit", "error");
  }
}

function setCrmView(view) {
  state.crmView = view || "all";
  document.querySelectorAll("#crm-view-filters .crm-view-chip").forEach((chip) => {
    const active = chip.dataset.crmView === state.crmView;
    chip.classList.toggle("is-active", active);
    chip.setAttribute("aria-selected", active ? "true" : "false");
  });
  const searchVal = document.getElementById("crm-search")?.value || "";
  if (searchVal.trim()) filterCRM(searchVal);
  else renderCRM();
  renderCrmSiteVisits();
}

function setCrmSiteVisitTab(tab) {
  state.crmSiteVisitTab = tab || "booked";
  renderCrmSiteVisits();
}

function escHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function callerInitials(name, phone) {
  const label = displayCallerName(name, phone);
  if (!label || label.startsWith("Phone ")) {
    const digits = String(phone || "").replace(/\D/g, "");
    return digits.slice(-2) || "•";
  }
  const parts = label.trim().split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  return (parts[0]?.[0] || "•").toUpperCase();
}

function formatCrmDuration(seconds) {
  const s = Math.max(0, Number(seconds) || 0);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rem = s % 60;
  return rem ? `${m}m ${rem}s` : `${m}m`;
}

function renderCrmRecordingPlayer(call) {
  const url = String(call.recording_url || call.recordingUrl || "").trim();
  if (!url) {
    return `<div class="crm-insights-recording-empty">No recording for this call</div>`;
  }
  if (window.CallLogs?.renderPlayer) {
    return window.CallLogs.renderPlayer(url, call.duration_seconds || 0, call.id);
  }
  return `<audio class="crm-audio" controls preload="none" src="${escHtml(url)}"></audio>`;
}

async function loadSiteVisitProperties() {
  if (state.siteVisitProperties.length) return state.siteVisitProperties;
  try {
    const res = await apiJson("/api/site-visits/properties");
    state.siteVisitProperties = Array.isArray(res.properties) ? res.properties : [];
  } catch {
    state.siteVisitProperties = [];
  }
  if (!state.siteVisitProperties.length) {
    state.siteVisitProperties = FALLBACK_SITE_VISIT_PROPERTIES;
  }
  return state.siteVisitProperties;
}

function defaultSiteVisitDatetimeLocal() {
  const d = new Date();
  d.setMinutes(d.getMinutes() + 60 - (d.getMinutes() % 30));
  d.setSeconds(0, 0);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}T${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function renderCrmPersonSiteVisits(person) {
  const visits = person.site_visits || [];
  if (!visits.length) return "";
  return `
    <section class="crm-person-visits">
      <div class="crm-insights-timeline-label">Site visits</div>
      <div class="crm-person-visits-list">
        ${visits.map((v) => `
          <div class="crm-person-visit-item">
            <div>
              <strong>${escHtml(v.property_name || "Property")}</strong>
              <span class="muted">${escHtml(safeAppointmentDate(v.appointment_time))}</span>
            </div>
            ${siteVisitStatusTag(v.status)}
          </div>
        `).join("")}
      </div>
    </section>
  `;
}

function renderCrmSiteVisitForm(person) {
  const props = state.siteVisitProperties.length ? state.siteVisitProperties : FALLBACK_SITE_VISIT_PROPERTIES;
  const options = props.map((p) => `<option value="${escHtml(p.key)}">${escHtml(p.label || p.key)}</option>`).join("");
  const name = displayCallerName(person.caller_name, person.phone_number);
  const defaultWhen = defaultSiteVisitDatetimeLocal();
  return `
    <section class="crm-site-visit">
      <div class="crm-site-visit-head">
        <h5 class="crm-site-visit-title">Schedule site visit</h5>
        <p class="muted crm-site-visit-sub">Sends WhatsApp confirmation with maps link and brochure when available.</p>
      </div>
      <form id="crm-site-visit-form" class="crm-site-visit-form" data-phone="${escHtml(person.phone_number || "")}">
        <label class="field">
          <span>Property</span>
          <select class="input" id="crm-sv-property" required>
            <option value="">Select property…</option>
            ${options}
          </select>
        </label>
        <label class="field">
          <span>Visit date &amp; time (IST)</span>
          <input class="input" type="datetime-local" id="crm-sv-datetime" value="${defaultWhen}" required>
        </label>
        <label class="field">
          <span>Area / location (optional)</span>
          <input class="input" id="crm-sv-location" placeholder="e.g. Kokapet, Financial District">
        </label>
        <label class="field">
          <span>Notes (optional)</span>
          <input class="input" id="crm-sv-notes" placeholder="Scheduled from CRM dashboard">
        </label>
        <input type="hidden" id="crm-sv-name" value="${escHtml(name)}">
        <input type="hidden" id="crm-sv-phone" value="${escHtml(person.phone_number || "")}">
        <p id="crm-sv-status" class="crm-site-visit-status muted" aria-live="polite"></p>
        <button type="submit" class="btn btn-primary btn-sm" id="crm-sv-submit">Confirm &amp; send WhatsApp</button>
      </form>
    </section>
  `;
}

function renderCrmCallTimeline(person, calls) {
  if (!calls.length) {
    return `
      <div class="crm-insights-timeline crm-insights-timeline--empty">
        <div class="crm-insights-timeline-label">Call history</div>
        <p class="muted">No calls logged yet for this contact.</p>
      </div>
    `;
  }
  return `
    <div class="crm-insights-timeline">
      <div class="crm-insights-timeline-label">Call history</div>
      ${calls.map((call, idx) => {
        const purpose = String(call.call_purpose || person.latest_purpose || "").trim();
        const summary = humanizeSummary(call.call_summary || call.summary || person.latest_summary || "Call completed.");
        const showPurpose = purpose && purpose.toLowerCase() !== summary.toLowerCase();
        return `
          <article class="crm-insights-call ${idx === 0 ? "is-latest" : ""}">
            <div class="crm-insights-call-rail" aria-hidden="true">
              <span class="crm-insights-call-dot"></span>
              ${idx < calls.length - 1 ? `<span class="crm-insights-call-line"></span>` : ""}
            </div>
            <div class="crm-insights-call-body">
              <div class="crm-insights-call-top">
                <time class="crm-insights-call-time">${escHtml(safeDate(call.created_at))}</time>
                <span class="crm-insights-call-duration">${formatCrmDuration(call.duration_seconds)}</span>
              </div>
              ${showPurpose ? `<div class="crm-insights-call-purpose">${escHtml(purpose)}</div>` : ""}
              <p class="crm-insights-call-summary">${escHtml(summary)}</p>
              <div class="crm-insights-recording">${renderCrmRecordingPlayer(call)}</div>
            </div>
          </article>
        `;
      }).join("")}
    </div>
  `;
}

async function submitCrmSiteVisit(form) {
  const phone = document.getElementById("crm-sv-phone")?.value?.trim() || form.dataset.phone || "";
  const listingId = document.getElementById("crm-sv-property")?.value?.trim() || "";
  const scheduledAt = document.getElementById("crm-sv-datetime")?.value?.trim() || "";
  const location = document.getElementById("crm-sv-location")?.value?.trim() || "";
  const notes = document.getElementById("crm-sv-notes")?.value?.trim() || "";
  const callerName = document.getElementById("crm-sv-name")?.value?.trim() || "";
  const statusEl = document.getElementById("crm-sv-status");
  const submitBtn = document.getElementById("crm-sv-submit");

  if (!phone || !listingId || !scheduledAt) {
    showToast("Select property and visit time", "error");
    return;
  }

  const prevLabel = submitBtn?.textContent;
  if (submitBtn) {
    submitBtn.disabled = true;
    submitBtn.textContent = "Scheduling…";
  }
  if (statusEl) statusEl.textContent = "Sending WhatsApp confirmation…";

  try {
    const props = state.siteVisitProperties.length ? state.siteVisitProperties : FALLBACK_SITE_VISIT_PROPERTIES;
    const prop = props.find((p) => p.key === listingId);
    const res = await apiJson("/api/site-visits", {
      method: "POST",
      body: JSON.stringify({
        phone,
        caller_name: callerName,
        listing_id: listingId,
        property_name: prop?.label || listingId,
        location,
        scheduled_at: scheduledAt,
        notes: notes || "Scheduled from CRM dashboard",
      }),
    });

    const msg = res.message || "Site visit scheduled";
    if (statusEl) {
      statusEl.className = `crm-site-visit-status ${res.whatsapp_sent ? "is-ok" : "is-warn"}`;
      statusEl.textContent = msg;
    }
    showToast(
      res.whatsapp_sent ? "Site visit confirmed — WhatsApp sent" : "Site visit saved — WhatsApp not delivered",
      res.whatsapp_sent ? "success" : "info",
    );

    await loadSiteVisits(true);
    const person = state.contacts.find((c) => c.phone_number === phone);
    if (person) {
      person.is_booked = true;
      person.crm_pipeline_status = "booked";
      renderCRM(state.contacts);
      document.querySelectorAll("#crm-body .crm-row").forEach((row) => {
        row.classList.toggle("selected", row.dataset.phone === phone);
      });
    }

    try {
      const bookings = await apiJson("/api/bookings");
      state.bookings = Array.isArray(bookings) ? bookings : [];
      window.Calendar?.render?.();
    } catch {}

    if (res.record) {
      form.reset();
      const whenInput = document.getElementById("crm-sv-datetime");
      if (whenInput) whenInput.value = defaultSiteVisitDatetimeLocal();
      const propSelect = document.getElementById("crm-sv-property");
      if (propSelect) propSelect.value = listingId;
    }
  } catch (e) {
    const detail = e.message || "Failed to schedule site visit";
    if (statusEl) {
      statusEl.className = "crm-site-visit-status is-error";
      statusEl.textContent = detail;
    }
    showToast(detail, "error");
  } finally {
    if (submitBtn) {
      submitBtn.disabled = false;
      submitBtn.textContent = prevLabel || "Confirm & send WhatsApp";
    }
  }
}

function renderCrmMemoryPanel(memory) {
  if (!memory?.has_memory) {
    return `
      <section class="crm-memory-panel crm-memory-panel--empty">
        <div class="crm-insights-timeline-label">Customer memory</div>
        <p class="muted">No RE Voice memory yet — profile builds after the first completed call.</p>
      </section>
    `;
  }

  const p = memory.profile || {};
  const prop = memory.property_interests || {};
  const obj = memory.objections || {};
  const budget = p.budget || "—";
  const areas = (p.preferred_areas || []).join(", ") || "—";
  const intent = p.intent_score != null ? `${p.intent_score}/10` : "—";
  const stage = p.deal_stage || "—";
  const memCalls = memory.memory_calls || [];

  const liked = (prop.liked || []).map((id) => `<span class="crm-memory-tag is-liked">${escHtml(id)}</span>`).join("");
  const rejected = (prop.rejected || []).map((id) => `<span class="crm-memory-tag is-rejected">${escHtml(id)}</span>`).join("");

  const objectionLines = (obj.entries || []).slice(0, 5).map((o) => `
    <li class="crm-memory-objection">
      <strong>${escHtml(o.type)}</strong> — ${escHtml(o.raw_phrase || "")}
      <span class="muted">(${escHtml(o.outcome || "")})</span>
    </li>
  `).join("");

  const memCallLines = memCalls.slice(0, 5).map((c) => `
    <li class="crm-memory-call-item">
      <span>${escHtml(safeDate(c.call_date))}</span>
      <span class="muted">${escHtml(c.outcome || c.sentiment || "")}</span>
      ${c.listings_discussed && c.listings_discussed !== "none" ? `<span class="muted"> · ${escHtml(c.listings_discussed)}</span>` : ""}
    </li>
  `).join("");

  return `
    <section class="crm-memory-panel">
      <div class="crm-insights-timeline-label">Customer memory <span class="crm-memory-badge">RE Voice</span></div>
      <div class="crm-memory-grid">
        <div class="crm-memory-field"><span class="crm-memory-label">Intent</span><span>${escHtml(intent)}</span></div>
        <div class="crm-memory-field"><span class="crm-memory-label">Stage</span><span>${escHtml(stage)}</span></div>
        <div class="crm-memory-field"><span class="crm-memory-label">Budget</span><span>${escHtml(budget)}</span></div>
        <div class="crm-memory-field"><span class="crm-memory-label">Areas</span><span>${escHtml(areas)}</span></div>
        <div class="crm-memory-field"><span class="crm-memory-label">Memory calls</span><span>${p.call_count || memCalls.length || 0}</span></div>
        <div class="crm-memory-field"><span class="crm-memory-label">Role</span><span>${escHtml(p.role || "buyer")}</span></div>
      </div>
      ${prop.summary && prop.summary !== "No property history." ? `<p class="crm-memory-summary muted">${escHtml(prop.summary)}</p>` : ""}
      ${liked || rejected ? `
        <div class="crm-memory-tags">
          ${liked ? `<div><span class="crm-memory-label">Liked</span>${liked}</div>` : ""}
          ${rejected ? `<div><span class="crm-memory-label">Rejected</span>${rejected}</div>` : ""}
        </div>
      ` : ""}
      ${obj.history_summary ? `<p class="crm-memory-summary muted">${escHtml(obj.history_summary)}</p>` : ""}
      ${objectionLines ? `<ul class="crm-memory-list">${objectionLines}</ul>` : ""}
      ${memCallLines ? `
        <div class="crm-memory-subhead">Stored call transcripts</div>
        <ul class="crm-memory-list">${memCallLines}</ul>
      ` : ""}
      ${p.notes ? `<p class="crm-memory-notes muted">${escHtml(p.notes)}</p>` : ""}
    </section>
  `;
}

async function fetchLeadMemory(phone) {
  if (!phone) return null;
  try {
    const q = encodeURIComponent(phone);
    return await apiJson(`/api/lead-memory?phone=${q}`);
  } catch {
    return null;
  }
}

function renderCRMDetails(phone) {
  const details = document.getElementById("crm-details");
  state.crmSelectedPhone = phone || "";
  const person = state.contacts.find((c) => c.phone_number === phone);
  const calls = state.logs
    .filter((l) => (l.phone_number || "") === phone)
    .sort((a, b) => new Date(b.created_at || 0).getTime() - new Date(a.created_at || 0).getTime());

  document.querySelectorAll("#crm-body .crm-row").forEach((row) => {
    row.classList.toggle("selected", row.dataset.phone === phone);
  });

  if (!person) {
    details.className = "crm-insights-body crm-details-empty";
    details.innerHTML = `
      <div class="crm-insights-placeholder">
        <div class="crm-insights-placeholder-icon" aria-hidden="true">◎</div>
        <p>Select a contact from the pipeline to schedule a site visit or view call history.</p>
      </div>
    `;
    return;
  }

  const name = displayCallerName(person.caller_name, person.phone_number);
  const initials = callerInitials(person.caller_name, person.phone_number);
  const pipelineStatus = person.crm_pipeline_status
    || (person.is_completed ? "completed" : (person.is_booked ? "booked" : "pending"));
  const statusLabel = pipelineStatus === "completed" ? "Completed" : pipelineStatus === "booked" ? "Booked" : "Pending";
  const statusClass = pipelineStatus === "completed" ? "is-completed" : pipelineStatus === "booked" ? "is-booked" : "is-pending";

  details.className = "crm-insights-body";
  details.innerHTML = `
    <header class="crm-insights-profile">
      <div class="crm-insights-avatar" aria-hidden="true">${escHtml(initials)}</div>
      <div class="crm-insights-identity">
        <h4 class="crm-insights-name">${escHtml(name)}</h4>
        <p class="crm-insights-phone">${escHtml(displayPhone(person.phone_number))}</p>
      </div>
    </header>

    <div class="crm-insights-stats">
      <div class="crm-insights-stat">
        <span class="crm-insights-stat-value">${person.total_calls || calls.length}</span>
        <span class="crm-insights-stat-label">Calls</span>
      </div>
      <div class="crm-insights-stat">
        <span class="crm-insights-stat-value crm-insights-stat-pill ${statusClass}">${statusLabel}</span>
        <span class="crm-insights-stat-label">Status</span>
      </div>
      <div class="crm-insights-stat">
        <span class="crm-insights-stat-value crm-insights-stat-date">${escHtml(safeDate(person.last_seen || calls[0]?.created_at))}</span>
        <span class="crm-insights-stat-label">Last contact</span>
      </div>
    </div>

    <div id="crm-memory-slot"><p class="muted">Loading customer memory…</p></div>

    ${renderCrmPersonSiteVisits(person)}
    ${renderCrmSiteVisitForm(person)}
    ${renderCrmCallTimeline(person, calls)}
  `;

  const form = document.getElementById("crm-site-visit-form");
  form?.addEventListener("submit", (e) => {
    e.preventDefault();
    submitCrmSiteVisit(form);
  });

  window.CallLogs?.initPlayers?.(details);

  fetchLeadMemory(person.phone_number).then((memory) => {
    if (state.crmSelectedPhone !== phone) return;
    const slot = document.getElementById("crm-memory-slot");
    if (slot) slot.innerHTML = renderCrmMemoryPanel(memory);
  });
}

function renderAnalytics() {
  window.Analytics?.render?.(state.analytics);
}

function renderCalendar() {
  window.Calendar?.setupToolbar?.();
  window.Calendar?.render?.();
}

function formatLanguages(langs) {
  if (!Array.isArray(langs) || !langs.length) return "en";
  return langs.map((l) => String(l).toUpperCase()).join(", ");
}

function agentCardMeta(agent) {
  const meta = agent.metadata || {};
  return {
    kbFiles: meta.knowledge_base_files ?? 0,
    searchEnabled: Boolean(meta.search_enabled),
  };
}

function agentAvatarHue(name) {
  let hash = 0;
  const label = String(name || "Agent");
  for (let i = 0; i < label.length; i += 1) hash = label.charCodeAt(i) + ((hash << 5) - hash);
  return Math.abs(hash) % 360;
}

function agentInitials(name) {
  const label = String(name || "Agent").trim();
  const parts = label.split(/\s+/).filter(Boolean);
  if (parts.length >= 2) return `${parts[0][0]}${parts[1][0]}`.toUpperCase();
  return label.slice(0, 2).toUpperCase();
}

function renderAssistants() {
  const grid = document.getElementById("assistants-grid");
  const countEl = document.getElementById("assistants-count");
  if (!grid) return;

  const agents = state.agents || [];
  if (countEl) {
    countEl.textContent = String(agents.length);
    countEl.setAttribute("aria-label", `${agents.length} agent${agents.length === 1 ? "" : "s"}`);
  }

  if (!agents.length) {
    grid.innerHTML = `
      <div class="agents-empty" id="assistants-empty">
        <h3>No agents yet</h3>
        <p>Add a brief above and tap Generate, or start with a blank preset.</p>
      </div>
    `;
    return;
  }

  grid.innerHTML = `
    <div class="agents-group">
      ${agents.map((agent) => {
    const meta = agentCardMeta(agent);
    const name = agent.name || "Unnamed agent";
    const purpose = (agent.purpose || "general").replace(/_/g, " ");
    const initials = agentInitials(name);
    const isLive = Boolean(agent.is_active);
    const specParts = [
      formatLanguages(agent.languages),
      agent.llm_model || agent.llm_provider || "—",
      agent.tts_voice_id || "—",
    ];
    if (meta.searchEnabled) specParts.push("Search");
    if (meta.kbFiles > 0) specParts.push(`${meta.kbFiles} KB`);

    return `
      <article class="assistant-card agent-row${isLive ? " is-live" : ""}" data-agent-id="${agent.id}">
        <div class="agent-row-main">
          <div class="agent-avatar" aria-hidden="true">${escHtml(initials)}</div>
          <div class="agent-row-copy">
            <div class="agent-row-top">
              <h3 class="agent-row-name">${escHtml(name)}</h3>
              <span class="agent-status ${isLive ? "is-live" : "is-paused"}">
                <span class="agent-status-dot" aria-hidden="true"></span>
                ${isLive ? "Live" : "Paused"}
              </span>
            </div>
            <p class="agent-row-purpose">${escHtml(purpose)}</p>
            <p class="agent-row-specs">${specParts.map((s) => `<span>${escHtml(s)}</span>`).join("")}</p>
          </div>
        </div>
        <div class="agent-row-actions">
          <button type="button" class="agent-run-btn" data-action="run" data-agent-id="${agent.id}">
            ${isLive ? "Run again" : "Run"}
          </button>
          <div class="agent-row-links">
            <button type="button" class="agent-link-btn" data-action="edit" data-agent-id="${agent.id}">Edit</button>
            <button type="button" class="agent-link-btn" data-action="test" data-agent-id="${agent.id}">Test</button>
            <button type="button" class="agent-link-btn agent-link-danger" data-action="delete" data-agent-id="${agent.id}" data-agent-name="${name.replace(/"/g, "&quot;")}">Delete</button>
          </div>
        </div>
      </article>
    `;
  }).join("")}
    </div>
  `;
}

async function loadAssistants() {
  try {
    const res = await apiJson("/api/agents");
    state.agents = Array.isArray(res.data) ? res.data : [];
    renderAssistants();
  } catch (e) {
    if (String(e.message).includes("Authentication")) return;
    showToast(e.message || "Failed to load assistants", "error");
    state.agents = [];
    renderAssistants();
  }
}

async function runAgent(agentId, agentName, triggerBtn) {
  if (!agentId) return;
  const prevLabel = triggerBtn?.textContent;
  if (triggerBtn) {
    triggerBtn.disabled = true;
    triggerBtn.textContent = "Starting…";
  }
  try {
    const res = await apiJson(`/api/agents/${agentId}/run`, { method: "POST" });
    const data = res.data || res;
    const name = data.agent?.name || agentName || "Agent";
    const preset = data.preset || {};
    const restarted = data.restart?.success;
    const presetHint = preset.llm_provider
      ? `${preset.llm_provider}/${preset.llm_model || "default"}`
      : "";
    showToast(
      restarted
        ? `“${name}” is running live${presetHint ? ` (${presetHint})` : ""}`
        : (data.message || `“${name}” set as live — restart agent.py manually`),
      restarted ? "success" : "info",
    );
    if (data.worker_hint) {
      console.info("[run-agent]", data.worker_hint);
    }
    await loadAssistants();
    return data;
  } catch (e) {
    if (String(e.message).includes("Authentication")) return;
    showToast(e.message || "Failed to run agent", "error");
    throw e;
  } finally {
    if (triggerBtn) {
      triggerBtn.disabled = false;
      triggerBtn.textContent = prevLabel || "Run";
    }
  }
}

/** @deprecated use runAgent */
async function activateAgent(agentId, agentName, triggerBtn) {
  return runAgent(agentId, agentName, triggerBtn);
}
window.runAgent = runAgent;
window.activateAgent = activateAgent;

async function deleteAgent(agentId, agentName) {
  if (!agentId) return;
  const label = agentName || "this agent";
  if (!window.confirm(`Delete “${label}”? This cannot be undone.`)) return;

  try {
    const res = await apiJson(`/api/agents/${agentId}`, { method: "DELETE" });
    showToast(res.message || `Deleted “${label}”`, "success");
    if (state.currentPage === "agent-detail" && String(state.currentAgentId) === String(agentId)) {
      state.currentAgentId = null;
      navigateTo("assistants");
    }
    await loadAssistants();
  } catch (e) {
    if (String(e.message).includes("Authentication")) return;
    if (e.code === "rls_denied" || String(e.message).includes("permission denied")) {
      showToast(
        "Database permission denied — set SUPABASE_SERVICE_ROLE_KEY in .env or run supabase_fix_rls.sql in Supabase",
        "error",
      );
      return;
    }
    showToast(e.message || "Failed to delete agent", "error");
  }
}
window.deleteAgent = deleteAgent;

async function createAssistantFromDescription() {
  const textarea = document.getElementById("assistant-description");
  const btn = document.getElementById("create-assistant-btn");
  const btnLabel = document.getElementById("create-assistant-btn-label");
  const description = (textarea?.value || "").trim();

  if (description.length < 10) {
    showToast("Please write at least 10 characters describing your assistant.", "error");
    textarea?.focus();
    return;
  }

  if (state.creatingAssistant) return;
  state.creatingAssistant = true;
  if (btn) btn.disabled = true;
  if (btnLabel) btnLabel.textContent = "Generating…";

  try {
    const useCases = Array.from(state.selectedUseCases);
    const res = await apiJson("/api/agents/create-from-description", {
      method: "POST",
      body: JSON.stringify({ description, use_cases: useCases }),
    });

    const data = res.data || res;
    const agent = data.agent || data;
    const agentId = agent.id || data.id;
    const agentName = agent.name || data.name || "New Assistant";
    const purpose = agent.purpose || data.purpose || "";

    showToast(`Created “${agentName}” — starting live with its preset…`, "success");
    if (textarea) textarea.value = "";
    state.selectedUseCases.clear();
    document.querySelectorAll("#use-case-chips .chip.selected").forEach((c) => c.classList.remove("selected"));

    await loadAssistants();
    if (agentId) {
      await runAgent(agentId, agentName);
      openAgentDetail(agentId, agentName, purpose);
    }
  } catch (e) {
    const msg = e.message || "Failed to create assistant";
    if (String(msg).includes("Authentication")) return;
    if (e.code === "rls_denied" || msg.includes("permission denied") || msg.includes("row-level security")) {
      showToast(
        "Database permission denied — set SUPABASE_SERVICE_ROLE_KEY in .env or run supabase_fix_rls.sql in Supabase SQL Editor",
        "error",
      );
    } else if (msg.includes("migration_required") || msg.includes("Agents tables not found")) {
      showToast("Database not ready — run supabase_migration_v4_agents.sql in Supabase", "error");
    } else if (msg.includes("not_configured") || msg.includes("Supabase")) {
      showToast("Set SUPABASE_URL and SUPABASE_KEY (or SERVICE_ROLE_KEY) in environment", "error");
    } else {
      showToast(msg, "error");
    }
  } finally {
    state.creatingAssistant = false;
    if (btn) btn.disabled = false;
    if (btnLabel) btnLabel.textContent = "Generate";
  }
}

function setupAssistants() {
  document.getElementById("use-case-chips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip[data-use-case]");
    if (!chip) return;
    const key = chip.dataset.useCase;
    if (state.selectedUseCases.has(key)) {
      state.selectedUseCases.delete(key);
      chip.classList.remove("selected");
    } else {
      state.selectedUseCases.add(key);
      chip.classList.add("selected");
    }
  });

  document.getElementById("create-assistant-btn")?.addEventListener("click", createAssistantFromDescription);

  document.getElementById("create-blank-agent-btn")?.addEventListener("click", async () => {
    if (state.creatingAssistant) return;
    state.creatingAssistant = true;
    const btn = document.getElementById("create-blank-agent-btn");
    if (btn) btn.disabled = true;
    try {
      const res = await apiJson("/api/agents", {
        method: "POST",
        body: JSON.stringify({
          agent: {
            name: "New Voice Agent",
            purpose: "general",
            system_prompt: "You are a named sales employee on a live phone call. Be warm, concise, and human.",
            welcome_message: "Hello! How can I help you today?",
            languages: ["en"],
            llm_provider: "gemini",
            llm_model: "gemini-2.0-flash",
            stt_provider: "sarvam",
            tts_provider: "sarvam",
            tts_voice_id: "kavya",
          },
        }),
      });
      const data = res.data || res;
      const agent = data.agent || data;
      showToast("Blank agent created — configure it in the detail view", "success");
      await loadAssistants();
      if (agent.id) openAgentDetail(agent.id, agent.name, agent.purpose);
    } catch (e) {
      showToast(e.message || "Quick create failed", "error");
    } finally {
      state.creatingAssistant = false;
      if (btn) btn.disabled = false;
    }
  });

  document.getElementById("assistants-grid")?.addEventListener("click", (e) => {
    const btn = e.target.closest("button[data-action]");
    if (btn) {
      const agentId = btn.dataset.agentId;
      const agent = state.agents.find((a) => String(a.id) === String(agentId));
      if (!agent) return;
      if (btn.dataset.action === "run" || btn.dataset.action === "activate") {
        runAgent(agent.id, agent.name, btn);
      } else if (btn.dataset.action === "edit") {
        openAgentDetail(agent.id, agent.name, agent.purpose);
      } else if (btn.dataset.action === "test") {
        openTestCallModal(agent.id, agent.name);
      } else if (btn.dataset.action === "delete") {
        deleteAgent(agent.id, btn.dataset.agentName || agent.name);
      }
      return;
    }

    const card = e.target.closest(".assistant-card[data-agent-id]");
    if (card) {
      const agent = state.agents.find((a) => String(a.id) === String(card.dataset.agentId));
      if (agent) openAgentDetail(agent.id, agent.name, agent.purpose);
    }
  });
}

/* ── Platform config (Settings) ── */

function populateConfigForm(cfg) {
  const map = {
    "cfg-first-line": cfg.first_line,
    "cfg-lang-preset": cfg.lang_preset,
    "cfg-language": cfg.language,
    "cfg-max-turns": cfg.max_turns,
    "cfg-agent-instructions": cfg.agent_instructions,
    "cfg-stt-provider": cfg.stt_provider,
    "cfg-stt-language": cfg.stt_language,
    "cfg-tts-provider": cfg.tts_provider,
    "cfg-tts-voice": cfg.tts_voice,
    "cfg-tts-language": cfg.tts_language,
    "cfg-stt-min-delay": cfg.stt_min_endpointing_delay,
    "cfg-stt-max-delay": cfg.stt_max_endpointing_delay,
    "cfg-llm-provider": cfg.llm_provider,
    "cfg-llm-model": cfg.llm_model,
    "cfg-gemini-model": cfg.gemini_model,
    "cfg-llm-max-tokens": cfg.llm_max_completion_tokens,
    "cfg-campaign-id": cfg.campaign_id,
    "cfg-timezone": cfg.timezone,
  };
  Object.entries(map).forEach(([id, value]) => {
    const el = document.getElementById(id);
    if (!el || value == null) return;
    el.value = String(value);
  });
}

function collectConfigForm() {
  const num = (id, fallback) => {
    const v = document.getElementById(id)?.value;
    const n = Number(v);
    return Number.isFinite(n) ? n : fallback;
  };
  return {
    first_line: document.getElementById("cfg-first-line")?.value?.trim() || "",
    agent_instructions: document.getElementById("cfg-agent-instructions")?.value?.trim() || "",
    lang_preset: document.getElementById("cfg-lang-preset")?.value || "tinglish",
    language: document.getElementById("cfg-language")?.value || "",
    max_turns: num("cfg-max-turns", 30),
    stt_provider: document.getElementById("cfg-stt-provider")?.value || "sarvam",
    stt_language: document.getElementById("cfg-stt-language")?.value?.trim() || "",
    stt_min_endpointing_delay: num("cfg-stt-min-delay", 0.35),
    stt_max_endpointing_delay: num("cfg-stt-max-delay", 3.5),
    tts_provider: document.getElementById("cfg-tts-provider")?.value || "sarvam",
    tts_voice: document.getElementById("cfg-tts-voice")?.value?.trim() || "kavya",
    tts_language: document.getElementById("cfg-tts-language")?.value?.trim() || "en-IN",
    llm_provider: document.getElementById("cfg-llm-provider")?.value || "gemini",
    llm_model: document.getElementById("cfg-llm-model")?.value?.trim() || "gpt-4o-mini",
    gemini_model: document.getElementById("cfg-gemini-model")?.value?.trim() || "gemini-2.0-flash",
    llm_max_completion_tokens: num("cfg-llm-max-tokens", 80),
    campaign_id: document.getElementById("cfg-campaign-id")?.value?.trim() || "",
    timezone: document.getElementById("cfg-timezone")?.value?.trim() || "Asia/Kolkata",
  };
}

async function loadPlatformConfig() {
  try {
    const cfg = await apiJson("/api/config");
    populateConfigForm(cfg);
  } catch (e) {
    if (!String(e.message).includes("Authentication")) {
      showToast(e.message || "Failed to load platform config", "error");
    }
  }
}

async function savePlatformConfig() {
  const btn = document.getElementById("settings-save-btn");
  const prev = btn?.textContent;
  if (btn) {
    btn.disabled = true;
    btn.textContent = "Saving…";
  }
  try {
    const payload = collectConfigForm();
    const res = await apiJson("/api/config", {
      method: "POST",
      body: JSON.stringify(payload),
    });
    if (res.config) populateConfigForm(res.config);
    showToast("Platform configuration saved", "success");
  } catch (e) {
    showToast(e.message || "Failed to save configuration", "error");
  } finally {
    if (btn) {
      btn.disabled = false;
      btn.textContent = prev || "Save Configuration";
    }
  }
}

function setupSettings() {
  document.getElementById("settings-save-btn")?.addEventListener("click", savePlatformConfig);
}

/* ── Test call modal ── */

function populateAgentSelect(selectId, selectedId = "") {
  const select = document.getElementById(selectId);
  if (!select) return;
  const current = selectedId || select.value;
  select.innerHTML = `<option value="">Default agent</option>${(state.agents || []).map((a) => `
    <option value="${a.id}" ${String(a.id) === String(current) ? "selected" : ""}>${a.name || "Unnamed"}</option>
  `).join("")}`;
}

function openTestCallModal(agentId = "", agentName = "") {
  populateAgentSelect("test-call-agent", agentId);
  const phoneEl = document.getElementById("test-call-phone");
  const resultEl = document.getElementById("test-call-result");
  if (phoneEl) phoneEl.value = "";
  if (resultEl) resultEl.textContent = "";
  const title = document.getElementById("test-call-title");
  if (title) {
    title.textContent = agentName ? `Test Call — ${agentName}` : "Test Call";
  }
  openModal("modal-test-call");
}

async function dispatchTestCall(phone, agentId) {
  const body = { phone: phone.trim() };
  if (agentId) body.agent_id = agentId;
  return apiJson("/api/call/single", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

function setupTestCall() {
  document.getElementById("test-call-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const phone = document.getElementById("test-call-phone")?.value || "";
    const agentId = document.getElementById("test-call-agent")?.value || "";
    const resultEl = document.getElementById("test-call-result");
    const submitBtn = document.getElementById("test-call-submit");
    if (!phone.trim()) {
      showToast("Enter a phone number", "error");
      return;
    }
    if (submitBtn) submitBtn.disabled = true;
    if (resultEl) resultEl.textContent = "Dispatching call…";
    try {
      const res = await dispatchTestCall(phone, agentId);
      if (res.status === "error") throw new Error(res.message || "Dispatch failed");
      const msg = `Dispatched to ${res.phone}. Room: ${res.room}`;
      if (resultEl) resultEl.textContent = msg;
      showToast("Call dispatched successfully", "success");
    } catch (err) {
      if (resultEl) resultEl.textContent = err.message || "Dispatch failed";
      if (!String(err.message).includes("Authentication")) {
        showToast(err.message || "Call dispatch failed", "error");
      }
    } finally {
      if (submitBtn) submitBtn.disabled = false;
    }
  });
}

window.openTestCallModal = openTestCallModal;

/* ── Main agent — Make Call modal ── */

const RECENT_CALLS_KEY = "speaksy_recent_call_phones";
const RECENT_CALLS_MAX = 6;
let makeCallDispatching = false;

function normalizeRecentCallPhone(phone) {
  return String(phone || "").trim().replace(/\s+/g, "");
}

function isDialablePhone(phone) {
  const value = normalizeRecentCallPhone(phone);
  return /^\+?\d{10,15}$/.test(value);
}

function loadStoredRecentCallPhones() {
  try {
    const raw = localStorage.getItem(RECENT_CALLS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    if (!Array.isArray(parsed)) return [];
    return parsed.map(normalizeRecentCallPhone).filter(isDialablePhone);
  } catch {
    return [];
  }
}

function rememberRecentCallPhone(phone) {
  const normalized = normalizeRecentCallPhone(phone);
  if (!isDialablePhone(normalized)) return;
  const stored = loadStoredRecentCallPhones().filter((p) => p !== normalized);
  stored.unshift(normalized);
  localStorage.setItem(RECENT_CALLS_KEY, JSON.stringify(stored.slice(0, RECENT_CALLS_MAX)));
}

function phonesFromCallLogs() {
  const phones = [];
  for (const log of state.logs || []) {
    const candidate = normalizeRecentCallPhone(log.to_number || log.phone_number || "");
    if (isDialablePhone(candidate)) phones.push(candidate);
  }
  return phones;
}

function getRecentCallPhones() {
  const seen = new Set();
  const merged = [];
  for (const phone of [...loadStoredRecentCallPhones(), ...phonesFromCallLogs()]) {
    if (seen.has(phone)) continue;
    seen.add(phone);
    merged.push(phone);
    if (merged.length >= RECENT_CALLS_MAX) break;
  }
  return merged;
}

function renderMakeCallRecent() {
  const wrap = document.getElementById("make-call-recent");
  const chipsEl = document.getElementById("make-call-recent-chips");
  if (!wrap || !chipsEl) return;

  const phones = getRecentCallPhones();
  if (!phones.length) {
    wrap.hidden = true;
    chipsEl.innerHTML = "";
    return;
  }

  wrap.hidden = false;
  chipsEl.innerHTML = phones
    .map(
      (phone) => `
        <button type="button" class="make-call-recent-chip" data-phone="${escHtml(phone)}" title="Call ${escHtml(displayPhone(phone))}">
          <span data-lucide="phone-outgoing" aria-hidden="true"></span>
          <span>${escHtml(displayPhone(phone))}</span>
        </button>
      `,
    )
    .join("");
  refreshMakeCallIcons();
}

function setMakeCallUiState({ loading = false, statusText = "", statusKind = "" } = {}) {
  const phoneEl = document.getElementById("make-call-phone");
  const statusEl = document.getElementById("make-call-status");
  const submitBtn = document.getElementById("make-call-submit");
  const submitText = submitBtn?.querySelector(".make-call-submit-text");
  const chips = document.querySelectorAll(".make-call-recent-chip");

  if (phoneEl) phoneEl.disabled = loading;
  if (submitBtn) {
    submitBtn.disabled = loading;
    submitBtn.classList.toggle("is-loading", loading);
  }
  if (submitText) submitText.textContent = loading ? "Calling…" : "Call now";
  chips.forEach((chip) => {
    chip.disabled = loading;
  });

  if (statusEl) {
    if (statusText) {
      statusEl.hidden = false;
      statusEl.textContent = statusText;
      statusEl.classList.remove("is-success", "is-error");
      if (statusKind) statusEl.classList.add(statusKind);
    } else {
      statusEl.hidden = true;
      statusEl.textContent = "";
      statusEl.classList.remove("is-success", "is-error");
    }
  }
}

async function dispatchMakeCall(phone) {
  const normalized = normalizeRecentCallPhone(phone);
  if (!normalized) {
    showToast("Enter a phone number", "error");
    document.getElementById("make-call-phone")?.focus();
    return;
  }
  if (makeCallDispatching) return;

  makeCallDispatching = true;
  setMakeCallUiState({
    loading: true,
    statusText: "Dispatching outbound call…",
  });

  try {
    const res = await dispatchTestCall(normalized, "");
    if (res.status === "error") throw new Error(res.message || "Dispatch failed");
    const dialed = res.phone || normalized;
    rememberRecentCallPhone(dialed);
    renderMakeCallRecent();
    makeCallDispatching = false;
    setMakeCallUiState({
      loading: false,
      statusText: `Calling ${dialed}…`,
      statusKind: "is-success",
    });
    showToast(`Call dispatched to ${dialed}`, "success");
    window.setTimeout(() => closeModal("modal-make-call"), 1200);
  } catch (err) {
    const message = err.message || "Call dispatch failed";
    makeCallDispatching = false;
    setMakeCallUiState({
      loading: false,
      statusText: message,
      statusKind: "is-error",
    });
    if (!String(message).includes("Authentication")) {
      showToast(message, "error");
    }
  }
}

function refreshMakeCallIcons() {
  window.lucide?.createIcons?.({
    attrs: { "stroke-width": 1.75 },
    nameAttr: "data-lucide",
  });
}

function openMakeCallModal() {
  const phoneEl = document.getElementById("make-call-phone");
  makeCallDispatching = false;
  if (phoneEl) {
    phoneEl.value = "";
    phoneEl.disabled = false;
  }
  setMakeCallUiState();
  renderMakeCallRecent();
  openModal("modal-make-call");
  refreshMakeCallIcons();
  window.setTimeout(() => phoneEl?.focus(), 60);
}

function setupMakeCall() {
  document.getElementById("ma-make-call-btn")?.addEventListener("click", openMakeCallModal);

  document.getElementById("make-call-recent-chips")?.addEventListener("click", (e) => {
    const chip = e.target.closest(".make-call-recent-chip");
    if (!chip || chip.disabled) return;
    const phone = chip.dataset.phone || "";
    const phoneEl = document.getElementById("make-call-phone");
    if (phoneEl) phoneEl.value = phone;
    dispatchMakeCall(phone);
  });

  document.getElementById("make-call-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const phone = document.getElementById("make-call-phone")?.value?.trim() || "";
    await dispatchMakeCall(phone);
  });

  document.addEventListener("keydown", (e) => {
    const modal = document.getElementById("modal-make-call");
    if (e.key === "Escape" && modal && !modal.hidden) closeModal("modal-make-call");
  });
}

window.openMakeCallModal = openMakeCallModal;

/* ── Outbound campaigns ── */

const campaignState = {
  fileName: "",
  currentCampaignId: "",
  pollTimer: null,
};

function parsePhonesFromText(text) {
  const phones = new Set();
  const lines = String(text || "").split(/\r?\n/);
  const phoneRe = /\+?\d{10,15}/g;

  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line || /^phone|mobile|number|contact/i.test(line)) continue;
    const cells = line.split(/[,;\t]/).map((c) => c.trim()).filter(Boolean);
    const targets = cells.length ? cells : [line];
    for (const cell of targets) {
      const matches = cell.match(phoneRe);
      if (matches) {
        matches.forEach((m) => {
          const normalized = m.replace(/[\s()-]/g, "");
          if (normalized.length >= 10) phones.add(normalized);
        });
      }
    }
  }
  return [...phones];
}

function getCampaignNumbers() {
  const textarea = document.getElementById("campaign-numbers");
  return parsePhonesFromText(textarea?.value || "");
}

function updateCampaignStats() {
  const numbers = getCampaignNumbers();
  const countEl = document.getElementById("campaign-count");
  const estimateEl = document.getElementById("campaign-estimate");
  const preview = document.getElementById("campaign-preview");
  const previewList = document.getElementById("campaign-preview-list");
  const previewCount = document.getElementById("campaign-preview-count");
  const delay = Math.max(0, Number(document.getElementById("campaign-delay")?.value) || 0);

  if (countEl) countEl.textContent = String(numbers.length);
  if (estimateEl) {
    if (!numbers.length) estimateEl.textContent = "—";
    else {
      const seconds = numbers.length * Math.max(3, delay + 2);
      const mins = Math.ceil(seconds / 60);
      estimateEl.textContent = mins < 60 ? `~${mins} min` : `~${Math.round(mins / 60)} hr`;
    }
  }

  if (preview && previewList && previewCount) {
    if (!numbers.length) {
      preview.hidden = true;
      previewList.innerHTML = "";
      return;
    }
    preview.hidden = false;
    previewCount.textContent = `${numbers.length} total`;
    previewList.innerHTML = numbers.slice(0, 8).map((n) => `<span class="campaign-preview-chip">${n}</span>`).join("")
      + (numbers.length > 8 ? `<span class="campaign-preview-more muted">+${numbers.length - 8} more</span>` : "");
  }
}

function mergeCampaignNumbers(newPhones) {
  const textarea = document.getElementById("campaign-numbers");
  if (!textarea) return;
  const existing = parsePhonesFromText(textarea.value);
  const merged = [...new Set([...existing, ...newPhones])];
  textarea.value = merged.join("\n");
  updateCampaignStats();
}

async function handleCampaignFile(file) {
  if (!file) return;
  const text = await file.text();
  const phones = parsePhonesFromText(text);
  if (!phones.length) {
    showToast("No phone numbers found in file", "error");
    return;
  }
  campaignState.fileName = file.name;
  const meta = document.getElementById("campaign-upload-meta");
  const nameEl = document.getElementById("campaign-upload-name");
  if (meta) meta.hidden = false;
  if (nameEl) nameEl.textContent = `${file.name} · ${phones.length} numbers imported`;
  mergeCampaignNumbers(phones);
  showToast(`Imported ${phones.length} numbers from ${file.name}`, "success");
}

function formatCampaignTime(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("en-IN", { dateStyle: "medium", timeStyle: "short" });
  } catch {
    return iso;
  }
}

function formatCampaignLabel(campaign = {}) {
  const name = (campaign.campaign_name || "").trim();
  if (name) return name;
  const id = campaign.campaign_id || campaignState.currentCampaignId || "";
  return id ? `Campaign ${id.slice(0, 8)}` : "Campaign";
}

function formatCampaignStatus(status = "") {
  return String(status || "pending").replace(/_/g, " ");
}

function campaignStatusClass(status = "") {
  if (status === "answered") return "is-answered";
  if (["not_lifted", "no_answer", "busy"].includes(status)) return "is-not-lifted";
  if (status === "dispatched") return "is-dispatched";
  if (["failed", "error", "cancelled"].includes(status)) return "is-failed";
  return "";
}

function renderCampaignOutcomeStats(stats = {}) {
  const el = document.getElementById("campaign-outcome-stats");
  if (!el) return;
  const items = [
    ["Total", stats.total || 0],
    ["Dispatched", stats.dispatched || 0],
    ["Answered", stats.answered || 0],
    ["Not lifted", stats.not_lifted || 0],
  ];
  el.innerHTML = items.map(([label, value]) => `
    <div class="campaign-stat-pill">
      <span class="campaign-stat-label">${label}</span>
      <strong>${value}</strong>
    </div>
  `).join("");
}

function renderCampaignDetailHeader(campaign = {}) {
  const titleEl = document.getElementById("campaign-detail-title");
  const subtitleEl = document.getElementById("campaign-detail-subtitle");
  const label = formatCampaignLabel(campaign);
  const created = formatCampaignTime(campaign.created_at);
  if (titleEl) titleEl.textContent = label;
  if (subtitleEl) {
    subtitleEl.textContent = campaign.campaign_id
      ? `Created ${created} · ${campaign.campaign_id.slice(0, 8)}…`
      : "Select a recent campaign below, or dispatch a new one.";
  }
}

function renderCampaignContactRows(contacts = []) {
  const body = document.getElementById("campaign-not-lifted-body");
  const panel = document.getElementById("campaign-not-lifted-panel");
  if (!body || !panel) return;

  panel.hidden = !campaignState.currentCampaignId;
  if (!contacts.length) {
    body.innerHTML = `<tr><td colspan="4" class="muted">No contacts in this campaign yet.</td></tr>`;
    return;
  }

  body.innerHTML = contacts.map((row) => {
    const status = row.status || "pending";
    return `
      <tr>
        <td>${row.phone || "—"}</td>
        <td><span class="campaign-status-pill ${campaignStatusClass(status)}">${formatCampaignStatus(status)}</span></td>
        <td>${row.duration_seconds != null ? `${row.duration_seconds}s` : "—"}</td>
        <td>${formatCampaignTime(row.updated_at)}</td>
      </tr>
    `;
  }).join("");
}

function showCampaignDetailPanel({ scroll = false } = {}) {
  const panel = document.getElementById("campaign-not-lifted-panel");
  if (!panel || !campaignState.currentCampaignId) return;
  panel.hidden = false;
  if (scroll) {
    panel.scrollIntoView({ behavior: "smooth", block: "start" });
  }
}

async function loadCampaignDetail(campaignId, { scroll = false } = {}) {
  if (!campaignId) return;
  try {
    const res = await apiJson(`/api/campaigns/${encodeURIComponent(campaignId)}`);
    const campaign = res.campaign;
    if (!campaign) {
      showToast("Campaign not found", "error");
      return;
    }
    campaignState.currentCampaignId = campaignId;
    renderCampaignDetailHeader(campaign);
    renderCampaignOutcomeStats(campaign.stats || {});
    renderCampaignContactRows(campaign.contacts || []);
    showCampaignDetailPanel({ scroll });
    highlightCampaignHistoryRow(campaignId);
  } catch (e) {
    showToast(e.message || "Failed to load campaign", "error");
  }
}

function highlightCampaignHistoryRow(campaignId) {
  document.querySelectorAll("#campaign-history-body tr[data-campaign-id]").forEach((row) => {
    const selected = row.getAttribute("data-campaign-id") === campaignId;
    row.classList.toggle("is-selected", selected);
    const btn = row.querySelector("[data-campaign-id]");
    if (btn) btn.textContent = selected ? "Viewing" : "View";
  });
}

async function loadCampaignHistory() {
  const body = document.getElementById("campaign-history-body");
  if (!body) return;
  try {
    const res = await apiJson("/api/campaigns");
    const rows = res.campaigns || [];
    if (!rows.length) {
      body.innerHTML = `<tr><td colspan="6" class="muted">No campaigns yet</td></tr>`;
      return;
    }
    body.innerHTML = rows.map((row) => {
      const selected = row.campaign_id === campaignState.currentCampaignId;
      return `
      <tr class="campaign-history-row${selected ? " is-selected" : ""}" data-campaign-id="${row.campaign_id}">
        <td>${row.campaign_name || row.campaign_id?.slice(0, 8) || "Campaign"}</td>
        <td>${row.total || 0}</td>
        <td>${row.answered || 0}</td>
        <td>${row.not_lifted || 0}</td>
        <td>${formatCampaignTime(row.created_at)}</td>
        <td><button type="button" class="btn btn-ghost btn-sm" data-campaign-id="${row.campaign_id}">${selected ? "Viewing" : "View"}</button></td>
      </tr>
    `;
    }).join("");
  } catch (e) {
    body.innerHTML = `<tr><td colspan="6" class="muted">${e.message || "Failed to load campaigns"}</td></tr>`;
  }
}

async function downloadNotLiftedCsv() {
  const campaignId = campaignState.currentCampaignId;
  if (!campaignId) {
    showToast("Dispatch a campaign first", "error");
    return;
  }
  try {
    const res = await apiFetch(`/api/campaigns/${encodeURIComponent(campaignId)}/not-lifted/export/csv`);
    if (!res.ok) throw new Error(`Export failed (${res.status})`);
    const blob = await res.blob();
    const link = document.createElement("a");
    link.href = URL.createObjectURL(blob);
    link.download = `not_lifted_${campaignId.slice(0, 8)}.csv`;
    link.click();
    URL.revokeObjectURL(link.href);
    showToast("Not-lifted CSV downloaded", "success");
  } catch (e) {
    showToast(e.message || "CSV download failed", "error");
  }
}

function startCampaignPolling(campaignId) {
  if (campaignState.pollTimer) clearInterval(campaignState.pollTimer);
  if (!campaignId) return;
  campaignState.pollTimer = setInterval(() => {
    if (state.currentPage === "campaigns") {
      loadCampaignDetail(campaignId).catch(() => {});
      loadCampaignHistory().catch(() => {});
    }
  }, 15000);
}

async function loadCampaignsPage() {
  try {
    await loadAssistants();
    populateAgentSelect("campaign-agent-select");
    updateCampaignStats();
    await loadCampaignHistory();
    if (campaignState.currentCampaignId) {
      await loadCampaignDetail(campaignState.currentCampaignId);
    }
  } catch (e) {
    showToast(e.message || "Failed to load agents for campaign", "error");
  }
}

async function dispatchCampaign() {
  const numbers = getCampaignNumbers();
  const agentId = document.getElementById("campaign-agent-select")?.value || "";
  const statusEl = document.getElementById("campaign-status");
  const resultsEl = document.getElementById("campaign-results");
  const resultsPanel = document.getElementById("campaign-results-panel");
  const btn = document.getElementById("campaign-dispatch-btn");

  if (!numbers.length) {
    showToast("Add at least one phone number", "error");
    return;
  }

  const payload = {
    numbers: numbers.join("\n"),
    agent_id: agentId || null,
    campaign_name: document.getElementById("campaign-name")?.value?.trim() || "",
    window_start: document.getElementById("campaign-window-start")?.value || "09:00",
    window_end: document.getElementById("campaign-window-end")?.value || "20:00",
    timezone: document.getElementById("campaign-timezone")?.value || "Asia/Kolkata",
    respect_window: document.getElementById("campaign-respect-window")?.checked !== false,
    delay_seconds: Number(document.getElementById("campaign-delay")?.value) || 0,
    max_concurrent: Number(document.getElementById("campaign-concurrency")?.value) || 5,
  };

  if (btn) btn.disabled = true;
  if (statusEl) statusEl.textContent = `Dispatching ${numbers.length} calls…`;
  if (resultsEl) resultsEl.innerHTML = "";
  if (resultsPanel) resultsPanel.hidden = true;

  try {
    const res = await apiJson("/api/call/bulk", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    if (res.blocked_by_window) {
      showToast(res.message || "Outside calling window", "error");
      if (statusEl) statusEl.textContent = res.message || "Outside calling window";
      return;
    }

    const results = res.results || [];
    const ok = results.filter((r) => r.status === "ok").length;
    if (statusEl) {
      statusEl.textContent = res.campaign_id
        ? `${ok}/${results.length} dispatched · ID ${res.campaign_id.slice(0, 8)}…`
        : `${ok}/${results.length} calls dispatched`;
    }
    if (resultsPanel) resultsPanel.hidden = false;
    if (resultsEl) {
      resultsEl.innerHTML = results.map((r) => `
        <div class="campaign-result-row ${r.status === "ok" ? "ok" : "err"}">
          <strong>${r.phone}</strong>
          <span>${r.status === "ok" ? `✓ ${r.dispatch_id || "dispatched"}` : `✗ ${r.message || "failed"}`}</span>
        </div>
      `).join("");
    }
    showToast(`Campaign: ${ok}/${results.length} dispatched`, ok === results.length ? "success" : "info");
    if (res.campaign_id) {
      campaignState.currentCampaignId = res.campaign_id;
      await loadCampaignDetail(res.campaign_id, { scroll: true });
      await loadCampaignHistory();
      startCampaignPolling(res.campaign_id);
    }
  } catch (e) {
    if (statusEl) statusEl.textContent = "";
    showToast(e.message || "Campaign dispatch failed", "error");
  } finally {
    if (btn) btn.disabled = false;
  }
}

function setupCampaigns() {
  const dropzone = document.getElementById("campaign-dropzone");
  const fileInput = document.getElementById("campaign-csv-input");
  const numbersEl = document.getElementById("campaign-numbers");

  document.getElementById("campaign-dispatch-btn")?.addEventListener("click", dispatchCampaign);

  dropzone?.addEventListener("click", (e) => {
    if (e.target.closest(".campaign-browse-link, .campaign-file-input")) return;
    fileInput?.click();
  });

  fileInput?.addEventListener("change", async () => {
    const file = fileInput.files?.[0];
    if (file) await handleCampaignFile(file);
    fileInput.value = "";
  });

  dropzone?.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropzone.classList.add("is-dragover");
  });

  dropzone?.addEventListener("dragleave", () => {
    dropzone.classList.remove("is-dragover");
  });

  dropzone?.addEventListener("drop", async (e) => {
    e.preventDefault();
    dropzone.classList.remove("is-dragover");
    const file = e.dataTransfer?.files?.[0];
    if (file) await handleCampaignFile(file);
  });

  numbersEl?.addEventListener("input", updateCampaignStats);
  document.getElementById("campaign-delay")?.addEventListener("input", updateCampaignStats);

  document.getElementById("campaign-clear-btn")?.addEventListener("click", () => {
    if (numbersEl) numbersEl.value = "";
    campaignState.fileName = "";
    const meta = document.getElementById("campaign-upload-meta");
    if (meta) meta.hidden = true;
    updateCampaignStats();
  });

  document.getElementById("campaign-clear-file")?.addEventListener("click", () => {
    campaignState.fileName = "";
    const meta = document.getElementById("campaign-upload-meta");
    if (meta) meta.hidden = true;
  });

  document.getElementById("campaign-download-not-lifted")?.addEventListener("click", downloadNotLiftedCsv);
  document.getElementById("campaign-refresh-not-lifted")?.addEventListener("click", () => {
    if (campaignState.currentCampaignId) {
      loadCampaignDetail(campaignState.currentCampaignId);
      loadCampaignHistory();
    }
  });

  document.getElementById("campaign-history-body")?.addEventListener("click", async (e) => {
    const btn = e.target.closest("button[data-campaign-id]");
    if (!btn) return;
    const campaignId = btn.getAttribute("data-campaign-id");
    if (!campaignId) return;
    campaignState.currentCampaignId = campaignId;
    await loadCampaignDetail(campaignId, { scroll: true });
    startCampaignPolling(campaignId);
  });
}

function renderAll() {
  renderOverview();
  renderCallLogs();
  renderCRM();
  renderCrmSiteVisits();
  renderAnalytics();
  renderCalendar();
  if (state.currentPage === "assistants") renderAssistants();
}

/* ── Data bootstrap ── */

async function bootstrapData() {
  const fetchJson = async (url) => {
    try {
      const res = await apiFetch(url);
      if (res.status === 401) return null;
      return await res.json();
    } catch {
      return null;
    }
  };

  const payload = await fetchJson("/api/bootstrap");
  if (payload && typeof payload === "object" && !Array.isArray(payload)) {
    state.logs = Array.isArray(payload.logs) ? payload.logs : [];
    state.contacts = Array.isArray(payload.contacts) ? payload.contacts : [];
    state.bookings = Array.isArray(payload.bookings) ? payload.bookings : [];
    state.siteVisits = Array.isArray(payload.site_visits) ? payload.site_visits : [];
    state.analytics = payload.analytics || {};
  } else {
    const [logs, contacts, bookings, analytics] = await Promise.all([
      fetchJson("/api/logs"),
      fetchJson("/api/contacts"),
      fetchJson("/api/bookings"),
      fetchJson("/api/analytics"),
    ]);
    state.logs = Array.isArray(logs) ? logs : [];
    state.contacts = Array.isArray(contacts) ? contacts : [];
    state.bookings = Array.isArray(bookings) ? bookings : [];
    state.analytics = analytics || {};
  }
  renderAll();
}

/* ── Shell setup ── */

const NAV_LUCIDE_ICONS = {
  overview: "house",
  leads: "users",
  "hot-leads": "flame",
  performance: "trending-up",
  "listing-performance": "list",
  reports: "file-text",
  assistants: "mic",
  "call-logs": "phone",
  analytics: "chart-column",
  calendar: "calendar",
  campaigns: "megaphone",
  settings: "settings",
  crm: "activity",
  knowledge: "book-open",
  integrations: "plug",
  billing: "credit-card",
};

function renderLucideIcons(root) {
  const lucide = window.lucide;
  if (!lucide?.createIcons || !lucide?.icons) {
    console.warn("[Speaksy] Lucide failed to load — check /static/vendor/lucide/lucide.min.js in Network tab");
    return false;
  }
  const options = {
    icons: lucide.icons,
    attrs: {
      "stroke-width": 1.75,
      width: 18,
      height: 18,
    },
  };
  if (root) options.root = root;
  try {
    lucide.createIcons(options);
    return true;
  } catch (err) {
    console.error("[Speaksy] Lucide createIcons failed:", err);
    return false;
  }
}

function injectNavIcons() {
  document.querySelectorAll("#nav .nav-btn[data-page], .bottom-nav-item[data-page], #bottom-nav-more").forEach((btn) => {
    const page = btn.dataset.page;
    const label = btn.querySelector(".nav-label") || [...btn.querySelectorAll("span")].find((el) => !el.classList.contains("nav-icon") && !el.classList.contains("bottom-nav-icon"));
    if (label?.textContent && !btn.getAttribute("title")) {
      btn.setAttribute("title", label.textContent.trim());
    }
    const iconEl = btn.querySelector(".nav-icon, .bottom-nav-icon");
    if (!iconEl) return;
    const iconName = (page && NAV_LUCIDE_ICONS[page]) || (btn.id === "bottom-nav-more" ? "menu" : null);
    if (iconName) iconEl.dataset.lucide = iconName;
  });
  renderLucideIcons();
}

function syncSidebarToggleState() {
  const shell = document.getElementById("app-shell");
  const toggle = document.getElementById("sidebar-toggle");
  if (!shell || !toggle) return;
  const collapsed = shell.classList.contains("sidebar-collapsed");
  toggle.setAttribute("aria-expanded", collapsed ? "false" : "true");
  toggle.setAttribute("aria-label", collapsed ? "Expand sidebar" : "Collapse sidebar");
}

function setupSidebar() {
  const shell = document.getElementById("app-shell");
  const sidebar = document.getElementById("sidebar");
  const backdrop = document.getElementById("sidebar-backdrop");
  const toggle = document.getElementById("sidebar-toggle");
  const closeBtn = document.getElementById("sidebar-close");

  const isMobile = () => window.innerWidth <= 900;

  function openMobileSidebar() {
    sidebar.classList.add("open");
    backdrop.hidden = false;
  }

  function closeMobileSidebar() {
    sidebar.classList.remove("open");
    backdrop.hidden = true;
  }

  toggle?.addEventListener("click", () => {
    if (isMobile()) {
      sidebar.classList.contains("open") ? closeMobileSidebar() : openMobileSidebar();
    } else {
      shell.classList.toggle("sidebar-collapsed");
      localStorage.setItem("speaksy-sidebar-collapsed", shell.classList.contains("sidebar-collapsed") ? "1" : "0");
      syncSidebarToggleState();
      renderLucideIcons(document.getElementById("nav"));
    }
  });

  closeBtn?.addEventListener("click", closeMobileSidebar);
  backdrop?.addEventListener("click", closeMobileSidebar);

  if (!isMobile() && localStorage.getItem("speaksy-sidebar-collapsed") === "1") {
    shell.classList.add("sidebar-collapsed");
  }
  syncSidebarToggleState();

  window.addEventListener("resize", () => {
    if (!isMobile()) closeMobileSidebar();
  });
}

function setupNavigation() {
  const handleNavClick = (e, selector) => {
    const btn = e.target.closest(selector);
    if (!btn?.dataset.page) return;
    setPage(btn.dataset.page);
    if (window.innerWidth <= 900) {
      document.getElementById("sidebar")?.classList.remove("open");
      document.getElementById("sidebar-backdrop").hidden = true;
    }
  };

  document.getElementById("nav")?.addEventListener("click", (e) => handleNavClick(e, ".nav-item"));
  document.getElementById("bottom-nav")?.addEventListener("click", (e) => handleNavClick(e, ".bottom-nav-item"));

  document.getElementById("bottom-nav-more")?.addEventListener("click", () => {
    const sidebar = document.getElementById("sidebar");
    const backdrop = document.getElementById("sidebar-backdrop");
    if (!sidebar || !backdrop) return;
    sidebar.classList.add("open");
    backdrop.hidden = false;
  });

  document.getElementById("agent-back-btn")?.addEventListener("click", () => setPage("assistants"));

  document.getElementById("agent-tabs")?.addEventListener("click", (e) => {
    const tab = e.target.closest(".tab");
    if (!tab?.dataset.tab) return;
    setAgentTab(tab.dataset.tab);
  });

  document.getElementById("refresh-btn")?.addEventListener("click", async () => {
    try {
      await bootstrapData();
      showToast("Dashboard refreshed", "success");
    } catch (e) {
      showToast(e.message || "Refresh failed", "error");
    }
  });
}

function filterCRM(query) {
  const q = String(query || "").trim().toLowerCase();
  if (!q) return renderCRM();
  const matched = state.contacts.filter((c) => {
    const name = String(displayCallerName(c.caller_name, c.phone_number) || "").toLowerCase();
    const phone = String(c.phone_number || "").toLowerCase();
    return name.includes(q) || phone.includes(q);
  });
  renderCRM(matched);
}

function filterAssistantsGrid(query) {
  const q = String(query || "").trim().toLowerCase();
  document.querySelectorAll("#assistants-grid .assistant-card, #assistants-grid .agent-row").forEach((card) => {
    const text = (card.textContent || "").toLowerCase();
    card.style.display = !q || text.includes(q) ? "" : "none";
  });
}

function setupCrmViewFilters() {
  document.querySelectorAll("#crm-view-filters .crm-view-chip").forEach((chip) => {
    chip.addEventListener("click", () => setCrmView(chip.dataset.crmView));
  });
  document.querySelectorAll("#crm-site-visit-tabs .crm-site-visit-tab").forEach((tab) => {
    tab.addEventListener("click", () => setCrmSiteVisitTab(tab.dataset.svTab));
  });
}

function setupSearch() {
  setupCrmViewFilters();

  document.getElementById("crm-search")?.addEventListener("input", (e) => {
    filterCRM(e.target.value);
  });

  document.getElementById("global-search")?.addEventListener("input", (e) => {
    const q = e.target.value;
    const page = state.currentPage;

    if (page === "crm") {
      const crmInput = document.getElementById("crm-search");
      if (crmInput) crmInput.value = q;
      filterCRM(q);
      return;
    }

    if (page === "call-logs") {
      const phoneFilter = document.getElementById("cl-filter-phone");
      if (phoneFilter) phoneFilter.value = q;
      return;
    }

    if (page === "assistants" || page === "agent-detail") {
      filterAssistantsGrid(q);
      return;
    }

    if (q.trim()) {
      setPage("crm");
      const crmInput = document.getElementById("crm-search");
      if (crmInput) crmInput.value = q;
      filterCRM(q);
    }
  });
}

function setupQuickLinks() {
  document.querySelectorAll(".quick-link-btn, .settings-card").forEach((el) => {
    el.addEventListener("click", () => {
      const page = el.dataset.page;
      if (page) setPage(page);
    });
  });
}

function setupCRMClicks() {
  document.getElementById("crm-body")?.addEventListener("click", async (e) => {
    const row = e.target.closest("tr.crm-row");
    if (!row) return;
    await loadSiteVisitProperties();
    renderCRMDetails(row.dataset.phone || "");
  });
}

const THEME_ICONS = {
  dark: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="12" cy="12" r="5"/><path d="M12 1v2M12 21v2M4.22 4.22l1.42 1.42M18.36 18.36l1.42 1.42M1 12h2M21 12h2M4.22 19.78l1.42-1.42M18.36 5.64l1.42-1.42"/></svg>`,
  light: `<svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>`,
};

function getTheme() {
  return document.documentElement.getAttribute("data-theme") || "light";
}

function setTheme(mode) {
  document.documentElement.setAttribute("data-theme", mode);
  localStorage.setItem("speaksy-theme", mode);
  const icon = document.getElementById("theme-icon");
  if (icon) icon.innerHTML = THEME_ICONS[mode] || THEME_ICONS.light;
}

function setupTheme() {
  const saved = localStorage.getItem("speaksy-theme");
  const initial = saved === "light" || saved === "dark" ? saved : "light";
  setTheme(initial);
  document.getElementById("theme-toggle")?.addEventListener("click", () => {
    setTheme(getTheme() === "dark" ? "light" : "dark");
  });
}

function setupModals() {
  document.querySelectorAll("[data-close-modal]").forEach((el) => {
    el.addEventListener("click", closeAllModals);
  });

  document.getElementById("login-form")?.addEventListener("submit", async (e) => {
    e.preventDefault();
    const fd = new FormData(e.target);
    try {
      const res = await fetch("/api/auth/login", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          username: fd.get("username"),
          password: fd.get("password"),
        }),
      });
      if (!res.ok) throw new Error("Invalid credentials");
      const data = await res.json();
      setAuthToken(data.access_token);
      closeModal("modal-login");
      showToast("Signed in successfully", "success");
      await bootstrapData();
      await loadAssistants();
    } catch (err) {
      showToast(err.message || "Login failed", "error");
    }
  });
}

/* Demo: open sample agent detail from console — openAgentDetail('a0000000-0000-4000-8000-000000000001', 'MGM Admissions Counselor', 'admissions') */
window.openAgentDetail = openAgentDetail;
window.apiJson = apiJson;
window.apiFetch = apiFetch;
window.showToast = showToast;
window.state = state;
window.setPage = setPage;
window.displayCallerName = displayCallerName;
window.displayPhone = displayPhone;
window.renderCRMDetails = renderCRMDetails;
window.parseAppointmentDate = parseAppointmentDate;
window.formatDateKeyIST = formatDateKeyIST;
window.appointmentDateKeyIST = appointmentDateKeyIST;
window.safeAppointmentDate = safeAppointmentDate;
window.safeAppointmentTime = safeAppointmentTime;

function bootNavIcons() {
  injectNavIcons();
  if (!document.querySelector("#nav svg, #nav .nav-icon svg, .bottom-nav svg")) {
    renderLucideIcons();
  }
}

window.addEventListener("DOMContentLoaded", async () => {
  if (!getAuthToken()) {
    redirectToLogin();
    return;
  }

  bootNavIcons();
  if (!window.lucide?.createIcons) {
    window.setTimeout(bootNavIcons, 250);
    window.setTimeout(bootNavIcons, 1000);
  }
  setupTheme();
  setupSidebar();
  setupNavigation();
  setupSearch();
  setupQuickLinks();
  setupCRMClicks();
  setupModals();
  setupAssistants();
  setupSettings();
  setupTestCall();
  setupMakeCall();
  setupCampaigns();
  window.Integrations?.init();
  const waChatId = window.location.pathname.match(/\/whatsapp\/chat\/([^/]+)/)?.[1];
  if (waChatId && window.WhatsAppChat?.open) {
    await window.WhatsAppChat.open(decodeURIComponent(waChatId));
  } else {
    setPage("overview", { skipLoader: true });
  }
  document.getElementById("logout-btn")?.addEventListener("click", logout);

  try {
    await loadAuthProfile();
    await bootstrapData();
    if (getAuthToken()) await loadAssistants();
  } catch (e) {
    console.error("App initialization failed:", e);
  }

  let polling = false;
  const refreshIfVisible = async () => {
    if (polling || document.visibilityState !== "visible") return;
    polling = true;
    try { await bootstrapData(); } catch {}
    polling = false;
  };
  setInterval(refreshIfVisible, 120000);
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") refreshIfVisible();
  });
});