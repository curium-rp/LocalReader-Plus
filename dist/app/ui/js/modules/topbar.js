import { THEME_LIST, getCurrentThemeId, setTheme } from "./themes.js";
import { fetchJSON } from "./api.js";
import { getTop9Recent } from "./recent.js";
import { openTypoMenu, closeTypoMenu } from "./typography.js";

function nativeApi() {
  return window.pywebview?.api || null;
}

let hoverSwitchTimer = null;

function scheduleMenuSwitch(fn, delay = 80) {
  cancelMenuSwitch();
  hoverSwitchTimer = setTimeout(() => {
    hoverSwitchTimer = null;
    fn();
  }, delay);
}

function cancelMenuSwitch() {
  if (hoverSwitchTimer) {
    clearTimeout(hoverSwitchTimer);
    hoverSwitchTimer = null;
  }
}

function closeTocDrawer() {
  const tocModal = document.getElementById("tocModal");
  if (tocModal && !tocModal.classList.contains("hidden")) {
    tocModal.classList.add("hidden");
  }
}

function isSettingsOpen() {
  const trigger = document.getElementById("settingsMenuTrigger");
  const typoMenu = document.getElementById("typoFloatMenu");
  return !!(trigger?.classList.contains("open") || typoMenu?.classList.contains("open"));
}

function closeAllMenus() {
  cancelMenuSwitch();
  document.querySelectorAll(".menubar-item.open").forEach((el) => el.classList.remove("open"));
  document.getElementById("settingsMenuTrigger")?.classList.remove("open");
  document.getElementById("progressMetric")?.classList.remove("open");
  closeTypoMenu();
}

function menubarIsOpen() {
  return !!document.querySelector(".menubar-item.open") || isSettingsOpen();
}

function openMenu(item) {
  cancelMenuSwitch();
  closeAllMenus();
  closeTocDrawer();
  if (item) item.classList.add("open");
  if (item?.dataset.menu === "file") populateRecentMenu();
}

function openSettingsMenu() {
  cancelMenuSwitch();
  closeAllMenus();
  closeTocDrawer();
  const trigger = document.getElementById("settingsMenuTrigger");
  trigger?.classList.add("open");
  openTypoMenu();
}

function wireMenubar() {
  const menubar = document.getElementById("appMenubar");
  if (!menubar) return;

  menubar.querySelectorAll(".menubar-item").forEach((item) => {
    const title = item.querySelector(".menubar-title");
    const dropdown = item.querySelector(".menubar-dropdown");

    if (title) {
      title.addEventListener("click", (e) => {
        e.stopPropagation();
        cancelMenuSwitch();
        const wasOpen = item.classList.contains("open");
        closeAllMenus();
        if (!wasOpen) {
          closeTocDrawer();
          item.classList.add("open");
          if (item.dataset.menu === "file") populateRecentMenu();
        }
      });

      title.addEventListener("mouseenter", () => {
        if (!menubarIsOpen()) return;
        if (item.classList.contains("open")) {
          cancelMenuSwitch();
          return;
        }
        scheduleMenuSwitch(() => openMenu(item));
      });
    }

    if (dropdown) {
      dropdown.addEventListener("mouseenter", cancelMenuSwitch);
    }
  });

  document.addEventListener("click", (e) => {
    if (
      e.target.closest(".menubar-item") ||
      e.target.closest("#settingsMenuTrigger") ||
      e.target.closest("#typoFloatMenu") ||
      e.target.closest("#typoMenuBtn")
    ) {
      return;
    }
    closeAllMenus();
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closeAllMenus();
    const fileMenu = document.querySelector('.menubar-item[data-menu="file"]');
    if (!fileMenu?.classList.contains("open")) return;
    if (["INPUT", "TEXTAREA"].includes(document.activeElement?.tagName)) return;
    if (document.activeElement?.isContentEditable) return;
    if (!/^[1-9]$/.test(e.key)) return;
    e.preventDefault();
    const targetBtn = document.querySelector(
      `#fileMenuRecentList .menubar-recent-item[data-index="${e.key}"]`,
    );
    if (targetBtn) targetBtn.click();
  });
}

