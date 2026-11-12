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
      grid.innerHTML = "";
      if (empty) empty.hidden = false;
      return;
    }
    if (empty) empty.hidden = true;

    var loggedInUserId = localStorage.getItem("bw_user_id");

    grid.innerHTML = state.products.map(function (p) {
      var stock = Number(p.stock_quantity || 0);
      var isMyProduct = (p.seller_id && loggedInUserId && String(p.seller_id) === String(loggedInUserId));

      var img = p.image_url
        ? '<img src="' + esc(p.image_url) + '" alt="' + esc(p.display_name) + '" loading="lazy">'
        : '<div style="font-size: 40px; opacity: 0.2;">🪵</div>';

      var badges =
        (p.is_featured ? '<span class="product-badge">Popular</span>' : "") +
        (p.is_treated  ? '<span class="product-badge treated">Treated</span>' : "");

      var actionButton = "";
      if (isMyProduct) {
        actionButton = '<button class="btn-add-text" disabled>Your Item</button>';
      } else if (stock <= 0) {
        actionButton = '<button class="btn-add-text" disabled>Out of Stock</button>';
      } else {
        actionButton = `
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
            '<div class="product-actions">' + actionButton + '</div>' +
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

  function renderCart() {
    var count = state.cart.reduce((n, i) => n + i.quantity, 0);
    var subtotal = state.cart.reduce((n, i) => n + (i.price_per_piece * i.quantity), 0);

    $("#cart-count").textContent = count;
    $("#cart-subtotal").textContent = money(subtotal);

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
    if (!state.cart.length) {
      host.innerHTML = '<div style="text-align: center; padding: 40px 0; color: #888;"><span style="font-size: 40px; display:block; margin-bottom: 10px;">🪵</span>Your cart is empty</div>';
      return;
    }

    host.innerHTML = state.cart.map(item => {
      return (
        '<div style="display: flex; gap: 12px; margin-bottom: 15px; border-bottom: 1px solid #eee; padding-bottom: 15px;">' +
          '<div style="width: 60px; height: 60px; border-radius: 8px; background: #f5f5f5; overflow: hidden; flex-shrink: 0;">' +
             (item.image_url ? '<img src="' + esc(item.image_url) + '" style="width:100%; height:100%; object-fit:cover;">' : "🪵") +
          '</div>' +
          '<div style="flex: 1;">' +
            '<div style="font-weight: 700; font-size: 13px; margin-bottom: 4px;">' + esc(item.local_name) + '</div>' +
            '<div style="font-size: 12px; color: #888;">Qty: ' + item.quantity + '</div>' +
            '<div style="font-weight: 800; font-size: 14px; margin-top: 4px;">' + money(item.price_per_piece * item.quantity, item.currency) + '</div>' +
          '</div>' +
          '<button type="button" data-cart-remove="' + item.cart_item_id + '" style="background: none; border: none; cursor: pointer; color: #dc3545; font-size: 16px;">✕</button>' +
        '</div>'
      );
    }).join("");
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

  function openCart() {
    $("#cart-drawer").style.right = "0";
    $("#drawer-backdrop").hidden = false;
    document.body.style.overflow = "hidden";
    loadCartFromDB();
  }

  function closeCart() {
    $("#cart-drawer").style.right = "-400px";
    $("#drawer-backdrop").hidden = true;
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

  /* ============================================================
     EVENT LISTENERS
     ============================================================ */
  document.addEventListener("click", function (e) {
    var cat = e.target.closest("[data-cat]");
    if (cat) { e.preventDefault(); setCategory(cat.dataset.cat); return; }

    var add = e.target.closest("[data-add]");
    if (add) {
      e.preventDefault();
      e.stopPropagation(); // Stop the link from firing
      addToCart(add.dataset.add);
      return;
    }

    var rm = e.target.closest("[data-cart-remove]");
    if (rm) { e.preventDefault(); removeItem(Number(rm.dataset.cartRemove)); return; }

    var clearBtn = e.target.closest("#empty-clear");
    if (clearBtn) {
      e.preventDefault();
      state.category = "";
      state.search = "";
      state.sort = "featured";
      if ($("#search-input")) $("#search-input").value = "";
      fetchProducts();
    }
  });

  if ($("#search-form")) {
    $("#search-form").addEventListener("submit", function (e) {
      e.preventDefault();
      state.search = $("#search-input").value.trim();
      fetchProducts();
    });
  }

  if ($("#sort-select")) {
    $("#sort-select").addEventListener("change", function (e) {
      state.sort = e.target.value;
      fetchProducts();
    });
  }

  if ($("#cart-open")) $("#cart-open").addEventListener("click", function (e) { e.preventDefault(); openCart(); });
  if ($("#floating-cart")) $("#floating-cart").addEventListener("click", function (e) { e.preventDefault(); openCart(); });
  if ($("#cart-close")) $("#cart-close").addEventListener("click", closeCart);
  if ($("#drawer-backdrop")) $("#drawer-backdrop").addEventListener("click", closeCart);

  if ($("#checkout-button")) {
    $("#checkout-button").addEventListener("click", function () {
      if (!state.cart.length) return;
      window.location.href = "/checkout";
    });
  }

  // Initialize
  loadCartFromDB();
  fetchProducts();
})();