/**
 * Agent Control — kill switch, dev mode, live status + logs.
 */
(function agentControlModule() {
  let pollTimer = null;

  function esc(t) {
    return String(t ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;");
  }

  function fmtUptime(sec) {
    if (!sec) return "—";
    const m = Math.floor(sec / 60);
    const s = sec % 60;
    return m ? `${m}m ${s}s` : `${s}s`;
  }

  function renderStatus(data) {
    const el = document.getElementById("ac-status-body");
    if (!el) return;
    const procs = data.processes || [];
    const dev = data.dev_mode || {};
    const sup = data.supervisor || {};
    const state = data.state || (data.running ? "running" : "stopped");
    const tone = state === "running" ? "ok" : "muted";

    const pill = document.getElementById("ac-state-pill");
    if (pill) {
      pill.className = `ac-state-pill ac-state-pill--${tone}`;
      pill.textContent = state === "running" ? "Running" : "Stopped";
    }

    const rows = procs.length
      ? procs.map((p) => `
        <div class="ac-proc-row">
          <span class="ac-proc-pid">PID ${esc(p.pid)}</span>
          <span class="ac-proc-meta">${esc(fmtUptime(p.uptime_sec))} · ${esc(p.memory_mb != null ? p.memory_mb + " MB" : "—")} · CPU ${esc(p.cpu_percent != null ? p.cpu_percent + "%" : "—")}</span>
        </div>`).join("")
      : `<p class="muted">No agent.py worker detected.</p>`;

    el.innerHTML = `
      <div class="ac-status-grid">
        <div><span class="section-label">State</span><strong>${esc(state)}</strong></div>
        <div><span class="section-label">Supervisor</span><strong>${esc(sup.status || "—")}</strong></div>
        <div><span class="section-label">Dev mode</span><strong>${dev.running ? `pid ${dev.pid}` : "off"}</strong></div>
        <div><span class="section-label">Processes</span><strong>${procs.length}</strong></div>
      </div>
      <div class="ac-proc-list">${rows}</div>
    `;
  }

  async function refreshStatus() {
    try {
      const data = await window.apiJson("/api/system/agent-status");
      renderStatus(data);
    } catch (e) {
      const el = document.getElementById("ac-status-body");
      if (el) el.innerHTML = `<p class="muted">${esc(e.message || "Status unavailable")}</p>`;
    }
  }

  async function refreshLogs() {
    const logEl = document.getElementById("ac-log-output");
    if (!logEl) return;
    try {
      const data = await window.apiJson("/api/system/agent-logs?tail=120");
      const lines = data.lines || [];
      logEl.textContent = lines.join("\n") || "(no logs yet — run dev mode)";
      logEl.scrollTop = logEl.scrollHeight;
    } catch {
      /* ignore */
    }
  }

  async function killAgent() {
    if (!window.confirm("Stop the voice agent? Active calls may drop.")) return;
    const btn = document.getElementById("ac-kill-btn");
    if (btn) btn.disabled = true;
    try {
      const res = await window.apiJson("/api/system/kill-agent", { method: "POST" });
      window.showToast?.(res.message || "Agent stopped", "success");
      await refreshStatus();
      await refreshLogs();
    } catch (e) {
      window.showToast?.(e.message || "Kill failed", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  async function runDev() {
    const btn = document.getElementById("ac-dev-btn");
    if (btn) btn.disabled = true;
    try {
      const res = await window.apiJson("/api/system/run-dev", { method: "POST" });
      window.showToast?.(res.message || "Dev agent started", "success");
      await refreshStatus();
      await refreshLogs();
    } catch (e) {
      window.showToast?.(e.message || "Dev start failed", "error");
    } finally {
      if (btn) btn.disabled = false;
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(() => {
      refreshStatus();
      refreshLogs();
    }, 4000);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function init() {
    document.getElementById("ac-kill-btn")?.addEventListener("click", killAgent);
    document.getElementById("ac-dev-btn")?.addEventListener("click", runDev);
    document.getElementById("ac-refresh-btn")?.addEventListener("click", () => {
      refreshStatus();
      refreshLogs();
    });
    refreshStatus();
    refreshLogs();
    startPolling();
  }

  window.AgentControl = { init, refreshStatus, refreshLogs, startPolling, stopPolling };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();