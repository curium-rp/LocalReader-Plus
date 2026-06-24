import { state } from "./modules/state.js";
import { fetchJSON, API_URL } from "./modules/api.js";
import {
  renderIcons,
  showToast,
  switchTab,
  renderRules,
  renderIgnoreList,
  updateEngineStatusUI,
  highlightSearchTerm,
  escapeRegex,
  updateTranslations,
} from "./modules/ui.js";
import {
  loadLibrary,
  selectDocument,
  renderPage,
  processPdfBlob,
  processJsonData,
  getSentencesForPage,
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
} from "./modules/export.js";
import { initTimer } from "./modules/timer.js";
import { initThemeSystem } from "./modules/themes.js";

window.state = state;

async function init() {
  try {
    const settings = await fetchJSON(`/api/settings`);
    state.rules = settings.pronunciationRules || [];
    state.ignoreList = settings.ignoreList || [];
    state.headerFooterMode = settings.header_footer_mode || "off";
    state.engineMode = settings.engine_mode || "gpu";
    state.pauseSettings = settings.pause_settings || state.pauseSettings || {
      comma: 1, period: 2, question: 2, exclamation: 2, colon: 1, semicolon: 1
    };
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
    
    // Load new visual states
    state.autoHidePlayer = settings.autoHidePlayer || false;
    state.manualHidePlayer = settings.manualHidePlayer || false;
    state.sentenceIndicatorOn = settings.sentenceIndicatorOn || false;
    updateSentenceBrightness(); // I reuse this later//
    
    const autoHideCheckbox = document.getElementById("toggleAutoHide");
    if (autoHideCheckbox) {
        autoHideCheckbox.checked = state.autoHidePlayer;
        autoHideCheckbox.onchange = (e) => {
            state.autoHidePlayer = e.target.checked;
            
            if (state.autoHidePlayer) {
                // Priority Mode: Turning Auto ON strictly nullifies Manual
                state.manualHidePlayer = false;
                const restoreBtn = document.getElementById("playbarRestoreBtn");
                if (restoreBtn) {
                    restoreBtn.classList.replace("opacity-100", "opacity-0");
                    restoreBtn.classList.replace("pointer-events-auto", "pointer-events-none");
                }
                resetAutoHideTimer(); // Instantly trigger the first show/countdown cycle
            } else {
                // Turning Auto OFF resets the player to fully visible
                clearTimeout(mouseHideTimeout);
                document.getElementById("controls").classList.remove("minimized");
            }
            saveSettings();
        };
    }

    if (state.manualHidePlayer) {
        document.getElementById("controls").classList.add("minimized");
        document.getElementById("playbarRestoreBtn").classList.replace("opacity-0", "opacity-100");
        document.getElementById("playbarRestoreBtn").classList.replace("pointer-events-none", "pointer-events-auto");
    }

    const speedRange = document.getElementById("speedRange");
    if (speedRange && settings.speed) {
      speedRange.value = settings.speed;
      const sv = document.getElementById("speedVal");
      if (sv) sv.textContent = parseFloat(settings.speed).toFixed(2);
    }
    const fontSizeSlider = document.getElementById("fontSizeSlider");
    if (fontSizeSlider && settings.font_size) {
      fontSizeSlider.value = settings.font_size;
      const tv = document.getElementById("textSizeVal");
      if (tv) tv.textContent = settings.font_size;
      const preview = document.getElementById("currentSentencePreview");
      if (preview) {
        preview.style.fontSize = `${settings.font_size}px`;
        preview.style.lineHeight = parseInt(settings.font_size) * 1.5 + "px";
      }
      const textContent = document.getElementById("textContent");
      if (textContent) {
        textContent.style.fontSize = `${settings.font_size}px`;
        textContent.style.lineHeight =
          parseInt(settings.font_size) * 1.6 + "px";
      }
    }
    const headerSelect = document.getElementById("headerFooterMode");
    if (headerSelect) headerSelect.value = state.headerFooterMode;
    const engineSelect = document.getElementById("engineMode");
    if (engineSelect) engineSelect.value = state.engineMode;

    [
      "comma",
      "period",
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

    const langToggle = document.getElementById("languageToggle");
    if (langToggle) langToggle.textContent = state.uiLanguage.toUpperCase();
    await updateTranslations(state.uiLanguage);

    const voiceSelect = document.getElementById("voiceSelect");
    if (settings.voice_id && voiceSelect) {
      const opt = document.createElement("option");
      opt.value = settings.voice_id;
      opt.textContent = "Loading...";
      voiceSelect.appendChild(opt);
      voiceSelect.value = settings.voice_id;
    }
  } catch (e) {
    console.error("Settings load error", e);
    showToast("Settings failed to load: " + e.message);
  }

  renderIcons();
  initThemeSystem(); 

  try { await loadVoices(); } catch (e) { console.error(e); }
  try { await loadLibrary(); } catch (e) { console.error(e); }

  renderRules();
  renderIgnoreList();
  startStatusPolling();
  initTimer();
}

document.addEventListener("DOMContentLoaded", init);

document.getElementById("playBtn").onclick = togglePlayback;
let mouseHideTimeout = null;

function resetAutoHideTimer() {
    const controls = document.getElementById("controls");
    const restoreBtn = document.getElementById("playbarRestoreBtn");
    
    // The "Peek" Condition: Manual hide is ON, but the player has been temporarily un-minimized by the user
    const isPeeking = state.manualHidePlayer && !controls.classList.contains("minimized");

    // If auto is off AND we are not currently peeking, ignore mouse movement
    if (!state.autoHidePlayer && !isPeeking) return;
    
    // Show player
    controls.classList.remove("minimized");
    
    // Ensure manual indicator is hidden while the player is visible
    if (restoreBtn) {
        restoreBtn.classList.replace("opacity-100", "opacity-0"); 
        restoreBtn.classList.replace("pointer-events-auto", "pointer-events-none");
    }
    
    clearTimeout(mouseHideTimeout);
    // 3000ms strict delay
    mouseHideTimeout = setTimeout(() => {
        controls.classList.add("minimized");
        
        // If we were peeking (Manual Hide is ON), bring the ^ indicator back when the player hides
        if (state.manualHidePlayer && restoreBtn) {
            restoreBtn.classList.replace("opacity-0", "opacity-100"); 
            restoreBtn.classList.replace("pointer-events-none", "pointer-events-auto");
        }
    }, 3000); 
}

const contentArea = document.querySelector(".content-area");
if (contentArea) contentArea.addEventListener("mousemove", resetAutoHideTimer);

const controlsArea = document.getElementById("controls");
if (controlsArea) controlsArea.addEventListener("mousemove", resetAutoHideTimer);

document.getElementById("hidePlaybarBtn").onclick = () => {
    const controls = document.getElementById("controls");
    const restoreBtn = document.getElementById("playbarRestoreBtn");

    if (state.autoHidePlayer) {
        clearTimeout(mouseHideTimeout);
        controls.classList.add("minimized");
    } else {
        state.manualHidePlayer = true;
        saveSettings();
        
        controls.classList.add("minimized");
        restoreBtn.classList.replace("opacity-0", "opacity-100");
        restoreBtn.classList.replace("pointer-events-none", "pointer-events-auto");
        
        // Swap which back button is showing if already scrolled away
        if (!state.autoScrollEnabled) {
            const backBtn = document.getElementById("backToReadingBtn");
            if (backBtn) {
                backBtn.classList.add("hidden");
                backBtn.classList.remove("flex");
            }
            const hiddenBtn = document.getElementById("hiddenModeBackBtn");
            if (hiddenBtn) {
                hiddenBtn.classList.replace("opacity-0", "opacity-100");
                hiddenBtn.classList.replace("pointer-events-none", "pointer-events-auto");
            }
        }
    }
};

// The ^ Button: ONLY cancels manual hide. Strictly separated.
document.getElementById("playbarRestoreBtn").onclick = () => {
    state.manualHidePlayer = false;
    saveSettings();
    
    const controls = document.getElementById("controls");
    const restoreBtn = document.getElementById("playbarRestoreBtn");
    controls.classList.remove("minimized");
    
    restoreBtn.classList.replace("opacity-100", "opacity-0");
    restoreBtn.classList.replace("pointer-events-auto", "pointer-events-none");
    
    if (state.autoHidePlayer) resetAutoHideTimer();
};

// The Hidden Mode "Peek & Center" Button
const hiddenModeBtn = document.getElementById("hiddenModeBackBtn");
if (hiddenModeBtn) {
    hiddenModeBtn.onclick = async () => {
        const controls = document.getElementById("controls");
        
        // 1. Physically show the player to activate the "Peek" condition
        controls.classList.remove("minimized");
        
        // 2. Start the timer logic (Because of the peek condition, mouse movements will keep it alive for 3s!)
        resetAutoHideTimer();
        
        // 3. Hide this center pill button
        hiddenModeBtn.classList.replace("opacity-100", "opacity-0");
        hiddenModeBtn.classList.replace("pointer-events-auto", "pointer-events-none");
        
        // 4. Force state to sync with the reading pointer
        state.viewPageIndex = state.readingPageIndex;
        state.autoScrollEnabled = true;
        
        // 5. Add a tiny 300ms delay before rendering. This gives the CSS animations 
        // time to clear out so the math camera calculates the center perfectly!
        setTimeout(async () => {
            await renderPage();
        }, 300);
    };
}

// The Normal Button: ONLY Jumps to text
document.getElementById("backToReadingBtn").onclick = async () => {
    state.viewPageIndex = state.readingPageIndex;
    state.autoScrollEnabled = true;

    const btn = document.getElementById("backToReadingBtn");
    if (btn) {
        btn.classList.add("hidden");
        btn.classList.remove("flex");
    }
    await renderPage();
};

document.getElementById("skipBack").onclick = () => {
  if (state.currentSentenceIndex > 0)
    safeJumpToSentence(state.currentSentenceIndex - 1);
};
document.getElementById("skipForward").onclick = async () => {
  if (state.currentSentenceIndex < state.readingSentences.length - 1) {
    safeJumpToSentence(state.currentSentenceIndex + 1);
  } else if (state.readingPageIndex < state.currentPages.length - 1) {
    state.readingPageIndex++;
    state.readingSentences = await getSentencesForPage(state.readingPageIndex);
    safeJumpToSentence(0);
  }
};

window.addEventListener("keydown", (e) => {
  if (
    e.target.tagName === "INPUT" ||
    e.target.tagName === "TEXTAREA" ||
    e.target.isContentEditable
  )
    return;
  if (e.code === "Space") {
    e.preventDefault();
    togglePlayback();
  } else if (e.code === "ArrowLeft") {
    e.preventDefault();
    document.getElementById("skipBack").click();
  } else if (e.code === "ArrowRight") {
    e.preventDefault();
    document.getElementById("skipForward").click();
  } else if ((e.ctrlKey || e.metaKey) && e.key === "f" && state.currentDoc) {
    e.preventDefault();
    document.getElementById("searchBtn").click();
  } else if (e.key === "Escape") {
    e.preventDefault();
    // 🌟 UNIFIED DISMISSAL: Close search, TOC, drawers and overlays instantly//
    document.getElementById("searchModal").classList.add("hidden");
    
    const tocModal = document.getElementById("tocModal");
    if (tocModal) tocModal.classList.add("hidden");
    
    document.querySelectorAll(".voice-settings-drawer").forEach(d => d.classList.remove("open"));
    document.getElementById("drawerOverlay").classList.remove("active");
  }
});

document.getElementById("prevPage").onclick = async () => {
  if (state.viewPageIndex > 0) {
    state.viewPageIndex--;
    state.autoScrollEnabled = false;
    await renderPage();
  }
};
document.getElementById("nextPage").onclick = async () => {
  if (state.viewPageIndex < state.currentPages.length - 1) {
    state.viewPageIndex++;
    state.autoScrollEnabled = false;
    await renderPage();
  }
};
document.getElementById("pageInput").onchange = async (e) => {
  let v = parseInt(e.target.value) - 1;
  if (v >= 0 && v < state.currentPages.length) {
    state.viewPageIndex = v;
    state.autoScrollEnabled = false;
    await renderPage();
  }
};

document.getElementById("backToReadingBtn").onclick = async () => {
    state.viewPageIndex = state.readingPageIndex;
    state.autoScrollEnabled = true;

    const btn = document.getElementById("backToReadingBtn");
    if (btn) {
        btn.classList.add("hidden");
        btn.classList.remove("flex");
    }

    // SYNERGY 2: Scrolled away + Manually Hidden + Click Back to Track -> Unhide player
    if (state.manualHidePlayer) {
        state.manualHidePlayer = false;
        saveSettings();
        
        const controls = document.getElementById("controls");
        const restoreBtn = document.getElementById("playbarRestoreBtn");
        controls.classList.remove("minimized");
        
        restoreBtn.classList.replace("opacity-100", "opacity-0");
        restoreBtn.classList.replace("pointer-events-auto", "pointer-events-none");
    }

    await renderPage();
};

let isAutoFlipping = false;
const scrollContainer = document.querySelector(".content-area");
if (scrollContainer) {
  scrollContainer.addEventListener(
    "wheel",
    async (e) => {
      if (isAutoFlipping) return;
      const bottom =
        scrollContainer.scrollTop + scrollContainer.clientHeight >=
        scrollContainer.scrollHeight - 10;
      const top = scrollContainer.scrollTop <= 10;

      // MONITOR MODE BREAK: If user scrolls manually, disable auto-follow and show the "Back to track" button
      if (state.autoScrollEnabled) {
        state.autoScrollEnabled = false;
        
        if (state.manualHidePlayer) {
            const hiddenBtn = document.getElementById("hiddenModeBackBtn");
            if (hiddenBtn) {
                hiddenBtn.classList.replace("opacity-0", "opacity-100");
                hiddenBtn.classList.replace("pointer-events-none", "pointer-events-auto");
            }
        } else {
            const backBtn = document.getElementById("backToReadingBtn");
            if (backBtn) {
                backBtn.classList.remove("hidden");
                backBtn.classList.add("flex");
            }
        }
      }

      if (
        e.deltaY > 0 &&
        bottom &&
        state.viewPageIndex < state.currentPages.length - 1
      ) {
        isAutoFlipping = true;
        state.viewPageIndex++;
        await renderPage();
        scrollContainer.scrollTop = 0;
        setTimeout(() => {
          isAutoFlipping = false;
        }, 700);
      } else if (e.deltaY < 0 && top && state.viewPageIndex > 0) {
        isAutoFlipping = true;
        state.viewPageIndex--;
        await renderPage();
        scrollContainer.scrollTop = scrollContainer.scrollHeight;
        setTimeout(() => {
          isAutoFlipping = false;
        }, 700);
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
        processJsonData(data.pages, file.name.replace(/\.epub$/i, ""), docId, data.image_map, data.toc_map);
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
        pronunciationRules: state.rules,
        ignoreList: state.ignoreList,
        voice_id: document.getElementById("voiceSelect").value,
        speed: parseFloat(document.getElementById("speedRange").value),
        font_size: parseInt(document.getElementById("fontSizeSlider").value),
        header_footer_mode: state.headerFooterMode,
        engine_mode: state.engineMode,
        pause_settings: state.pauseSettings,
        behavior_settings: state.behaviorSettings,
        ui_language: state.uiLanguage,
        autoHidePlayer: state.autoHidePlayer,
        manualHidePlayer: state.manualHidePlayer,
        sentenceIndicatorOn: state.sentenceIndicatorOn,
      }),
    });
  } catch (e) {
    console.error(e);
  }
}

