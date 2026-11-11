/* Builder Wood — messaging client.
 *
 * Exposes window.Messages with:
 *   Messages.loadInbox(containerId)     — render conversation list
 *   Messages.loadChat(conversationId)   — render a single conversation + open SSE
 *   Messages.sendMessage(conversationId) — send the textarea contents
 *   Messages.refreshUnreadBadge()       — update the header unread badge
 *
 * Real-time delivery:
 *   - Conversation history renders as soon as GET /conversations/{id} returns.
 *   - SSE opens in parallel. New messages append without refresh.
 *   - Sending is optimistic: your bubble shows immediately, then the server
 *     echo (via SSE) is deduped by message_id.
 */
(function () {
  "use strict";

  const token = () => localStorage.getItem("bw_token");
  const myUserId = () => localStorage.getItem("bw_user_id");

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, (c) => ({
      "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
    }[c]));
  }

  function formatTime(iso) {
    if (!iso) return "";
    try {
      const d = new Date(iso);
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    } catch { return ""; }
  }

  // ============================================================
  // Inbox
  // ============================================================
  async function loadInbox(containerId) {
    const host = document.getElementById(containerId);
    if (!host) return;
    const t = token();
    if (!t) { window.location.href = "/login"; return; }

    try {
      const res = await fetch("/conversations", {
        headers: { "Authorization": `Bearer ${t}` },
      });
      if (res.status === 401) { window.location.href = "/login"; return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const convs = await res.json();

      if (!Array.isArray(convs) || convs.length === 0) {
        host.innerHTML =
          '<div class="empty-state">' +
            '<div class="mark">💬</div>' +
            '<p>No conversations yet.</p>' +
            '<p><a href="/" style="color:var(--wood-med);font-weight:700;">Browse products →</a></p>' +
          '</div>';
        return;
      }

      host.innerHTML = convs.map((c) => {
        const unread = Number(c.unread_count) || 0;
        const initial = (c.other_party_name || "?").trim().charAt(0).toUpperCase();
        const preview = c.last_message_preview
          ? esc(c.last_message_preview)
          : '<em style="color:var(--muted);">No messages yet</em>';
        const when = c.last_message_at
          ? new Date(c.last_message_at).toLocaleDateString()
          : "";

        return (
          '<a class="conv-item' + (unread > 0 ? " unread" : "") + '"' +
            ' href="/messages/' + encodeURIComponent(c.conversation_id) + '">' +
            '<div class="conv-avatar">' + esc(initial) + '</div>' +
            '<div class="conv-body">' +
              '<div class="conv-head">' +
                '<span class="conv-name">' + esc(c.other_party_name || "Unknown") + '</span>' +
                '<span class="conv-time">' + esc(when) + '</span>' +
              '</div>' +
              '<div class="conv-preview">' + preview + '</div>' +
              '<div style="display:flex; justify-content:space-between; align-items:center; margin-top:4px;">' +
                '<span class="conv-role">' + esc(c.other_party_role || "") + '</span>' +
                (unread > 0 ? '<span class="conv-badge">' + unread + '</span>' : "") +
              '</div>' +
            '</div>' +
          '</a>'
        );
      }).join("");

      const totalUnread = convs.reduce((n, c) => n + (Number(c.unread_count) || 0), 0);
      const badge = document.getElementById("total-unread");
      if (badge) {
        badge.textContent = totalUnread > 0
          ? `${totalUnread} unread`
          : "";
      }
    } catch (err) {
      console.error("loadInbox failed", err);
      host.innerHTML =
        '<div class="empty-state">' +
          '<div class="mark">⚠️</div>' +
          '<p>Could not load conversations. Try again.</p>' +
        '</div>';
    }
  }

  // ============================================================
  // Chat view
  // ============================================================
  let activeConversationId = null;
  let activeEventSource = null;
  const renderedIds = new Set();
  let sending = false;

  function showError(msg) {
    const el = document.getElementById("chat-error");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
  }
  function clearError() {
    const el = document.getElementById("chat-error");
    if (!el) return;
    el.classList.remove("show");
    el.textContent = "";
  }

  function appendMessage(m, opts = {}) {
    const host = document.getElementById("chat-messages");
    if (!host || !m || m.is_flagged) return;

    // Remove the loading placeholder on first real message.
    host.querySelectorAll(".loading-text").forEach((el) => el.remove());

    const id = m.message_id != null ? String(m.message_id) : null;
    if (id && renderedIds.has(id)) return;
    if (id) renderedIds.add(id);

    const isMine = m.sender_id != null
      && myUserId() != null
      && String(m.sender_id) === String(myUserId());

    const row = document.createElement("div");
    row.className = "msg-row " + (isMine ? "sent" : "received");
    if (opts.pending) row.classList.add("pending");
    row.dataset.messageId = id || "";

    const bubble = document.createElement("div");
    bubble.className = "msg-bubble";
    bubble.textContent = m.message_text;

    const meta = document.createElement("div");
    meta.className = "msg-meta";
    meta.textContent = formatTime(m.created_at);

    const wrap = document.createElement("div");
    wrap.appendChild(bubble);
    wrap.appendChild(meta);

    row.appendChild(wrap);
    host.appendChild(row);
    host.scrollTop = host.scrollHeight;
  }

  function renderConversation(conv) {
    const title = document.getElementById("chat-title");
    const role  = document.getElementById("chat-role");
    const wood  = document.getElementById("wood-context");
    const host  = document.getElementById("chat-messages");

    if (title) title.textContent = conv.other_party_name || "Conversation";
    if (role)  role.textContent  = (conv.other_party_role || "").toUpperCase();

    if (wood) {
      if (conv.wood_context) {
        const w = conv.wood_context;
        wood.style.display = "block";
        wood.innerHTML =
          'About: <a href="/product/' + encodeURIComponent(w.wood_id) + '">' +
            esc(w.local_name || "Timber") +
          '</a>' +
          (w.size_label ? ' · ' + esc(w.size_label) : '') +
          (w.price != null ? ' · GH₵' + Number(w.price).toFixed(2) : '');
      } else {
        wood.style.display = "none";
        wood.innerHTML = "";
      }
    }

    if (host) {
      host.innerHTML = "";
      renderedIds.clear();

      const msgs = Array.isArray(conv.messages) ? conv.messages : [];
      if (!msgs.length) {
        const p = document.createElement("p");
        p.className = "loading-text";
        p.style.padding = "20px";
        p.textContent = "No messages yet. Say hello.";
        host.appendChild(p);
      } else {
        msgs.forEach((m) => appendMessage(m));
      }
    }
  }

  async function loadChat(conversationId) {
    activeConversationId = Number(conversationId);
    const t = token();
    if (!t) { window.location.href = "/login"; return; }

    // 1. Render conversation history FIRST. Never wait for the stream.
    try {
      const res = await fetch(`/conversations/${activeConversationId}`, {
        headers: { "Authorization": `Bearer ${t}` },
      });

      if (res.status === 401) { window.location.href = "/login"; return; }
      if (res.status === 403) { showError("You are not part of this conversation."); return; }
      if (res.status === 404) { showError("Conversation not found."); return; }
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const conv = await res.json();
      renderConversation(conv);

      // Mark read in the background. Do not block rendering.
      fetch(`/conversations/${activeConversationId}/mark-read`, {
        method: "POST",
        headers: { "Authorization": `Bearer ${t}` },
      }).catch(() => {});
    } catch (err) {
      console.error("loadChat failed", err);
      showError("Could not load the conversation.");
    }

    // 2. Open the stream. New messages append as they arrive.
    openStream(activeConversationId);
  }

  function openStream(conversationId) {
    if (activeEventSource) {
      try { activeEventSource.close(); } catch { /* noop */ }
      activeEventSource = null;
    }
    const t = token();
    if (!t) return;

    const es = new EventSource(
      `/conversations/${conversationId}/stream?token=${encodeURIComponent(t)}`
    );
    activeEventSource = es;

    es.addEventListener("open", () => {
      // Optional: could light up a "live" indicator; not required.
    });

    es.addEventListener("message", (ev) => {
      try {
        const m = JSON.parse(ev.data);
        appendMessage(m);
      } catch { /* ignore malformed */ }
    });

    es.onerror = () => {
      // Browser retries automatically. Nothing to do.
    };

    window.addEventListener("beforeunload", () => {
      try { es.close(); } catch { /* noop */ }
    }, { once: true });
  }

  // ============================================================
  // Send
  // ============================================================
  async function sendMessage(conversationId) {
    if (sending) return;
    const t = token();
    if (!t) { window.location.href = "/login"; return; }

    const input = document.getElementById("msg-input");
    const btn   = document.getElementById("msg-send");
    if (!input) return;

    const text = input.value.trim();
    if (!text) return;

    sending = true;
    if (btn) btn.disabled = true;
    clearError();

    // Optimistic bubble.
    const tempId = `tmp-${Date.now()}`;
    appendMessage({ message_id: tempId, message_text: text }, { pending: true });
    input.value = "";

    try {
      const res = await fetch(`/conversations/${conversationId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${t}`,
        },
        body: JSON.stringify({ message_text: text }),
      });

      if (res.ok) {
        const msg = await res.json().catch(() => null);
        // Remove optimistic placeholder; the server echo arrives via SSE.
        const host = document.getElementById("chat-messages");
        host?.querySelectorAll(`[data-message-id="${tempId}"]`)
          .forEach((el) => el.remove());
        if (msg) appendMessage(msg);
        input.focus();
        return;
      }

      // Rollback.
      const host = document.getElementById("chat-messages");
      host?.querySelectorAll(`[data-message-id="${tempId}"]`)
        .forEach((el) => el.remove());
      input.value = text;

      const data = await res.json().catch(() => ({}));
      const detail = data.detail;

      if (res.status === 403 && detail && detail.code === "contact_info_blocked") {
        showError(detail.message || "Contact info is not allowed in messages.");
        return;
      }
      if (res.status === 403 && detail && detail.code === "messaging_banned") {
        showError(detail.message || "Messaging is suspended.");
        if (btn) btn.disabled = true;
        if (input) input.disabled = true;
        return;
      }
      if (res.status === 422) {
        showError("Your message could not be sent. Please rephrase.");
        return;
      }
      showError(typeof detail === "string" ? detail : "Could not send message.");
    } catch (err) {
      const host = document.getElementById("chat-messages");
      host?.querySelectorAll(`[data-message-id="${tempId}"]`)
        .forEach((el) => el.remove());
      input.value = text;
      showError("Network error. Please try again.");
    } finally {
      sending = false;
      if (btn) btn.disabled = false;
    }
  }

  // ============================================================
  // Header unread badge (called from app.js)
  // ============================================================
  async function refreshUnreadBadge() {
    const t = token();
    if (!t) return;
    try {
      const res = await fetch("/conversations/unread-count", {
        headers: { "Authorization": `Bearer ${t}` },
      });
      if (!res.ok) return;
      const data = await res.json();
      const n = Number(data.unread_count) || 0;
      const badge = document.getElementById("messages-badge");
      if (badge) {
        badge.textContent = n;
        badge.style.display = n > 0 ? "inline-block" : "none";
      }
    } catch { /* ignore */ }
  }

  // Periodic refresh of the header badge while the page is open.
  if (token()) {
    setInterval(refreshUnreadBadge, 15000);
    setTimeout(refreshUnreadBadge, 500);
  }

  // ============================================================
  // Export
  // ============================================================
  window.Messages = {
    loadInbox,
    loadChat,
    sendMessage,
    refreshUnreadBadge,
  };
})();