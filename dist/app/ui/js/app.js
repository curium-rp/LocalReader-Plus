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

window.refreshVoiceDropdown = async () => {
  try {
    const engineToFetch = state.ttsEngine || "kokoro";

    // 1. If F5 is active, intercept the UI and mount the studio
    if (engineToFetch === "f5") {
      await window.f5Studio.mount();
      return;
    }

    // 2. If Kokoro is active, unmount F5 and restore standard select
    window.f5Studio.unmount();
    
    const response = await fetch(`/api/voices/available?engine=kokoro`);
    const data = await response.json();
    const voiceSelect = document.getElementById("voiceSelect");
    if (!voiceSelect) return;
    
    const currentVal = state.voiceId || voiceSelect.value;
    voiceSelect.innerHTML = ""; 

    Object.entries(data.categories || {}).forEach(([catKey, catData]) => {
      const group = document.createElement("optgroup");
      group.label = catData.label || catKey;
      catData.voices.forEach(v => {
        const opt = document.createElement("option");
        opt.value = v.id;
        opt.textContent = v.name;
        group.appendChild(opt);
      });
      voiceSelect.appendChild(group);
    });

    if (currentVal && voiceSelect.querySelector(`option[value="${currentVal}"]`)) {
      voiceSelect.value = currentVal;
    } else {
      voiceSelect.selectedIndex = 0;
    }
    
    state.voiceId = voiceSelect.value;
  } catch (e) {
    console.error("Failed to refresh voices:", e);
  }
};

async function init() {
  try {
    if (window.pdfjsLib)
      window.pdfjsLib.GlobalWorkerOptions.workerSrc = "lib/pdf.worker.min.js";
  } catch (e) {
    console.error("PDF.js init error", e);
  }

  try {
    const settings = await fetchJSON(`/api/settings`);
    state.rules = settings.pronunciationRules || [];
    state.ignoreList = settings.ignoreList || [];
    state.headerFooterMode = settings.header_footer_mode || "off";
    state.engineMode = settings.engine_mode || "gpu";
    state.ttsEngine = settings.active_engine || "kokoro";
    state.pauseSettings = settings.pause_settings || state.pauseSettings || {
      comma: 1, period: 2, question: 2, exclamation: 2, colon: 1, semicolon: 1
    };
    state.uiLanguage = settings.ui_language || "en";

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
        textContent.style.lineHeight = parseInt(settings.font_size) * 1.6 + "px";
      }
    }
    
    const headerSelect = document.getElementById("headerFooterMode");
    if (headerSelect) headerSelect.value = state.headerFooterMode;
    
    const engineSelect = document.getElementById("engineMode");
    if (engineSelect) {
      if (state.ttsEngine === "f5") {
        engineSelect.value = "f5";
      } else {
        engineSelect.value = state.engineMode; 
      }
    }

    ["comma", "period", "question", "exclamation", "colon", "semicolon"].forEach((key) => {
      const input = document.getElementById(`pause${key.charAt(0).toUpperCase() + key.slice(1)}`);
      const val = document.getElementById(`pause${key.charAt(0).toUpperCase() + key.slice(1)}Val`);
      if (input && val && state.pauseSettings[key] !== undefined) {
        input.value = state.pauseSettings[key];
        val.textContent = state.pauseSettings[key];
      }
    });

    const langToggle = document.getElementById("languageToggle");
    if (langToggle) langToggle.textContent = state.uiLanguage.toUpperCase();
    await updateTranslations(state.uiLanguage);

  } catch (e) {
    console.error("Settings load error:", e);
  }

  renderIcons();
  initThemeSystem(); 

  try { await loadVoices(); } catch (e) { console.error(e); }
  try { await loadLibrary(); } catch (e) { console.error(e); }

  renderRules();
  renderIgnoreList();
  startStatusPolling();
  initTimer();
  document.addEventListener('f5-voice-changed', async (e) => {
      state.voiceId = e.detail;
      stopPlayback();
      state.audioBufferCache.clear();
      try {
          await fetchJSON("/api/system/clear-cache", { method: "POST" });
      } catch (err) {}
      await saveSettings();
  });

}