function wireFileMenu() {
  const fileInput = document.getElementById("pdfUpload");
  document.getElementById("topbarOpenBookBtn")?.addEventListener("click", () => {
    closeAllMenus();
    fileInput?.click();
  });
  document.getElementById("topbarExportAudioBtn")?.addEventListener("click", () => {
    closeAllMenus();
    document.getElementById("exportBtn")?.click();
  });
}

function escapeMenuText(str) {
  return String(str || "").replace(/[&<>'"]/g, (ch) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    "'": "&#39;",
    '"': "&quot;",
  }[ch] || ch));
}

async function populateRecentMenu() {
  const container = document.getElementById("fileMenuRecentList");
  const divider = document.getElementById("fileMenuRecentDivider");
  if (!container) return;

  try {
    const items = await fetchJSON(`/api/library?t=${Date.now()}`);
    const top9 = getTop9Recent(items);
    container.innerHTML = "";

    if (top9.length === 0) {
      if (divider) divider.hidden = true;
      return;
    }

    if (divider) divider.hidden = false;
    const fragment = document.createDocumentFragment();
    top9.forEach((book, idx) => {
      const num = idx + 1;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "menubar-recent-item";
      btn.dataset.index = String(num);
      btn.dataset.id = book.id;
      btn.title = book.fileName || "";
      btn.innerHTML = `
        <span class="menubar-num"><u>${num}</u></span>
        <span class="menubar-recent-title">${escapeMenuText(book.fileName)}</span>
      `;
      btn.onclick = (e) => {
        e.preventDefault();
        e.stopPropagation();
        closeAllMenus();
        if (typeof window.selectDocById === "function") {
          window.selectDocById(book.id);
        }
      };
      fragment.appendChild(btn);
    });
    container.appendChild(fragment);
  } catch (err) {
    console.error("Failed to populate recent files:", err);
  }
}

function wireSettingsMenu() {
  const trigger = document.getElementById("settingsMenuTrigger");
  const typoMenu = document.getElementById("typoFloatMenu");

  trigger?.addEventListener("click", (e) => {
    e.stopPropagation();
    cancelMenuSwitch();
    const wasOpen = isSettingsOpen();
    closeAllMenus();
    if (!wasOpen) {
      openSettingsMenu();
    }
  });

  trigger?.addEventListener("mouseenter", () => {
    if (!menubarIsOpen()) return;
    if (isSettingsOpen()) {
      cancelMenuSwitch();
      return;
    }
    scheduleMenuSwitch(() => openSettingsMenu());
  });

  if (typoMenu) {
    typoMenu.addEventListener("mouseenter", cancelMenuSwitch);
  }

  const tocBtn = document.getElementById("tocBtn");
  tocBtn?.addEventListener("click", () => {
    cancelMenuSwitch();
    closeAllMenus();
  });
}

function renderThemeMenu() {
  const host = document.getElementById("themeMenuContent");
  if (!host) return;
  const current = getCurrentThemeId();
  host.innerHTML = "";
  THEME_LIST.forEach((theme) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "menubar-item-btn" + (theme.id === current ? " is-active" : "");
    btn.dataset.themeId = theme.id;
    btn.textContent = theme.name;
    btn.addEventListener("click", async () => {
      closeAllMenus();
      await setTheme(theme.id);
      renderThemeMenu();
    });
    host.appendChild(btn);
  });
}

const DEFAULT_VIEW = {
  reader: {
    progressBar: true,
    pageCounter: true,
    percentage: true,
    clock: true,
  },
  library: {
    fraction: true,
    percentage: true,
  },
};

const VIEW_MENU = [
  {
    group: "Reader",
    category: "reader",
    items: [
      { key: "progressBar", label: "Show Progress Bar" },
      { key: "pageCounter", label: "Show Page Counter" },
      { key: "percentage", label: "Show Percentage" },
      { key: "clock", label: "Show Clock" },
    ],
  },
  {
    group: "Library Cards",
    category: "library",
    items: [
      { key: "fraction", label: "Show Book Fraction" },
      { key: "percentage", label: "Show Book Percentage" },
    ],
  },
];

const ATTR_MAP = {
  "reader.progressBar": "data-view-reader-progressbar",
  "reader.pageCounter": "data-view-reader-pagecounter",
  "reader.percentage": "data-view-reader-percentage",
  "reader.clock": "data-view-reader-clock",
  "library.fraction": "data-view-lib-fraction",
  "library.percentage": "data-view-lib-percentage",
};

