import { state, applyDocumentUiLang } from "./modules/state.js";
import { fetchJSON } from "./modules/api.js";
import {
  renderIcons,
  showToast,
  initToast,
  switchTab,
  renderRules,
  renderIgnoreList,
  highlightSearchTerm,
  updateTranslations,
  toggleSettingsDrawer,
  closeAllDrawers,
  syncBackToReadingButton,
} from "./modules/ui.js";
import {
  updateEngineStatusUI,
  openModelManagerModal,
  closeModelManagerModal,
  initDownloader,
} from "./modules/downloader.js";
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
import { mountFilesUI, openFilesUI } from "./modules/files_UI.js";
import { mountHistoryUI } from "./modules/history.js";
import {
  isHorizontalMode,
  revealInSpread,
  setHorizontalPageTurn,
  layoutSpreads,
  getSpreadIndex,
  getNextSpreadPageIndex,
  getPrevSpreadPageIndex,
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
  mountFilesUI();
  mountHistoryUI();

  window.loadVoices = loadVoices;
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
  const prevIdx = getPrevSpreadPageIndex(state.viewPageIndex);
  if (prevIdx !== state.viewPageIndex && prevIdx >= 0) {
    state.viewPageIndex = prevIdx;
    state.autoScrollEnabled = (state.viewPageIndex === state.readingPageIndex);
    await renderPage();
    saveProgress(true);
  }
};
document.getElementById("nextPage").onclick = async () => {
  const nextIdx = getNextSpreadPageIndex(state.viewPageIndex);
  if (nextIdx !== state.viewPageIndex && nextIdx < state.currentPages.length) {
    state.viewPageIndex = nextIdx;
    state.autoScrollEnabled = (state.viewPageIndex === state.readingPageIndex);
    await renderPage();
    saveProgress(true);
  }
};

setHorizontalPageTurn(async (dir) => {
  if (dir < 0) {
    const prevIdx = getPrevSpreadPageIndex(state.viewPageIndex);
    if (prevIdx !== state.viewPageIndex && prevIdx >= 0) {
      state.viewPageIndex = prevIdx;
      state.autoScrollEnabled = (state.viewPageIndex === state.readingPageIndex);
      await renderPage();
      saveProgress(true);
      return true;
    }
    return false;
  }
  const nextIdx = getNextSpreadPageIndex(state.viewPageIndex);
  if (nextIdx !== state.viewPageIndex && nextIdx < state.currentPages.length) {
    state.viewPageIndex = nextIdx;
    state.autoScrollEnabled = (state.viewPageIndex === state.readingPageIndex);
    await renderPage();
    saveProgress(true);
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
    saveProgress(true);
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
// --- NATIVE OLE DROP HANDLERS (native_shell.dll) ---
window.__lrOnNativeEpubDrop = async function(book) {
  try {
    const dropOverlay = document.getElementById("dropOverlay");
    if (dropOverlay) dropOverlay.classList.add("hidden");
    if (typeof window.closeFilesUI === "function") window.closeFilesUI();
    if (typeof closeAllDrawers === "function") closeAllDrawers();
    if (typeof stopPlayback === "function") stopPlayback();

    showToast("Opening " + (book.title || book.fileName || "book") + "...", "info", 1200);
    await loadLibrary();
    document.dispatchEvent(new CustomEvent("lr-library-change"));
    await selectDocument(book);
    showToast("Book opened", "success", 1200);
  } catch (err) {
    console.error("[NATIVE DROP] Failed to open EPUB:", err);
    showToast("Failed to open book: " + err.message, "error");
  }
};

window.__lrOnNativePdfDrop = async function(filePath) {
  try {
    const dropOverlay = document.getElementById("dropOverlay");
    if (dropOverlay) dropOverlay.classList.add("hidden");
    if (typeof window.closeFilesUI === "function") window.closeFilesUI();
    if (typeof closeAllDrawers === "function") closeAllDrawers();
    if (typeof stopPlayback === "function") stopPlayback();

    const fileName = filePath.split(/[/\\]/).pop();
    showToast("Loading " + fileName + "...", "info", 1200);
    const res = await fetch(`/api/explorer/read-file?path=${encodeURIComponent(filePath)}`);
    if (!res.ok) throw new Error("Could not read dropped PDF from disk");
    const blob = await res.blob();
    processPdfBlob(blob, fileName);
  } catch (err) {
    console.error("[NATIVE DROP] Failed to process PDF:", err);
    showToast("Failed to process PDF: " + err.message, "error");
  }
};

window.__lrProcessPendingNativeDrops = async function() {
  if (!window.__lrPendingNativeDrops || window.__lrPendingNativeDrops.length === 0) return;
  const queue = window.__lrPendingNativeDrops.splice(0, window.__lrPendingNativeDrops.length);
  for (const item of queue) {
    if (item.type === "epub" && item.book) {
      await window.__lrOnNativeEpubDrop(item.book);
    } else if (item.type === "pdf" && item.filePath) {
      await window.__lrOnNativePdfDrop(item.filePath);
    }
  }
};

// Process any pending native drops queued before app.js finished loading
window.__lrProcessPendingNativeDrops();

async function openEpubByPathOrFallback(file) {
  const filePath = file.path || (window.webUtils && window.webUtils.getPathForFile ? window.webUtils.getPathForFile(file) : null);
  if (!filePath) {
    // In native PyWebView on Windows, native_shell.dll catches the drop via OLE IDropTarget.
    // If we reach here, it means the drop was handled by HTML5 without a native path.
    showToast("App cannot read path from browser drop. Please use 'Open Files' instead.", "warning", 3500);
    return;
  }

  showToast("Binding EPUB to shelf...", "info", 1200);
  try {
    const res = await fetchJSON("/api/explorer/add-to-shelf", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ epub_path: filePath }),
    });

    if (res && res.book) {
      await loadLibrary();
      document.dispatchEvent(new CustomEvent("lr-library-change"));
      await selectDocument(res.book);
      showToast("Book opened", "success", 1200);
    }
  } catch (err) {
    console.error("[EPUB] Peek binding failed:", err);
    showToast("Failed to open EPUB: " + err.message, "error");
  }
}