document.addEventListener("DOMContentLoaded", init);

// --- Event Listeners ---
const playBtn = document.getElementById("playBtn");
if (playBtn) playBtn.onclick = togglePlayback;

const hidePlaybarBtn = document.getElementById("hidePlaybarBtn");
if (hidePlaybarBtn) {
  hidePlaybarBtn.onclick = () => {
    const controls = document.getElementById("controls");
    const restoreBtn = document.getElementById("playbarRestoreBtn");
    controls.classList.add("minimized");
    restoreBtn.classList.add("visible");
  };
}

const restoreBtn = document.getElementById("playbarRestoreBtn");
if (restoreBtn) {
  restoreBtn.onclick = () => {
    const controls = document.getElementById("controls");
    controls.classList.remove("minimized");
    restoreBtn.classList.remove("visible");
  };
}

const skipBack = document.getElementById("skipBack");
if (skipBack) {
  skipBack.onclick = () => {
    if (state.currentSentenceIndex > 0) jumpToSentence(state.currentSentenceIndex - 1);
  };
}

const skipForward = document.getElementById("skipForward");
if (skipForward) {
  skipForward.onclick = async () => {
    if (state.currentSentenceIndex < state.readingSentences.length - 1) {
      jumpToSentence(state.currentSentenceIndex + 1);
    } else if (state.readingPageIndex < state.currentPages.length - 1) {
      state.readingPageIndex++;
      state.readingSentences = await getSentencesForPage(state.readingPageIndex);
      jumpToSentence(0);
    }
  };
}

window.addEventListener("keydown", (e) => {
  if (e.target.tagName === "INPUT" || e.target.tagName === "TEXTAREA" || e.target.isContentEditable) return;
  if (e.code === "Space") {
    e.preventDefault();
    togglePlayback();
  } else if (e.code === "ArrowLeft") {
    e.preventDefault();
    document.getElementById("skipBack")?.click();
  } else if (e.code === "ArrowRight") {
    e.preventDefault();
    document.getElementById("skipForward")?.click();
  } else if ((e.ctrlKey || e.metaKey) && e.key === "f" && state.currentDoc) {
    e.preventDefault();
    document.getElementById("searchBtn")?.click();
  } else if (e.key === "Escape") {
    document.getElementById("closeSearchBtn")?.click();
  }
});

const prevPage = document.getElementById("prevPage");
if (prevPage) {
  prevPage.onclick = async () => {
    if (state.viewPageIndex > 0) {
      state.viewPageIndex--;
      state.autoScrollEnabled = false;
      await renderPage();
    }
  };
}

const nextPage = document.getElementById("nextPage");
if (nextPage) {
  nextPage.onclick = async () => {
    if (state.viewPageIndex < state.currentPages.length - 1) {
      state.viewPageIndex++;
      state.autoScrollEnabled = false;
      await renderPage();
    }
  };
}

const pageInput = document.getElementById("pageInput");
if (pageInput) {
  pageInput.onchange = async (e) => {
    let v = parseInt(e.target.value) - 1;
    if (v >= 0 && v < state.currentPages.length) {
      state.viewPageIndex = v;
      state.autoScrollEnabled = false;
      await renderPage();
    }
  };
}

const backToReadingBtn = document.getElementById("backToReadingBtn");
if (backToReadingBtn) {
  backToReadingBtn.onclick = async () => {
    state.viewPageIndex = state.readingPageIndex;
    state.autoScrollEnabled = true;
    await renderPage();
    const active = document.querySelector(".active-sentence");
    if (active) active.scrollIntoView({ behavior: "smooth", block: "center" });
  };
}

