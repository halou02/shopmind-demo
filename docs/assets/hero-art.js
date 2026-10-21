/* ==========================================================================
   ShopMind — hero-art.js
   A fixed-seed orange light field drawn with Canvas 2D.
   - No third-party code
   - DPR capped at 2
   - Pauses when the hero leaves the viewport or the tab is hidden
   - Draws a single static frame when prefers-reduced-motion is set
   ========================================================================== */
(function () {
  "use strict";

  var canvas = document.getElementById("hero-canvas");
  if (!canvas) return;

  /* The flow field is decoration. Under mobile CPU throttling it measured
     1196ms of main-thread work and pushed index.html LCP to 5558ms, so it is
     not initialised below the width where the hero stops compositing it anyway. */
  var narrow = window.matchMedia("(max-width: 900px)");
  if (narrow.matches) {
    canvas.remove();
    return;
  }

  var hero = canvas.closest(".hero") || canvas.parentElement;
  var ctx = canvas.getContext("2d", { alpha: true });
  if (!ctx) return;

  var reduce = false;
  try {
    reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  } catch (err) {}

  var dpr = Math.min(window.devicePixelRatio || 1, 2);
  var w = 1;
  var h = 1;
  var particles = [];
  var rafId = 0;
  var running = false;
  var startTime = 0;
  var lastFrame = 0;
  var SEED = 20261021;

  function makeRandom(seed) {
    var state = seed >>> 0;
    return function () {
      state = (state * 1664525 + 1013904223) >>> 0;
      return state / 4294967296;
    };
  }

  function angleAt(x, y, time) {
    return (
      Math.sin(x * 0.0021 + time * 0.16) * 1.55 +
      Math.cos(y * 0.0017 - time * 0.13) * 1.35 +
      Math.sin((x + y) * 0.00085 + time * 0.08) * 0.85
    );
  }

  function initParticles() {
    var random = makeRandom(SEED);
    var count = Math.round(w * h / 15500);
    count = Math.max(46, Math.min(148, count));
    if (w < 680) count = Math.round(count * 0.58);
    particles = [];
    for (var i = 0; i < count; i++) {
      particles.push({
        x: random() * w,
        y: random() * h,
        px: 0,
        py: 0,
        size: 0.5 + random() * 1.35,
        speed: 0.22 + random() * 0.42,
        alpha: 0.12 + random() * 0.34,
        warm: random()
      });
    }
  }

  var blobs = [
    { x: 0.69, y: 0.27, r: 0.52, rgb: "255,116,48", a: 0.11, sx: 0.026, sy: 0.021 },
    { x: 0.84, y: 0.55, r: 0.4, rgb: "255,62,40", a: 0.085, sx: 0.018, sy: 0.026 },
    { x: 0.35, y: 0.16, r: 0.44, rgb: "255,181,108", a: 0.06, sx: 0.013, sy: 0.017 }
  ];

  function drawBlobs(time) {
    var minSide = Math.min(w, h);
    for (var i = 0; i < blobs.length; i++) {
      var b = blobs[i];
      var cx = w * b.x + Math.sin(time * b.sx + i * 1.7) * minSide * 0.045;
      var cy = h * b.y + Math.cos(time * b.sy + i * 1.1) * minSide * 0.035;
      var radius = minSide * b.r;
      var grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius);
      grad.addColorStop(0, "rgba(" + b.rgb + "," + b.a + ")");
      grad.addColorStop(0.48, "rgba(" + b.rgb + "," + (b.a * 0.38) + ")");
      grad.addColorStop(1, "rgba(" + b.rgb + ",0)");
      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(cx, cy, radius, 0, Math.PI * 2);
      ctx.fill();
    }
  }

  function step(time) {
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      var angle = angleAt(p.x, p.y, time);
      p.px = p.x;
      p.py = p.y;
      p.x += Math.cos(angle) * p.speed * 1.35;
      p.y += Math.sin(angle) * p.speed * 1.35;

      if (p.x < -24) p.x = w + 24;
      if (p.x > w + 24) p.x = -24;
      if (p.y < -24) p.y = h + 24;
      if (p.y > h + 24) p.y = -24;

      var warm = p.warm > 0.5 ? "255,181,108" : "255,106,58";
      ctx.strokeStyle = "rgba(" + warm + "," + p.alpha + ")";
      ctx.lineWidth = p.size * 0.55;
      ctx.beginPath();
      ctx.moveTo(p.px, p.py);
      ctx.lineTo(p.x, p.y);
      ctx.stroke();

      if (p.size > 1.15) {
        ctx.fillStyle = "rgba(" + warm + "," + p.alpha * 1.35 + ")";
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size * 0.44, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  function drawAnimated(timeSeconds) {
    ctx.clearRect(0, 0, w, h);
    ctx.globalCompositeOperation = "lighter";
    drawBlobs(timeSeconds);
    step(timeSeconds);
  }

  function drawStatic() {
    ctx.clearRect(0, 0, w, h);
    ctx.globalCompositeOperation = "lighter";
    drawBlobs(1.4);
    var random = makeRandom(SEED + 7);
    for (var i = 0; i < particles.length; i++) {
      var p = particles[i];
      var warm = p.warm > 0.5 ? "255,181,108" : "255,106,58";
      ctx.fillStyle = "rgba(" + warm + "," + (p.alpha * 0.95) + ")";
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.size * 0.55, 0, Math.PI * 2);
      ctx.fill();
      if (random() > 0.72) {
        ctx.strokeStyle = "rgba(" + warm + "," + (p.alpha * 0.45) + ")";
        ctx.lineWidth = 0.5;
        ctx.beginPath();
        ctx.moveTo(p.x - 10, p.y + 12);
        ctx.lineTo(p.x, p.y);
        ctx.stroke();
      }
    }
  }

  function resize() {
    var rect = canvas.getBoundingClientRect();
    w = Math.max(1, Math.round(rect.width || window.innerWidth));
    h = Math.max(1, Math.round(rect.height || window.innerHeight));
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    initParticles();
    startTime = performance.now();
    lastFrame = 0;
    if (reduce) {
      drawStatic();
    } else {
      drawAnimated(0.6);
    }
  }

  function frame(now) {
    if (!running) return;
    rafId = window.requestAnimationFrame(frame);
    if (now - lastFrame < 33) return;
    lastFrame = now;
    drawAnimated((now - startTime) / 1000);
  }

  function start() {
    if (reduce || running) return;
    running = true;
    rafId = window.requestAnimationFrame(frame);
  }

  function stop() {
    running = false;
    if (rafId) {
      window.cancelAnimationFrame(rafId);
      rafId = 0;
    }
  }

  var resizeTimer = null;
  var visibleInViewport = true;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(resize, 150);
  });

  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stop();
    else if (visibleInViewport) start();
  });

  if (!reduce && "IntersectionObserver" in window && hero) {
    new IntersectionObserver(function (entries) {
      entries.forEach(function (entry) {
        visibleInViewport = entry.isIntersecting;
        if (entry.isIntersecting) start(); else stop();
      });
    }, { threshold: 0.02 }).observe(hero);
  }

  resize();
  if (!reduce) start();
})();