// --- SAFELY MOUNT UPLOAD HANDLER ---
const pdfUpload = document.getElementById("pdfUpload");
if (pdfUpload) {
  pdfUpload.onchange = async (e) => {
    const file = e.target.files[0];
    if (!file) return;
    
    if (file.name.toLowerCase().endsWith(".epub")) {
      await openEpubByPathOrFallback(file);
    } else {
      processPdfBlob(file, file.name);
    }
    e.target.value = "";
  };
}

// --- Empty state welcome card buttons ---
document.getElementById("emptyStateOpenEpubBtn")?.addEventListener("click", () => {
  if (typeof window.openFilesExplorer === "function") window.openFilesExplorer();
});
document.getElementById("emptyStateHistoryBtn")?.addEventListener("click", () => {
  if (typeof window.openHistoryUI === "function") window.openHistoryUI();
});

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

const speedRange = document.getElementById("speedRange");
const speedVal = document.getElementById("speedVal");
const speedInput = document.getElementById("speedInput");
const speedValBtn = document.getElementById("speedValBtn");
const speedStepUp = document.getElementById("speedStepUp");
const speedStepDown = document.getElementById("speedStepDown");
const speedStepperBox = document.getElementById("speedStepperBox");

const updateSpeedDisplay = (val) => {
  const str = Number(val).toFixed(2);
  if (speedRange) speedRange.value = str;
  if (speedVal) speedVal.textContent = str;
  if (speedInput) speedInput.value = str;
};

if (speedRange) {
  speedRange.step = "any";
  speedRange.oninput = (e) => {
    const stepped = (Math.round(parseFloat(e.target.value) / 0.05) * 0.05).toFixed(2);
    if (speedVal) speedVal.textContent = stepped;
    if (speedInput) speedInput.value = stepped;
  };
  speedRange.onchange = (e) => {
    const stepped = (Math.round(parseFloat(e.target.value) / 0.05) * 0.05).toFixed(2);
    e.target.value = stepped;
    updateSpeedDisplay(stepped);
    state.audioBufferCache.clear();
    saveSettings();
  };
}

const adjustSpeed = (delta) => {
  let current = parseFloat(speedRange?.value || speedVal?.textContent || 1.0);
  if (isNaN(current)) current = 1.0;
  let next = Math.round((current + delta) * 100) / 100;
  if (next < 0.5) next = 0.5;
  if (next > 3.0) next = 3.0;
  updateSpeedDisplay(next);
};

