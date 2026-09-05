
import { state, applyDocumentUiLang } from './state.js';
import { fetchJSON } from './api.js';

// --- Icon Management ---
export function renderIcons() {
    // Debounced icon rendering
    if (window.iconRenderTimeout) clearTimeout(window.iconRenderTimeout);
    window.iconRenderTimeout = setTimeout(() => {
        if (window.lucide) window.lucide.createIcons();
    }, 50);
}

// --- Text Helpers ---
export function stripHTML(text) {
    if (!text) return '';
    text = text.replace(/<[^>]*>/g, '');
    text = text.replace(/[\u{1F300}-\u{1F9FF}]|[\u{2600}-\u{26FF}]|[\u{2700}-\u{27BF}]|[\u{1F000}-\u{1F02F}]|[\u{1F0A0}-\u{1F0FF}]/gu, '');
    return text.trim();
}

export function setMonitorPreview(text, { center = false } = {}) {
    const apply = (el) => {
        if (!el) return;
        el.textContent = text ?? "";
        if (center) {
            el.classList.add("player-text-center");
            return;
        }
        el.classList.remove("player-text-center");
        requestAnimationFrame(() => {
            if (el.textContent !== (text ?? "")) return;
            const style = getComputedStyle(el);
            let lineHeight = parseFloat(style.lineHeight);
            if (!Number.isFinite(lineHeight) || style.lineHeight === "normal") {
                lineHeight = parseFloat(style.fontSize) * 1.5 || 24;
            }
            const lines = el.scrollHeight / lineHeight;
            el.classList.toggle("player-text-center", lines < 1.6);
        });
    };
    apply(document.getElementById("currentSentencePreview"));
    apply(document.getElementById("monitorSentenceText"));
}

// --- Toast ---
export function showToast(msg) {
    const toast = document.getElementById('toast');
    const toastMsg = document.getElementById('toastMsg');
    if (toast && toastMsg) {
        toastMsg.textContent = msg;
        toast.classList.remove('hidden');
        setTimeout(() => toast.classList.add('hidden'), 5000);
    }
}

// --- Drawer Management ---
export function toggleSettingsDrawer(drawerId, open) {
    const drawer = typeof drawerId === "string" ? document.getElementById(drawerId) : drawerId;
    const overlay = document.getElementById("drawerOverlay");
    if (open) {
        document.querySelectorAll(".voice-settings-drawer").forEach(d => {
            if (d !== drawer) d.classList.remove("open");
        });
        drawer?.classList.add("open");
        overlay?.classList.add("active");
        renderIcons();
        document.dispatchEvent(new CustomEvent("settings-drawer-change", { detail: { open: true } }));
    } else {
        drawer?.classList.remove("open");
        const anyOpen = Array.from(document.querySelectorAll(".voice-settings-drawer")).some(d => d.classList.contains("open"));
        if (!anyOpen) {
            overlay?.classList.remove("active");
            document.dispatchEvent(new CustomEvent("settings-drawer-change", { detail: { open: false } }));
        }
    }
}

export function closeAllDrawers() {
    document.querySelectorAll(".voice-settings-drawer").forEach(d => d.classList.remove("open"));
    document.getElementById("drawerOverlay")?.classList.remove("active");
    document.dispatchEvent(new CustomEvent("settings-drawer-change", { detail: { open: false } }));
}

// --- Tabs ---
export function switchTab(activeTab, activePanel) {
    const tabs = [document.getElementById('tabLibrary'), document.getElementById('tabRules'), document.getElementById('tabIgnore')];
    const panels = [document.getElementById('libraryPanel'), document.getElementById('rulesPanel'), document.getElementById('ignorePanel')];
    const activeClass = "flex-1 py-3 flex items-center justify-center gap-1.5 text-[10px] font-bold uppercase tracking-widest border-b-2 border-blue-600 text-blue-500 bg-white/5";
    const inactiveClass = "flex-1 py-3 flex items-center justify-center gap-1.5 text-[10px] font-bold uppercase tracking-widest border-b-2 border-transparent text-zinc-500 hover:text-zinc-300";

    tabs.forEach(tab => {
        if (tab) tab.className = tab === activeTab ? activeClass : inactiveClass;
    });
    panels.forEach(panel => {
        if (panel) panel.classList.toggle('hidden', panel !== activePanel);
    });
    renderIcons();
}

