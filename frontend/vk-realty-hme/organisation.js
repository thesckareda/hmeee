/** VK Realty — Organisation & role workspaces */
window.Organisation = (() => {
  let parentOrgId = null;
  let featureCatalog = {};
  let workspacesCache = [];
  let selectedId = null;

  const TAB_ORDER = [
    "overview",
    "leads",
    "hot-leads",
    "crm",
    "call-logs",
    "calendar",
    "reports",
    "settings",
    "performance",
    "listing-performance",
    "main-agent",
    "lang-presets",
    "assistants",
    "analytics",
    "campaigns",
    "knowledge",
    "integrations",
    "billing",
  ];

  const TAB_LABELS = {
    overview: "Today",
    leads: "All Leads",
    "hot-leads": "Priority Leads",
    performance: "Performance",
    "listing-performance": "Listings",
    reports: "Reports",
    "main-agent": "Arjun (AI agent)",
    "lang-presets": "How Arjun speaks",
    assistants: "AI assistants",
    "call-logs": "Call History",
    analytics: "Numbers & trends",
    calendar: "Site Visits",
    campaigns: "Outbound lists",
    settings: "Settings",
    crm: "CRM",
    knowledge: "Property info",
    integrations: "Connections",
    billing: "Billing",
    organisation: "Team dashboards",
  };

  /* Tabs a property team never needs day to day */
  const ADVANCED_TABS = new Set([
    "performance",
    "listing-performance",
    "main-agent",
    "lang-presets",
    "assistants",
    "analytics",
    "campaigns",
    "knowledge",
    "integrations",
    "billing",
  ]);

  const ROLE_PRESETS = [
    {
      key: "sales",
      icon: "&#128222;",
      label: "Sales rep",
      desc: "Follow up on leads and listen to calls",
      features: ["overview", "leads", "hot-leads", "call-logs", "crm"],
    },
    {
      key: "coordinator",
      icon: "&#128197;",
      label: "Site-visit coordinator",
      desc: "Today's visits and who booked them",
      features: ["overview", "calendar", "call-logs", "leads"],
    },
    {
      key: "manager",
      icon: "&#128200;",
      label: "Manager",
      desc: "Leads, visits, reports and CRM — the full picture",
      features: ["overview", "leads", "hot-leads", "crm", "call-logs", "calendar", "reports", "settings"],
    },
  ];

  let activePreset = "sales";

  let bootstrapped = false;
  let formWired = false;

  function $(id) {
    return document.getElementById(id);
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function slugify(text) {
    return String(text || "")
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .slice(0, 48);
  }

  function normalizeWorkspace(ws) {
    const copy = { ...ws };
    if (typeof copy.metadata === "string") {
      try {
        copy.metadata = JSON.parse(copy.metadata);
      } catch {
        copy.metadata = {};
      }
    }
    if (!Array.isArray(copy.features)) {
      copy.features = [];
    }
    return copy;
  }

  function featureLabel(key) {
    return TAB_LABELS[key] || featureCatalog[key] || key;
  }

  function orderedFeatureKeys() {
    const keys = Object.keys(featureCatalog).length
      ? Object.keys(featureCatalog)
      : TAB_ORDER.filter((k) => k !== "organisation");
    return [
      ...TAB_ORDER.filter((k) => keys.includes(k) && k !== "organisation"),
      ...keys.filter((k) => !TAB_ORDER.includes(k) && k !== "organisation"),
    ];
  }

  async function ensureFeatureCatalog() {
    if (Object.keys(featureCatalog).length) return featureCatalog;
    try {
      const data = await window.apiJson("/api/portal/features");
      featureCatalog = data.features || data.portal_features || TAB_LABELS;
    } catch {
      featureCatalog = { ...TAB_LABELS };
    }
    return featureCatalog;
  }

  async function loadAuth() {
    const data = await window.apiJson("/api/auth/me");
    window.state.auth = data;
    featureCatalog = data.portal_features || featureCatalog;
    if (!Object.keys(featureCatalog).length) {
      await ensureFeatureCatalog();
    }
    return data;
  }

  function resolveParentOrgId(auth) {
    const org = auth.current_organization;
    if (!org) return auth.org_id;
    if (org.parent_org_id) return org.parent_org_id;
    return org.id;
  }

  function renderFeatureCheckboxes(selected = []) {
    const grid = $("org-features-grid");
    if (!grid) return;
    const keys = orderedFeatureKeys();
    const chip = (key) => {
      const checked = selected.includes(key) ? "checked" : "";
      return `
          <label class="org-feature-chip">
            <input type="checkbox" name="features" value="${escapeHtml(key)}" ${checked}>
            <span>${escapeHtml(featureLabel(key))}</span>
          </label>`;
    };
    grid.innerHTML = `
      <div class="org-feature-group-label">Everyday tabs</div>
      ${keys.filter((k) => !ADVANCED_TABS.has(k)).map(chip).join("")}
      <div class="org-feature-group-label">Technical tabs — usually OUTPERO only</div>
      ${keys.filter((k) => ADVANCED_TABS.has(k)).map(chip).join("")}`;
    grid.querySelectorAll('input[name="features"]').forEach((input) => {
      input.addEventListener("change", () => {
        if (activePreset !== null) {
          activePreset = null;
          renderPresetPicker();
        }
      });
    });
  }

  function renderPresetPicker() {
    const grid = $("org-preset-grid");
    if (!grid) return;
    const cards = ROLE_PRESETS.map((p) => {
      const active = p.key === activePreset ? " active" : "";
      return `
        <button type="button" class="org-preset-card${active}" data-preset="${p.key}">
          <span class="org-preset-icon" aria-hidden="true">${p.icon}</span>
          <span class="org-preset-copy">
            <strong>${p.label}</strong>
            <span class="org-preset-desc">${p.desc}</span>
            <span class="org-preset-tabs">${p.features.map((f) => escapeHtml(featureLabel(f))).join(" · ")}</span>
          </span>
          <span class="org-preset-check" aria-hidden="true">&#10003;</span>
        </button>`;
    });
    grid.innerHTML = cards.join("");
    grid.querySelectorAll("[data-preset]").forEach((btn) => {
      btn.addEventListener("click", () => {
        activePreset = btn.dataset.preset;
        const preset = ROLE_PRESETS.find((p) => p.key === activePreset);
        renderPresetPicker();
        if (preset) renderFeatureCheckboxes(preset.features);
        const custom = $("org-custom-tabs");
        if (custom) custom.open = false;
      });
    });
  }

  function renderTabAccessTable(ws, { editable = true } = {}) {
    const enabled = new Set(ws.features || []);
    const rows = orderedFeatureKeys()
      .map((key) => {
        const on = enabled.has(key);
        const advTag = ADVANCED_TABS.has(key) ? ' <span class="org-adv-tag">technical</span>' : "";
        return `
          <tr data-feature="${escapeHtml(key)}">
            <td>${escapeHtml(featureLabel(key))}${advTag}</td>
            <td class="org-access-cell">
              ${
                editable
                  ? `<label class="org-access-toggle">
                      <input type="checkbox" class="org-tab-toggle" data-feature="${escapeHtml(key)}" ${on ? "checked" : ""}>
                      <span class="org-access-label">${on ? "On" : "Off"}</span>
                    </label>`
                  : `<span class="pill ${on ? "pill-on" : "pill-off"}">${on ? "Can access" : "Hidden"}</span>`
              }
            </td>
          </tr>`;
      })
      .join("");

    return `
      <table class="org-access-table">
        <thead><tr><th>Dashboard tab</th><th>Access</th></tr></thead>
        <tbody>${rows}</tbody>
      </table>`;
  }

  function renderWorkspaceDetail(ws) {
    const panel = $("org-workspace-detail");
    if (!panel || !ws) return;

    const name = ws.metadata?.role_name || ws.name;
    const base = window.clientBasePath ? window.clientBasePath() : "/vk-realty-hme";
    const path = ws.portal_path || `${base}/o/${ws.slug}/dashboard`;
    const url = `${window.location.origin}${path}`;
    const enabledCount = (ws.features || []).length;
    const waText = encodeURIComponent(
      `Your VK Realty dashboard\n\nLink: ${url}\nTeam code: ${ws.slug}\n\nOpen the link and sign in with the team code plus your username and password.`
    );

    panel.innerHTML = `
      <div class="org-detail-head">
        <div>
          <h3>${escapeHtml(name)}</h3>
          <p class="muted">${enabledCount} tab${enabledCount === 1 ? "" : "s"} enabled · created ${escapeHtml((ws.created_at || "").slice(0, 10) || "—")}</p>
        </div>
        <button type="button" class="btn btn-secondary btn-sm" data-open-role="${escapeHtml(ws.slug)}">Open dashboard</button>
      </div>
      <div class="org-share-card">
        <h4>Share with your team</h4>
        <p class="muted org-share-hint">Send them the link and the team code — that&rsquo;s all they need to sign in.</p>
        <div class="org-share-row">
          <span class="org-share-label">Link</span>
          <code class="org-share-value">${escapeHtml(url)}</code>
          <button type="button" class="btn btn-ghost btn-sm" data-copy-value="${escapeHtml(url)}">Copy</button>
        </div>
        <div class="org-share-row">
          <span class="org-share-label">Team code</span>
          <code class="org-share-value">${escapeHtml(ws.slug)}</code>
          <button type="button" class="btn btn-ghost btn-sm" data-copy-value="${escapeHtml(ws.slug)}">Copy</button>
        </div>
        <a class="btn btn-secondary btn-sm org-wa-share" href="https://wa.me/?text=${waText}" target="_blank" rel="noopener">Share on WhatsApp</a>
      </div>
      <h4 class="org-detail-sub">What they can see</h4>
      <p class="muted org-detail-hint">Turn tabs on or off — it applies the next time they open their dashboard.</p>
      <div id="org-detail-access-wrap">${renderTabAccessTable(ws, { editable: true })}</div>
      <div class="org-detail-actions">
        <button type="button" class="btn btn-primary btn-sm" id="org-save-access-btn">Save changes</button>
      </div>`;

    panel.querySelectorAll(".org-tab-toggle").forEach((input) => {
      input.addEventListener("change", () => {
        const label = input.closest(".org-access-toggle")?.querySelector(".org-access-label");
        if (label) label.textContent = input.checked ? "On" : "Off";
      });
    });

    $("org-save-access-btn")?.addEventListener("click", () => saveWorkspaceAccess(ws.id));
    panel.querySelector("[data-open-role]")?.addEventListener("click", () => openRoleWorkspace(ws.slug));
    panel.querySelectorAll("[data-copy-value]").forEach((btn) => {
      btn.addEventListener("click", () => {
        navigator.clipboard?.writeText(btn.dataset.copyValue);
        window.showToast?.("Copied", "success");
      });
    });
  }

  async function saveWorkspaceAccess(workspaceId) {
    const features = [...document.querySelectorAll("#org-detail-access-wrap .org-tab-toggle:checked")]
      .map((el) => el.dataset.feature)
      .filter(Boolean);
    if (!features.length) {
      window.showToast?.("Enable at least one tab", "error");
      return;
    }
    try {
      const res = await window.apiJson(`/api/organizations/${parentOrgId}/workspaces/${workspaceId}`, {
        method: "PATCH",
        body: JSON.stringify({ features }),
      });
      window.showToast?.("Saved — their dashboard is updated", "success");
      const updated = normalizeWorkspace(res.workspace);
      workspacesCache = workspacesCache.map((w) => (w.id === updated.id ? updated : w));
      selectWorkspace(updated.id);
      renderWorkspaceList(workspacesCache);
      renderOverviewRoles(workspacesCache);
    } catch (err) {
      window.showToast?.(err.message, "error");
    }
  }

  function selectWorkspace(id) {
    selectedId = id;
    const ws = workspacesCache.find((w) => w.id === id);
    renderWorkspaceList(workspacesCache);
    if (ws) {
      renderWorkspaceDetail(ws);
    }
  }

  function renderWorkspaceList(workspaces) {
    const el = $("org-workspace-list");
    const countEl = $("org-role-count");
    if (!el) return;

    workspacesCache = workspaces.map(normalizeWorkspace);
    if (countEl) countEl.textContent = String(workspacesCache.length);

    if (!workspacesCache.length) {
      el.innerHTML = `<p class="muted org-list-empty">No team dashboards yet — create your first one below.</p>`;
      const detail = $("org-workspace-detail");
      if (detail) {
        detail.innerHTML = `<p class="muted org-detail-empty">Create a dashboard to get a link and team code you can share on WhatsApp.</p>`;
      }
      return;
    }

    el.innerHTML = workspacesCache
      .map((ws) => {
        const name = ws.metadata?.role_name || ws.name;
        const active = ws.id === selectedId ? " active" : "";
        const tabCount = (ws.features || []).length;
        return `
          <button type="button" class="org-workspace-item${active}" data-workspace-id="${escapeHtml(ws.id)}">
            <span class="org-workspace-item-name">${escapeHtml(name)}</span>
            <span class="org-workspace-item-meta"><code>${escapeHtml(ws.slug)}</code> · ${tabCount} tabs</span>
          </button>`;
      })
      .join("");

    el.querySelectorAll("[data-workspace-id]").forEach((btn) => {
      btn.addEventListener("click", () => selectWorkspace(btn.dataset.workspaceId));
    });

    if (!selectedId && workspacesCache[0]) {
      selectWorkspace(workspacesCache[0].id);
    } else if (selectedId && workspacesCache.some((w) => w.id === selectedId)) {
      renderWorkspaceDetail(workspacesCache.find((w) => w.id === selectedId));
    }
  }

  function renderOverviewTabChips(ws) {
    const enabled = new Set(ws.features || []);
    const chips = orderedFeatureKeys()
      .filter((key) => enabled.has(key))
      .map((key) => {
        const label = featureLabel(key);
        return `<span class="vk-tab-chip vk-tab-on" title="${escapeHtml(label)}">&#10003; ${escapeHtml(label)}</span>`;
      });
    return chips.join("") || '<span class="muted">No tabs enabled yet</span>';
  }

  function renderOverviewRoles(workspaces) {
    const panel = $("vk-overview-roles");
    const list = $("vk-overview-roles-list");
    if (!panel || !list) return;

    const auth = window.state?.auth;
    if (!canManageRoles(auth)) {
      panel.hidden = true;
      return;
    }

    const items = (workspaces || []).map(normalizeWorkspace);
    panel.hidden = false;

    if (!items.length) {
      list.innerHTML = `<p class="muted vk-overview-empty">No roles yet. <button type="button" class="btn btn-ghost btn-sm" data-goto-org>Create one</button></p>`;
      list.querySelector("[data-goto-org]")?.addEventListener("click", () => window.setPage?.("organisation"));
      return;
    }

    list.innerHTML = items
      .map((w) => {
        const name = w.metadata?.role_name || w.name;
        const base = window.clientBasePath ? window.clientBasePath() : "/vk-realty-hme";
        const path = w.portal_path || `${base}/o/${w.slug}/dashboard`;
        const url = `${window.location.origin}${path}`;
        const enabledCount = (w.features || []).length;
        return `
          <article class="vk-overview-role-card">
            <div class="vk-overview-role-head">
              <div>
                <strong>${escapeHtml(name)}</strong>
                <p class="muted vk-overview-role-meta">
                  Team code: <code>${escapeHtml(w.slug)}</code>
                  · <span>${enabledCount} tab${enabledCount === 1 ? "" : "s"}</span>
                </p>
              </div>
              <div class="vk-overview-role-actions">
                <button type="button" class="btn btn-ghost btn-sm" data-org-manage="${escapeHtml(w.id)}">Edit access</button>
                <button type="button" class="btn btn-ghost btn-sm" data-copy-code="${escapeHtml(w.slug)}">Copy code</button>
                <button type="button" class="btn btn-secondary btn-sm" data-open-role="${escapeHtml(w.slug)}">Open</button>
              </div>
            </div>
            <p class="vk-overview-role-url muted"><code>${escapeHtml(url)}</code></p>
            <div class="vk-overview-tab-chips">${renderOverviewTabChips(w)}</div>
          </article>`;
      })
      .join("");

    list.querySelectorAll("[data-org-manage]").forEach((btn) => {
      btn.addEventListener("click", () => {
        window.setPage?.("organisation");
        setTimeout(() => selectWorkspace(btn.dataset.orgManage), 150);
      });
    });
    list.querySelectorAll("[data-copy-code]").forEach((btn) => {
      btn.addEventListener("click", () => {
        navigator.clipboard?.writeText(btn.dataset.copyCode);
        window.showToast?.(`Team code copied: ${btn.dataset.copyCode}`, "success");
      });
    });
    list.querySelectorAll("[data-open-role]").forEach((btn) => {
      btn.addEventListener("click", () => openRoleWorkspace(btn.dataset.openRole));
    });
  }

  async function refresh() {
    if (!parentOrgId) return [];
    const data = await window.apiJson(`/api/organizations/${parentOrgId}/workspaces`);
    const workspaces = (data.workspaces || []).map(normalizeWorkspace);
    renderWorkspaceList(workspaces);
    renderOverviewRoles(workspaces);
    return workspaces;
  }

  function canManageRoles(auth) {
    if (!auth) return false;
    const isChild = Boolean(auth.current_organization?.parent_org_id);
    return !isChild && (auth.can_manage_org || auth.is_superadmin);
  }

  async function openRoleWorkspace(slug) {
    if (!slug) return;
    try {
      const data = await window.apiJson("/api/auth/switch-org", {
        method: "POST",
        body: JSON.stringify({ org_code: slug }),
      });
      window.setAuthToken?.(data.access_token);
      await window.loadAuthProfile?.();
      const base = window.clientBasePath ? window.clientBasePath() : "/vk-realty-hme";
      window.location.assign(`${base}/o/${slug}/dashboard`);
    } catch (err) {
      window.showToast?.(err.message || "Could not open role dashboard", "error");
    }
  }

  async function loadOverviewRoles() {
    const panel = $("vk-overview-roles");
    const list = $("vk-overview-roles-list");
    try {
      const auth = window.state?.auth?.org_id ? window.state.auth : await loadAuth();
      await ensureFeatureCatalog();
      parentOrgId = resolveParentOrgId(auth);

      if (!canManageRoles(auth)) {
        if (panel) panel.hidden = true;
        return;
      }

      if (panel) panel.hidden = false;
      if (list && !workspacesCache.length) {
        list.innerHTML = `<p class="muted vk-overview-empty">Loading roles…</p>`;
      }

      if (!parentOrgId) {
        if (list) list.innerHTML = `<p class="muted vk-overview-empty">No organisation found.</p>`;
        return;
      }

      const workspaces = await refresh();
      return workspaces;
    } catch (e) {
      console.error("loadOverviewRoles:", e);
      if (panel) panel.hidden = false;
      if (list) {
        list.innerHTML = `<p class="muted vk-overview-empty">Could not load roles. <button type="button" class="btn btn-ghost btn-sm" data-retry-roles>Retry</button></p>`;
        list.querySelector("[data-retry-roles]")?.addEventListener("click", () => loadOverviewRoles());
      }
    }
  }

  async function bootstrap({ force = false } = {}) {
    if (bootstrapped && !force) return;
    const auth = await loadAuth();
    parentOrgId = resolveParentOrgId(auth);
    await ensureFeatureCatalog();
    wireCreateForm();
    renderPresetPicker();
    if (parentOrgId && canManageRoles(auth)) {
      await refresh();
    }
    bootstrapped = true;
  }

  function wireCreateForm() {
    if (formWired) return;
    formWired = true;
    const form = $("org-create-form");
    const slugInput = $("org-role-slug");
    const nameInput = $("org-role-name");

    nameInput?.addEventListener("input", () => {
      if (slugInput && !slugInput.dataset.touched) {
        slugInput.value = slugify(nameInput.value);
      }
    });
    slugInput?.addEventListener("input", () => {
      slugInput.dataset.touched = "1";
    });

    form?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const features = [...form.querySelectorAll('input[name="features"]:checked')].map((el) => el.value);
      if (!features.length) {
        window.showToast?.("Pick a role or choose at least one tab", "error");
        return;
      }
      const fd = new FormData(form);
      try {
        const res = await window.apiJson(`/api/organizations/${parentOrgId}/workspaces`, {
          method: "POST",
          body: JSON.stringify({
            name: fd.get("name"),
            role_slug: fd.get("role_slug"),
            features,
          }),
        });
        const ws = normalizeWorkspace(res.workspace);
        window.showToast?.("Dashboard created — now share the link with your team", "success");
        form.reset();
        if (slugInput) delete slugInput.dataset.touched;
        activePreset = ROLE_PRESETS[0].key;
        renderPresetPicker();
        renderFeatureCheckboxes(ROLE_PRESETS[0].features);
        await refresh();
        selectWorkspace(ws.id);
        $("org-workspace-detail")?.scrollIntoView({ behavior: "smooth", block: "nearest" });
      } catch (err) {
        window.showToast?.(err.message, "error");
      }
    });
  }

  async function loadTeam() {
    const panel = $("org-team-panel");
    if (!panel || !parentOrgId) return;
    try {
      const data = await window.apiJson(`/api/organizations/${parentOrgId}`);
      const members = data.members || [];
      panel.innerHTML = `
        <h4>Team · ${escapeHtml(data.organization?.name || "Organisation")}</h4>
        <div class="table-wrap">
          <table class="data-table">
            <thead><tr><th>Email</th><th>Name</th><th>Role</th></tr></thead>
            <tbody>
              ${members
                .map(
                  (m) => `
                <tr>
                  <td>${escapeHtml(m.email)}</td>
                  <td>${escapeHtml(m.display_name || "—")}</td>
                  <td><span class="tag">${escapeHtml(m.role)}</span></td>
                </tr>`
                )
                .join("")}
            </tbody>
          </table>
        </div>`;
    } catch (err) {
      panel.innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
    }
  }

  async function init() {
    const auth = window.state?.auth?.org_id ? window.state.auth : await loadAuth();
    parentOrgId = resolveParentOrgId(auth);
    await ensureFeatureCatalog();
    wireCreateForm();

    const canManage = canManageRoles(auth);
    const isChild = Boolean(auth.current_organization?.parent_org_id);

    $("org-create-section")?.toggleAttribute("hidden", !canManage);
    $("org-admin-note")?.toggleAttribute("hidden", canManage && !isChild);

    if (isChild) {
      $("org-child-note")?.removeAttribute("hidden");
      $("org-child-slug") && ($("org-child-slug").textContent = auth.current_organization?.slug || "");
      const list = $("org-workspace-list");
      const detail = $("org-workspace-detail");
      if (list) list.innerHTML = `<p class="muted org-list-empty">Team dashboards are managed from your main account.</p>`;
      if (detail) detail.innerHTML = `<p class="muted org-detail-empty">Sign in with your main account to make changes here.</p>`;
      return;
    }

    $("org-child-note")?.setAttribute("hidden", "");

    renderPresetPicker();
    renderFeatureCheckboxes(ROLE_PRESETS[0].features);

    if (parentOrgId && canManage) {
      try {
        await refresh();
        await loadTeam();
      } catch (err) {
        console.error("Organisation.init:", err);
        const list = $("org-workspace-list");
        if (list) list.innerHTML = `<p class="muted">${escapeHtml(err.message)}</p>`;
      }
    }
    bootstrapped = true;
  }

  return { init, bootstrap, refresh, loadOverviewRoles, selectWorkspace };
})();