if ((speedValBtn || speedVal) && speedInput && speedRange) {
  const valDisplayTrigger = speedValBtn || speedVal;
  valDisplayTrigger.onclick = () => {
    speedInput.value = parseFloat(speedRange.value || 1.0).toFixed(2);
    valDisplayTrigger.classList.add("hidden");
    speedInput.classList.remove("hidden");
    speedInput.focus();
    speedInput.select();
  };

  const commitSpeedInput = () => {
    if (speedInput.classList.contains("hidden")) return;
    let val = parseFloat(speedInput.value);
    // Protect: lower than 0.5 or more than 3.0 snaps to 1.00
    if (isNaN(val) || val < 0.5 || val > 3.0) {
      val = 1.0;
    }
    val = Math.round(val * 100) / 100;
    updateSpeedDisplay(val);
    speedInput.classList.add("hidden");
    valDisplayTrigger.classList.remove("hidden");
    state.audioBufferCache.clear();
    saveSettings();
  };

  speedInput.onkeydown = (e) => {
    if (e.key === "Enter") {
      e.preventDefault();
      commitSpeedInput();
    } else if (e.key === "Escape") {
      e.preventDefault();
      speedInput.classList.add("hidden");
      valDisplayTrigger.classList.remove("hidden");
    }
  };

  speedInput.onblur = commitSpeedInput;
}

// Stepper controls: click, hold-to-repeat, and wheel support
let speedStepTimeout = null;
let speedStepInterval = null;

const startStepping = (delta) => {
  adjustSpeed(delta);
  speedStepTimeout = setTimeout(() => {
    speedStepInterval = setInterval(() => {
      adjustSpeed(delta);
    }, 60);
  }, 350);
};

const stopStepping = () => {
  const wasActive = speedStepTimeout || speedStepInterval;
  if (speedStepTimeout) {
    clearTimeout(speedStepTimeout);
    speedStepTimeout = null;
  }
  if (speedStepInterval) {
    clearInterval(speedStepInterval);
    speedStepInterval = null;
  }
  if (wasActive) {
    state.audioBufferCache.clear();
    saveSettings();
  }
};

if (speedStepUp) {
  speedStepUp.addEventListener("mousedown", (e) => {
    e.preventDefault();
    startStepping(e.shiftKey ? 0.05 : 0.01);
  });
  speedStepUp.addEventListener("mouseup", stopStepping);
  speedStepUp.addEventListener("mouseleave", stopStepping);
}

if (speedStepDown) {
  speedStepDown.addEventListener("mousedown", (e) => {
    e.preventDefault();
    startStepping(e.shiftKey ? -0.05 : -0.01);
  });
  speedStepDown.addEventListener("mouseup", stopStepping);
  speedStepDown.addEventListener("mouseleave", stopStepping);
}

// Scroll wheel support on speed number box and stepper container
let speedWheelDebounce = null;
const handleSpeedWheel = (e) => {
  e.preventDefault();
  const step = e.shiftKey ? 0.05 : 0.01;
  adjustSpeed(e.deltaY < 0 ? step : -step);
  clearTimeout(speedWheelDebounce);
  speedWheelDebounce = setTimeout(() => {
    state.audioBufferCache.clear();
    saveSettings();
  }, 300);
};

if (speedValBtn) {
  speedValBtn.addEventListener("wheel", handleSpeedWheel, { passive: false });
}
if (speedStepperBox) {
  speedStepperBox.addEventListener("wheel", handleSpeedWheel, { passive: false });
}
document.getElementById("voiceSelect").onchange = async () => {
  state.audioBufferCache.clear();
  await saveSettings();
};
const engineModeEl = document.getElementById("engineMode");
if (engineModeEl) {
  engineModeEl.onchange = async (e) => {
    const val = e.target.value;
    state.engineMode = val;
    state.audioBufferCache.clear();
    await saveSettings();
    try {
      showToast(`Switching engine to ${val.toUpperCase()}...`);
      const res = await fetchJSON('/api/system/models/select', {
        method: 'POST',
        body: JSON.stringify({ model_id: val }),
      });
      if (res.default_voice) {
        state.voice = res.default_voice;
      }
      setTimeout(async () => {
        await loadVoices();
      }, 600);
    } catch (err) {
      console.error("Failed to switch engine:", err);
      showToast(err.message || "Failed to switch engine");
    }
  };
}
const setupBtn = document.getElementById("setupBtn");
if (setupBtn) {
  setupBtn.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    closeSidebarMiniPopups();
    openModelManagerModal();
  };
}
const modelManagerBtn = document.getElementById("modelManagerBtn");
if (modelManagerBtn) {
  modelManagerBtn.onclick = (e) => {
    e.preventDefault();
    e.stopPropagation();
    openModelManagerModal();
  };
}

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
    syncEmptyStateCompact();
    applyReaderTypography();
    requestGeometrySync();
    if (collapsing) closeTypoMenu();
  };
}