document.getElementById("speedRange").onchange = saveSettings;
document.getElementById("speedRange").oninput = (e) =>
  (document.getElementById("speedVal").textContent = parseFloat(
    e.target.value,
  ).toFixed(2));
document.getElementById("fontSizeSlider").onchange = saveSettings;
document.getElementById("fontSizeSlider").oninput = (e) => {
  const newSize = e.target.value;
  document.getElementById("textSizeVal").textContent = newSize;

  const preview = document.getElementById("currentSentencePreview");
  if (preview) {
    preview.style.fontSize = `${newSize}px`;
    preview.style.lineHeight = parseInt(newSize) * 1.5 + "px";
  }

  const textContent = document.getElementById("textContent");
  if (textContent) {
    textContent.style.fontSize = `${newSize}px`;
    textContent.style.lineHeight = parseInt(newSize) * 1.6 + "px";
  }
};
document.getElementById("voiceSelect").onchange = async () => {
  stopPlayback();
  state.audioBufferCache.clear();
  try {
    await fetchJSON("/api/system/clear-cache", { method: "POST" });
  } catch (e) {
    console.error("Failed to clear backend cache", e);
  }
  await saveSettings();
};
document.getElementById("headerFooterMode").onchange = async (e) => {
  state.headerFooterMode = e.target.value;
  await saveSettings();
  if (state.currentDoc) await renderPage();
};
document.getElementById("engineMode").onchange = async (e) => {
  state.engineMode = e.target.value;
  await saveSettings();
};
document.getElementById("setupBtn").onclick = async () => {
  try {
    await fetchJSON(`/api/system/setup?model_type=${state.engineMode}`, {
      method: "POST",
    });
    showToast("Started downloading...");
  } catch (e) {
    showToast(e.message);
  }
};

