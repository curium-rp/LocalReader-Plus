import { state, applyDocumentUiLang } from "./modules/state.js";
import { fetchJSON } from "./modules/api.js";
import {
  renderIcons,
  showToast,
  initToast,
  switchTab,
  renderRules,
  renderIgnoreList,
  updateEngineStatusUI,
  highlightSearchTerm,
  updateTranslations,
  toggleSettingsDrawer,
  closeAllDrawers,
  syncBackToReadingButton,
} from "./modules/ui.js";
import {
  loadLibrary,
  selectDocument,
  renderPage,
  processPdfBlob,
  processJsonData,
  getSentencesForPage,
  updateActiveTOC,
} from "./modules/library.js";
import {
  loadVoices,
  togglePlayback,
  stopPlayback,
  playNext,
  jumpToSentence,
  initAudioContext,
  saveProgress,
} from "./modules/tts.js";
import {
  startExport,
  cancelExport,
  startFFMPEGDownload,
  openExportLocation,
  closeExportModal,
} from "./modules/export.js";
import { initTimer } from "./modules/timer.js";
import { initWakeLock } from "./modules/wakelock.js";
import { initThemeSystem } from "./modules/themes.js";
import { initTopBar } from "./modules/topbar.js";
import { initProgress, jumpToDisplayedPage, updateProgressDisplay } from "./modules/progress.js";
import { initResizeBorders } from "./modules/resize.js";
import { initTypography, getRenderState, closeTypoMenu, applyReaderTypography } from "./modules/typography.js";
import { initLayoutHooks, requestGeometrySync } from "./modules/reader-layout.js";
import {
  isHorizontalMode,
  revealInSpread,
  setHorizontalPageTurn,
  layoutSpreads,
  getSpreadIndex,
} from "./modules/horizontal.js";
import { initShortcuts } from "./shortcuts.js";
import { initSearch, closeSearchMode, handleSearchPopupKeys } from "./search.js";

window.state = state;

async function init() {
  try {
    const [settings, rules, ignore] = await Promise.all([
      fetchJSON(`/api/settings`),
      fetchJSON(`/api/rules`).catch(() => null),
      fetchJSON(`/api/ignore`).catch(() => null),
    ]);
    // TODO(deprecate): [BACKWARD COMPATIBILITY] Fallback to settings.pronunciationRules if /api/rules returned null/empty. Safe to remove in ~6 months.
    const initialRules = (Array.isArray(rules) && rules.length > 0)
      ? rules
      : (settings?.pronunciationRules || rules || []);
    state.rules = initialRules.map((r) => ({
      ...r,
      id: r.id || crypto.randomUUID(),
    }));
    // TODO(deprecate): [BACKWARD COMPATIBILITY] Fallback to settings.ignoreList if /api/ignore returned null/empty. Safe to remove in ~6 months.
    state.ignoreList = (Array.isArray(ignore) && ignore.length > 0)
      ? ignore
      : (settings?.ignoreList || ignore || []);
    state.engineMode = settings.engine_mode || "gpu";
    state.pauseSettings = settings.pause_settings || state.pauseSettings || {
      comma: 0, period: 0, spam: 0, question: 600, exclamation: 600, colon: 400, semicolon: 400, newline: 0
    };
    if (state.pauseSettings.spam === undefined) state.pauseSettings.spam = 0;
    state.behaviorSettings = settings.behavior_settings || { H: 2000, Img: 3000, S: 1000, N: 500 };
    ['H', 'Img', 'S', 'N'].forEach(k => {
        const input = document.getElementById(`behavior${k}`);
        const val = document.getElementById(`behavior${k}Val`);
        if (input && val && state.behaviorSettings[k] !== undefined) {
            input.value = state.behaviorSettings[k];
            val.textContent = state.behaviorSettings[k];
        }
    });
    state.uiLanguage = settings.ui_language || "en";
    applyDocumentUiLang(state.uiLanguage);

    const validHideModes = ["always", "auto", "manual"];
    let themeData = {};
    try {
      themeData = await fetchJSON(`/api/theme`);
    } catch (e) {}
    let hideMode = themeData?.player_hide_mode;
    if (!validHideModes.includes(hideMode)) {
      if (settings.manualHidePlayer) hideMode = "manual";
      else if (settings.autoHidePlayer) hideMode = "auto";
      else hideMode = "always";
      try {
        await fetchJSON(`/api/theme`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ player_hide_mode: hideMode }),
        });
      } catch (e) {}
    }
    state.playerHideMode = hideMode;
    state.sentenceDim = themeData?.sentence_dim === true;
    updateSentenceBrightness();
    applyPlayerHideUi({ minimized: hideMode === "manual" });
    if (hideMode === "auto") resetAutoHideTimer();

    document.querySelectorAll("[data-hide-mode]").forEach((btn) => {
      btn.onclick = () => setPlayerHideMode(btn.dataset.hideMode);
    });
    const miniPlayBtn = document.getElementById("sidebarMiniPlayBtn");
    if (miniPlayBtn) miniPlayBtn.onclick = () => togglePlayback();

    const speedRange = document.getElementById("speedRange");
    if (speedRange && settings.speed) {
      speedRange.value = settings.speed;
      const sv = document.getElementById("speedVal");
      if (sv) sv.textContent = parseFloat(settings.speed).toFixed(2);
    }
    await initTypography(settings.font_size);
    const engineSelect = document.getElementById("engineMode");
    if (engineSelect) engineSelect.value = state.engineMode;

    [
      "comma",
      "period",
      "spam",
      "question",
      "exclamation",
      "colon",
      "semicolon",
    ].forEach((key) => {
      const input = document.getElementById(
        `pause${key.charAt(0).toUpperCase() + key.slice(1)}`,
      );
      const val = document.getElementById(
        `pause${key.charAt(0).toUpperCase() + key.slice(1)}Val`,
      );
      if (input && val && state.pauseSettings[key] !== undefined) {
        input.value = state.pauseSettings[key];
        val.textContent = state.pauseSettings[key];
      }
    });

    syncLanguageChip(state.uiLanguage);
    await updateTranslations(state.uiLanguage);
    applyPlayerHideUi();

    const voiceSelect = document.getElementById("voiceSelect");
    if (settings.voice_id) {
      state.voice = settings.voice_id;
      if (voiceSelect) {
        const opt = document.createElement("option");
        opt.value = settings.voice_id;
        opt.textContent = "Loading...";
        voiceSelect.appendChild(opt);
        voiceSelect.value = settings.voice_id;
      }
    }
  } catch (e) {
    console.error("Settings load error", e);
  }

  initLayoutHooks();

  renderIcons();
  await initThemeSystem();
  initTopBar();
  initProgress();
  initResizeBorders(); 

  try { await loadVoices(); } catch (e) { console.error(e); }
  try { await loadLibrary(); } catch (e) { console.error(e); }

  renderRules();
  renderIgnoreList();
  startStatusPolling();
  window.isJumpingCamera = false;

  initTimer();
  initToast();
  await initWakeLock();
  
  let imgViewer = document.getElementById("imageViewerModal");
  if (!imgViewer) {
    imgViewer = document.createElement("div");
    imgViewer.id = "imageViewerModal";
    imgViewer.innerHTML = `
      <button id="imageViewerClose">
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><line x1="18" y1="6" x2="6" y2="18"></line><line x1="6" y1="6" x2="18" y2="18"></line></svg>
      </button>
      <img id="imageViewerImg" src="" alt="Fullscreen Image" />
    `;
    document.body.appendChild(imgViewer);
    
    imgViewer.onclick = (e) => {
      if (e.target.id === "imageViewerModal" || e.target.closest("#imageViewerClose")) {
        imgViewer.classList.remove("active");
      }
    };
  }
}