let viewState = JSON.parse(JSON.stringify(DEFAULT_VIEW));

function mergeView(data) {
  const next = JSON.parse(JSON.stringify(DEFAULT_VIEW));
  if (data?.reader) {
    Object.keys(DEFAULT_VIEW.reader).forEach((key) => {
      if (typeof data.reader[key] === "boolean") next.reader[key] = data.reader[key];
    });
  }
  if (data?.library) {
    Object.keys(DEFAULT_VIEW.library).forEach((key) => {
      if (typeof data.library[key] === "boolean") next.library[key] = data.library[key];
    });
  }
  return next;
}

export function syncViewAttributes() {
  const root = document.documentElement;
  Object.entries(ATTR_MAP).forEach(([path, attr]) => {
    const [category, key] = path.split(".");
    const visible = viewState?.[category]?.[key] !== false;
    root.setAttribute(attr, visible ? "true" : "false");
  });
}

export async function setViewSetting(category, key, value) {
  if (!viewState[category] || !Object.prototype.hasOwnProperty.call(viewState[category], key)) {
    return;
  }
  viewState[category][key] = !!value;
  syncViewAttributes();
  renderViewMenu();
  try {
    await fetchJSON("/api/view", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(viewState),
    });
  } catch (err) {
    console.error("Failed to save view settings", err);
  }
}

function renderViewMenu() {
  const host = document.getElementById("viewMenuContent");
  if (!host) return;
  host.innerHTML = "";
  VIEW_MENU.forEach((section, idx) => {
    const label = document.createElement("div");
    label.className = "menubar-group-label";
    label.textContent = section.group;
    host.appendChild(label);

    section.items.forEach(({ key, label: itemLabel }) => {
      const checked = viewState[section.category][key] !== false;
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "menubar-item-btn" + (checked ? " is-active" : "");
      btn.textContent = itemLabel;
      btn.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        setViewSetting(section.category, key, !checked);
      });
      host.appendChild(btn);
    });

    if (idx < VIEW_MENU.length - 1) {
      const divider = document.createElement("div");
      divider.className = "menubar-divider";
      host.appendChild(divider);
    }
  });
}

export async function initViewMenu() {
  syncViewAttributes();
  renderViewMenu();
  try {
    const saved = await fetchJSON("/api/view");
    viewState = mergeView(saved);
  } catch {
    viewState = mergeView(DEFAULT_VIEW);
  }
  syncViewAttributes();
  renderViewMenu();
}

function setMaximizedChrome(maximized) {
  const isMax = !!maximized;
  document.documentElement.dataset.maximized = isMax ? "true" : "false";
  if (isMax) {
    document.documentElement.dataset.fullscreen = "false";
  }
  const maxBtn = document.getElementById("winMaxBtn");
  const restoreBtn = document.getElementById("winRestoreBtn");
  maxBtn?.classList.toggle("hidden", isMax);
  restoreBtn?.classList.toggle("hidden", !isMax);
}

function setFullscreenChrome(fullscreen) {
  const isFs = !!fullscreen;
  document.documentElement.dataset.fullscreen = isFs ? "true" : "false";
  if (isFs) {
    document.documentElement.dataset.maximized = "false";
    const maxBtn = document.getElementById("winMaxBtn");
    const restoreBtn = document.getElementById("winRestoreBtn");
    maxBtn?.classList.toggle("hidden", false);
    restoreBtn?.classList.toggle("hidden", true);
  }
}

function applyWindowState(state) {
  if (!state || typeof state !== "object") return;
  if (state.maximized && state.fullscreen) {
    console.warn("[WINDOW] Conflicting window state received (both maximized & fullscreen). Resetting.");
    setMaximizedChrome(false);
    setFullscreenChrome(false);
    return;
  }
  if (typeof state.fullscreen === "boolean") setFullscreenChrome(state.fullscreen);
  if (typeof state.maximized === "boolean") setMaximizedChrome(state.maximized);
}

function wireNoDragChrome() {
  const stop = (e) => e.stopPropagation();
  document.querySelectorAll(
    "#appMenubar, .window-controls, .topbar-icon-btn, #pageNav, #osClock, #searchBtn",
  ).forEach((el) => el.addEventListener("mousedown", stop));
}