export function syncEmptyStateCompact() {
  const w = window.innerWidth || document.documentElement.clientWidth || 0;
  const isCompact = w < 1100;

  const emptyState = document.getElementById("emptyState");
  if (emptyState) {
    if (isCompact) {
      emptyState.style.setProperty("display", "none", "important");
    } else {
      emptyState.style.removeProperty("display");
    }
  }

  document.body.classList.toggle("compact-empty-state", isCompact);
}

window.addEventListener("resize", syncEmptyStateCompact);
try {
  const ro = new ResizeObserver(() => syncEmptyStateCompact());
  ro.observe(document.body);
  const ca = document.querySelector(".content-area");
  if (ca) ro.observe(ca);
} catch (e) {
  console.warn("ResizeObserver init skipped", e);
}
syncEmptyStateCompact();

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
    await openEpubByPathOrFallback(file);
  } else {
    processPdfBlob(file, file.name);
  }
});

function closeSidebarMiniPopups() {
  document.getElementById("languagePopup")?.classList.add("hidden");
  document.getElementById("engineStatusPopup")?.classList.add("hidden");
  document.getElementById("kokoroDownloadPopup")?.classList.add("hidden");
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
    return;
  }

  const st = e.target.closest('[data-action="select-doc"]') || e.target.closest('[data-doc-id]');
  if (st) {
    const docId = st.dataset.id || st.dataset.docId;
    if (docId) {
      selectDocById(docId);
    }
    return;
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


// --- Setting Help Popover ---
const settingHelpPopup = document.getElementById("settingHelpPopup");
const settingHelpPopupText = document.getElementById("settingHelpPopupText");
const closeSettingHelpPopupBtn = document.getElementById("closeSettingHelpPopupBtn");

function closeSettingHelp() {
  if (settingHelpPopup) {
    settingHelpPopup.classList.add("hidden");
    delete settingHelpPopup.dataset.activeBtn;
  }
}

if (closeSettingHelpPopupBtn) {
  closeSettingHelpPopupBtn.onclick = (e) => {
    e.stopPropagation();
    closeSettingHelp();
  };
}

document.querySelectorAll(".setting-help-btn").forEach((btn) => {
  btn.onclick = (e) => {
    e.stopPropagation();
    const key = btn.dataset.helpKey;
    if (!key || !settingHelpPopup || !settingHelpPopupText) return;

    let text = "";
    if (state.translations) {
      const parts = key.split(".");
      let curr = state.translations;
      for (const p of parts) {
        if (curr && typeof curr === "object") curr = curr[p];
        else { curr = null; break; }
      }
      if (typeof curr === "string") text = curr;
    }

    if (!text) {
      const fallbackMap = {
        "settings.comma_pause_desc": "Recommended 0ms. Setting values above 0ms may cause unnatural pauses or speech cadence artifacts.",
        "settings.spam_symbols_pause_desc": "Adds pause when punctuation repeats (e.g. ..., ???, !!!, ?!?). Stacks extra silence for each repeated mark.",
        "settings.behavior_h_desc": "Breathing room before and after chapter headings (100% front, 30% tail). Lower heading levels scale down automatically.",
        "settings.behavior_img_desc": "Temporary silence while viewing an image or cover before narration resumes.",
        "settings.behavior_s_desc": "Dramatic pause for scene break dividers (such as ***, ---, or ◇◇◇).",
        "settings.behavior_n_desc": "Micro-pause between standard sentences and narrative text blocks."
      };
      text = fallbackMap[key] || "No description available.";
    }

    if (!settingHelpPopup.classList.contains("hidden") && settingHelpPopup.dataset.activeBtn === key) {
      closeSettingHelp();
      return;
    }

    settingHelpPopupText.textContent = text;
    settingHelpPopup.dataset.activeBtn = key;
    settingHelpPopup.classList.remove("hidden");

    if (window.lucide && typeof window.lucide.createIcons === "function") {
      window.lucide.createIcons();
    }

    const rect = btn.getBoundingClientRect();
    const popupWidth = settingHelpPopup.offsetWidth || 260;
    const popupHeight = settingHelpPopup.offsetHeight || 80;

    let left = rect.right - popupWidth;
    if (left < 12) left = 12;
    if (left + popupWidth > window.innerWidth - 12) left = window.innerWidth - popupWidth - 12;

    let top = rect.bottom + 6;
    if (top + popupHeight > window.innerHeight - 12) {
      top = rect.top - popupHeight - 6;
    }

    settingHelpPopup.style.left = `${left}px`;
    settingHelpPopup.style.top = `${top}px`;
  };
});

document.addEventListener("click", (e) => {
  if (settingHelpPopup && !settingHelpPopup.classList.contains("hidden")) {
    if (!settingHelpPopup.contains(e.target) && !e.target.closest(".setting-help-btn")) {
      closeSettingHelp();
    }
  }
});

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") closeSettingHelp();
});

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
let _wasSysDownloading = false;
let _lastSysReportedError = null;

