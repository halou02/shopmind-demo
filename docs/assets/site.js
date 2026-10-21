/* ==========================================================================
   ShopMind — site.js
   Shared behaviours: header, menu, reveal, counters, demo tour, pipeline rail.
   No dependencies. All effects degrade gracefully.
   ========================================================================== */
(function () {
  "use strict";

  var reduce = false;
  try {
    reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (err) {}

  function qs(sel, root) { return (root || document).querySelector(sel); }
  function qsa(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  /* ---------------- Header state ---------------- */
  var header = qs("[data-header]");
  if (header) {
    var onScroll = function () {
      header.classList.toggle("is-scrolled", window.scrollY > 16);
    };
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
  }

  /* ---------------- Menu overlay ---------------- */
  var menu = document.getElementById("site-menu");
  var menuToggle = qs("[data-menu-toggle]");
  var menuClose = qs("[data-menu-close]");
  var menuTimer = null;

  function focusableInMenu() {
    if (!menu) return [];
    return qsa("a, button", menu).filter(function (el) {
      return !el.hasAttribute("disabled") && el.offsetParent !== null;
    });
  }

  function openMenu() {
    if (!menu || !menuToggle) return;
    clearTimeout(menuTimer);
    menu.hidden = false;
    document.body.classList.add("menu-open");
    menuToggle.setAttribute("aria-expanded", "true");
    window.requestAnimationFrame(function () {
      menu.classList.add("is-open");
      var items = focusableInMenu();
      if (items.length) items[0].focus();
    });
  }

  function closeMenu(restoreFocus) {
    if (!menu || !menuToggle) return;
    menu.classList.remove("is-open");
    document.body.classList.remove("menu-open");
    menuToggle.setAttribute("aria-expanded", "false");
    menuTimer = setTimeout(function () { menu.hidden = true; }, 280);
    if (restoreFocus !== false) menuToggle.focus();
  }

  if (menuToggle && menu) {
    menuToggle.addEventListener("click", function () {
      if (menu.hidden) openMenu(); else closeMenu();
    });
    if (menuClose) menuClose.addEventListener("click", function () { closeMenu(); });
    document.addEventListener("keydown", function (e) {
      if (menu.hidden) return;
      if (e.key === "Escape") { closeMenu(); return; }
      if (e.key !== "Tab") return;
      var items = focusableInMenu();
      if (!items.length) return;
      var first = items[0];
      var last = items[items.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    });
  }

  /* ---------------- Scroll reveal (one shot) ---------------- */
  var revealItems = qsa("[data-reveal]");
  if (revealItems.length && !reduce && "IntersectionObserver" in window) {
    document.body.classList.add("reveal-ready");
    var revealObserver = new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        if (!entry.isIntersecting) return;
        entry.target.classList.add("is-revealed");
        revealObserver.unobserve(entry.target);
      });
    }, { threshold: 0.1, rootMargin: "0px 0px -6% 0px" });
    revealItems.forEach(function (el) { revealObserver.observe(el); });
  }

  /* ---------------- Number count-up ---------------- */
  var counters = qsa("[data-count]");
  function animateCount(el) {
    var target = parseFloat(el.getAttribute("data-count"));
    if (!isFinite(target)) return;
    if (reduce) { el.textContent = String(target); return; }
    var decimals = (el.getAttribute("data-count").split(".")[1] || "").length;
    var duration = 880;
    var started = null;
    function frame(now) {
      if (started === null) started = now;
      var progress = Math.min(1, (now - started) / duration);
      var eased = 1 - Math.pow(1 - progress, 3);
      var value = target * eased;
      el.textContent = decimals ? value.toFixed(decimals) : String(Math.round(value));
      if (progress < 1) window.requestAnimationFrame(frame);
      else el.textContent = decimals ? target.toFixed(decimals) : String(target);
    }
    window.requestAnimationFrame(frame);
  }
  if (counters.length) {
    if (!reduce && "IntersectionObserver" in window) {
      var countObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (!entry.isIntersecting) return;
          animateCount(entry.target);
          countObserver.unobserve(entry.target);
        });
      }, { threshold: 0.35 });
      counters.forEach(function (el) { countObserver.observe(el); });
    } else {
      counters.forEach(function (el) {
        el.textContent = el.getAttribute("data-count");
      });
    }
  }

  /* ---------------- Demo tour ---------------- */
  var tour = qs("[data-tour]");
  if (tour) {
    var tabs = qsa("[data-tour-step]", tour);
    var images = qsa("[data-tour-img]", tour);
    var capK = qs("[data-tour-k]", tour);
    var capTitle = qs("[data-tour-title]", tour);
    var capDesc = qs("[data-tour-desc]", tour);
    var bar = qs("[data-tour-bar]", tour);
    var autoBtn = qs("[data-tour-toggle]", tour);
    var current = 0;
    var auto = !reduce;
    var visible = false;
    var hovering = false;
    var timer = null;
    var STEP_MS = 5600;

    var copy = [
      { k: "STEP 01", t: "录入商品资料", d: "填写商品名称、类别与价格，或从素材库挑选图片、参数表与文档，作为 AI 的参考资料。" },
      { k: "STEP 02", t: "AI 理解商品", d: "扫描来源、抽取评价证据，生成商品理解卡与经营判断；结论可以手动修正。" },
      { k: "STEP 03", t: "生成创意方向", d: "产出多个带证据的创意方向，每个方向都标注推荐素材与禁忌表达。" },
      { k: "STEP 04", t: "生成多平台内容", d: "一次产出小红书、抖音、快手、淘宝详情、视频脚本与视觉提示词，并逐条校验。" },
      { k: "STEP 05", t: "导出标准内容包", d: "打包 manifest、结构化报告与六渠道正文，附 SHA256 校验，归档到历史记录。" }
    ];

    function clearTimer() {
      if (timer) { clearTimeout(timer); timer = null; }
    }

    function schedule() {
      clearTimer();
      if (!auto || !visible || hovering || reduce) return;
      timer = setTimeout(function () { render((current + 1) % tabs.length, false); }, STEP_MS);
    }

    function render(index, userAction) {
      current = (index + tabs.length) % tabs.length;
      tabs.forEach(function (tab, i) {
        var active = i === current;
        tab.classList.toggle("is-active", active);
        tab.setAttribute("aria-selected", active ? "true" : "false");
        tab.setAttribute("tabindex", active ? "0" : "-1");
      });
      images.forEach(function (img, i) {
        img.classList.toggle("is-active", i === current);
        img.setAttribute("aria-hidden", i === current ? "false" : "true");
      });
      var panel = document.getElementById("tour-panel");
      if (panel) panel.setAttribute("aria-labelledby", tabs[current].id || "tour-tab-" + (current + 1));
      if (capK) capK.textContent = copy[current].k;
      if (capTitle) capTitle.textContent = copy[current].t;
      if (capDesc) capDesc.textContent = copy[current].d;
      if (bar) bar.style.width = (((current + 1) / tabs.length) * 100) + "%";
      if (userAction) schedule();
    }

    function setAuto(next) {
      auto = next && !reduce;
      if (autoBtn) {
        autoBtn.setAttribute("aria-pressed", auto ? "true" : "false");
        autoBtn.textContent = reduce ? "动效已关闭" : (auto ? "暂停轮播" : "继续轮播");
        autoBtn.disabled = reduce;
      }
      schedule();
    }

    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () { render(i, true); });
      tab.addEventListener("keydown", function (e) {
        var next = null;
        if (e.key === "ArrowRight" || e.key === "ArrowDown") next = (i + 1) % tabs.length;
        if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = (i - 1 + tabs.length) % tabs.length;
        if (e.key === "Home") next = 0;
        if (e.key === "End") next = tabs.length - 1;
        if (next === null) return;
        e.preventDefault();
        render(next, true);
        tabs[next].focus();
      });
    });

    if (autoBtn) {
      autoBtn.addEventListener("click", function () { setAuto(!auto); });
    }

    tour.addEventListener("mouseenter", function () { hovering = true; clearTimer(); });
    tour.addEventListener("mouseleave", function () { hovering = false; schedule(); });
    tour.addEventListener("focusin", function () { hovering = true; clearTimer(); });
    tour.addEventListener("focusout", function (e) {
      if (tour.contains(e.relatedTarget)) return;
      hovering = false;
      schedule();
    });

    if ("IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          visible = entry.isIntersecting;
          if (visible) schedule(); else clearTimer();
        });
      }, { threshold: 0.25 }).observe(tour);
    } else {
      visible = true;
      schedule();
    }

    document.addEventListener("visibilitychange", function () {
      if (document.hidden) clearTimer(); else schedule();
    });

    setAuto(!reduce);
    render(0, false);
  }

  /* ---------------- In-view muted video ---------------- */
  var video = qs("[data-autoplay-video]");
  if (video) {
    video.muted = true;
    video.defaultMuted = true;
    if (!reduce && "IntersectionObserver" in window) {
      new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) {
            var p = video.play();
            if (p && p.catch) p.catch(function () {});
          } else {
            try { video.pause(); } catch (err) {}
          }
        });
      }, { threshold: 0.35 }).observe(video);
    }
  }

  /* ---------------- Pipeline rail highlight ---------------- */
  var railLinks = qsa("[data-rail]");
  var pipeSteps = qsa(".pipe-step");
  if (railLinks.length && pipeSteps.length) {
    function setRail(id) {
      railLinks.forEach(function (link) {
        var active = link.getAttribute("href") === "#" + id;
        link.classList.toggle("is-active", active);
        if (active) link.setAttribute("aria-current", "true");
        else link.removeAttribute("aria-current");
      });
    }
    railLinks.forEach(function (link) {
      link.addEventListener("click", function () {
        setRail(link.getAttribute("href").slice(1));
      });
    });
    if ("IntersectionObserver" in window) {
      var railObserver = new IntersectionObserver(function (entries) {
        entries.forEach(function (entry) {
          if (entry.isIntersecting) setRail(entry.target.id);
        });
      }, { rootMargin: "-28% 0px -58% 0px", threshold: 0 });
      pipeSteps.forEach(function (step) { railObserver.observe(step); });
    }
  }
})();
