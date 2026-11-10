/* ============================================================
   In-app messaging shared JS.

   Used by:
   - messages_inbox.html (inbox list)
   - messages_chat.html  (single conversation)
   - index.html          (product card [💬] buttons + unread badge)

   Fixes:
   - appendMessage is defensive: guards against missing element,
     removes placeholder, and scrolls AFTER browser paints.
   - sendMessage keeps the text in the input on error so the user
     can edit and retry without retyping.
   ============================================================ */
(function (window) {
  "use strict";

  const Messages = {};

  function token() {
    return localStorage.getItem("bw_token");
  }

  function getUserId() {
    return localStorage.getItem("bw_user_id");
  }

  function fmtTime(iso) {
    if (!iso) return "";
    const d = new Date(iso);
    const now = new Date();
    const sameDay = d.toDateString() === now.toDateString();
    if (sameDay) {
      return d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
    }
    return d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c])
    );
  }

  // ============================================================
  // Inbox
  // ============================================================
  Messages.loadInbox = async function (containerId) {
    const host = document.getElementById(containerId);
    const token_ = token();
    if (!token_) {
      window.location.href = "/login";
      return;
    }

    try {
      const res = await fetch("/conversations", {
        headers: { Authorization: `Bearer ${token_}` },
      });
      if (!res.ok) throw new Error("Failed");
      const convs = await res.json();

      if (!convs.length) {
        host.innerHTML = `
          <div class="empty-state">
            <div class="mark">💬</div>
            <p><strong>No conversations yet.</strong></p>
            <p>When you message a seller about a product, it will appear here.</p>
          </div>`;
        return;
      }

      const totalUnread = convs.reduce((s, c) => s + (c.unread_count || 0), 0);
      const badge = document.getElementById("total-unread");
      if (badge) {
        badge.textContent = totalUnread > 0 ? `${totalUnread} unread` : "";
      }

      host.innerHTML = convs.map((c) => {
        const initial = (c.other_party_name || "?").charAt(0).toUpperCase();
        const unread = c.unread_count > 0;
        return `
          <a href="/messages/${c.conversation_id}" class="conv-item ${unread ? "unread" : ""}">
            <div class="conv-avatar">${esc(initial)}</div>
            <div class="conv-body">
              <div class="conv-head">
                <span class="conv-name">${esc(c.other_party_name)}</span>
                <span class="conv-time">${fmtTime(c.last_message_at)}</span>
              </div>
              <div class="conv-role">${esc(c.other_party_role)}</div>
              <div class="conv-preview">${esc(c.last_message_preview || "No messages yet")}</div>
            </div>
            ${unread ? `<span class="conv-badge">${c.unread_count}</span>` : ""}
          </a>`;
      }).join("");
    } catch (e) {
      host.innerHTML = `<p class="empty-state">Could not load conversations.</p>`;
    }
  };

  // ============================================================
  // Chat view
  // ============================================================
  let currentConvId = null;

  Messages.loadChat = async function (convId) {
    currentConvId = convId;
    const token_ = token();
    if (!token_) {
      window.location.href = "/login";
      return;
    }

    try {
      const res = await fetch(`/conversations/${convId}`, {
        headers: { Authorization: `Bearer ${token_}` },
      });
      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        throw new Error(data.detail || "Failed to load conversation");
      }
      const conv = await res.json();

      document.getElementById("chat-title").textContent = conv.other_party_name || "Chat";
      document.getElementById("chat-role").textContent = conv.other_party_role || "";

      if (conv.wood_context) {
        const banner = document.getElementById("wood-context");
        banner.style.display = "block";
        banner.innerHTML = `
          About: <a href="/product/${conv.wood_context.wood_id}">${esc(conv.wood_context.local_name)} ${esc(conv.wood_context.size_label)}</a>
          · GH₵${Number(conv.wood_context.price).toFixed(2)}`;
      }

      renderMessages(conv.messages);
    } catch (e) {
      document.getElementById("chat-messages").innerHTML =
        `<p class="loading-text">Could not load conversation.</p>`;
    }
  };

  function renderMessages(messages) {
    const host = document.getElementById("chat-messages");
    if (!messages || !messages.length) {
      host.innerHTML = `<p class="loading-text">No messages yet. Say hello 👋</p>`;
      return;
    }

    const myId = String(getUserId());
    host.innerHTML = messages.map((m) => {
      const mine = String(m.sender_id) === myId;
      const side = mine ? "sent" : "received";
      return `
        <div class="msg-row ${side}">
          <div>
            <div class="msg-bubble">${esc(m.message_text)}</div>
            <div class="msg-meta">${mine ? "You" : esc(m.sender_name)} · ${fmtTime(m.created_at)}</div>
          </div>
        </div>`;
    }).join("");

    // Scroll to bottom after render
    requestAnimationFrame(() => {
      host.scrollTop = host.scrollHeight;
    });
  }

  // ============================================================
  // Send message — keeps text on error, appends live on success
  // ============================================================
  Messages.sendMessage = async function (convId) {
    const token_ = token();
    const input = document.getElementById("msg-input");
    const btn = document.getElementById("msg-send");
    const errorBox = document.getElementById("chat-error");

    const text = input.value.trim();
    if (!text) return;

    errorBox.classList.remove("show");
    errorBox.textContent = "";

    btn.disabled = true;
    const originalValue = input.value;

    try {
      const res = await fetch(`/conversations/${convId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token_}`,
        },
        body: JSON.stringify({ message_text: text }),
      });

      let data = null;
      try { data = await res.json(); } catch (_) {}

      if (res.ok) {
        input.value = "";
        input.style.height = "auto";
        appendMessage(data);
      } else {
        const detail = (data && data.detail) || "Failed to send message.";
        errorBox.textContent = detail;
        errorBox.classList.add("show");
        // Keep text so user can edit and retry
        input.value = originalValue;
      }
    } catch (e) {
      errorBox.textContent = "Network error. Please try again.";
      errorBox.classList.add("show");
      input.value = originalValue;
    } finally {
      btn.disabled = false;
      input.focus();
    }
  };

  // ============================================================
  // Append message — defensive + proper scroll timing
  // ============================================================
  function appendMessage(msg) {
    const host = document.getElementById("chat-messages");
    if (!host) {
      console.warn("chat-messages element not found");
      return;
    }

    // Remove any placeholder text (loading state)
    host.querySelectorAll(".loading-text, .empty-state").forEach((p) => p.remove());

    const myId = String(getUserId());
    const mine = String(msg.sender_id) === myId;
    const side = mine ? "sent" : "received";

    const wrapper = document.createElement("div");
    wrapper.className = `msg-row ${side}`;
    wrapper.innerHTML = `
      <div>
        <div class="msg-bubble">${esc(msg.message_text || "")}</div>
        <div class="msg-meta">${mine ? "You" : esc(msg.sender_name || "Unknown")} · ${fmtTime(msg.created_at)}</div>
      </div>`;

    host.appendChild(wrapper);

    // Scroll AFTER browser paints the new element
    requestAnimationFrame(() => {
      host.scrollTop = host.scrollHeight;
    });
  }

  // ============================================================
  // Start conversation from a product [💬] button
  // ============================================================
  Messages.startConversation = async function (sellerId, woodId) {
    const token_ = token();
    if (!token_) {
      window.location.href = "/login";
      return;
    }

    if (String(sellerId) === String(getUserId())) {
      alert("You can't message yourself.");
      return;
    }

    try {
      const res = await fetch("/conversations/start", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Authorization: `Bearer ${token_}`,
        },
        body: JSON.stringify({
          seller_id: parseInt(sellerId, 10),
          wood_id: woodId ? parseInt(woodId, 10) : null,
        }),
      });

      if (!res.ok) {
        const data = await res.json().catch(() => ({}));
        alert(data.detail || "Could not open conversation");
        return;
      }

      const conv = await res.json();
      window.location.href = `/messages/${conv.conversation_id}`;
    } catch (e) {
      alert("Network error");
    }
  };

  // ============================================================
  // Unread badge (header)
  // ============================================================
  Messages.refreshUnreadBadge = async function () {
    const token_ = token();
    if (!token_) return 0;

    try {
      const res = await fetch("/conversations/unread-count", {
        headers: { Authorization: `Bearer ${token_}` },
      });
      if (!res.ok) return 0;
      const data = await res.json();
      const badge = document.getElementById("messages-badge");
      if (badge) {
        if (data.unread_count > 0) {
          badge.textContent = data.unread_count > 99 ? "99+" : data.unread_count;
          badge.style.display = "inline-grid";
        } else {
          badge.style.display = "none";
        }
      }
      return data.unread_count;
    } catch (e) {
      return 0;
    }
  };

  window.addEventListener("DOMContentLoaded", () => {
    if (document.getElementById("messages-badge")) {
      Messages.refreshUnreadBadge();
      setInterval(Messages.refreshUnreadBadge, 30000);
    }
  });

  window.Messages = Messages;
})(window);