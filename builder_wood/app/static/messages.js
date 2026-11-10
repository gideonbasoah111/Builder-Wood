(() => {
  "use strict";

  const token = localStorage.getItem("bw_token");
  const convId = Number(
    (window.location.pathname.match(/\/messages\/(\d+)/) || [])[1]
  );

  const form = document.getElementById("message-form");
  const input = document.getElementById("message-input");
  const sendBtn = document.getElementById("send-btn");
  const list = document.getElementById("messages-list");
  const banner = document.getElementById("message-banner");
  const statusPill = document.getElementById("conn-status");

  let sending = false;
  const renderedIds = new Set();

  function showBanner(text, kind = "err") {
    if (!banner) return;
    banner.textContent = text;
    banner.className = `message-banner ${kind}`;
    banner.hidden = false;
  }
  function clearBanner() {
    if (!banner) return;
    banner.hidden = true;
    banner.textContent = "";
  }

  function setStatus(text, cls = "") {
    if (!statusPill) return;
    statusPill.textContent = text;
    statusPill.className = `conn-status ${cls}`;
  }

  function appendMessage(m, opts = {}) {
    if (!list || !m || m.is_flagged) return;
    const id = Number(m.message_id);
    if (Number.isFinite(id) && renderedIds.has(id)) return;
    if (Number.isFinite(id)) renderedIds.add(id);

    const el = document.createElement("div");
    el.className = "msg-bubble";
    if (m.sender_id && String(m.sender_id) === String(localStorage.getItem("bw_user_id"))) {
      el.classList.add("mine");
    }
    el.dataset.messageId = m.message_id ?? "";
    el.textContent = m.message_text;
    if (opts.pending) el.classList.add("pending");

    list.appendChild(el);
    list.scrollTop = list.scrollHeight;
  }

  async function sendMessage(e) {
    if (e) e.preventDefault();
    if (sending) return;

    const text = input?.value.trim() || "";
    if (!text) return;

    if (!token) {
      window.location.href = "/login";
      return;
    }

    sending = true;
    if (sendBtn) sendBtn.disabled = true;
    clearBanner();

    // Optimistic render so the sender sees it instantly.
    const tempId = `tmp-${Date.now()}`;
    appendMessage({ message_id: tempId, message_text: text }, { pending: true });
    input.value = "";

    try {
      const res = await fetch(`/conversations/${convId}/messages`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
        },
        body: JSON.stringify({ message_text: text }),
      });

      if (res.ok) {
        const msg = await res.json().catch(() => null);
        // Remove the optimistic placeholder; the server echo will arrive
        // via SSE and be rendered once (deduped by message_id).
        const placeholder = list?.querySelector(`[data-message-id="${tempId}"]`);
        if (placeholder) placeholder.remove();
        if (msg) appendMessage(msg);
        input.focus();
        return;
      }

      // Non-OK: remove the optimistic bubble and restore the text.
      const placeholder = list?.querySelector(`[data-message-id="${tempId}"]`);
      if (placeholder) placeholder.remove();
      input.value = text;

      const data = await res.json().catch(() => ({}));
      const detail = data.detail;

      if (res.status === 403 && detail && detail.code === "contact_info_blocked") {
        showBanner(detail.message || "Contact info is not allowed.", "warn");
        return;
      }
      if (res.status === 403 && detail && detail.code === "messaging_banned") {
        showBanner(detail.message || "Messaging is suspended.", "err");
        if (sendBtn) sendBtn.disabled = true;
        input.disabled = true;
        return;
      }
      if (res.status === 422) {
        showBanner("Your message could not be sent. Please rephrase.", "err");
        return;
      }

      showBanner(
        typeof detail === "string" ? detail : "Could not send message.",
        "err"
      );
    } catch (err) {
      const placeholder = list?.querySelector(`[data-message-id="${tempId}"]`);
      if (placeholder) placeholder.remove();
      input.value = text;
      showBanner("Network error. Please try again.", "err");
    } finally {
      sending = false;
      if (sendBtn) sendBtn.disabled = false;
    }
  }

  // ONE submit path. No onclick on the button, no separate Enter handler.
  form?.addEventListener("submit", sendMessage);

  // Mark existing messages as read once the page is open.
  if (token && convId) {
    fetch(`/conversations/${convId}/mark-read`, {
      method: "POST",
      headers: { "Authorization": `Bearer ${token}` },
    }).catch(() => { /* best-effort */ });
  }

  // Real-time feed — events arrive within ~1s.
  if (token && convId) {
    setStatus("Connecting…", "connecting");
    const es = new EventSource(
      `/conversations/${convId}/stream?token=${encodeURIComponent(token)}`
    );

    es.addEventListener("open", () => setStatus("Live", "live"));

    es.addEventListener("message", (ev) => {
      try {
        appendMessage(JSON.parse(ev.data));
      } catch { /* ignore malformed */ }
    });

    // Keep EventSource alive on transient errors (browser auto-reconnects),
    // but reflect the state to the user.
    es.onerror = () => setStatus("Reconnecting…", "connecting");

    window.addEventListener("beforeunload", () => es.close());
  }
})();