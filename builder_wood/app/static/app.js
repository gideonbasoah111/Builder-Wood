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

  /* ============================================================
     FETCH & RENDER PRODUCTS
     ============================================================ */
  function fetchProducts() {
    var params = new URLSearchParams();
    if (state.category) params.set("category", state.category);
    if (state.search)   params.set("search", state.search);
    if (state.sort !== "featured") params.set("sort", state.sort);

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
    var skeleton = document.getElementById("product-skeleton");

    if (skeleton) skeleton.style.display = "none";

    if ($("#result-count")) $("#result-count").textContent = state.products.length + " items";

    if (!state.products.length) {
      if (grid) grid.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    var loggedInUserId = localStorage.getItem("bw_user_id");

    if (!grid) return;

    grid.innerHTML = state.products.map(function (p) {
      var stock = Number(p.stock_quantity || 0);
      var isMyProduct = (p.seller_id && loggedInUserId && String(p.seller_id) === String(loggedInUserId));

      var img = p.image_url
        ? '<img src="' + esc(p.image_url) + '" alt="' + esc(p.display_name) + '" loading="lazy">'
        : '<div style="font-size: 40px; opacity: 0.2;">🪵</div>';

      var badges =
        (p.is_featured ? '<span class="product-badge">Popular</span>' : "") +
        (p.is_treated  ? '<span class="product-badge treated">Treated</span>' : "");

      // In-app message button (Fix: Added type="button" and data-message-seller)
      var msgSellerBtn = !isMyProduct
        ? '<button type="button" class="btn-add-icon" style="background:var(--green); width:auto; padding:0 10px;" data-message-seller="' + esc(p.seller_id) + '" data-wood-id="' + esc(getId(p)) + '" title="Message seller">💬</button>'
        : '';

      var addButton;
      if (isMyProduct) {
        addButton = '<button type="button" class="btn-add-text" disabled>Your Item</button>';
      } else if (stock <= 0) {
        addButton = '<button type="button" class="btn-add-text" disabled>Out of Stock</button>';
      } else {
        addButton = `
          <button type="button" class="btn-add-icon" data-add="${esc(getId(p))}" title="Add to cart">+</button>
          <button type="button" class="btn-add-text" data-add="${esc(getId(p))}">Add</button>
        `;
      }

      return (
        '<article class="product-card">' +
          '<a href="/product/' + esc(getId(p)) + '" class="product-image">' + img + badges + '</a>' +
          '<div class="product-body">' +
            '<a href="/product/' + esc(getId(p)) + '" class="product-title">' + esc(p.display_name) + '</a>' +
            '<div class="product-price">' + money(p.price_per_piece, p.currency) + ' <small>/ pc</small></div>' +
            '<div class="product-sub">Sold by: <b>' + esc(p.seller_name) + '</b></div>' +
            '<div class="product-actions">' + msgSellerBtn + addButton + '</div>' +
          '</div>' +
        '</article>'
      );
    }).join("");

    updateNavActive();
  }

  function updateNavActive() {
    $$(".cat-pill").forEach(function (el) {
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

  /* ============================================================
     CART LOGIC
     ============================================================ */
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
    if (!token) {
      toast("Please log in to add items.");
      setTimeout(() => window.location.href = "/login", 1000);
      return;
    }

    try {
      const res = await fetch("/cart/add", {
        method: "POST",
        headers: { "Content-Type": "application/json", "Authorization": `Bearer ${token}` },
        body: JSON.stringify({ wood_id: Number(id), quantity: 1 })
      });
      if (res.ok) {
        toast("Added to cart");
        loadCartFromDB();
      } else {
        const err = await res.json().catch(() => ({}));
        toast(err.detail || "Could not add item");
      }
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

    if ($("#cart-count")) $("#cart-count").textContent = count;
    if ($("#drawer-count")) $("#drawer-count").textContent = `(${count})`;
    if ($("#cart-subtotal")) $("#cart-subtotal").textContent = money(subtotal);

    // Floating cart pill
    var floatPill = document.getElementById("floating-cart");
    if (floatPill) {
      if (count > 0) {
        document.getElementById("floating-cart-count").textContent = count;
        document.getElementById("floating-cart-total").textContent = money(subtotal);
        floatPill.style.display = "flex";
      } else {
        floatPill.style.display = "none";
      }
    }

    var host = $("#cart-items");
    if (!host) return;

    if (!state.cart.length) {
      host.innerHTML = '<div class="cart-empty"><span>🪵</span><b>Your cart is empty</b><p>Add timber to start building your order.</p></div>';
      return;
    }

    let cartHtml = state.cart.map(item => {
      return (
        '<div class="cart-line">' +
          '<div class="cart-thumb">' + (item.image_url ? '<img src="' + esc(item.image_url) + '" alt="">' : "🪵") + '</div>' +
          '<div class="cart-details">' +
            '<div class="cart-line-title">' + esc(item.local_name) + '</div>' +
            '<div class="cart-line-price">' + money(item.price_per_piece * item.quantity, item.currency) + '</div>' +
          '</div>' +
          '<div class="cart-actions">' +
            '<div class="qty">' +
              '<button type="button" data-cart-qty="' + item.cart_item_id + '" data-delta="-1">−</button>' +
              '<input type="number" class="qty-input" data-cart-input="' + item.cart_item_id + '" value="' + item.quantity + '" min="1">' +
              '<button type="button" data-cart-qty="' + item.cart_item_id + '" data-delta="1">+</button>' +
            '</div>' +
            '<button type="button" class="btn-remove" data-cart-remove="' + item.cart_item_id + '">Remove</button>' +
          '</div>' +
        '</div>'
      );
    }).join("");

    // In-app message seller button (cart drawer)
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
    var cartDrawer = document.getElementById("cart-drawer");
    var backdrop = document.getElementById("drawer-backdrop");

    if (cartDrawer) cartDrawer.classList.add("open");
    if (backdrop) backdrop.hidden = false;

    document.body.style.overflow = "hidden";
    loadCartFromDB();
  }

  function closeCart() {
    var cartDrawer = document.getElementById("cart-drawer");
    var backdrop = document.getElementById("drawer-backdrop");

    if (cartDrawer) cartDrawer.classList.remove("open");
    if (backdrop) backdrop.hidden = true;

    document.body.style.overflow = "";
  }

  var toastTimer;
  function toast(msg) {
    var t = $("#toast");
    if (!t) return;
    t.textContent = msg;
    t.style.opacity = "1";
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { t.style.opacity = "0"; }, 2500);
  }

  function runSearch() {
    state.search = $("#search-input").value.trim();
    fetchProducts();
  }

  /* ============================================================
     IN-APP MESSAGING
     ============================================================ */
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

  /* ============================================================
     EVENT LISTENERS
     ============================================================ */
  document.addEventListener("click", function (e) {
    // 1. In-app messaging button (product cards) - MUST BE FIRST
    var msgBtn = e.target.closest("[data-message-seller]");
    if (msgBtn) {
      e.preventDefault();
      e.stopPropagation();
      startConversation(msgBtn.dataset.messageSeller, msgBtn.dataset.woodId, msgBtn);
      return;
    }

    // 2. In-app messaging button (cart drawer)
    var cartMsgBtn = e.target.closest("[data-cart-message-seller]");
    if (cartMsgBtn) {
      e.preventDefault();
      e.stopPropagation();
      startConversation(
        cartMsgBtn.dataset.cartMessageSeller,
        cartMsgBtn.dataset.cartMessageWood || null,
        cartMsgBtn
      );
      return;
    }

    // 3. Category Filter
    var cat = e.target.closest("[data-cat]");
    if (cat) { e.preventDefault(); setCategory(cat.dataset.cat); return; }

    // 4. Search Suggestion Click
    var sp = e.target.closest("[data-search]");
    if (sp) { e.preventDefault(); $("#search-input").value = sp.dataset.search; runSearch(); return; }

    // 5. Add to Cart
    var add = e.target.closest("[data-add]");
    if (add) {
      e.preventDefault();
      e.stopPropagation();
      addToCart(add.dataset.add);
      return;
    }

    // 6. Cart Quantity +/-
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

    // 7. Cart Remove
    var rm = e.target.closest("[data-cart-remove]");
    if (rm) { e.preventDefault(); removeItem(Number(rm.dataset.cartRemove)); return; }

    // 8. Clear Filters (Empty State)
    var clearBtn = e.target.closest("#empty-clear");
    if (clearBtn) {
      e.preventDefault();
      state.category = "";
      state.search = "";
      state.sort = "featured";
      if ($("#search-input")) $("#search-input").value = "";
      fetchProducts();
      return;
    }

    // 9. Cart Open/Close
    if (e.target.closest("#cart-open")) {
      e.preventDefault();
      openCart();
      return;
    }

    if (e.target.closest("#floating-cart")) {
      e.preventDefault();
      openCart();
      return;
    }

    if (e.target.closest("#cart-close")) {
      e.preventDefault();
      closeCart();
      return;
    }

    if (e.target.closest("#drawer-backdrop")) {
      e.preventDefault();
      closeCart();
      return;
    }

    // 10. Checkout
    if (e.target.closest("#checkout-button")) {
      if (!state.cart.length) {
        if ($("#cart-message")) $("#cart-message").textContent = "Your cart is empty.";
        return;
      }
      window.location.href = "/checkout";
      return;
    }
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
    if (e.key === "Escape") closeCart();
  });

  if ($("#search-form")) {
    $("#search-form").addEventListener("submit", function (e) { e.preventDefault(); runSearch(); });
  }

  // Initialize
  loadCartFromDB();
  fetchProducts();

  window.BW = window.BW || {};
  window.BW.startConversation = startConversation;
})();