let isAutoFlipping = false;
const scrollContainer = document.querySelector(".content-area");
if (scrollContainer) {
  scrollContainer.addEventListener("wheel", async (e) => {
      if (isAutoFlipping) return;
      const bottom = scrollContainer.scrollTop + scrollContainer.clientHeight >= scrollContainer.scrollHeight - 10;
      const top = scrollContainer.scrollTop <= 10;

      if (state.autoScrollEnabled) state.autoScrollEnabled = false;

      if (e.deltaY > 0 && bottom && state.viewPageIndex < state.currentPages.length - 1) {
        isAutoFlipping = true;
        state.viewPageIndex++;
        await renderPage();
        scrollContainer.scrollTop = 0;
        setTimeout(() => { isAutoFlipping = false; }, 700);
      } else if (e.deltaY < 0 && top && state.viewPageIndex > 0) {
        isAutoFlipping = true;
        state.viewPageIndex--;
        await renderPage();
        scrollContainer.scrollTop = scrollContainer.scrollHeight;
        setTimeout(() => { isAutoFlipping = false; }, 700);
      }
    },
    { passive: true },
  );
}

const pdfUpload = document.getElementById("pdfUpload");
if (pdfUpload) {
  pdfUpload.onchange = async (e) => {
    const file = e.target.files[0];
    if (file) {
      if (file.name.toLowerCase().endsWith(".epub")) {
        showToast("Converting EPUB...");
        const formData = new FormData();
        formData.append("file", file);
        try {
          const res = await fetch("/api/convert/epub", { method: "POST", body: formData });
          if (!res.ok) throw new Error("Conversion failed");
          const blob = await res.blob();
          processPdfBlob(blob, file.name.replace(".epub", ".pdf"));
        } catch (err) {
          showToast("EPUB conversion failed: " + err.message);
        }
      } else {
        processPdfBlob(file, file.name);
      }
      e.target.value = "";
    }
  };
}

const tabLib = document.getElementById("tabLibrary");
if (tabLib) tabLib.onclick = () => switchTab(tabLib, document.getElementById("libraryPanel"));

const tabRules = document.getElementById("tabRules");
if (tabRules) tabRules.onclick = () => switchTab(tabRules, document.getElementById("rulesPanel"));

const tabIgnore = document.getElementById("tabIgnore");
if (tabIgnore) tabIgnore.onclick = () => switchTab(tabIgnore, document.getElementById("ignorePanel"));

async function saveSettings() {
  try {
    const voiceVal = document.getElementById("voiceSelect") ? document.getElementById("voiceSelect").value : null;
    const speedVal = document.getElementById("speedRange") ? parseFloat(document.getElementById("speedRange").value) : 1.0;
    const fontVal = document.getElementById("fontSizeSlider") ? parseInt(document.getElementById("fontSizeSlider").value) : 16;
    
    await fetchJSON(`/api/settings`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        pronunciationRules: state.rules,
        ignoreList: state.ignoreList,
        voice_id: voiceVal,
        active_engine: state.ttsEngine,    
        engine_mode: state.engineMode,     
        speed: speedVal,
        font_size: fontVal,
        header_footer_mode: state.headerFooterMode,
        pause_settings: state.pauseSettings,
        ui_language: state.uiLanguage,
      }),
    });
  } catch (e) {
    console.error(e);
  }
}

const speedRange = document.getElementById("speedRange");
if (speedRange) {
  speedRange.onchange = saveSettings;
  speedRange.oninput = (e) => (document.getElementById("speedVal").textContent = parseFloat(e.target.value).toFixed(2));
}

