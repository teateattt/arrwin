const prefersReducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
const header = document.querySelector(".site-header");
const progress = document.querySelector(".scroll-progress");
const menuToggle = document.querySelector(".menu-toggle");
const siteNav = document.querySelector(".site-nav");
const stage = document.querySelector("#window-stage");
const windows = [...document.querySelectorAll(".demo-window")];
const arrangeButton = document.querySelector("#arrange-button");
const resetButton = document.querySelector("#reset-button");
const gapInput = document.querySelector("#window-gap");
const gapOutput = document.querySelector("#gap-output");
const stageStatus = document.querySelector("#stage-status");
const demoClock = document.querySelector("#demo-clock");
const occlusionCard = document.querySelector(".occlusion-card");
const occlusionToggle = document.querySelector("#occlusion-toggle");
const occlusionStatus = document.querySelector("#occlusion-status");

function setPageInteractions() {
  const menuLinks = siteNav.querySelectorAll("a");

  function closeMenu() {
    siteNav.classList.remove("is-open");
    menuToggle.setAttribute("aria-expanded", "false");
    menuToggle.querySelector(".sr-only").textContent = "打开导航";
    document.body.classList.remove("menu-open");
  }

  menuToggle.addEventListener("click", () => {
    const isOpen = menuToggle.getAttribute("aria-expanded") === "true";
    siteNav.classList.toggle("is-open", !isOpen);
    menuToggle.setAttribute("aria-expanded", String(!isOpen));
    menuToggle.querySelector(".sr-only").textContent = isOpen ? "打开导航" : "关闭导航";
    document.body.classList.toggle("menu-open", !isOpen);
  });

  menuLinks.forEach((link) => link.addEventListener("click", closeMenu));

  window.addEventListener("resize", () => {
    if (window.innerWidth > 920) closeMenu();
  });
}

function setScrollEffects() {
  let ticking = false;

  function updateScroll() {
    const scrollTop = window.scrollY;
    const scrollHeight = document.documentElement.scrollHeight - window.innerHeight;
    const ratio = scrollHeight > 0 ? Math.min(scrollTop / scrollHeight, 1) : 0;

    header.classList.toggle("is-scrolled", scrollTop > 18);
    progress.style.transform = `scaleX(${ratio})`;
    ticking = false;
  }

  window.addEventListener(
    "scroll",
    () => {
      if (!ticking) {
        window.requestAnimationFrame(updateScroll);
        ticking = true;
      }
    },
    { passive: true },
  );

  updateScroll();
}

function setRevealEffects() {
  const revealItems = document.querySelectorAll(".reveal");

  if (prefersReducedMotion.matches || !("IntersectionObserver" in window)) {
    revealItems.forEach((item) => item.classList.add("is-visible"));
    return;
  }

  const observer = new IntersectionObserver(
    (entries) => {
      entries.forEach((entry) => {
        if (!entry.isIntersecting) return;
        const item = entry.target;
        const siblings = [...item.parentElement.children].filter((child) =>
          child.classList.contains("reveal"),
        );
        const siblingIndex = Math.max(siblings.indexOf(item), 0);
        item.style.transitionDelay = `${Math.min(siblingIndex * 70, 280)}ms`;
        item.classList.add("is-visible");
        observer.unobserve(item);
      });
    },
    { threshold: 0.12, rootMargin: "0px 0px -40px 0px" },
  );

  revealItems.forEach((item) => observer.observe(item));
}

function resetWindowStyles() {
  windows.forEach((windowElement) => {
    windowElement.style.left = "";
    windowElement.style.top = "";
    windowElement.style.zIndex = "";
  });
}

function arrangeWindows() {
  resetWindowStyles();
  stage.classList.add("is-arranged");
  stage.classList.add("has-moved");
  stageStatus.textContent = "已整理 3 个窗口";
  arrangeButton.innerHTML = '重新整理 <span class="button-arrow" aria-hidden="true">↗</span>';
  arrangeButton.setAttribute("aria-label", "重新整理窗口");
}