document.addEventListener("DOMContentLoaded", () => {
  init();
  
  const scrollContainer = document.querySelector(".content-area");
  if (scrollContainer) {
    scrollContainer.addEventListener('load', (e) => {
      if (e.target && e.target.tagName === 'IMG') {
        const activeEl = document.querySelector('.active-sentence');
        if (activeEl && state.autoScrollEnabled) {
          requestAnimationFrame(() => {
            if (revealInSpread(activeEl)) {
              if (typeof updateActiveTOC === "function") updateActiveTOC();
              return;
            }
            const elRect = activeEl.getBoundingClientRect();
            const containerRect = scrollContainer.getBoundingClientRect();
            const relativeTop = elRect.top - containerRect.top + scrollContainer.scrollTop;
            const centerPosition = relativeTop - (containerRect.height / 2) + (elRect.height / 2);
            scrollContainer.scrollTop = Math.max(0, centerPosition);
            
            if (typeof updateActiveTOC === 'function') updateActiveTOC();
          });
        }
      }
    }, true);
  }
});

document.addEventListener("dblclick", (e) => {
    if (e.target && e.target.tagName && e.target.tagName.toLowerCase() === "img" && e.target.classList.contains("epub-image")) {
        if (e.target.closest("s")) return;
        const viewer = document.getElementById("imageViewerModal");
        const viewerImg = document.getElementById("imageViewerImg");
        if (viewer && viewerImg) {
            viewerImg.src = e.target.src;
            viewer.classList.add("active");
        }
    }
});

let mouseHideTimeout = null;
window.isJumpingCamera = false;

function isManualPlaybarHidden() {
  const controls = document.getElementById("controls");
  return state.playerHideMode === "manual" && controls && controls.classList.contains("minimized");
}

function isSettingsDrawerOpen() {
  return !!document.querySelector(".voice-settings-drawer.open");
}

function syncChromeFloatButtons(hidden) {
  document.querySelectorAll(".voice-settings-button").forEach((btn) => {
    btn.classList.toggle("chrome-hidden", hidden);
  });
}

function freezePlayerAutoHide() {
  clearTimeout(mouseHideTimeout);
  applyPlayerHideUi({ minimized: false });
}

function resumePlayerAutoHide() {
  if (isSettingsDrawerOpen()) return;
  if (state.playerHideMode === "auto") resetAutoHideTimer();
}

async function saveThemeSettings(partial) {
  try {
    await fetchJSON(`/api/theme`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(partial),
    });
  } catch (e) {
    console.error(e);
  }
}

async function savePlayerHideMode() {
  await saveThemeSettings({ player_hide_mode: state.playerHideMode });
}