const fontSizeSlider = document.getElementById("fontSizeSlider");
if (fontSizeSlider) {
  fontSizeSlider.onchange = saveSettings;
  fontSizeSlider.oninput = (e) => {
    const newSize = e.target.value;
    const txtVal = document.getElementById("textSizeVal");
    if (txtVal) txtVal.textContent = newSize;

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
}

const engineMode = document.getElementById("engineMode");
if (engineMode) {
  engineMode.onchange = async (e) => {
    const val = e.target.value;
    
if (val === "f5") {
      state.ttsEngine = "f5";
      state.engineMode = "gpu"; 
    } else if (val === "fish") {      
      state.ttsEngine = "fish";
      state.engineMode = "gpu";
    } else {
      state.ttsEngine = "kokoro";
      state.engineMode = val; 
    }

    stopPlayback();
    state.audioBufferCache.clear();
    try {
      await fetchJSON("/api/system/clear-cache", { method: "POST" });
      await fetchJSON(`/api/system/switch-engine?target_mode=${state.engineMode}&engine=${state.ttsEngine}`, { method: "POST" });
    } catch (err) {}

    await saveSettings();
    await loadVoices(); 
  };
}

// --- Fix 1: Setup Button ---
const setupBtn = document.getElementById("setupBtn");
if (setupBtn) {
  setupBtn.onclick = async () => {
    try {
      const activeEngine = state.ttsEngine; // <-- SURGICAL FIX: Automatically uses f5, fish, or kokoro
      await fetchJSON(`/api/system/setup?model_type=${state.engineMode}&engine=${activeEngine}`, {
        method: "POST",
      });
      showToast(`Downloading ${activeEngine.toUpperCase()}... Check server console for progress.`);
    } catch (e) {
      showToast("Setup Error: " + e.message);
    }
  };
}

const headerFooterMode = document.getElementById("headerFooterMode");
if (headerFooterMode) {
  headerFooterMode.onchange = async (e) => {
    state.headerFooterMode = e.target.value;
    await saveSettings();
    if (state.currentDoc) await renderPage();
  };
}

const toggleDrawer = (open) => {
  const d = document.getElementById("voiceSettingsDrawer");
  const o = document.getElementById("drawerOverlay");
  
  if (d) { // Only check if the drawer exists
    if (open) {
      d.classList.add("open");
      if (o) o.classList.add("active"); // Safely toggle overlay if it exists
    } else {
      d.classList.remove("open");
      if (o) o.classList.remove("active");
    }
  }
};

const voiceSettingsBtn = document.getElementById("voiceSettingsBtn");
if (voiceSettingsBtn) voiceSettingsBtn.onclick = () => toggleDrawer(true);
const closeDrawerBtn = document.getElementById("closeDrawerBtn");
if (closeDrawerBtn) closeDrawerBtn.onclick = () => toggleDrawer(false);
const drawerOverlay = document.getElementById("drawerOverlay");
if (drawerOverlay) drawerOverlay.onclick = () => toggleDrawer(false);

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
    document.documentElement.style.setProperty("--sidebar-width", collapsed ? "0px" : sidebar.style.width || "320px");
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
    showToast("Converting EPUB...");
    const formData = new FormData();
    formData.append("file", file);
    try {
      const res = await fetch("/api/convert/epub", { method: "POST", body: formData });
      if (!res.ok) throw new Error("Conversion failed");
      const blob = await res.blob();
      processPdfBlob(blob, file.name.replace(".epub", ".pdf"));
    } catch (err) {
      showToast("EPUB conversion failed: " + err.message);
    }
  } else {
    processPdfBlob(file, file.name);
  }
});

const langToggle = document.getElementById("languageToggle");
if (langToggle) {
  langToggle.onclick = async () => {
    const langs = ["en", "es", "fr", "zh"];
    let cur = langs.includes(state.uiLanguage) ? state.uiLanguage : "en";
    const next = langs[(langs.indexOf(cur) + 1) % langs.length];

    state.uiLanguage = next;
    langToggle.textContent = next.toUpperCase();

    await updateTranslations(next);
    renderIcons();
    saveSettings();
    loadVoices();
    showToast(`Language set to ${next.toUpperCase()}`);
  };
}

const searchBtn = document.getElementById("searchBtn");
if (searchBtn) {
  searchBtn.onclick = () => {
    if (!state.currentDoc) {
      showToast("No document loaded");
      return;
    }
    document.getElementById("searchModal").classList.remove("hidden");
    document.getElementById("searchInput").focus();
  };
}

const closeSearchBtn = document.getElementById("closeSearchBtn");
if (closeSearchBtn) closeSearchBtn.onclick = () => document.getElementById("searchModal").classList.add("hidden");