async function startStatusPolling() {
  const poll = async () => {
    try {
      const status = await fetchJSON(`/api/system/status?t=${Date.now()}`);
      window.isEngineReady = status.model_loaded;
      
      if (status.selected_model) {
        state.selectedModel = status.selected_model;
      }
      if (status.active_model) {
        state.activeModel = status.active_model;
      }
      if (status.engine_mode) {
        state.engineMode = status.engine_mode.toLowerCase();
      }
      if (status.active_hardware) {
        state.activeHardware = status.active_hardware.toLowerCase();
      }

      const engineSelectEl = document.getElementById("engineMode");
      if (engineSelectEl && document.activeElement !== engineSelectEl) {
        let activeVal = status.active_model || status.selected_model || state.engineMode;
        if (activeVal === "kokoro-v1.0") activeVal = "gpu";
        else if (activeVal === "kokoro-v1.0-int8") activeVal = "cpu";
        if (engineSelectEl.value !== activeVal && Array.from(engineSelectEl.options).some(o => o.value === activeVal)) {
          engineSelectEl.value = activeVal;
        }
      }
      
      // Alert user if a background download terminates with an error
      if (_wasSysDownloading && !status.is_downloading && status.last_error && status.last_error !== _lastSysReportedError) {
        _lastSysReportedError = status.last_error;
        showToast(status.last_error);
      } else if (status.is_downloading) {
        _lastSysReportedError = null;
      }
      _wasSysDownloading = Boolean(status.is_downloading);

      const curState = `${status.is_downloading}-${status.is_loading}-${status.model_loaded}-${status.active_model}-${status.active_hardware}-${JSON.stringify(status.available_models)}-${status.last_error || ''}`;
      if (curState !== lastSysState) {
        lastSysState = curState;
        updateEngineStatusUI(status, true);
        if (status.model_loaded) {
          state.audioBufferCache.clear();
          loadVoices();
        }
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

// --- Reading Progress Session Buffer Flush on Unload/Exit ---
function flushProgressOnExit() {
  if (!state.currentDoc) return;
  const currentEl = state.sentenceElements ? state.sentenceElements[state.currentSentenceIndex] : null;
  let sentenceIdString = null;
  if (currentEl) {
    sentenceIdString = currentEl.dataset?.sentenceId || currentEl.getAttribute('id') || currentEl.closest('[id^="s_"]')?.getAttribute('id') || currentEl.id || null;
  }
  const payload = {
    currentPage: state.readingPageIndex || 0,
    lastSentenceId: sentenceIdString || state.currentDoc.lastSentenceId || null,
    lastSentenceIndex: typeof state.currentSentenceIndex === "number" ? state.currentSentenceIndex : 0,
    lastAccessed: Date.now(),
    current_page: state.currentDoc.current_page || 1,
    total_pages: state.currentDoc.total_pages || 1,
    progress_percent: state.currentDoc.progress_percent || 0,
  };
  const body = JSON.stringify(payload);
  const url = `/api/library/progress/${state.currentDoc.id}?flush=true`;
  if (navigator.sendBeacon) {
    const blob = new Blob([body], { type: "application/json" });
    navigator.sendBeacon(url, blob);
  } else {
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body,
      keepalive: true,
    }).catch(() => {});
  }
}

window.addEventListener("beforeunload", flushProgressOnExit);
window.addEventListener("pagehide", flushProgressOnExit);