function applyPlayerHideUi({ minimized = null } = {}) {
  const controls = document.getElementById("controls");
  const restoreBtn = document.getElementById("playbarRestoreBtn");
  const miniBtn = document.getElementById("sidebarMiniPlayBtn");
  if (!controls) return;

  if (minimized === true) controls.classList.add("minimized");
  else if (minimized === false) controls.classList.remove("minimized");

  const isMin = controls.classList.contains("minimized");
  const showRestore = state.playerHideMode === "manual" && isMin;
  syncChromeFloatButtons(isMin && !isSettingsDrawerOpen());

  if (restoreBtn) {
    restoreBtn.classList.toggle("opacity-100", showRestore);
    restoreBtn.classList.toggle("opacity-0", !showRestore);
    restoreBtn.classList.toggle("pointer-events-auto", showRestore);
    restoreBtn.classList.toggle("pointer-events-none", !showRestore);
  }
  if (miniBtn) {
    miniBtn.classList.toggle("hidden", !showRestore);
    if (showRestore) renderIcons();
  }

  document.querySelectorAll("[data-hide-mode]").forEach((btn) => {
    const on = btn.dataset.hideMode === state.playerHideMode;
    btn.classList.toggle("text-zinc-200", on);
    btn.classList.toggle("bg-zinc-700", on);
    btn.classList.toggle("text-zinc-500", !on);
  });
}

function setPlayerHideMode(mode) {
  if (!["always", "auto", "manual"].includes(mode)) return;
  state.playerHideMode = mode;
  clearTimeout(mouseHideTimeout);
  if (mode === "always") {
    applyPlayerHideUi({ minimized: false });
  } else if (mode === "auto") {
    applyPlayerHideUi({ minimized: false });
    resetAutoHideTimer();
  } else {
    applyPlayerHideUi({ minimized: true });
  }
  savePlayerHideMode();
}

function resetAutoHideTimer() {
    if (window.isJumpingCamera) return; // Shield against phantom mouse movements when scrolling
    if (isSettingsDrawerOpen()) return;

    const controls = document.getElementById("controls");
    if (!controls) return;

    if (state.playerHideMode === "always") return;
    if (state.playerHideMode === "manual" && controls.classList.contains("minimized")) return;
    if (state.playerHideMode !== "auto" && state.playerHideMode !== "manual") return;

    applyPlayerHideUi({ minimized: false });

    clearTimeout(mouseHideTimeout);
    mouseHideTimeout = setTimeout(() => {
        if (isSettingsDrawerOpen()) return;
        applyPlayerHideUi({ minimized: true });
    }, 3000);
}

const contentArea = document.querySelector(".content-area");
if (contentArea) contentArea.addEventListener("mousemove", resetAutoHideTimer);

const controlsArea = document.getElementById("controls");
if (controlsArea) controlsArea.addEventListener("mousemove", resetAutoHideTimer);

// --- THE PLAY BUTTON ---
document.getElementById("playBtn").onclick = togglePlayback;

document.querySelectorAll(".voice-settings-button").forEach((btn) => {
  btn.addEventListener("mousemove", resetAutoHideTimer);
  btn.addEventListener("mouseenter", resetAutoHideTimer);
});

document.addEventListener("settings-drawer-change", (e) => {
  if (e.detail?.open) freezePlayerAutoHide();
  else resumePlayerAutoHide();
});

document.getElementById("playbarRestoreBtn").onclick = () => {
    setPlayerHideMode("always");
    syncBackToReadingButton();
};

// --- THE UNIFIED BACK TO READING ENGINE ---
const performSafeJump = async () => {
    window.isJumpingCamera = true; // Lock the mouse tracker globally

    state.viewPageIndex = state.readingPageIndex;
    state.autoScrollEnabled = true;

    syncBackToReadingButton();

    // Notice we do NOT change playerHideMode. If it is hidden, it stays hidden!
    await renderPage();

    if (isHorizontalMode()) {
        const activeEl = document.querySelector("#textContent .active-sentence");
        if (activeEl) revealInSpread(activeEl);
    }

    // Release the mouse tracker lock 500ms after the jump finishes
    setTimeout(() => { window.isJumpingCamera = false; }, 500);
};

// Wire both buttons to the exact same safe jump logic
document.getElementById("backToReadingBtn").onclick = performSafeJump;
const hiddenModeBtn = document.getElementById("hiddenModeBackBtn");
if (hiddenModeBtn) hiddenModeBtn.onclick = performSafeJump;


// --- NAVIGATION & KEYBOARD LOGIC ---
document.getElementById("skipBack").onclick = async () => {
  if (state.currentSentenceIndex > 0) {
    safeJumpToSentence(state.currentSentenceIndex - 1);
  } else {
    let targetPage = state.readingPageIndex - 1;
    let foundSentences = [];
    while (targetPage >= 0) {
        foundSentences = await getSentencesForPage(targetPage);
        if (foundSentences && foundSentences.length > 0) break;
        targetPage--; 
    }
    if (targetPage >= 0 && foundSentences.length > 0) {
        state.readingPageIndex = targetPage;
        state.viewPageIndex = targetPage; 
        state.readingSentences = foundSentences;
        state.autoScrollEnabled = true;
        await renderPage();
        safeJumpToSentence(foundSentences.length - 1);
    }
  }
};

