/**
 * Assistant Settings — card UI + modals (main agent / config.json).
 */
(function assistantSettingsModule() {
  const state = {
    settings: null,
    catalog: null,
    saving: false,
    dirty: false,
  };

  function esc(t) {
    return String(t ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function $(id) {
    return document.getElementById(id);
  }

  function setStatus(text, tone = "muted") {
    const el = $("as-status");
    if (!el) return;
    el.className = `main-agent-status main-agent-status--${tone}`;
    el.textContent = text;
  }

  function langLabel(code) {
    const c = state.catalog?.languages?.find((l) => l.code === code);
    return c?.label || code;
  }

  function findVoiceInCatalog(voiceId) {
    const vid = String(voiceId || "").trim();
    if (!vid) return null;
    const voices = state.catalog?.voices || {};
    for (const [tab, list] of Object.entries(voices)) {
      const hit = (list || []).find((v) => v.id === vid);
      if (hit) return { ...hit, tab };
    }
    return null;
  }

  function voiceLabel() {
    const v = state.settings?.voice;
    if (!v) return "—";
    if (v.display_name && v.display_name !== v.voice_id) return v.display_name;
    const hit = findVoiceInCatalog(v.voice_id);
    return hit?.name || v.voice_id || "—";
  }

  function ambientLabel() {
    const a = state.settings?.ambient_sound;
    if (!a?.enabled) return "Off";
    const opt = state.catalog?.ambient_sounds?.find((s) => s.id === a.sound_type);
    return opt?.label || a.sound_type || "Office ambience";
  }

  const MODAL_META = {
    languages: {
      mark: "Lang",
      markClass: "as-modal-mark--lang",
      title: "Languages",
      subtitle: "Languages your agent understands and responds in",
      hint: "At least one language must stay enabled.",
      confirm: "Save languages",
      picker: true,
    },
    voice: {
      mark: "TTS",
      markClass: "as-modal-mark--voice",
      title: "Voice",
      subtitle: "How Arjun sounds on live calls — Cartesia, Gemini, Sarvam",
      hint: "Double-click a voice to save and close instantly.",
      confirm: "Use this voice",
      picker: true,
    },
    llm: {
      mark: "LLM",
      markClass: "as-modal-mark--llm",
      title: "AI model",
      subtitle: "Conversation quality, speed, and creativity",
      hint: "Lower temperature = more consistent replies.",
      confirm: "Save model",
      picker: false,
    },
    stt: {
      mark: "STT",
      markClass: "as-modal-mark--stt",
      title: "Transcription",
      subtitle: "Speech-to-text for caller audio",
      hint: "Silence timeout controls how quickly turns end.",
      confirm: "Save transcription",
      picker: false,
    },
    advanced: {
      mark: "Adv",
      markClass: "as-modal-mark--adv",
      title: "Advanced",
      subtitle: "Interruption, VAD, turn-taking, and ambient sound",
      hint: "Tune barge-in, endpointing, and background ambience (volume 0.08–0.18).",
      confirm: "Save advanced",
      picker: false,
    },
  };

  let _modalCloseTimer = null;
  let _activeModalKind = null;

  function syncWelcomeToggleRows() {
    ["as-welcome-dynamic", "as-welcome-interrupt"].forEach((id) => {
      const input = $(id);
      const row = input?.closest(".as-setting-row");
      if (row) row.classList.toggle("is-on", input?.checked !== false);
    });
  }

  function updateWelcomeCount() {
    const ta = $("as-welcome-text");
    const counter = $("as-welcome-count");
    if (!ta || !counter) return;
    const len = (ta.value || "").length;
    const max = Number(ta.maxLength) || 600;
    counter.textContent = `${len} / ${max}`;
    counter.classList.toggle("as-char-count--warn", len > max * 0.9);
  }

  function renderCards() {
    const root = $("as-settings-cards");
    if (!root || !state.settings) return;
    const s = state.settings;

    const picks = [
      {
        modal: "languages",
        mark: "Lang",
        markClass: "as-pick-mark--lang",
        label: "Languages",
        value: (s.languages || []).map(langLabel).join(", ") || "None",
        meta: `${(s.languages || []).length} active`,
      },
      {
        modal: "voice",
        mark: "TTS",
        markClass: "as-pick-mark--voice",
        label: "Voice",
        value: voiceLabel(),
        meta: [formatProviderLabel(s.voice?.provider), formatTabLabel(s.voice?.tab)].filter(Boolean).join(" · "),
      },
      {
        modal: "llm",
        mark: "LLM",
        markClass: "as-pick-mark--llm",
        label: "AI model",
        value: s.llm?.model || "—",
        meta: [formatProviderLabel(s.llm?.provider), `temp ${s.llm?.temperature ?? 0.7}`].filter(Boolean).join(" · "),
      },
      {
        modal: "stt",
        mark: "STT",
        markClass: "as-pick-mark--stt",
        label: "Transcription",
        value: s.stt?.model || "—",
        meta: [formatProviderLabel(s.stt?.provider), s.stt?.language || ""].filter(Boolean).join(" · "),
      },
      {
        modal: "advanced",
        mark: "Adv",
        markClass: "as-pick-mark--adv",
        label: "Advanced",
        value: "Interruption & VAD",
        meta: [
          s.advanced?.speaking_interruption_allowed ? "Barge-in on" : "Barge-in off",
          s.advanced?.speculative_reply_enabled ? "Next-turn prep on" : "Next-turn prep off",
          s.ambient_sound?.enabled ? ambientLabel() : "No ambience",
        ].join(" · "),
      },
    ];

    root.innerHTML = picks.map((p) => `
      <button type="button" class="settings-card as-card as-pick-card ma-pick-card" data-modal="${esc(p.modal)}" data-pick="${esc(p.modal)}" aria-label="Edit ${esc(p.label)}">
        <span class="as-pick-accent" aria-hidden="true"></span>
        <header class="as-pick-header">
          <span class="as-pick-mark ${p.markClass}" aria-hidden="true">${esc(p.mark)}</span>
          <span class="as-pick-edit" aria-hidden="true">
            <span>Edit</span>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><path d="m9 18 6-6-6-6"/></svg>
          </span>
        </header>
        <div class="as-pick-body">
          <span class="as-pick-label">${esc(p.label)}</span>
          <p class="as-pick-value">${esc(p.value)}</p>
          <span class="as-pick-meta">${esc(p.meta)}</span>
        </div>
      </button>
    `).join("");

    root.querySelectorAll(".as-card").forEach((btn) => {
      btn.addEventListener("click", () => openModal(btn.dataset.modal));
    });

    $("as-welcome-text").value = s.welcome?.message || "";
    $("as-welcome-dynamic").checked = s.welcome?.dynamic !== false;
    $("as-welcome-interrupt").checked = s.welcome?.interruptable !== false;
    $("as-name").value = s.name || "";
    $("as-system-prompt").value = s.system_prompt || "";
    updateWelcomeCount();
    syncWelcomeToggleRows();

    renderFlowSections();
  }

  function bindFlowSectionToggles(root) {
    root.querySelectorAll(".as-flow-enabled").forEach((el) => {
      el.addEventListener("change", () => {
        const card = el.closest(".as-flow-card");
        const ta = card?.querySelector(".as-flow-content");
        const enabled = el.checked;
        if (ta) ta.disabled = !enabled;
        card?.classList.toggle("is-enabled", enabled);
        card?.querySelector(".as-flow-row")?.classList.toggle("is-on", enabled);
        const status = card?.querySelector(".as-flow-status");
        if (status) status.textContent = enabled ? "Included in system prompt" : "Disabled — not sent to agent";
        state.dirty = true;
      });
    });
    root.querySelectorAll(".as-flow-content").forEach((el) => {
      el.addEventListener("input", () => { state.dirty = true; });
    });
  }

  function renderFlowSections() {
    const root = $("as-flow-sections");
    if (!root || !state.settings?.flow) return;
    const sections = [
      state.settings.flow.identity_purpose,
      state.settings.flow.facts,
      state.settings.flow.actions_limits,
    ];
    root.innerHTML = sections.map((sec, idx) => `
      <article class="as-flow-card ${sec.enabled ? "is-enabled" : ""}" data-idx="${idx}">
        <label class="as-setting-row as-flow-row ${sec.enabled ? "is-on" : ""}">
          <span class="as-setting-copy">
            <strong class="as-setting-title">${esc(sec.title)}</strong>
            <span class="as-setting-sub as-flow-status">${sec.enabled ? "Included in system prompt" : "Disabled — not sent to agent"}</span>
          </span>
          <span class="toggle as-setting-switch" aria-hidden="true">
            <input type="checkbox" class="as-flow-enabled" data-idx="${idx}" ${sec.enabled ? "checked" : ""}>
            <span class="toggle-slider"></span>
          </span>
        </label>
        <div class="as-flow-body">
          <textarea class="textarea as-flow-content" data-idx="${idx}" rows="6" ${sec.enabled ? "" : "disabled"}>${esc(sec.content)}</textarea>
        </div>
      </article>
    `).join("");
    bindFlowSectionToggles(root);
  }

  function collectFromForm() {
    if (!state.settings) return null;
    const s = JSON.parse(JSON.stringify(state.settings));
    s.name = $("as-name")?.value?.trim() || s.name;
    s.system_prompt = $("as-system-prompt")?.value?.trim() || "";
    s.welcome.message = $("as-welcome-text")?.value?.trim() || "";
    s.welcome.dynamic = $("as-welcome-dynamic")?.checked !== false;
    s.welcome.interruptable = $("as-welcome-interrupt")?.checked !== false;

    const sections = [
      s.flow.identity_purpose,
      s.flow.facts,
      s.flow.actions_limits,
    ];
    document.querySelectorAll(".as-flow-enabled").forEach((el) => {
      const i = Number(el.dataset.idx);
      if (sections[i]) sections[i].enabled = el.checked;
    });
    document.querySelectorAll(".as-flow-content").forEach((el) => {
      const i = Number(el.dataset.idx);
      if (sections[i]) sections[i].content = el.value;
    });
    return s;
  }

  function formatTabLabel(tab) {
    return String(tab || "").replace(/_/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
  }

  function formatProviderLabel(provider) {
    const raw = String(provider || "").trim();
    if (!raw) return "";
    return raw.charAt(0).toUpperCase() + raw.slice(1);
  }

  function setModalChrome(kind) {
    const meta = MODAL_META[kind] || {};
    const card = $("as-modal")?.querySelector(".as-modal-card");
    card?.classList.toggle("as-modal-card--picker", !!meta.picker);
    card?.classList.toggle(`as-modal-card--${kind}`, true);

    const mark = $("as-modal-mark");
    if (mark) {
      mark.textContent = meta.mark || "Set";
      mark.className = `as-modal-mark ${meta.markClass || ""}`.trim();
    }
    if ($("as-modal-title")) $("as-modal-title").textContent = meta.title || "Settings";
    if ($("as-modal-subtitle")) $("as-modal-subtitle").textContent = meta.subtitle || "";
    if ($("as-modal-foot-hint")) $("as-modal-foot-hint").textContent = meta.hint || "Changes apply when you confirm.";
    if ($("as-modal-confirm")) $("as-modal-confirm").textContent = meta.confirm || "Save";
  }

  function clearModalChromeClasses() {
    const card = $("as-modal")?.querySelector(".as-modal-card");
    if (!card) return;
    card.classList.remove("as-modal-card--picker");
    Object.keys(MODAL_META).forEach((k) => card.classList.remove(`as-modal-card--${k}`));
  }

  function updateVoiceHero(name, style = "") {
    const title = $("as-voice-selection");
    const meta = $("as-voice-selection-meta");
    if (title) title.textContent = name || "—";
    if (meta) meta.textContent = style || "Select a voice below";
    $("as-voice-hero")?.classList.toggle("has-selection", !!name);
  }

  function searchIconSvg() {
    return `<svg class="as-search-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>`;
  }

  function waveBarsHtml() {
    return `<span class="as-voice-wave" aria-hidden="true">${[3, 5, 8, 5, 7, 4, 6, 3].map((h, i) => `<span class="as-voice-wave-bar" style="--h:${h}"></span>`).join("")}</span>`;
  }

  function openModal(kind) {
    const modal = $("as-modal");
    const body = $("as-modal-body");
    if (!modal || !body) return;
    const s = state.settings;
    const cat = state.catalog || {};
    _activeModalKind = kind;
    setModalChrome(kind);

    if (kind === "languages") {
      const selected = new Set(s.languages || []);
      body.innerHTML = `
        <div class="as-modal-panel">
          <div class="as-lang-summary">
            <span class="as-lang-count"><strong id="as-lang-count-num">${selected.size}</strong> languages active</span>
          </div>
        </div>
        <div class="as-lang-grid" id="as-lang-grid" role="group" aria-label="Languages">
          ${(cat.languages || []).map((l) => `
            <button type="button" class="as-lang-card ${selected.has(l.code) ? "is-selected" : ""}" data-lang="${esc(l.code)}" aria-pressed="${selected.has(l.code)}">
              <span class="as-lang-card-check" aria-hidden="true">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6 9 17l-5-5"/></svg>
              </span>
              <span class="as-lang-card-name">${esc(l.label)}</span>
              <span class="as-lang-card-code">${esc(l.code)}</span>
            </button>
          `).join("")}
        </div>
      `;
      const syncLangCount = () => {
        const n = body.querySelectorAll(".as-lang-card.is-selected").length;
        const el = $("as-lang-count-num");
        if (el) el.textContent = String(n);
      };
      body.querySelectorAll(".as-lang-card").forEach((btn) => {
        btn.addEventListener("click", () => {
          const selectedCards = [...body.querySelectorAll(".as-lang-card.is-selected")];
          if (btn.classList.contains("is-selected") && selectedCards.length <= 1) {
            window.showToast?.("Keep at least one language enabled", "warn");
            btn.classList.add("as-lang-card--shake");
            setTimeout(() => btn.classList.remove("as-lang-card--shake"), 400);
            return;
          }
          const on = !btn.classList.contains("is-selected");
          btn.classList.toggle("is-selected", on);
          btn.setAttribute("aria-pressed", on ? "true" : "false");
          syncLangCount();
        });
      });
    } else if (kind === "voice") {
      const tabs = Object.keys(cat.voices || { recommended: [] });
      const catalogVoice = findVoiceInCatalog(s.voice?.voice_id);
      const activeTab = catalogVoice?.tab || s.voice?.tab || tabs[0];
      const curName = voiceLabel();
      const curParts = voiceDisplayParts(
        catalogVoice || {
          id: s.voice?.voice_id,
          name: curName,
          quality: "",
          provider: s.voice?.provider,
        },
        activeTab,
      );
      state._pendingVoice = {
        voice_id: s.voice?.voice_id,
        tab: activeTab,
        provider: catalogVoice?.provider || s.voice?.provider,
        display_name: curName,
      };
      state._voiceTab = activeTab;
      body.innerHTML = `
        <div class="as-voice-panel">
          <div class="as-voice-hero ma-outline-inset has-selection" id="as-voice-hero">
            <div class="as-voice-hero-visual" aria-hidden="true">${waveBarsHtml()}</div>
            <div class="as-voice-hero-copy">
              <span class="as-voice-hero-label">Current voice</span>
              <strong id="as-voice-selection" class="as-voice-hero-name">${esc(curName)}</strong>
              <span id="as-voice-selection-meta" class="as-voice-hero-meta">${esc(curParts.style || catalogVoice?.provider || s.voice?.provider || "")}</span>
            </div>
          </div>
          <div class="as-voice-toolbar">
            <label class="as-search-field ma-outline-inset">
              ${searchIconSvg()}
              <input type="search" class="input as-voice-search ma-input-outline" id="as-voice-search" placeholder="Search voices by name or style…" autocomplete="off">
            </label>
            <div class="as-segmented as-voice-tabs ma-outline-inset" role="tablist" aria-label="Voice catalog">
              ${tabs.map((t) => `<button type="button" class="as-seg-btn ${t === activeTab ? "active" : ""}" data-vtab="${esc(t)}" role="tab" aria-selected="${t === activeTab}">${esc(formatTabLabel(t))}</button>`).join("")}
            </div>
          </div>
          <div class="as-voice-list-wrap ma-outline-inset">
            <div class="as-voice-list" id="as-voice-list" role="listbox" aria-label="Voices"></div>
          </div>
        </div>
      `;
      const search = $("as-voice-search");
      const renderCurrentVoiceList = () => renderVoiceList(state._voiceTab, search?.value?.trim() || "", kind);
      renderCurrentVoiceList();
      search?.addEventListener("input", renderCurrentVoiceList);
      search?.focus();
      body.querySelector(".as-voice-tabs")?.addEventListener("click", (e) => {
        const tab = e.target.closest("[data-vtab]");
        if (!tab) return;
        state._voiceTab = tab.dataset.vtab;
        body.querySelectorAll(".as-voice-tabs .as-seg-btn").forEach((t) => {
          const active = t === tab;
          t.classList.toggle("active", active);
          t.setAttribute("aria-selected", active ? "true" : "false");
        });
        renderCurrentVoiceList();
      });
    } else if (kind === "llm") {
      const providers = cat.llm_providers || [];
      body.innerHTML = `
        <section class="as-modal-section">
          <h4 class="as-modal-section-title">Provider &amp; model</h4>
          <div class="as-modal-fields">
            ${field("Provider", `<select class="input ma-input-outline" id="m-llm-provider">${providers.map((p) => `<option value="${esc(p.id)}" ${s.llm.provider === p.id ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select>`)}
            ${field("Model", `<select class="input ma-input-outline" id="m-llm-model"></select>`)}
          </div>
        </section>
        <section class="as-modal-section">
          <h4 class="as-modal-section-title">Behavior</h4>
          <div class="as-modal-fields">
            ${sliderField("Temperature", "m-llm-temp", 0, 1, 0.05, s.llm.temperature ?? 0.7, "m-llm-temp-val")}
            <label class="as-setting-row as-modal-toggle-row">
              <span class="as-setting-copy">
                <strong class="as-setting-title">Streaming responses</strong>
                <span class="as-setting-sub">Stream tokens from the LLM for lower latency</span>
              </span>
              <span class="toggle as-setting-switch">
                <input type="checkbox" id="m-llm-stream" ${s.llm.streaming !== false ? "checked" : ""}>
                <span class="toggle-slider"></span>
              </span>
            </label>
          </div>
        </section>
      `;
      const provSel = $("m-llm-provider");
      const modelSel = $("m-llm-model");
      function fillModels() {
        const p = providers.find((x) => x.id === provSel.value) || providers[0];
        modelSel.innerHTML = (p?.models || []).map((m) => `<option value="${esc(m)}" ${s.llm.model === m ? "selected" : ""}>${esc(m)}</option>`).join("");
      }
      provSel?.addEventListener("change", fillModels);
      fillModels();
      bindSlider("m-llm-temp", "m-llm-temp-val");
    } else if (kind === "stt") {
      const sttProv = cat.stt_providers || [];
      body.innerHTML = `
        <section class="as-modal-section">
          <h4 class="as-modal-section-title">Provider</h4>
          <div class="as-modal-fields">
            ${field("Provider", `<select class="input ma-input-outline" id="m-stt-provider">${sttProv.map((p) => `<option value="${esc(p.id)}" ${s.stt.provider === p.id ? "selected" : ""}>${esc(p.label)}</option>`).join("")}</select>`)}
            ${field("Model", `<select class="input ma-input-outline" id="m-stt-model"></select>`)}
            ${field("Language code", `<input class="input ma-input-outline" id="m-stt-lang" value="${esc(s.stt.language || "te-IN")}" placeholder="te-IN">`)}
          </div>
        </section>
        <section class="as-modal-section">
          <h4 class="as-modal-section-title">Soniox endpoint detection</h4>
          <p class="muted as-modal-hint"><strong>Silence timeout</strong> = how long Soniox waits after you stop speaking before finalizing (200–3000 ms). Lower = faster replies.</p>
          <div class="as-modal-fields as-modal-fields--grid">
            ${sliderField("Silence timeout", "m-stt-max-endpoint-ms", 200, 3000, 50, s.stt.silence_timeout_ms ?? s.stt.max_endpoint_delay_ms ?? 200, "m-stt-max-endpoint-ms-val", "ms")}
            ${sliderField("Endpoint sensitivity", "m-stt-endpoint-sens", -1, 1, 0.05, s.stt.endpoint_sensitivity ?? 0.35, "m-stt-endpoint-sens-val", "")}
            ${field("Min endpointing (s)", `<input type="number" step="0.05" min="0.2" max="3" class="input ma-input-outline" id="m-stt-min-endpoint" value="${s.stt.min_endpointing_delay ?? 0.2}">`)}
            ${field("Max endpointing (s)", `<input type="number" step="0.05" min="0.2" max="5" class="input ma-input-outline" id="m-stt-max-endpoint" value="${s.stt.max_endpointing_delay ?? 0.2}">`)}
          </div>
          <div class="as-settings-group as-setting-list">
            <label class="as-setting-row as-modal-toggle-row">
              <span class="as-setting-copy">
                <strong class="as-setting-title">Language identification</strong>
                <span class="as-setting-sub">Soniox auto-detects Telugu / English / Hindi</span>
              </span>
              <span class="toggle as-setting-switch">
                <input type="checkbox" id="m-stt-lang-id" ${s.stt.enable_language_identification !== false ? "checked" : ""}>
                <span class="toggle-slider"></span>
              </span>
            </label>
            <label class="as-setting-row as-modal-toggle-row">
              <span class="as-setting-copy">
                <strong class="as-setting-title">Noise reduction</strong>
                <span class="as-setting-sub">BVC filter on LiveKit room input</span>
              </span>
              <span class="toggle as-setting-switch">
                <input type="checkbox" id="m-stt-noise" ${s.stt.noise_reduction !== false ? "checked" : ""}>
                <span class="toggle-slider"></span>
              </span>
            </label>
          </div>
        </section>
      `;
      const provSel = $("m-stt-provider");
      const modelSel = $("m-stt-model");
      function fillSttModels() {
        const p = sttProv.find((x) => x.id === provSel.value) || sttProv[0];
        modelSel.innerHTML = (p?.models || []).map((m) => `<option value="${esc(m)}" ${s.stt.model === m ? "selected" : ""}>${esc(m)}</option>`).join("");
      }
      provSel?.addEventListener("change", fillSttModels);
      fillSttModels();
      bindSlider("m-stt-max-endpoint-ms", "m-stt-max-endpoint-ms-val", "ms");
      bindSlider("m-stt-endpoint-sens", "m-stt-endpoint-sens-val", "");
    } else if (kind === "advanced") {
      const a = s.advanced || {};
      const amb = s.ambient_sound || {};
      const sounds = cat.ambient_sounds || [];
      const vol = cat.ambient_volume || { min: 0.08, max: 0.18, step: 0.01 };
      const soundOpts = sounds.map((opt) =>
        `<option value="${esc(opt.id)}" ${amb.sound_type === opt.id ? "selected" : ""}>${esc(opt.label)}</option>`
      ).join("");
      const selectedHint = sounds.find((opt) => opt.id === amb.sound_type)?.hint
        || "Subtle background loop during the call.";
      body.innerHTML = `
        <div class="as-settings-group as-modal-group as-setting-list">
          <label class="as-setting-row as-modal-toggle-row">
            <span class="as-setting-copy">
              <strong class="as-setting-title">Allow interruptions</strong>
              <span class="as-setting-sub">Let callers barge in while the agent is speaking</span>
            </span>
            <span class="toggle as-setting-switch">
              <input type="checkbox" id="m-adv-interrupt" ${a.speaking_interruption_allowed !== false ? "checked" : ""}>
              <span class="toggle-slider"></span>
            </span>
          </label>
        </div>
        <section class="as-modal-section">
          <h4 class="as-modal-section-title">Trial · Next-turn prep</h4>
          <p class="muted as-modal-section-hint">While the agent speaks (2s+ replies), a background LLM predicts likely follow-ups — does not block live replies.</p>
          <div class="as-settings-group as-setting-list">
            <label class="as-setting-row as-modal-toggle-row">
              <span class="as-setting-copy">
                <strong class="as-setting-title">Speculative next-turn prep</strong>
                <span class="as-setting-sub">Prep probable replies while the caller listens (trial)</span>
              </span>
              <span class="toggle as-setting-switch">
                <input type="checkbox" id="m-adv-speculative" ${a.speculative_reply_enabled ? "checked" : ""}>
                <span class="toggle-slider"></span>
              </span>
            </label>
          </div>
          <div class="as-modal-fields as-modal-fields--grid">
            ${field("Min speech to prep (ms)", `<input type="number" min="1500" max="8000" step="100" class="input" id="m-adv-spec-min-ms" value="${a.speculative_reply_min_speech_ms ?? 2000}">`)}
          </div>
        </section>
        <section class="as-modal-section">
          <h4 class="as-modal-section-title">While listening</h4>
          <div class="as-modal-fields as-modal-fields--grid">
            ${field("Detection confidence", `<input type="number" step="0.05" min="0" max="1" class="input" id="m-listen-conf" value="${a.listening?.speech_detection_confidence ?? 0.5}">`)}
            ${field("Speech start (ms)", `<input type="number" class="input" id="m-listen-start" value="${a.listening?.user_speech_start_duration_ms ?? 200}">`)}
            ${field("Min speech (ms)", `<input type="number" class="input" id="m-listen-min" value="${a.listening?.minimum_user_speech_duration_ms ?? 300}">`)}
          </div>
        </section>
        <section class="as-modal-section">
          <h4 class="as-modal-section-title">While agent speaks</h4>
          <div class="as-modal-fields as-modal-fields--grid">
            ${field("Min words to interrupt", `<input type="number" min="0" class="input" id="m-bot-words" value="${a.bot_speaking?.minimum_words_to_interrupt ?? 1}">`)}
            ${field("Detection confidence", `<input type="number" step="0.05" min="0" max="1" class="input" id="m-bot-conf" value="${a.bot_speaking?.speech_detection_confidence ?? 0.5}">`)}
            ${field("Speech start (ms)", `<input type="number" class="input" id="m-bot-start" value="${a.bot_speaking?.user_speech_start_duration_ms ?? 200}">`)}
            ${field("Min speech (ms)", `<input type="number" class="input" id="m-bot-min" value="${a.bot_speaking?.minimum_user_speech_duration_ms ?? 300}">`)}
          </div>
        </section>
        <section class="as-modal-section as-modal-section--ambient">
          <h4 class="as-modal-section-title">Ambient sound</h4>
          <div class="as-settings-group as-setting-list">
            <label class="as-setting-row as-modal-toggle-row">
              <span class="as-setting-copy">
                <strong class="as-setting-title">Enable ambient sound</strong>
                <span class="as-setting-sub">Loops quietly under the agent voice</span>
              </span>
              <span class="toggle as-setting-switch">
                <input type="checkbox" id="m-amb-enabled" ${amb.enabled !== false ? "checked" : ""}>
                <span class="toggle-slider"></span>
              </span>
            </label>
          </div>
          <div class="as-ambient-controls">
            <div class="as-ambient-sound-picker">
              ${field("Sound", `<select class="input ma-input-outline as-ambient-select" id="m-amb-sound">${soundOpts}</select>`)}
              <p class="muted as-ambient-hint" id="m-amb-hint">${esc(selectedHint)}</p>
            </div>
            <div class="as-ambient-volume">
              ${sliderField(
                "Volume",
                "m-amb-volume",
                vol.min ?? 0.08,
                vol.max ?? 0.18,
                vol.step ?? 0.01,
                amb.volume ?? vol.default ?? 0.12,
                "m-amb-volume-val"
              )}
            </div>
          </div>
        </section>
      `;
      bindSlider("m-amb-volume", "m-amb-volume-val");
      const ambSound = $("m-amb-sound");
      const ambPicker = ambSound?.closest(".as-ambient-sound-picker");
      const syncAmbPickerOpen = (open) => {
        ambPicker?.classList.toggle("is-open", open);
      };
      ambSound?.addEventListener("mousedown", () => syncAmbPickerOpen(true));
      ambSound?.addEventListener("focus", () => syncAmbPickerOpen(true));
      ambSound?.addEventListener("blur", () => syncAmbPickerOpen(false));
      ambSound?.addEventListener("change", () => {
        syncAmbPickerOpen(false);
        const hint = sounds.find((opt) => opt.id === ambSound?.value)?.hint;
        const el = $("m-amb-hint");
        if (el && hint) el.textContent = hint;
      });
    }

    if (_modalCloseTimer) {
      clearTimeout(_modalCloseTimer);
      _modalCloseTimer = null;
    }
    modal.hidden = false;
    document.body.classList.add("as-modal-open");
    requestAnimationFrame(() => modal.classList.add("is-open"));

    const focusTarget = body.querySelector("input, select, button, textarea");
    if (focusTarget && kind !== "voice") focusTarget.focus();

    $("as-modal-confirm").onclick = () => {
      applyModal(kind);
      closeModal();
    };
  }

  function field(label, html) {
    return `<label class="field as-modal-field"><span>${label}</span>${html}</label>`;
  }

  function sliderField(label, inputId, min, max, step, value, valId, suffix = "") {
    const display = suffix ? `${value} ${suffix}` : value;
    const fillId = `${inputId}-fill`;
    const scaleMin = suffix ? `${min} ${suffix}` : min;
    const scaleMax = suffix ? `${max} ${suffix}` : max;
    return `
      <div class="as-slider-field" data-slider-min="${min}" data-slider-max="${max}">
        <div class="as-slider-head">
          <span>${label}</span>
          <span class="as-slider-badge" id="${valId}">${display}</span>
        </div>
        <div class="as-slider-track">
          <div class="as-slider-rail" aria-hidden="true">
            <div class="as-slider-fill" id="${fillId}"></div>
          </div>
          <input type="range" class="as-range as-slider-input" id="${inputId}" min="${min}" max="${max}" step="${step}" value="${value}" aria-valuemin="${min}" aria-valuemax="${max}" aria-valuenow="${value}">
        </div>
        <div class="as-slider-scale" aria-hidden="true">
          <span>${scaleMin}</span>
          <span>${scaleMax}</span>
        </div>
      </div>`;
  }

  function bindSlider(inputId, valId, suffix = "") {
    const input = $(inputId);
    const badge = $(valId);
    const fill = $(`${inputId}-fill`);
    if (!input || !badge) return;
    const min = Number(input.min);
    const max = Number(input.max);
    const sync = () => {
      const val = Number(input.value);
      badge.textContent = suffix ? `${input.value} ${suffix}` : input.value;
      input.setAttribute("aria-valuenow", input.value);
      if (fill && Number.isFinite(min) && Number.isFinite(max) && max > min) {
        const pct = ((val - min) / (max - min)) * 100;
        fill.style.width = `${Math.min(100, Math.max(0, pct))}%`;
      }
    };
    input.addEventListener("input", sync);
    sync();
  }

  function voiceDisplayParts(v, tab) {
    const name = v.name || v.id;
    const style = v.quality || "";
    const label = style && tab === "google" ? `${name} — ${style}` : name;
    return { name, style, label };
  }

  function renderVoiceList(tab, query = "", modalKind = "voice") {
    const list = $("as-voice-list");
    if (!list) return;
    const voices = state.catalog?.voices?.[tab] || [];
    const q = query.toLowerCase();
    const filtered = voices.filter((v) => {
      if (!q) return true;
      const { name, style, label } = voiceDisplayParts(v, tab);
      const hay = `${name} ${style} ${label} ${v.id} ${v.provider || ""}`.toLowerCase();
      return hay.includes(q);
    });
    const cur = state._pendingVoice?.voice_id || state.settings?.voice?.voice_id;

    if (!filtered.length) {
      list.innerHTML = `<p class="as-voice-empty muted">No voices match “${esc(query)}”. Try another tab or search.</p>`;
      return;
    }

    list.innerHTML = filtered.map((v) => {
      const { name, style, label } = voiceDisplayParts(v, tab);
      const selected = cur === v.id;
      return `
        <button
          type="button"
          class="as-voice-row ${selected ? "is-selected" : ""}"
          role="option"
          aria-selected="${selected}"
          data-vid="${esc(v.id)}"
          data-vtab="${esc(tab)}"
          data-prov="${esc(v.provider)}"
          data-name="${esc(label)}"
          data-style="${esc(style || v.provider || tab)}"
        >
          <div class="as-voice-row-top">
            <span class="as-voice-row-identity">
              <span class="as-voice-row-avatar" aria-hidden="true">${esc(name.slice(0, 2).toUpperCase())}</span>
              <span class="as-voice-row-main">
                <strong class="as-voice-row-name">${esc(name)}</strong>
                <span class="as-voice-row-style">${esc(style || v.provider || tab)}</span>
              </span>
            </span>
            <span class="as-voice-row-badge" aria-hidden="true">
              <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><path d="M20 6 9 17l-5-5"/></svg>
            </span>
          </div>
          <span class="as-voice-row-provider">${esc(formatProviderLabel(v.provider || tab))}</span>
        </button>`;
    }).join("");

    const selectVoice = (btn, instantSave = false) => {
      list.querySelectorAll(".as-voice-row").forEach((b) => {
        b.classList.remove("is-selected");
        b.setAttribute("aria-selected", "false");
      });
      btn.classList.add("is-selected");
      btn.setAttribute("aria-selected", "true");
      state._pendingVoice = {
        voice_id: btn.dataset.vid,
        tab: btn.dataset.vtab,
        provider: btn.dataset.prov,
        display_name: btn.dataset.name,
      };
      updateVoiceHero(btn.dataset.name, btn.dataset.style);
      if (instantSave && modalKind === "voice") {
        applyModal("voice");
        closeModal();
      }
    };

    list.querySelectorAll(".as-voice-row").forEach((btn) => {
      btn.addEventListener("click", () => selectVoice(btn, false));
      btn.addEventListener("dblclick", () => selectVoice(btn, true));
    });

    const selectedRow = list.querySelector(".as-voice-row.is-selected");
    if (selectedRow) selectedRow.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  function applyModal(kind) {
    const s = state.settings;
    if (!s) return;
    if (kind === "languages") {
      s.languages = [...document.querySelectorAll("#as-modal-body .as-lang-card.is-selected")].map((el) => el.dataset.lang);
      if (!s.languages.length) s.languages = ["te"];
    } else if (kind === "voice") {
      if (state._pendingVoice) Object.assign(s.voice, state._pendingVoice);
      delete state._pendingVoice;
      delete state._voiceTab;
    } else if (kind === "llm") {
      s.llm.provider = $("m-llm-provider")?.value || s.llm.provider;
      s.llm.model = $("m-llm-model")?.value || s.llm.model;
      s.llm.temperature = Number($("m-llm-temp")?.value) || 0.7;
      s.llm.streaming = $("m-llm-stream")?.checked !== false;
    } else if (kind === "stt") {
      s.stt.provider = $("m-stt-provider")?.value || s.stt.provider;
      s.stt.model = $("m-stt-model")?.value || s.stt.model;
      s.stt.language = $("m-stt-lang")?.value?.trim() || s.stt.language;
      const maxEpMs = Number($("m-stt-max-endpoint-ms")?.value) || 200;
      s.stt.max_endpoint_delay_ms = maxEpMs;
      s.stt.silence_timeout_ms = maxEpMs;
      s.stt.endpoint_sensitivity = Number($("m-stt-endpoint-sens")?.value);
      if (!Number.isFinite(s.stt.endpoint_sensitivity)) s.stt.endpoint_sensitivity = 0.35;
      s.stt.min_endpointing_delay = Number($("m-stt-min-endpoint")?.value) || s.stt.min_endpointing_delay;
      s.stt.max_endpointing_delay = Number($("m-stt-max-endpoint")?.value) || s.stt.max_endpointing_delay;
      s.stt.enable_language_identification = $("m-stt-lang-id")?.checked !== false;
      s.stt.enable_endpoint_detection = true;
      s.stt.noise_reduction = $("m-stt-noise")?.checked !== false;
    } else if (kind === "advanced") {
      s.advanced.speaking_interruption_allowed = $("m-adv-interrupt")?.checked !== false;
      s.advanced.speculative_reply_enabled = $("m-adv-speculative")?.checked === true;
      s.advanced.speculative_reply_min_speech_ms = Number($("m-adv-spec-min-ms")?.value) || 2000;
      s.advanced.listening.speech_detection_confidence = Number($("m-listen-conf")?.value) || 0.5;
      s.advanced.listening.user_speech_start_duration_ms = Number($("m-listen-start")?.value) || 200;
      s.advanced.listening.minimum_user_speech_duration_ms = Number($("m-listen-min")?.value) || 300;
      s.advanced.bot_speaking.minimum_words_to_interrupt = Number($("m-bot-words")?.value) || 1;
      s.advanced.bot_speaking.speech_detection_confidence = Number($("m-bot-conf")?.value) || 0.5;
      s.advanced.bot_speaking.user_speech_start_duration_ms = Number($("m-bot-start")?.value) || 200;
      s.advanced.bot_speaking.minimum_user_speech_duration_ms = Number($("m-bot-min")?.value) || 300;
      if (!s.ambient_sound) s.ambient_sound = {};
      s.ambient_sound.enabled = $("m-amb-enabled")?.checked === true;
      s.ambient_sound.sound_type = $("m-amb-sound")?.value || "cicd_1";
      const volMin = state.catalog?.ambient_volume?.min ?? 0.08;
      const volMax = state.catalog?.ambient_volume?.max ?? 0.18;
      let vol = Number($("m-amb-volume")?.value);
      if (!Number.isFinite(vol)) vol = 0.12;
      s.ambient_sound.volume = Math.min(volMax, Math.max(volMin, vol));
    }
    state.dirty = true;
    renderCards();
  }

  function closeModal() {
    const modal = $("as-modal");
    if (!modal) return;
    modal.classList.remove("is-open");
    document.body.classList.remove("as-modal-open");
    if (_modalCloseTimer) clearTimeout(_modalCloseTimer);
    _modalCloseTimer = setTimeout(() => {
      modal.hidden = true;
      _modalCloseTimer = null;
    }, 220);
    delete state._pendingVoice;
    delete state._voiceTab;
    _activeModalKind = null;
    clearModalChromeClasses();
  }

  async function load() {
    setStatus("Loading settings…");
    try {
      const data = await window.apiJson("/api/assistants/main/settings");
      state.settings = data.settings;
      state.catalog = data.catalog;
      state.dirty = false;
      document.querySelectorAll(".ma-zone--dirty").forEach((z) => z.classList.remove("ma-zone--dirty"));
      renderCards();
      setStatus("Settings loaded", "ok");
      window.AgentControl?.refreshStatus?.();
    } catch (e) {
      setStatus(e.message || "Load failed", "error");
      window.showToast?.(e.message || "Failed to load settings", "error");
    }
  }

  async function save(applyOnly = false) {
    if (state.saving) return;
    const payload = collectFromForm();
    if (!payload) return;
    state.saving = true;
    const btn = $("as-save-btn");
    const prev = btn?.textContent;
    if (btn) {
      btn.disabled = true;
      btn.textContent = applyOnly ? "Applying…" : "Saving…";
    }
    setStatus(applyOnly ? "Applying…" : "Saving…");
    try {
      const endpoint = applyOnly
        ? "/api/assistants/main/settings/apply"
        : "/api/assistants/main/settings";
      const method = applyOnly ? "POST" : "PUT";
      const res = await window.apiJson(endpoint, {
        method,
        body: JSON.stringify({
          settings: payload,
          restart_agent: $("as-restart")?.checked !== false,
        }),
      });
      state.settings = res.settings || payload;
      state.dirty = false;
      document.querySelectorAll(".ma-zone--dirty").forEach((z) => z.classList.remove("ma-zone--dirty"));
      renderCards();
      const restarted = res.restart?.success;
      setStatus(restarted ? "Saved & agent restarted" : res.message || "Saved", restarted ? "ok" : "ok");
      window.showToast?.(res.message || "Settings saved", "success");
      window.AgentControl?.refreshStatus?.();
    } catch (e) {
      setStatus(e.message || "Save failed", "error");
      window.showToast?.(e.message || "Save failed", "error");
    } finally {
      state.saving = false;
      if (btn) {
        btn.disabled = false;
        btn.textContent = prev || "Save & Apply";
      }
    }
  }

  function init() {
    $("as-save-btn")?.addEventListener("click", () => save(false));
    $("as-save-footer-btn")?.addEventListener("click", () => save(false));
    $("as-apply-btn")?.addEventListener("click", () => save(true));
    $("as-apply-footer-btn")?.addEventListener("click", () => save(true));
    $("as-reload-btn")?.addEventListener("click", load);
    $("as-modal-cancel")?.addEventListener("click", closeModal);
    $("as-modal-close")?.addEventListener("click", closeModal);
    $("as-modal")?.querySelectorAll("[data-as-dismiss]").forEach((el) => {
      el.addEventListener("click", closeModal);
    });
    document.addEventListener("keydown", (e) => {
      const modal = $("as-modal");
      if (e.key === "Escape" && modal && !modal.hidden) closeModal();
    });
    $("as-welcome-text")?.addEventListener("input", () => {
      state.dirty = true;
      updateWelcomeCount();
    });
    ["as-welcome-dynamic", "as-welcome-interrupt"].forEach((id) => {
      $(id)?.addEventListener("change", () => {
        state.dirty = true;
        syncWelcomeToggleRows();
      });
    });
    const markDirtyZone = (el) => {
      el?.closest(".ma-field-box, .ma-zone")?.classList.add("ma-zone--dirty");
      state.dirty = true;
    };
    ["as-name", "as-system-prompt", "as-welcome-text"].forEach((id) => {
      $(id)?.addEventListener("input", () => markDirtyZone($(id)));
    });
  }

  window.AssistantSettings = { load, save, init };
  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();