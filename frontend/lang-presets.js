/**
 * Language Presets — edit, enable/disable, and activate runtime language packs.
 */
(function langPresetsModule() {
  const state = {
    activePresetId: "",
    runtimeLangPreset: "",
    presets: [],
    editingId: null,
    busy: false,
  };

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

  function setStatus(text, tone = "muted") {
    const el = $("lp-status");
    if (!el) return;
    el.className = `main-agent-status main-agent-status--${tone}`;
    el.textContent = text;
  }

  function toast(msg, tone = "info") {
    if (typeof window.showToast === "function") window.showToast(msg, tone);
    else setStatus(msg, tone === "error" ? "error" : "ok");
  }

  async function fetchPresets() {
    const res = await window.apiFetch("/api/lang-presets");
    if (!res.ok) throw new Error("Failed to load presets");
    return res.json();
  }

  function langList(preset) {
    return (preset.languages || []).join(", ") || "—";
  }

  function renderGrid() {
    const root = $("lp-presets-grid");
    if (!root) return;

    if (!state.presets.length) {
      root.innerHTML = '<p class="muted">No presets found.</p>';
      return;
    }

    root.innerHTML = state.presets
      .map((preset) => {
        const id = preset.id;
        const isActive =
          id === state.activePresetId || id === state.runtimeLangPreset;
        const enabled = preset.enabled !== false;
        const idle = preset.user_idle || {};

        return `
          <article class="card lp-preset-card${isActive ? " lp-preset-card--active" : ""}${!enabled ? " lp-preset-card--disabled" : ""}" data-preset-id="${esc(id)}">
            <header class="lp-preset-head">
              <div>
                <h4>${esc(preset.label || id)}</h4>
                <p class="muted lp-preset-desc">${esc(preset.description || "")}</p>
              </div>
              <div class="lp-preset-badges">
                ${isActive ? '<span class="lp-badge lp-badge--active">Active</span>' : ""}
                ${!enabled ? '<span class="lp-badge lp-badge--off">Disabled</span>' : ""}
              </div>
            </header>
            <dl class="lp-preset-meta">
              <div><dt>Languages</dt><dd>${esc(langList(preset))}</dd></div>
              <div><dt>Voice</dt><dd>${esc(preset.tts_voice_display || preset.tts_voice || "—")}</dd></div>
              <div><dt>Idle prompt</dt><dd>${esc(idle.prompt_message || "—")}</dd></div>
              <div><dt>Interrupt ack</dt><dd>${esc(preset.interrupt_ack || "—")}</dd></div>
            </dl>
            <p class="lp-preset-greeting muted">"${esc((preset.first_line || "").slice(0, 120))}${(preset.first_line || "").length > 120 ? "…" : ""}"</p>
            <footer class="lp-preset-actions">
              <label class="lp-toggle" title="${enabled ? "Disable preset" : "Enable preset"}">
                <input type="checkbox" class="lp-enable-input" data-preset-id="${esc(id)}" ${enabled ? "checked" : ""} ${state.busy ? "disabled" : ""}>
                <span>Enabled</span>
              </label>
              <div class="lp-action-btns">
                <button type="button" class="btn btn-ghost btn-sm lp-edit-btn" data-preset-id="${esc(id)}" ${state.busy ? "disabled" : ""}>Edit</button>
                <button type="button" class="btn btn-primary btn-sm lp-activate-btn" data-preset-id="${esc(id)}" ${!enabled || state.busy ? "disabled" : ""}>
                  ${isActive ? "Re-apply" : "Activate"}
                </button>
              </div>
            </footer>
          </article>
        `;
      })
      .join("");

    root.querySelectorAll(".lp-edit-btn").forEach((btn) => {
      btn.addEventListener("click", () => openEditor(btn.dataset.presetId));
    });
    root.querySelectorAll(".lp-activate-btn").forEach((btn) => {
      btn.addEventListener("click", () => activatePreset(btn.dataset.presetId));
    });
    root.querySelectorAll(".lp-enable-input").forEach((input) => {
      input.addEventListener("change", () => togglePreset(input.dataset.presetId, input.checked));
    });
  }

  function findPreset(id) {
    return state.presets.find((p) => p.id === id);
  }

  function openEditor(presetId) {
    const preset = findPreset(presetId);
    if (!preset) return;
    state.editingId = presetId;
    $("lp-modal-title").textContent = `Edit — ${preset.label || presetId}`;
    $("lp-edit-label").value = preset.label || "";
    $("lp-edit-description").value = preset.description || "";
    $("lp-edit-first-line").value = preset.first_line || "";
    $("lp-edit-interrupt-ack").value = preset.interrupt_ack || "";
    $("lp-edit-voice").value = preset.tts_voice || "";
    $("lp-edit-directive").value = preset.language_directive || "";
    $("lp-edit-instructions").value = preset.agent_instructions || "";
    const idle = preset.user_idle || {};
    $("lp-edit-idle-prompt").value = idle.prompt_message || "";
    $("lp-edit-idle-last").value = idle.last_message || "";
    $("lp-modal").hidden = false;
  }

  function closeEditor() {
    $("lp-modal").hidden = true;
    state.editingId = null;
  }

  async function saveEditor() {
    if (!state.editingId || state.busy) return;
    state.busy = true;
    setStatus("Saving…", "muted");
    const patch = {
      label: $("lp-edit-label").value.trim(),
      description: $("lp-edit-description").value.trim(),
      first_line: $("lp-edit-first-line").value.trim(),
      interrupt_ack: $("lp-edit-interrupt-ack").value.trim(),
      tts_voice: $("lp-edit-voice").value.trim(),
      language_directive: $("lp-edit-directive").value.trim(),
      agent_instructions: $("lp-edit-instructions").value.trim(),
      user_idle: {
        prompt_message: $("lp-edit-idle-prompt").value.trim(),
        last_message: $("lp-edit-idle-last").value.trim(),
      },
    };
    try {
      const res = await window.apiFetch(`/api/lang-presets/${state.editingId}`, {
        method: "PUT",
        body: JSON.stringify(patch),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(window.parseApiError?.(body, "Save failed") || "Save failed");
      closeEditor();
      await load();
      toast("Preset saved", "success");
    } catch (err) {
      setStatus(err.message || "Save failed", "error");
      toast(err.message || "Save failed", "error");
    } finally {
      state.busy = false;
      renderGrid();
    }
  }

  async function activatePreset(presetId) {
    if (state.busy) return;
    state.busy = true;
    setStatus(`Activating ${presetId}…`, "muted");
    try {
      const res = await window.apiFetch(`/api/lang-presets/${presetId}/activate`, {
        method: "POST",
        body: JSON.stringify({ restart_agent: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(window.parseApiError?.(body, "Activate failed") || "Activate failed");
      state.activePresetId = presetId;
      state.runtimeLangPreset = presetId;
      await load();
      toast(`Activated ${presetId} — agent reloading`, "success");
      setStatus(`Active: ${presetId}`, "ok");
    } catch (err) {
      setStatus(err.message || "Activate failed", "error");
      toast(err.message || "Activate failed", "error");
    } finally {
      state.busy = false;
      renderGrid();
    }
  }

  async function togglePreset(presetId, enabled) {
    if (state.busy) return;
    state.busy = true;
    setStatus(`${enabled ? "Enabling" : "Disabling"} ${presetId}…`, "muted");
    try {
      const res = await window.apiFetch(`/api/lang-presets/${presetId}/toggle`, {
        method: "POST",
        body: JSON.stringify({ enabled }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(window.parseApiError?.(body, "Toggle failed") || "Toggle failed");
      await load();
      toast(`${presetId} ${enabled ? "enabled" : "disabled"}`, "success");
    } catch (err) {
      await load();
      toast(err.message || "Toggle failed", "error");
    } finally {
      state.busy = false;
      renderGrid();
    }
  }

  async function load() {
    setStatus("Loading…", "muted");
    try {
      const data = await fetchPresets();
      state.activePresetId = data.active_preset_id || "";
      state.runtimeLangPreset = data.runtime_lang_preset || "";
      state.presets = data.presets || [];
      renderGrid();
      const active = state.runtimeLangPreset || state.activePresetId || "none";
      setStatus(`Runtime: ${active}`, "ok");
    } catch (err) {
      setStatus(err.message || "Load failed", "error");
    }
  }

  function bindEvents() {
    $("lp-reload-btn")?.addEventListener("click", () => load());
    $("lp-modal-close")?.addEventListener("click", closeEditor);
    $("lp-modal-save")?.addEventListener("click", () => saveEditor());
    document.querySelectorAll("[data-lp-dismiss]").forEach((el) => {
      el.addEventListener("click", closeEditor);
    });
  }

  bindEvents();
  window.LangPresets = { load, activatePreset };
})();