const toggleDrawer = (open) => {
  const d = document.getElementById("voiceSettingsDrawer");
  const o = document.getElementById("drawerOverlay");
  if (open) { o.classList.add("active"); d.classList.add("open"); } 
  else { d.classList.remove("open"); }
};
const toggleBehaviorDrawer = (open) => {
  const d = document.getElementById("behaviorSettingsDrawer");
  const o = document.getElementById("drawerOverlay");
  if (open) { o.classList.add("active"); d.classList.add("open"); } 
  else { d.classList.remove("open"); }
};

document.getElementById("voiceSettingsBtn").onclick = () => toggleDrawer(true);
document.getElementById("closeDrawerBtn").onclick = () => toggleDrawer(false);

document.getElementById("behaviorSettingsBtn").onclick = () => toggleBehaviorDrawer(true);
document.getElementById("closeBehaviorDrawerBtn").onclick = () => toggleBehaviorDrawer(false);

document.getElementById("drawerOverlay").onclick = () => {
  document.querySelectorAll(".voice-settings-drawer").forEach(d => d.classList.remove("open"));
  document.getElementById("drawerOverlay").classList.remove("active");
};

const sidebar = document.querySelector(".sidebar");
const dragHandle = document.getElementById("sidebarDragHandle");
let isResizing = false;
if (dragHandle && sidebar) {
  dragHandle.addEventListener("mousedown", (e) => {
    isResizing = true;
    document.body.style.cursor = "col-resize";
    e.preventDefault();
  });
  window.addEventListener("mousemove", (e) => {
    if (!isResizing) return;
    const newWidth = e.clientX;
    if (newWidth > 200 && newWidth < 600) sidebar.style.width = `${newWidth}px`;
  });
  window.addEventListener("mouseup", () => {
    isResizing = false;
    document.body.style.cursor = "";
  });
}

