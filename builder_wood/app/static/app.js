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
      state.products = rows.map(p => ({
        ...p,
        display_name: p.display_name || p.local_name || "Timber",
        local_name: p.local_name || "",
        category_slug: String(p.category_slug || "").toLowerCase(),
        category_name: p.category_name || "",
        seller_id: p.seller_id || null,
        seller_name: p.seller_name || "Builder Wood",
        seller_phone: p.seller_phone || "",
        size_label: p.size_label || "",
        primary_image_url: p.primary_image_url || "",
        is_treated: Boolean(p.is_treated),
        is_featured: Boolean(p.is_featured)
      }));
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

    if (!state.products.length) {
      grid.innerHTML = ""; empty.hidden = false; return;
    }
    empty.hidden = true;

    var loggedInUserId = localStorage.getItem("bw_user_id");

    grid.innerHTML = state.products.map(function (p) {
      var stock = Number(p.stock_quantity || 0);
      var cat = p.category_name || p.trade_name || "Timber";
      var isMyProduct = (p.seller_id && loggedInUserId && String(p.seller_id) === String(loggedInUserId));

      var img = p.primary_image_url
        ? '<img src="' + esc(p.primary_image_url) + '" alt="' + esc(p.display_name) + '" loading="lazy">'
        : '<div class="image-fallback">\ud83e\udeb5</div>';
      var badges =
        (p.is_featured ? '<span class="product-badge">Popular</span>' : "") +
        (p.is_treated  ? '<span class="product-badge treated">Treated</span>' : "");

      var phone = p.seller_phone ? p.seller_phone.replace(/\D/g, '') : '';
      var waLink = phone
        ? `https://wa.me/233${phone.slice(-9)}?text=Hello ${encodeURIComponent(p.seller_name)}, I am interested in buying ${encodeURIComponent(p.display_name)}`
        : '#';

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
                '<a href="' + waLink + '" target="_blank" class="add-btn" style="background:var(--green); text-decoration:none; display:flex; align-items:center; justify-content:center;" title="Contact Seller">💬</a>' +
                (isMyProduct
                  ? '<button class="add-btn" disabled style="background:#ccc; cursor:not-allowed;">Your Item</button>'
                  : '<button class="add-btn" data-add="' + esc(getId(p)) + '"' + (stock <= 0 ? " disabled" : "") + '>+ Add</button>'
                ) +
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
      else { const err = await res.json(); toast(err.detail || "Could not add item"); }
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
      else { const err = await res.json(); toast(err.detail || "Could not update quantity"); loadCartFromDB(); }
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

    // Add "Message Seller" button to the cart
    var firstItem = state.cart[0];
    if (firstItem && firstItem.seller_phone) {
        var phone = firstItem.seller_phone.replace(/\D/g, '');
        var waLink = `https://wa.me/233${phone.slice(-9)}?text=Hello ${encodeURIComponent(firstItem.seller_name)}, I have a question about my cart.`;

        cartHtml += `
          <div style="margin-top:20px; text-align:center; padding-top: 15px; border-top: 1px dashed var(--line);">
            <a href="${waLink}" target="_blank" style="display:inline-flex; align-items:center; gap:8px; text-decoration:none; color:var(--green); font-weight:700; font-size:13px; padding: 8px 16px; border: 1px solid var(--green); border-radius: 8px;">
                💬 Message Seller Before Buying
            </a>
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

  document.addEventListener("click", function (e) {
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

  $("#cart-open").addEventListener("click", function (e) { e.preventDefault(); openCart(); });
  $("#cart-close").addEventListener("click", closeCart);
  $("#drawer-backdrop").addEventListener("click", closeCart);
  document.addEventListener("keydown", function (e) { if (e.key === "Escape") closeCart(); });

  $("#checkout-button").addEventListener("click", function () {
    if (!state.cart.length) { $("#cart-message").textContent = "Your cart is empty."; return; }
    window.location.href = "/checkout";
  });

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

    if (loggedOut && loggedIn) {
      if (token && name) {
        loggedOut.style.display = "none";
        loggedIn.style.display = "flex";
        greeting.textContent = "Hi, " + name.split(" ")[0];

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

  updateAuthUI();
  loadCartFromDB();
  fetchProducts();
})();