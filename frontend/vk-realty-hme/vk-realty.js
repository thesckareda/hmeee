/**
 * VK Realty — scroll-scrubbed canvas (Apple-style)
 */
(function () {
  const FRAME_COUNT = 114;
  const FRAME_PREFIX = "/static/vk-realty-hme/frames/CICD_";
  const SCROLL_HEIGHT = 5200;
  const LERP = 0.15;
  const BATCH_SIZE = 20;

  const WORDMARK_INTRO_MS = 1000;
  const BUILDING_CENTER_X = 0.275;
  const BUILDING_TOP = 0.435;
  const WORDMARK_GAP_PX = 14;

  const SECTIONS = [
    { id: "hero", start: 0, end: 0.15 },
    { id: "ring", start: 0.15, end: 0.35 },
    { id: "answer", start: 0.35, end: 0.6 },
    { id: "book", start: 0.6, end: 0.85 },
    { id: "trust", start: 0.85, end: 0.95 },
  ];

  const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  const isMobile = window.matchMedia("(max-width: 900px)").matches;
  const frameStep = isMobile ? 2 : 1;

  const loader = document.getElementById("loader");
  const loaderFill = document.getElementById("loader-fill");
  const loaderLabel = document.getElementById("loader-label");
  const canvas = document.getElementById("scrub-canvas");
  const stage = document.getElementById("scrub-stage");
  const scrubSticky = document.querySelector(".scrub-sticky");
  const trustPanel = document.getElementById("panel-trust");
  const wordmarkFly = document.getElementById("wordmark-fly");
  const navBrand = document.getElementById("nav-brand");
  const copyLayer = document.querySelector(".scrub-copy-layer");
  const ctx = canvas.getContext("2d");

  const frames = [];
  let frameLayout = { dx: 0, dy: 0, dw: 0, dh: 0 };
  let brandFontReady = false;
  let loaded = 0;
  let displayIndex = 0;
  let targetIndex = 0;
  let rafId = null;
  let introRafId = null;
  let introStart = 0;
  let introDone = false;
  let imagesReady = false;

  function frameUrl(i) {
    return `${FRAME_PREFIX}${i}.jpg`;
  }

  function buildFrameIndices() {
    const indices = [];
    for (let i = 0; i < FRAME_COUNT; i += frameStep) indices.push(i);
    if (indices[indices.length - 1] !== FRAME_COUNT - 1) {
      indices.push(FRAME_COUNT - 1);
    }
    return indices;
  }

  const frameIndices = buildFrameIndices();
  const totalLoad = frameIndices.length;

  function setLoaderProgress() {
    const pct = Math.round((loaded / totalLoad) * 100);
    if (loaderFill) loaderFill.style.width = `${pct}%`;
    if (loaderLabel) loaderLabel.textContent = `Loading experience… ${pct}%`;
  }

  function loadFrame(num) {
    return new Promise((resolve, reject) => {
      const img = new Image();
      img.onload = () => {
        loaded += 1;
        setLoaderProgress();
        resolve({ index: num, img });
      };
      img.onerror = reject;
      img.src = frameUrl(num);
    });
  }

  async function preloadFrames() {
    await ensureBrandFont();

    // Blocking batch: first frames for instant hero
    const firstBatch = frameIndices.slice(0, Math.min(BATCH_SIZE, frameIndices.length));
    const rest = frameIndices.slice(firstBatch.length);

    for (const num of firstBatch) {
      const item = await loadFrame(num);
      frames.push(item);
    }
    frames.sort((a, b) => a.index - b.index);

    // Async rest
    await Promise.all(
      rest.map(async (num) => {
        try {
          const item = await loadFrame(num);
          frames.push(item);
          frames.sort((a, b) => a.index - b.index);
        } catch (e) {
          console.warn("Frame load failed", num, e);
        }
      })
    );

    imagesReady = true;
    if (loader) loader.classList.add("hidden");
    window.dispatchEvent(new CustomEvent("scrub:frames-ready"));
    resizeCanvas();
    drawFrame(displayIndex);
    startWordmarkIntro();
    if (!reducedMotion) requestTick();
  }

  function resizeCanvas() {
    const wrap = canvas.parentElement;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const rect = wrap.getBoundingClientRect();
    canvas.width = rect.width * dpr;
    canvas.height = rect.height * dpr;
    canvas.style.width = `${rect.width}px`;
    canvas.style.height = `${rect.height}px`;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  }

  function easeInOutCubic(t) {
    return t < 0.5 ? 4 * t * t * t : 1 - Math.pow(-2 * t + 2, 3) / 2;
  }

  function ensureBrandFont() {
    if (brandFontReady || !document.fonts) {
      brandFontReady = true;
      return Promise.resolve();
    }
    return Promise.all([
      document.fonts.load('800 26px "Syne"'),
      document.fonts.load('800 40px "Syne"'),
      document.fonts.load('800 56px "Syne"'),
    ]).then(() => {
      brandFontReady = true;
    }).catch(() => {
      brandFontReady = true;
    });
  }

  function measureWordmark(fontSize) {
    if (!wordmarkFly) return { width: 0, height: 0 };
    wordmarkFly.style.fontSize = `${fontSize}px`;
    wordmarkFly.style.transform = "translate3d(-9999px, 0, 0)";
    wordmarkFly.style.opacity = "1";
    const width = wordmarkFly.offsetWidth;
    const height = wordmarkFly.offsetHeight;
    return { width, height };
  }

  function wordmarkStartMetrics(wrapRect) {
    const startFontSize = Math.min(Math.max(wrapRect.width * 0.055, 44), 64);
    const { width, height } = measureWordmark(startFontSize);
    const frameLeft = wrapRect.left + frameLayout.dx;
    const frameTop = wrapRect.top + frameLayout.dy;
    const buildingTopPx = frameTop + frameLayout.dh * BUILDING_TOP;
    const buildingCenterX = frameLeft + frameLayout.dw * BUILDING_CENTER_X;
    return {
      startFontSize,
      startX: buildingCenterX - width / 2,
      startY: buildingTopPx - WORDMARK_GAP_PX - height,
      width,
      height,
    };
  }

  function applyWordmarkAtT(t) {
    if (!wordmarkFly || !navBrand || !brandFontReady) return;

    const wrap = canvas.parentElement;
    if (!wrap || !frameLayout.dh) return;
    const wrapRect = wrap.getBoundingClientRect();
    const { startFontSize, startX, startY } = wordmarkStartMetrics(wrapRect);

    const endRect = navBrand.getBoundingClientRect();
    const endX = endRect.left;
    const endY = endRect.top;
    const endFontSize = parseFloat(getComputedStyle(navBrand).fontSize) || 24;

    const eased = easeInOutCubic(Math.max(0, Math.min(1, t)));
    const fontSize = startFontSize + (endFontSize - startFontSize) * eased;
    const x = startX + (endX - startX) * eased;
    const y = startY + (endY - startY) * eased;

    wordmarkFly.style.fontSize = `${fontSize}px`;
    wordmarkFly.style.opacity = "1";
    wordmarkFly.style.transform = `translate3d(${x}px, ${y}px, 0)`;
  }

  function finishWordmarkIntro() {
    introDone = true;
    if (wordmarkFly) wordmarkFly.style.opacity = "0";
    if (navBrand) navBrand.classList.add("is-visible");
    copyLayer?.classList.remove("intro-hidden");
    if (introRafId) cancelAnimationFrame(introRafId);
    introRafId = null;
    updateCopyPanels(scrollProgress());
  }

  function startWordmarkIntro() {
    if (reducedMotion) {
      finishWordmarkIntro();
      return;
    }

    introDone = false;
    navBrand?.classList.remove("is-visible");
    copyLayer?.classList.add("intro-hidden");
    document.querySelectorAll(".copy-panel").forEach((p) => p.classList.remove("active"));
    introStart = performance.now();

    function introTick(now) {
      const elapsed = now - introStart;
      const t = Math.min(1, elapsed / WORDMARK_INTRO_MS);
      applyWordmarkAtT(t);

      if (elapsed < WORDMARK_INTRO_MS) {
        introRafId = requestAnimationFrame(introTick);
        return;
      }
      finishWordmarkIntro();
    }

    if (introRafId) cancelAnimationFrame(introRafId);
    introRafId = requestAnimationFrame(introTick);
  }

  function drawFrame(floatIndex) {
    if (!frames.length) return;
    const maxIdx = frames.length - 1;
    const idx = Math.max(0, Math.min(maxIdx, Math.round(floatIndex)));
    const { img } = frames[idx];
    const w = canvas.width / (window.devicePixelRatio || 1);
    const h = canvas.height / (window.devicePixelRatio || 1);

    const pageBg = getComputedStyle(document.documentElement).getPropertyValue("--bg").trim();
    ctx.fillStyle = pageBg || "#d0d1d5";
    ctx.fillRect(0, 0, w, h);

    // Top-align, width-fill — removes top white band from vertical centering
    const scale = w / img.width;
    const dw = w;
    const dh = img.height * scale;
    const dx = 0;
    const dy = 0;

    frameLayout = { dx, dy, dw, dh };
    ctx.drawImage(img, dx, dy, dw, dh);
  }

  function updateWordmarkOnResize() {
    if (!introDone && introStart > 0) {
      const elapsed = Math.min(WORDMARK_INTRO_MS, performance.now() - introStart);
      applyWordmarkAtT(elapsed / WORDMARK_INTRO_MS);
      return;
    }
    if (wordmarkFly) wordmarkFly.style.opacity = "0";
    navBrand?.classList.add("is-visible");
  }

  function scrollProgress() {
    const rect = stage.getBoundingClientRect();
    const scrollable = stage.offsetHeight - window.innerHeight;
    if (scrollable <= 0) return 0;
    const scrolled = -rect.top;
    return Math.max(0, Math.min(1, scrolled / scrollable));
  }

  function progressToFrameIndex(progress) {
    return progress * (frames.length - 1);
  }

  function updateTrustOcclusion(progress) {
    const trustStart = 0.85;
    const trustEnd = 0.95;
    const inTrust = progress >= trustStart && progress < trustEnd;
    scrubSticky?.classList.toggle("building-front", inTrust);

    if (!trustPanel) return;
    if (!inTrust) {
      trustPanel.style.clipPath = "";
      return;
    }

    const local = (progress - trustStart) / (trustEnd - trustStart);
    const cover = Math.min(1, Math.max(0, (local - 0.08) / 0.82));
    const insetLeft = cover * 72;
    trustPanel.style.clipPath = `inset(0 0 0 ${insetLeft}%)`;
  }

  function updateCopyPanels(progress) {
    if (!introDone) return;

    SECTIONS.forEach(({ id, start, end }) => {
      const panel = document.getElementById(`panel-${id}`);
      if (!panel) return;
      const inRange = progress >= start && progress < end;
      panel.classList.toggle("active", inRange);
    });

    updateTrustOcclusion(progress);
  }

  function tick() {
    if (!imagesReady || reducedMotion) return;

    const progress = scrollProgress();
    targetIndex = progressToFrameIndex(progress);
    displayIndex += (targetIndex - displayIndex) * LERP;

    if (Math.abs(targetIndex - displayIndex) < 0.02) {
      displayIndex = targetIndex;
    }

    drawFrame(displayIndex);
    updateCopyPanels(progress);
    rafId = requestAnimationFrame(tick);
  }

  function requestTick() {
    if (rafId) cancelAnimationFrame(rafId);
    rafId = requestAnimationFrame(tick);
  }

  // Reduced motion: static frame + stacked panels
  async function initReducedMotion() {
    await ensureBrandFont();
    if (loader) loader.classList.add("hidden");
    loadFrame(0).then((item) => {
      frames.push(item);
      imagesReady = true;
      resizeCanvas();
      drawFrame(0);
      finishWordmarkIntro();
      document.querySelectorAll(".copy-panel").forEach((p) => p.classList.add("active"));
    });
  }

  // Audio demo
  const DEMO_LINES = [
    "Namaste andi! Nenu Arjun, VK Realty nunchi matladutunnanu.",
    "Kokapet lo 3 BHK flat price 1.2 Crores nunchi start avutundi. Site visit schedule cheddama?",
  ];

  function playVoiceDemo() {
    if (!("speechSynthesis" in window)) {
      window.open("/demo", "_blank");
      return;
    }
    window.speechSynthesis.cancel();
    let i = 0;
    function speakNext() {
      if (i >= DEMO_LINES.length) return;
      const u = new SpeechSynthesisUtterance(DEMO_LINES[i]);
      u.lang = "te-IN";
      u.rate = 0.95;
      u.onend = () => {
        i += 1;
        setTimeout(speakNext, 400);
      };
      window.speechSynthesis.speak(u);
    }
    speakNext();
  }

  document.getElementById("btn-hear-demo")?.addEventListener("click", playVoiceDemo);
  document.querySelectorAll("[data-hear-demo]").forEach((el) => {
    el.addEventListener("click", (e) => {
      e.preventDefault();
      playVoiceDemo();
    });
  });

  document.getElementById("btn-book-demo")?.addEventListener("click", () => {
    window.location.href = "mailto:sales@speaksy.ai?subject=Property%20Demo%20Request";
  });

  window.addEventListener("resize", () => {
    resizeCanvas();
    if (imagesReady) {
      drawFrame(displayIndex);
      updateWordmarkOnResize();
    }
  });

  if (reducedMotion) {
    initReducedMotion();
  } else {
    preloadFrames().catch((err) => {
      console.error(err);
      if (loaderLabel) loaderLabel.textContent = "Could not load frames. Refresh to retry.";
    });
  }
})();