const sidebarCollapseBtn = document.getElementById("sidebarCollapseBtn");
const sidebarExpandBtn = document.getElementById("sidebarExpandBtn");
if (sidebarCollapseBtn && sidebarExpandBtn && sidebar) {
  const updateSidebarVar = (collapsed) => {
    document.documentElement.style.setProperty(
      "--sidebar-width",
      collapsed ? "0px" : sidebar.style.width || "320px",
    );
  };
  sidebarCollapseBtn.onclick = () => {
    sidebar.classList.add("collapsed");
    sidebarExpandBtn.classList.add("visible");
    updateSidebarVar(true);
  };
  sidebarExpandBtn.onclick = () => {
    sidebar.classList.remove("collapsed");
    sidebarExpandBtn.classList.remove("visible");
    updateSidebarVar(false);
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
      
      processJsonData(data.pages, file.name.replace(/\.epub$/i, ""), docId, data.image_map, data.toc_map);
    } catch (err) {
      console.error(err);
      showToast("EPUB conversion failed: " + err.message);
    }
  } else {
    processPdfBlob(file, file.name);
  }
});

document.getElementById("languageToggle").onclick = async () => {
  const langs = ["en", "es", "fr", "zh"];
  let cur = langs.includes(state.uiLanguage) ? state.uiLanguage : "en";
  const next = langs[(langs.indexOf(cur) + 1) % langs.length];

  state.uiLanguage = next;
  document.getElementById("languageToggle").textContent = next.toUpperCase();

  await updateTranslations(next);
  renderIcons();
  saveSettings();
  loadVoices();
  showToast(`Language set to ${next.toUpperCase()}`);
};