document.getElementById("skipForward").onclick = async () => {
  if (state.currentSentenceIndex < state.readingSentences.length - 1) {
    safeJumpToSentence(state.currentSentenceIndex + 1);
  } else {
    let targetPage = state.readingPageIndex + 1;
    let foundSentences = [];
    while (targetPage < state.currentPages.length) {
        foundSentences = await getSentencesForPage(targetPage);
        if (foundSentences && foundSentences.length > 0) break;
        targetPage++; 
    }
    if (targetPage < state.currentPages.length && foundSentences.length > 0) {
        state.readingPageIndex = targetPage;
        state.viewPageIndex = targetPage; 
        state.readingSentences = foundSentences;
        state.autoScrollEnabled = true;
        await renderPage();
        safeJumpToSentence(0);
    }
  }
};

initShortcuts({ closeSearchMode, handleSearchPopupKeys, closeSidebarMiniPopups });

document.getElementById("prevPage").onclick = async () => {
  if (state.viewPageIndex > 0) {
    state.viewPageIndex--;
    state.autoScrollEnabled = (state.viewPageIndex === state.readingPageIndex);
    await renderPage();
  }
};
document.getElementById("nextPage").onclick = async () => {
  if (state.viewPageIndex < state.currentPages.length - 1) {
    state.viewPageIndex++;
    state.autoScrollEnabled = (state.viewPageIndex === state.readingPageIndex);
    await renderPage();
  }
};

setHorizontalPageTurn(async (dir) => {
  if (dir < 0) {
    if (state.viewPageIndex > 0) {
      state.viewPageIndex--;
      state.autoScrollEnabled = (state.viewPageIndex === state.readingPageIndex);
      await renderPage();
      return true;
    }
    return false;
  }
  if (state.viewPageIndex < state.currentPages.length - 1) {
    state.viewPageIndex++;
    state.autoScrollEnabled = (state.viewPageIndex === state.readingPageIndex);
    await renderPage();
    return true;
  }
  return false;
});
document.getElementById("pageInput").onchange = async (e) => {
  const v = parseInt(e.target.value, 10);
  if (!Number.isFinite(v)) return;
  if (jumpToDisplayedPage(v)) {
    state.autoScrollEnabled = (state.viewPageIndex === state.readingPageIndex);
    await renderPage();
  } else {
    updateProgressDisplay();
  }
};

// --- THE SMART SCROLL TRACKER ---
let isAutoFlipping = false;
let tocScrollTimer = null;
const appScroller = document.querySelector(".content-area");
if (appScroller) {
  appScroller.addEventListener("scroll", () => {
      const tocModal = document.getElementById("tocModal");
      if (tocModal && !tocModal.classList.contains("hidden")) {
          clearTimeout(tocScrollTimer);
          tocScrollTimer = setTimeout(updateActiveTOC, 80);
      }
  }, { passive: true });

  const readerScrollMetrics = () => {
    const inner = document.getElementById("readerContent");
    const candidates = [appScroller, inner].filter(Boolean);
    let scroller = appScroller;
    let maxOverflow = -1;
    for (const el of candidates) {
      const overflow = el.scrollHeight - el.clientHeight;
      if (overflow > maxOverflow) {
        maxOverflow = overflow;
        scroller = el;
      }
    }
    const maxScroll = Math.max(0, scroller.scrollHeight - scroller.clientHeight);
    return {
      scroller,
      maxScroll,
      canScroll: maxScroll > 10,
      top: scroller.scrollTop <= 10,
      bottom: scroller.scrollTop >= maxScroll - 10,
    };
  };

  const pageImagesStillExpanding = () => Array.from(
    document.querySelectorAll("#textContent img.epub-image:not(.epub-icon)")
  ).some(img => !img.complete || img.naturalHeight === 0);

  appScroller.addEventListener(
    "wheel",
    async (e) => {
      if (isHorizontalMode()) return;
      if (isAutoFlipping) return;
      const metrics = readerScrollMetrics();

      // When the user breaks auto-scroll, spawn the correct button
      if (state.autoScrollEnabled) {
        state.autoScrollEnabled = false;
        syncBackToReadingButton();
      }

      // Short page with unloaded mixed images: wait for layout, don't skip HTML pages.
      if (!metrics.canScroll && pageImagesStillExpanding()) return;

      if (e.deltaY > 0 && metrics.bottom && state.viewPageIndex < state.currentPages.length - 1) {
        isAutoFlipping = true;
        state.viewPageIndex++;
        await renderPage();
        readerScrollMetrics().scroller.scrollTop = 0;
        setTimeout(() => { isAutoFlipping = false; }, 700);
      } else if (e.deltaY < 0 && metrics.top && state.viewPageIndex > 0) {
        isAutoFlipping = true;
        state.viewPageIndex--;
        await renderPage();
        const after = readerScrollMetrics();
        after.scroller.scrollTop = after.scroller.scrollHeight;
        setTimeout(() => { isAutoFlipping = false; }, 700);
      }
    },
    { passive: true },
  );
}
// --- SAFELY MOUNT UPLOAD HANDLER ---
const pdfUpload = document.getElementById("pdfUpload");
if (pdfUpload) {
  pdfUpload.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    
    if (file.name.toLowerCase().endsWith(".epub")) {
      showToast("Parsing EPUB...");
      const docId = crypto.randomUUID();
      const formData = new FormData();
      formData.append("file", file);

      try {
        const res = await fetch(`/api/convert/epub?id=${docId}`, {
          method: "POST",
          body: formData,
        });
        if (!res.ok) throw new Error("Conversion failed");
        const data = await res.json();
        
        // Pass everything safely
        processJsonData(data.pages, file.name.replace(/\.epub$/i, ""), docId, data.image_map, data.toc_map, data.language, data.bookType || "epub");
      } catch (err) {
        console.error(err);
        showToast("EPUB conversion failed: " + err.message);
      }
    } else {
      processPdfBlob(file, file.name);
    }
    e.target.value = "";
  };
}

