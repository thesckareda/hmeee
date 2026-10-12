/** Organisations (clients) and team management. */

window.Clients = (() => {
  let selectedOrgId = null;

  function $(id) {
    return document.getElementById(id);
  }

  async function loadAuth() {
    const data = await window.apiJson("/api/auth/me");
    window.state.auth = data;
    return data;
  }

  function roleBadge(role) {
    const cls = role === "owner" ? "ok" : role === "admin" ? "info" : "muted";
    return `<span class="tag tag-${cls}">${role}</span>`;
  }

  function renderOrgList(orgs, { platform = false } = {}) {
    const el = $("clients-org-list");
    if (!el) return;
    if (!orgs?.length) {
      el.innerHTML = `<p class="muted">No organisations yet.</p>`;
      return;
    }
    el.innerHTML = orgs
      .map(
        (org) => `
      <button type="button" class="clients-org-card ${selectedOrgId === org.id ? "active" : ""}" data-org-id="${org.id}">
        <strong>${escapeHtml(org.name)}</strong>
        <span class="muted">${escapeHtml(org.slug || "")}</span>
        ${org.member_count != null ? `<span class="pill">${org.member_count} members</span>` : ""}
        ${org.role ? roleBadge(org.role) : ""}
      </button>`
      )
      .join("");

    el.querySelectorAll("[data-org-id]").forEach((btn) => {
      btn.addEventListener("click", () => selectOrg(btn.dataset.orgId));
    });

    if (!selectedOrgId && orgs[0]) selectOrg(orgs[0].id);
  }

  function escapeHtml(s) {
    return String(s || "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  async function selectOrg(orgId) {
    selectedOrgId = orgId;
    document.querySelectorAll(".clients-org-card").forEach((el) => {
      el.classList.toggle("active", el.dataset.orgId === orgId);
    });
    await loadOrgDetail(orgId);
  }

  async function loadOrgDetail(orgId) {
    const panel = $("clients-detail");
    if (!panel) return;
    panel.innerHTML = `<p class="muted vk-skeleton">Loading…</p>`;
    try {
      const data = await window.apiJson(`/api/organizations/${orgId}`);
      const org = data.organization;
      const members = data.members || [];
      const auth = window.state.auth || {};
      const canManage = auth.is_superadmin || (auth.org_id === orgId && auth.can_manage_org);

      panel.innerHTML = `
        <div class="clients-detail-header">
          <div>
            <h3>${escapeHtml(org.name)}</h3>
            <p class="muted">ID: <code>${escapeHtml(org.id)}</code> · ${escapeHtml(org.status)}</p>
          </div>
          ${
            auth.is_superadmin
              ? `<button type="button" class="btn btn-secondary btn-sm" id="clients-switch-workspace">Switch to this client</button>`
              : ""
          }
        </div>
        <div class="card" style="margin-top:16px">
          <div class="card-header-row">
            <h4>Team members</h4>
            ${canManage ? `<button type="button" class="btn btn-primary btn-sm" id="clients-add-member-btn">Add member</button>` : ""}
          </div>
          <div class="table-wrap">
            <table class="data-table">
              <thead><tr><th>Email</th><th>Name</th><th>Role</th><th></th></tr></thead>
              <tbody>
                ${members
                  .map(
                    (m) => `
                  <tr>
                    <td>${escapeHtml(m.email)}</td>
                    <td>${escapeHtml(m.display_name || "—")}</td>
                    <td>${roleBadge(m.role)}</td>
                    <td class="table-actions">
                      ${
                        canManage && m.id !== auth.user_id
                          ? `<button type="button" class="btn btn-ghost btn-sm" data-remove-member="${m.id}">Remove</button>`
                          : ""
                      }
                    </td>
                  </tr>`
                  )
                  .join("")}
              </tbody>
            </table>
          </div>
        </div>
        ${
          canManage
            ? `
        <form id="clients-add-member-form" class="card form-stack" style="margin-top:16px;display:none">
          <h4>Add team member</h4>
          <label class="field"><span>Email</span><input class="input" name="email" type="email" required></label>
          <label class="field"><span>Temporary password</span><input class="input" name="password" type="password" minlength="8" required></label>
          <label class="field"><span>Display name</span><input class="input" name="display_name"></label>
          <label class="field"><span>Role</span>
            <select class="input" name="role">
              <option value="member">Member</option>
              <option value="admin">Admin</option>
              <option value="viewer">Viewer</option>
            </select>
          </label>
          <button type="submit" class="btn btn-primary">Invite</button>
        </form>`
            : ""
        }`;

      $("clients-switch-workspace")?.addEventListener("click", () => switchOrg(orgId));
      $("clients-add-member-btn")?.addEventListener("click", () => {
        const form = $("clients-add-member-form");
        if (form) form.style.display = form.style.display === "none" ? "block" : "none";
      });
      $("clients-add-member-form")?.addEventListener("submit", async (e) => {
        e.preventDefault();
        const fd = new FormData(e.target);
        try {
          await window.apiJson(`/api/organizations/${orgId}/members`, {
            method: "POST",
            body: JSON.stringify({
              email: fd.get("email"),
              password: fd.get("password"),
              display_name: fd.get("display_name"),
              role: fd.get("role"),
            }),
          });
          window.showToast("Member added", "success");
          e.target.reset();
          e.target.style.display = "none";
          await loadOrgDetail(orgId);
        } catch (err) {
          window.showToast(err.message, "error");
        }
      });
      panel.querySelectorAll("[data-remove-member]").forEach((btn) => {
        btn.addEventListener("click", async () => {
          if (!confirm("Remove this member?")) return;
          try {
            await window.apiJson(`/api/organizations/${orgId}/members/${btn.dataset.removeMember}`, {
              method: "DELETE",
            });
            window.showToast("Member removed", "success");
            await loadOrgDetail(orgId);
          } catch (err) {
            window.showToast(err.message, "error");
          }
        });
      });
    } catch (err) {
      panel.innerHTML = `<p class="login-error">${escapeHtml(err.message)}</p>`;
    }
  }

  async function switchOrg(orgId) {
    try {
      const data = await window.apiJson("/api/auth/switch-org", {
        method: "POST",
        body: JSON.stringify({ org_id: orgId }),
      });
      window.setAuthToken(data.access_token);
      await loadAuth();
      window.renderOrgSwitcher?.();
      window.showToast(`Switched to ${data.organization?.name || "workspace"}`, "success");
      if (window.bootstrapData) await window.bootstrapData();
    } catch (err) {
      window.showToast(err.message, "error");
    }
  }

  async function createOrg(name) {
    await window.apiJson("/api/organizations", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  }

  async function init() {
    const auth = await loadAuth();
    const platformPanel = $("clients-platform-panel");
    const teamPanel = $("clients-team-panel");
    const createBtn = $("clients-create-org-btn");

    if (auth.is_superadmin) {
      platformPanel?.removeAttribute("hidden");
      teamPanel?.setAttribute("hidden", "");
      createBtn?.removeAttribute("hidden");
      const data = await window.apiJson("/api/organizations");
      renderOrgList(data.organizations, { platform: true });
    } else {
      platformPanel?.setAttribute("hidden", "");
      teamPanel?.removeAttribute("hidden");
      createBtn?.setAttribute("hidden", "");
      const orgId = auth.org_id;
      if (orgId) await loadOrgDetail(orgId);
    }

    createBtn?.addEventListener("click", async () => {
      const name = prompt("Client organisation name:");
      if (!name?.trim()) return;
      try {
        await createOrg(name.trim());
        window.showToast("Client created", "success");
        const data = await window.apiJson("/api/organizations");
        renderOrgList(data.organizations, { platform: true });
      } catch (err) {
        window.showToast(err.message, "error");
      }
    });
  }

  return { init, switchOrg, loadAuth };
})();