document.getElementById("searchBtn").onclick = () => {
  if (!state.currentDoc) {
    showToast("No document loaded");
    return;
  }
  document.getElementById("searchModal").classList.remove("hidden");
  document.getElementById("searchInput").focus();
};
document.getElementById("closeSearchBtn").onclick = () =>
  document.getElementById("searchModal").classList.add("hidden");

let searchMatchCase = false;
let searchWholeWord = false;

document.getElementById("btnMatchCase").onclick = (e) => {
  searchMatchCase = !searchMatchCase;
  const btn = e.currentTarget;
  btn.classList.toggle("bg-blue-600/20", searchMatchCase);
  btn.classList.toggle("text-blue-400", searchMatchCase);
  btn.classList.toggle("border-blue-500/50", searchMatchCase);
  document.getElementById("searchInput").dispatchEvent(new Event("input"));
};

document.getElementById("btnWholeWord").onclick = (e) => {
  searchWholeWord = !searchWholeWord;
  const btn = e.currentTarget;
  btn.classList.toggle("bg-blue-600/20", searchWholeWord);
  btn.classList.toggle("text-blue-400", searchWholeWord);
  btn.classList.toggle("border-blue-500/50", searchWholeWord);
  document.getElementById("searchInput").dispatchEvent(new Event("input"));
};

