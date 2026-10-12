/**
 * Call Logs page — filtered list, pagination, CSV export, detail modal (Step 7).
 */
(function () {
  const clState = {
    rows: [],
    total: 0,
    page: 1,
    pageSize: 25,
    totalPages: 1,
    loading: false,
    initialized: false,
    filtersOpen: true,
    agents: [],
  };

  let latencyChart = null;
  let activeAudio = null;
  let activePlayerEl = null;

  function stopAudio() {
    if (activeAudio) {
      activeAudio.pause();
      activeAudio.currentTime = 0;
    }
    if (activePlayerEl) {
      setPlayerPlaying(activePlayerEl, false);
      activePlayerEl = null;
    }
    activeAudio = null;
    document.querySelectorAll(".transcript-bubble.is-active").forEach((el) => {
      el.classList.remove("is-active");
    });
  }

  function claimAudio(audio, playerEl) {
    if (activeAudio && activeAudio !== audio) {
      activeAudio.pause();
      if (activePlayerEl) setPlayerPlaying(activePlayerEl, false);
    }
    activeAudio = audio;
    activePlayerEl = playerEl || null;
  }

  async function playCallRecording(callId, fallbackUrl = "") {
    const streamUrl = callId ? recordingStreamUrl(callId) : "";
    const src = streamUrl || String(fallbackUrl || "").trim();
    if (!src) throw new Error("No recording URL");
    stopAudio();
    const audio = new Audio(src);
    claimAudio(audio, null);
    await audio.play();
    return audio;
  }

  function bindAudioLifecycle() {
    if (clState.audioLifecycleBound) return;
    clState.audioLifecycleBound = true;
    document.addEventListener("visibilitychange", () => {
      if (document.hidden) stopAudio();
    });
    window.addEventListener("pagehide", stopAudio);
  }

  function $(id) {
    return document.getElementById(id);
  }

  function esc(value) {
    return String(value ?? "")
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function formatDuration(seconds) {
    const s = Math.max(0, Number(seconds) || 0);
    if (s < 60) return `${s}s`;
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return rem ? `${m}m ${rem}s` : `${m}m`;
  }

  function formatPlayerTime(seconds) {
    const s = Math.max(0, Math.floor(Number(seconds) || 0));
    const m = Math.floor(s / 60);
    const rem = s % 60;
    return `${m}:${String(rem).padStart(2, "0")}`;
  }

  function recordingStreamUrl(callId) {
    if (!callId) return "";
    const token = localStorage.getItem("speaksy-auth-token") || "";
    const qs = token ? `?token=${encodeURIComponent(token)}` : "";
    return `/api/calls/${encodeURIComponent(callId)}/recording${qs}`;
  }

  function renderRecordingPlayer(recordingUrl, durationSeconds = 0, callId = "") {
    const externalUrl = String(recordingUrl || "").trim();
    const streamUrl = callId ? recordingStreamUrl(callId) : externalUrl;
    if (!streamUrl && !externalUrl) return "";

    return `
      <div class="call-audio-player" data-duration="${Number(durationSeconds) || 0}" data-call-id="${esc(callId)}">
        <audio class="call-audio-element" preload="metadata" src="${esc(streamUrl || externalUrl)}"></audio>
        <div class="call-audio-main">
          <button type="button" class="call-audio-play" aria-label="Play recording">
            <svg class="call-audio-icon call-audio-icon--play" viewBox="0 0 16 16" aria-hidden="true"><path d="M5.5 4.2v7.6l6-3.8-6-3.8z" fill="currentColor"/></svg>
            <svg class="call-audio-icon call-audio-icon--pause" viewBox="0 0 16 16" aria-hidden="true" hidden><path d="M5.5 4.5h2v7h-2v-7zm3 0h2v7h-2v-7z" fill="currentColor"/></svg>
          </button>
          <div class="call-audio-track">
            <div class="call-audio-progress">
              <div class="call-audio-progress-fill"></div>
              <input type="range" class="call-audio-seek" min="0" max="1000" value="0" step="1" aria-label="Seek recording">
            </div>
            <div class="call-audio-times">
              <span class="call-audio-current">0:00</span>
              <span class="call-audio-duration">${formatPlayerTime(durationSeconds)}</span>
            </div>
          </div>
          <button type="button" class="call-audio-mute" aria-label="Mute" title="Mute">
            <svg class="call-audio-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M3.5 6h2l2.5-2v8L5.5 10h-2V6z" fill="currentColor"/><path d="M10.5 5.5a4.5 4.5 0 010 5" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/><path d="M11.8 4.2a6.2 6.2 0 010 7.6" fill="none" stroke="currentColor" stroke-width="1.2" stroke-linecap="round"/></svg>
          </button>
          <a class="call-audio-open" href="${esc(externalUrl || streamUrl)}" target="_blank" rel="noopener" title="Open recording in new tab" aria-label="Open recording">
            <svg class="call-audio-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M6 3.5h6.5V10" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M9.5 6.5L3 13" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/><path d="M3 6.5V13h6.5" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>
          </a>
        </div>
        <div class="call-audio-loading" hidden>
          <span class="call-audio-loading-dot"></span>
          Loading recording…
        </div>
      </div>
    `;
  }

  function setPlayerPlaying(player, playing) {
    const playIcon = player.querySelector(".call-audio-icon--play");
    const pauseIcon = player.querySelector(".call-audio-icon--pause");
    const playBtn = player.querySelector(".call-audio-play");
    if (playIcon) playIcon.hidden = playing;
    if (pauseIcon) pauseIcon.hidden = !playing;
    if (playBtn) playBtn.setAttribute("aria-label", playing ? "Pause recording" : "Play recording");
    player.classList.toggle("is-playing", playing);
  }

  function setPlayerError(player, hasError) {
    if (hasError) console.warn("Recording failed to load", player.dataset.callId || "");
    const loadingEl = player.querySelector(".call-audio-loading");
    if (loadingEl) loadingEl.hidden = true;
    if (hasError) {
      setPlayerPlaying(player, false);
      if (activePlayerEl === player) {
        activePlayerEl = null;
        activeAudio = null;
      }
    }
  }

  function updatePlayerProgress(player, audio, seek, fill, currentEl, durationEl) {
    const duration = Number.isFinite(audio.duration) && audio.duration > 0
      ? audio.duration
      : Number(player.dataset.duration) || 0;
    const current = Number.isFinite(audio.currentTime) ? audio.currentTime : 0;

    if (durationEl && duration > 0) durationEl.textContent = formatPlayerTime(duration);
    if (currentEl) currentEl.textContent = formatPlayerTime(current);
    if (seek && duration > 0) {
      seek.value = String(Math.round((current / duration) * 1000));
      seek.disabled = false;
    }
    if (fill && duration > 0) {
      fill.style.width = `${Math.min(100, (current / duration) * 100)}%`;
    }
  }

  function initRecordingPlayers(root) {
    const scope = root || document;
    scope.querySelectorAll(".call-audio-player:not([data-player-ready])").forEach((player) => {
      player.dataset.playerReady = "1";
      const audio = player.querySelector(".call-audio-element");
      const playBtn = player.querySelector(".call-audio-play");
      const seek = player.querySelector(".call-audio-seek");
      const fill = player.querySelector(".call-audio-progress-fill");
      const currentEl = player.querySelector(".call-audio-current");
      const durationEl = player.querySelector(".call-audio-duration");
      const muteBtn = player.querySelector(".call-audio-mute");
      const loadingEl = player.querySelector(".call-audio-loading");
      if (!audio || !playBtn) return;

      const showLoading = (on) => {
        if (loadingEl) loadingEl.hidden = !on;
      };

      playBtn.addEventListener("click", async () => {
        try {
          if (audio.paused) {
            claimAudio(audio, player);
            showLoading(true);
            await audio.play();
            setPlayerPlaying(player, true);
          } else {
            audio.pause();
            setPlayerPlaying(player, false);
            if (activeAudio === audio) {
              activeAudio = null;
              activePlayerEl = null;
            }
          }
        } catch {
          setPlayerError(player, true);
        } finally {
          showLoading(false);
        }
      });

      seek?.addEventListener("input", () => {
        const duration = Number.isFinite(audio.duration) && audio.duration > 0
          ? audio.duration
          : Number(player.dataset.duration) || 0;
        if (!duration) return;
        audio.currentTime = (Number(seek.value) / 1000) * duration;
        updatePlayerProgress(player, audio, seek, fill, currentEl, durationEl);
      });

      muteBtn?.addEventListener("click", () => {
        audio.muted = !audio.muted;
        muteBtn.classList.toggle("is-muted", audio.muted);
        muteBtn.setAttribute("aria-label", audio.muted ? "Unmute" : "Mute");
      });

      audio.addEventListener("loadedmetadata", () => {
        showLoading(false);
        updatePlayerProgress(player, audio, seek, fill, currentEl, durationEl);
      });

      audio.addEventListener("timeupdate", () => {
        updatePlayerProgress(player, audio, seek, fill, currentEl, durationEl);
        highlightTranscriptAtTime(player, audio.currentTime);
      });

      audio.addEventListener("ended", () => {
        setPlayerPlaying(player, false);
        audio.currentTime = 0;
        updatePlayerProgress(player, audio, seek, fill, currentEl, durationEl);
        if (activeAudio === audio) {
          activeAudio = null;
          activePlayerEl = null;
        }
        highlightTranscriptAtTime(player, -1);
      });

      audio.addEventListener("waiting", () => showLoading(true));
      audio.addEventListener("canplay", () => showLoading(false));

      audio.addEventListener("error", () => {
        showLoading(false);
        setPlayerError(player, true);
      });

      updatePlayerProgress(player, audio, seek, fill, currentEl, durationEl);
    });
  }

  function parseExtractedInfo(raw) {
    if (!raw) return {};
    if (typeof raw === "object") return raw;
    if (typeof raw === "string") {
      try {
        const parsed = JSON.parse(raw);
        return parsed && typeof parsed === "object" ? parsed : {};
      } catch {
        return {};
      }
    }
    return {};
  }

  function extractLatencyInteractions(call) {
    const info = parseExtractedInfo(call?.extracted_info);
    const metrics = info?.quality_scores?.metrics || {};
    const perTurn = metrics.latency_per_interaction;
    if (Array.isArray(perTurn) && perTurn.length) {
      return perTurn.map((row, idx) => ({
        turn: row.turn || idx + 1,
        endpointing_ms: row.endpointing_ms ?? null,
        llm_ms: row.llm_ms ?? null,
        tts_ms: row.tts_ms ?? null,
        stt_ms: row.stt_ms ?? null,
        buffer_ms: row.buffer_ms ?? null,
        ttfa_server_ms: row.ttfa_server_ms ?? null,
        ttfa_estimated_ms: row.ttfa_estimated_ms ?? null,
        e2e_ms: row.e2e_ms ?? null,
      }));
    }

    const llm = Array.isArray(metrics.response_times_ms) ? metrics.response_times_ms : [];
    const tts = Array.isArray(metrics.tts_latencies_ms) ? metrics.tts_latencies_ms : [];
    const count = Math.max(llm.length, tts.length);
    if (!count) return [];

    return Array.from({ length: count }, (_, idx) => ({
      turn: idx + 1,
      endpointing_ms: null,
      llm_ms: idx < llm.length ? llm[idx] : null,
      tts_ms: idx < tts.length ? tts[idx] : null,
      stt_ms: null,
      buffer_ms: null,
      ttfa_server_ms: null,
      ttfa_estimated_ms: null,
      e2e_ms: null,
    }));
  }

  function latencyStackSeconds(row) {
    const endpointing = Number(row.endpointing_ms);
    const llm = Number(row.llm_ms);
    const tts = Number(row.tts_ms);
    const stt = Number(row.stt_ms);
    const buffer = Number(row.buffer_ms);
    const ttfaEst = Number(row.ttfa_estimated_ms);
    const ttfaServer = Number(row.ttfa_server_ms);
    const e2e = Number(row.e2e_ms);

    const endpointingS = Number.isFinite(endpointing) && endpointing > 0 ? endpointing / 1000 : 0;
    const llmS = Number.isFinite(llm) && llm > 0 ? llm / 1000 : 0;
    const ttsS = Number.isFinite(tts) && tts > 0 ? tts / 1000 : 0;
    const sttS = Number.isFinite(stt) && stt > 0 ? stt / 1000 : 0;
    const bufferS = Number.isFinite(buffer) && buffer > 0 ? buffer / 1000 : 0;
    const ttfaEstS = Number.isFinite(ttfaEst) && ttfaEst > 0 ? ttfaEst / 1000 : 0;
    const ttfaServerS = Number.isFinite(ttfaServer) && ttfaServer > 0 ? ttfaServer / 1000 : 0;
    const e2eS = Number.isFinite(e2e) && e2e > 0 ? e2e / 1000 : 0;

    let pipelineS = 0;
    if (bufferS > 0) {
      pipelineS = bufferS;
    } else if (e2eS > 0 || ttfaServerS > 0) {
      const serverTotal = ttfaServerS > 0 ? ttfaServerS : e2eS;
      pipelineS = Math.max(0, serverTotal - endpointingS - sttS - llmS);
    } else if (ttsS > 0) {
      pipelineS = ttsS;
    }

    const stackedS = endpointingS + sttS + llmS + pipelineS;
    const totalS = ttfaEstS > 0
      ? ttfaEstS
      : ttfaServerS > 0
        ? ttfaServerS
        : e2eS > 0
          ? e2eS
          : stackedS;
    return { endpointingS, llmS, ttsS, sttS, pipelineS, e2eS, ttfaEstS, ttfaServerS, totalS };
  }

  function latencyPercentile(values, p) {
    const sorted = values.filter((v) => Number.isFinite(v) && v > 0).sort((a, b) => a - b);
    if (!sorted.length) return null;
    if (sorted.length === 1) return sorted[0];
    const rank = (p / 100) * (sorted.length - 1);
    const lower = Math.floor(rank);
    const upper = Math.ceil(rank);
    if (lower === upper) return sorted[lower];
    const weight = rank - lower;
    return sorted[lower] * (1 - weight) + sorted[upper] * weight;
  }

  function summarizeCallLatency(interactions) {
    const ttfaValues = interactions
      .map((row) => {
        const est = Number(row.ttfa_estimated_ms);
        if (Number.isFinite(est) && est > 0) return est;
        const server = Number(row.ttfa_server_ms);
        if (Number.isFinite(server) && server > 0) return server;
        const e2e = Number(row.e2e_ms);
        if (Number.isFinite(e2e) && e2e > 0) return e2e;
        return null;
      })
      .filter((v) => v != null);

    if (!ttfaValues.length) return null;

    const p50 = latencyPercentile(ttfaValues, 50);
    const p95 = latencyPercentile(ttfaValues, 95);
    const worst = Math.max(...ttfaValues);
    const worstTurn = interactions.find((row) => {
      const est = Number(row.ttfa_estimated_ms);
      const server = Number(row.ttfa_server_ms);
      const e2e = Number(row.e2e_ms);
      const value = Number.isFinite(est) && est > 0
        ? est
        : Number.isFinite(server) && server > 0
          ? server
          : e2e;
      return value === worst;
    });

    return {
      p50,
      p95,
      worst,
      worstTurn: worstTurn?.turn ?? null,
      hasEstimated: interactions.some((row) => Number(row.ttfa_estimated_ms) > 0),
      slowCall: p95 != null && p95 > 1500,
    };
  }

  function destroyLatencyChart() {
    if (latencyChart) {
      latencyChart.destroy();
      latencyChart = null;
    }
  }

  function formatLatencySeconds(ms) {
    const n = Number(ms);
    if (!Number.isFinite(n) || n < 0) return "—";
    return `${(n / 1000).toFixed(2)}s`;
  }

  function renderLatencySection(call) {
    const interactions = extractLatencyInteractions(call);
    if (!interactions.length) {
      return `
        <div class="call-detail-section call-latency-section">
          <h4 class="call-detail-section-title">Latency per Interaction</h4>
          <p class="call-detail-empty">Latency data will appear after new calls complete with the updated agent.</p>
        </div>
      `;
    }

    const hasTtfa = interactions.some(
      (row) => Number(row.ttfa_estimated_ms) > 0 || Number(row.ttfa_server_ms) > 0 || Number(row.e2e_ms) > 0,
    );
    const summary = summarizeCallLatency(interactions);
    const summaryHtml = summary
      ? `
        <div class="call-latency-summary${summary.slowCall ? " call-latency-summary--slow" : ""}">
          <div class="call-latency-summary-item">
            <span class="call-latency-summary-label">p50 TTFA</span>
            <strong>${formatLatencySeconds(summary.p50)}</strong>
          </div>
          <div class="call-latency-summary-item">
            <span class="call-latency-summary-label">p95 TTFA</span>
            <strong>${formatLatencySeconds(summary.p95)}</strong>
          </div>
          <div class="call-latency-summary-item">
            <span class="call-latency-summary-label">Worst turn</span>
            <strong>#${summary.worstTurn ?? "—"} · ${formatLatencySeconds(summary.worst)}</strong>
          </div>
        </div>
      `
      : "";

    return `
      <div class="call-detail-section call-latency-section">
        <h4 class="call-detail-section-title">Latency per Interaction</h4>
        ${summaryHtml}
        <p class="call-latency-note muted">
          ${hasTtfa
            ? "Voice-to-voice TTFA = you stop speaking → first agent audio at the handset (+ ~200ms PSTN estimate). Endpointing is pre-STT silence; reply buffer is first LLM token through speakable audio. Soniox semantic endpointing will shrink the endpointing bar when enabled."
            : "LLM = first token only (not full reply). Older calls may miss endpointing/TTFA — place a new call after agent restart."}
        </p>
        <div class="call-latency-chart-wrap">
          <canvas id="call-latency-chart" aria-label="Latency per interaction chart"></canvas>
        </div>
        <div class="call-latency-legend" aria-hidden="true">
          <span class="call-latency-legend-item call-latency-legend-item--endpointing">Endpointing</span>
          <span class="call-latency-legend-item call-latency-legend-item--stt">STT detect</span>
          <span class="call-latency-legend-item call-latency-legend-item--llm">LLM (1st token)</span>
          <span class="call-latency-legend-item call-latency-legend-item--pipe">Reply buffer</span>
        </div>
      </div>
    `;
  }

  function initLatencyChart(root, call) {
    destroyLatencyChart();
    const canvas = root?.querySelector("#call-latency-chart");
    const interactions = extractLatencyInteractions(call);
    if (!canvas || !interactions.length || typeof Chart === "undefined") return;

    const labels = interactions.map((row) => `#${row.turn}`);
    const stacks = interactions.map((row) => latencyStackSeconds(row));
    const endpointingData = stacks.map((s) => s.endpointingS);
    const sttData = stacks.map((s) => s.sttS);
    const llmData = stacks.map((s) => s.llmS);
    const pipeData = stacks.map((s) => s.pipelineS);
    const maxVal = Math.max(0.2, ...stacks.map((s) => s.totalS));

    latencyChart = new Chart(canvas, {
      type: "bar",
      data: {
        labels,
        datasets: [
          {
            label: "Endpointing",
            data: endpointingData,
            backgroundColor: "rgba(59, 130, 246, 0.9)",
            borderColor: "rgba(96, 165, 250, 0.9)",
            borderWidth: 1,
            borderRadius: 2,
            stack: "latency",
          },
          {
            label: "STT detect",
            data: sttData,
            backgroundColor: "rgba(96, 165, 250, 0.9)",
            borderColor: "rgba(147, 197, 253, 0.9)",
            borderWidth: 1,
            borderRadius: 2,
            stack: "latency",
          },
          {
            label: "LLM (1st token)",
            data: llmData,
            backgroundColor: "rgba(139, 92, 246, 0.92)",
            borderColor: "rgba(167, 139, 250, 0.9)",
            borderWidth: 1,
            borderRadius: 2,
            stack: "latency",
          },
          {
            label: "Reply buffer",
            data: pipeData,
            backgroundColor: "rgba(148, 163, 184, 0.85)",
            borderColor: "rgba(203, 213, 225, 0.9)",
            borderWidth: 1,
            borderRadius: 4,
            stack: "latency",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            backgroundColor: "rgba(255, 255, 255, 0.96)",
            titleColor: "#111827",
            bodyColor: "#374151",
            borderColor: "rgba(0, 0, 0, 0.08)",
            borderWidth: 1,
            padding: 10,
            displayColors: true,
            callbacks: {
              title(items) {
                return items[0]?.label || "";
              },
              label(ctx) {
                const value = Number(ctx.raw) || 0;
                if (value <= 0) return null;
                return `${ctx.dataset.label} : ${value.toFixed(2)}s`;
              },
              afterBody(items) {
                const idx = items[0]?.dataIndex ?? 0;
                const row = interactions[idx];
                const ttfaEst = Number(row?.ttfa_estimated_ms);
                const ttfaServer = Number(row?.ttfa_server_ms);
                const e2e = Number(row?.e2e_ms);
                const lines = [];
                if (Number.isFinite(ttfaEst) && ttfaEst > 0) {
                  lines.push(`Total (TTFA est.) : ${(ttfaEst / 1000).toFixed(2)}s`);
                } else if (Number.isFinite(ttfaServer) && ttfaServer > 0) {
                  lines.push(`Total (TTFA server) : ${(ttfaServer / 1000).toFixed(2)}s`);
                } else if (Number.isFinite(e2e) && e2e > 0) {
                  lines.push(`Total (E2E) : ${(e2e / 1000).toFixed(2)}s`);
                }
                return lines;
              },
            },
          },
        },
        scales: {
          x: {
            stacked: true,
            grid: { display: false },
            ticks: {
              color: "rgba(255, 255, 255, 0.72)",
              font: { size: 11, weight: "600" },
            },
            border: { display: false },
          },
          y: {
            stacked: true,
            beginAtZero: true,
            suggestedMax: Math.ceil(maxVal * 10) / 10,
            grid: {
              color: "rgba(255, 255, 255, 0.08)",
              borderDash: [4, 4],
            },
            ticks: {
              color: "rgba(255, 255, 255, 0.45)",
              font: { size: 10 },
              callback: (v) => v,
            },
            border: { display: false },
          },
        },
      },
    });
  }

  function mountCallDetailBody(body, call, callId) {
    if (!body) return;
    destroyLatencyChart();
    closeThinkingPanel();
    body.innerHTML = renderDetailBody(call);
    body.dataset.callId = callId;
    updateInspectThinkingButton(call);
    initRecordingPlayers(body);
    bindTranscriptSeek(body);
    initLatencyChart(body, call);
  }

  function formatCost(value) {
    if (value === null || value === undefined || value === "") return "—";
    const n = Number(value);
    return Number.isFinite(n) ? `$${n.toFixed(4)}` : "—";
  }

  function callStatusBadge(row) {
    const status = String(row.call_status || "").toLowerCase();
    if (status === "completed" || status === "answered") return tag("Completed", "ok");
    if (status === "failed") return tag("Failed", "warn");
    if (status === "no_answer") return tag("No answer", "warn");
    if (status === "busy") return tag("Busy", "warn");
    if (status === "cancelled") return tag("Cancelled", "warn");
    if (row.was_booked) return tag("Booked", "ok");
    if ((row.duration_seconds || 0) > 0) return tag("Completed", "ok");
    return tag(status || "Unknown", "warn");
  }

  function readFilters() {
    const transferred = $("cl-filter-transferred")?.value || "";
    return {
      agent_id: $("cl-filter-agent")?.value || "",
      call_status: $("cl-filter-status")?.value || "",
      call_direction: $("cl-filter-direction")?.value || "",
      channel_type: $("cl-filter-channel")?.value || "",
      call_transferred: transferred === "" ? "" : transferred,
      call_id: ($("cl-filter-call-id")?.value || "").trim(),
      from_number: ($("cl-filter-from")?.value || "").trim(),
      to_number: ($("cl-filter-to")?.value || "").trim(),
      phone: ($("cl-filter-phone")?.value || "").trim(),
      bulk_call_id: ($("cl-filter-bulk-id")?.value || "").trim(),
      bulk_only: $("cl-filter-bulk-only")?.checked ? "true" : "",
      duration_min: ($("cl-filter-dur-min")?.value || "").trim(),
      duration_max: ($("cl-filter-dur-max")?.value || "").trim(),
      date_from: $("cl-filter-date-from")?.value || "",
      date_to: $("cl-filter-date-to")?.value || "",
    };
  }

  function buildQueryString(extra = {}) {
    const f = { ...readFilters(), page: clState.page, page_size: clState.pageSize, ...extra };
    const params = new URLSearchParams();
    Object.entries(f).forEach(([key, value]) => {
      if (value !== "" && value !== null && value !== undefined) {
        params.set(key, String(value));
      }
    });
    return params.toString();
  }

  async function loadAgents() {
    try {
      const res = await apiJson("/api/agents?limit=200");
      clState.agents = res.data || [];
      const select = $("cl-filter-agent");
      if (!select) return;
      const current = select.value;
      select.innerHTML = `<option value="">All assistants</option>${clState.agents
        .map((a) => `<option value="${esc(a.id)}">${esc(a.name)}</option>`)
        .join("")}`;
      if (current) select.value = current;
    } catch {
      clState.agents = [];
    }
  }

  async function loadCalls(page = clState.page) {
    clState.page = page;
    clState.loading = true;
    renderTable();

    try {
      const qs = buildQueryString();
      const res = await apiJson(`/api/calls?${qs}`);
      clState.rows = res.data || [];
      clState.total = res.total ?? clState.rows.length;
      clState.pageSize = res.page_size ?? clState.pageSize;
      clState.totalPages = res.total_pages ?? 1;
    } catch (e) {
      if (!String(e.message).includes("Authentication")) {
        showToast(e.message || "Failed to load call logs", "error");
      }
      clState.rows = [];
      clState.total = 0;
      clState.totalPages = 1;
    } finally {
      clState.loading = false;
      renderTable();
      renderPagination();
    }
  }

  function renderPagination() {
    const root = $("call-logs-pagination");
    const label = $("call-logs-results-label");
    if (label) {
      label.textContent = clState.loading
        ? "Loading calls…"
        : `${clState.total} call${clState.total === 1 ? "" : "s"}`;
    }
    if (!root) return;

    const prevDisabled = clState.page <= 1 || clState.loading;
    const nextDisabled = clState.page >= clState.totalPages || clState.loading;

    root.innerHTML = `
      <span class="page-meta">Page ${clState.page} of ${clState.totalPages}</span>
      <button type="button" class="btn btn-ghost btn-sm" id="cl-page-prev" ${prevDisabled ? "disabled" : ""}>Prev</button>
      <button type="button" class="btn btn-ghost btn-sm" id="cl-page-next" ${nextDisabled ? "disabled" : ""}>Next</button>
    `;
    $("cl-page-prev")?.addEventListener("click", () => loadCalls(clState.page - 1));
    $("cl-page-next")?.addEventListener("click", () => loadCalls(clState.page + 1));
  }

  function renderTable() {
    const tbody = $("call-logs-body");
    if (!tbody) return;

    if (clState.loading) {
      tbody.innerHTML = `<tr><td colspan="9"><span class="vk-skeleton">Loading…</span></td></tr>`;
      return;
    }

    if (!clState.rows.length) {
      tbody.innerHTML = `<tr><td colspan="9">No calls match your filters.</td></tr>`;
      return;
    }

    tbody.innerHTML = clState.rows.map((r) => {
      const recording = (r.recording_url || "").trim();
      const from = displayPhone(r.from_number || r.phone_number);
      const to = displayPhone(r.to_number);
      return `
        <tr class="call-log-row" data-call-id="${esc(r.id)}">
          <td>${safeDate(r.created_at)}</td>
          <td>${esc(r.agent_name || "Unassigned")}</td>
          <td>${from}</td>
          <td>${to}</td>
          <td>${formatDuration(r.duration_seconds)}</td>
          <td>${esc((r.call_direction || "outbound").replace(/_/g, " "))}</td>
          <td>${callStatusBadge(r)}</td>
          <td>${esc(r.ended_by || "—")}</td>
          <td class="call-logs-actions-cell">
            <div class="call-logs-action-group">
              <button type="button" class="call-logs-detail-btn" data-view-call="${esc(r.id)}" title="View transcript and summary">
                <svg class="call-logs-action-icon" viewBox="0 0 16 16" aria-hidden="true"><path d="M2.5 8s2.2-4.5 5.5-4.5S13.5 8 13.5 8s-2.2 4.5-5.5 4.5S2.5 8 2.5 8z" fill="none" stroke="currentColor" stroke-width="1.35"/><circle cx="8" cy="8" r="1.8" fill="currentColor"/></svg>
                <span>View details</span>
              </button>
              ${recording ? `
                <button type="button" class="call-logs-recording-btn" data-play-call="${esc(r.id)}" title="Play recording">
                  <svg class="call-logs-action-icon" viewBox="0 0 16 16" aria-hidden="true"><circle cx="8" cy="8" r="5.5" fill="none" stroke="currentColor" stroke-width="1.35"/><path d="M6.6 5.8v4.4l3.8-2.2-3.8-2.2z" fill="currentColor"/></svg>
                  <span>Play</span>
                </button>
              ` : ""}
            </div>
          </td>
        </tr>
      `;
    }).join("");
  }

  function resetFilters() {
    [
      "cl-filter-agent", "cl-filter-status", "cl-filter-direction", "cl-filter-channel",
      "cl-filter-transferred", "cl-filter-call-id", "cl-filter-from", "cl-filter-to",
      "cl-filter-phone", "cl-filter-bulk-id", "cl-filter-dur-min", "cl-filter-dur-max",
      "cl-filter-date-from", "cl-filter-date-to",
    ].forEach((id) => {
      const el = $(id);
      if (!el) return;
      if (el.type === "checkbox") el.checked = false;
      else el.value = "";
    });
    clState.page = 1;
    loadCalls(1);
  }

  function toggleFilters() {
    clState.filtersOpen = !clState.filtersOpen;
    $("call-logs-filters")?.classList.toggle("collapsed", !clState.filtersOpen);
    const btn = $("call-logs-toggle-filters");
    if (btn) btn.textContent = clState.filtersOpen ? "Hide filters" : "Show filters";
  }

  async function exportCsv() {
    try {
      const qs = buildQueryString();
      const res = await apiFetch(`/api/calls/export/csv?${qs}`);
      if (!res.ok) throw new Error(`Export failed (${res.status})`);
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement("a");
      a.href = url;
      a.download = `call_logs_${new Date().toISOString().slice(0, 10)}.csv`;
      a.click();
      URL.revokeObjectURL(url);
      showToast("CSV export started", "success");
    } catch (e) {
      showToast(e.message || "CSV export failed", "error");
    }
  }

  const EXTRACTED_INFO_HIDDEN_KEYS = new Set([
    "speculative_thinking",
    "call_audit",
    "quality_scores",
    "prompt_version",
    "is_canary",
  ]);

  function extractSpeculativeThinking(call) {
    const info = call?.extracted_info;
    if (!info || typeof info !== "object") return [];
    const raw = info.speculative_thinking;
    if (!Array.isArray(raw)) return [];
    return raw.filter((item) => item && typeof item === "object");
  }

  function formatThinkingTime(iso) {
    if (!iso) return "—";
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? String(iso) : d.toLocaleTimeString("en-IN", { hour12: false });
  }

  function thinkingEventLabel(event) {
    const labels = {
      scheduled: "Scheduled",
      skipped_short_speech: "Skipped — speech too short",
      prep_ready: "Prep ready",
      prep_failed: "Prep failed",
      injected: "Injected into turn",
      cancelled: "Cancelled",
    };
    return labels[event] || String(event || "Event");
  }

  function renderSpeculativeThinking(entries) {
    if (!entries.length) {
      return `<p class="call-detail-empty">No speculative prep ran on this call.</p>`;
    }

    return entries.map((entry, idx) => {
      const event = String(entry.event || "");
      const hint = String(entry.hint || "").trim();
      const preview = String(entry.spoken_preview || "").trim();
      const meta = [];
      if (entry.for_turn != null) meta.push(`Turn ${entry.for_turn}`);
      if (entry.est_speech_ms != null) meta.push(`est speech ${entry.est_speech_ms}ms`);
      if (entry.min_speech_ms != null) meta.push(`min ${entry.min_speech_ms}ms`);
      if (entry.reason) meta.push(String(entry.reason));

      return `
        <article class="call-thinking-entry call-thinking-entry--${esc(event)}">
          <div class="call-thinking-entry-head">
            <span class="call-thinking-event">${esc(thinkingEventLabel(event))}</span>
            <time class="call-thinking-time">${esc(formatThinkingTime(entry.at))}</time>
          </div>
          ${meta.length ? `<p class="call-thinking-meta muted">${esc(meta.join(" · "))}</p>` : ""}
          ${preview ? `
            <div class="call-thinking-block">
              <span class="call-thinking-block-label">Agent was saying</span>
              <p class="call-thinking-block-text">${esc(preview)}</p>
            </div>
          ` : ""}
          ${hint ? `
            <div class="call-thinking-block call-thinking-block--ideas">
              <span class="call-thinking-block-label">Prep ideas</span>
              <pre class="call-thinking-hint">${esc(hint)}</pre>
            </div>
          ` : ""}
          ${!hint && event === "prep_failed" ? `<p class="call-thinking-meta muted">Background prep did not return a hint.</p>` : ""}
        </article>
      `;
    }).join("");
  }

  function updateInspectThinkingButton(call) {
    const btn = $("btn-inspect-thinking");
    if (!btn) return;
    const entries = extractSpeculativeThinking(call);
    const hasThinking = entries.length > 0;
    btn.hidden = !hasThinking;
    btn.dataset.thinkingCount = String(entries.length);
    clState.activeSpeculativeThinking = entries;
  }

  function openThinkingPanel() {
    const panel = $("call-thinking-panel");
    const body = $("call-thinking-panel-body");
    if (!panel || !body) return;
    body.innerHTML = renderSpeculativeThinking(clState.activeSpeculativeThinking || []);
    panel.hidden = false;
  }

  function closeThinkingPanel() {
    const panel = $("call-thinking-panel");
    if (panel) panel.hidden = true;
  }

  function renderExtractedInfo(info) {
    if (!info || typeof info !== "object" || !Object.keys(info).length) {
      return `<p class="call-detail-empty">No extracted variables.</p>`;
    }
    const visible = Object.entries(info).filter(([key]) => !EXTRACTED_INFO_HIDDEN_KEYS.has(key));
    if (!visible.length) {
      return `<p class="call-detail-empty">No extracted variables.</p>`;
    }
    const rows = visible.map(([key, value]) => {
      let display = "—";
      if (value === null || value === undefined || value === "") {
        display = "—";
      } else if (typeof value === "object") {
        display = "—";
      } else {
        display = String(value);
      }
      return `
        <div class="call-extracted-item">
          <span class="call-extracted-key">${esc(key.replace(/_/g, " "))}</span>
          <span class="call-extracted-value">${esc(display)}</span>
        </div>
      `;
    }).join("");
    return `<div class="call-extracted-grid">${rows}</div>`;
  }

  function parseTranscriptBubbles(transcript) {
    const raw = String(transcript || "").trim();
    if (!raw) return [];

    const lines = raw.split("\n").map((l) => l.trim()).filter(Boolean);
    const bubbles = [];

    for (const line of lines) {
      const match = line.match(/^\[?(USER|ASSISTANT|AGENT|BOT|CALLER|HUMAN)\]?\s*:?\s*(.*)$/i);
      if (match) {
        const roleKey = match[1].toUpperCase();
        const isUser = roleKey === "USER" || roleKey === "CALLER" || roleKey === "HUMAN";
        bubbles.push({ role: isUser ? "user" : "assistant", text: match[2].trim() });
        continue;
      }
      if (bubbles.length) {
        bubbles[bubbles.length - 1].text += (bubbles[bubbles.length - 1].text ? "\n" : "") + line;
      } else {
        bubbles.push({ role: "assistant", text: line });
      }
    }

    return bubbles;
  }

  function computeBubbleTimestamps(bubbles, durationSeconds) {
    const duration = Math.max(0, Number(durationSeconds) || 0);
    if (!bubbles.length) return [];
    const weights = bubbles.map((b) => {
      const wc = String(b.text || "").split(/\s+/).filter(Boolean).length;
      return wc || 1;
    });
    const total = weights.reduce((sum, w) => sum + w, 0) || bubbles.length;
    let cursor = 0;
    return bubbles.map((bubble, idx) => {
      const ts = cursor;
      cursor += duration * (weights[idx] / total);
      return { ...bubble, ts, idx };
    });
  }

  function formatTranscriptHtml(transcript, durationSeconds = 0) {
    const raw = String(transcript || "").trim();
    if (!raw) {
      return `<p class="call-detail-empty">No transcript available for this call.</p>`;
    }

    const bubbles = computeBubbleTimestamps(parseTranscriptBubbles(transcript), durationSeconds);
    if (!bubbles.length) {
      return `<pre class="call-detail-transcript-fallback">${esc(raw)}</pre>`;
    }

    const seekable = durationSeconds > 0;
    return bubbles.map((b) => `
      <div class="transcript-bubble transcript-bubble--${b.role}${seekable ? " transcript-bubble--seekable" : ""}"
           ${seekable ? `data-ts="${b.ts.toFixed(2)}" data-idx="${b.idx}" role="button" tabindex="0" title="Jump to ${formatPlayerTime(b.ts)}"` : ""}>
        <div class="transcript-bubble-meta">
          <span class="transcript-bubble-role">${b.role === "user" ? "Caller" : "Agent"}</span>
        </div>
        <div class="transcript-bubble-text">${esc(b.text)}</div>
      </div>
    `).join("");
  }

  function highlightTranscriptAtTime(scope, currentTime) {
    const root = scope?.closest?.(".call-detail-section")?.parentElement || scope || document;
    const thread = root.querySelector?.(".call-transcript-thread");
    if (!thread) return;
    const bubbles = [...thread.querySelectorAll(".transcript-bubble--seekable")];
    if (!bubbles.length) return;

    bubbles.forEach((el) => el.classList.remove("is-active"));
    if (currentTime < 0) return;

    let active = bubbles[0];
    for (const bubble of bubbles) {
      const ts = Number(bubble.dataset.ts) || 0;
      if (ts <= currentTime) active = bubble;
      else break;
    }
    active?.classList.add("is-active");
  }

  function bindTranscriptSeek(root) {
    const thread = root?.querySelector(".call-transcript-thread");
    const player = root?.querySelector(".call-audio-player");
    const audio = player?.querySelector(".call-audio-element");
    if (!thread || !audio) return;

    const seekToBubble = async (bubble) => {
      const ts = Number(bubble?.dataset?.ts);
      if (!Number.isFinite(ts)) return;
      try {
        claimAudio(audio, player);
        audio.currentTime = ts;
        await audio.play();
        setPlayerPlaying(player, true);
        highlightTranscriptAtTime(root, ts);
      } catch {
        setPlayerError(player, true);
      }
    };

    thread.addEventListener("click", (e) => {
      const bubble = e.target.closest(".transcript-bubble--seekable");
      if (!bubble) return;
      seekToBubble(bubble);
    });

    thread.addEventListener("keydown", (e) => {
      if (e.key !== "Enter" && e.key !== " ") return;
      const bubble = e.target.closest(".transcript-bubble--seekable");
      if (!bubble) return;
      e.preventDefault();
      seekToBubble(bubble);
    });
  }

  function renderSummaryCard(callSummary, callId) {
    const text = humanizeSummary(callSummary);
    return `
      <div class="call-summary-card">
        <div class="call-summary-head">
          <div class="call-summary-title">
            <span class="call-summary-icon" aria-hidden="true">✦</span>
            <h4>Summary</h4>
          </div>
          <button type="button" class="call-summary-regen" id="btn-regenerate-summary" data-call-id="${esc(callId)}" title="Regenerate summary with AI">
            <span class="call-summary-regen-icon" aria-hidden="true">⟳</span>
            <span class="call-summary-regen-label">Regenerate with AI</span>
          </button>
        </div>
        <p class="call-summary-text">${esc(text)}</p>
      </div>
    `;
  }

  function renderDetailBody(call) {
    const recording = (call.recording_url || "").trim();
    const callSummary = call.call_summary || call.summary || "";

    return `
      <div class="call-detail-meta">
        <div class="call-detail-kv"><span class="call-detail-kv-label">Bot</span><span class="call-detail-kv-value">${esc(call.agent_name || "Unassigned")}</span></div>
        <div class="call-detail-kv"><span class="call-detail-kv-label">From</span><span class="call-detail-kv-value">${displayPhone(call.from_number || call.phone_number)}</span></div>
        <div class="call-detail-kv"><span class="call-detail-kv-label">To</span><span class="call-detail-kv-value">${displayPhone(call.to_number)}</span></div>
        <div class="call-detail-kv"><span class="call-detail-kv-label">Duration</span><span class="call-detail-kv-value">${formatDuration(call.duration_seconds)}</span></div>
        <div class="call-detail-kv"><span class="call-detail-kv-label">Status</span><span class="call-detail-kv-value">${callStatusBadge(call)}</span></div>
        <div class="call-detail-kv"><span class="call-detail-kv-label">Direction</span><span class="call-detail-kv-value">${esc(call.call_direction || "—")}</span></div>
        <div class="call-detail-kv"><span class="call-detail-kv-label">Channel</span><span class="call-detail-kv-value">${esc(call.channel_type || "—")}</span></div>
        <div class="call-detail-kv"><span class="call-detail-kv-label">Ended by</span><span class="call-detail-kv-value">${esc(call.ended_by || "—")}</span></div>
        <div class="call-detail-kv"><span class="call-detail-kv-label">Sentiment</span><span class="call-detail-kv-value">${esc(call.sentiment || "—")}</span></div>
        <div class="call-detail-kv call-detail-kv--wide"><span class="call-detail-kv-label">Call ID</span><span class="call-detail-kv-value call-detail-kv-mono">${esc(call.id || "")}</span></div>
      </div>

      <div id="call-detail-summary-wrapper">
        ${renderSummaryCard(callSummary, call.id)}
      </div>

      ${renderLatencySection(call)}

      ${recording ? `
        <div class="call-detail-section">
          <h4 class="call-detail-section-title">Recording</h4>
          <div class="call-recording-wrap">
            ${renderRecordingPlayer(recording, call.duration_seconds, call.id)}
          </div>
        </div>
      ` : ""}

      <div class="call-detail-section">
        <h4 class="call-detail-section-title">Transcript</h4>
        <div class="call-transcript-thread">${formatTranscriptHtml(call.transcript, call.duration_seconds)}</div>
      </div>

      <div class="call-detail-section">
        <h4 class="call-detail-section-title">Extracted information</h4>
        ${renderExtractedInfo(call.extracted_info)}
      </div>
    `;
  }

  async function openDetail(callId) {
    if (!callId) return;

    try {
      closeThinkingPanel();
      const res = await apiJson(`/api/calls/${encodeURIComponent(callId)}`);
      const call = res.data || {};
      const title = $("modal-call-detail-title");
      const body = $("modal-call-detail-body");
      const link = $("modal-call-transcript-link");
      if (title) title.textContent = `Call — ${safeDate(call.created_at)}`;
      if (link) {
        link.href = `/api/logs/${encodeURIComponent(callId)}/transcript`;
        link.hidden = false;
      }
      mountCallDetailBody(body, call, callId);
      openModal("modal-call-detail");
    } catch (e) {
      showToast(e.message || "Failed to load call detail", "error");
    }
  }

  function bindEvents() {
    $("call-logs-apply-btn")?.addEventListener("click", () => loadCalls(1));
    $("call-logs-reset-btn")?.addEventListener("click", resetFilters);
    $("call-logs-export-btn")?.addEventListener("click", exportCsv);
    $("call-logs-toggle-filters")?.addEventListener("click", toggleFilters);

    $("call-logs-body")?.addEventListener("click", (e) => {
      const detailBtn = e.target.closest("[data-view-call]");
      if (detailBtn?.dataset.viewCall) {
        e.stopPropagation();
        openDetail(detailBtn.dataset.viewCall);
        return;
      }

      const playBtn = e.target.closest("[data-play-call]");
      if (playBtn) {
        e.stopPropagation();
        const callId = playBtn.dataset.playCall;
        if (!callId) return;
        playCallRecording(callId).catch(() => {});
      }
    });

    $("btn-inspect-thinking")?.addEventListener("click", () => openThinkingPanel());
    $("btn-close-thinking")?.addEventListener("click", () => closeThinkingPanel());

    document.getElementById("modal-call-detail")?.addEventListener("click", async (e) => {
      if (e.target.closest("[data-close-modal]")) {
        stopAudio();
        destroyLatencyChart();
        closeThinkingPanel();
        closeModal("modal-call-detail");
        return;
      }

      const regenBtn = e.target.closest("#btn-regenerate-summary");
      if (!regenBtn) return;

      const body = $("modal-call-detail-body");
      const activeCallId = body?.dataset.callId || regenBtn.dataset.callId;
      if (!activeCallId) return;

      regenBtn.disabled = true;
      regenBtn.classList.add("call-summary-regen--loading");
      const regenLabel = regenBtn.querySelector(".call-summary-regen-label");
      if (regenLabel) regenLabel.textContent = "Generating…";
      try {
        const result = await apiJson(`/api/calls/${encodeURIComponent(activeCallId)}/summarise`, { method: "POST" });
        if (result.success) {
          showToast("Summary regenerated", "success");
          const updated = await apiJson(`/api/calls/${encodeURIComponent(activeCallId)}`);
          const call = updated.data || {};
          mountCallDetailBody(body, call, activeCallId);
        } else {
          showToast(result.error || "Failed to regenerate summary", "error");
        }
      } catch (err) {
        showToast(err.message || "Failed to regenerate summary", "error");
      } finally {
        regenBtn.disabled = false;
        regenBtn.classList.remove("call-summary-regen--loading");
        const label = regenBtn.querySelector(".call-summary-regen-label");
        if (label) label.textContent = "Regenerate with AI";
      }
    });
  }

  async function init() {
    if (!clState.initialized) {
      bindAudioLifecycle();
      bindEvents();
      clState.initialized = true;
    }
    await loadAgents();
    await loadCalls(1);
  }

  window.CallLogs = {
    init,
    load: loadCalls,
    openDetail,
    initPlayers: initRecordingPlayers,
    renderPlayer: renderRecordingPlayer,
    streamUrl: recordingStreamUrl,
    stopAudio,
    playCallRecording,
  };
})();