let searchDebounce = null;
const searchInput = document.getElementById("searchInput");
if (searchInput) {
  searchInput.oninput = (e) => {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(async () => {
      const query = e.target.value.trim();
      const resultsList = document.getElementById("searchResultsList");
      if (!query || query.length < 2) {
        resultsList.innerHTML = "";
        return;
      }
      try {
        const data = await fetchJSON(`/api/library/search/${state.currentDoc.id}?q=${encodeURIComponent(query)}`);
        resultsList.innerHTML = "";
        if (data.results.length === 0) {
          document.getElementById("searchEmpty").classList.remove("hidden");
          return;
        }
        document.getElementById("searchEmpty").classList.add("hidden");
        const fragment = document.createDocumentFragment();
        data.results.forEach((result) => {
          result.matches.forEach((match) => {
            const div = document.createElement("div");
            div.className = "search-result-item";
            div.innerHTML = `<div class="flex justify-between mb-2"><span class="text-xs font-bold text-blue-400">Page ${result.page_index + 1}</span></div><div class="search-result-snippet">${match.snippet}</div>`;
            div.onclick = async () => {
              state.currentSearchQuery = data.query;
              state.viewPageIndex = result.page_index;
              state.autoScrollEnabled = false;
              document.getElementById("searchModal").classList.add("hidden");
              await renderPage();
              highlightSearchTerm(state.currentSearchQuery);
            };
            fragment.appendChild(div);
          });
        });
        resultsList.appendChild(fragment);
      } catch (e) {}
    }, 300);
  };
}

const exportBtn = document.getElementById("exportBtn");
if (exportBtn) exportBtn.onclick = startExport;
const cancelExpBtn = document.getElementById("cancelExportBtn");
if (cancelExpBtn) cancelExpBtn.onclick = cancelExport;
const ffmpegBtn = document.getElementById("startFFMPEGDownload");
if (ffmpegBtn) ffmpegBtn.onclick = startFFMPEGDownload;
const cancelFFMPEGBtn = document.getElementById("cancelFFMPEGBtn");
if (cancelFFMPEGBtn) cancelFFMPEGBtn.onclick = () => document.getElementById("ffmpegModal").classList.add("hidden");
const openFileLocBtn = document.getElementById("openFileLocationBtn");
if (openFileLocBtn) openFileLocBtn.onclick = openExportLocation;

const rulesList = document.getElementById("rulesList");
if (rulesList) {
  rulesList.addEventListener("input", (e) => {
    if (e.target.dataset.action === "update-rule") {
      const id = e.target.dataset.id, field = e.target.dataset.field, val = e.target.type === "checkbox" ? e.target.checked : e.target.value;
      state.rules = state.rules.map((r) => r.id === id ? { ...r, [field]: val } : r );
      saveSettings();
    }
  });
  rulesList.addEventListener("click", (e) => {
    const t = e.target.closest("[data-action]");
    if (!t) return;
    const action = t.dataset.action, id = t.dataset.id;
    if (action === "toggle-rule") {
      state.rules = state.rules.map((r) => r.id === id ? { ...r, isExpanded: !r.isExpanded } : r );
      renderRules();
    } else if (action === "delete-rule") {
      state.rules = state.rules.filter((r) => r.id !== id);
      renderRules();
      saveSettings();
    }
  });
}

const addRuleBtn = document.getElementById("addRuleBtn");
if (addRuleBtn) {
  addRuleBtn.onclick = () => {
    state.rules.push({ id: crypto.randomUUID(), original: "", replacement: "", match_case: false, word_boundary: true, is_regex: false, isExpanded: true });
    renderRules();
    saveSettings();
  };
}

const addIgnoreBtn = document.getElementById("addIgnoreBtn");
if (addIgnoreBtn) {
  addIgnoreBtn.onclick = () => {
    state.ignoreList.push("");
    renderIgnoreList();
    saveSettings();
  };
}

