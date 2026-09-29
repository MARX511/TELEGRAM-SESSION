/* Dashboard interactions. No dependencies. Everything degrades gracefully without JS
 * (theme/language have server-side fallbacks, forms are plain POSTs). Honours prefers-reduced-motion. */
(() => {
  "use strict";
  const root = document.documentElement;
  const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const finePointer = window.matchMedia("(hover: hover) and (pointer: fine)").matches;
  const YEAR = 60 * 60 * 24 * 365;

  /* ---------- theme toggle (instant; cookie keeps the server render in sync, no flash on reload) ---------- */
  function setTheme(next) {
    root.setAttribute("data-theme", next);
    document.cookie = `theme=${next}; path=/; max-age=${YEAR}; samesite=lax`;
    window.dispatchEvent(new CustomEvent("themechange", { detail: { theme: next } }));
  }
  document.addEventListener("click", (e) => {
    const btn = e.target.closest("[data-theme-toggle]");
    if (!btn) return;
    setTheme(root.getAttribute("data-theme") === "light" ? "dark" : "light");
  });

  /* ---------- mobile drawer ---------- */
  document.addEventListener("click", (e) => {
    if (e.target.closest("[data-drawer-toggle]")) root.classList.toggle("drawer-open");
    else if (e.target.closest("[data-drawer-close]")) root.classList.remove("drawer-open");
  });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") root.classList.remove("drawer-open"); });

  /* ---------- flash: dismiss + auto-hide ---------- */
  function hideFlash(el) { el.classList.add("hide"); setTimeout(() => el.remove(), 420); }
  document.addEventListener("click", (e) => {
    const close = e.target.closest("[data-flash-close]");
    if (close) hideFlash(close.closest("[data-flash]"));
  });
  document.querySelectorAll("[data-flash]").forEach((el) => setTimeout(() => el.isConnected && hideFlash(el), 7000));

  /* ---------- password visibility ---------- */
  document.addEventListener("click", (e) => {
    const t = e.target.closest("[data-pw-toggle]");
    if (!t) return;
    const input = document.getElementById(t.getAttribute("data-pw-toggle"));
    if (input) input.type = input.type === "password" ? "text" : "password";
  });

  /* ---------- 3D tilt + glare ---------- */
  function bindTilt(el) {
    if (el.dataset.tiltBound) return;
    el.dataset.tiltBound = "1";
    const max = 7;
    let frame = 0;
    el.addEventListener("pointermove", (e) => {
      const r = el.getBoundingClientRect();
      const px = (e.clientX - r.left) / r.width;
      const py = (e.clientY - r.top) / r.height;
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(() => {
        el.classList.add("tilting");
        el.style.setProperty("--ry", `${(px - 0.5) * max * 2}deg`);
        el.style.setProperty("--rx", `${(0.5 - py) * max * 2}deg`);
        el.style.setProperty("--gx", `${px * 100}%`);
        el.style.setProperty("--gy", `${py * 100}%`);
      });
    });
    el.addEventListener("pointerleave", () => {
      cancelAnimationFrame(frame);
      el.classList.remove("tilting");
      el.style.setProperty("--rx", "0deg");
      el.style.setProperty("--ry", "0deg");
    });
  }

  /* ---------- count-up for numeric stat values ---------- */
  const fmt = new Intl.NumberFormat("en-US");
  function countUp(el) {
    const target = Number(el.getAttribute("data-count"));
    if (!Number.isFinite(target) || target <= 0 || el.dataset.counted) return;
    el.dataset.counted = "1";
    const decimals = String(el.getAttribute("data-count")).includes(".") ? 1 : 0;
    const dur = 900;
    const t0 = performance.now();
    const step = (now) => {
      const p = Math.min(1, (now - t0) / dur);
      const eased = 1 - Math.pow(1 - p, 3);
      el.textContent = decimals ? (target * eased).toFixed(decimals) : fmt.format(Math.round(target * eased));
      if (p < 1) requestAnimationFrame(step);
    };
    requestAnimationFrame(step);
  }

  /* ---------- tooltips for [data-tip] (charts, icon buttons) ---------- */
  const tip = document.getElementById("tooltip");
  function showTip(el, x, y) {
    if (!tip) return;
    tip.textContent = el.getAttribute("data-tip");
    tip.classList.add("show");
    const pad = 12;
    const w = tip.offsetWidth, h = tip.offsetHeight;
    let left = x + pad, top = y - h - pad;
    if (left + w > window.innerWidth - 8) left = x - w - pad;
    if (top < 8) top = y + pad;
    tip.style.left = `${Math.max(8, left)}px`;
    tip.style.top = `${top}px`;
  }
  function hideTip() { tip && tip.classList.remove("show"); }
  document.addEventListener("pointerover", (e) => {
    const el = e.target.closest("[data-tip]");
    if (el) showTip(el, e.clientX, e.clientY);
  });
  document.addEventListener("pointermove", (e) => {
    const el = e.target.closest("[data-tip]");
    if (el) showTip(el, e.clientX, e.clientY); else hideTip();
  });
  document.addEventListener("pointerout", (e) => { if (!e.relatedTarget || !e.relatedTarget.closest("[data-tip]")) hideTip(); });
  document.addEventListener("focusin", (e) => {
    const el = e.target.closest("[data-tip]");
    if (!el) return;
    const r = el.getBoundingClientRect();
    showTip(el, r.left + r.width / 2, r.top);
  });
  document.addEventListener("focusout", hideTip);
  window.addEventListener("scroll", hideTip, { passive: true });

  /* ---------- session upload: drag highlight, chosen-file chips, busy state ---------- */
  function sizeLabel(bytes) {
    if (bytes >= 1048576) return `${(bytes / 1048576).toFixed(1)} MB`;
    return `${Math.max(1, Math.round(bytes / 1024))} KB`;
  }
  function showChosen(form) {
    const input = form.querySelector('input[type="file"]');
    const list = form.querySelector("[data-upload-list]");
    if (!input || !list) return;
    list.textContent = "";
    const files = Array.from(input.files || []);
    files.slice(0, 6).forEach((f) => {
      const chip = document.createElement("span");
      chip.className = "file-chip";
      chip.textContent = `${f.name} · ${sizeLabel(f.size)}`;
      list.appendChild(chip);
    });
    if (files.length > 6) {
      const more = document.createElement("span");
      more.className = "file-chip";
      more.textContent = (form.getAttribute("data-more") || "+{n}").replace("{n}", String(files.length - 6));
      list.appendChild(more);
    }
  }
  document.addEventListener("change", (e) => {
    const form = e.target.closest("[data-upload]");
    if (form && e.target.type === "file") showChosen(form);
  });
  ["dragenter", "dragover"].forEach((type) => document.addEventListener(type, (e) => {
    const zone = e.target.closest && e.target.closest("[data-dropzone]");
    if (zone) zone.classList.add("dragging");
  }));
  ["dragleave", "drop"].forEach((type) => document.addEventListener(type, (e) => {
    const zone = e.target.closest && e.target.closest("[data-dropzone]");
    if (zone && (type === "drop" || !zone.contains(e.relatedTarget))) zone.classList.remove("dragging");
  }));
  document.addEventListener("submit", (e) => {
    const form = e.target.closest("[data-upload]");
    if (!form) return;
    const btn = form.querySelector("[data-busy-text]");
    if (!btn) return;
    btn.setAttribute("aria-busy", "true");
    const label = btn.querySelector("span");
    if (label) label.textContent = btn.getAttribute("data-busy-text");
  });

  /* ---------- init (also after htmx swaps) ---------- */
  function init(scope) {
    if (!reduceMotion && finePointer) scope.querySelectorAll("[data-tilt]").forEach(bindTilt);
    const counters = scope.querySelectorAll("[data-count]");
    if (reduceMotion || !("IntersectionObserver" in window)) return;
    const io = new IntersectionObserver((entries) => {
      entries.forEach((en) => { if (en.isIntersecting) { countUp(en.target); io.unobserve(en.target); } });
    }, { threshold: 0.4 });
    counters.forEach((c) => io.observe(c));
  }
  document.addEventListener("DOMContentLoaded", () => init(document));
  document.body && document.body.addEventListener("htmx:afterSwap", (e) => init(e.target));
})();
