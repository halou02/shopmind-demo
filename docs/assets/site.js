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

  /* ==========================================================================
     Cinematic layer — intro, scroll progress, line reveals, scramble,
     pointer parallax, magnetic buttons, tilt + spotlight.
     Everything here is additive: if a hook is absent the block no-ops.
     ========================================================================== */
  var finePointer = false;
  try { finePointer = window.matchMedia("(pointer: fine)").matches; } catch (err) {}

  /* ---------------- Intro curtain: once per tab, skippable, never under reduce ---------- */
  var intro = document.getElementById("intro");
  var introSkip = qs("[data-intro-skip]");
  var INTRO_KEY = "shopmind.intro.seen";
  if (intro) {
    var alreadyPlayed = false;
    try { alreadyPlayed = !!sessionStorage.getItem(INTRO_KEY); } catch (err) { alreadyPlayed = false; }
    if (reduce || alreadyPlayed) {
      intro.remove();
      intro = null;
    } else {
      document.documentElement.classList.add("is-intro-lock");
      intro.hidden = false;
      var finish = function () {
        if (!intro || intro.classList.contains("is-out")) return;
        intro.classList.add("is-out");
        document.documentElement.classList.remove("is-intro-lock");
        try { sessionStorage.setItem(INTRO_KEY, "1"); } catch (err) {}
        setTimeout(function () { if (intro) intro.remove(); }, 520);
        if (introSkip) introSkip.removeEventListener("click", finish);
      };
      if (introSkip) introSkip.addEventListener("click", finish);
      document.addEventListener("keydown", function (e) { if (e.key === "Escape") finish(); });
      window.setTimeout(finish, 1180);
    }
  }

  /* ---------------- Scroll progress ---------------- */
  var progress = document.createElement("div");
  progress.className = "scroll-progress";
  progress.setAttribute("aria-hidden", "true");
  document.body.appendChild(progress);
  var progressQueued = false;
  function paintProgress() {
    progressQueued = false;
    var doc = document.documentElement;
    var max = doc.scrollHeight - window.innerHeight;
    var ratio = max > 0 ? Math.min(1, Math.max(0, window.scrollY / max)) : 0;
    progress.style.transform = "scaleX(" + ratio.toFixed(4) + ")";
  }
  window.addEventListener("scroll", function () {
    if (progressQueued) return;
    progressQueued = true;
    window.requestAnimationFrame(paintProgress);
  }, { passive: true });
  paintProgress();

  /* ---------------- Per-line reveal: split only after the webfont settles ---------- */
  var lineHosts = qsa('[data-reveal="line"]');
  function splitLines(host) {
    if (host.dataset.splitDone === "1") return;
    var lines = qsa(".ln", host);
    if (!lines.length) return;
    host.dataset.splitDone = "1";
    lines.forEach(function (ln) {
      var inner = ln.firstElementChild;
      if (!inner || inner.tagName !== "SPAN") {
        var text = ln.textContent;
        ln.textContent = "";
        inner = document.createElement("span");
        inner.textContent = text;
        ln.appendChild(inner);
      }
    });
  }
  if (lineHosts.length) {
    if (document.fonts && document.fonts.ready && typeof document.fonts.ready.then === "function") {
      document.fonts.ready.then(function () { lineHosts.forEach(splitLines); });
    } else {
      lineHosts.forEach(splitLines);
    }
  }

  /* ---------------- Scramble / decode ---------------- */
  var GLYPHS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/\\<>*#·";
  qsa("[data-scramble]").forEach(function (el) {
    var finalText = el.getAttribute("data-scramble") || el.textContent;
    if (reduce) { el.textContent = finalText; return; }
    var chars = finalText.split("");
    var frame = 0;
    var revealEvery = 2;
    // keep the resolved string in the DOM for assistive tech once we are done
    el.setAttribute("aria-label", finalText);
    var id = window.setInterval(function () {
      frame += 1;
      var done = frame * revealEvery;
      el.textContent = chars.map(function (c, i) {
        if (c === " ") return " ";
        if (i < done) return c;
        return GLYPHS[Math.floor(Math.random() * GLYPHS.length)];
      }).join("");
      if (done >= chars.length) {
        window.clearInterval(id);
        el.textContent = finalText;
      }
    }, 34);
  });

  /* ---------------- WELCOME letter cascade ---------------- */
  var welcome = qs("[data-welcome]");
  if (welcome && !reduce) {
    var wText = welcome.textContent.trim();
    welcome.textContent = "";
    wText.split("").forEach(function (ch, i) {
      var s = document.createElement("span");
      s.className = "ch";
      s.textContent = ch;
      s.style.opacity = "0";
      s.style.transform = "translateY(.5em) rotateX(-70deg)";
      s.style.transition = "opacity .5s ease " + (i * 0.045 + 0.25) + "s, transform .7s cubic-bezier(.16,1,.3,1) " + (i * 0.045 + 0.25) + "s";
      welcome.appendChild(s);
    });
    window.setTimeout(function () {
      qsa(".ch", welcome).forEach(function (s) { s.style.opacity = "1"; s.style.transform = "none"; });
    }, intro ? 1150 : 60);
  }

  /* ---------------- Pointer parallax + magnetic + tilt, one shared loop ---------- */
  var pointer = { x: 0, y: 0, tx: 0, ty: 0, active: false };
  var parallaxTargets = [];
  function registerParallax(sel, depth) {
    var el = qs(sel);
    if (el) parallaxTargets.push({ el: el, depth: depth, cx: 0, cy: 0 });
  }
  if (finePointer && !reduce) {
    registerParallax(".hero-art", -14);
    registerParallax(".hero-bloom", 10);
    registerParallax(".hero-brand", 7);
    registerParallax(".hero-left", 4);

    var heroSection = qs(".hero");
    window.addEventListener("pointermove", function (e) {
      if (e.pointerType !== "mouse") return;
      pointer.tx = (e.clientX / window.innerWidth - 0.5) * 2;
      pointer.ty = (e.clientY / window.innerHeight - 0.5) * 2;
      pointer.active = true;
      kick();
    }, { passive: true });
    if (heroSection) {
      heroSection.addEventListener("pointerleave", function () {
        pointer.tx = 0; pointer.ty = 0;
        kick();
      });
    }
  }

  var magnets = finePointer && !reduce ? qsa("[data-magnetic]") : [];
  magnets.forEach(function (el) {
    el.addEventListener("pointermove", function (e) {
      var r = el.getBoundingClientRect();
      var dx = (e.clientX - (r.left + r.width / 2)) * 0.22;
      var dy = (e.clientY - (r.top + r.height / 2)) * 0.3;
      el.style.transform = "translate(" + Math.max(-14, Math.min(14, dx)) + "px," + Math.max(-10, Math.min(10, dy)) + "px)";
    });
    el.addEventListener("pointerleave", function () { el.style.transform = ""; });
  });

  var tilts = finePointer && !reduce ? qsa(".tilt") : [];
  tilts.forEach(function (el) {
    el.addEventListener("pointermove", function (e) {
      var r = el.getBoundingClientRect();
      var px = (e.clientX - r.left) / r.width;
      var py = (e.clientY - r.top) / r.height;
      el.style.setProperty("--mx", (px * 100).toFixed(1) + "%");
      el.style.setProperty("--my", (py * 100).toFixed(1) + "%");
      el.style.transform = "perspective(760px) rotateY(" + ((px - 0.5) * 9).toFixed(2) + "deg) rotateX(" + ((0.5 - py) * 9).toFixed(2) + "deg) translateZ(0)";
    });
    el.addEventListener("pointerleave", function () { el.style.transform = ""; });
  });

  qsa(".spot").forEach(function (el) {
    if (!finePointer) return;
    el.addEventListener("pointermove", function (e) {
      var r = el.getBoundingClientRect();
      el.style.setProperty("--mx", (((e.clientX - r.left) / r.width) * 100).toFixed(1) + "%");
      el.style.setProperty("--my", (((e.clientY - r.top) / r.height) * 100).toFixed(1) + "%");
    });
  });

  var rafId = 0;
  function kick() {
    if (!rafId && pointer.active) rafId = window.requestAnimationFrame(loop);
  }
  function loop() {
    rafId = 0;
    var dx = pointer.tx - pointer.x;
    var dy = pointer.ty - pointer.y;
    pointer.x += dx * 0.075;
    pointer.y += dy * 0.075;
    for (var i = 0; i < parallaxTargets.length; i++) {
      var t = parallaxTargets[i];
      t.cx = pointer.x * t.depth;
      t.cy = pointer.y * t.depth;
      t.el.style.transform = "translate3d(" + t.cx.toFixed(2) + "px," + t.cy.toFixed(2) + "px,0)";
    }
    if (Math.abs(dx) > 0.0015 || Math.abs(dy) > 0.0015) {
      rafId = window.requestAnimationFrame(loop);
    } else {
      pointer.active = false;
    }
  }
})();