document.getElementById("tabLibrary").onclick = () =>
  switchTab(
    document.getElementById("tabLibrary"),
    document.getElementById("libraryPanel"),
  );
document.getElementById("tabRules").onclick = () =>
  switchTab(
    document.getElementById("tabRules"),
    document.getElementById("rulesPanel"),
  );
document.getElementById("tabIgnore").onclick = () =>
  switchTab(
    document.getElementById("tabIgnore"),
    document.getElementById("ignorePanel"),
  );

async function saveSettings() {
  try {
    await fetchJSON(`/api/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        voice_id: document.getElementById("voiceSelect").value,
        speed: parseFloat(document.getElementById("speedRange").value),
        font_size: getRenderState().font_size,
        engine_mode: state.engineMode,
        pause_settings: state.pauseSettings,
        behavior_settings: state.behaviorSettings,
        ui_language: state.uiLanguage,
      }),
    });
  } catch (e) {
    console.error(e);
  }
}

let saveRulesTimeout = null;
async function saveRules(immediate = false) {
  if (saveRulesTimeout) clearTimeout(saveRulesTimeout);
  const doSave = async () => {
    try {
      // Strip transient UI state (e.g. isExpanded) and ensure predictable format
      const payload = state.rules.map((r) => ({
        id: r.id,
        original: r.original || "",
        replacement: r.replacement || "",
        match_case: Boolean(r.match_case),
        word_boundary: r.word_boundary !== false,
        is_regex: Boolean(r.is_regex),
      }));
      await fetchJSON(`/api/rules`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
    } catch (e) {
      console.error("Failed to save pronunciation rules:", e);
    }
  };

  if (immediate) {
    await doSave();
  } else {
    saveRulesTimeout = setTimeout(doSave, 250);
  }
}

let saveIgnoreTimeout = null;
async function saveIgnore(immediate = false) {
  if (saveIgnoreTimeout) clearTimeout(saveIgnoreTimeout);
  const doSave = async () => {
    try {
      await fetchJSON(`/api/ignore`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(state.ignoreList),
      });
    } catch (e) {
      console.error("Failed to save ignore list:", e);
    }
  };

  if (immediate) {
    await doSave();
  } else {
    saveIgnoreTimeout = setTimeout(doSave, 250);
  }
}

document.getElementById("speedRange").onchange = saveSettings;
document.getElementById("speedRange").oninput = (e) =>
  (document.getElementById("speedVal").textContent = parseFloat(
    e.target.value,
  ).toFixed(2));
document.getElementById("voiceSelect").onchange = async () => {
  state.audioBufferCache.clear();
  try {
    await fetchJSON("/api/system/clear-cache", { method: "POST" });
  } catch (e) {
    console.error("Failed to clear backend cache", e);
  }
  await saveSettings();
};
document.getElementById("engineMode").onchange = async (e) => {
  state.engineMode = e.target.value;
  await saveSettings();
};
document.getElementById("setupBtn").onclick = async (e) => {
  e.preventDefault();
  e.stopPropagation();
  const popup = document.getElementById("kokoroDownloadPopup");
  if (!popup) return;
  closeSidebarMiniPopups();
  popup.classList.toggle("hidden");
};

document.getElementById("voiceSettingsBtn").onclick = () => toggleSettingsDrawer("voiceSettingsDrawer", true);
document.getElementById("closeDrawerBtn").onclick = () => toggleSettingsDrawer("voiceSettingsDrawer", false);

document.getElementById("behaviorSettingsBtn").onclick = () => toggleSettingsDrawer("behaviorSettingsDrawer", true);
document.getElementById("closeBehaviorDrawerBtn").onclick = () => toggleSettingsDrawer("behaviorSettingsDrawer", false);

document.getElementById("drawerOverlay").onclick = () => closeAllDrawers();

const sidebar = document.querySelector(".sidebar");

const sidebarCollapseBtn = document.getElementById("sidebarCollapseBtn");
if (sidebarCollapseBtn && sidebar) {
  let pendingSpreadIndex = null;
  let sidebarAnimTimer = null;
  const updateSidebarVar = (collapsed) => {
    document.documentElement.style.setProperty(
      "--sidebar-width",
      collapsed ? "0px" : sidebar.style.width || "320px",
    );
  };
  const finishSidebarAnim = () => {
    if (!sidebar.classList.contains("animating")) return;
    clearTimeout(sidebarAnimTimer);
    sidebar.classList.remove("animating");
    applyReaderTypography();
    const k = pendingSpreadIndex;
    pendingSpreadIndex = null;
    void layoutSpreads({ spreadIndex: k });
  };
  const beginSidebarWidthAnim = () => {
    pendingSpreadIndex = getSpreadIndex();
    sidebar.classList.add("animating");
    clearTimeout(sidebarAnimTimer);
    sidebarAnimTimer = setTimeout(finishSidebarAnim, 350);
  };
  sidebar.addEventListener("transitionend", (e) => {
    if (e.target !== sidebar) return;
    if (e.propertyName !== "width") return;
    applyReaderTypography();
    requestGeometrySync();
    finishSidebarAnim();
  });
  sidebarCollapseBtn.onclick = () => {
    const collapsing = !sidebar.classList.contains("collapsed");
    closeSidebarMiniPopups();
    beginSidebarWidthAnim();
    sidebar.classList.toggle("collapsed", collapsing);
    updateSidebarVar(collapsing);
    applyReaderTypography();
    requestGeometrySync();
    if (collapsing) closeTypoMenu();
  };
}

let dragCounter = 0;
const dropOverlay = document.getElementById("dropOverlay");
document.body.addEventListener("dragenter", (e) => {
  e.preventDefault();
  dragCounter++;
  if (dropOverlay) dropOverlay.classList.remove("hidden");
});
document.body.addEventListener("dragleave", (e) => {
  e.preventDefault();
  dragCounter--;
  if (dragCounter <= 0) {
    dragCounter = 0;
    if (dropOverlay) dropOverlay.classList.add("hidden");
  }
});
document.body.addEventListener("dragover", (e) => e.preventDefault());
document.body.addEventListener("drop", async (e) => {
  e.preventDefault();
  dragCounter = 0;
  if (dropOverlay) dropOverlay.classList.add("hidden");
  
  const file = e.dataTransfer.files[0];
  if (!file) return;
  
  const name = file.name.toLowerCase();
  if (!name.endsWith(".pdf") && !name.endsWith(".epub")) {
    showToast("Please drop a PDF or EPUB file.");
    return;
  }
  
  if (name.endsWith(".epub")) {
    showToast("Parsing EPUB...");
    const docId = crypto.randomUUID(); // Added missing docId to fix conversion crash
    const formData = new FormData();
    formData.append("file", file);
    
    try {
      const res = await fetch(`/api/convert/epub?id=${docId}`, { // Added ?id parameter
        method: "POST",
        body: formData,
      });
      if (!res.ok) throw new Error("Conversion failed");
      const data = await res.json();
      
      processJsonData(data.pages, file.name.replace(/\.epub$/i, ""), docId, data.image_map, data.toc_map, data.language, data.bookType || "epub");
    } catch (err) {
      console.error(err);
      showToast("EPUB conversion failed: " + err.message);
    }
  } else {
    processPdfBlob(file, file.name);
  }
});

function closeSidebarMiniPopups() {
  document.getElementById("languagePopup")?.classList.add("hidden");
  document.getElementById("engineStatusPopup")?.classList.add("hidden");
}

function syncLanguageChip(lang) {
  const langToggle = document.getElementById("languageToggle");
  if (langToggle) langToggle.textContent = (lang || "en").toUpperCase();
  document.querySelectorAll("#languagePopup [data-lang]").forEach((btn) => {
    btn.classList.toggle("is-active", btn.dataset.lang === lang);
  });
}

async function applyUiLanguage(next) {
  if (!next || next === state.uiLanguage) {
    closeSidebarMiniPopups();
    if (next) syncLanguageChip(next);
    return;
  }
  state.uiLanguage = next;
  syncLanguageChip(next);
  closeSidebarMiniPopups();
  await updateTranslations(next);
  applyReaderTypography();
  applyPlayerHideUi();
  renderIcons();
  saveSettings();
  loadVoices();
  showToast(`Language set to ${next.toUpperCase()}`);
}

function toggleSidebarMiniPopup(popup) {
  if (!popup) return;
  const willOpen = popup.classList.contains("hidden");
  closeSidebarMiniPopups();
  if (willOpen) popup.classList.remove("hidden");
}

const languageToggle = document.getElementById("languageToggle");
const languagePopup = document.getElementById("languagePopup");
if (languageToggle && languagePopup) {
  languageToggle.onclick = (e) => {
    e.stopPropagation();
    toggleSidebarMiniPopup(languagePopup);
  };
  languagePopup.querySelectorAll("[data-lang]").forEach((btn) => {
    btn.onclick = (e) => {
      e.stopPropagation();
      applyUiLanguage(btn.dataset.lang);
    };
  });
}

const engineStatusBtn = document.getElementById("engineStatusBtn");
const engineStatusPopup = document.getElementById("engineStatusPopup");
if (engineStatusBtn && engineStatusPopup) {
  engineStatusBtn.onclick = (e) => {
    e.stopPropagation();
    toggleSidebarMiniPopup(engineStatusPopup);
  };
}

document.addEventListener("click", (e) => {
  if (e.target.closest(".sidebar-tray-wrap, #setupArea, #engineStatusPopup, #kokoroDownloadPopup")) return;
  closeSidebarMiniPopups();
});

initSearch();

document.getElementById("exportBtn").onclick = startExport;
document.getElementById("cancelExportBtn").onclick = cancelExport;
document.getElementById("startFFMPEGDownload").onclick = startFFMPEGDownload;
document.getElementById("cancelFFMPEGBtn").onclick = () =>
  document.getElementById("ffmpegModal").classList.add("hidden");
document.getElementById("openFileLocationBtn").onclick = openExportLocation;
const closeExportModalBtn = document.getElementById("closeExportModalBtn");
if (closeExportModalBtn) closeExportModalBtn.onclick = closeExportModal;
const closeExportErrorBtn = document.getElementById("closeExportErrorBtn");
if (closeExportErrorBtn) closeExportErrorBtn.onclick = closeExportModal;

document.getElementById("rulesList").addEventListener("input", (e) => {
  if (e.target.dataset.action === "update-rule") {
    const id = e.target.dataset.id,
      field = e.target.dataset.field,
      val = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    state.rules = state.rules.map((r) =>
      r.id === id ? { ...r, [field]: val } : r,
    );
    saveRules(false);
  }
});
document.getElementById("rulesList").addEventListener("click", (e) => {
  const t = e.target.closest("[data-action]");
  if (!t) return;
  const action = t.dataset.action,
    id = t.dataset.id;
  if (action === "toggle-rule") {
    state.rules = state.rules.map((r) =>
      r.id === id ? { ...r, isExpanded: !r.isExpanded } : r,
    );
    renderRules();
  } else if (action === "delete-rule") {
    state.rules = state.rules.filter((r) => r.id !== id);
    renderRules();
    saveRules(true);
  }
});
document.getElementById("addRuleBtn").onclick = () => {
  state.rules.push({
    id: crypto.randomUUID(),
    original: "",
    replacement: "",
    match_case: false,
    word_boundary: true,
    is_regex: false,
    isExpanded: true,
  });
  renderRules();
  saveRules(true);
};

document.getElementById("addIgnoreBtn").onclick = () => {
  state.ignoreList.push("");
  renderIgnoreList();
  saveIgnore(true);
};
document.getElementById("ignoreListUI").addEventListener("input", (e) => {
  if (e.target.dataset.action === "update-ignore") {
    state.ignoreList[parseInt(e.target.dataset.index)] = e.target.value;
    saveIgnore(false);
  }
});
document.getElementById("ignoreListUI").addEventListener("change", (e) => {
  if (e.target.dataset.action === "update-ignore") {
    state.ignoreList[parseInt(e.target.dataset.index)] = e.target.value;
    saveIgnore(true);
  }
});
document.getElementById("ignoreListUI").addEventListener("click", (e) => {
  const t = e.target.closest('[data-action="delete-ignore"]');
  if (t) {
    state.ignoreList.splice(parseInt(t.dataset.index), 1);
    renderIgnoreList();
    saveIgnore(true);
  }
});

document.getElementById("libraryPanel").addEventListener("click", async (e) => {
  const st = e.target.closest('[data-action="select-doc"]');
  if (st) {
    selectDocById(st.dataset.id);
    return;
  }
  
  const dt = e.target.closest('[data-action="delete-doc"]');
  if (dt) {
    if (dt.disabled) return; // 🌟 CONCURRENCY SHIELD: Block double-clicks
    
    if (confirm("Delete?")) {
      dt.disabled = true;
      dt.classList.add("opacity-50", "pointer-events-none");
      
      try {
        await fetchJSON(`/api/library/${dt.dataset.id}`, { method: "DELETE" });
        if (state.currentDoc?.id === dt.dataset.id) {
            location.reload();
        } else {
            await loadLibrary();
        }
      } catch (err) {
        console.error("Deletion failed:", err);
        dt.disabled = false;
        dt.classList.remove("opacity-50", "pointer-events-none");
      }
    }
  }
});

window.selectDocById = async (id) => {
  if (state.isPlaying) stopPlayback();
  const items = await fetchJSON(`/api/library`);
  const item = items.find((i) => i.id === id);
  if (item) selectDocument(item);
};

["Comma", "Period", "Spam", "Question", "Exclamation", "Colon", "Semicolon"].forEach(
  (k) => {
    const el = document.getElementById(`pause${k}`);
    if (el) {
      el.oninput = (e) => {
        if (!state.pauseSettings) state.pauseSettings = {};
        state.pauseSettings[k.toLowerCase()] = parseInt(e.target.value);
        const valEl = document.getElementById(`pause${k}Val`);
        if (valEl) valEl.textContent = e.target.value;
      };
      el.onchange = () => {
          state.audioBufferCache.clear();
          saveSettings();
      };
    }
  },
);

['H', 'Img', 'S', 'N'].forEach(k => {
    const el = document.getElementById(`behavior${k}`);
    if (el) {
        el.oninput = (e) => {
            if (!state.behaviorSettings) state.behaviorSettings = {};
            state.behaviorSettings[k] = parseInt(e.target.value);
            const valEl = document.getElementById(`behavior${k}Val`);
            if (valEl) valEl.textContent = e.target.value;
        };
        el.onchange = () => {
            // 🌟 SETTING SYNC FIX: Instantly clear frontend cache so new structural pauses apply
            state.audioBufferCache.clear();
            saveSettings();
        }; 
    }
});

const pauseToggleBtn = document.getElementById("pauseSettingsToggle");
if (pauseToggleBtn) {
  pauseToggleBtn.onclick = () => {
    const content = document.getElementById("pauseSettingsContent");
    if (content) content.classList.toggle("hidden");
  };
}

function safeJumpToSentence(index) {
    state.autoScrollEnabled = true;
    const wasPlaying = state.isPlaying;
    if (wasPlaying) {
        stopPlayback();
        // Restore playing state flag so the next sentence handles autoplay instantly
        state.isPlaying = true; 
    }
    jumpToSentence(index);
}

let isJumpingLock = false;

window.addEventListener("jump-to-sentence", (e) => {
    // 1. Block duplicate ghost clicks instantly
    if (isJumpingLock) return;
    // 2. Prevent jumping if the user is just highlighting text to copy
    const selection = window.getSelection();
    if (selection && selection.toString().trim().length > 0) {
        return; 
    }
    // 3. Engage the lock and perform the jump
    isJumpingLock = true;
    safeJumpToSentence(e.detail);
    
    // 🌟 FIX: Sync the TOC indicator with the new CSS image size layout shift!
    setTimeout(() => {
        if (typeof updateActiveTOC === 'function') updateActiveTOC();
    }, 400); // Wait for the 0.4s CSS transition to finish expanding the image
    
    // 4. Release the lock after 300ms (enough time to eat browser event bubbling)
    setTimeout(() => {
        isJumpingLock = false;
    }, 300);
});

let lastSysState = null;
async function startStatusPolling() {
  const poll = async () => {
    try {
      const status = await fetchJSON(`/api/system/status?t=${Date.now()}`);
      window.isEngineReady = status.model_loaded;
      
      if (status.engine_mode) {
        state.engineMode = status.engine_mode.toLowerCase();
      }
      if (status.active_hardware) {
        state.activeHardware = status.active_hardware.toLowerCase();
      }
      
      const selModel = state.engineMode === "gpu" ? status.available_models?.gpu : status.available_models?.cpu;
      
      const curState = `${status.is_downloading}-${status.is_loading}-${status.model_loaded}-${selModel}-${status.available_models?.gpu}-${status.available_models?.cpu}-${status.available_models?.voices}-${state.engineMode}-${state.activeHardware}`;
      if (curState !== lastSysState) {
        lastSysState = curState;
        updateEngineStatusUI(status, selModel);
        if (status.model_loaded) loadVoices();
      }
    } catch (e) {}
    setTimeout(poll, 2000);
  };
  poll();
}

// SAFE TOC BUTTON WIRING
const tocBtn = document.getElementById("tocBtn");
if (tocBtn) {
    tocBtn.onclick = () => {
        if (!state.currentDoc) {
            showToast("No document loaded");
            return;
        }
        const modal = document.getElementById("tocModal");
        if (modal) {
            modal.classList.remove("hidden");
            updateActiveTOC();
            requestAnimationFrame(() => {
                setTimeout(() => {
                    const active = document.querySelector('#tocList > .border-blue-500');
                    if (active) active.scrollIntoView({ block: 'center', behavior: 'smooth' });
                }, 50);
            });
        }
    };
}

const closeTocBtn = document.getElementById("closeTocBtn");
if (closeTocBtn) {
    closeTocBtn.onclick = () => {
        const modal = document.getElementById("tocModal");
        if (modal) modal.classList.add("hidden");
    };
}

// 🌟 NEW INTERCEPTOR: Click outside empty space on backdrop to hide TOC Drawer
const tocModalElement = document.getElementById("tocModal");
if (tocModalElement) {
    tocModalElement.onclick = (e) => {
        if (e.target === e.currentTarget) {
            tocModalElement.classList.add("hidden");
        }
    };
}

// --- Current Sentence Brightness Toggle ---
const brightnessBtn = document.getElementById("toggleBrightnessBtn");
if (brightnessBtn) {
    brightnessBtn.onclick = () => {
        state.sentenceDim = !state.sentenceDim;
        updateSentenceBrightness();
        saveThemeSettings({ sentence_dim: state.sentenceDim });
    };
}

function updateSentenceBrightness() {
  const preview = document.getElementById("currentSentencePreview");
  const btn = document.getElementById("toggleBrightnessBtn");

  if (!preview || !btn) return;

  if (state.sentenceDim) {
    preview.classList.remove("text-zinc-100");
    preview.classList.add("text-zinc-400", "opacity-60");
  } else {
    preview.classList.remove("text-zinc-400", "opacity-60");
    preview.classList.add("text-zinc-100");
  }

  while (btn.firstChild) {
    btn.removeChild(btn.firstChild);
  }

  const newIcon = document.createElement("i");
  if (state.sentenceDim) {
    newIcon.setAttribute("data-lucide", "moon");
    newIcon.className = "w-4 h-4 text-zinc-500";
  } else {
    newIcon.setAttribute("data-lucide", "sun");
    newIcon.className = "w-4 h-4 text-zinc-400";
  }

  btn.appendChild(newIcon);
  if (typeof renderIcons === "function") renderIcons();
}