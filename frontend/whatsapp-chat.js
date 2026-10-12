/**
 * WhatsApp Chat History — two-panel inbox for a linked number.
 * Route: /tinglish-vk-realty/whatsapp/chat/{number_id} or /whatsapp/chat/{number_id}
 */
(function () {
  const POLL_MS = 5000;
  let numberId = null;
  let selectedConversationId = null;
  let conversations = [];
  let messages = [];
  let msgCursor = null;
  let convCursor = null;
  let eventAbort = null;
  let pollTimer = null;
  let sessionPhone = "";
  let eventsBound = false;

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

  function showMigrationHint(msg) {
    const empty = $("wa-chat-conv-empty");
    if (empty) {
      empty.hidden = false;
      empty.innerHTML = `
        <div class="wa-chat-empty-icon" aria-hidden="true">⚙️</div>
        <p>${esc(msg || "Chat history tables are missing in Supabase.")}</p>
        <p class="muted" style="font-size:0.82rem;margin-top:0.5rem">File: <code>sql/supabase_migration_v8_whatsapp_chat.sql</code></p>`;
    }
    conversations = [];
    const list = $("wa-chat-conv-list");
    if (list) list.innerHTML = "";
  }

  function formatTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return "";
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    return sameDay
      ? d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" })
      : d.toLocaleDateString("en-IN", { day: "numeric", month: "short" });
  }

  function chatUrl(id) {
    const base = window.location.pathname.includes("/tinglish-vk-realty")
      ? "/tinglish-vk-realty"
      : "";
    return `${base}/whatsapp/chat/${id}`;
  }

  function integrationsUrl() {
    return window.location.pathname.includes("/o/")
      ? window.location.pathname.replace(/\/whatsapp\/chat\/.*$/, "/dashboard")
      : "/tinglish-vk-realty/dashboard";
  }

  function showPage() {
    document.querySelectorAll(".page.vui-page").forEach((p) => p.classList.remove("active"));
    const page = $("page-whatsapp-chat");
    if (page) {
      page.classList.add("active");
      page.removeAttribute("hidden");
    }
    document.body.dataset.currentPage = "whatsapp-chat";
    if (window.state) window.state.currentPage = "whatsapp-chat";
    const title = $("page-title");
    const subtitle = $("page-subtitle");
    if (title) title.textContent = "Chat history";
    if (subtitle) {
      subtitle.textContent = sessionPhone
        ? `Every message sent and received on ${sessionPhone}.`
        : "Every message sent and received on your linked WhatsApp number.";
    }
    document.querySelector(".vui-sidebar")?.classList.add("wa-chat-sidebar-collapsed");
    document.querySelector(".vui-main")?.classList.add("wa-chat-main-full");
  }

  function hidePage() {
    $("page-whatsapp-chat")?.classList.remove("active");
    document.querySelector(".vui-sidebar")?.classList.remove("wa-chat-sidebar-collapsed");
    document.querySelector(".vui-main")?.classList.remove("wa-chat-main-full");
    window.setPage?.("integrations");
  }

  async function loadConversations({ append = false } = {}) {
    if (!numberId) return;
    const search = $("wa-chat-search")?.value?.trim() || "";
    let url = `/api/whatsapp/${encodeURIComponent(numberId)}/conversations?`;
    if (search) url += `search=${encodeURIComponent(search)}&`;
    if (append && convCursor) url += `cursor=${encodeURIComponent(convCursor)}&`;

    const data = await window.apiJson(url);
    if (data.migration_required) {
      showMigrationHint(data.detail);
      return;
    }
    sessionPhone = data.whatsapp_number || sessionPhone;
    const subtitle = $("page-subtitle");
    if (subtitle && sessionPhone) {
      subtitle.textContent = `Every message sent and received on ${sessionPhone}.`;
    }
    const items = data.items || [];
    conversations = append ? [...conversations, ...items] : items;
    convCursor = data.next_cursor || null;
    renderConversationList();
  }

  function renderConversationList() {
    const list = $("wa-chat-conv-list");
    const empty = $("wa-chat-conv-empty");
    if (!list) return;

    if (!conversations.length) {
      list.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    list.innerHTML = conversations
      .map((c) => {
        const active = c.id === selectedConversationId ? " is-active" : "";
        const unread = c.unread_count > 0 ? `<span class="wa-chat-unread">${c.unread_count}</span>` : "";
        const label = c.contact_name || c.contact_number || "Unknown";
        return `
          <button type="button" class="wa-chat-conv-item${active}" data-conv-id="${c.id}">
            <div class="wa-chat-conv-top">
              <span class="wa-chat-conv-name">${esc(label)}</span>
              <span class="wa-chat-conv-time">${formatTime(c.last_message_at)}</span>
            </div>
            <div class="wa-chat-conv-preview">${esc(c.last_message_preview || "")}${unread}</div>
          </button>`;
      })
      .join("");
  }

  async function selectConversation(convId) {
    selectedConversationId = convId;
    messages = [];
    msgCursor = null;
    renderConversationList();
    $("wa-chat-thread-empty")?.toggleAttribute("hidden", true);
    await loadMessages();
    await markRead(convId);
    renderThread();
  }

  async function loadMessages({ older = false } = {}) {
    if (!numberId || !selectedConversationId) return;
    let url = `/api/whatsapp/${encodeURIComponent(numberId)}/conversations/${encodeURIComponent(selectedConversationId)}/messages?`;
    if (older && msgCursor) url += `cursor=${encodeURIComponent(msgCursor)}&`;
    const data = await window.apiJson(url);
    const items = data.items || [];
    messages = older ? [...items, ...messages] : items;
    msgCursor = data.next_cursor || null;
  }

  function renderThread() {
    const thread = $("wa-chat-thread");
    const empty = $("wa-chat-thread-empty");
    if (!thread) return;

    if (!selectedConversationId) {
      thread.innerHTML = "";
      if (empty) empty.hidden = false;
      $("wa-chat-composer")?.toggleAttribute("hidden", true);
      return;
    }
    if (empty) empty.hidden = true;
    $("wa-chat-composer")?.toggleAttribute("hidden", false);

    const conv = conversations.find((c) => c.id === selectedConversationId);
    const head = $("wa-chat-thread-head");
    if (head) {
      head.textContent = conv?.contact_name || conv?.contact_number || "Conversation";
    }

    thread.innerHTML = messages
      .map((m) => {
        const out = m.direction === "outbound";
        const cls = out ? "wa-bubble-out" : "wa-bubble-in";
        const bot =
          out && m.sent_by === "assistant"
            ? `<span class="wa-bubble-bot" title="AI assistant">🤖</span>`
            : out && m.sent_by === "human_agent"
              ? `<span class="wa-bubble-bot" title="Human agent">👤</span>`
              : "";
        const tick = out ? `<span class="wa-bubble-status">${m.status === "read" ? "✓✓" : "✓"}</span>` : "";
        return `
          <div class="wa-bubble-row ${out ? "is-out" : "is-in"}">
            <div class="wa-bubble ${cls}">
              ${bot}
              <div class="wa-bubble-text">${esc(m.content || "")}</div>
              <div class="wa-bubble-meta">${formatTime(m.created_at)} ${tick}</div>
            </div>
          </div>`;
      })
      .join("");

    thread.scrollTop = thread.scrollHeight;
  }

  async function markRead(convId) {
    try {
      await window.apiJson(
        `/api/whatsapp/${encodeURIComponent(numberId)}/conversations/${encodeURIComponent(convId)}/read`,
        { method: "PATCH" }
      );
      const c = conversations.find((x) => x.id === convId);
      if (c) c.unread_count = 0;
      renderConversationList();
    } catch {
      /* non-fatal */
    }
  }

  async function sendMessage() {
    const input = $("wa-chat-composer-input");
    const text = input?.value?.trim();
    if (!text || !selectedConversationId) return;

    $("wa-chat-send-btn")?.setAttribute("disabled", "true");
    try {
      await window.apiJson(
        `/api/whatsapp/${encodeURIComponent(numberId)}/conversations/${encodeURIComponent(selectedConversationId)}/messages`,
        { method: "POST", body: JSON.stringify({ text }) }
      );
      if (input) input.value = "";
      messages = [];
      msgCursor = null;
      await loadMessages();
      await loadConversations();
      renderThread();
      window.showToast?.("Message sent", "success");
    } catch (err) {
      window.showToast?.(err.message || "Send failed", "error");
    } finally {
      $("wa-chat-send-btn")?.removeAttribute("disabled");
    }
  }

  function onRealtimeEvent(event) {
    if (!event || event.whatsapp_number_id !== numberId) return;
    if (event.type === "message") {
      loadConversations();
      if (event.conversation_id === selectedConversationId) {
        loadMessages().then(renderThread);
      }
    }
    if (event.type === "conversation_read" && event.conversation_id === selectedConversationId) {
      const c = conversations.find((x) => x.id === event.conversation_id);
      if (c) c.unread_count = 0;
      renderConversationList();
    }
  }

  async function connectRealtime() {
    stopRealtime();
    if (!numberId || !window.apiFetch) return;

    eventAbort = new AbortController();
    try {
      const res = await window.apiFetch(
        `/api/whatsapp/${encodeURIComponent(numberId)}/events`,
        { signal: eventAbort.signal }
      );
      if (!res.ok || !res.body) {
        startPolling();
        return;
      }
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const chunk of parts) {
          const line = chunk.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          try {
            onRealtimeEvent(JSON.parse(line.slice(6)));
          } catch {
            /* ignore malformed */
          }
        }
      }
    } catch (err) {
      if (err.name !== "AbortError") startPolling();
    }
  }

  function startPolling() {
    stopPolling();
    pollTimer = setInterval(() => {
      loadConversations();
      if (selectedConversationId) loadMessages().then(renderThread);
    }, POLL_MS);
  }

  function stopPolling() {
    if (pollTimer) clearInterval(pollTimer);
    pollTimer = null;
  }

  function stopRealtime() {
    stopPolling();
    eventAbort?.abort();
    eventAbort = null;
  }

  function bindEvents() {
    $("wa-chat-back-btn")?.addEventListener("click", () => {
      stopRealtime();
      history.pushState({}, "", integrationsUrl());
      hidePage();
    });

    $("wa-chat-search")?.addEventListener(
      "input",
      debounce(() => loadConversations(), 300)
    );

    $("wa-chat-conv-list")?.addEventListener("click", (e) => {
      const btn = e.target.closest("[data-conv-id]");
      if (!btn) return;
      selectConversation(btn.dataset.convId);
    });

    $("wa-chat-send-btn")?.addEventListener("click", sendMessage);
    $("wa-chat-new-btn")?.addEventListener("click", startNewChat);
    $("wa-chat-composer-input")?.addEventListener("keydown", (e) => {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        sendMessage();
      }
    });

    $("wa-chat-thread")?.addEventListener("scroll", async () => {
      const el = $("wa-chat-thread");
      if (!el || !msgCursor || el.scrollTop > 40) return;
      const prevH = el.scrollHeight;
      await loadMessages({ older: true });
      renderThread();
      el.scrollTop = el.scrollHeight - prevH;
    });
  }

  function debounce(fn, ms) {
    let t;
    return (...args) => {
      clearTimeout(t);
      t = setTimeout(() => fn(...args), ms);
    };
  }

  function parseRoute() {
    const m = window.location.pathname.match(/\/whatsapp\/chat\/([^/]+)/);
    return m ? decodeURIComponent(m[1]) : null;
  }

  async function startNewChat() {
    const phone = window.prompt("Contact number (with country code, e.g. 919876543210):");
    if (!phone) return;
    const text = window.prompt("First message:");
    if (!text?.trim()) return;
    try {
      await window.apiJson(
        `/api/whatsapp/${encodeURIComponent(numberId)}/conversations/start?contact_number=${encodeURIComponent(phone)}`,
        { method: "POST", body: JSON.stringify({ text: text.trim() }) }
      );
      window.showToast?.("Message queued — cold outbound pacing applies", "success");
      await loadConversations();
    } catch (err) {
      window.showToast?.(err.message || "Could not send", "error");
    }
  }

  async function open(id) {
    numberId = id;
    selectedConversationId = null;
    messages = [];
    conversations = [];
    showPage();
    if (!eventsBound) {
      bindEvents();
      eventsBound = true;
    }
    renderConversationList();
    $("wa-chat-thread-empty")?.toggleAttribute("hidden", false);
    try {
      await loadConversations();
    } catch (err) {
      window.showToast?.(err.message || "Could not load conversations", "error");
    }
    connectRealtime();
    window.renderLucideIcons?.($("page-whatsapp-chat"));
  }

  function initFromUrl() {
    const id = parseRoute();
    if (id) open(id);
  }

  window.WhatsAppChat = { open, hide: hidePage, parseRoute, initFromUrl };
})();