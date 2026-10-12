/**
 * Agent detail tabs — Step 5
 * Requires app.js globals: state, apiJson, showToast, setAgentTab, displayPhone, displayCallerName, safeDate, humanizeSummary, tag
 */
(function () {
  const detail = {
    config: null,
    agentCalls: [],
    flowSections: [],
    saving: false,
    aiApplying: false,
    versions: [],
    versionsLoading: false,
  };

  const LANG_OPTIONS = ["en", "hi", "ta", "te", "mr", "bn"];
  const LLM_PROVIDERS = ["gemini", "openai", "groq"];
  const STT_PROVIDERS = ["sarvam", "deepgram", "soniox"];
  const TTS_PROVIDERS = ["cartesia", "gemini", "sarvam", "elevenlabs", "openai"];
  const AMBIENT_SOUND_OPTIONS = [
    { value: "cicd_1", label: "CICD office (custom)" },
    { value: "office_ambient", label: "Office ambience" },
    { value: "city_ambient", label: "City street" },
    { value: "crowded_room", label: "Crowded room" },
    { value: "forest_ambient", label: "Forest ambience" },
    { value: "keyboard_typing", label: "Keyboard typing" },
    { value: "keyboard_typing2", label: "Keyboard typing (alt)" },
  ];

  const TTS_VOICES = [
    "nivas",
    "ramya",
    "vikram",
    "4b732050-eaed-49f0-ae76-a6281a20e303",
    "3f2297f1-cac9-4942-b0bd-5d9bb3c35946",
    "cf061d8b-a752-4865-81a2-57570a6e0565",
    "38bded0a-3ab4-42d1-8e47-2e0b6b10ced9",
    "Rasalgethi",
    "kavya",
    "arvind",
    "ananya",
    "amartya",
  ];
  const DELIVERY_METHODS = ["webhook", "email", "both", "none"];
  const TRIGGER_STATUSES = ["completed", "transferred", "voicemail", "failed", "no_answer"];

  function esc(value) {
    const el = document.createElement("span");
    el.textContent = value == null ? "" : String(value);
    return el.innerHTML;
  }

  function $(id) {
    return document.getElementById(id);
  }

  function agentId() {
    return state.currentAgentId;
  }

  function activeTab() {
    return state.currentAgentTab || "assistant-details";
  }

  function deepClone(obj) {
    return JSON.parse(JSON.stringify(obj || {}));
  }

  function normalizeSections(sections) {
    return (sections || []).map((s, i) => ({
      id: s.id || `section_${i + 1}`,
      title: s.title || `Section ${i + 1}`,
      content: s.content || "",
      enabled: s.enabled !== false,
      order: Number(s.order) || i + 1,
    })).sort((a, b) => a.order - b.order);
  }

  function setLoading(panelId, message = "Loading…") {
    const panel = $(panelId);
    if (panel) {
      panel.innerHTML = `<div class="agent-loading glass-card"><span class="pulse-dot"></span> <span class="vk-skeleton">${esc(message)}</span></div>`;
    }
  }

  async function loadAgent(agentId) {
    if (!agentId) return;
    setLoading("tab-assistant-details", "Loading assistant…");
    try {
      const res = await apiJson(`/api/agents/${agentId}`);
      detail.config = res.data || res;
      detail.flowSections = normalizeSections(detail.config.conversational_flow?.sections);
      const agent = detail.config.agent || {};
      $("agent-detail-name").textContent = agent.name || "Agent";
      updateActivateButton();
      $("agent-detail-purpose").textContent = (agent.purpose || "general").replace(/_/g, " ");
      renderActiveTab();
    } catch (e) {
      showToast(e.message || "Failed to load agent", "error");
    }
  }

  function renderActiveTab() {
    const tab = activeTab();
    if (tab === "assistant-details") renderAssistantDetails();
    else if (tab === "ai-edit") renderAiEdit();
    else if (tab === "call-configuration") renderCallConfiguration();
    else if (tab === "knowledge-base") renderKnowledgeBase();
    else if (tab === "integrations") renderIntegrations();
    else if (tab === "post-call") renderPostCall();
    else if (tab === "recent-calls") renderRecentCalls();
  }

  function field(label, inputHtml) {
    return `<label class="field"><span>${esc(label)}</span>${inputHtml}</label>`;
  }

  function selectField(label, id, options, value) {
    const opts = options.map((o) => {
      const v = typeof o === "string" ? o : o.value;
      const t = typeof o === "string" ? o : o.label;
      return `<option value="${esc(v)}" ${String(value) === String(v) ? "selected" : ""}>${esc(t)}</option>`;
    }).join("");
    return field(label, `<select class="input" id="${id}">${opts}</select>`);
  }

  function checkboxField(id, label, checked) {
    return `
      <label class="check-row">
        <input type="checkbox" id="${id}" ${checked ? "checked" : ""}>
        <span>${esc(label)}</span>
      </label>
    `;
  }

  function renderFlowSectionsList() {
    if (!detail.flowSections.length) {
      return `<div class="flow-empty text-muted">No sections yet. Add one to guide the conversation.</div>`;
    }
    return detail.flowSections.map((s, idx) => `
      <div class="flow-section-item glass-card" draggable="true" data-flow-idx="${idx}">
        <div class="flow-section-toolbar">
          <button type="button" class="drag-handle" aria-label="Drag to reorder" data-flow-idx="${idx}">⋮⋮</button>
          <label class="flow-toggle" title="${s.enabled ? "Section enabled" : "Section disabled"}">
            <input type="checkbox" class="flow-enabled" data-flow-idx="${idx}" ${s.enabled ? "checked" : ""}>
            <span class="flow-toggle-track" aria-hidden="true">
              <span class="flow-toggle-text">${s.enabled ? "ON" : "OFF"}</span>
              <span class="flow-toggle-knob"></span>
            </span>
          </label>
          <span class="flow-section-spacer"></span>
          <button type="button" class="btn btn-ghost btn-sm flow-delete" data-flow-idx="${idx}">Remove</button>
        </div>
        <div class="flow-section-fields">
          <div class="form-grid-2">
            ${field("Section title", `<input class="input flow-title" data-flow-idx="${idx}" value="${esc(s.title)}">`)}
            ${field("Order", `<input class="input flow-order" type="number" min="1" data-flow-idx="${idx}" value="${s.order}">`)}
          </div>
          ${field("Content / instructions", `<textarea class="textarea flow-content" rows="4" data-flow-idx="${idx}">${esc(s.content)}</textarea>`)}
        </div>
      </div>
    `).join("");
  }

  function formatVersionDate(iso) {
    if (!iso) return "—";
    try {
      return new Date(iso).toLocaleString(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
    } catch {
      return iso;
    }
  }

  function versionSourceLabel(source) {
    const map = {
      ai_edit: "AI Edit",
      pre_ai_edit: "Before AI Edit",
      pre_restore: "Before Restore",
      restore: "Restored",
      manual: "Manual",
    };
    return map[source] || (source || "Snapshot").replace(/_/g, " ");
  }

  function renderVersionHistory() {
    if (detail.versionsLoading) {
      return `<div class="ai-edit-versions-empty text-muted">Loading version history…</div>`;
    }
    if (!detail.versions.length) {
      return `<div class="ai-edit-versions-empty text-muted">No versions yet. Apply an AI edit to create the first snapshot.</div>`;
    }
    return detail.versions.map((v) => `
      <article class="ai-edit-version-item glass-card" data-version-id="${esc(v.id)}">
        <div class="ai-edit-version-head">
          <span class="ai-edit-version-badge">${esc(versionSourceLabel(v.source))}</span>
          <time class="ai-edit-version-time">${esc(formatVersionDate(v.created_at))}</time>
        </div>
        <p class="ai-edit-version-summary">${esc(v.summary || v.prompt || "Configuration snapshot")}</p>
        ${v.prompt && v.summary ? `<p class="ai-edit-version-prompt text-muted">${esc(v.prompt.slice(0, 140))}${v.prompt.length > 140 ? "…" : ""}</p>` : ""}
        <button type="button" class="btn btn-ghost btn-sm ai-edit-restore-btn" data-version-id="${esc(v.id)}">Restore</button>
      </article>
    `).join("");
  }

  function renderAiEdit() {
    $("tab-ai-edit").innerHTML = `
      <div class="ai-edit-layout">
        <article class="ai-edit-main glass-card">
          <div class="ai-edit-head">
            <div>
              <h3 class="agent-form-title">AI Configuration Edit</h3>
              <p class="muted ai-edit-subtitle">Describe changes in plain language or paste facts, FAQs, pricing, or JSON. The assistant updates prompt, flows, call settings, and post-call config — with version history.</p>
            </div>
          </div>
          <label class="field ai-edit-prompt-field">
            <span>What should change?</span>
            <textarea class="textarea ai-edit-prompt" id="ai-edit-prompt" rows="10" placeholder="Examples:
• Add Hindi support and make the tone warmer
• Project: VK Realty — 2BHK from ₹85L at Hinjewadi, site visits Sat 10–6
• Increase silence timeout to 12 seconds
• Paste brochure facts or FAQ bullets here"></textarea>
          </label>
          <label class="check-row ai-edit-restart-row">
            <input type="checkbox" id="ai-edit-restart-live" checked>
            <span>Restart live voice worker if this agent is running</span>
          </label>
          <div class="ai-edit-actions">
            <button type="button" class="btn btn-secondary btn-sm" id="ai-edit-preview-btn">Preview</button>
            <button type="button" class="btn btn-primary btn-sm" id="ai-edit-apply-btn">Apply AI Edit</button>
          </div>
          <p id="ai-edit-status" class="ai-edit-status text-muted" aria-live="polite"></p>
        </article>
        <aside class="ai-edit-sidebar glass-card">
          <div class="ai-edit-sidebar-head">
            <h3 class="agent-form-title">Version History</h3>
            <button type="button" class="btn btn-ghost btn-sm" id="ai-edit-refresh-versions" title="Refresh history">↻</button>
          </div>
          <div id="ai-edit-versions-list" class="ai-edit-versions-list">${renderVersionHistory()}</div>
        </aside>
      </div>
    `;
    bindAiEditEvents();
    loadVersionHistory();
  }

  async function loadVersionHistory() {
    const id = agentId();
    if (!id) return;
    detail.versionsLoading = true;
    const list = $("ai-edit-versions-list");
    if (list) list.innerHTML = renderVersionHistory();
    try {
      const res = await apiJson(`/api/agents/${id}/versions`);
      detail.versions = res.data || res || [];
    } catch (e) {
      detail.versions = [];
      if (activeTab() === "ai-edit") {
        const status = $("ai-edit-status");
        if (status) status.textContent = e.message || "Could not load version history";
      }
    } finally {
      detail.versionsLoading = false;
      const listEl = $("ai-edit-versions-list");
      if (listEl && activeTab() === "ai-edit") {
        listEl.innerHTML = renderVersionHistory();
        bindAiEditVersionEvents();
      }
    }
  }

  async function runAiEdit(previewOnly) {
    const id = agentId();
    const prompt = $("ai-edit-prompt")?.value?.trim();
    if (!id) return;
    if (!prompt || prompt.length < 3) {
      showToast("Enter at least 3 characters describing the change", "error");
      return;
    }
    if (detail.aiApplying) return;

    detail.aiApplying = true;
    const applyBtn = $("ai-edit-apply-btn");
    const previewBtn = $("ai-edit-preview-btn");
    const status = $("ai-edit-status");
    const prevApply = applyBtn?.textContent;
    const prevPreview = previewBtn?.textContent;
    if (applyBtn) {
      applyBtn.disabled = true;
      applyBtn.textContent = previewOnly ? "Previewing…" : "Applying…";
    }
    if (previewBtn) previewBtn.disabled = true;
    if (status) status.textContent = previewOnly ? "Generating preview…" : "Applying AI edit…";

    try {
      const res = await apiJson(`/api/agents/${id}/ai-edit`, {
        method: "POST",
        body: JSON.stringify({
          prompt,
          preview_only: previewOnly,
          restart_if_live: $("ai-edit-restart-live")?.checked !== false,
        }),
      });
      const data = res.data || res;
      if (previewOnly) {
        const summary = data.summary || "Preview ready";
        if (status) status.textContent = `Preview: ${summary}`;
        showToast(summary, "info");
        return;
      }

      detail.config = data.config || detail.config;
      if (detail.config?.conversational_flow?.sections) {
        detail.flowSections = normalizeSections(detail.config.conversational_flow.sections);
      }
      const agent = detail.config?.agent || {};
      $("agent-detail-name").textContent = agent.name || "Agent";
      $("agent-detail-purpose").textContent = (agent.purpose || "general").replace(/_/g, " ");
      updateActivateButton();

      const summary = data.summary || "Configuration updated";
      if (status) status.textContent = summary;
      showToast(summary, "success");
      await loadVersionHistory();
    } catch (e) {
      if (status) status.textContent = e.message || "AI edit failed";
      showToast(e.message || "AI edit failed", "error");
    } finally {
      detail.aiApplying = false;
      if (applyBtn) {
        applyBtn.disabled = false;
        applyBtn.textContent = prevApply || "Apply AI Edit";
      }
      if (previewBtn) {
        previewBtn.disabled = false;
        previewBtn.textContent = prevPreview || "Preview";
      }
    }
  }

  async function restoreVersion(versionId) {
    const id = agentId();
    if (!id || !versionId || detail.aiApplying) return;
    if (!window.confirm("Restore this version? Current settings will be saved to history first.")) return;

    detail.aiApplying = true;
    const status = $("ai-edit-status");
    if (status) status.textContent = "Restoring version…";

    try {
      const res = await apiJson(`/api/agents/${id}/versions/${versionId}/restore`, {
        method: "POST",
        body: JSON.stringify({
          restart_if_live: $("ai-edit-restart-live")?.checked !== false,
        }),
      });
      const data = res.data || res;
      detail.config = data.config || detail.config;
      if (detail.config?.conversational_flow?.sections) {
        detail.flowSections = normalizeSections(detail.config.conversational_flow.sections);
      }
      const agent = detail.config?.agent || {};
      $("agent-detail-name").textContent = agent.name || "Agent";
      $("agent-detail-purpose").textContent = (agent.purpose || "general").replace(/_/g, " ");
      updateActivateButton();
      if (status) status.textContent = "Version restored";
      showToast("Configuration restored", "success");
      await loadVersionHistory();
      renderActiveTab();
    } catch (e) {
      if (status) status.textContent = e.message || "Restore failed";
      showToast(e.message || "Restore failed", "error");
    } finally {
      detail.aiApplying = false;
    }
  }

  function bindAiEditVersionEvents() {
    document.querySelectorAll(".ai-edit-restore-btn").forEach((btn) => {
      btn.addEventListener("click", () => restoreVersion(btn.dataset.versionId));
    });
  }

  function bindAiEditEvents() {
    $("ai-edit-preview-btn")?.addEventListener("click", () => runAiEdit(true));
    $("ai-edit-apply-btn")?.addEventListener("click", () => runAiEdit(false));
    $("ai-edit-refresh-versions")?.addEventListener("click", loadVersionHistory);
    bindAiEditVersionEvents();
  }

  function renderAssistantDetails() {
    const agent = detail.config?.agent || {};
    const langs = new Set(agent.languages || ["en"]);
    const langChips = LANG_OPTIONS.map((l) => `
      <button type="button" class="chip lang-chip ${langs.has(l) ? "selected" : ""}" data-lang="${l}">${l.toUpperCase()}</button>
    `).join("");

    $("tab-assistant-details").innerHTML = `
      <div class="agent-form-stack">
        <article class="agent-form-card glass-card">
          <h3 class="agent-form-title">Basic Information</h3>
          <div class="form-grid-2">
            ${field("Name", `<input class="input" id="ad-name" value="${esc(agent.name)}">`)}
            ${field("Purpose", `<input class="input" id="ad-purpose" value="${esc(agent.purpose || "")}" placeholder="admissions, support, …">`)}
          </div>
          ${field("Description", `<input class="input" id="ad-description" value="${esc(agent.description || "")}">`)}
          <div class="lang-picker">
            <span class="use-case-label">Languages</span>
            <div class="chip-group" id="ad-languages">${langChips}</div>
          </div>
          ${field("Welcome message", `<textarea class="textarea" id="ad-welcome" rows="2">${esc(agent.welcome_message || "")}</textarea>`)}
          ${field("System prompt", `<textarea class="textarea" id="ad-system-prompt" rows="8">${esc(agent.system_prompt || "")}</textarea>`)}
        </article>

        <article class="agent-form-card glass-card">
          <h3 class="agent-form-title">AI Model &amp; Voice</h3>
          <div class="form-grid-2">
            ${selectField("LLM provider", "ad-llm-provider", LLM_PROVIDERS, agent.llm_provider)}
            ${field("LLM model", `<input class="input" id="ad-llm-model" value="${esc(agent.llm_model || "")}">`)}
            ${selectField("STT provider", "ad-stt-provider", STT_PROVIDERS, agent.stt_provider)}
            ${field("STT model", `<input class="input" id="ad-stt-model" value="${esc(agent.stt_model || "")}">`)}
            ${selectField("TTS provider", "ad-tts-provider", TTS_PROVIDERS, agent.tts_provider)}
            ${selectField("TTS voice", "ad-tts-voice", TTS_VOICES, agent.tts_voice_id)}
            ${field("Temperature", `<input class="input" type="number" step="0.05" min="0" max="2" id="ad-temperature" value="${agent.temperature ?? 0.7}">`)}
            ${field("Max call duration (sec)", `<input class="input" type="number" min="60" id="ad-max-duration" value="${agent.max_call_duration_seconds ?? 1200}">`)}
          </div>
          <label class="check-row">
            <input type="checkbox" id="ad-is-active" ${agent.is_active !== false ? "checked" : ""}>
            <span>Assistant is active</span>
          </label>
        </article>

        <article class="agent-form-card glass-card">
          <div class="agent-form-title-row">
            <h3 class="agent-form-title">Conversational Flow</h3>
            <button type="button" class="btn btn-secondary btn-sm" id="flow-add-section">+ Add Section</button>
          </div>
          <div id="flow-sections-list" class="flow-sections-list">${renderFlowSectionsList()}</div>
        </article>
      </div>
    `;
    bindFlowSectionEvents();
  }

  function cfg(path, fallback = {}) {
    const cc = detail.config?.call_configuration || {};
    const parts = path.split(".");
    let cur = cc;
    for (const p of parts) {
      cur = cur?.[p];
    }
    return cur == null ? fallback : cur;
  }

  function renderCallConfiguration() {
    const sh = cfg("silence_handling", {});
    const ec = cfg("end_call_rules", {});
    const tr = cfg("transfer_routing", {});
    const rb = cfg("response_behavior", {});
    const am = cfg("ambient_sound", {});
    const adv = cfg("advanced_settings", {});

    $("tab-call-configuration").innerHTML = `
      <div class="agent-form-stack">
        <details class="config-accordion glass-card" open>
          <summary>Silence Handling</summary>
          <div class="accordion-body">
            ${checkboxField("cc-sh-enabled", "Enable silence prompts", sh.enabled !== false)}
            ${field("Timeout (seconds)", `<input class="input" type="number" id="cc-sh-timeout" value="${sh.timeout_seconds ?? 8}">`)}
            ${field("Max prompts", `<input class="input" type="number" id="cc-sh-max" value="${sh.max_prompts ?? 2}">`)}
            ${field("Prompt message", `<input class="input" id="cc-sh-message" value="${esc(sh.prompt_message || "Are you still there?")}">`)}
            ${checkboxField("cc-sh-end", "End call after max prompts", Boolean(sh.end_call_after_max_prompts))}
          </div>
        </details>

        <details class="config-accordion glass-card" open>
          <summary>End Call Rules</summary>
          <div class="accordion-body">
            ${field("Max duration (seconds)", `<input class="input" type="number" id="cc-ec-max" value="${ec.max_duration_seconds ?? 1200}">`)}
            ${field("End phrases (comma-separated)", `<input class="input" id="cc-ec-phrases" value="${esc((ec.end_phrases || []).join(", "))}">`)}
            ${checkboxField("cc-ec-vm", "Voicemail detection", ec.voicemail_detection !== false)}
            ${field("Voicemail message", `<textarea class="textarea" id="cc-ec-vm-msg" rows="2">${esc(ec.voicemail_message || "")}</textarea>`)}
          </div>
        </details>

        <details class="config-accordion glass-card">
          <summary>Transfer &amp; Routing</summary>
          <div class="accordion-body">
            ${checkboxField("cc-tr-enabled", "Enable transfer", Boolean(tr.enabled))}
            ${field("Transfer number", `<input class="input" id="cc-tr-number" value="${esc(tr.transfer_number || "")}">`)}
            ${field("Transfer phrases", `<input class="input" id="cc-tr-phrases" value="${esc((tr.transfer_phrases || []).join(", "))}">`)}
            ${checkboxField("cc-tr-warm", "Warm transfer", Boolean(tr.warm_transfer))}
          </div>
        </details>

        <details class="config-accordion glass-card">
          <summary>Response Behavior</summary>
          <div class="accordion-body">
            ${selectField("Interruption sensitivity", "cc-rb-sensitivity", ["low", "medium", "high"], rb.interruption_sensitivity || "medium")}
            ${field("Max response sentences", `<input class="input" type="number" id="cc-rb-sentences" value="${rb.max_response_sentences ?? 3}">`)}
            ${checkboxField("cc-rb-filler", "Filler phrases", rb.filler_phrases_enabled !== false)}
            ${checkboxField("cc-rb-backchannel", "Backchannel", rb.backchannel_enabled !== false)}
          </div>
        </details>

        <details class="config-accordion glass-card" ${am.enabled ? "open" : ""}>
          <summary>Ambient Sound</summary>
          <div class="accordion-body">
            ${checkboxField("cc-am-enabled", "Enable ambient sound", Boolean(am.enabled))}
            ${selectField("Sound", "cc-am-type", AMBIENT_SOUND_OPTIONS, am.sound_type || "cicd_1")}
            ${field("Volume (0.08–0.18)", `<input class="input" type="number" step="0.01" min="0.08" max="0.18" id="cc-am-volume" value="${am.volume ?? 0.12}">`)}
            <p class="muted">Subtle background loop — keep volume low so TTS stays clear.</p>
          </div>
        </details>

        <details class="config-accordion glass-card">
          <summary>Advanced</summary>
          <div class="accordion-body">
            ${checkboxField("cc-adv-record", "Record calls", adv.record_calls !== false)}
            ${checkboxField("cc-adv-noise", "Noise cancellation", adv.noise_cancellation !== false)}
            ${checkboxField("cc-adv-lang", "Language auto-detect", adv.language_auto_detect !== false)}
          </div>
        </details>
      </div>
    `;
  }

  function formatKbFileSize(bytes) {
    if (!bytes) return "";
    if (bytes < 1024) return `${bytes} B`;
    if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
    return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  }

  function renderKbFileList(files) {
    if (!files.length) {
      return `
        <div class="kb-empty-state" id="kb-file-list">
          <span class="kb-empty-icon" aria-hidden="true">📄</span>
          <p>No documents uploaded yet</p>
          <span class="muted">Files appear here after you upload</span>
        </div>
      `;
    }
    return `
      <ul class="kb-file-list" id="kb-file-list">
        ${files.map((f, i) => {
          const name = f.name || f;
          const size = f.size ? formatKbFileSize(f.size) : "";
          return `
            <li class="kb-file-item">
              <div class="kb-file-icon" aria-hidden="true">📄</div>
              <div class="kb-file-meta">
                <span class="kb-file-name">${esc(name)}</span>
                ${size ? `<span class="kb-file-size muted">${esc(size)}</span>` : ""}
              </div>
              <button type="button" class="btn btn-ghost btn-sm kb-remove" data-kb-idx="${i}">Remove</button>
            </li>
          `;
        }).join("")}
      </ul>
    `;
  }

  function renderKnowledgeBase() {
    const agent = detail.config?.agent || {};
    const meta = agent.metadata || {};
    const files = meta.knowledge_base_files_list || [];
    const searchOn = Boolean(meta.search_enabled);

    $("tab-knowledge-base").innerHTML = `
      <div class="agent-form-stack">
        <article class="agent-form-card glass-card">
          <h3 class="agent-form-title">Knowledge Base Files</h3>
          <p class="agent-form-sub muted">Upload documents to ground this assistant. Files are tracked in agent metadata (full RAG in a future step).</p>

          <label class="check-row kb-search-row">
            <input type="checkbox" id="kb-search-enabled" ${searchOn ? "checked" : ""}>
            <span>Enable knowledge search during calls</span>
          </label>

          <div id="kb-dropzone" class="kb-dropzone" tabindex="0" role="button" aria-label="Upload knowledge base files">
            <input type="file" id="kb-file-input" class="kb-file-input" multiple accept=".pdf,.txt,.md,.doc,.docx">
            <div class="kb-dropzone-icon" aria-hidden="true">↑</div>
            <div class="kb-dropzone-title">Drag &amp; drop documents here</div>
            <p class="kb-dropzone-hint muted">or <label for="kb-file-input" class="kb-browse-link">browse files</label></p>
            <p class="kb-dropzone-meta muted">PDF, TXT, MD, DOC, DOCX</p>
          </div>

          ${renderKbFileList(files)}
        </article>
      </div>
    `;
    bindKnowledgeEvents();
  }

  function renderIntegrations() {
    const meta = detail.config?.agent?.metadata || {};
    const integ = meta.integrations || {};

    $("tab-integrations").innerHTML = `
      <article class="agent-form-card glass-card">
        <h3 class="agent-form-title">Integrations</h3>
        <p class="text-muted">Connect external services for this assistant.</p>
        <div class="form-grid-2">
          ${checkboxField("integ-calendar", "Google / Cal.com calendar booking", Boolean(integ.calendar_enabled))}
          ${checkboxField("integ-crm", "CRM sync after calls", Boolean(integ.crm_enabled))}
          ${checkboxField("integ-webhook", "Outbound webhook on events", Boolean(integ.webhook_enabled))}
          ${field("Webhook URL", `<input class="input" id="integ-webhook-url" value="${esc(integ.webhook_url || "")}" placeholder="https://...">`)}
          ${field("CRM type", `<input class="input" id="integ-crm-type" value="${esc(integ.crm_type || "none")}" placeholder="none, hubspot, salesforce, webhook">`)}
        </div>
      </article>
    `;
  }

  function renderExtractedVariables(vars) {
    const rows = vars || [];
    if (!rows.length) {
      return `
        <div class="extracted-vars-empty" id="pc-vars-empty">
          <p>No extracted variables yet</p>
          <span class="muted">Add fields to capture from each call — name, interest, booking intent, etc.</span>
        </div>
      `;
    }
    return `
      <div class="extracted-vars-list">
        ${rows.map((v, i) => `
          <div class="extracted-var-card" data-pc-idx="${i}">
            <div class="extracted-var-header">
              <span class="extracted-var-index">Variable ${i + 1}</span>
              <button type="button" class="btn btn-ghost btn-sm pc-var-remove" data-pc-idx="${i}">Remove</button>
            </div>
            <div class="extracted-var-fields">
              <div class="form-grid-2">
                ${field("Name", `<input class="input pc-var-name" data-pc-idx="${i}" value="${esc(v.name || "")}" placeholder="caller_name">`)}
                ${field("Description", `<input class="input pc-var-desc" data-pc-idx="${i}" value="${esc(v.description || "")}" placeholder="What this field represents">`)}
              </div>
              ${field("How to extract", `<textarea class="textarea pc-var-how" rows="2" data-pc-idx="${i}" placeholder="Instructions for the AI — e.g. from conversation when they introduce themselves">${esc(v.how_to_extract || "")}</textarea>`)}
            </div>
          </div>
        `).join("")}
      </div>
    `;
  }

  function renderPostCall() {
    const pc = detail.config?.post_call_action || {};
    const triggers = new Set(pc.trigger_statuses || ["completed"]);

    $("tab-post-call").innerHTML = `
      <div class="agent-form-stack">
        <article class="agent-form-card glass-card">
          <h3 class="agent-form-title">Post-Call Delivery</h3>
          <p class="agent-form-sub muted">Configure how call summaries and extracted data are sent after each call ends.</p>
          <div class="form-grid-2">
            ${selectField("Delivery method", "pc-delivery", DELIVERY_METHODS, pc.delivery_method || "webhook")}
            ${field("Webhook URL", `<input class="input" id="pc-webhook-url" value="${esc(pc.webhook_url || "")}" placeholder="https://your-app.com/webhook">`)}
            ${field("Email recipients (comma-separated)", `<input class="input" id="pc-emails" value="${esc((pc.email_recipients || []).join(", "))}" placeholder="ops@company.com, sales@company.com">`)}
          </div>

          <div class="pc-form-section">
            <span class="use-case-label">Trigger on call status</span>
            <div class="chip-group" id="pc-triggers">
              ${TRIGGER_STATUSES.map((s) => `
                <button type="button" class="chip pc-trigger-chip ${triggers.has(s) ? "selected" : ""}" data-status="${s}">${esc(s)}</button>
              `).join("")}
            </div>
          </div>

          <div class="pc-form-section">
            <span class="use-case-label">Include in delivery</span>
            <div class="check-grid pc-include-grid">
              ${checkboxField("pc-inc-summary", "Summary", pc.include_summary !== false)}
              ${checkboxField("pc-inc-transcript", "Full conversation", Boolean(pc.include_full_transcript))}
              ${checkboxField("pc-inc-sentiment", "Sentiment", pc.include_sentiment !== false)}
              ${checkboxField("pc-inc-extracted", "Extracted information", pc.include_extracted_info !== false)}
            </div>
          </div>
        </article>

        <article class="agent-form-card glass-card">
          <div class="agent-form-title-row">
            <div>
              <h3 class="agent-form-title">Extracted Variables</h3>
              <p class="agent-form-sub muted">Define fields to pull from conversations automatically.</p>
            </div>
            <button type="button" class="btn btn-secondary btn-sm" id="pc-add-var">+ Add Variable</button>
          </div>
          <div id="pc-vars-list">${renderExtractedVariables(pc.extracted_variables)}</div>
        </article>
      </div>
    `;
    bindPostCallEvents();
  }

  async function renderRecentCalls() {
    $("tab-recent-calls").innerHTML = `<div class="agent-loading glass-card"><span class="pulse-dot"></span> Loading calls…</div>`;
    try {
      const res = await apiJson(`/api/agents/${agentId()}/calls?limit=50`);
      detail.agentCalls = res.data || [];
    } catch (e) {
      detail.agentCalls = [];
    }

    const rows = detail.agentCalls;
    $("tab-recent-calls").innerHTML = `
      <article class="agent-form-card glass-card">
        <div class="panel-head">
          <h3 class="agent-form-title">Recent Calls</h3>
          <span class="text-muted">${rows.length} call${rows.length === 1 ? "" : "s"}</span>
        </div>
        <div class="table-wrap">
          <table class="table call-logs-table">
            <thead>
              <tr>
                <th>Date</th><th>From</th><th>To</th><th>Duration</th><th>Status</th><th>Summary</th>
              </tr>
            </thead>
            <tbody id="agent-recent-calls-body">
              ${rows.length ? rows.map((r) => `
                <tr class="call-log-row" data-call-id="${esc(r.id)}">
                  <td>${safeDate(r.created_at)}</td>
                  <td>${displayPhone(r.from_number || r.phone_number)}</td>
                  <td>${displayPhone(r.to_number)}</td>
                  <td>${r.duration_seconds || 0}s</td>
                  <td>${tag(r.call_status || "completed", "ok")}</td>
                  <td>${esc(humanizeSummary(r.call_summary || r.summary).slice(0, 100))}</td>
                </tr>
              `).join("") : `<tr><td colspan="6">No calls linked to this assistant yet.</td></tr>`}
            </tbody>
          </table>
        </div>
      </article>
    `;
    $("agent-recent-calls-body")?.addEventListener("click", (e) => {
      const row = e.target.closest(".call-log-row");
      if (!row?.dataset.callId) return;
      if (window.CallLogs?.openDetail) window.CallLogs.openDetail(row.dataset.callId);
    });
  }

  function readLanguagesFromDom() {
    const langs = [];
    document.querySelectorAll("#ad-languages .lang-chip.selected").forEach((c) => langs.push(c.dataset.lang));
    return langs.length ? langs : ["en"];
  }

  function readFlowSectionsFromDom() {
    return detail.flowSections.map((s, idx) => {
      const title = document.querySelector(`.flow-title[data-flow-idx="${idx}"]`);
      const content = document.querySelector(`.flow-content[data-flow-idx="${idx}"]`);
      const order = document.querySelector(`.flow-order[data-flow-idx="${idx}"]`);
      const enabled = document.querySelector(`.flow-enabled[data-flow-idx="${idx}"]`);
      return {
        id: s.id,
        title: title?.value?.trim() || s.title,
        content: content?.value?.trim() || "",
        enabled: enabled?.checked !== false,
        order: Number(order?.value) || idx + 1,
      };
    }).sort((a, b) => a.order - b.order);
  }

  function splitCsv(value) {
    return String(value || "").split(",").map((s) => s.trim()).filter(Boolean);
  }

  async function saveAssistantDetails() {
    const id = agentId();
    const payload = {
      name: $("ad-name")?.value?.trim(),
      purpose: $("ad-purpose")?.value?.trim(),
      description: $("ad-description")?.value?.trim(),
      languages: readLanguagesFromDom(),
      welcome_message: $("ad-welcome")?.value?.trim(),
      system_prompt: $("ad-system-prompt")?.value?.trim() || "",
      llm_provider: $("ad-llm-provider")?.value,
      llm_model: $("ad-llm-model")?.value?.trim(),
      stt_provider: $("ad-stt-provider")?.value,
      stt_model: $("ad-stt-model")?.value?.trim() || null,
      tts_provider: $("ad-tts-provider")?.value,
      tts_voice_id: $("ad-tts-voice")?.value,
      temperature: Number($("ad-temperature")?.value) || 0.7,
      max_call_duration_seconds: Number($("ad-max-duration")?.value) || 1200,
      is_active: $("ad-is-active")?.checked !== false,
    };

    const sections = readFlowSectionsFromDom();
    await apiJson(`/api/agents/${id}`, { method: "PUT", body: JSON.stringify(payload) });
    await apiJson(`/api/agents/${id}/conversational-flow`, {
      method: "PUT",
      body: JSON.stringify({ sections }),
    });
    detail.flowSections = normalizeSections(sections);
  }

  async function saveCallConfiguration() {
    const body = {
      silence_handling: {
        enabled: $("cc-sh-enabled")?.checked !== false,
        timeout_seconds: Number($("cc-sh-timeout")?.value) || 8,
        max_prompts: Number($("cc-sh-max")?.value) || 2,
        prompt_message: $("cc-sh-message")?.value || "",
        end_call_after_max_prompts: $("cc-sh-end")?.checked === true,
      },
      end_call_rules: {
        max_duration_seconds: Number($("cc-ec-max")?.value) || 1200,
        end_phrases: splitCsv($("cc-ec-phrases")?.value),
        voicemail_detection: $("cc-ec-vm")?.checked !== false,
        voicemail_message: $("cc-ec-vm-msg")?.value || "",
      },
      transfer_routing: {
        enabled: $("cc-tr-enabled")?.checked === true,
        transfer_number: $("cc-tr-number")?.value || "",
        transfer_phrases: splitCsv($("cc-tr-phrases")?.value),
        warm_transfer: $("cc-tr-warm")?.checked === true,
      },
      response_behavior: {
        interruption_sensitivity: $("cc-rb-sensitivity")?.value || "medium",
        max_response_sentences: Number($("cc-rb-sentences")?.value) || 3,
        filler_phrases_enabled: $("cc-rb-filler")?.checked !== false,
        backchannel_enabled: $("cc-rb-backchannel")?.checked !== false,
      },
      ambient_sound: {
        enabled: $("cc-am-enabled")?.checked === true,
        sound_type: $("cc-am-type")?.value || "cicd_1",
        volume: Math.min(0.18, Math.max(0.08, Number($("cc-am-volume")?.value) || 0.12)),
      },
      advanced_settings: {
        record_calls: $("cc-adv-record")?.checked !== false,
        noise_cancellation: $("cc-adv-noise")?.checked !== false,
        language_auto_detect: $("cc-adv-lang")?.checked !== false,
      },
    };
    await apiJson(`/api/agents/${agentId()}/call-configuration`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }

  function getKbFilesList() {
    const meta = detail.config?.agent?.metadata || {};
    return [...(meta.knowledge_base_files_list || [])];
  }

  async function saveKnowledgeBase() {
    const files = getKbFilesList();
    const searchEnabled = $("kb-search-enabled")?.checked === true;
    const meta = deepClone(detail.config?.agent?.metadata || {});
    meta.knowledge_base_files_list = files;
    meta.knowledge_base_files = files.length;
    meta.search_enabled = searchEnabled;
    await apiJson(`/api/agents/${agentId()}`, {
      method: "PUT",
      body: JSON.stringify({ metadata: meta }),
    });
    if (detail.config?.agent) detail.config.agent.metadata = meta;
  }

  async function saveIntegrations() {
    const meta = deepClone(detail.config?.agent?.metadata || {});
    meta.integrations = {
      calendar_enabled: $("integ-calendar")?.checked === true,
      crm_enabled: $("integ-crm")?.checked === true,
      webhook_enabled: $("integ-webhook")?.checked === true,
      webhook_url: $("integ-webhook-url")?.value?.trim() || "",
      crm_type: $("integ-crm-type")?.value?.trim() || "none",
    };
    await apiJson(`/api/agents/${agentId()}`, {
      method: "PUT",
      body: JSON.stringify({ metadata: meta }),
    });
    if (detail.config?.agent) detail.config.agent.metadata = meta;
  }

  function readExtractedVariables() {
    const rows = [];
    document.querySelectorAll(".extracted-var-card").forEach((row) => {
      const idx = row.dataset.pcIdx;
      rows.push({
        name: document.querySelector(`.pc-var-name[data-pc-idx="${idx}"]`)?.value?.trim() || "",
        description: document.querySelector(`.pc-var-desc[data-pc-idx="${idx}"]`)?.value?.trim() || "",
        how_to_extract: document.querySelector(`.pc-var-how[data-pc-idx="${idx}"]`)?.value?.trim() || "",
      });
    });
    return rows.filter((r) => r.name);
  }

  async function savePostCall() {
    const triggers = [];
    document.querySelectorAll("#pc-triggers .pc-trigger-chip.selected").forEach((c) => {
      triggers.push(c.dataset.status);
    });
    const body = {
      delivery_method: $("pc-delivery")?.value || "webhook",
      webhook_url: $("pc-webhook-url")?.value?.trim() || null,
      email_recipients: splitCsv($("pc-emails")?.value),
      trigger_statuses: triggers.length ? triggers : ["completed"],
      include_summary: $("pc-inc-summary")?.checked !== false,
      include_full_transcript: $("pc-inc-transcript")?.checked === true,
      include_sentiment: $("pc-inc-sentiment")?.checked !== false,
      include_extracted_info: $("pc-inc-extracted")?.checked !== false,
      extracted_variables: readExtractedVariables(),
    };
    await apiJson(`/api/agents/${agentId()}/post-call-actions`, {
      method: "PUT",
      body: JSON.stringify(body),
    });
  }

  async function saveCurrentTab() {
    if (!agentId() || detail.saving) return;
    detail.saving = true;
    const btn = $("agent-save-btn");
    const prev = btn?.textContent;
    if (btn) {
      btn.disabled = true;
      btn.textContent = "Saving…";
    }
    try {
      const tab = activeTab();
      if (tab === "assistant-details") await saveAssistantDetails();
      else if (tab === "call-configuration") await saveCallConfiguration();
      else if (tab === "knowledge-base") await saveKnowledgeBase();
      else if (tab === "integrations") await saveIntegrations();
      else if (tab === "post-call") await savePostCall();
      else if (tab === "ai-edit") {
        showToast("Use Apply AI Edit on the AI Edit tab", "info");
        return;
      }
      else if (tab === "recent-calls") {
        showToast("Recent calls are read-only", "info");
        return;
      }
      await loadAgent(agentId());
      showToast("Changes saved", "success");
    } catch (e) {
      showToast(e.message || "Save failed", "error");
    } finally {
      detail.saving = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = prev || "Save Changes";
      }
    }
  }

  function bindFlowSectionEvents() {
    $("flow-add-section")?.addEventListener("click", () => {
      const n = detail.flowSections.length + 1;
      detail.flowSections.push({
        id: `section_${Date.now()}`,
        title: `New Section ${n}`,
        content: "",
        enabled: true,
        order: n,
      });
      $("flow-sections-list").innerHTML = renderFlowSectionsList();
      bindFlowSectionEvents();
    });

    document.querySelectorAll(".flow-delete").forEach((btn) => {
      btn.addEventListener("click", () => {
        const idx = Number(btn.dataset.flowIdx);
        detail.flowSections.splice(idx, 1);
        detail.flowSections.forEach((s, i) => { s.order = i + 1; });
        $("flow-sections-list").innerHTML = renderFlowSectionsList();
        bindFlowSectionEvents();
      });
    });

    document.querySelectorAll(".flow-enabled").forEach((input) => {
      input.addEventListener("change", () => {
        const toggle = input.closest(".flow-toggle");
        const text = toggle?.querySelector(".flow-toggle-text");
        if (text) text.textContent = input.checked ? "ON" : "OFF";
        if (toggle) toggle.title = input.checked ? "Section enabled" : "Section disabled";
      });
    });

    document.querySelectorAll("#ad-languages .lang-chip").forEach((chip) => {
      chip.addEventListener("click", () => chip.classList.toggle("selected"));
    });

    setupFlowDragDrop();
  }

  function setupFlowDragDrop() {
    const list = $("flow-sections-list");
    if (!list) return;
    let dragIdx = null;

    list.querySelectorAll(".flow-section-item").forEach((item) => {
      item.addEventListener("dragstart", (e) => {
        dragIdx = Number(item.dataset.flowIdx);
        item.classList.add("dragging");
        e.dataTransfer.effectAllowed = "move";
      });
      item.addEventListener("dragend", () => {
        item.classList.remove("dragging");
        dragIdx = null;
      });
      item.addEventListener("dragover", (e) => {
        e.preventDefault();
        e.dataTransfer.dropEffect = "move";
      });
      item.addEventListener("drop", (e) => {
        e.preventDefault();
        const targetIdx = Number(item.dataset.flowIdx);
        if (dragIdx == null || dragIdx === targetIdx) return;
        const moved = detail.flowSections.splice(dragIdx, 1)[0];
        detail.flowSections.splice(targetIdx, 0, moved);
        detail.flowSections.forEach((s, i) => { s.order = i + 1; });
        $("flow-sections-list").innerHTML = renderFlowSectionsList();
        bindFlowSectionEvents();
      });
    });
  }

  function addKbFiles(fileList) {
    const incoming = Array.from(fileList || []);
    if (!incoming.length) return;
    const meta = deepClone(detail.config?.agent?.metadata || {});
    const files = [...(meta.knowledge_base_files_list || [])];
    incoming.forEach((f) => {
      files.push({ name: f.name, size: f.size, added_at: new Date().toISOString() });
    });
    meta.knowledge_base_files_list = files;
    if (detail.config?.agent) detail.config.agent.metadata = meta;
    renderKnowledgeBase();
  }

  function bindKnowledgeEvents() {
    const dropzone = $("kb-dropzone");
    const fileInput = $("kb-file-input");

    dropzone?.addEventListener("click", (e) => {
      if (e.target.closest(".kb-browse-link, .kb-file-input")) return;
      fileInput?.click();
    });

    fileInput?.addEventListener("change", () => {
      addKbFiles(fileInput.files);
      fileInput.value = "";
    });

    dropzone?.addEventListener("dragover", (e) => {
      e.preventDefault();
      dropzone.classList.add("is-dragover");
    });

    dropzone?.addEventListener("dragleave", () => {
      dropzone.classList.remove("is-dragover");
    });

    dropzone?.addEventListener("drop", (e) => {
      e.preventDefault();
      dropzone.classList.remove("is-dragover");
      addKbFiles(e.dataTransfer?.files);
    });

    document.querySelectorAll(".kb-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        const meta = deepClone(detail.config?.agent?.metadata || {});
        const files = [...(meta.knowledge_base_files_list || [])];
        files.splice(Number(btn.dataset.kbIdx), 1);
        meta.knowledge_base_files_list = files;
        if (detail.config?.agent) detail.config.agent.metadata = meta;
        renderKnowledgeBase();
      });
    });
  }

  function bindPostCallEvents() {
    document.querySelectorAll(".pc-trigger-chip").forEach((chip) => {
      chip.addEventListener("click", () => chip.classList.toggle("selected"));
    });

    $("pc-add-var")?.addEventListener("click", () => {
      const pc = detail.config?.post_call_action || {};
      const vars = [...(pc.extracted_variables || []), { name: "", description: "", how_to_extract: "" }];
      if (!detail.config.post_call_action) detail.config.post_call_action = {};
      detail.config.post_call_action.extracted_variables = vars;
      $("pc-vars-list").innerHTML = renderExtractedVariables(vars);
      bindPostCallEvents();
    });

    document.querySelectorAll(".pc-var-remove").forEach((btn) => {
      btn.addEventListener("click", () => {
        const pc = detail.config?.post_call_action || {};
        const vars = [...(pc.extracted_variables || [])];
        vars.splice(Number(btn.dataset.pcIdx), 1);
        detail.config.post_call_action.extracted_variables = vars;
        $("pc-vars-list").innerHTML = renderExtractedVariables(vars);
        bindPostCallEvents();
      });
    });
  }

  async function onTabChange(tabId) {
    if (!detail.config) return;
    if (tabId === "recent-calls") await renderRecentCalls();
    else if (tabId === "ai-edit") renderAiEdit();
    else renderActiveTab();
  }

  function updateActivateButton() {
    const btn = $("agent-activate-btn");
    const agent = detail.config?.agent;
    if (!btn || !agent) return;
    const active = agent.is_active !== false;
    btn.disabled = false;
    btn.textContent = active ? "▶ Run Again" : "▶ Run Agent";
    btn.classList.add("btn-primary");
    btn.classList.remove("btn-secondary");
    btn.title = "Pause all other agents, then run this agent live with its saved preset";
  }

  function init() {
    $("agent-activate-btn")?.addEventListener("click", async () => {
      const agent = detail.config?.agent || {};
      const id = agentId();
      const runFn = window.runAgent || window.activateAgent;
      if (!id || typeof runFn !== "function") {
        showToast("Run agent unavailable — reload the dashboard", "error");
        return;
      }
      const btn = $("agent-activate-btn");
      try {
        await runFn(id, agent.name, btn);
        await loadAgent(id);
      } finally {
        updateActivateButton();
      }
    });
    $("agent-save-btn")?.addEventListener("click", saveCurrentTab);
    $("agent-test-call-btn")?.addEventListener("click", () => {
      const agent = detail.config?.agent || {};
      if (typeof window.openTestCallModal === "function") {
        window.openTestCallModal(agentId(), agent.name || "Agent");
      } else {
        showToast("Test call unavailable — reload the dashboard", "error");
      }
    });
    $("agent-delete-btn")?.addEventListener("click", async () => {
      const agent = detail.config?.agent || {};
      const id = agentId();
      if (!id || typeof window.deleteAgent !== "function") {
        showToast("Delete unavailable — reload the dashboard", "error");
        return;
      }
      await window.deleteAgent(id, agent.name || "Agent");
    });
  }

  window.AgentDetail = {
    load: loadAgent,
    onTabChange,
    saveCurrentTab,
    init,
  };

  window.addEventListener("DOMContentLoaded", init);
})();