function nativeCall(fn) {
  const api = nativeApi();
  if (!api) return Promise.resolve(undefined);
  const method = api[fn];
  if (typeof method !== "function") return Promise.resolve(undefined);
  return Promise.resolve(method());
}

async function toggleMaximize() {
  const maximized = await nativeCall("maximize_toggle");
  if (typeof maximized === "boolean") setMaximizedChrome(maximized);
  const state = await nativeCall("get_state");
  applyWindowState(state);
}

async function toggleFullscreen() {
  await nativeCall("fullscreen_toggle");
  const state = await nativeCall("get_state");
  applyWindowState(state);
}

function wireWindowControls() {
  window.__lrSetMaximized = setMaximizedChrome;
  window.__lrSetFullscreen = setFullscreenChrome;

  document.getElementById("winMinBtn")?.addEventListener("click", () => nativeCall("minimize"));
  document.getElementById("winMaxBtn")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleMaximize();
  });
  document.getElementById("winRestoreBtn")?.addEventListener("click", (e) => {
    e.preventDefault();
    e.stopPropagation();
    toggleMaximize();
  });
  document.getElementById("winCloseBtn")?.addEventListener("click", () => nativeCall("close"));

  const bar = document.getElementById("appTopBar");
  bar?.addEventListener("dblclick", (e) => {
    if (e.target.closest("button, input, select, .no-drag, .pywebview-no-drag")) return;
    toggleMaximize();
  });

  const syncFromNative = async () => {
    const state = await nativeCall("get_state");
    if (state && typeof state === "object") {
      applyWindowState(state);
      return;
    }
    const maximized = await nativeCall("is_maximized");
    if (typeof maximized === "boolean") setMaximizedChrome(maximized);
  };
  window.addEventListener("pywebviewready", syncFromNative);
  syncFromNative();

  window.addEventListener("keydown", (e) => {
    if (e.key !== "F11") return;
    e.preventDefault();
    e.stopPropagation();
    toggleFullscreen();
  }, true);
}

function wireSidebarToggle() {
  document.getElementById("topbarSidebarToggle")?.addEventListener("click", () => {
    document.getElementById("sidebarCollapseBtn")?.click();
  });
}

function wireClock() {
  const el = document.getElementById("osClock");
  if (!el) return;

  const formatter = new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });

  let timeoutId = 0;
  let intervalId = 0;

  const tick = () => {
    el.textContent = formatter.format(new Date());
  };

  const arm = () => {
    tick();
    window.clearTimeout(timeoutId);
    window.clearInterval(intervalId);
    const now = new Date();
    const wait = (60 - now.getSeconds()) * 1000 - now.getMilliseconds();
    timeoutId = window.setTimeout(() => {
      tick();
      intervalId = window.setInterval(tick, 60000);
    }, Math.max(200, wait));
  };

  arm();
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) arm();
  });
}

export function initTopBar() {
  wireMenubar();
  wireFileMenu();
  wireSettingsMenu();
  initViewMenu();
  renderThemeMenu();
  wireWindowControls();
  wireSidebarToggle();
  wireClock();
  wireNoDragChrome();
  
  initTightModeObserver();

  document.addEventListener("lr-theme-change", renderThemeMenu);
  document.addEventListener("lr-library-change", populateRecentMenu);
  populateRecentMenu();
}

export function initTightModeObserver() {
  const checkTightMode = () => {
    const rootStyle = getComputedStyle(document.documentElement);
    const bodyStyle = getComputedStyle(document.body);
    const lhStr = rootStyle.getPropertyValue('--reader-line-height') || bodyStyle.getPropertyValue('--reader-line-height');
    
    let lh = 1.8;
    if (lhStr) {
      if (lhStr.includes('%')) lh = parseFloat(lhStr) / 100;
      else lh = parseFloat(lhStr);
    }
    
    if (lh < 1.45) {
      document.body.classList.add('tight-highlight-mode');
    } else {
      document.body.classList.remove('tight-highlight-mode');
    }
  };

  checkTightMode();
  const observer = new MutationObserver(checkTightMode);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ['style'] });
  observer.observe(document.body, { attributes: true, attributeFilter: ['style'] });
}