let searchDebounce = null;
document.getElementById("searchInput").oninput = (e) => {
  clearTimeout(searchDebounce);
  searchDebounce = setTimeout(async () => {
    const query = e.target.value.trim();
    const resultsList = document.getElementById("searchResultsList");
    if (!query || query.length < 2) {
      resultsList.innerHTML = "";
      document.getElementById("searchEmpty").classList.add("hidden");
      return;
    }
    try {
      // Pass the new filters to the backend
      const data = await fetchJSON(
        `/api/library/search/${state.currentDoc.id}?q=${encodeURIComponent(query)}&match_case=${searchMatchCase}&whole_word=${searchWholeWord}`
      );
      resultsList.innerHTML = "";
      if (data.results.length === 0) {
        document.getElementById("searchEmpty").classList.remove("hidden");
        return;
      }
      document.getElementById("searchEmpty").classList.add("hidden");
      const fragment = document.createDocumentFragment();
      
      const hlRegex = new RegExp(`(${escapeRegex(query)})`, searchMatchCase ? 'g' : 'gi');
      
      data.results.forEach((result) => {
        result.matches.forEach((match) => {
          const div = document.createElement("div");
          div.className = "search-result-item";
          div.innerHTML = `<div class="flex justify-between mb-2"><span class="text-xs font-bold text-blue-400">Page ${result.page_index + 1}</span></div><div class="search-result-snippet">${match.snippet.replace(hlRegex, '<mark>$1</mark>')}</div>`;
          div.onclick = async () => {
            // Keep the query state so renderPage() applies the yellow <mark> highlights
            state.currentSearchQuery = data.query;
            state.searchMatchCase = searchMatchCase;
            state.searchWholeWord = searchWholeWord;
            
            // 1. Change ONLY the view page. Do NOT change the reading page!
            state.viewPageIndex = result.page_index;
            
            // Disable monitor mode so the camera doesn't fight the search scroll
            state.autoScrollEnabled = false;
            
            document.getElementById("searchModal").classList.add("hidden");
            
            // 2. Render the page to physically generate the DOM and the highlights
            await renderPage();
            
            // 3. Smoothly center the physical screen on the highlighted text.
            // Notice: The audio jump event has been completely removed so it will NOT auto-play.
            setTimeout(() => {
                const finalHl = document.querySelector('.search-highlight');
                if (finalHl) finalHl.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }, 200);
          };
          fragment.appendChild(div);
        });
      });
      resultsList.appendChild(fragment);
    } catch (e) {}
  }, 300);
};

document.getElementById("exportBtn").onclick = startExport;
document.getElementById("cancelExportBtn").onclick = cancelExport;
document.getElementById("startFFMPEGDownload").onclick = startFFMPEGDownload;
document.getElementById("cancelFFMPEGBtn").onclick = () =>
  document.getElementById("ffmpegModal").classList.add("hidden");
document.getElementById("openFileLocationBtn").onclick = openExportLocation;