function resetWindows() {
  stage.classList.remove("is-arranged");
  stage.classList.remove("has-moved");
  resetWindowStyles();
  stageStatus.textContent = "3 个窗口正在排队";
  arrangeButton.innerHTML = '一键整理 <span class="button-arrow" aria-hidden="true">↗</span>';
  arrangeButton.setAttribute("aria-label", "整理窗口");
}

function setWindowGap() {
  const value = gapInput.value;
  stage.style.setProperty("--window-gap", `${value}px`);
  gapOutput.value = `${value} px`;
  gapOutput.textContent = `${value} px`;
}

function setWindowDragging() {
  let activeWindow = null;
  let activeWindowIndex = -1;
  let dragOffsetX = 0;
  let dragOffsetY = 0;

  function pointerDown(event) {
    if (event.button !== undefined && event.button !== 0) return;

    activeWindow = event.currentTarget;
    activeWindowIndex = Number(activeWindow.dataset.window);
    const windowRect = activeWindow.getBoundingClientRect();
    dragOffsetX = event.clientX - windowRect.left;
    dragOffsetY = event.clientY - windowRect.top;

    stage.classList.remove("is-arranged");
    stage.classList.add("has-moved");
    stageStatus.textContent = "正在手动布置窗口";
    activeWindow.classList.add("is-dragging");
    activeWindow.style.zIndex = "30";
    activeWindow.setPointerCapture?.(event.pointerId);
    event.preventDefault();
  }

  function pointerMove(event) {
    if (!activeWindow) return;

    const stageRect = stage.getBoundingClientRect();
    const maxLeft = stageRect.width - activeWindow.offsetWidth - 8;
    const maxTop = stageRect.height - activeWindow.offsetHeight - 8;
    const nextLeft = Math.min(Math.max(event.clientX - stageRect.left - dragOffsetX, 8), maxLeft);
    const nextTop = Math.min(Math.max(event.clientY - stageRect.top - dragOffsetY, 45), maxTop);

    activeWindow.style.left = `${nextLeft}px`;
    activeWindow.style.top = `${nextTop}px`;
  }

  function pointerUp() {
    if (!activeWindow) return;

    activeWindow.classList.remove("is-dragging");
    activeWindow.style.zIndex = String(activeWindowIndex + 1);
    activeWindow = null;
    arrangeButton.innerHTML = '一键整理 <span class="button-arrow" aria-hidden="true">↗</span>';
  }

  windows.forEach((windowElement) => {
    windowElement.addEventListener("pointerdown", pointerDown);
    windowElement.addEventListener("pointermove", pointerMove);
    windowElement.addEventListener("pointerup", pointerUp);
    windowElement.addEventListener("pointercancel", pointerUp);
  });
}

function setOcclusionToggle() {
  occlusionToggle.addEventListener("click", () => {
    const isPaused = occlusionCard.classList.toggle("is-paused");
    occlusionToggle.setAttribute("aria-pressed", String(!isPaused));
    occlusionStatus.textContent = isPaused ? "已暂停" : "已开启";
    occlusionStatus.classList.toggle("off", isPaused);
  });
}

function setClock() {
  const now = new Date();
  demoClock.textContent = new Intl.DateTimeFormat("zh-CN", {
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  }).format(now);
}

function setCurrentYear() {
  document.querySelector("#current-year").textContent = String(new Date().getFullYear());
}

function initializeDemo() {
  arrangeButton.addEventListener("click", arrangeWindows);
  resetButton.addEventListener("click", resetWindows);
  gapInput.addEventListener("input", setWindowGap);

  setWindowGap();
  setWindowDragging();
  setClock();
  window.setInterval(setClock, 30_000);

  if (!prefersReducedMotion.matches) {
    window.setTimeout(() => {
      if (!stage.classList.contains("has-moved")) arrangeWindows();
    }, 1350);
  }
}

setPageInteractions();
setScrollEffects();
setRevealEffects();
setOcclusionToggle();
setCurrentYear();
initializeDemo();
