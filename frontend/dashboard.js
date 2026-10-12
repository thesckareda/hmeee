/**
 * Speaksy unified dashboard — Home, Leads, Hot Leads, Performance, Listings, Reports, Settings
 */
(function () {
  const state = {
    summary: null,
    performance: null,
    campaigns: null,
    leads: { items: [], page: 1, total_pages: 1, total: 0 },
    hotLeads: [],
    weeklyReport: null,
    monthlyReport: null,
    settingsTab: "settings-main-agent",
    leadsSearch: "",
    leadsOutcome: "",
  };

  const OUTCOME_BADGE = {
    booked: "badge-booked",
    interested: "badge-interested",
    follow_up: "badge-follow-up",
    not_interested: "badge-not-interested",
    unknown: "badge-muted",
  };

  function formatINR(value) {
    const n = Number(value) || 0;
    if (n >= 10000000) return `₹${(n / 10000000).toFixed(1)}Cr`;
    if (n >= 100000) return `₹${(n / 100000).toFixed(1)}L`;
    return `₹${n.toLocaleString("en-IN")}`;
  }

  function initials(name) {
    const parts = String(name || "?").trim().split(/\s+/).filter(Boolean);
    if (!parts.length) return "?";
    return parts.slice(0, 2).map((p) => p[0]).join("").toUpperCase();
  }

  function timeAgo(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "—";
    const diff = Date.now() - d.getTime();
    const minutes = Math.floor(diff / 60000);
    if (minutes < 1) return "just now";
    if (minutes < 60) return `${minutes}m ago`;
    const hours = Math.floor(diff / 3600000);
    if (hours < 24) return `${hours}h ago`;
    const days = Math.round(hours / 24);
    if (days < 7) return `${days} day${days === 1 ? "" : "s"} ago`;
    const weeks = Math.round(days / 7);
    if (weeks < 5) return `${weeks} week${weeks === 1 ? "" : "s"} ago`;
    const months = Math.round(days / 30);
    return `${months} month${months === 1 ? "" : "s"} ago`;
  }

  window.timeAgo = timeAgo;

  function navigateTo(page) {
    if (typeof window.setPage === "function") window.setPage(page);
  }
  window.navigateTo = navigateTo;

  function callerName(name, phone) {
    if (typeof window.displayCallerName === "function") {
      return window.displayCallerName(name, phone);
    }
    return name || phone || "Unknown";
  }

  function callerPhone(phone) {
    if (typeof window.displayPhone === "function") return window.displayPhone(phone);
    return phone || "—";
  }

  function isJunkSummary(text) {
    const t = String(text || "").toLowerCase();
    return t.includes("recovered minimal log") || t.includes("shutdown hook");
  }

  function filterPreviewLeads(leads) {
    return (leads || []).filter((l) => !isJunkSummary(l.intent_summary || l.what_they_want));
  }

  function outcomeBadge(outcome, label) {
    const cls = OUTCOME_BADGE[outcome] || "badge-muted";
    return `<span class="badge outcome-tag ${cls}" data-outcome="${outcome || "unknown"}">${label || outcome}</span>`;
  }

  function leadActionIcon(variant) {
    const icons = {
      listing: '<svg class="lead-action-svg" viewBox="0 0 16 16" aria-hidden="true"><path d="M4 2.5h5.2L12 5.3v8.2H4V2.5z" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linejoin="round"/><path d="M9.2 2.5V5H12" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round"/></svg>',
      visit: '<svg class="lead-action-svg" viewBox="0 0 16 16" aria-hidden="true"><rect x="2.5" y="3.5" width="11" height="10" rx="1.5" fill="none" stroke="currentColor" stroke-width="1.35"/><path d="M5.5 2v2M10.5 2v2M2.5 6.5h11" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round"/></svg>',
      booked: '<svg class="lead-action-svg" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.35"/><path d="M5.6 8.1l1.7 1.7 3.2-3.4" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/></svg>',
      escalate: '<svg class="lead-action-svg" viewBox="0 0 16 16" aria-hidden="true"><path d="M8 11.5V4.5M8 4.5L5.5 7M8 4.5l2.5 2.5" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round" stroke-linejoin="round"/><path d="M3.5 12.5h9" fill="none" stroke="currentColor" stroke-width="1.35" stroke-linecap="round"/></svg>',
      play: '<svg class="lead-action-svg" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.35"/><path d="M6.6 5.8v4.4l3.8-2.2-3.8-2.2z" fill="currentColor"/></svg>',
    };
    return icons[variant] || "";
  }

  function renderLeadActionGroup(lead, labels = {}) {
    const id = lead.id;
    const recUrl = (lead.recording_url || "").trim();
    const phone = (lead.phone_number || "").trim();
    const clientMode = window.VK_CLIENT_MODE !== false;
    const listing = labels.listing || (clientMode ? "WhatsApp" : "Listing");
    const visit = labels.visit || (clientMode ? "Site visit" : "Visit");
    const booked = labels.booked || (clientMode ? "Booked" : "Booked");
    const escalate = labels.escalate || (clientMode ? "Call team" : "Escalate");

    const btn = (action, label, variant, extra = "", speaksy = false) => `
      <button type="button" class="lead-action-btn lead-action-btn--${variant}${speaksy ? " btn-speaksy btn-speaksy--soft" : ""}" data-action="${action}" data-id="${id}"${extra}>
        ${leadActionIcon(variant)}
        <span class="lead-action-label">${label}</span>
      </button>
    `;

    const parts = [];

    if (clientMode && phone && phone !== "—") {
      const tel = phone.startsWith("+") ? phone : `+${phone.replace(/\D/g, "")}`;
      parts.push(`
        <a class="lead-action-btn lead-action-btn--call" href="tel:${tel}" title="Call back">
          <span class="lead-action-label">Call back</span>
        </a>
      `);
    }

    if (recUrl || id) {
      parts.push(`
        <button type="button" class="lead-action-btn lead-action-btn--play" data-action="play_recording" data-id="${id}" data-url="${recUrl}" title="Listen to call">
          ${leadActionIcon("play")}
          <span class="lead-action-label">${clientMode ? "Listen" : "Play"}</span>
        </button>
      `);
    }

    parts.push(
      btn("send_whatsapp", listing, "listing"),
      btn("schedule_site_visit", visit, "visit"),
      btn("mark_booked", booked, "booked", "", true),
      btn("escalate_human", escalate, "escalate"),
    );

    return `<div class="lead-action-group" role="group" aria-label="Lead actions">${parts.join("")}</div>`;
  }

  function urgencyBadge(label, score) {
    const cls = score >= 80 ? "badge-hot" : score >= 60 ? "badge-follow-up" : "badge-muted";
    return `<span class="badge ${cls}">${label}</span>`;
  }

  /* ── Home / Today's Summary ── */

  function renderOverviewAlerts(alerts) {
    const el = document.getElementById("overview-alerts");
    if (!el) return;
    if (!alerts?.length) {
      el.innerHTML = "";
      return;
    }
    el.innerHTML = alerts.map((a) => `
      <div class="alert-banner alert-hot" role="alert">
        <span>${a.message}</span>
        <button type="button" class="btn btn-sm btn-ghost" onclick="navigateTo('hot-leads')">View leads</button>
      </div>
    `).join("");
  }

  function renderOverviewKPIs(data) {
    const today = data?.today || {};
    const set = (id, val) => {
      const el = document.getElementById(id);
      if (el) el.textContent = val;
    };
    set("kpi-today-calls-val", today.total_calls ?? "—");
    set("kpi-week-calls-val", `${today.week_calls ?? 0} this week`);
    set("kpi-successful-val", today.successful_conversations ?? "—");
    set("kpi-conversion-val", `${today.conversion_rate ?? 0}%`);
    set("kpi-money-val", formatINR(today.money_impact_inr));
    const topAgent = today.top_agent;
    set(
      "kpi-top-agent-val",
      topAgent ? `Top: ${topAgent.name} · ${topAgent.conversions} conv.` : "No agent data",
    );
  }

  function drawHourlyChart(analytics, canvasId = "overview-hourly-chart") {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    const wrap = canvas.parentElement;
    const cssW = wrap?.clientWidth || canvas.offsetWidth || 320;
    const cssH = 180;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.floor(cssW * dpr);
    canvas.height = Math.floor(cssH * dpr);
    canvas.style.width = `${cssW}px`;
    canvas.style.height = `${cssH}px`;

    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, cssW, cssH);

    const series = analytics?.hourly_series || [];
    const total = series.reduce((s, p) => s + (p.calls || 0), 0);
    const green = getComputedStyle(document.documentElement).getPropertyValue("--speaksy-green").trim() || "#63dcb4";
    const faint = getComputedStyle(document.documentElement).getPropertyValue("--color-text-faint").trim() || "rgba(255,255,255,0.35)";
    const padL = 8;
    const padR = 8;
    const padT = 12;
    const padB = 28;
    const chartW = cssW - padL - padR;
    const chartH = cssH - padT - padB;

    if (!total) {
      ctx.fillStyle = faint;
      ctx.font = "13px Geist, -apple-system, sans-serif";
      ctx.textAlign = "center";
      ctx.fillText("No calls yet today", cssW / 2, cssH / 2);
      return;
    }

    const max = Math.max(1, ...series.map((s) => s.calls || 0));
    const barW = chartW / 24;

    ctx.strokeStyle = "rgba(255,255,255,0.06)";
    ctx.lineWidth = 1;
    for (let i = 0; i <= 3; i++) {
      const y = padT + (chartH / 3) * i;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(cssW - padR, y);
      ctx.stroke();
    }

    series.forEach((point, i) => {
      const val = point.calls || 0;
      const barH = Math.max(val ? 4 : 0, (val / max) * chartH);
      const x = padL + i * barW + 1;
      const y = padT + chartH - barH;
      const w = Math.max(2, barW - 2);
      const barGradient = ctx.createLinearGradient(x, y, x, y + barH);
      barGradient.addColorStop(0, "#0e2410");
      barGradient.addColorStop(0.22, "#163d18");
      barGradient.addColorStop(0.45, "#245c28");
      barGradient.addColorStop(0.65, "#397e39");
      barGradient.addColorStop(0.82, "#4c9349");
      barGradient.addColorStop(1, "#5aa652");
      ctx.fillStyle = val ? barGradient : green;
      ctx.globalAlpha = val ? 0.9 : 0.15;
      roundRect(ctx, x, y, w, barH, 2);
      ctx.fill();
    });
    ctx.globalAlpha = 1;

    ctx.fillStyle = faint;
    ctx.font = "10px Geist Mono, monospace";
    ctx.textAlign = "center";
    [0, 4, 8, 12, 16, 20].forEach((hour) => {
      const x = padL + hour * barW + barW / 2;
      const label = hour === 0 ? "12a" : hour < 12 ? `${hour}a` : hour === 12 ? "12p" : `${hour - 12}p`;
      ctx.fillText(label, x, cssH - 8);
    });
  }

  function roundRect(ctx, x, y, w, h, r) {
    if (h < 2) return;
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y);
    ctx.quadraticCurveTo(x + w, y, x + w, y + r);
    ctx.lineTo(x + w, y + h);
    ctx.lineTo(x, y + h);
    ctx.lineTo(x, y + r);
    ctx.quadraticCurveTo(x, y, x + r, y);
    ctx.closePath();
  }

  function renderAgentStatusList(agents) {
    const el = document.getElementById("agent-status-list");
    if (!el) return;
    const list = agents || [];
    if (window.VK_CLIENT_MODE !== false) {
      const active = list.some((a) => a.is_active);
      el.innerHTML = `
        <div class="agent-status-row">
          <span class="status-dot ${active ? "" : "offline"}"></span>
          <span class="agent-status-name">Arjun</span>
          <span class="agent-status-state">${active ? "Answering calls" : "Paused — contact OUTPERO"}</span>
        </div>
      `;
      return;
    }
    if (!list.length) {
      el.innerHTML = `<p class="muted">No assistants configured</p>`;
      return;
    }
    el.innerHTML = list.slice(0, 4).map((a) => `
      <div class="agent-status-row">
        <span class="status-dot ${a.is_active ? "" : "offline"}"></span>
        <span class="agent-status-name">${a.name || "Agent"}</span>
        <span class="agent-status-state">${a.is_active ? "Online" : "Paused"}</span>
      </div>
    `).join("");
  }

  function renderOverviewNeedsAction() {
    const el = document.getElementById("vk-needs-action-list");
    if (!el) return;
    const hot = filterPreviewLeads(state.hotLeads || []).slice(0, 5);
    if (!hot.length) {
      el.innerHTML = `<p class="muted vk-needs-empty">You're all caught up. New priority leads will show here after calls.</p>`;
      return;
    }
    el.innerHTML = hot
      .map((l) => {
        const phone = callerPhone(l.phone_number);
        const tel =
          l.phone_number && l.phone_number !== "—"
            ? `tel:${String(l.phone_number).startsWith("+") ? l.phone_number : "+" + String(l.phone_number).replace(/\D/g, "")}`
            : "";
        return `
          <div class="vk-needs-card">
            <div class="vk-needs-card-top">
              <strong>${callerName(l.caller_name, l.phone_number)}</strong>
              ${urgencyBadge(l.urgency_label, l.urgency_score)}
            </div>
            <p class="vk-needs-summary">${l.intent_summary || "Interested in property — follow up soon."}</p>
            <div class="vk-needs-meta">${phone} · ${l.last_call_date ? timeAgo(l.last_call_date) : `${l.hours_since_call || 0}h ago`}</div>
            <div class="vk-needs-actions">
              ${tel ? `<a class="btn btn-primary btn-sm btn-speaksy" href="${tel}">Call back</a>` : ""}
              <button type="button" class="btn btn-secondary btn-sm" onclick="navigateTo('hot-leads')">Details</button>
            </div>
          </div>
        `;
      })
      .join("");
  }

  function renderOverviewHotLeads(hotLeads) {
    const el = document.getElementById("overview-hot-leads-list");
    if (!el) return;
    const items = filterPreviewLeads(hotLeads).slice(0, 3);
    if (!items.length) {
      el.innerHTML = `<p class="empty-inline muted">No urgent follow-ups — Arjun will flag new priority leads here.</p>`;
      return;
    }
    el.innerHTML = items.map((l) => `
      <div class="mini-lead-item">
        <div class="mini-lead-top">
          <strong>${callerName(l.caller_name, l.phone_number)}</strong>
          ${urgencyBadge(l.urgency_label, l.urgency_score)}
        </div>
        <p class="mini-lead-summary">${l.intent_summary}</p>
        <span class="mini-lead-meta">${callerPhone(l.phone_number)} · ${l.last_call_date ? timeAgo(l.last_call_date) : `${l.hours_since_call || 0}h ago`}</span>
      </div>
    `).join("");
  }

  function renderOverviewRecentCalls(leads) {
    const el = document.getElementById("overview-recent-calls");
    if (!el) return;
    const items = (leads || []).slice(0, 5);
    if (!items.length) {
      el.innerHTML = `<p class="muted">When customers call, they'll appear here automatically.</p>`;
      return;
    }
    el.innerHTML = `
      <table class="table table-compact">
        <thead><tr><th>Contact</th><th>Outcome</th><th>When</th></tr></thead>
        <tbody>
          ${items.map((l) => `
            <tr>
              <td>
                <strong>${callerName(l.caller_name, l.phone_number)}</strong>
                <span class="cell-sub">${callerPhone(l.phone_number)}</span>
              </td>
              <td>${outcomeBadge(l.outcome, l.outcome_label)}</td>
              <td class="cell-meta">${timeAgo(l.last_call_date)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    `;
  }

  async function loadOverview() {
    try {
      const [summary, analytics, hot, agentsRes, leadsRes] = await Promise.all([
        window.apiJson("/api/analytics/summary"),
        window.apiJson("/api/analytics"),
        window.apiJson("/api/leads/hot"),
        window.apiJson("/api/agents"),
        window.apiJson("/api/leads?page=1&page_size=8"),
      ]);
      state.summary = summary;
      state.hotLeads = hot.hot_leads || [];

      renderOverviewAlerts(summary.alerts);
      renderOverviewKPIs(summary);
      const hotCount = (hot.hot_leads || []).length;
      const hotEl = document.getElementById("kpi-hot-leads-val");
      if (hotEl) hotEl.textContent = hotCount;

      const hero = document.getElementById("hero-status-text");
      if (hero) {
        const n = summary.today?.total_calls || 0;
        hero.textContent = n
          ? `Arjun handled ${n} call${n === 1 ? "" : "s"} today`
          : "Arjun is ready — calls will show up here automatically";
      }

      const dateEl = document.getElementById("overview-chart-date");
      if (dateEl) dateEl.textContent = new Date().toLocaleDateString("en-IN", { timeZone: "Asia/Kolkata" });

      drawHourlyChart(analytics);
      renderAgentStatusList(agentsRes.data || agentsRes);
      renderOverviewHotLeads(filterPreviewLeads(hot.hot_leads));
      renderOverviewRecentCalls(leadsRes.leads);
      renderOverviewNeedsAction();
    } catch (e) {
      console.error("loadOverview:", e);
    }
  }

  /* ── Leads & Calls ── */

  function leadRow(lead) {
    const name = callerName(lead.caller_name, lead.phone_number);
    return `
      <tr class="lead-row" data-id="${lead.id}">
        <td>
          <strong>${name}</strong>
          <span class="cell-sub">${callerPhone(lead.phone_number)}</span>
        </td>
        <td class="lead-want-cell">${lead.what_they_want}</td>
        <td>${outcomeBadge(lead.outcome, lead.outcome_label)}</td>
        <td class="cell-meta">${timeAgo(lead.last_call_date)}</td>
        <td class="lead-actions-cell">
          ${renderLeadActionGroup(lead)}
        </td>
      </tr>
    `;
  }

  function leadsTable(leads) {
    if (!leads.length) {
      return `<div class="empty-state card"><p class="muted">No leads match your search. Try clearing filters or check back after the next call.</p></div>`;
    }
    return `
      <div class="table-wrap">
        <table class="table leads-table">
          <thead>
            <tr>
              <th>Contact</th>
              <th>Intent</th>
              <th>Outcome</th>
              <th>When</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>${leads.map(leadRow).join("")}</tbody>
        </table>
      </div>
    `;
  }

  function renderLeadsPagination() {
    const el = document.getElementById("leads-pagination");
    if (!el) return;
    const { page, total_pages, total } = state.leads;
    el.innerHTML = `
      <span class="muted">${total} leads</span>
      <div class="pagination-controls">
        <button type="button" class="btn btn-ghost btn-sm" id="leads-prev" ${page <= 1 ? "disabled" : ""}>← Prev</button>
        <span>Page ${page} / ${total_pages}</span>
        <button type="button" class="btn btn-ghost btn-sm" id="leads-next" ${page >= total_pages ? "disabled" : ""}>Next →</button>
      </div>
    `;
    document.getElementById("leads-prev")?.addEventListener("click", () => loadLeads(page - 1));
    document.getElementById("leads-next")?.addEventListener("click", () => loadLeads(page + 1));
  }

  async function loadLeads(page = 1) {
    const params = new URLSearchParams({
      page: String(page),
      page_size: "20",
      search: state.leadsSearch,
      outcome: state.leadsOutcome,
    });
    try {
      const res = await window.apiJson(`/api/leads?${params}`);
      state.leads = {
        items: res.leads || [],
        page: res.page || 1,
        total_pages: res.total_pages || 1,
        total: res.total || 0,
      };
      const list = document.getElementById("leads-list");
      const label = document.getElementById("leads-count-label");
      if (label) label.textContent = `${state.leads.total} leads`;
      if (list) list.innerHTML = leadsTable(state.leads.items);
      renderLeadsPagination();
    } catch (e) {
      window.showToast?.(e.message || "Failed to load leads", "error");
    }
  }

  async function performLeadAction(callId, action) {
    try {
      const res = await window.apiJson(`/api/leads/${callId}/action`, {
        method: "POST",
        body: JSON.stringify({ action }),
      });
      window.showToast?.(res.message || "Action recorded", "success");
      await loadLeads(state.leads.page);
      if (state.currentPageNeedsRefresh?.()) await loadOverview();
    } catch (e) {
      window.showToast?.(e.message || "Action failed", "error");
    }
  }

  async function downloadAuthedCsv(url, filename) {
    try {
      const res = await window.apiFetch(url);
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const link = document.createElement("a");
      link.href = URL.createObjectURL(blob);
      link.download = filename;
      link.click();
      URL.revokeObjectURL(link.href);
      window.showToast?.("Export downloaded", "success");
    } catch (e) {
      window.showToast?.(e.message || "Export failed", "error");
    }
  }

  function setupLeads() {
    document.getElementById("leads-search")?.addEventListener("input", (e) => {
      state.leadsSearch = e.target.value;
      clearTimeout(state._leadsDebounce);
      state._leadsDebounce = setTimeout(() => loadLeads(1), 350);
    });
    document.getElementById("leads-outcome-filter")?.addEventListener("change", (e) => {
      state.leadsOutcome = e.target.value;
      loadLeads(1);
    });
    document.getElementById("leads-export-btn")?.addEventListener("click", () => {
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      downloadAuthedCsv("/api/reports/export/leads/csv", `leads_export_${stamp}.csv`);
    });
    document.getElementById("leads-list")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action]");
      if (!btn) return;
      const action = btn.dataset.action;
      const id = btn.dataset.id;
      if (action === "play_recording") {
        const callId = btn.dataset.id;
        const fallback = String(btn.dataset.url || "").trim();
        if (window.CallLogs?.playCallRecording) {
          window.CallLogs.playCallRecording(callId, fallback).catch(() => {
            if (fallback) window.open(fallback, "_blank");
          });
        } else if (fallback) {
          window.open(fallback, "_blank");
        }
        return;
      }
      if (id && action) performLeadAction(id, action);
    });
  }

  /* ── Hot Leads ── */

  function hotLeadCard(lead) {
    const junk = isJunkSummary(lead.intent_summary);
    return `
      <article class="hot-lead-card card ${junk ? "is-low-quality" : ""}">
        <div class="hot-lead-header">
          <div class="lead-avatar">${initials(callerName(lead.caller_name, lead.phone_number))}</div>
          <div class="hot-lead-info">
            <strong>${callerName(lead.caller_name, lead.phone_number)}</strong>
            <span class="cell-sub">${callerPhone(lead.phone_number)} · ${lead.last_call_date ? timeAgo(lead.last_call_date) : `${lead.hours_since_call || 0}h ago`}</span>
          </div>
          <div class="hot-lead-score">
            <span class="score-num">${lead.urgency_score}</span>
            ${urgencyBadge(lead.urgency_label, lead.urgency_score)}
          </div>
        </div>
        <p class="hot-intent">${lead.intent_summary}</p>
        <p class="hot-meta muted">${lead.suggested_action} · ${lead.agent_name}</p>
        <div class="hot-lead-actions">
          ${renderLeadActionGroup(lead, {
            listing: "Send listing",
            visit: "Site visit",
            booked: "Mark booked",
            escalate: "Escalate",
          })}
        </div>
      </article>
    `;
  }

  async function loadHotLeads() {
    try {
      const res = await window.apiJson("/api/leads/hot");
      state.hotLeads = res.hot_leads || [];
      const el = document.getElementById("hot-leads-list");
      const count = document.getElementById("hot-leads-count");
      if (count) count.textContent = `${state.hotLeads.length} need action`;
      if (el) {
        el.innerHTML = state.hotLeads.length
          ? state.hotLeads.map(hotLeadCard).join("")
          : `<div class="empty-state card"><p class="muted">No hot leads — check back after more calls.</p></div>`;
      }
    } catch (e) {
      window.showToast?.(e.message || "Failed to load hot leads", "error");
    }
  }

  function setupHotLeads() {
    document.getElementById("hot-leads-list")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-action][data-id]");
      if (!btn) return;
      performLeadAction(btn.dataset.id, btn.dataset.action);
    });
  }

  /* ── Performance ── */

  function drawConversionChart(chartData) {
    const canvas = document.getElementById("performance-conversion-chart");
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    const data = chartData || [];
    const w = canvas.width = canvas.offsetWidth || 480;
    const h = canvas.height = 200;
    ctx.clearRect(0, 0, w, h);
    if (!data.length) return;

    const max = Math.max(1, ...data.map((d) => Math.max(d.calls || 0, d.converted || 0)));
    const step = (w - 40) / data.length;
    const green = getComputedStyle(document.documentElement).getPropertyValue("--speaksy-green").trim() || "#63dcb4";

    data.forEach((point, i) => {
      const x = 20 + i * step + step / 4;
      const callsH = ((point.calls || 0) / max) * (h - 40);
      const convH = ((point.converted || 0) / max) * (h - 40);
      ctx.fillStyle = "rgba(255,255,255,0.12)";
      ctx.fillRect(x, h - 20 - callsH, step / 2 - 4, callsH);
      ctx.fillStyle = green;
      ctx.fillRect(x + step / 2, h - 20 - convH, step / 2 - 4, convH);
    });
  }

  async function loadPerformance() {
    try {
      const perf = await window.apiJson("/api/analytics/performance");
      state.performance = perf;
      const set = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.textContent = val;
      };
      set("perf-site-visits", perf.site_visits_this_month ?? 0);
      set("perf-offers", perf.offers_received_this_month ?? 0);
      set("perf-avg-duration", `${perf.avg_call_duration_seconds ?? 0}s`);
      set("perf-success-rate", `${perf.overall_success_rate ?? 0}%`);
      set("perf-best-days", (perf.best_days || []).join(", ") || "—");
      set("perf-best-hours", (perf.best_hours || []).map((h) => `${h}:00`).join(", ") || "—");
      drawConversionChart(perf.weekly_conversion_chart);

      const outcomesEl = document.getElementById("perf-outcomes-grid");
      const outcomesPanel = document.getElementById("perf-outcomes-panel");
      const outcomes = perf.outcomes || perf.outcome_breakdown || {};
      const outcomeRows = Object.entries(outcomes).filter(([, v]) => Number(v) > 0);
      if (outcomesEl && outcomeRows.length) {
        if (outcomesPanel) outcomesPanel.hidden = false;
        outcomesEl.innerHTML = outcomeRows.map(([key, val]) => `
          <div class="perf-outcome-cell">
            <span class="perf-outcome-label">${key.replace(/_/g, " ")}</span>
            <strong class="kpi-value">${val}</strong>
          </div>
        `).join("");
      } else if (outcomesPanel) {
        outcomesPanel.hidden = true;
      }
    } catch (e) {
      console.error("loadPerformance:", e);
    }
  }

  /* ── Listing / Campaign Performance ── */

  async function loadListingPerformance() {
    try {
      const data = await window.apiJson("/api/analytics/campaigns");
      state.campaigns = data;

      const listingsEl = document.getElementById("listing-performance-list");
      if (listingsEl) {
        const items = data.top_listings || [];
        listingsEl.innerHTML = items.length
          ? items.map((l) => `
            <div class="listing-row">
              <strong>${l.listing_id}</strong>
              <div class="progress-bar"><div class="progress-fill" style="width:${l.interest_score}%"></div></div>
              <span class="muted">${l.mentions} mentions</span>
            </div>
          `).join("")
          : `<p class="muted">No listing mentions yet</p>`;
      }

      const areasEl = document.getElementById("area-performance-list");
      if (areasEl) {
        const areas = data.top_areas || [];
        areasEl.innerHTML = areas.length
          ? areas.map((a) => `<div class="area-chip"><span>${a.area}</span><strong>${a.mentions}</strong></div>`).join("")
          : `<p class="muted">No area data yet</p>`;
      }

      const ab = data.ab_test || {};
      const abEl = document.getElementById("ab-test-results");
      if (abEl) {
        if (ab.tone_a && ab.tone_b) {
          abEl.innerHTML = `
            <div class="ab-card">
              <div><strong>${ab.tone_a.name}</strong><br><span class="kpi-value">${ab.tone_a.conversion_rate}%</span> (${ab.tone_a.calls} calls)</div>
              <div class="ab-vs">vs</div>
              <div><strong>${ab.tone_b.name}</strong><br><span class="kpi-value">${ab.tone_b.conversion_rate}%</span> (${ab.tone_b.calls} calls)</div>
            </div>
            <p class="muted ab-winner">${ab.winner} converts ${ab.delta_pct}% better</p>
          `;
        } else if (ab.tone_a) {
          abEl.innerHTML = `<p class="muted">Need more agents for A/B comparison. Top: ${ab.tone_a.name} at ${ab.tone_a.conversion_rate}%</p>`;
        } else {
          abEl.innerHTML = `<p class="muted">A/B data will appear once you have multiple active agents.</p>`;
        }
      }
    } catch (e) {
      console.error("loadListingPerformance:", e);
    }
  }

  /* ── Reports ── */

  async function loadReports() {
    try {
      const [weekly, monthly] = await Promise.all([
        window.apiJson("/api/reports/weekly-summary"),
        window.apiJson("/api/reports/monthly-summary"),
      ]);
      state.weeklyReport = weekly;
      state.monthlyReport = monthly;

      const wEl = document.getElementById("weekly-report-text");
      const mEl = document.getElementById("monthly-report-text");
      if (wEl) wEl.textContent = weekly.summary_text || "No weekly data.";
      if (mEl) mEl.textContent = monthly.summary_text || "No monthly data.";

      const wKpi = document.getElementById("weekly-report-kpis");
      if (wKpi && weekly.kpis) {
        const k = weekly.kpis;
        wKpi.innerHTML = `
          <div class="metric-cell"><span class="section-label">Calls</span><strong class="metric-value">${k.total_calls ?? 0}</strong></div>
          <div class="metric-cell"><span class="section-label">Successful</span><strong class="metric-value">${k.successful ?? 0}</strong></div>
          <div class="metric-cell"><span class="section-label">Conversion</span><strong class="metric-value">${k.conversion_rate ?? 0}%</strong></div>
          <div class="metric-cell"><span class="section-label">Follow-ups</span><strong class="metric-value">${weekly.pending_followups ?? 0}</strong></div>
        `;
      }

      const mKpi = document.getElementById("monthly-report-kpis");
      if (mKpi && monthly.kpis) {
        const k = monthly.kpis;
        mKpi.innerHTML = `
          <div class="metric-cell"><span class="section-label">Calls</span><strong class="metric-value">${k.total_calls ?? 0}</strong></div>
          <div class="metric-cell"><span class="section-label">Revenue</span><strong class="metric-value">${formatINR(k.estimated_revenue_inr)}</strong></div>
          <div class="metric-cell"><span class="section-label">Site visits</span><strong class="metric-value">${k.site_visits ?? 0}</strong></div>
          <div class="metric-cell"><span class="section-label">Offers</span><strong class="metric-value">${k.offers ?? 0}</strong></div>
        `;
      }
    } catch (e) {
      console.error("loadReports:", e);
    }
  }

  function buildPrintableReport() {
    const weekly = state.weeklyReport || {};
    const monthly = state.monthlyReport || {};
    const wk = weekly.kpis || {};
    const mo = monthly.kpis || {};
    return `
      <div id="vk-print-report" style="font-family:system-ui,sans-serif;padding:32px;color:#111;">
        <h1 style="margin:0 0 8px;font-size:24px;">VK Realty — Performance Report</h1>
        <p style="margin:0 0 24px;color:#555;">Generated ${new Date().toLocaleString("en-IN")}</p>
        <h2 style="font-size:18px;color:#2563eb;">Weekly Summary</h2>
        <p>${weekly.summary_text || "No weekly data."}</p>
        <ul>
          <li>Calls: ${wk.total_calls ?? 0}</li>
          <li>Successful: ${wk.successful ?? 0}</li>
          <li>Conversion: ${wk.conversion_rate ?? 0}%</li>
          <li>Follow-ups pending: ${weekly.pending_followups ?? 0}</li>
        </ul>
        <h2 style="font-size:18px;color:#047857;margin-top:28px;">Monthly Performance</h2>
        <p>${monthly.summary_text || "No monthly data."}</p>
        <ul>
          <li>Calls: ${mo.total_calls ?? 0}</li>
          <li>Revenue: ${formatINR(mo.estimated_revenue_inr)}</li>
          <li>Site visits: ${mo.site_visits ?? 0}</li>
          <li>Offers: ${mo.offers ?? 0}</li>
        </ul>
      </div>
    `;
  }

  function setupReports() {
    document.getElementById("export-leads-btn")?.addEventListener("click", () => {
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      downloadAuthedCsv("/api/reports/export/leads/csv", `leads_export_${stamp}.csv`);
    });
    document.getElementById("export-calls-btn")?.addEventListener("click", () => {
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      downloadAuthedCsv("/api/calls/export/csv", `calls_export_${stamp}.csv`);
    });
    document.getElementById("export-booked-calls-btn")?.addEventListener("click", () => {
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      downloadAuthedCsv("/api/calls/export/csv?was_booked=true", `booked_calls_${stamp}.csv`);
    });
    document.getElementById("export-missed-calls-btn")?.addEventListener("click", () => {
      const stamp = new Date().toISOString().slice(0, 10).replace(/-/g, "");
      downloadAuthedCsv("/api/calls/export/csv?call_status=no_answer", `missed_calls_${stamp}.csv`);
    });
    document.getElementById("send-weekly-email-btn")?.addEventListener("click", async () => {
      const email = document.getElementById("report-email")?.value || "";
      try {
        const res = await window.apiJson("/api/reports/send-weekly-email", {
          method: "POST",
          body: JSON.stringify({ email }),
        });
        window.showToast?.(res.message, res.success ? "success" : "info");
      } catch (e) {
        window.showToast?.(e.message || "Send failed", "error");
      }
    });
    document.getElementById("print-weekly-btn")?.addEventListener("click", () => {
      const w = window.open("", "_blank");
      if (!w) return;
      w.document.write(`<!DOCTYPE html><html><head><title>VK Realty Report</title></head><body>${buildPrintableReport()}</body></html>`);
      w.document.close();
      w.focus();
      w.print();
    });
  }

  /* ── API Keys panel ── */

  const API_SETTINGS_TOKEN_KEY = "speaksy-api-settings-token";

  function getApiSettingsToken() {
    return sessionStorage.getItem(API_SETTINGS_TOKEN_KEY);
  }

  function setApiSettingsToken(token) {
    if (token) sessionStorage.setItem(API_SETTINGS_TOKEN_KEY, token);
    else sessionStorage.removeItem(API_SETTINGS_TOKEN_KEY);
  }

  function showApiKeysLockScreen() {
    const lock = document.getElementById("api-keys-lock");
    const panel = document.getElementById("api-keys-panel");
    if (lock) lock.hidden = false;
    if (panel) panel.hidden = true;
    const pwd = document.getElementById("api-keys-password");
    if (pwd) pwd.value = "";
  }

  function showApiKeysUnlocked() {
    const lock = document.getElementById("api-keys-lock");
    const panel = document.getElementById("api-keys-panel");
    if (lock) lock.hidden = true;
    if (panel) panel.hidden = false;
  }

  async function apiSettingsJson(url, options = {}) {
    const token = getApiSettingsToken();
    const headers = { ...(options.headers || {}) };
    if (token) headers["X-API-Settings-Token"] = token;
    try {
      return await window.apiJson(url, { ...options, headers });
    } catch (err) {
      const msg = String(err.message || "");
      if (
        msg.includes("Unlock API settings") ||
        msg.includes("API settings session expired") ||
        msg.includes("API settings access required") ||
        msg.includes("Invalid API settings token")
      ) {
        setApiSettingsToken(null);
        showApiKeysLockScreen();
      }
      throw err;
    }
  }

  function renderApiKeyGroups(data) {
    const container = document.getElementById("api-keys-groups");
    if (!container) return;
    const groups = data?.groups || [];
    container.innerHTML = groups.map((group) => `
      <article class="api-keys-group card">
        <h4 class="api-keys-group-title">${group.label}</h4>
        <div class="api-keys-grid">
          ${(group.keys || []).map((key) => {
            const inputType = key.secret ? "password" : "text";
            const statusClass = key.is_set ? "is-set" : "";
            const statusText = key.is_set ? "Configured" : "Not set";
            return `
              <label class="api-key-field">
                <span class="api-key-field-label">
                  <span>${key.label}</span>
                  <code>${key.env}</code>
                </span>
                <div class="api-key-input-wrap">
                  <input
                    class="input api-key-input"
                    type="${inputType}"
                    data-env="${key.env}"
                    data-secret="${key.secret ? "1" : "0"}"
                    data-masked="${key.secret && key.value ? "1" : "0"}"
                    value="${(key.value || "").replace(/"/g, "&quot;")}"
                    placeholder="${key.secret ? "Enter secret value" : ""}"
                    autocomplete="off"
                  >
                  ${key.secret ? `<button type="button" class="btn btn-ghost btn-sm api-key-toggle" data-toggle-env="${key.env}">Show</button>` : ""}
                </div>
                <span class="api-key-status ${statusClass}">${statusText}</span>
              </label>
            `;
          }).join("")}
        </div>
      </article>
    `).join("");

    container.querySelectorAll(".api-key-input[data-masked='1']").forEach((input) => {
      input.addEventListener("focus", () => {
        if (input.dataset.masked === "1") {
          input.value = "";
          input.dataset.masked = "0";
        }
      }, { once: true });
    });

    container.querySelectorAll(".api-key-toggle").forEach((btn) => {
      btn.addEventListener("click", () => {
        const env = btn.dataset.toggleEnv;
        const input = container.querySelector(`.api-key-input[data-env="${env}"]`);
        if (!input) return;
        const showing = input.type === "text";
        input.type = showing ? "password" : "text";
        btn.textContent = showing ? "Show" : "Hide";
      });
    });
  }

  function renderVoiceConfigPanel(voiceConfig) {
    const cfg = voiceConfig || {};
    const set = (id, value) => {
      const el = document.getElementById(id);
      if (el) el.value = value || "";
    };
    set("voice-tts-id", cfg.tts_voice);
    set("voice-tts-display", cfg.tts_voice_display);
    set("voice-cartesia-model", cfg.cartesia_tts_model);
    set("voice-tts-provider", cfg.tts_provider);
    set("voice-tts-language", cfg.tts_language);
  }

  function collectVoiceConfig() {
    const val = (id) => document.getElementById(id)?.value?.trim() || "";
    return {
      tts_voice: val("voice-tts-id"),
      tts_voice_display: val("voice-tts-display"),
      cartesia_tts_model: val("voice-cartesia-model"),
      tts_provider: val("voice-tts-provider"),
      tts_language: val("voice-tts-language"),
    };
  }

  async function loadApiKeysPanel() {
    if (!getApiSettingsToken()) {
      showApiKeysLockScreen();
      return;
    }
    try {
      const data = await apiSettingsJson("/api/settings/api-keys");
      const envFile = document.getElementById("api-keys-env-file");
      if (envFile) envFile.textContent = data.env_file || ".env";
      renderVoiceConfigPanel(data.voice_config);
      renderApiKeyGroups(data);
      showApiKeysUnlocked();
    } catch (err) {
      if (!getApiSettingsToken()) return;
      window.showToast?.(err.message || "Failed to load API keys", "error");
    }
  }

  async function saveApiKeys() {
    const inputs = [...document.querySelectorAll("#api-keys-groups .api-key-input")];
    const keys = {};
    inputs.forEach((input) => {
      const env = input.dataset.env;
      if (!env) return;
      keys[env] = input.value;
    });
    const restartAgent = document.getElementById("api-keys-restart-agent")?.checked !== false;
    const btn = document.getElementById("api-keys-save-btn");
    const prev = btn?.textContent;
    if (btn) {
      btn.disabled = true;
      btn.textContent = restartAgent ? "Saving & restarting…" : "Saving…";
    }
    try {
      const res = await apiSettingsJson("/api/settings/api-keys", {
        method: "PUT",
        body: JSON.stringify({
          keys,
          restart_agent: restartAgent,
          voice_config: collectVoiceConfig(),
        }),
      });
      renderApiKeyGroups({ groups: res.groups });
      renderVoiceConfigPanel(res.voice_config);
      const keyCount = (res.updated_keys || []).length;
      const voiceCount = (res.updated_voice || []).length;
      const restarted = res.restart?.success;
      const msg = res.message
        || (restarted
          ? "Settings saved and agent restarted"
          : keyCount || voiceCount
            ? `Updated ${keyCount + voiceCount} setting(s)`
            : "No changes to save");
      window.showToast?.(msg, restarted || keyCount || voiceCount ? "success" : "info");
    } catch (err) {
      window.showToast?.(err.message || "Failed to save API keys", "error");
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.textContent = prev || "Save & apply";
      }
    }
  }

  /* ── Settings tabs ── */

  function setSettingsTab(tabId) {
    state.settingsTab = tabId;
    document.querySelectorAll("#settings-tabs .tab").forEach((t) => {
      t.classList.toggle("active", t.dataset.tab === tabId);
    });
    document.querySelectorAll(".tab-panel-settings").forEach((p) => {
      p.classList.toggle("active", p.id === tabId);
    });
    if (tabId === "settings-api-keys") loadApiKeysPanel();
    if (tabId === "settings-main-agent") window.AssistantSettings?.load?.();
  }

  async function loadSettingsExtras() {
    try {
      const [hours, notifications, tone, phones, agentsRes] = await Promise.all([
        window.apiJson("/api/settings/business-hours"),
        window.apiJson("/api/settings/notifications"),
        window.apiJson("/api/settings/tone"),
        window.apiJson("/api/settings/phone-numbers"),
        window.apiJson("/api/agents"),
      ]);

      document.getElementById("hours-open") && (document.getElementById("hours-open").value = hours.open_time || "09:00");
      document.getElementById("hours-close") && (document.getElementById("hours-close").value = hours.close_time || "20:00");
      document.getElementById("hours-timezone") && (document.getElementById("hours-timezone").value = hours.timezone || "Asia/Kolkata");
      document.querySelectorAll(".day-toggle input").forEach((cb) => {
        cb.checked = (hours.working_days || []).includes(cb.value);
      });

      const n = notifications;
      const setCheck = (id, val) => {
        const el = document.getElementById(id);
        if (el) el.checked = Boolean(val);
      };
      setCheck("notif-whatsapp", n.whatsapp_alerts);
      setCheck("notif-email", n.email_alerts);
      setCheck("notif-hot", n.alert_on_hot_lead);
      setCheck("notif-booking", n.alert_on_booking);
      const phoneIn = document.getElementById("notif-phone");
      if (phoneIn) phoneIn.value = n.alert_phone || "";
      const emailIn = document.getElementById("notif-email-addr");
      if (emailIn) emailIn.value = n.alert_email || "";

      document.querySelectorAll(".tone-card").forEach((card) => {
        card.classList.toggle("selected", card.dataset.tone === tone.current_tone);
      });

      const phonesEl = document.getElementById("settings-phone-list");
      if (phonesEl) {
        const nums = phones.phone_numbers || [];
        phonesEl.innerHTML = `
          <p><strong>SIP Trunk:</strong> ${phones.sip_trunk_id || "—"}</p>
          <p><strong>LiveKit Agent:</strong> ${phones.livekit_agent_name || "—"}</p>
          <p><strong>Transfer:</strong> ${phones.transfer_number || "—"}</p>
          <ul>${nums.map((p) => `<li>${p}</li>`).join("") || "<li class='muted'>No inbound numbers in env</li>"}</ul>
        `;
      }

      const agentsList = document.getElementById("settings-agents-list");
      const agents = agentsRes.data || agentsRes || [];
      if (agentsList && Array.isArray(agents)) {
        agentsList.innerHTML = agents.map((a) => `
          <div class="settings-agent-row card">
            <div>
              <strong>${a.name}</strong>
              <span class="muted">${a.purpose || "general"}</span>
            </div>
            <label class="toggle-switch">
              <input type="checkbox" data-agent-toggle="${a.id}" ${a.is_active ? "checked" : ""}>
              <span>${a.is_active ? "On" : "Off"}</span>
            </label>
            <button type="button" class="btn btn-ghost btn-sm" data-edit-agent="${a.id}">Configure</button>
          </div>
        `).join("");
      }
    } catch (e) {
      console.error("loadSettingsExtras:", e);
    }
  }

  function setupSettingsTabs() {
    document.getElementById("settings-tabs")?.addEventListener("click", (e) => {
      const tab = e.target.closest(".tab[data-tab]");
      if (!tab) return;
      setSettingsTab(tab.dataset.tab);
    });

    document.getElementById("hours-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const days = [...document.querySelectorAll(".day-toggle input:checked")].map((cb) => cb.value);
      try {
        await window.apiJson("/api/settings/business-hours", {
          method: "POST",
          body: JSON.stringify({
            timezone: document.getElementById("hours-timezone")?.value,
            open_time: document.getElementById("hours-open")?.value,
            close_time: document.getElementById("hours-close")?.value,
            working_days: days,
            holiday_dates: [],
          }),
        });
        window.showToast?.("Business hours saved", "success");
      } catch (err) {
        window.showToast?.(err.message, "error");
      }
    });

    document.getElementById("notifications-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      try {
        await window.apiJson("/api/settings/notifications", {
          method: "POST",
          body: JSON.stringify({
            whatsapp_alerts: document.getElementById("notif-whatsapp")?.checked,
            email_alerts: document.getElementById("notif-email")?.checked,
            alert_on_hot_lead: document.getElementById("notif-hot")?.checked,
            alert_on_booking: document.getElementById("notif-booking")?.checked,
            alert_phone: document.getElementById("notif-phone")?.value,
            alert_email: document.getElementById("notif-email-addr")?.value,
          }),
        });
        window.showToast?.("Notification preferences saved", "success");
      } catch (err) {
        window.showToast?.(err.message, "error");
      }
    });

    document.getElementById("tone-selector")?.addEventListener("click", async (e) => {
      const btn = e.target.closest(".tone-select-btn");
      const card = e.target.closest(".tone-card");
      if (!btn && !card) return;
      const tone = (card || btn.closest(".tone-card"))?.dataset.tone;
      if (!tone) return;
      try {
        const res = await window.apiJson("/api/settings/tone", {
          method: "POST",
          body: JSON.stringify({ tone }),
        });
        window.showToast?.(`Tone set to ${tone} (${res.agents_updated} agents updated)`, "success");
        document.querySelectorAll(".tone-card").forEach((c) => c.classList.toggle("selected", c.dataset.tone === tone));
      } catch (err) {
        window.showToast?.(err.message, "error");
      }
    });

    document.getElementById("settings-agents-list")?.addEventListener("change", async (e) => {
      const input = e.target.closest("[data-agent-toggle]");
      if (!input) return;
      const agentId = input.dataset.agentToggle;
      try {
        await window.apiJson(`/api/agents/${agentId}/toggle`, {
          method: "POST",
          body: JSON.stringify({ is_active: input.checked }),
        });
        window.showToast?.(input.checked ? "Agent enabled" : "Agent paused", "success");
      } catch (err) {
        input.checked = !input.checked;
        window.showToast?.(err.message, "error");
      }
    });

    document.getElementById("settings-agents-list")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-edit-agent]");
      if (!btn) return;
      const agent = (window.state?.agents || []).find((a) => String(a.id) === btn.dataset.editAgent);
      if (agent && window.openAgentDetail) {
        window.openAgentDetail(agent.id, agent.name, agent.purpose);
      } else {
        navigateTo("assistants");
      }
    });

    document.getElementById("api-keys-unlock-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const password = document.getElementById("api-keys-password")?.value || "";
      const submitBtn = e.target.querySelector('button[type="submit"]');
      const prev = submitBtn?.textContent;
      if (submitBtn) {
        submitBtn.disabled = true;
        submitBtn.textContent = "Unlocking…";
      }
      try {
        const res = await window.apiJson("/api/settings/api-keys/unlock", {
          method: "POST",
          body: JSON.stringify({ password }),
        });
        setApiSettingsToken(res.api_settings_token);
        await loadApiKeysPanel();
        window.showToast?.("API settings unlocked", "success");
      } catch (err) {
        window.showToast?.(err.message || "Invalid password", "error");
      } finally {
        if (submitBtn) {
          submitBtn.disabled = false;
          submitBtn.textContent = prev || "Unlock";
        }
      }
    });

    document.getElementById("api-keys-lock-btn")?.addEventListener("click", () => {
      setApiSettingsToken(null);
      showApiKeysLockScreen();
      window.showToast?.("API settings locked", "info");
    });

    document.getElementById("api-keys-save-btn")?.addEventListener("click", () => {
      saveApiKeys();
    });

    document.getElementById("assign-knowledge-form")?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const agentId = document.getElementById("knowledge-agent-select")?.value;
      const path = document.getElementById("knowledge-path")?.value;
      if (!agentId || !path) {
        window.showToast?.("Select agent and knowledge path", "error");
        return;
      }
      try {
        const res = await window.apiJson(`/api/agents/${agentId}/assign-knowledge`, {
          method: "POST",
          body: JSON.stringify({
            knowledge_data_path: path,
            config_preset: document.getElementById("knowledge-config-preset")?.value || "config.json",
          }),
        });
        window.showToast?.(`Knowledge assigned (${res.file_size_bytes} bytes)`, "success");
      } catch (err) {
        window.showToast?.(err.message, "error");
      }
    });
  }

  /* ── Page router hook ── */

  const PAGE_LOADERS = {
    overview: loadOverview,
    leads: () => loadLeads(1),
    "hot-leads": loadHotLeads,
    performance: loadPerformance,
    "listing-performance": loadListingPerformance,
    reports: loadReports,
    settings: async () => {
      await loadSettingsExtras();
      setSettingsTab("settings-main-agent");
    },

    calendar: () => {
      window.Calendar?.setupToolbar?.();
      window.Calendar?.render?.();
    },
    analytics: () => {
      window.Analytics?.render?.(window.state?.analytics);
    },
  };

  window.Dashboard = {
    async onPageChange(page) {
      const loader = PAGE_LOADERS[page];
      if (loader) await loader();
    },
    refreshOverview: loadOverview,
    renderOverviewNeedsAction,
    setSettingsTab,
  };

  window.addEventListener("DOMContentLoaded", () => {
    setupLeads();
    setupHotLeads();
    setupReports();
    setupSettingsTabs();

  });
})();