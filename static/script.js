document.addEventListener("DOMContentLoaded", () => {
  // Count-up animation for stat card numbers (Dashboard).
  document.querySelectorAll(".count-up").forEach((el) => {
    const target = parseFloat(el.dataset.target);
    if (Number.isNaN(target)) return;
    const isFloat = el.dataset.float === "true";
    let current = 0;
    const step = target / 50;
    const timer = setInterval(() => {
      current += step;
      if (current >= target) {
        current = target;
        clearInterval(timer);
      }
      el.textContent = isFloat ? "₹ " + current.toFixed(2) : Math.floor(current);
    }, 30);
  });

  // Live elapsed timers (Kitchen tickets + Orders cards).
  const elapsedEls = Array.from(document.querySelectorAll(".elapsed-live[data-created-at]"));
  const elapsedKotEls = Array.from(document.querySelectorAll(".kot-elapsed[data-created-at]"));
  const allElapsedEls = elapsedEls.concat(elapsedKotEls);

  const formatElapsed = (seconds) => {
    seconds = Math.max(0, seconds);
    const mins = Math.floor(seconds / 60);
    const hrs = Math.floor(mins / 60);
    if (hrs > 0) return `${hrs}h ${mins % 60}m`;
    return `${mins}m ${seconds % 60}s`;
  };

  const nowTick = () => {
    const now = Date.now();
    allElapsedEls.forEach((el) => {
      const created = new Date(el.dataset.createdAt);
      const t = created.getTime();
      if (!t || Number.isNaN(t)) return;
      const seconds = Math.floor((now - t) / 1000);
      el.textContent = formatElapsed(seconds);
    });
  };
  if (allElapsedEls.length) {
    nowTick();
    setInterval(nowTick, 1000);
  }

  // Kitchen: live clock + countdown + auto refresh every 15 seconds.
  const clockEl = document.getElementById("kitchen-clock");
  if (clockEl) {
    const updateClock = () => {
      clockEl.textContent = new Date().toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    };
    updateClock();
    setInterval(updateClock, 1000);
  }

  const countdownEl = document.getElementById("kitchen-countdown");
  if (countdownEl) {
    const refreshMs = 15000;
    let nextRefreshAt = Date.now() + refreshMs;
    const tick = () => {
      const remaining = Math.max(0, nextRefreshAt - Date.now());
      countdownEl.textContent = Math.ceil(remaining / 1000);
      if (remaining <= 0) {
        window.location.reload();
      }
    };
    tick();
    setInterval(tick, 250);
  }

  // Menu: category filter tabs (frontend-only).
  const menuTabs = document.querySelectorAll("[data-menu-category-tab]");
  const menuCards = document.querySelectorAll("[data-menu-category-id]");
  if (menuTabs.length && menuCards.length) {
    const setVisible = (catId) => {
      menuCards.forEach((card) => {
        const cardCat = card.dataset.menuCategoryId;
        const show = !catId || catId === "all" || cardCat === catId;
        card.style.display = show ? "" : "none";
      });
    };

    menuTabs.forEach((tab) => {
      tab.addEventListener("click", () => {
        menuTabs.forEach((t) => t.classList.remove("active"));
        tab.classList.add("active");
        setVisible(tab.dataset.menuCategoryTab);
      });
    });
  }

  // Order/New: keep a live cart subtotal (excl. GST).
  const formEl = document.getElementById("order-new-form");
  const totalEl = document.getElementById("cart-total");
  if (formEl && totalEl) {
    const calcSubtotal = () => {
      let total = 0;
      const inputs = formEl.querySelectorAll(".qty-input");
      inputs.forEach((input) => {
        const qty = parseInt(input.value || "0", 10);
        const unit = parseFloat(input.dataset.unitPrice || "0");
        if (!Number.isNaN(qty) && !Number.isNaN(unit)) total += qty * unit;
      });
      totalEl.textContent = total.toFixed(2);
    };

    formEl.querySelectorAll(".qty-input").forEach((inp) => {
      inp.addEventListener("input", calcSubtotal);
      inp.addEventListener("change", calcSubtotal);
    });

    calcSubtotal();
  }

  // Dashboard: copy customer URL button.
  const shareBtn = document.getElementById("share-customer-view");
  if (shareBtn) {
    shareBtn.addEventListener("click", async () => {
      const customerUrl = shareBtn.dataset.customerUrl || (window.location.origin + "/customer");
      try {
        await navigator.clipboard.writeText(customerUrl);
        shareBtn.innerHTML = '<i class="bi bi-check2-circle me-2"></i>Copied!';
        setTimeout(() => {
          shareBtn.innerHTML = '<i class="bi bi-link-45deg me-2"></i>Share with Customers';
        }, 1600);
      } catch (_e) {
        window.prompt("Copy customer URL:", customerUrl);
      }
    });
  }

  // Countdown timers for table booking/availability cards.
  document.querySelectorAll("[id^='countdown-'][data-until]").forEach((el) => {
    startCountdown(el.id, el.dataset.until);
  });

  // Customer page live table availability AJAX updates.
  if (window.location.pathname.startsWith("/customer")) {
    let lastUpdated = 0;
    setInterval(async () => {
      try {
        const res = await fetch("/api/tables/availability");
        const tables = await res.json();
        tables.forEach((t) => {
          const card = document.getElementById("table-card-" + t.id);
          if (!card) return;
          card.className = card.className.replace(/status-\w+/g, "").trim() + " status-" + t.status;
          const badge = card.querySelector(".status-badge");
          if (badge) {
            if (t.status === "free") badge.textContent = "🟢 Available";
            else if (t.status === "booked") badge.textContent = "🟡 Pre-Booked";
            else badge.textContent = "🔴 Occupied";
          }
        });
        lastUpdated = 0;
      } catch (_e) {
        // keep UI stable on temporary network hiccups
      }
    }, 30000);

    setInterval(() => {
      const el = document.getElementById("last-updated");
      if (el) el.textContent = String(++lastUpdated);
    }, 1000);
  }
});

function startCountdown(elementId, targetTimeISO) {
  const el = document.getElementById(elementId);
  if (!el || !targetTimeISO) return;
  const timer = setInterval(() => {
    const now = new Date();
    const target = new Date(targetTimeISO);
    const diff = target - now;
    if (diff <= 0) {
      el.textContent = "Table is now free!";
      el.style.color = "#10b981";
      clearInterval(timer);
      return;
    }
    const h = Math.floor(diff / 3600000);
    const m = Math.floor((diff % 3600000) / 60000);
    const s = Math.floor((diff % 60000) / 1000);
    el.textContent = String(h).padStart(2, "0") + ":" + String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }, 1000);
}