// --- Rules Rendering ---
export function renderRules() {
    const rulesList = document.getElementById('rulesList');
    if (!rulesList) return;

    const fragment = document.createDocumentFragment();
    state.rules.forEach(r => {
        const isExpanded = r.isExpanded || false;
        const div = document.createElement('div');
        div.className = `rule-item bg-zinc-900/80 rounded-xl border border-zinc-800 ${isExpanded ? 'rule-expanded' : ''}`;

        const hasOriginal = r.original && r.original.trim();
        const hasReplacement = r.replacement && r.replacement.trim();
        const isEmpty = !hasOriginal && !hasReplacement;

        const escapeHtml = (text) => {
            const d = document.createElement('div');
            d.textContent = text;
            return d.innerHTML;
        };

        const originalText = hasOriginal ? escapeHtml(r.original) : '<span class="rule-empty">(Empty)</span>';
        const replacementText = hasReplacement ? escapeHtml(r.replacement) : '<span class="rule-empty">(Empty)</span>';

        div.innerHTML = `
            <div class="rule-collapsed p-3 flex items-center" data-action="toggle-rule" data-id="${r.id}">
                ${isEmpty ?
                '<div class="flex-1"><span class="text-xs rule-empty">Empty rule - click to edit</span></div>' :
                `<div class="rule-original text-xs">${originalText}</div>
                     <div class="rule-arrow"><i data-lucide="arrow-right" class="w-3 h-3"></i></div>
                     <div class="rule-replacement text-xs">${replacementText}</div>`
            }
                <div class="rule-meta">
                    ${r.match_case ? '<span class="rule-badge bg-blue-600/20 text-blue-400">Case</span>' : ''}
                    ${r.word_boundary ? '<span class="rule-badge bg-green-600/20 text-green-400">Word</span>' : ''}
                    ${r.is_regex ? '<span class="rule-badge bg-purple-600/20 text-purple-400">Regex</span>' : ''}
                    <i data-lucide="${isExpanded ? 'chevron-up' : 'chevron-down'}" class="w-4 h-4 text-zinc-500 ml-2"></i>
                </div>
            </div>
            <div class="rule-content ${isExpanded ? 'expanded' : ''}">
                <div class="px-3 pb-3 space-y-3">
                    <div class="h-px bg-zinc-800"></div>
                    <div class="grid grid-cols-1 gap-2">
                        <input type="text" placeholder="Original Text" value="${r.original}" class="bg-black text-xs p-2.5 border border-zinc-800 rounded-md text-zinc-300 placeholder-zinc-600" data-action="update-rule" data-field="original" data-id="${r.id}">
                        <input type="text" placeholder="Replacement Text" value="${r.replacement}" class="bg-black text-xs p-2.5 border border-zinc-800 rounded-md text-zinc-300 placeholder-zinc-600" data-action="update-rule" data-field="replacement" data-id="${r.id}">
                    </div>
                    <div class="space-y-2">
                        <label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer hover:text-zinc-300">
                            <input type="checkbox" ${r.match_case ? 'checked' : ''} data-action="update-rule" data-field="match_case" data-id="${r.id}" class="w-3.5 h-3.5 rounded border-zinc-700 bg-zinc-800 text-blue-600 focus:ring-blue-600 focus:ring-offset-0">
                            <span>Match Case</span>
                        </label>
                        <label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer hover:text-zinc-300">
                            <input type="checkbox" ${r.word_boundary ? 'checked' : ''} data-action="update-rule" data-field="word_boundary" data-id="${r.id}" class="w-3.5 h-3.5 rounded border-zinc-700 bg-zinc-800 text-blue-600 focus:ring-blue-600 focus:ring-offset-0">
                            <span>Whole Word</span>
                        </label>
                        <label class="flex items-center gap-2 text-xs text-zinc-400 cursor-pointer hover:text-zinc-300">
                            <input type="checkbox" ${r.is_regex ? 'checked' : ''} data-action="update-rule" data-field="is_regex" data-id="${r.id}" class="w-3.5 h-3.5 rounded border-zinc-700 bg-zinc-800 text-blue-600 focus:ring-blue-600 focus:ring-offset-0">
                            <span>Use Pattern Matching</span>
                        </label>
                    </div>
                    <div class="flex justify-between items-center pt-2">
                        <button data-action="toggle-rule" data-id="${r.id}" class="text-xs text-zinc-500 hover:text-zinc-300 flex items-center gap-1">
                            <i data-lucide="check" class="w-3 h-3"></i>
                            <span>Done</span>
                        </button>
                        <button data-action="delete-rule" data-id="${r.id}" class="text-zinc-600 hover:text-red-500 p-1.5">
                            <i data-lucide="trash-2" class="w-3.5 h-3.5"></i>
                        </button>
                    </div>
                </div>
            </div>
        `;
        fragment.appendChild(div);
    });
    rulesList.innerHTML = '';
    rulesList.appendChild(fragment);
    renderIcons();

    // Attach event listeners for the newly created elements
    // Note: We use global delegation in app.js for better performance, 
    // but the inputs need 'change' events. 
    // We'll rely on app.js to handle these via delegation on #rulesList
}