const ignoreListUI = document.getElementById("ignoreListUI");
if (ignoreListUI) {
  ignoreListUI.addEventListener("change", (e) => {
    if (e.target.dataset.action === "update-ignore") {
      state.ignoreList[parseInt(e.target.dataset.index)] = e.target.value;
      saveSettings();
    }
  });
  ignoreListUI.addEventListener("click", (e) => {
    const t = e.target.closest('[data-action="delete-ignore"]');
    if (t) {
      state.ignoreList.splice(parseInt(t.dataset.index), 1);
      renderIgnoreList();
      saveSettings();
    }
  });
}

const libPanel = document.getElementById("libraryPanel");
if (libPanel) {
  libPanel.addEventListener("click", (e) => {
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
}

window.selectDocById = async (id) => {
  const items = await fetchJSON(`/api/library`);
  const item = items.find((i) => i.id === id);
  if (item) selectDocument(item);
};

["Comma", "Period", "Question", "Exclamation", "Colon", "Semicolon"].forEach((k) => {
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
});

const pauseToggleBtn = document.getElementById("pauseSettingsToggle");
if (pauseToggleBtn) {
  pauseToggleBtn.onclick = () => {
    const content = document.getElementById("pauseSettingsContent");
    if (content) content.classList.toggle("hidden");
  };
}

window.addEventListener("jump-to-sentence", (e) => jumpToSentence(e.detail));

let lastSysState = null;
async function startStatusPolling() {
  const poll = async () => {
    try {
      const status = await fetchJSON(`/api/system/status?t=${Date.now()}`);
      window.isEngineReady = status.model_loaded;
      
      let selModel = false;
      if (state.ttsEngine === "f5") {
        selModel = status.available_models?.f5;
      } else if (state.ttsEngine === "fish") { 
        selModel = status.available_models?.fish;
      } else {
        selModel = state.engineMode === "gpu" ? status.available_models?.gpu : status.available_models?.cpu;
      }

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


window.addEventListener("unhandledrejection", (event) => {
  if (event.reason && event.reason.message && event.reason.message.includes("reference audio is missing")) {
      showToast("Please upload an audio file to clone this voice first!", "error");
      const cloneModal = document.getElementById("cloneVoiceModal");
      if (cloneModal) cloneModal.classList.remove("hidden");
  }
});

// SURGICAL FIX: Using Event Delegation so the listener NEVER detaches when the HTML swaps!
document.addEventListener("change", async (e) => {
    if (e.target && e.target.id === "voiceSelect") {
        state.voiceId = e.target.value; 
        stopPlayback();
        state.audioBufferCache.clear();
        try {
            await fetchJSON("/api/system/clear-cache", { method: "POST" });
        } catch (err) {}
        await saveSettings();
    }
});

// ==========================================
// 1. SMART VOICE UI UPDATER (Manages the Clone Box)
// ==========================================
window.refreshVoiceDropdown = async () => {
  try {
    const engineToFetch = state.ttsEngine || "kokoro";

    // 1. If F5 is active, safely mount the studio
    if (engineToFetch === "f5") {
      if (window.f5Studio) await window.f5Studio.mount();
      return;
    }

    // 2. Safely unmount F5 studio if switching engines
    if (window.f5Studio) window.f5Studio.unmount();
    
    // SURGICAL FIX: Connect to the universal Python API!
    const response = await fetch(`/api/voices/available?engine=${engineToFetch}`);
    const data = await response.json();
 
    const voiceSelect = document.getElementById("voiceSelect");
    const cloneBtn = document.getElementById("openCloneModalBtn"); 
    if (!voiceSelect) return;

    // BOX VISIBILITY: F5 & Fish (Blue) vs Kokoro (Faded)
    if (cloneBtn) {
        if (engineToFetch === "f5" || engineToFetch === "fish") {
            cloneBtn.disabled = false;
            cloneBtn.className = "w-full bg-blue-600/10 text-xs font-bold border border-blue-500/50 rounded px-2 py-2 outline-none text-blue-400 hover:border-blue-500 hover:bg-blue-600/20 transition-all mb-2 flex items-center justify-center gap-2 shadow-[0_0_10px_rgba(59,130,246,0.1)] cursor-pointer";
            cloneBtn.innerHTML = `<i data-lucide="plus" class="w-3.5 h-3.5"></i><span>Create Voice Clone (${engineToFetch.toUpperCase()})</span>`;
        } else {
            cloneBtn.disabled = true;
            cloneBtn.className = "w-full bg-zinc-900/30 text-xs font-bold border border-zinc-800/50 rounded px-2 py-2 outline-none text-zinc-600 cursor-not-allowed mb-2 flex items-center justify-center gap-2 opacity-50";
            cloneBtn.innerHTML = `<i data-lucide="mic-off" class="w-3.5 h-3.5"></i><span>Clone Voice (Requires F5 or Fish)</span>`;
        }
        if (typeof renderIcons === 'function') renderIcons();
    }

    const currentVal = state.voiceId || voiceSelect.value;
    voiceSelect.innerHTML = ""; 

    // POPULATE VOICES
    if (engineToFetch === "f5" || engineToFetch === "fish") {
        const catName = engineToFetch === "f5" ? "F5" : "Fish";
        if (data.categories && data.categories[catName]) {
            data.categories[catName].voices.forEach(v => {
                const opt = document.createElement("option");
                opt.value = v.id;
                opt.textContent = v.name;
                voiceSelect.appendChild(opt);
            });
        }
    } else {
        Object.entries(data.categories || {}).forEach(([catKey, catData]) => {
          const group = document.createElement("optgroup");
          group.label = catData.label || catKey;
          catData.voices.forEach(v => {
            const opt = document.createElement("option");
            opt.value = v.id;
            opt.textContent = v.name;
            group.appendChild(opt);
          });
          voiceSelect.appendChild(group);
        });
    }

    if (currentVal && voiceSelect.querySelector(`option[value="${currentVal}"]`)) {
      voiceSelect.value = currentVal;
    } else {
      voiceSelect.selectedIndex = 0;
    }
    
    state.voiceId = voiceSelect.value;
  } catch (e) {
    console.error("Failed to refresh voices:", e);
  }
};

// ==========================================
// VOICE CLONE GATEKEEPER & API ROUTER
// ==========================================

const openCloneModalBtn = document.getElementById("openCloneModalBtn");
const submitCloneBtn = document.getElementById("submitCloneBtn");
const cloneVoiceForm = document.getElementById("cloneVoiceForm");

// 1. DYNAMIC UI WARNINGS
if (openCloneModalBtn) {
    openCloneModalBtn.addEventListener("click", () => {
        const engine = state.ttsEngine || "kokoro";
        let instructionsBox = document.getElementById("dynamicCloneRules");
        
        // Inject the instruction box into the modal if it doesn't exist
        if (!instructionsBox) {
            const form = document.getElementById("cloneVoiceForm");
            if (form) {
                instructionsBox = document.createElement("div");
                instructionsBox.id = "dynamicCloneRules";
                form.prepend(instructionsBox);
            }
        }

        // Apply Official Fish Audio Guidelines
        if (instructionsBox) {
            if (engine === "fish") {
                instructionsBox.className = "text-xs p-3 rounded mb-4 bg-blue-900/20 border border-blue-500/30 text-blue-300";
                instructionsBox.innerHTML = `
                    <strong class="text-blue-400 block mb-1">Fish-TTS Voice Memory Rules:</strong>
                    <ul class="list-disc pl-4 space-y-1">
                        <li><strong>Length:</strong> Optimal is <span class="text-white">8 to 20 seconds</span>.</li>
                        <li class="text-red-400"><strong>Strict Limit:</strong> Do NOT exceed 30 seconds (Prevents GPU crashes).</li>
                        <li><strong>Quality:</strong> Must have clear speech with NO background noise.</li>
                        <li><strong>Transcript:</strong> Must match the audio 100% perfectly, including punctuation.</li>
                    </ul>
                `;
            } else if (engine === "f5") {
                instructionsBox.className = "text-xs p-3 rounded mb-4 bg-purple-900/20 border border-purple-500/30 text-purple-300";
                instructionsBox.innerHTML = `
                    <strong class="text-purple-400 block mb-1">F5-TTS Voice Memory Rules:</strong>
                    <ul class="list-disc pl-4 space-y-1">
                        <li>Provide a clear, 10-15 second clean audio sample.</li>
                        <li>Ensure the transcript matches exactly.</li>
                    </ul>
                `;
            }
        }
    });
}

// 2. THE AUDIO DURATION VALIDATOR (Memory Crash Prevention)
const cloneFileInput = document.querySelector('#cloneVoiceModal input[type="file"]');
if (cloneFileInput && submitCloneBtn) {
    cloneFileInput.addEventListener("change", (e) => {
        const file = e.target.files[0];
        if (!file) return;

        // Only enforce strict duration limits for Fish-TTS
        if (state.ttsEngine === "fish") {
            try {
                // Create an invisible audio element to check duration instantly
                const audio = document.createElement('audio');
                const objectUrl = URL.createObjectURL(file);
                
                audio.addEventListener('loadedmetadata', () => {
                    const duration = audio.duration;
                    URL.revokeObjectURL(objectUrl); // Clean up memory
                    
                    if (duration < 8) {
                        showToast(`Audio is ${duration.toFixed(1)}s. Minimum 8s recommended for Fish-TTS.`, "error");
                        submitCloneBtn.disabled = true;
                        submitCloneBtn.classList.add("opacity-50", "cursor-not-allowed");
                        submitCloneBtn.textContent = "Audio Too Short";
                    } else if (duration > 30) {
                        showToast(`Audio is ${duration.toFixed(1)}s! Exceeds 30s strict limit. Please trim it.`, "error");
                        submitCloneBtn.disabled = true;
                        submitCloneBtn.classList.add("opacity-50", "cursor-not-allowed");
                        submitCloneBtn.textContent = "Exceeds 30s Limit";
                    } else {
                        submitCloneBtn.disabled = false;
                        submitCloneBtn.classList.remove("opacity-50", "cursor-not-allowed");
                        submitCloneBtn.textContent = "Create Voice Clone";
                        
                        if (duration > 20) {
                            showToast(`Audio is ${duration.toFixed(1)}s. Acceptable, but 8-20s is optimal.`, "warning");
                        } else {
                            showToast(`Audio is ${duration.toFixed(1)}s. Perfect length for Fish-TTS!`, "success");
                        }
                    }
                });
                
                audio.src = objectUrl;
            } catch (err) {
                console.error("Failed to parse audio duration", err);
            }
        } else {
            // Restore button for F5
            submitCloneBtn.disabled = false;
            submitCloneBtn.classList.remove("opacity-50", "cursor-not-allowed");
            submitCloneBtn.textContent = "Create Voice Clone";
        }
    });
}

// 3. API SUBMISSION & ROUTER
if (cloneVoiceForm) {
    cloneVoiceForm.onsubmit = async (e) => {
        e.preventDefault();
        const originalText = submitCloneBtn.innerHTML;
        
        submitCloneBtn.disabled = true;
        submitCloneBtn.innerHTML = 'Processing...';
        
        try {
            const formData = new FormData(cloneVoiceForm);
            
            // SURGICAL FIX: Tell the backend which engine to route this memory to!
            formData.append("engine", state.ttsEngine || "kokoro");

            const res = await fetch("/api/voices/clone", {
                method: "POST",
                body: formData
            });
            
            const data = await res.json();
            
            if (res.ok) {
                showToast(data.message || "Voice cloned successfully!", "success");
                document.getElementById("cloneVoiceModal").classList.add("hidden");
                cloneVoiceForm.reset();
                
                // Instantly update the dropdown so the user can use the voice right away
                if (typeof window.refreshVoiceDropdown === 'function') {
                    await window.refreshVoiceDropdown();
                }
            } else {
                throw new Error(data.detail || "Failed to clone voice");
            }
        } catch (err) {
            showToast("Cloning Error: " + err.message, "error");
        } finally {
            submitCloneBtn.disabled = false;
            submitCloneBtn.innerHTML = originalText;
        }
    };
}