document.getElementById("rulesList").addEventListener("input", (e) => {
  if (e.target.dataset.action === "update-rule") {
    const id = e.target.dataset.id,
      field = e.target.dataset.field,
      val = e.target.type === "checkbox" ? e.target.checked : e.target.value;
    state.rules = state.rules.map((r) =>
      r.id === id ? { ...r, [field]: val } : r,
    );
    saveSettings();
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
    saveSettings();
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
  saveSettings();
};

document.getElementById("addIgnoreBtn").onclick = () => {
  state.ignoreList.push("");
  renderIgnoreList();
  saveSettings();
};
document.getElementById("ignoreListUI").addEventListener("change", (e) => {
  if (e.target.dataset.action === "update-ignore") {
    state.ignoreList[parseInt(e.target.dataset.index)] = e.target.value;
    saveSettings();
  }
});
document.getElementById("ignoreListUI").addEventListener("click", (e) => {
  const t = e.target.closest('[data-action="delete-ignore"]');
  if (t) {
    state.ignoreList.splice(parseInt(t.dataset.index), 1);
    renderIgnoreList();
    saveSettings();
  }
});

document.getElementById("libraryPanel").addEventListener("click", (e) => {
  const st = e.target.closest('[data-action="select-doc"]');
  if (st) {
    selectDocById(st.dataset.id);
    return;
  }
  const dt = e.target.closest('[data-action="delete-doc"]');
  if (dt && confirm("Delete?")) {
    fetchJSON(`/api/library/${dt.dataset.id}`, { method: "DELETE" }).then(
      () => {
        if (state.currentDoc?.id === dt.dataset.id) location.reload();
        else loadLibrary();
      },
    );
  }
});

window.selectDocById = async (id) => {
  const items = await fetchJSON(`/api/library`);
  const item = items.find((i) => i.id === id);
  if (item) selectDocument(item);
};

["Comma", "Period", "Question", "Exclamation", "Colon", "Semicolon"].forEach(
  (k) => {
    const el = document.getElementById(`pause${k}`);
    if (el) {
      el.oninput = (e) => {
        if (!state.pauseSettings) state.pauseSettings = {};
        state.pauseSettings[k.toLowerCase()] = parseInt(e.target.value);
        const valEl = document.getElementById(`pause${k}Val`);
        if (valEl) valEl.textContent = e.target.value;
      };
      el.onchange = saveSettings;
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
        el.onchange = saveSettings; 
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
    const wasPlaying = state.isPlaying;
    if (wasPlaying) {
        stopPlayback();
        // Restore playing state flag so the next sentence handles autoplay instantly
        state.isPlaying = true; 
    }
    jumpToSentence(index);
}

window.addEventListener("jump-to-sentence", (e) => safeJumpToSentence(e.detail));

let lastSysState = null;
async function startStatusPolling() {
  const poll = async () => {
    try {
      const status = await fetchJSON(`/api/system/status?t=${Date.now()}`);
      window.isEngineReady = status.model_loaded;
      const selModel =
        state.engineMode === "gpu"
          ? status.available_models?.gpu
          : status.available_models?.cpu;
      const curState = `${status.is_downloading}-${status.is_loading}-${status.model_loaded}-${selModel}`;
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
        if (modal) modal.classList.remove("hidden");
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

// Current Sentence Brightness Toggle
const brightnessBtn = document.getElementById("toggleBrightnessBtn");
if (brightnessBtn) {
    brightnessBtn.onclick = () => {
        state.sentenceIndicatorOn = !state.sentenceIndicatorOn;
        updateSentenceBrightness();
        saveSettings();
    };
}

function updateSentenceBrightness() {
    const preview = document.getElementById("currentSentencePreview");
    const icon = document.getElementById("brightnessIcon");
    if (!preview) return;

    if (state.sentenceIndicatorOn) {
        // ON - Bright
        preview.classList.remove("text-zinc-600");
        preview.classList.add("text-zinc-200");
        if (icon) icon.setAttribute("data-lucide", "sun");
    } else {
        // OFF - Dim (Hard to notice)
        preview.classList.remove("text-zinc-200");
        preview.classList.add("text-zinc-600");
        if (icon) icon.setAttribute("data-lucide", "moon");
    }
    
    if (window.lucide) window.lucide.createIcons();
}