// --- Ignore List Rendering ---
export function renderIgnoreList() {
    const ignoreListUI = document.getElementById('ignoreListUI');
    if (!ignoreListUI) return;

    const fragment = document.createDocumentFragment();
    state.ignoreList.forEach((item, i) => {
        const div = document.createElement('div');
        div.className = 'flex items-center gap-2 bg-zinc-900/80 p-2 rounded-lg border border-zinc-800';
        div.innerHTML = `<input type="text" value="${item}" class="flex-1 bg-black text-[10px] p-1.5 border border-zinc-800 rounded outline-none text-zinc-300" data-action="update-ignore" data-index="${i}">
                         <button data-action="delete-ignore" data-index="${i}" class="text-zinc-600 hover:text-red-500 p-1"><i data-lucide="x" class="w-3.5 h-3.5"></i></button>`;
        fragment.appendChild(div);
    });
    ignoreListUI.innerHTML = '';
    ignoreListUI.appendChild(fragment);
    renderIcons();
}

// --- Search ---
export function escapeRegex(str) {
    let escaped = str.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
    
    // Make search immune to smart quotes vs straight quotes
    escaped = escaped.replace(/['‘’´`]/g, "['‘’´`]");
    escaped = escaped.replace(/["“”]/g, '["“”]');
    
    return escaped;
}

export function highlightSearchTerm(query, matchCase = false, wholeWord = false) {
    const textContent = document.getElementById('textContent');
    if (!query || !textContent) return;

    // 🌟 STRICT PAGE GATE FIX: 
    // If the user turns the page away from the search result, stop highlighting immediately!
    // This absolutely kills the "ghosting whole book" bug.
    if (state.searchTargetPage !== undefined && state.viewPageIndex !== state.searchTargetPage) {
        return; 
    }

    const textElements = textContent.querySelectorAll('.sentence');
    const escapedQuery = escapeRegex(query);
    
    let pattern = escapedQuery.replace(/\s+/g, '\\s+');
    if (wholeWord) pattern = `\\b${pattern}\\b`;
    
    const regex = new RegExp(`(${pattern})`, matchCase ? 'g' : 'gi');

    // 🌟 ONLY highlight if a specific snippet was clicked
    if (state.searchTargetSnippet) {
        let bestEl = null;
        let maxScore = 0; // Score must be strictly greater than 0 to prevent random matching
        
        // Fingerprint the snippet
        const snippetWords = stripHTML(state.searchTargetSnippet)
            .toLowerCase()
            .replace(/[^\p{L}\p{N}\s]/gu, ' ')
            .split(/\s+/)
            .filter(w => w.length > 2);
        
        textElements.forEach(el => {
            regex.lastIndex = 0;
            
            if (regex.test(el.textContent)) {
                let score = 0;
                const sentenceText = el.textContent.toLowerCase();
                
                snippetWords.forEach(w => {
                    if (sentenceText.includes(w)) score++;
                });
                
                if (score > maxScore) {
                    maxScore = score;
                    bestEl = el;
                }
            }
        });
        
        // Highlight ONLY the single exact sentence the user clicked
        if (bestEl) {
            regex.lastIndex = 0;
            highlightTextNodes(bestEl, regex);
        }
    } 
}

// Wrap matches using flattened textContent offsets so a phrase that
// crosses inline tags (e.g. the ne<em>xt</em> day) stays contiguous —
// no extra spaces are inserted between text nodes.
function highlightTextNodes(root, regex) {
    const collectPieces = () => {
        const pieces = [];
        const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
        let currentNode;
        let haystack = '';
        while ((currentNode = walker.nextNode())) {
            const text = currentNode.textContent;
            if (!text) continue;
            pieces.push({ node: currentNode, start: haystack.length, length: text.length });
            haystack += text;
        }
        return { pieces, haystack };
    };

    const { haystack } = collectPieces();
    regex.lastIndex = 0;
    const matches = [];
    let match;
    while ((match = regex.exec(haystack)) !== null) {
        if (!match[0]) {
            regex.lastIndex += 1;
            continue;
        }
        matches.push({ start: match.index, end: match.index + match[0].length });
    }

    for (let i = matches.length - 1; i >= 0; i--) {
        const { pieces } = collectPieces();
        wrapMatchAcrossNodes(pieces, matches[i].start, matches[i].end);
    }
}

function wrapMatchAcrossNodes(pieces, start, end) {
    for (let p = pieces.length - 1; p >= 0; p--) {
        const piece = pieces[p];
        const pieceEnd = piece.start + piece.length;
        const from = Math.max(start, piece.start);
        const to = Math.min(end, pieceEnd);
        if (from >= to) continue;
        wrapTextNodeSlice(piece.node, from - piece.start, to - piece.start);
    }
}

function wrapTextNodeSlice(textNode, start, end) {
    if (!textNode || !textNode.parentNode) return;
    if (textNode.parentNode.classList && textNode.parentNode.classList.contains('search-highlight')) return;
    const text = textNode.textContent;
    if (start < 0 || end > text.length || start >= end) return;

    let target = textNode;
    if (end < text.length) target.splitText(end);
    if (start > 0) target = target.splitText(start);

    const highlight = document.createElement('span');
    highlight.className = 'search-highlight';
    target.parentNode.replaceChild(highlight, target);
    highlight.appendChild(target);
}

// --- Setup/Status ---
// --- Translations ---
export async function updateTranslations(lang) {
    applyDocumentUiLang(lang);
    try {
        const translations = await fetchJSON(`/api/locale/${lang}`);
        state.translations = translations; // Store in global state
        document.querySelectorAll('[data-i18n]').forEach(el => {
            const key = el.dataset.i18n;
            const keys = key.split('.');
            let val = translations;
            for (const k of keys) {
                val = val ? val[k] : null;
            }
            if (val) el.textContent = val;
        });
        document.querySelectorAll('[data-i18n-title]').forEach(el => {
            const key = el.dataset.i18nTitle;
            const keys = key.split('.');
            let val = translations;
            for (const k of keys) {
                val = val ? val[k] : null;
            }
            if (val) el.title = val;
        });
    } catch (e) { console.error("Translation error", e); }
}

export function updateEngineStatusUI(status, selectedModelExists) {
    const engineStatusDot = document.getElementById('engineStatusDot');
    const setupArea = document.getElementById('setupArea');
    const setupBtn = document.getElementById('setupBtn');
    const exportArea = document.getElementById('exportArea');

    const gpuReady = !!status.available_models?.gpu;
    const cpuReady = !!status.available_models?.cpu;
    const voicesReady = !!status.available_models?.voices;
    const hasAnyModel = gpuReady || cpuReady;

    const gpuStatusEl = document.getElementById('gpuStatus');
    const cpuStatusEl = document.getElementById('cpuStatus');

    if (gpuStatusEl) {
        gpuStatusEl.innerHTML = `FP32: <span class="${gpuReady ? 'text-green-400' : 'text-zinc-600'}">${gpuReady ? '✓' : '✗'}</span>`;
    }
    if (cpuStatusEl) {
        cpuStatusEl.innerHTML = `INT8: <span class="${cpuReady ? 'text-green-400' : 'text-zinc-600'}">${cpuReady ? '✓' : '✗'}</span>`;
    }

    renderEngineDownloadPopup(status);

    const setEngineDot = (className) => {
        if (engineStatusDot) engineStatusDot.className = `sidebar-status-dot ${className}`;
    };

    if (status.is_downloading) {
        setEngineDot("w-2.5 h-2.5 rounded-full bg-blue-500 animate-pulse");
        if (setupArea) setupArea.style.display = 'block';
        if (setupBtn) {
            setupBtn.disabled = true;
            setupBtn.innerHTML = '<i data-lucide="loader-2" class="w-3.5 h-3.5 animate-spin"></i><span class="text-xs font-bold">Downloading...</span>';
        }
        renderIcons();
    } else if (status.is_loading && hasAnyModel && voicesReady) {
        setEngineDot("w-2.5 h-2.5 rounded-full bg-yellow-500 animate-pulse");
        if (setupArea) setupArea.style.display = 'none';
    } else if (status.model_loaded && selectedModelExists && voicesReady) {
        setEngineDot("w-2.5 h-2.5 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.6)]");
        if (setupArea) setupArea.style.display = 'none';
        if (state.currentDoc) {
            if (exportArea) exportArea.style.display = 'block';
        }
    } else {
        setEngineDot("w-2.5 h-2.5 rounded-full bg-red-600");
        if (setupArea) setupArea.style.display = 'block';
        if (setupBtn) {
            setupBtn.disabled = false;
            setupBtn.innerHTML = '<i data-lucide="download-cloud" class="w-3.5 h-3.5"></i><span class="text-xs font-bold">Setup Voice Engine</span>';
        }
        renderIcons();
    }
}

function renderEngineDownloadPopup(status) {
    const dlModel = status.downloading_model; // "gpu", "cpu", or null

    const gpuDownloading = status.is_downloading && dlModel === "gpu";
    const cpuDownloading = status.is_downloading && dlModel === "cpu";

    const gpuMeta = status.available_models?.gpu
        ? "Ready"
        : gpuDownloading ? "Downloading…" : "Download";
    const cpuMeta = status.available_models?.cpu
        ? "Ready"
        : cpuDownloading ? "Downloading…" : "Download";

    const html = `
      <div class="engine-download-title">Download Kokoro model</div>
      <button type="button" class="sidebar-mini-item engine-dl-btn" data-download-model="gpu" ${gpuDownloading || status.available_models?.gpu ? "disabled" : ""}>
        <span class="engine-dl-name">FP32 <span class="text-[10px] text-green-400 font-normal">(Recommended)</span></span>
        <span class="engine-dl-meta">${gpuMeta}</span>
      </button>
      <button type="button" class="sidebar-mini-item engine-dl-btn" data-download-model="cpu" ${cpuDownloading || status.available_models?.cpu ? "disabled" : ""}>
        <span class="engine-dl-name">INT8</span>
        <span class="engine-dl-meta">${cpuMeta}</span>
      </button>
    `;
    ["engineStatusPopup", "kokoroDownloadPopup"].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = html;
    });
}

function showInt8WarningModal(onConfirm, onChooseFP32) {
    let modal = document.getElementById("int8WarningModal");
    if (!modal) {
        modal = document.createElement("div");
        modal.id = "int8WarningModal";
        modal.className = "fixed inset-0 z-[9999] flex items-center justify-center bg-black/70 backdrop-blur-sm p-4";
        document.body.appendChild(modal);
    }
    modal.innerHTML = `
      <div class="bg-zinc-900 border border-zinc-700 rounded-xl p-5 max-w-md w-full shadow-2xl space-y-4 text-white">
        <div class="flex items-center gap-2.5 text-amber-400 font-bold text-sm">
          <svg class="w-5 h-5 flex-shrink-0" fill="none" stroke="currentColor" stroke-width="2" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z"/>
          </svg>
          <span>Hardware Acceleration Notice</span>
        </div>
        <div class="text-xs text-zinc-300 leading-relaxed space-y-2">
          <p>Please try <strong class="text-green-400">FP32</strong> first!</p>
          <p>The <strong class="text-zinc-100">INT8</strong> model requires specialized CPU hardware acceleration (<code class="bg-zinc-800 px-1 py-0.5 rounded text-zinc-300 font-mono text-[11px]">AVX-512 VNNI</code>). Without it, INT8 is actually <span class="text-red-400 font-semibold">slower and uses more CPU</span> than FP32.</p>
        </div>
        <div class="flex flex-col gap-2 pt-2 text-xs">
          <button id="int8UseFp32Btn" class="w-full py-2 px-3 bg-blue-600 hover:bg-blue-500 font-semibold text-white rounded-lg transition-colors flex items-center justify-center gap-1.5 shadow-md">
            Download FP32 Instead (Recommended)
          </button>
          <div class="flex items-center justify-between gap-2 pt-1">
            <button id="int8CancelBtn" class="py-1.5 px-3 bg-zinc-800 hover:bg-zinc-700 text-zinc-300 rounded-lg transition-colors">
              Cancel
            </button>
            <button id="int8ConfirmBtn" class="py-1.5 px-3 text-zinc-400 hover:text-zinc-200 text-[11px] underline transition-colors">
              Download INT8 Anyway
            </button>
          </div>
        </div>
      </div>
    `;
    modal.classList.remove("hidden");

    const close = () => modal.classList.add("hidden");

    modal.querySelector("#int8UseFp32Btn").onclick = () => {
        close();
        if (onChooseFP32) onChooseFP32();
    };
    modal.querySelector("#int8ConfirmBtn").onclick = () => {
        close();
        if (onConfirm) onConfirm();
    };
    modal.querySelector("#int8CancelBtn").onclick = () => {
        close();
    };
    modal.onclick = (e) => {
        if (e.target === modal) close();
    };
}

async function startKokoroDownload(modelType) {
    if (modelType !== "gpu" && modelType !== "cpu") return;
    try {
        await fetchJSON(`/api/system/setup?model_type=${modelType}`, { method: "POST" });
        showToast(modelType === "gpu" ? "Downloading FP32 model…" : "Downloading INT8 model…");
    } catch (e) {
        showToast(e.message || "Download failed");
    }
}

let engineDownloadWired = false;
function wireEngineDownloadPopup() {
    if (engineDownloadWired) return;
    engineDownloadWired = true;
    const onPick = (e) => {
        e.stopPropagation();
        const btn = e.target.closest("[data-download-model]");
        if (!btn || btn.disabled) return;
        const modelType = btn.dataset.downloadModel;
        // Close the popup immediately after the user picks a model
        const popup = e.currentTarget;
        if (popup) popup.classList.add("hidden");

        if (modelType === "cpu") {
            showInt8WarningModal(
                () => startKokoroDownload("cpu"),
                () => startKokoroDownload("gpu")
            );
        } else {
            startKokoroDownload("gpu");
        }
    };
    document.getElementById("engineStatusPopup")?.addEventListener("click", onPick);
    document.getElementById("kokoroDownloadPopup")?.addEventListener("click", onPick);
}

wireEngineDownloadPopup();
//Popup footnote UI
export function showFootnoteModal(htmlContent, onJumpCallback) {
    let modal = document.getElementById('footnoteModal');

    if (!modal) {
        modal = document.createElement('div');
        modal.id = 'footnoteModal';
        modal.style.cssText = `
            position: fixed;
            inset: 0;
            z-index: 9999;
            display: flex;
            align-items: center;
            justify-content: center;
            background: rgba(0, 0, 0, 0.65);
            -webkit-backdrop-filter: blur(6px);
            backdrop-filter: blur(6px);
            padding: clamp(16px, 2vw, 32px);
            opacity: 0;
            pointer-events: none;
            transition: opacity 0.2s ease;
        `;

        modal.innerHTML = `
            <div style="
                background: #121214;
                border: 1px solid #3f3f46;
                border-radius: 12px;
                width: min(92vw, 850px);
                min-height: 200px;
                max-height: 88vh;
                display: flex;
                flex-direction: column;
                box-shadow: 0 24px 60px rgba(0,0,0,0.6);
                transform: scale(0.97);
                transition: transform 0.2s cubic-bezier(0.16, 1, 0.3, 1);
            ">
                <div style="
                    display: flex;
                    justify-content: space-between;
                    align-items: center;
                    padding: clamp(12px, 1.5vh, 18px) clamp(16px, 2.5vw, 28px);
                    border-bottom: 1px solid #27272a;
                    background: #18181b;
                    border-radius: 12px 12px 0 0;
                    flex-shrink: 0;
                ">
                    <span style="
                        font-size: clamp(11px, 1vw, 13px);
                        font-weight: 600;
                        color: #a1a1aa;
                        letter-spacing: 0.08em;
                        text-transform: uppercase;
                    ">Reference Note</span>
                    
                    <div style="display: flex; gap: clamp(8px, 1vw, 16px); align-items: center;">
                        <button id="jumpFootnoteBtn" style="
                            background: #2563eb;
                            color: white;
                            border: none;
                            border-radius: 6px;
                            padding: clamp(6px, 1vh, 8px) clamp(12px, 1.5vw, 18px);
                            font-size: clamp(11px, 1vw, 13px);
                            font-weight: bold;
                            cursor: pointer;
                            transition: background 0.2s;
                        ">Jump to Note ⏎</button>
                        
                        <button id="closeFootnoteBtn" style="
                            background: none;
                            border: none;
                            color: #71717a;
                            font-size: clamp(18px, 2vw, 26px);
                            line-height: 1;
                            cursor: pointer;
                            padding: 2px 6px;
                            border-radius: 6px;
                        ">&times;</button>
                    </div>
                </div>

                <div id="footnoteModalContent" style="
                    padding: clamp(20px, 3.5vh, 40px) clamp(24px, 4.5vw, 56px);
                    overflow-y: auto;
                    color: #e4e4e7;
                    font-size: clamp(15px, 1.15vw, 24.5px);
                    line-height: 1.75;
                    font-weight: 400;
                    overscroll-behavior: contain;
                "></div>
            </div>
        `;

        document.body.appendChild(modal);
    }
    
    window.currentFootnoteJump = onJumpCallback;
    
    const close = () => {
        modal.style.opacity = '0';
        modal.style.pointerEvents = 'none';
        modal.querySelector('div').style.transform = 'scale(0.97)';
    };

    const handleKey = (e) => {
        if (modal.style.opacity !== '1') return;
        if (e.key === 'Enter' && window.currentFootnoteJump) {
            e.preventDefault();
            window.currentFootnoteJump();
            close();
        } else if (e.key === 'Escape') {
            close();
        }
    };
    
    if (window._footnoteKeyHandler) window.removeEventListener('keydown', window._footnoteKeyHandler);
    window._footnoteKeyHandler = handleKey;
    window.addEventListener('keydown', handleKey);

    document.getElementById('jumpFootnoteBtn').onclick = () => {
        if (window.currentFootnoteJump) window.currentFootnoteJump();
        close();
    };
    document.getElementById('closeFootnoteBtn').onclick = close;
    modal.onclick = (e) => { if (e.target === modal) close(); };

    const content = document.getElementById('footnoteModalContent');
    content.innerHTML = htmlContent;

    const charCount = content.textContent.length;
    if (charCount < 100) {
        content.style.fontSize = 'clamp(20px, 1.5vw, 24px)';
        content.style.lineHeight = '1.6';
    } else if (charCount > 500) {
        content.style.fontSize = 'clamp(16px, 1vw, 20px)';
        content.style.lineHeight = '1.5';
    } else {
        content.style.fontSize = 'clamp(15px, 1.15vw, 17.5px)';
        content.style.lineHeight = '1.75';
    }

    content.querySelectorAll('p').forEach(p => {
        p.style.margin = '0 0 1.25em 0';
    });

    content.querySelectorAll('a').forEach(a => {
        const text = a.textContent.trim();
        const cleanText = text.replace(/[\[\]\(\)]/g, '');
        
        if (text === '↩' || text === '↑' || text.toLowerCase() === 'return' || a.getAttribute('epub:type') === 'backlink') {
            a.remove();
        } else if (cleanText.length <= 3 && /^\d+$/.test(cleanText)) {
            const span = document.createElement('span');
            span.style.cssText = 'color:#60a5fa;font-weight:bold;margin-right:6px;';
            span.textContent = `[${cleanText}]`;
            a.replaceWith(span);
        } else {
            a.removeAttribute('href');
            a.style.cssText = 'color:#60a5fa;text-decoration:underline;cursor:text;';
        }
    });

    modal.style.opacity = '1';
    modal.style.pointerEvents = 'auto';
    modal.querySelector('div').style.transform = 'scale(1)';
}

export function syncBackToReadingButton() {
    const backToReadingBtn = document.getElementById("backToReadingBtn");
    const hiddenModeBackBtn = document.getElementById("hiddenModeBackBtn");
    if (!backToReadingBtn && !hiddenModeBackBtn) return;

    const isOutOfFocus = (state.viewPageIndex !== state.readingPageIndex) || !state.autoScrollEnabled;
    const isPlaybarHidden = state.playerHideMode === "manual" && document.getElementById("controls")?.classList.contains("minimized");

    if (isOutOfFocus) {
        if (isPlaybarHidden) {
            if (backToReadingBtn) {
                backToReadingBtn.classList.add("hidden");
                backToReadingBtn.classList.remove("flex");
            }
            if (hiddenModeBackBtn) {
                hiddenModeBackBtn.classList.replace("opacity-0", "opacity-100");
                hiddenModeBackBtn.classList.replace("pointer-events-none", "pointer-events-auto");
            }
        } else {
            if (hiddenModeBackBtn) {
                hiddenModeBackBtn.classList.replace("opacity-100", "opacity-0");
                hiddenModeBackBtn.classList.replace("pointer-events-auto", "pointer-events-none");
            }
            if (backToReadingBtn) {
                backToReadingBtn.classList.remove("hidden");
                backToReadingBtn.classList.add("flex");
            }
        }
    } else {
        if (backToReadingBtn) {
            backToReadingBtn.classList.add("hidden");
            backToReadingBtn.classList.remove("flex");
        }
        if (hiddenModeBackBtn) {
            hiddenModeBackBtn.classList.replace("opacity-100", "opacity-0");
            hiddenModeBackBtn.classList.replace("pointer-events-auto", "pointer-events-none");
        }
    }
}