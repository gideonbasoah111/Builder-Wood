/* Builder Wood storefront logic */
(function () {
  "use strict";

  var API_PATH = "/woods/";
  var $  = function (s) { return document.querySelector(s); };
  var $$ = function (s) { return Array.prototype.slice.call(document.querySelectorAll(s)); };

  var state = {
    products: [],
    category: "",
    search: "",
    sort: "featured",
    cart: []
  };

  var CATEGORY_LABEL = {
    hardwood:  "Hardwood", softwood:  "Softwood", roofing:   "Roofing",
    ceiling:   "Ceiling and battens", furniture: "Furniture",
    interior:  "Interior and doors", flooring:  "Flooring"
  };

  function esc(v) {
    return String(v == null ? "" : v).replace(/[&<>"']/g, function (c) {
      return ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c];
    });
  }
  function money(v, c) {
    var n = Number(v);
    var currency = c || "GH\u20b5";
    return Number.isFinite(n) ? currency + n.toFixed(2) : "Ask seller";
  }
  function getId(p) { return String(p.wood_id != null ? p.wood_id : p.id); }
  function getToken() { return localStorage.getItem("bw_token"); }

  /* ---- Product fetching ---- */
  function fetchProducts() {
    var params = new URLSearchParams();
    if (state.category) params.set("category", state.category);
    if (state.search)   params.set("search", state.search);
    if (state.sort)     params.set("sort", state.sort);

    return fetch(API_PATH + "?" + params.toString(), {
      headers: { "Accept": "application/json" }
    }).then(res => {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    }).then(data => {
      var rows = Array.isArray(data) ? data : (data.items || data.results || []);
      state.products = rows.map(p => {
        var catObj    = p.category || {};
        var sellerObj = p.seller   || {};
        var images    = Array.isArray(p.images) ? p.images : [];

        return Object.assign({}, p, {
          display_name: p.display_name || p.local_name || "Timber",
          local_name:   p.local_name || "",
          category_slug: String(catObj.slug || p.category_slug || "").toLowerCase(),
          category_name: catObj.category_name || p.category_name || "",
          seller_id:     p.seller_id || null,
          seller_name:   sellerObj.name || p.seller_name || "Builder Wood",
          size_label:    p.size_label || "",
          image_url:     p.image_url || (images[0] && images[0].image_url) || "",
          is_treated:    Boolean(p.is_treated),
          is_featured:   Boolean(p.is_featured)
        });
      });
    }).catch(err => {
      console.warn("Woods fetch failed:", err.message);
      state.products = [];
    }).then(() => {
      renderProducts();
    });
  }

  function renderProducts() {
    var grid  = $("#product-grid");
    var empty = $("#empty-state");

    var title = state.category ? (CATEGORY_LABEL[state.category] || state.category)
      : (state.search ? 'Results for "' + state.search + '"' : "Our timber");

    $("#catalog-title").textContent = title;
    $("#result-count").textContent = state.products.length + " item" + (state.products.length === 1 ? "" : "s");

    // remove the loading skeleton once products render
    var skeleton = document.getElementById("product-skeleton");
    if (skeleton) skeleton.remove();

    if (!state.products.length) {
      grid.innerHTML = ""; empty.hidden = false; return;
    }
    empty.hidden = true;

    var loggedInUserId = localStorage.getItem("bw_user_id");

    grid.innerHTML = state.products.map(function (p) {
      var stock = Number(p.stock_quantity || 0);
      var cat = p.category_name || p.trade_name || "Timber";
      var isMyProduct = (p.seller_id && loggedInUserId && String(p.seller_id) === String(loggedInUserId));

      var img = p.image_url
        ? '<img src="' + esc(p.image_url) + '" alt="' + esc(p.display_name) + '" loading="lazy">'
        : '<div class="image-fallback">\ud83e\udeb5</div>';

      var badges =
        (p.is_featured ? '<span class="product-badge">Popular</span>' : "") +
        (p.is_treated  ? '<span class="product-badge treated">Treated</span>' : "");

      var msgSellerBtn = !isMyProduct
        ? '<button class="add-btn" style="background:var(--green);" data-message-seller="' + esc(p.seller_id) + '" data-wood-id="' + esc(getId(p)) + '" title="Message seller">💬</button>'
        : '';

      var addButton;
      if (isMyProduct) {
        addButton = '<button class="add-btn" disabled style="background:#ccc; cursor:not-allowed;">Your Item</button>';
      } else if (stock <= 0) {
        addButton = '<button class="add-btn" disabled style="background:#ccc; cursor:not-allowed;">Out of stock</button>';
      } else {
        addButton = '<button class="add-btn" data-add="' + esc(getId(p)) + '">+ Add</button>';
      }

      return (
        '<article class="product-card" data-id="' + esc(getId(p)) + '">' +
          '<a href="/product/' + esc(getId(p)) + '" class="product-image" style="display:block;">' + img + badges + '</a>' +
          '<div class="product-body">' +
            '<span class="product-cat">' + esc(cat) + '</span>' +
            '<a href="/product/' + esc(getId(p)) + '" style="text-decoration:none; color:inherit;">' +
              '<h3 class="product-title">' + esc(p.display_name) + '</h3>' +
            '</a>' +
            '<span class="product-sub">Sold by: <b>' + esc(p.seller_name) + '</b></span>' +
            '<div class="product-chips">' +
              (p.size_label ? '<span class="chip-spec">' + esc(p.size_label) + '</span>' : "") +
              (p.is_treated ? '<span class="chip-spec">Treated</span>' : "") +
            '</div>' +
            '<span class="stock-line ' + (stock <= 0 ? "out" : "") + '">' +
              (stock <= 0 ? "Out of stock" : stock + " in stock") +
            '</span>' +
            '<div class="product-foot">' +
              '<div class="price">' +
                '<b>' + money(p.price_per_piece, p.currency) + '</b>' +
                '<small>per piece</small>' +
              '</div>' +
              '<div style="display:flex; gap:5px;">' +
                msgSellerBtn +
                addButton +
              '</div>' +
            '</div>' +
          '</div>' +
        '</article>'
      );
    }).join("");
    updateNavActive();
  }

  function updateNavActive() {
    $$(".catnav-link, .chip").forEach(function (el) {
      var isAll   = el.dataset.cat === "" && !state.category;
      var isMatch = el.dataset.cat === state.category;
      el.classList.toggle("active", isAll || isMatch);
    });
  }

  function setCategory(cat) {
    state.category = cat || "";
    fetchProducts();
    var target = document.getElementById("products");
    if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* ---- Cart ---- */
  async function loadCartFromDB() {
    var token = getToken();
    if (!token) { state.cart = []; renderCart(); return; }

    try {
      const res = await fetch("/cart/", {
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (res.ok) {
        const items = await res.json();
        state.cart = items;
        renderCart();
      }
    } catch (err) { console.warn("Cart fetch failed", err); }
  }

  async function addToCart(id) {
    var token = getToken();
    if (!token) { toast("Please log in to add items to your cart."); return; }

    try {
      const res = await fetch("/cart/add", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ wood_id: Number(id), quantity: 1 })
      });
      if (res.ok) { toast("Added to cart"); loadCartFromDB(); }
      else { const err = await res.json().catch(() => ({})); toast(err.detail || "Could not add item"); }
    } catch (err) { toast("Network error"); }
  }

  async function updateQuantity(id, newQty) {
    var token = getToken();
    if (!token) return;
    if (newQty < 1) return;

    try {
      const res = await fetch(`/cart/${id}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ quantity: newQty })
      });
      if (res.ok) { loadCartFromDB(); }
      else { const err = await res.json().catch(() => ({})); toast(err.detail || "Could not update quantity"); loadCartFromDB(); }
    } catch (err) { toast("Network error"); }
  }

  async function removeItem(cart_item_id) {
    var token = getToken();
    if (!token) return;
    try {
      await fetch(`/cart/${cart_item_id}`, {
        method: "DELETE",
        headers: { "Authorization": `Bearer ${token}` }
      });
      loadCartFromDB();
    } catch (err) { toast("Network error"); }
  }

  function renderCart() {
    var count = state.cart.reduce((n, i) => n + i.quantity, 0);
    var subtotal = state.cart.reduce((n, i) => n + (i.price_per_piece * i.quantity), 0);

    $("#cart-count").textContent = count;
    $("#drawer-count").textContent = `(${count})`;
    $("#cart-subtotal").textContent = money(subtotal);

    var floatPill = document.getElementById("floating-cart");
    var floatCount = document.getElementById("floating-cart-count");
    var floatTotal = document.getElementById("floating-cart-total");
    if (floatPill && floatCount && floatTotal) {
      if (count > 0) {
        floatCount.textContent = count;
        floatTotal.textContent = money(subtotal);
        floatPill.classList.add("visible");
      } else {
        floatPill.classList.remove("visible");
      }
    }

    var host = $("#cart-items");
    if (!state.cart.length) {
      host.innerHTML = '<div class="cart-empty"><span>\ud83e\udeb5</span><b>Your cart is empty</b><p>Add timber to start building your order.</p></div>';
      return;
    }

    let cartHtml = state.cart.map(item => {
      return (
        '<div class="cart-line">' +
          '<div class="cart-thumb">' + (item.image_url ? '<img src="' + esc(item.image_url) + '" alt="">' : "\ud83e\udeb5") + '</div>' +
          '<div class="cart-details">' +
            '<div class="cart-line-title">' + esc(item.local_name) + '</div>' +
            '<div class="cart-line-price">' + money(item.price_per_piece * item.quantity, item.currency) + '</div>' +
          '</div>' +
          '<div class="cart-actions">' +
            '<div class="qty">' +
              '<button data-cart-qty="' + item.cart_item_id + '" data-delta="-1">\u2212</button>' +
              '<input type="number" class="qty-input" data-cart-input="' + item.cart_item_id + '" value="' + item.quantity + '" min="1">' +
              '<button data-cart-qty="' + item.cart_item_id + '" data-delta="1">+</button>' +
            '</div>' +
            '<button class="btn-remove" data-cart-remove="' + item.cart_item_id + '">Remove</button>' +
          '</div>' +
        '</div>'
      );
    }).join("");

    // In-app message seller button (no phone, no WhatsApp)
    var firstItem = state.cart[0];
    if (firstItem && firstItem.seller_id) {
      cartHtml += `
        <div style="margin-top:20px; text-align:center; padding-top: 15px; border-top: 1px dashed var(--line);">
          <button type="button"
                  data-cart-message-seller="${esc(firstItem.seller_id)}"
                  data-cart-message-wood="${esc(firstItem.wood_id ?? "")}"
                  style="display:inline-flex; align-items:center; gap:8px; color:var(--green); font-weight:700; font-size:13px; padding: 8px 16px; border: 1px solid var(--green); border-radius: 8px; background:#fff; cursor:pointer;">
            💬 Message Seller Before Buying
          </button>
        </div>
      `;
    }

    host.innerHTML = cartHtml;
  }

  function openCart() {
    $("#cart-drawer").classList.add("open");
    $("#cart-drawer").setAttribute("aria-hidden", "false");
    $("#drawer-backdrop").hidden = false;
    document.body.style.overflow = "hidden";
    loadCartFromDB();
  }
  function closeCart() {
    $("#cart-drawer").classList.remove("open");
    $("#cart-drawer").setAttribute("aria-hidden", "true");
    $("#drawer-backdrop").hidden = true;
    document.body.style.overflow = "";
  }

  var toastTimer;
  function toast(msg) {
    var t = $("#toast");
    t.textContent = msg;
    t.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.classList.remove("show"); }, 2200);
  }

  function runSearch() {
    state.search = $("#search-input").value.trim();
    fetchProducts();
  }

  function showHints() {
    var term = $("#search-input").value.trim().toLowerCase();
    var host = $("#search-hints");
    if (term.length < 2) { host.classList.remove("show"); return; }

    var seen = {}; var unique = [];
    state.products.forEach(function (p) {
      var n = p.local_name;
      if (n && !seen[n]) { seen[n] = true; unique.push(n); }
    });

    var filtered = unique.filter(n => n.toLowerCase().indexOf(term) !== -1).slice(0, 5);
    host.innerHTML = filtered.map(s => '<button class="hint-item" data-hint="' + esc(s) + '"><b>' + esc(s) + '</b><span>Search</span></button>').join("");
    host.classList.toggle("show", filtered.length > 0);
  }

  async function startConversation(sellerId, woodId, btnEl) {
    var token = getToken();
    if (!token) {
      window.location.href = "/login";
      return;
    }
    if (!sellerId) { toast("Seller unavailable."); return; }

    var original = btnEl ? btnEl.innerHTML : null;
    if (btnEl) { btnEl.disabled = true; btnEl.innerHTML = "Opening…"; }

    try {
      const res = await fetch("/conversations/start", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${token}`,
        },
        body: JSON.stringify({
          seller_id: Number(sellerId),
          wood_id: woodId ? Number(woodId) : null,
        }),
      });

      if (res.status === 401) { window.location.href = "/login"; return; }

      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(
        typeof data.detail === "string" ? data.detail : `HTTP ${res.status}`
      );

      window.location.href = `/messages/${data.conversation_id}`;
    } catch (err) {
      if (btnEl) { btnEl.disabled = false; btnEl.innerHTML = original; }
      toast(err.message || "Could not open the conversation.");
    }
  }

  document.addEventListener("click", function (e) {
    // In-app messaging button (product cards)
    var msgBtn = e.target.closest("[data-message-seller]");
    if (msgBtn) {
      e.preventDefault();
      startConversation(msgBtn.dataset.messageSeller, msgBtn.dataset.woodId, msgBtn);
      return;
    }

    // In-app messaging button (cart drawer)
    var cartMsgBtn = e.target.closest("[data-cart-message-seller]");
    if (cartMsgBtn) {
      e.preventDefault();
      startConversation(
        cartMsgBtn.dataset.cartMessageSeller,
        cartMsgBtn.dataset.cartMessageWood || null,
        cartMsgBtn
      );
      return;
    }

    var cat = e.target.closest("[data-cat]");
    if (cat) { e.preventDefault(); setCategory(cat.dataset.cat); return; }

    var sp = e.target.closest("[data-search]");
    if (sp) { e.preventDefault(); $("#search-input").value = sp.dataset.search; runSearch(); return; }

    var add = e.target.closest("[data-add]");
    if (add) { e.preventDefault(); addToCart(add.dataset.add); return; }

    var qtyBtn = e.target.closest("[data-cart-qty]");
    if (qtyBtn) {
      e.preventDefault();
      var id = Number(qtyBtn.dataset.cartQty);
      var delta = Number(qtyBtn.dataset.delta);
      var item = state.cart.find(x => x.cart_item_id === id);
      if (item) {
        var newQty = item.quantity + delta;
        if (newQty >= 1) updateQuantity(id, newQty);
        else removeItem(id);
      }
      return;
    }

    var rm = e.target.closest("[data-cart-remove]");
    if (rm) { e.preventDefault(); removeItem(Number(rm.dataset.cartRemove)); return; }

    var hint = e.target.closest("[data-hint]");
    if (hint) { $("#search-input").value = hint.dataset.hint; runSearch(); $("#search-hints").classList.remove("show"); return; }

    if (!e.target.closest(".search")) $("#search-hints").classList.remove("show");
  });

  document.addEventListener("change", function(e) {
    if (e.target.matches("[data-cart-input]")) {
      var id = Number(e.target.dataset.cartInput);
      var newQty = parseInt(e.target.value);
      if (isNaN(newQty) || newQty < 1) { e.target.value = 1; newQty = 1; }
      updateQuantity(id, newQty);
    }
  });

  document.addEventListener("keydown", function(e) {
    if (e.key === "Enter" && e.target.matches("[data-cart-input]")) { e.target.blur(); }
  });

  $("#search-form").addEventListener("submit", function (e) { e.preventDefault(); runSearch(); });
  $("#search-input").addEventListener("input", showHints);
  $("#sort-select").addEventListener("change", function (e) { state.sort = e.target.value; fetchProducts(); });
  $("#clear-filters").addEventListener("click", function () { state.category = ""; state.search = ""; state.sort = "featured"; $("#search-input").value = ""; $("#sort-select").value = "featured"; fetchProducts(); });
  $("#empty-clear").addEventListener("click", function () { $("#clear-filters").click(); });

  var errorRetry = document.getElementById("error-retry");
  if (errorRetry) {
    errorRetry.addEventListener("click", function () {
      var errorState = document.getElementById("error-state");
      if (errorState) errorState.hidden = true;
      fetchProducts();
    });
  }

  $("#cart-open").addEventListener("click", function (e) { e.preventDefault(); openCart(); });
  $("#cart-close").addEventListener("click", closeCart);
  $("#drawer-backdrop").addEventListener("click", closeCart);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeCart(); });

  $("#checkout-button").addEventListener("click", function () {
    if (!state.cart.length) { $("#cart-message").textContent = "Your cart is empty."; return; }
    window.location.href = "/checkout";
  });

  var floatingCartBtn = document.getElementById("floating-cart");
  if (floatingCartBtn) {
    floatingCartBtn.addEventListener("click", function (e) {
      e.preventDefault();
      openCart();
    });
  }

  var yearEl = $("#year");
  if (yearEl) yearEl.textContent = new Date().getFullYear();

  function updateAuthUI() {
    var token = getToken();
    var role = localStorage.getItem("bw_role");
    var name = localStorage.getItem("bw_name");

    var loggedOut = document.getElementById("auth-logged-out");
    var loggedIn = document.getElementById("auth-logged-in");
    var greeting = document.getElementById("user-greeting");
    var dashboardLink = document.getElementById("dashboard-link");
    var buyerOrdersLink = document.getElementById("buyer-orders-link");
    var messagesLink = document.getElementById("messages-link");

    if (loggedOut && loggedIn) {
      if (token && name) {
        loggedOut.style.display = "none";
        loggedIn.style.display = "flex";
        greeting.textContent = "Hi, " + name.split(" ")[0];

        if (messagesLink) {
          messagesLink.style.display = "inline-flex";
          if (window.Messages) window.Messages.refreshUnreadBadge();
        }

        if (role === "seller" || role === "admin") {
          dashboardLink.style.display = "inline-block";
          dashboardLink.href = role === "admin" ? "/admin" : "/seller/orders";
          dashboardLink.textContent = role === "admin" ? "Admin Panel" : "Seller Dashboard";
          if (buyerOrdersLink) buyerOrdersLink.style.display = "none";
        } else {
          dashboardLink.style.display = "none";
          if (buyerOrdersLink) buyerOrdersLink.style.display = "inline-block";
        }
      } else {
        loggedOut.style.display = "flex";
        loggedIn.style.display = "none";
        if (messagesLink) messagesLink.style.display = "none";
      }
    }

    document.getElementById("logout-btn")?.addEventListener("click", function() {
      localStorage.removeItem("bw_token");
      localStorage.removeItem("bw_role");
      localStorage.removeItem("bw_name");
      localStorage.removeItem("bw_user_id");
      window.location.reload();
    });
  }

  async function refreshUserData() {
    const token = getToken();
    if (!token) return;
    try {
      const res = await fetch("/auth/me", {
        headers: { "Authorization": `Bearer ${token}` }
      });
      if (res.ok) {
        const user = await res.json();
        localStorage.setItem("bw_name", user.name || "");
        localStorage.setItem("bw_role", user.role || "");
        localStorage.setItem("bw_user_id", user.user_id != null ? String(user.user_id) : "");
        updateAuthUI();
      } else if (res.status === 401) {
        localStorage.removeItem("bw_token");
        localStorage.removeItem("bw_role");
        localStorage.removeItem("bw_name");
        localStorage.removeItem("bw_user_id");
        updateAuthUI();
      }
    } catch (err) {
      console.warn("Could not refresh user data:", err);
    }
  }

  updateAuthUI();
  refreshUserData();
  loadCartFromDB();
  fetchProducts();

  // Expose for other scripts (e.g. messages.js) that want to reuse
  window.BW = window.BW || {};
  window.BW.startConversation = startConversation;
})();