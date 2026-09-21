import { state, normalizeBcp47, langFromHtmlMarkup, guessLangFromText } from "./state.js";
import { fetchJSON, fetchBlob } from "./api.js";
import { showToast, renderIcons, stripHTML, highlightSearchTerm, showFootnoteModal, setMonitorPreview, syncBackToReadingButton } from "./ui.js";
import { applyReaderTypography, getRenderState } from "./typography.js";
import { isHorizontalMode, layoutSpreads, revealInSpread, updateHorizontalSpreadFocus, wantsSpreadTwoPage, isStandaloneIllustrationPage, getActiveSpreadPages } from "./horizontal.js";
import { indexDocument, updateProgressDisplay, getProgressMetrics, setRamIndexReady, isRamIndexReady } from "./progress.js";

// Tags that survive EPUB/PDF restore; stripped when extracting spoken/preview text.
export const validTags = /<\/?(?:n|s|p|div|h[1-6]|span|font|a|b|i|u|em|strong|ins|del|strike|sub|sup|mark|small|big|abbr|cite|dfn|q|code|pre|ruby|rt|rp|figure|figcaption|blockquote|img|image|svg|picture|hr|br|li|ul|ol|table|caption|tr|td|th|tbody|thead|tfoot|section|article|aside|nav|main|header|footer|address|dd|dt|summary)\b[^>]*>/gi;

export function hasBookPath(item) {
    return Boolean(item && (item.source_path || item.path));
}

function isMissingBookError(err) {
    const msg = String(err?.message || err || "").toLowerCase();
    return msg.includes("book missing from path") || msg.includes("source epub file missing");
}

function markPeekEpub(item) {
    if (!item) return item;
    if (item.source_path || item.path || item.is_peek) {
        item.bookType = "epub";
        item.is_peek = true;
    }
    return item;
}

function pageHasAnchorId(pageHtml, anchorId) {
    if (!pageHtml || !anchorId) return false;
    const id = String(anchorId);
    return (
        pageHtml.includes(`id="${id}"`) ||
        pageHtml.includes(`id='${id}'`) ||
        pageHtml.includes(`name="${id}"`) ||
        pageHtml.includes(`name='${id}'`)
    );
}

function pageHasOrigAnchor(pageHtml, anchorId) {
    if (!pageHtml || !anchorId) return false;
    const id = String(anchorId);
    return (
        pageHtml.includes(`data-orig-id="${id}"`) ||
        pageHtml.includes(`data-orig-id='${id}'`)
    );
}

function footnotePointerSelector(anchorId, peeked) {
    const escaped = escapeCssAttr(anchorId);
    if (peeked) {
        return `[data-orig-id="${escaped}"], [id="${escaped}"], [name="${escaped}"]`;
    }
    return `[id="${escaped}"], [name="${escaped}"]`;
}

function findAnchorElement(root, anchorId, peeked) {
    if (!root || !anchorId) return null;
    const escaped = escapeCssAttr(anchorId);
    if (peeked) {
        return root.querySelector(`[data-orig-id="${escaped}"]`)
            || root.getElementById?.(anchorId)
            || root.querySelector(`[id="${escaped}"]`)
            || root.querySelector(`[name="${escaped}"]`);
    }
    return root.getElementById?.(anchorId)
        || root.querySelector(`[id="${escaped}"]`)
        || root.querySelector(`[name="${escaped}"]`);
}

function extractFootnoteHtml(targetEl) {
    if (!targetEl) return null;
    const container = targetEl.closest('aside, li, p[epub\\:type="footnote"], div[epub\\:type="footnote"], .epub-footnote')
        || targetEl.closest('p')
        || targetEl.parentElement;
    return container ? container.innerHTML : targetEl.innerHTML;
}

function findFootnoteInRamPages(cleanId, peeked) {
    if (!state.currentPages || !cleanId) return { html: null, pageIdx: -1 };
    const searchOrder = [state.viewPageIndex];
    for (let i = state.viewPageIndex + 1; i < state.currentPages.length; i++) searchOrder.push(i);
    for (let i = state.viewPageIndex - 1; i >= 0; i--) searchOrder.push(i);

    for (const idx of searchOrder) {
        const pageHtml = state.currentPages[idx];
        if (!pageHtml) continue;
        const hit = peeked
            ? (pageHasOrigAnchor(pageHtml, cleanId) || pageHasAnchorId(pageHtml, cleanId))
            : pageHasAnchorId(pageHtml, cleanId);
        if (!hit) continue;

        const parser = new DOMParser();
        const doc = parser.parseFromString(pageHtml, "text/html");
        const targetEl = findAnchorElement(doc, cleanId, peeked);
        if (!targetEl) continue;
        return { html: extractFootnoteHtml(targetEl), pageIdx: idx };
    }
    return { html: null, pageIdx: -1 };
}

function footnoteBasename(path) {
    if (!path) return "";
    const noQuery = String(path).split("?")[0];
    const parts = noQuery.split("/");
    return parts[parts.length - 1] || "";
}

function unwrapFootnoteRecord(raw, fallbackId = "") {
    if (!raw) return null;
    if (typeof raw === "string") {
        return { html: raw, page_index: -1, file: "", anchor_id: fallbackId };
    }
    if (typeof raw === "object") {
        return raw;
    }
    return null;
}

function lookupFootnoteMap(cleanId, targetId, chBase) {
    if (!state.footnoteMap) return null;
    const base = footnoteBasename(chBase);
    const keys = [
        targetId,
        base && cleanId ? `${base}#${cleanId}` : "",
        chBase && cleanId ? `${chBase}#${cleanId}` : "",
        cleanId,
    ].filter(Boolean);
    for (const key of keys) {
        if (state.footnoteMap[key]) return unwrapFootnoteRecord(state.footnoteMap[key], cleanId);
    }
    const lower = (value) => String(value || "").toLowerCase();
    const wanted = new Set(keys.map(lower));
    for (const [key, value] of Object.entries(state.footnoteMap)) {
        if (wanted.has(lower(key))) return unwrapFootnoteRecord(value, cleanId);
    }
    return null;
}

const _peekInflight = new Map();
let _renderPageSeq = 0;

async function ensurePeekedPage(pageIdx) {
    if (!Number.isInteger(pageIdx) || pageIdx < 0) return false;
    if (!state.currentPages) return false;
    if (state.currentPages[pageIdx]) return true;
    if (!state.currentDoc?.id) return false;

    const key = `${state.currentDoc.id}:${pageIdx}`;
    const pending = _peekInflight.get(key);
    if (pending) return pending;

    const request = (async () => {
        try {
            const chapter = await fetchJSON(`/api/books/${state.currentDoc.id}/chapter/${pageIdx}`);
            if (chapter?.html) {
                if (!state.currentPages) return false;
                while (state.currentPages.length <= pageIdx) state.currentPages.push("");
                state.currentPages[pageIdx] = chapter.html;
                if (isRamIndexReady()) indexDocument(state.currentPages);
                return true;
            }
        } catch (e) {}
        return false;
    })();

    _peekInflight.set(key, request);
    try {
        return await request;
    } finally {
        _peekInflight.delete(key);
    }
}

async function ensureViewPagesReady(pageIdx) {
    if (!hasBookPath(state.currentDoc)) return Boolean(state.currentPages?.[pageIdx]);
    const targets = new Set([pageIdx]);
    try {
        if (isHorizontalMode() && wantsSpreadTwoPage()) {
            for (const idx of getActiveSpreadPages(pageIdx)) {
                if (Number.isInteger(idx) && idx >= 0) targets.add(idx);
            }
        }
    } catch (e) {}
    await Promise.all([...targets].map((idx) => ensurePeekedPage(idx)));
    return Boolean(state.currentPages?.[pageIdx]);
}

function highlightFootnoteTarget(cleanId, peeked) {
    const targetEl = findAnchorElement(document, cleanId, peeked);
    if (!targetEl) return;
    const scrollTarget = targetEl.closest(".sentence") || targetEl;
    scrollTarget.scrollIntoView({ behavior: "smooth", block: "center" });
    scrollTarget.classList.add("bg-blue-600/40", "rounded", "px-1", "transition-colors", "duration-500");
    setTimeout(() => scrollTarget.classList.remove("bg-blue-600/40", "px-1"), 1500);
}

async function openFootnote(targetId) {
    if (!targetId) return;

    const parts = targetId.split("#");
    const targetFile = parts.length > 1 ? parts[0] : "";
    const cleanId = parts.pop();
    const chBase = targetFile ? footnoteBasename(targetFile) : "";
    const peeked = hasBookPath(state.currentDoc);
    let footnoteHTML = null;
    let foundPageIdx = -1;
    let jumpAnchor = cleanId;

    if (peeked) {
        const rec = lookupFootnoteMap(cleanId, targetId, chBase);
        if (rec) {
            footnoteHTML = rec.html || null;
            if (Number.isInteger(rec.page_index)) foundPageIdx = rec.page_index;
            jumpAnchor = rec.anchor_id || cleanId;
        }
        if (!footnoteHTML) {
            const ramHit = findFootnoteInRamPages(cleanId, true);
            footnoteHTML = ramHit.html;
            foundPageIdx = ramHit.pageIdx;
        }
        if (!footnoteHTML && state.currentDoc?.id) {
            try {
                const fnRes = await fetchJSON(`/api/books/${state.currentDoc.id}/footnote?file=${encodeURIComponent(targetFile)}&anchor=${encodeURIComponent(cleanId)}`);
                if (fnRes && fnRes.html) footnoteHTML = fnRes.html;
            } catch (err) {}
        }
    } else {
        const rec = lookupFootnoteMap(cleanId, targetId, chBase);
        if (rec?.html) footnoteHTML = rec.html;
        if (!footnoteHTML) {
            const jsonHit = findFootnoteInRamPages(cleanId, false);
            footnoteHTML = jsonHit.html;
            foundPageIdx = jsonHit.pageIdx;
        }
    }

    if (footnoteHTML) {
        showFootnoteModal(footnoteHTML, async () => {
            if (foundPageIdx >= 0) {
                if (peeked) await ensurePeekedPage(foundPageIdx);
                state.viewPageIndex = foundPageIdx;
                state.autoScrollEnabled = false;
                await renderPage();
                setTimeout(() => highlightFootnoteTarget(jumpAnchor || cleanId, peeked), 100);
            }
        });
    } else {
        showToast("Footnote content not found.");
    }
}

export function isPeekShelfBook(item) {
  if (!item || item.is_peek === false) return false;
  if (String(item.bookType || "").toLowerCase() === "pdf") return false;
  return Boolean(item.source_path || item.path || item.is_peek);
}

export function peekShelfStatus(item) {
  if (!isPeekShelfBook(item)) return "reading";
  const raw = String(item.shelf_status || "").toLowerCase().replace(/_/g, " ");
  if (raw === "hold" || raw === "on hold") return "hold";
  if (raw === "finished") return "finished";
  return "reading";
}

export function isLibrarySidebarBook(item) {
  if (!isPeekShelfBook(item)) return true;
  return peekShelfStatus(item) === "reading";
}

export async function setBookShelfStatus(docId, status) {
  const res = await fetchJSON(`/api/books/${encodeURIComponent(docId)}/shelf-status`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ status }),
  });
  if (state.currentDoc?.id === docId) {
    state.currentDoc.shelf_status = (res && res.shelf_status) || status || "reading";
  }
  await loadLibrary();
  return res;
}

let shelfMenuEl = null;
let shelfMenuDocId = null;

export function hideShelfStatusMenu() {
  if (shelfMenuEl) shelfMenuEl.classList.add("hidden");
  shelfMenuDocId = null;
}

function ensureShelfStatusMenu() {
  if (shelfMenuEl) return shelfMenuEl;
  shelfMenuEl = document.createElement("div");
  shelfMenuEl.id = "lrShelfStatusMenu";
  shelfMenuEl.className =
    "hidden fixed z-[10050] min-w-[148px] py-1 bg-zinc-900 border border-zinc-700 rounded-md shadow-2xl text-xs text-zinc-200";
  shelfMenuEl.innerHTML = `
    <button type="button" data-shelf="reading" class="w-full text-left px-3 py-1.5 hover:bg-zinc-800">Reading</button>
    <button type="button" data-shelf="hold" class="w-full text-left px-3 py-1.5 hover:bg-zinc-800">On hold</button>
    <button type="button" data-shelf="finished" class="w-full text-left px-3 py-1.5 hover:bg-zinc-800">Finished</button>
  `;
  document.body.appendChild(shelfMenuEl);
  shelfMenuEl.addEventListener("click", async (e) => {
    const btn = e.target.closest("[data-shelf]");
    if (!btn || !shelfMenuDocId) return;
    const status = btn.getAttribute("data-shelf");
    const id = shelfMenuDocId;
    hideShelfStatusMenu();
    try {
      await setBookShelfStatus(id, status);
    } catch (err) {
      console.error(err);
      showToast("Failed to update shelf status", "error");
    }
  });
  document.addEventListener("pointerdown", (e) => {
    if (shelfMenuEl && !shelfMenuEl.contains(e.target)) hideShelfStatusMenu();
  });
  window.addEventListener("blur", hideShelfStatusMenu);
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") hideShelfStatusMenu();
  });
  return shelfMenuEl;
}

export function openShelfStatusMenu(ev, item) {
  if (!item?.id || !isPeekShelfBook(item)) return false;
  ev.preventDefault();
  ev.stopPropagation();
  const menu = ensureShelfStatusMenu();
  shelfMenuDocId = item.id;
  const current = peekShelfStatus(item);
  menu.querySelectorAll("[data-shelf]").forEach((btn) => {
    const active = btn.getAttribute("data-shelf") === current;
    btn.classList.toggle("bg-zinc-800", active);
    btn.classList.toggle("text-blue-400", active);
  });
  menu.classList.remove("hidden");
  const pad = 8;
  const w = 148;
  const h = 108;
  const x = Math.min(Math.max(pad, ev.clientX), window.innerWidth - w - pad);
  const y = Math.min(Math.max(pad, ev.clientY), window.innerHeight - h - pad);
  menu.style.left = `${x}px`;
  menu.style.top = `${y}px`;
  return true;
}

export function resolveLibraryProgress(item) {
  if (!item) return { current: 0, total: 1, percent: 0 };
  if (state.currentDoc?.id === item.id) {
    const metrics = getProgressMetrics();
    const total = Math.max(1, metrics.totalPages || item.totalPages || 1);
    return {
      current: metrics.currentPage || 0,
      total,
      percent: Math.round(metrics.percent || 0),
    };
  }

  const total = Math.max(1, Number(item.total_pages) || Number(item.totalPages) || 1);
  const hasLogged =
    item.current_page != null ||
    Number(item.progress_percent) > 0 ||
    Number(item.currentPage) > 0 ||
    Number(item.lastSentenceIndex) > 0;
  const current = item.current_page != null
    ? Number(item.current_page)
    : hasLogged
      ? Number(item.currentPage || 0) + 1
      : 0;
  const percent = hasLogged
    ? (item.progress_percent != null
        ? Math.round(Number(item.progress_percent))
        : Math.round((current / total) * 100))
    : 0;
  return { current, total, percent };
}

function paintCardBadge(root, item) {
  const { current, total, percent } = resolveLibraryProgress(item);
  const frac = root.querySelector(".card-badge-fraction");
  const pct = root.querySelector(".card-badge-percent");
  if (frac) frac.textContent = `${current}/${total} p.`;
  if (pct) pct.textContent = `${percent}%`;
}

export function renderLibraryCard(item) {
  const isSelected = state.currentDoc?.id === item.id;
  const { current, total, percent } = resolveLibraryProgress(item);
  const div = document.createElement("div");
  div.dataset.docId = item.id;
  div.dataset.id = item.id;
  div.dataset.action = "select-doc";
  div.title = item.fileName;
  const extracted = !isPeekShelfBook(item);
  div.className = `group select-none p-3 rounded-xl cursor-pointer border transition-all ${
    isSelected
      ? "bg-blue-600/10 border-blue-600/50 text-blue-400 hover:bg-blue-600/15"
      : "bg-zinc-900/50 border-zinc-800 text-zinc-400 hover:border-zinc-700 hover:bg-zinc-800/40"
  }${extracted ? " opacity-70" : ""}`;
  div.innerHTML = `
                <div class="flex items-start justify-between gap-2">
                    <div class="flex items-start gap-3 flex-1 min-w-0" data-action="select-doc" data-id="${item.id}">
                        <i data-lucide="file" class="w-4 h-4 mt-0.5 shrink-0"></i>
                        <div class="flex-1 min-w-0">
                            <p class="text-xs font-bold leading-tight truncate">${item.fileName}</p>
                            <p class="card-badge text-[10px] opacity-60 mt-1">
                              <span class="card-badge-fraction">${current}/${total} p.</span>
                              <span class="card-badge-sep">•</span>
                              <span class="card-badge-percent">${percent}%</span>
                            </p>
                        </div>
                    </div>
                    <button data-action="delete-doc" data-id="${item.id}" title="Delete book" class="p-1.5 hover:bg-red-500/20 hover:text-red-500 rounded-md transition-colors opacity-0 group-hover:opacity-100 shrink-0 relative z-10">
                        <i data-lucide="x" class="w-3.5 h-3.5"></i>
                    </button>
                </div>`;
  div.addEventListener("contextmenu", (e) => {
    if (e.target.closest('[data-action="delete-doc"]')) return;
    e.preventDefault();
    openShelfStatusMenu(e, item);
  });
  return div;
}

function refreshOpenBookBadge() {
  if (!state.currentDoc?.id) return;
  const card = document.querySelector(`[data-doc-id="${state.currentDoc.id}"]`);
  if (card) paintCardBadge(card, state.currentDoc);
}

document.addEventListener("lr-progress-updated", refreshOpenBookBadge);

export async function loadLibrary() {
  const libraryPanel = document.getElementById("libraryPanel");
  try {
    const items = await fetchJSON(`/api/library?t=${Date.now()}`);
    libraryPanel.innerHTML = "";
    if (!Array.isArray(items) || items.length === 0) {
      libraryPanel.innerHTML = '<div class="p-4 text-xs text-zinc-500 italic">Library is empty. Upload a PDF to start.</div>';
      document.dispatchEvent(new CustomEvent("lr-library-change"));
      return;
    }
    const visible = items.filter(isLibrarySidebarBook);
    if (visible.length === 0) {
      libraryPanel.innerHTML = '<div class="p-4 text-xs text-zinc-500 italic">No books currently reading.</div>';
      document.dispatchEvent(new CustomEvent("lr-library-change"));
      return;
    }
    const fragment = document.createDocumentFragment();
    visible
      .sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0))
      .forEach((item) => {
        fragment.appendChild(renderLibraryCard(item));
      });
    libraryPanel.appendChild(fragment);
    renderIcons();
    document.dispatchEvent(new CustomEvent("lr-library-change"));
  } catch (e) {
    libraryPanel.innerHTML = '<div class="p-4 text-xs text-red-500 italic">Failed to load library.</div>';
  }
}

export async function processJsonData(pagesText, fileName, explicitDocId = null, imageMap = null, tocMap = null, language = null, bookType = null) {
    try {
        const docId = explicitDocId || crypto.randomUUID();
        const bookLang = normalizeBcp47(language);
        const kind = String(bookType || "").toLowerCase() === "pdf" ? "pdf" : "epub";
        const newDoc = {
            id: docId, fileName: fileName, totalPages: pagesText.length,
            currentPage: 0, lastSentenceId: null, lastSentenceIndex: 0, lastAccessed: Date.now(),
            bookType: kind,
        };
        if (bookLang) newDoc.language = bookLang;

        await fetchJSON("/api/library", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(newDoc) });
        
        const contentPayload = { id: docId, pages: pagesText };
        if (imageMap) contentPayload.image_map = imageMap;
        if (tocMap) contentPayload.toc_map = tocMap;
        if (bookLang) contentPayload.language = bookLang;

        await fetch("/api/library/content", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(contentPayload) });

        selectDocument(newDoc);
        showToast("Book added to library");
    } catch (err) {
        showToast("Failed to process document: " + err.message);
    }
}

export async function processPdfBlob(blob, fileName) {
    showToast("Processing PDF with native backend engine...");
    
    // Generate the unique ID upfront so we can send it to the backend route
    const docId = crypto.randomUUID(); 
    
    const formData = new FormData();
    formData.append("file", blob, fileName);

    try {
        // Send to our new PyMuPDF backend route
        const response = await fetch(`/api/convert/pdf?id=${docId}`, {
            method: "POST",
            body: formData
        });

        if (!response.ok) {
            const errorData = await response.json().catch(() => ({}));
            throw new Error(errorData.detail || `Server error: ${response.status}`);
        }

        const data = await response.json();
        
        // Feed the extracted pages, image map, and TOC directly into the unified JSON processor
        await processJsonData(
            data.pages, 
            fileName.replace(/\.pdf$/i, ""), 
            docId, 
            data.image_map, 
            data.toc_map,
            data.language,
            data.bookType || "pdf"
        );

    } catch (err) { 
        console.error("PDF Conversion Error:", err);
        showToast("Failed to process PDF: " + err.message); 
    }
}

export async function flushReadingProgress() {
    if (!state.currentDoc) return;
    try {
        const metrics = getProgressMetrics();
        const payload = {
            currentPage: state.readingPageIndex || 0,
            lastSentenceId: state.currentDoc.lastSentenceId || null,
            lastSentenceIndex: state.currentSentenceIndex || 0,
            lastAccessed: Date.now(),
            current_page: metrics.currentPage,
            total_pages: metrics.totalPages,
            progress_percent: Math.round(metrics.percent),
        };
        await fetchJSON(`/api/library/progress/${state.currentDoc.id}?flush=true`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(payload),
        });
        console.log(`[Progress] Flushed checkpoint for ${state.currentDoc.id}`);
    } catch (e) {
        console.warn("[Progress] Flush failed:", e);
    }
}

export async function selectDocument(item) {
    if (state.currentDoc && state.currentDoc.id !== item.id) {
        await flushReadingProgress();
    }

    if (!window._hasSyncedSettingsOnOpen) {
        try {
            const savedSettings = await fetchJSON(`/api/settings?t=${Date.now()}`).catch(() => null);
            if (savedSettings) {
                const voiceSelect = document.getElementById("voiceSelect");
                if (voiceSelect && savedSettings.voice && voiceSelect.value !== savedSettings.voice) {
                    voiceSelect.value = savedSettings.voice;
                    state.voice = savedSettings.voice;
                }
            }
        } catch (err) {}
        window._hasSyncedSettingsOnOpen = true;
    }

    markPeekEpub(item);
    state.currentDoc = item;

    // ── BR toggle: sync to this book's stored setting ─────────────────────
    const _brBtn   = document.getElementById("brToggleBtn");
    const _brLabel = document.getElementById("brToggleLabel");
    const _brActive = !!item.disable_br;
    if (_brBtn) {
        _brBtn.classList.toggle("on", _brActive);
        _brBtn.setAttribute("aria-checked", _brActive ? "true" : "false");
    }
    if (_brLabel) _brLabel.classList.toggle("br-active", _brActive);

    // Wire click handler once
    if (!window._brToggleWired) {
        window._brToggleWired = true;
        const brBtn   = document.getElementById("brToggleBtn");
        const brLabel = document.getElementById("brToggleLabel");
        if (brBtn) {
            brBtn.addEventListener("click", async () => {
                if (!state.currentDoc) return;
                state.currentDoc.disable_br = !state.currentDoc.disable_br;
                const active = !!state.currentDoc.disable_br;
                brBtn.classList.toggle("on", active);
                brBtn.setAttribute("aria-checked", active ? "true" : "false");
                if (brLabel) brLabel.classList.toggle("br-active", active);
                const tc = document.getElementById("textContent");
                if (tc) tc.classList.toggle("disable-br", active);
                try {
                    if (hasBookPath(state.currentDoc)) {
                        await fetchJSON(`/api/library/progress/${state.currentDoc.id}`, {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify({
                                currentPage: state.currentDoc.currentPage || 0,
                                lastSentenceId: state.currentDoc.lastSentenceId || null,
                                lastSentenceIndex: state.currentDoc.lastSentenceIndex || 0,
                                lastAccessed: Date.now(),
                                disable_br: active,
                            }),
                        });
                    } else {
                        await fetchJSON("/api/library", {
                            method: "POST",
                            headers: { "Content-Type": "application/json" },
                            body: JSON.stringify(state.currentDoc),
                        });
                    }
                } catch (e) {}
            });
        }
    }
    // ──────────────────────────────────────────────────────────────────────

    state.pageLanguage = null;
    state.bookLanguage = normalizeBcp47(item.language) || null;
    const textContent = document.getElementById("textContent");
    if (textContent) {
        textContent.classList.toggle("disable-br", !!item.disable_br);
        textContent.classList.remove("hidden");
    }

    try {
        const isNewBook = hasBookPath(item);
        let data = null;
        let peekWaitingOnRam = false;

        if (isNewBook) {
            // --- NEW CODE PIPELINE (Tier 1 peek, then background fill) ---
            try {
                const meta = await fetchJSON(`/api/books/${item.id}/meta`);
                if (meta) {
                    state.metadata = meta;
                    state.tocMap = meta.toc_map || [];
                    state.footnoteMap = meta.footnote_map || {};
                    markPeekEpub(item);
                    if (meta.source_path && !item.source_path) item.source_path = meta.source_path;
                    if (meta.path && !item.path) item.path = meta.path;
                    item.bookType = "epub";
                    item.is_peek = true;
                }
            } catch (err) {
                console.warn("[Library] New pipeline: metadata fetch bypass:", err);
            }

            const spineLen = (state.metadata?.spine && state.metadata.spine.length)
                || state.metadata?.total_pages
                || state.metadata?.totalPages
                || 0;
            let startIndex = item.currentPage || 0;
            if (spineLen && (startIndex < 0 || startIndex >= spineLen)) startIndex = 0;

            try {
                const chapter = await fetchJSON(`/api/books/${item.id}/chapter/${startIndex}`);
                const total = chapter.total_chapters || spineLen || 1;
                const pages = new Array(total).fill("");
                const idx = (typeof chapter.chapter_index === "number") ? chapter.chapter_index : startIndex;
                pages[idx] = chapter.html || "";
                state.currentPages = pages;
                state.smartStartPage = 0;
                data = {
                    pages,
                    toc_map: state.tocMap || [],
                    footnote_map: state.footnoteMap || {},
                    bookType: "epub",
                    language: state.metadata?.language,
                    smart_start_page: 0,
                    is_peek: true,
                };
                fillPeekedBookInBackground(item.id, item);
                peekWaitingOnRam = true;
            } catch (err) {
                if (isMissingBookError(err)) throw err;
                console.warn("[Library] Chapter peek failed, falling back to full content:", err);
                data = await fetchJSON(`/api/library/content/${item.id}`);
                state.currentPages = data.pages;
                state.smartStartPage = data.smart_start_page || 0;
                if (!state.tocMap || state.tocMap.length === 0) {
                    state.tocMap = data.toc_map || [];
                }
                if (!state.footnoteMap || Object.keys(state.footnoteMap).length === 0) {
                    state.footnoteMap = data.footnote_map || {};
                }
            }
        } else {
            // --- LEGACY CODE PIPELINE (Disk-based unzipped book, no source path) ---
            state.metadata = null;
            data = await fetchJSON(`/api/library/content/${item.id}`);
            state.currentPages = data.pages;
            state.smartStartPage = data.smart_start_page || 0;
            state.tocMap = data.toc_map || [];
            state.footnoteMap = data.footnote_map || {};
        }

        resolveBookLanguage(data, item);
        if (hasBookPath(item)) item.bookType = "epub";
        else if (!item.bookType) item.bookType = data.bookType;
        setRamIndexReady(!peekWaitingOnRam);
        if (!peekWaitingOnRam) indexDocument(state.currentPages);
        else updateProgressDisplay();

        if ((item.currentPage || 0) === 0 && state.smartStartPage > 0) {
            state.readingPageIndex = state.smartStartPage;
            state.viewPageIndex = state.smartStartPage;
            state.currentSentenceIndex = 0;
        } else {
            state.readingPageIndex = item.currentPage || 0;
            state.viewPageIndex = item.currentPage || 0;
            state.currentSentenceIndex = item.lastSentenceIndex || 0;
        }

        const pageCount = (state.currentPages || []).length;
        if (pageCount > 0) {
            if (state.readingPageIndex < 0 || state.readingPageIndex >= pageCount) {
                state.readingPageIndex = 0;
            }
            if (state.viewPageIndex < 0 || state.viewPageIndex >= pageCount) {
                state.viewPageIndex = state.readingPageIndex;
            }
            if (!state.currentPages[state.viewPageIndex]) {
                const filled = state.currentPages.findIndex((page) => page);
                if (filled >= 0) {
                    state.viewPageIndex = filled;
                    state.readingPageIndex = filled;
                }
            }
        }

        state.readingSentences = await getSentencesForPage(state.readingPageIndex);

        const docTitle = document.getElementById("docTitle");
        const pageNav = document.getElementById("pageNav");
        const controls = document.getElementById("controls");
        const emptyState = document.getElementById("emptyState");
        const prevPage = document.getElementById("prevPage");
        const nextPage = document.getElementById("nextPage");
        const pageInput = document.getElementById("pageInput");
        const searchBtn = document.getElementById("searchBtn");
        const exportArea = document.getElementById("exportArea");
        
        if (docTitle) {
            docTitle.textContent = item.fileName;
            docTitle.removeAttribute("title");
            docTitle.classList.remove("hidden");
        }
        if (pageNav) { pageNav.classList.remove("opacity-50", "pointer-events-none"); pageNav.removeAttribute("data-inactive"); }
        if (prevPage) prevPage.disabled = false;
        if (nextPage) nextPage.disabled = false;
        if (pageInput) pageInput.disabled = false;
        if (controls) controls.classList.remove("hidden");
        if (emptyState) emptyState.classList.add("hidden");
        if (searchBtn) searchBtn.classList.remove("hidden");

        if (exportArea && window.isEngineReady) exportArea.style.display = 'block';

        state.autoScrollEnabled = true;

        item.lastAccessed = Date.now();
        if (!hasBookPath(item)) {
            try {
                await fetchJSON("/api/library", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify(item),
                });
            } catch (e) {}
        }

        await renderPage(); 
        renderTOC(); 
        await loadLibrary();
        document.dispatchEvent(new CustomEvent("lr-library-change"));

        const autoCollapse = getRenderState().sidebar_auto_collapse !== "show";
        const sidebar = document.querySelector(".sidebar");
        const collapseBtn = document.getElementById("sidebarCollapseBtn");
        if (autoCollapse && sidebar && collapseBtn && !sidebar.classList.contains("collapsed")) {
            collapseBtn.click();
        } 
    } catch (e) {
        console.error("Select document error:", e);
        if (isMissingBookError(e)) {
            showToast("Book missing from path", "warning");
        } else {
            showToast("Failed to load document content");
        }
        state.bookLanguage = null;
        state.pageLanguage = null;
        if (textContent) textContent.innerHTML = '';
    }
}

function fillPeekedBookInBackground(openedId, item) {
    fetchJSON(`/api/library/content/${openedId}`).then((full) => {
        if (!full?.pages?.length) {
            setRamIndexReady(true);
            indexDocument(state.currentPages || []);
            return;
        }
        if (!state.currentDoc || state.currentDoc.id !== openedId) return;

        const oldPages = state.currentPages || [];
        const viewIdx = state.viewPageIndex;
        const readingIdx = state.readingPageIndex;
        const sentenceIdx = state.currentSentenceIndex;
        const oldLen = oldPages.length;

        let spreadIdxs = [viewIdx];
        try {
            if (isHorizontalMode() && wantsSpreadTwoPage()) {
                spreadIdxs = getActiveSpreadPages(viewIdx);
            }
        } catch (e) {}
        const hadHole = spreadIdxs.some((i) => !oldPages[i]) || !oldPages[viewIdx];

        state.currentPages = full.pages;
        if (Array.isArray(full.toc_map)) state.tocMap = full.toc_map;
        if (full.footnote_map && typeof full.footnote_map === "object") {
            state.footnoteMap = full.footnote_map;
        }
        if (full.bookType && item && !item.bookType) item.bookType = full.bookType;
        resolveBookLanguage(full, item);
        setRamIndexReady(true);
        indexDocument(state.currentPages);
        const newLen = (state.currentPages || []).length;
        const clampIdx = (idx) => {
            if (!newLen) return 0;
            if (!Number.isInteger(idx) || idx < 0) return 0;
            return Math.min(idx, newLen - 1);
        };
        state.viewPageIndex = clampIdx(viewIdx);
        state.readingPageIndex = clampIdx(readingIdx);
        state.currentSentenceIndex = sentenceIdx;
        renderTOC();
        if (hadHole || oldLen !== newLen) {
            renderPage();
        }
    }).catch((err) => {
        console.warn("[Library] Background content fill failed:", err);
        setRamIndexReady(true);
        indexDocument(state.currentPages || []);
    });
}

function resolveBookLanguage(data, item) {
    const stored = normalizeBcp47(data?.language || item?.language);
    if (stored) {
        state.bookLanguage = stored;
        if (item && item.language !== stored) persistBookLanguage(stored);
        return;
    }
    let found = null;
    const pages = state.currentPages || [];
    for (const page of pages.slice(0, 8)) {
        found = langFromHtmlMarkup(page);
        if (found) break;
    }
    if (!found) {
        const sample = pages.slice(0, 3).map((page) => String(page || "").replace(/<[^>]+>/g, " ")).join(" ");
        found = guessLangFromText(sample);
    }
    state.bookLanguage = found || null;
    if (found) persistBookLanguage(found);
}

async function persistBookLanguage(lang) {
    if (!state.currentDoc || !lang) return;
    state.currentDoc.language = lang;
    if (hasBookPath(state.currentDoc)) return;
    try {
        await fetchJSON("/api/library", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify(state.currentDoc),
        });
    } catch (e) {}
}

function escapeCssAttr(value) {
    if (typeof CSS !== "undefined" && typeof CSS.escape === "function") {
        return CSS.escape(value);
    }
    return String(value).replace(/["\\]/g, "\\$&");
}

function preferTocCameraElement(el) {
    if (!el) return null;
    if (/^H[1-6]$/i.test(el.tagName)) return el;
    const parentHeading = el.closest("h1, h2, h3, h4, h5, h6");
    if (parentHeading) return parentHeading;
    if (el.matches("n, s, img.epub-image")) return el;
    const nestedHeading = el.querySelector("h1, h2, h3, h4, h5, h6");
    if (nestedHeading) return nestedHeading;
    const nestedBlock = el.querySelector("n[data-block-id], n");
    if (nestedBlock) return nestedBlock;
    return el;
}

function findExactAttrInRoot(root, attrName, value) {
    if (!root || !value) return null;
    try {
        if (attrName === "id") {
            const byId = root.querySelector(`#${escapeCssAttr(value)}`);
            if (byId && byId.getAttribute("id") === value) return byId;
        }
    } catch (error) {}
    const matches = root.querySelectorAll(`[${attrName}]`);
    for (const node of matches) {
        if (node.getAttribute(attrName) === value) return node;
    }
    return null;
}

function resolveTocTargetElement(tocItem) {
    const root = document.getElementById("textContent");
    if (!root) return null;

    const targetId = tocItem && tocItem.target_tts_id;
    if (!targetId) return null;

    const byId = findExactAttrInRoot(root, "id", targetId);
    if (byId) return preferTocCameraElement(byId);

    const byBlock = findExactAttrInRoot(root, "data-block-id", targetId);
    if (byBlock) return preferTocCameraElement(byBlock);

    return null;
}

function alignElementToReaderTop(targetEl) {
    if (isHorizontalMode()) {
        revealInSpread(targetEl);
        return;
    }

    const scrollContainer = document.querySelector(".content-area");
    if (!scrollContainer || !targetEl) return;

    const stickyHeader = scrollContainer.querySelector(":scope > header");
    const headerHeight = stickyHeader ? stickyHeader.offsetHeight : 0;
    const elRect = targetEl.getBoundingClientRect();
    const containerRect = scrollContainer.getBoundingClientRect();
    const relativeTop = elRect.top - containerRect.top + scrollContainer.scrollTop;
    scrollContainer.scrollTop = Math.max(0, relativeTop - headerHeight);
}

function waitForElementImages(targetEl) {
    if (!targetEl) return Promise.resolve();
    const images = [];
    if (targetEl.tagName === "IMG") images.push(targetEl);
    images.push(...targetEl.querySelectorAll("img"));
    const pending = [...new Set(images)]
        .filter(image => !image.complete)
        .map(image => new Promise(resolve => {
            image.addEventListener("load", resolve, { once: true });
            image.addEventListener("error", resolve, { once: true });
        }));
    return pending.length ? Promise.all(pending) : Promise.resolve();
}

function renderTOC() {
    const tocList = document.getElementById('tocList');
    if (!tocList) return;
    tocList.innerHTML = '';
    if ((!state.tocMap || state.tocMap.length === 0) && state.metadata?.toc_map) {
        state.tocMap = state.metadata.toc_map;
    }
    if (!state.tocMap || state.tocMap.length === 0) {
        tocList.innerHTML = '<div class="p-4 text-xs text-zinc-500 italic">No Table of Contents available.</div>';
        return;
    }
    const fragment = document.createDocumentFragment();
    
    state.tocMap.forEach(item => {
        const div = document.createElement('div');
        const paddingLeft = item.level === 1 ? '0.5rem' : item.level === 2 ? '1.5rem' : '2.5rem';
        div.className = `cursor-pointer py-2 px-2 hover:bg-zinc-800 text-sm transition-colors border-l-2 border-transparent hover:border-blue-500`;
        div.style.paddingLeft = paddingLeft;
        div.innerHTML = `<div class="flex justify-between items-center opacity-80 hover:opacity-100 gap-2 min-w-0"><span class="truncate flex-1 ${item.level === 1 ? 'font-bold text-zinc-200' : 'text-zinc-400'}">${item.title}</span><span class="text-[10px] text-zinc-500 shrink-0 whitespace-nowrap">Pg ${item.page_index + 1}</span></div>`;
        
        div.onclick = async () => {
            const tocModal = document.getElementById('tocModal');
            if (tocModal) tocModal.classList.add('hidden');

            await flushReadingProgress();

            // Camera-only: open this entry's page, then pin its target_tts_id.
            // Playback stays on its own page. IDs are unique only within a page.
            state.viewPageIndex = Number(item.page_index);
            state.autoScrollEnabled = false;
            await ensureViewPagesReady(state.viewPageIndex);
            await renderPage();

            const targetEl = resolveTocTargetElement(item);
            if (!targetEl) return;

            requestAnimationFrame(() => {
                alignElementToReaderTop(targetEl);
                waitForElementImages(targetEl).then(() => {
                    requestAnimationFrame(() => alignElementToReaderTop(targetEl));
                });
                setTimeout(() => alignElementToReaderTop(targetEl), 450);
            });
        };
        fragment.appendChild(div);
    });
    tocList.appendChild(fragment);
    updateActiveTOC();
}

export function findTocEntryForPage(pageIndex, sentenceId = null, origId = null) {
    if (!Array.isArray(state.tocMap) || !Number.isInteger(pageIndex)) return null;

    const pageEntries = state.tocMap.filter(
        item => Number(item.page_index) === pageIndex
    );
    if (pageEntries.length === 0) return null;

    const candidateIds = [sentenceId, origId].filter(Boolean);
    const matched = pageEntries.find(item => candidateIds.some(candidate => (
        item.target_tts_id === candidate ||
        item.id === candidate ||
        item.anchor_id === candidate
    )));
    if (matched) return matched;

    // A page containing one mapped image heading is unambiguous even when
    // malformed publisher markup left the heading without a usable ID.
    return pageEntries.length === 1 ? pageEntries[0] : null;
}

const READER_ELEMENT_SELECTOR = 'n, s, img.epub-image, h1, h2, h3, h4, h5, h6, [id^="s_"], [data-sentence-id]';
const PROTECTED_PERIOD = '\uE000';
const SENTENCE_ABBREVIATIONS = [
    "Mr", "Mrs", "Ms", "Dr", "Prof", "Rev", "Hon", "Jr", "Sr", "Esq",
    "Messrs", "Mmes", "Fr", "Pres", "Gen", "Col", "Maj", "Capt", "Lt",
    "Sgt", "Cpl", "Pvt", "Adm", "Cmdr", "Brig", "Sen", "Rep", "Gov",
    "Amb", "Atty", "Cllr", "St", "Rd", "Ave", "Blvd", "Ln", "Ct", "Pl",
    "Sq", "Ter", "Pkwy", "Hwy", "Apt", "Ste", "Bldg", "Co", "Inc",
    "Ltd", "Corp", "LLC", "Mfg", "vs", "viz", "etc", "eg", "ie", "al",
    "ca", "cf", "ibid", "op", "Fig", "Figs", "No", "Nos", "Vol", "Vols",
    "ch", "sec", "ed", "eds", "pp", "p", "approx", "dept", "est", "Jan",
    "Feb", "Mar", "Apr", "Jun", "Jul", "Aug", "Sep", "Sept", "Oct",
    "Nov", "Dec", "e\\.g", "i\\.e"
];

function protectAbbreviationPeriods(text) {
    const pattern = new RegExp(
        `\\b(?:${SENTENCE_ABBREVIATIONS.join('|')})\\.(?=\\s|$)`,
        'gi'
    );
    return text.replace(pattern, match => match.replace(/\./g, PROTECTED_PERIOD));
}

function protectEllipsisPeriods(text) {
    return text.replace(
        /\.(?:\s*\.)+/g,
        match => match.replace(/\./g, PROTECTED_PERIOD)
    );
}

function countSentenceWords(text) {
    return (
        text.match(/[\p{L}\p{N}]+(?:['’\-][\p{L}\p{N}]+)*/gu) || []
    ).length;
}

function getSentenceOffsets(text) {
    if (!text || !text.trim()) return [];

    const protectedText = protectEllipsisPeriods(
        protectAbbreviationPeriods(text)
    );
    const rawOffsets = [];
    const sentenceStop = /(?:\.(?:["'”’»])?|。(?:["'”’」』])?)(?=\s+(?:["'“‘«]*[A-Z0-9\u00C0-\u024F\u0400-\u04FF])|\s*["'“‘「『]*[\u3040-\u30FF\u3400-\u9FFF\uAC00-\uD7AF])/g;
    let start = 0;
    let match;

    while ((match = sentenceStop.exec(protectedText))) {
        rawOffsets.push({ start, end: match.index + match[0].length });
        start = match.index + match[0].length;
    }
    rawOffsets.push({ start, end: text.length });

    const trimmedOffsets = rawOffsets
        .map(({ start, end }) => {
            while (start < end && /\s/.test(text[start])) start++;
            while (end > start && /\s/.test(text[end - 1])) end--;
            return { start, end };
        })
        .filter(({ start, end }) => end > start && text.slice(start, end).trim());

    const mergedOffsets = [];
    let pendingStart = null;

    trimmedOffsets.forEach((offset, index) => {
        if (pendingStart === null) pendingStart = offset.start;
        const isLast = index === trimmedOffsets.length - 1;
        const pendingText = text.slice(pendingStart, offset.end);

        // Match the original splitter: do not create tiny sentence fragments.
        if (!isLast && countSentenceWords(pendingText) < 4) return;

        mergedOffsets.push({ start: pendingStart, end: offset.end });
        pendingStart = null;
    });

    for (let i = 1; i < mergedOffsets.length; i++) {
        if (mergedOffsets[i].start < mergedOffsets[i - 1].end) {
            mergedOffsets[i] = {
                start: mergedOffsets[i - 1].end,
                end: mergedOffsets[i].end
            };
        }
    }

    return mergedOffsets.filter(({ start, end }) => end > start);
}

function isRubyAnnotationNode(node) {
    const el = node.nodeType === Node.TEXT_NODE ? node.parentElement : node;
    return !!(el && el.closest && el.closest("rt, rp"));
}

function getTextNodeOffsets(paragraph) {
    const entries = [];
    const walker = document.createTreeWalker(
        paragraph,
        NodeFilter.SHOW_TEXT
    );
    let offset = 0;
    let node;

    while ((node = walker.nextNode())) {
        if (isRubyAnnotationNode(node)) continue;
        const length = node.nodeValue.length;
        entries.push({ node, start: offset, end: offset + length });
        offset += length;
    }
    return entries;
}

function expandRangeToRuby(range, paragraph) {
    const startEl = range.startContainer.nodeType === Node.TEXT_NODE
        ? range.startContainer.parentElement
        : range.startContainer;
    const endEl = range.endContainer.nodeType === Node.TEXT_NODE
        ? range.endContainer.parentElement
        : range.endContainer;
    const startRuby = startEl && startEl.closest && startEl.closest("ruby");
    const endRuby = endEl && endEl.closest && endEl.closest("ruby");
    if (startRuby && paragraph.contains(startRuby)) {
        range.setStartBefore(startRuby);
    }
    if (endRuby && paragraph.contains(endRuby)) {
        range.setEndAfter(endRuby);
    }
}

function resolveTextPosition(entries, offset, preferNextNode) {
    for (let index = 0; index < entries.length; index++) {
        const entry = entries[index];
        if (offset < entry.end) {
            return { node: entry.node, offset: offset - entry.start };
        }
        if (offset === entry.end) {
            const next = entries[index + 1];
            if (preferNextNode && next && next.start === offset) {
                return { node: next.node, offset: 0 };
            }
            return { node: entry.node, offset: entry.node.nodeValue.length };
        }
    }

    const last = entries[entries.length - 1];
    return last
        ? { node: last.node, offset: last.node.nodeValue.length }
        : null;
}

function cloneRangeWithInlineContext(range, paragraph) {
    let fragment = range.cloneContents();
    let context = range.commonAncestorContainer;
    if (context.nodeType === Node.TEXT_NODE) {
        context = context.parentElement;
    }

    while (context && context !== paragraph) {
        if (context.tagName && /^(N)$/i.test(context.tagName)) {
            context = context.parentElement;
            continue;
        }
        const wrapper = context.cloneNode(false);
        wrapper.appendChild(fragment);
        fragment = document.createDocumentFragment();
        fragment.appendChild(wrapper);
        context = context.parentElement;
    }
    return fragment;
}

function wrapWholeParagraph(paragraph) {
    if (!(paragraph.textContent || '').trim()) return;

    const sentence = document.createElement('n');
    sentence.id = `${paragraph.id}_1`;
    sentence.dataset.blockId = paragraph.id;
    while (paragraph.firstChild) {
        sentence.appendChild(paragraph.firstChild);
    }
    paragraph.appendChild(sentence);
}

function injectParagraphSentences(root) {
    const blocks = Array.from(root.querySelectorAll('[id^="s_"]:not(h1, h2, h3, h4, h5, h6), [data-sentence-id]:not(h1, h2, h3, h4, h5, h6)'));

    blocks.forEach(paragraph => {
        if (paragraph.id && !paragraph.dataset.sentenceId) {
            paragraph.dataset.sentenceId = paragraph.id;
        }
        if (paragraph.querySelector('n')) return;
        if (paragraph.querySelector('img, svg, picture, figure, s')) return;

        try {
            const textNodes = getTextNodeOffsets(paragraph);
            const text = textNodes.map((entry) => entry.node.nodeValue).join("");
            const sentenceOffsets = getSentenceOffsets(text);
            if (!sentenceOffsets.length || !textNodes.length) {
                wrapWholeParagraph(paragraph);
                return;
            }

            const sentenceFragments = sentenceOffsets.map(({ start, end }) => {
                const startPosition = resolveTextPosition(textNodes, start, true);
                const endPosition = resolveTextPosition(textNodes, end, false);
                if (!startPosition || !endPosition) return null;

                const range = document.createRange();
                range.setStart(startPosition.node, startPosition.offset);
                range.setEnd(endPosition.node, endPosition.offset);
                expandRangeToRuby(range, paragraph);
                return cloneRangeWithInlineContext(range, paragraph);
            });

            if (sentenceFragments.some(fragment => fragment === null)) {
                wrapWholeParagraph(paragraph);
                return;
            }

            const blockId = paragraph.id;
            paragraph.replaceChildren();
            sentenceFragments.forEach((fragment, index) => {
                if (index > 0) {
                    const gap = text.slice(sentenceOffsets[index - 1].end, sentenceOffsets[index].start);
                    if (/\s/.test(gap)) {
                        paragraph.appendChild(document.createTextNode(' '));
                    }
                }
                const sentence = document.createElement('n');
                sentence.id = `${blockId}_${index + 1}`;
                sentence.dataset.blockId = blockId;
                sentence.appendChild(fragment);
                paragraph.appendChild(sentence);
            });
        } catch (error) {
            console.warn("Sentence injection fallback:", error);
            wrapWholeParagraph(paragraph);
        }
    });
}

export function clearActiveSentenceHighlights() {
    const textContent = document.getElementById("textContent");
    if (!textContent) return;
    textContent.querySelectorAll(".active-sentence").forEach((el) => {
        el.classList.remove("active-sentence");
    });
}

function collectReaderElements(root) {
    return Array.from(root.querySelectorAll(READER_ELEMENT_SELECTOR))
        .filter(element => {
            const tag = element.tagName.toLowerCase();
            // If an element contains child <n> or <s> elements, it is represented by those children
            if (element.querySelector('n, s')) {
                return false;
            }
            // If a container element wraps other content blocks with reader IDs, yield to the children
            if (element.querySelector('[id^="s_"]:not(img), [data-sentence-id]:not(img)')) {
                return false;
            }
            // Filter out nested reader elements:
            // An element inside another reader element should only be kept if it is an <n> or <s>
            // inside a block that was split into sentences.
            const parentReader = element.parentElement
                ? element.parentElement.closest('s, n, h1, h2, h3, h4, h5, h6, [id^="s_"], [data-sentence-id]')
                : null;
            if (parentReader) {
                if ((tag === 'n' || tag === 's') && parentReader.querySelector(tag)) {
                    return !element.parentElement.closest('s, n, h1, h2, h3, h4, h5, h6');
                }
                return false;
            }
            return true;
        });
}

function readerElementToTtsValue(element) {
    if (element.tagName.toLowerCase() === 's') {
        return element.outerHTML;
    }
    if (element.tagName.toLowerCase() === 'img') {
        return element.outerHTML;
    }

    const rawText = (element.textContent || '').trim();
    const hasNarrative = /[a-zA-Z0-9\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\uFF00-\uFF9F\u4E00-\u9FAF\u3400-\u4DBF]/.test(rawText);

    // If the element is purely a wrapper around media with no narrative text
    // (e.g. <p id="s_0"><img src="..." class="epub-image"></p>),
    // preserve the media tags so TTS and UI recognize it as an image block (bType = "Img")
    if (!hasNarrative && element.querySelector('img, svg, picture')) {
        const pause = parseInt(element.getAttribute('data-pause') || "0");
        return `${pause > 0 ? `[PAUSE_${pause}] ` : ''}${element.outerHTML}`;
    }

    const clone = element.cloneNode(true);
    // Remove img, svg, and picture nodes from the cloned node
    // so TTS receives pure speech text without mutating the visual DOM
    clone.querySelectorAll('img, svg, picture').forEach(img => {
        if (img.previousSibling && img.previousSibling.nodeType === 3 && img.nextSibling && img.nextSibling.nodeType === 3) {
            if (/\s+$/.test(img.previousSibling.nodeValue) && /^[,.:;!?]/.test(img.nextSibling.nodeValue.trim())) {
                img.previousSibling.nodeValue = img.previousSibling.nodeValue.replace(/\s+$/, '');
            }
        }
        img.remove();
    });

    clone.querySelectorAll('a[epub\\:type="noteref"], a[href*="#R_"], .epub-noteref').forEach(ref => {
        const parentText = ref.parentElement ? ref.parentElement.textContent.trim() : "";
        const refText = ref.textContent.trim();
        const nextText = ref.nextSibling && ref.nextSibling.nodeType === 3
            ? ref.nextSibling.textContent.trim()
            : "";
        const isDefinition = parentText.startsWith(refText) || nextText.startsWith(':');

        if (isDefinition) {
            const cleanNum = refText.replace(/[\[\]\(\)]/g, '');
            ref.textContent = `Footnote ${cleanNum}, `;
            if (ref.nextSibling && ref.nextSibling.nodeType === 3) {
                ref.nextSibling.textContent = ref.nextSibling.textContent.replace(/^[\s:,\-]+/, ' ');
            }
        } else {
            ref.remove();
        }
    });

    const pause = parseInt(element.getAttribute('data-pause') || "0");
    return `${pause > 0 ? `[PAUSE_${pause}] ` : ''}${clone.outerHTML}`;
}

export async function getSentencesForPage(pageIndex) {
    if (!state.currentPages) return [];
    if (!state.currentPages[pageIndex] && hasBookPath(state.currentDoc)) {
        await ensurePeekedPage(pageIndex);
    }
    if (!state.currentPages[pageIndex]) return [];
    const pageText = state.currentPages[pageIndex];

    const tempDiv = document.createElement('div');
    tempDiv.innerHTML = pageText;
    injectParagraphSentences(tempDiv);
    const readerElements = collectReaderElements(tempDiv);
    if (readerElements.length > 0) {
        return readerElements.map(readerElementToTtsValue);
    }

    let text = pageText.replace(/(\[H[1-6]\].*?\[\/H[1-6]\])/g, "\n\n$1\n\n").replace(/(\[SCENE_BREAK\])/g, "\n\n$1\n\n");
    text = text.replace(/\n\n+/g, "<!PARAGRAPH!>").replace(/([^.!?:;。！？：；])\n/g, "$1 ").replace(/<!PARAGRAPH!>/g, "\n\n").replace(/  +/g, " ");

    const protectedText = protectAbbreviationPeriods(text).replace(
        new RegExp(PROTECTED_PERIOD, "g"),
        "<DOT>"
    );

    const sentences = [];
    const segmenter = new Intl.Segmenter(state.uiLanguage || 'en', { granularity: 'sentence' });

    for (const segmentItem of segmenter.segment(protectedText)) {
        let s = segmentItem.segment.trim().replace(/<DOT>/g, '.').replace(/^[\"\'\u201c\u2018\u201d\u2019]+(?=[\"\'\u201c\u2018\u201d\u2019])/, '').replace(/[\"\'\u201c\u2018\u201d\u2019]+$/, (match) => match.length > 1 ? match[0] : match);
        if (s) {
            if (s.includes("[DIM]") && !s.includes("[/DIM]")) s += "[/DIM]";
            if (!s.includes("[DIM]") && s.includes("[/DIM]")) s = "[DIM]" + s;
            const hStartMatch = s.match(/\[H[1-6]\]/);
            const hEndMatch = s.match(/\[\/H[1-6]\]/);
            if (hStartMatch && !hEndMatch) s += `[/${hStartMatch[0].replace('[','').replace(']','')}]`;
            if (!hStartMatch && hEndMatch) s = `[${hEndMatch[0].replace('[/','').replace(']','')}]` + s;
            if (segmentItem.segment.includes('\n')) s += '\n'; 
            sentences.push(s);
        }
    }
    return sentences;
}



export async function renderPage() {
    const textContent = document.getElementById("textContent");
    const scrollContainer = document.querySelector(".content-area");
    const currentSentencePreview = document.getElementById("currentSentencePreview");
    const backToReadingBtn = document.getElementById("backToReadingBtn");
    const seq = ++_renderPageSeq;
    const pageIdx = state.viewPageIndex;

    if (!state.currentPages) {
        if (textContent) textContent.innerHTML = '<div class="text-zinc-500 p-4">Error: Page not found</div>';
        return;
    }

    if (hasBookPath(state.currentDoc) && Number.isInteger(pageIdx) && pageIdx >= 0) {
        if (!state.currentPages[pageIdx] && textContent) {
            textContent.innerHTML = '<div class="text-zinc-500 p-4 animate-pulse">Loading page...</div>';
        }
        await ensureViewPagesReady(pageIdx);
        if (seq !== _renderPageSeq) return;
    }

    if (!state.currentPages[pageIdx]) {
        if (textContent) textContent.innerHTML = '<div class="text-zinc-500 p-4">Error: Page not found</div>';
        return;
    }

    if (state.viewPageIndex !== state.readingPageIndex) {
        state.autoScrollEnabled = false;
    }
    syncBackToReadingButton();

    state.viewSentences = await getSentencesForPage(pageIdx);
    if (seq !== _renderPageSeq) return;
    const pageText = state.currentPages[pageIdx];
    state.pageLanguage = langFromHtmlMarkup(pageText);
    const isReadingCurrentPage = state.viewPageIndex === state.readingPageIndex;
    
    // 🌟 FIX: Safety check to prevent a stale index from overwriting the next page
    const isOnSavedPage = state.currentDoc && state.viewPageIndex === (state.currentDoc.currentPage || 0);

    if (textContent) {
        textContent.innerHTML = "";

        let contentMarkup = pageText;
        if (isHorizontalMode() && wantsSpreadTwoPage()) {
            const spreadPages = getActiveSpreadPages(state.viewPageIndex);
            if (spreadPages.length === 2) {
                const pageTextA = state.currentPages[spreadPages[0]] || "";
                const pageTextB = state.currentPages[spreadPages[1]] || "";
                contentMarkup = `<div class="spread-slot spread-slot-left" data-page-index="${spreadPages[0]}">${pageTextA}</div><div class="spread-slot spread-slot-right" data-page-index="${spreadPages[1]}">${pageTextB}</div>`;
            } else if (isStandaloneIllustrationPage(state.viewPageIndex)) {
                contentMarkup = `<div class="spread-slot spread-slot-isolated" data-page-index="${state.viewPageIndex}">${pageText}</div>`;
            }
        }

        textContent.innerHTML = contentMarkup;
        injectParagraphSentences(textContent);
        const readerElements = collectReaderElements(textContent);

        if (readerElements.length > 0) {
            state.sentenceElements = readerElements;
            
            if (isReadingCurrentPage && state.currentDoc && isOnSavedPage) {
                let positionFound = false;
                if (state.currentDoc.lastSentenceId) {
                    const structuralIdIndex = state.sentenceElements.findIndex(el => (
                        el.getAttribute('id') === state.currentDoc.lastSentenceId ||
                        el.dataset?.sentenceId === state.currentDoc.lastSentenceId ||
                        el.getAttribute('data-block-id') === state.currentDoc.lastSentenceId ||
                        el.closest('p[id^="s_"]')?.getAttribute('id') === state.currentDoc.lastSentenceId
                    ));
                    if (structuralIdIndex !== -1) {
                        state.currentSentenceIndex = structuralIdIndex;
                        positionFound = true;
                    }
                }
                if (!positionFound && typeof state.currentDoc.lastSentenceIndex === 'number') {
                    if (state.currentDoc.lastSentenceIndex >= 0 && state.currentDoc.lastSentenceIndex < state.sentenceElements.length) {
                        state.currentSentenceIndex = state.currentDoc.lastSentenceIndex;
                    } else state.currentSentenceIndex = 0; 
                }
            }

            clearActiveSentenceHighlights();
            state.sentenceElements.forEach((tag, i) => {
                tag.classList.add('sentence'); 
                if (isReadingCurrentPage && i === state.currentSentenceIndex) tag.classList.add('active-sentence');
                tag.onclick = (e) => {
                    e.stopPropagation(); // 🌟 PHANTOM ROUTER: Prevent click from bubbling up to parent header and double-firing
                    
                    const isImg = e.target &&
                        e.target.tagName &&
                        e.target.tagName.toLowerCase() === 'img' &&
                        e.target.classList.contains('epub-image') &&
                        !e.target.closest('s');
                    const triggerJump = () => {
                        state.readingPageIndex = state.viewPageIndex;
                        state.readingSentences = [...state.viewSentences];
                        state.autoScrollEnabled = true; 
                        window.dispatchEvent(new CustomEvent("jump-to-sentence", { detail: i }));
                    };
                    
                    if (isImg) {
                        if (e.detail === 1) window._imgClickTimer = setTimeout(triggerJump, 250);
                        else if (e.detail === 2) clearTimeout(window._imgClickTimer);
                    } else {
                        triggerJump();
                    }
                };
            });
        } else {
            textContent.innerHTML = "";
            const fragment = document.createDocumentFragment();
            state.viewSentences.forEach((s, i) => {
                const span = document.createElement("span");
                span.className = `sentence ${(isReadingCurrentPage && i === state.currentSentenceIndex) ? "active-sentence" : ""}`;
                let cleanS = s;
                const hMatch = cleanS.match(/\[(H[1-6])\](.*?)\[\/\1\]/);
                const imgMatch = cleanS.match(/\[IMAGE_(\d+)\]/);

                const fallbackImgLoading = state.viewSentences.length === 1 ? "lazy" : "eager";
                if (hMatch) span.innerHTML = `<${hMatch[1].toLowerCase()} class="book-heading ${hMatch[1].toLowerCase()}">${hMatch[2]}</${hMatch[1].toLowerCase()}>`;
                else if (imgMatch) span.innerHTML = `<img src="/api/library/image/${state.currentDoc?.id}/${imgMatch[1]}" class="epub-image" onload="if(this.naturalWidth < 150 && this.naturalHeight < 150) { this.classList.add('epub-icon'); }" loading="${fallbackImgLoading}" alt="Illustration" />`;
                else if (cleanS.includes("[SCENE_BREAK]")) span.innerHTML = `<div class="scene-break">♦ ♦ ♦</div>`;
                else {
                    if (cleanS.includes("[DIM]")) span.innerHTML = cleanS.replace(/\[DIM\](.*?)\[\/DIM\]/g, '<span class="dimmed-text">$1</span>');
                    else span.textContent = cleanS;
                }

                span.onclick = (e) => {
                    e.stopPropagation();
                    const isImg = e.target && e.target.tagName && e.target.tagName.toLowerCase() === 'img' && e.target.classList.contains('epub-image') && !e.target.closest('s');
                    const triggerJump = () => {
                        state.readingPageIndex = state.viewPageIndex;
                        state.readingSentences = [...state.viewSentences];
                        state.autoScrollEnabled = true; 
                        window.dispatchEvent(new CustomEvent("jump-to-sentence", { detail: i }));
                    };
                    
                    if (isImg) {
                        if (e.detail === 1) window._imgClickTimer = setTimeout(triggerJump, 250);
                        else if (e.detail === 2) clearTimeout(window._imgClickTimer);
                    } else {
                        triggerJump();
                    }
                };
                fragment.appendChild(span);
            });
            textContent.appendChild(fragment);
            state.sentenceElements = Array.from(textContent.querySelectorAll(".sentence"));
        }
    }

    applyReaderTypography({ skipLayout: isHorizontalMode() });
    updateProgressDisplay();

    if (isHorizontalMode()) {
        const shouldResetSpread = !isReadingCurrentPage || !state.autoScrollEnabled;
        await layoutSpreads({ reset: shouldResetSpread });
        const activeEl = document.querySelector("#textContent .active-sentence");
        if (activeEl && isReadingCurrentPage && state.autoScrollEnabled) {
            revealInSpread(activeEl);
        }
        if (typeof updateActiveTOC === "function") updateActiveTOC();
        updateProgressDisplay();
        updateHorizontalSpreadFocus();
    } else if (scrollContainer) {
        // 🌟 FIX: THE BULLETPROOF MATHEMATICAL FOCUS CAMERA
        if (!isReadingCurrentPage) {
            scrollContainer.scrollTop = 0;
        } 
        else if (state.autoScrollEnabled) {
            requestAnimationFrame(() => {
                const alignCam = () => {
                    if (!state.autoScrollEnabled) return false;
                    const activeEl = document.querySelector('#textContent .active-sentence');
                    if (activeEl) {
                        const elRect = activeEl.getBoundingClientRect();
                        const containerRect = scrollContainer.getBoundingClientRect();
                        const relativeTop = elRect.top - containerRect.top + scrollContainer.scrollTop;
                        const centerPosition = relativeTop - (containerRect.height / 2) + (elRect.height / 2);
                        scrollContainer.scrollTop = Math.max(0, centerPosition);
                        return activeEl.tagName && (activeEl.tagName.toLowerCase() === 'img' || activeEl.querySelector('img, svg')) && activeEl.tagName.toLowerCase() !== 's';
                    }
                    return false;
                };
                
                setTimeout(() => {
                    const isImg = alignCam();
                    updateProgressDisplay();
                    if (isImg) {
                        // Strike 2: Only re-lock if it's an image transitioning size
                        setTimeout(() => {
                            alignCam();
                            if (typeof updateActiveTOC === 'function') updateActiveTOC();
                        }, 450);
                    } else {
                        // Text locks instantly, trigger TOC update immediately
                        if (typeof updateActiveTOC === 'function') updateActiveTOC();
                    }
                }, 20);
            });
        }
    }
    const currentReadingSentence = (state.readingSentences && state.readingSentences.length > 0) ? state.readingSentences[state.currentSentenceIndex] : "";
    if (currentSentencePreview && currentReadingSentence) {
        const cleanText = currentReadingSentence.replace(/\[PAUSE_\d+\]\s*/g, '');
        
        let finalStr = cleanText.replace(/<(?:rt|rp)\b[^>]*>[\s\S]*?<\/(?:rt|rp)>/gi, '').replace(/<\/?br\s*\/?>/gi, ' ').replace(validTags, '').replace(/[\u200B-\u200D\uFEFF]/g, '').replace(/\s+/g, ' ').trim();
        finalStr = finalStr.replace(/\s+([,.:;!?])/g, '$1');

        const hasNarrative = /[a-zA-Z0-9\u3000-\u303F\u3040-\u309F\u30A0-\u30FF\uFF00-\uFF9F\u4E00-\u9FAF\u3400-\u4DBF]/.test(finalStr);

        let bType = "N";
        if (/<h[1-6]/i.test(currentReadingSentence)) bType = "H";
        else if (/<s\b/i.test(currentReadingSentence) || /class="scene-break"/i.test(currentReadingSentence)) bType = "S";
        else if ((/<img|<svg/i.test(currentReadingSentence) || /\[IMAGE_/i.test(currentReadingSentence)) && !hasNarrative) bType = "Img";
        
        if (finalStr === "" && bType.startsWith("H")) {
            const idMatch = currentReadingSentence.match(/\sid=['"]([^'"]+)['"]/);
            const origMatch = currentReadingSentence.match(/data-orig-id=['"]([^'"]+)['"]/);
            const matchedToc = findTocEntryForPage(
                state.readingPageIndex,
                idMatch ? idMatch[1] : null,
                origMatch ? origMatch[1] : null
            );
            if (matchedToc && matchedToc.title) {
                finalStr = matchedToc.title;
            }
        }
        
        if (bType === "Img") finalStr = "🖼️ [Viewing Image]";
        else if (bType === "S" && finalStr === "") finalStr = "•••";
        
        setMonitorPreview(finalStr, { center: bType === "Img" || bType === "S" });
    }

    const pageImages = document.querySelectorAll('#textContent img.epub-image:not(s img.epub-image)');
    const MIXED_PARENT_TAGS = new Set(['P', 'DIV', 'SPAN', 'N', 'S']);
    const EMPTY_WRAPPER_TAGS = new Set(['SPAN', 'A', 'PICTURE', 'FIGURE']);

    const parentHasLeftoverText = (parent) => {
        if (!parent) return false;
        const clone = parent.cloneNode(true);
        clone.querySelectorAll('img, svg, picture').forEach(node => node.remove());
        return (clone.textContent || '').trim().length > 0;
    };

    const hasSentenceSiblings = (img, parent) => {
        if (!parent) return false;
        return Array.from(parent.childNodes).some(sibling => {
            if (sibling === img || (sibling.contains && sibling.contains(img))) return false;
            if (sibling.nodeType === Node.TEXT_NODE) return sibling.textContent.trim().length > 0;
            if (sibling.nodeType !== Node.ELEMENT_NODE) return false;
            const tag = sibling.tagName.toLowerCase();
            return (tag === 'n' || tag === 's') && (sibling.textContent || '').trim().length > 0;
        });
    };

    const isMixedInlineImage = (img) => {
        let parent = img.parentElement;
        while (parent && parent.id !== 'textContent') {
            if (hasSentenceSiblings(img, parent) || (MIXED_PARENT_TAGS.has(parent.tagName) && parentHasLeftoverText(parent))) {
                return true;
            }
            if (!EMPTY_WRAPPER_TAGS.has(parent.tagName) || parentHasLeftoverText(parent)) {
                return false;
            }
            parent = parent.parentElement;
        }
        return false;
    };

    const pageClone = textContent ? textContent.cloneNode(true) : null;
    if (pageClone) pageClone.querySelectorAll('img, svg, picture').forEach(node => node.remove());
    const pageHasNarrative = pageClone ? (pageClone.textContent || '').trim().length > 0 : false;
    const parentMixed = Array.from(pageImages).some(isMixedInlineImage);
    const htmlEager = Array.from(pageImages).some(img => (img.loading || '').toLowerCase() === 'eager');
    const isSpreadSlot = !!(textContent && textContent.querySelector('.spread-slot'));
    const isMultiImage = pageImages.length > 1 && !isSpreadSlot;
    const hasInlineImages = pageImages.length > 0 && (pageHasNarrative || parentMixed || htmlEager || isMultiImage);

    if (pageImages.length > 0) {

        const markIconIfSmall = (img) => {
            if (img.naturalWidth > 0 && img.naturalWidth < 150 && img.naturalHeight < 150) {
                img.classList.add('epub-icon');
            }
        };

        const realignAfterInlineImages = () => {
            if (isHorizontalMode()) {
                void layoutSpreads({ reset: false }).then(() => {
                    const activeEl = document.querySelector("#textContent .active-sentence");
                    if (activeEl && state.viewPageIndex === state.readingPageIndex && state.autoScrollEnabled) {
                        revealInSpread(activeEl);
                    }
                    updateProgressDisplay();
                    if (typeof updateActiveTOC === "function") updateActiveTOC();
                    updateHorizontalSpreadFocus();
                });
                return;
            }
            const camScroll = document.querySelector(".content-area");
            if (camScroll && state.autoScrollEnabled && state.viewPageIndex === state.readingPageIndex) {
                const activeEl = document.querySelector('#textContent .active-sentence');
                if (activeEl) {
                    const elRect = activeEl.getBoundingClientRect();
                    const containerRect = camScroll.getBoundingClientRect();
                    const relativeTop = elRect.top - containerRect.top + camScroll.scrollTop;
                    const centerPosition = relativeTop - (containerRect.height / 2) + (elRect.height / 2);
                    camScroll.scrollTop = Math.max(0, centerPosition);
                }
            }
            updateProgressDisplay();
            if (typeof updateActiveTOC === "function") updateActiveTOC();
        };

        if (hasInlineImages) {
            const pending = [];
            pageImages.forEach(img => {
                img.loading = "eager";
                img.decoding = "async";
                img.classList.remove('lazy-prep');
                img.classList.add('lazy-loaded');
                markIconIfSmall(img);
                if (!img.complete) {
                    pending.push(new Promise(resolve => {
                        const done = () => {
                            markIconIfSmall(img);
                            resolve();
                        };
                        img.addEventListener('load', done, { once: true });
                        img.addEventListener('error', resolve, { once: true });
                    }));
                }
            });
            const settle = () => requestAnimationFrame(realignAfterInlineImages);
            if (pending.length > 0) Promise.all(pending).then(settle);
            else settle();
        } else {
            const onIllustrationSettled = () => {
                if (isHorizontalMode()) {
                    void layoutSpreads({ reset: false }).then(() => {
                        const activeEl = document.querySelector("#textContent .active-sentence");
                        if (activeEl && state.viewPageIndex === state.readingPageIndex && state.autoScrollEnabled) {
                            revealInSpread(activeEl);
                        }
                        updateProgressDisplay();
                        if (typeof updateActiveTOC === "function") updateActiveTOC();
                        updateHorizontalSpreadFocus();
                    });
                }
            };

            const imgObserver = new IntersectionObserver((entries, observer) => {
                entries.forEach(entry => {
                    if (entry.isIntersecting) {
                        const img = entry.target;

                        const reveal = () => {
                            markIconIfSmall(img);
                            img.classList.remove('lazy-prep');
                            img.classList.add('lazy-loaded');
                            requestAnimationFrame(onIllustrationSettled);
                        };

                        if (img.complete) {
                            reveal();
                        } else {
                            img.addEventListener('load', reveal, { once: true });
                            img.addEventListener('error', reveal, { once: true });
                        }

                        observer.unobserve(img);
                    }
                });
            }, { root: null, rootMargin: '800px 800px' });

            pageImages.forEach(img => {
                markIconIfSmall(img);
                if (isHorizontalMode()) {
                    img.loading = "eager";
                    img.decoding = "async";
                }
                if (img.complete) {
                    img.classList.add('lazy-loaded');
                    requestAnimationFrame(onIllustrationSettled);
                } else {
                    img.classList.add('lazy-prep');
                    imgObserver.observe(img);
                    img.addEventListener('load', () => {
                        markIconIfSmall(img);
                        img.classList.remove('lazy-prep');
                        img.classList.add('lazy-loaded');
                        requestAnimationFrame(onIllustrationSettled);
                    }, { once: true });
                }
            });
        }
    }

   if (state.currentSearchQuery && typeof highlightSearchTerm === "function") highlightSearchTerm(state.currentSearchQuery, state.searchMatchCase, state.searchWholeWord);
    
    const footnoteElements = textContent.querySelectorAll(
        'a[epub\\:type="noteref"], a[epub\\:type="footnote"], a[epub\\:type="backlink"], a[href*="#R_"], a[id*="R_"], .epub-noteref, .epub-footnote, p[epub\\:type="footnote"], div[epub\\:type="footnote"], aside[epub\\:type="footnote"], li[epub\\:type="footnote"], sup a[href*="#"], a.reference, .reference a'
    );

    footnoteElements.forEach(ref => {
        const href = ref.getAttribute('href') || '';
        const id = ref.getAttribute('id') || '';
        const epubType = (ref.getAttribute('epub:type') || '').toLowerCase();
        const isDefinition = epubType === 'footnote' || epubType === 'backlink' || id.startsWith('R_') || ref.classList.contains('epub-footnote');

        if (!isDefinition) {
            // --- 1. CALLOUT / IN-TEXT NOTE (POPUP TRIGGER) ---
            ref.classList.add('relative', 'group', 'cursor-pointer', 'text-blue-400', 'hover:text-blue-300', 'transition-colors', 'font-bold');
            ref.style.display = 'inline';
            ref.style.margin = '0 2px';
            ref.style.padding = '0';

            if (!ref.querySelector('.footnote-tooltip')) {
                const tooltip = document.createElement('span');
                tooltip.className = 'footnote-tooltip absolute bottom-full left-1/2 -translate-x-1/2 mb-1.5 w-max whitespace-nowrap px-2.5 py-1.5 bg-[#27272a] text-zinc-100 text-[11px] font-bold rounded-md opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50 shadow-xl border border-zinc-600';
                tooltip.style.textIndent = '0px';
                tooltip.style.lineHeight = '1';
                tooltip.textContent = 'View Footnote';
                ref.appendChild(tooltip);
            }

            ref.onclick = async (e) => {
                e.preventDefault();
                e.stopPropagation();
                const origId = ref.getAttribute("data-orig-id") || "";
                const target = href || (origId ? `#${origId}` : "");
                if (target) openFootnote(target);
            };
        } else {
            // --- 2. DEFINITION / ENDNOTE (JUMP-BACK TRIGGER) ---
            if (ref.tagName === 'A') {
                ref.classList.add('cursor-pointer', 'text-zinc-900', 'bg-blue-500', 'hover:bg-blue-400', 'font-bold', 'inline-flex', 'items-center', 'px-1.5', 'py-0.5', 'rounded', 'text-[10px]', 'uppercase', 'mx-1');
                const label = ref.textContent.trim();
                const isArrowOnly = !/\d/.test(label) && (
                    /[↩↑⇤←]/.test(label) || /^(back|return)$/i.test(label)
                );
                if (isArrowOnly) {
                    ref.textContent = '↩ Back';
                }
            } else if (ref.tagName === 'LI') {
                ref.classList.add('cursor-pointer', 'hover:bg-blue-900/20', 'rounded', 'transition-colors');
            } else {
                ref.classList.add('cursor-pointer', 'hover:bg-blue-900/20', 'rounded', 'transition-colors', 'block', 'p-1');
            }
            
            ref.onclick = async (e) => {
                e.preventDefault();
                e.stopPropagation();

                const origId = ref.getAttribute("data-orig-id") || "";
                const cleanHref = href.split("#").pop();
                const cleanId = origId || id;
                if (!cleanHref && !cleanId) return;

                const peeked = hasBookPath(state.currentDoc);
                let targetPageIdx = -1;
                let foundSelector = "";

                if (peeked) {
                    const rec = lookupFootnoteMap(cleanId, href, "") || lookupFootnoteMap(cleanHref, href, "");
                    const calloutPage = Number.isInteger(rec?.callout_page_index) ? rec.callout_page_index : -1;
                    const calloutId = rec?.callout_id || cleanHref;
                    if (calloutPage >= 0) {
                        await ensurePeekedPage(calloutPage);
                        targetPageIdx = calloutPage;
                        foundSelector = footnotePointerSelector(calloutId, true);
                        if (calloutId) {
                            foundSelector += `, [href="#${escapeCssAttr(calloutId)}"]`;
                        }
                    }
                }

                if (targetPageIdx < 0) {
                const searchOrder = [];
                for (let i = state.viewPageIndex - 1; i >= 0; i--) searchOrder.push(i);
                searchOrder.push(state.viewPageIndex);
                for (let i = state.viewPageIndex + 1; i < state.currentPages.length; i++) searchOrder.push(i);

                for (const idx of searchOrder) {
                    const pageHtml = state.currentPages[idx];
                    if (!pageHtml) continue;

                    if (peeked) {
                        if (cleanHref && (pageHasOrigAnchor(pageHtml, cleanHref) || pageHasAnchorId(pageHtml, cleanHref))) {
                            targetPageIdx = idx;
                            foundSelector = footnotePointerSelector(cleanHref, true);
                            break;
                        }
                        if (cleanId && (pageHtml.includes(`href="#${cleanId}"`) || pageHtml.includes(`href='#${cleanId}'`))) {
                            targetPageIdx = idx;
                            foundSelector = `[href="#${escapeCssAttr(cleanId)}"]`;
                            break;
                        }
                    } else if (cleanHref && pageHasAnchorId(pageHtml, cleanHref)) {
                        targetPageIdx = idx;
                        foundSelector = footnotePointerSelector(cleanHref, false);
                        break;
                    } else if (cleanId && (pageHtml.includes(`href="#${cleanId}"`) || pageHtml.includes(`href='#${cleanId}'`))) {
                        targetPageIdx = idx;
                        foundSelector = `[href="#${cleanId}"]`;
                        break;
                    } else if (cleanHref && (pageHtml.includes(`href="#${cleanHref}"`) || pageHtml.includes(`href='#${cleanHref}'`))) {
                        targetPageIdx = idx;
                        foundSelector = `[href="#${cleanHref}"]`;
                        break;
                    }
                }
                }

                if (targetPageIdx !== -1) {
                    if (peeked) await ensurePeekedPage(targetPageIdx);
                    state.viewPageIndex = targetPageIdx;
                    state.autoScrollEnabled = false;
                    await renderPage();

                    setTimeout(() => {
                        const targetEl = document.querySelector(foundSelector);
                        if (targetEl) {
                            const scrollTarget = targetEl.closest('.sentence') || targetEl;
                            scrollTarget.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            scrollTarget.classList.add('bg-blue-600/40', 'rounded', 'px-1', 'transition-colors', 'duration-500');
                            setTimeout(() => scrollTarget.classList.remove('bg-blue-600/40', 'px-1'), 1500);
                        }
                    }, 100);
                } else {
                    if (typeof showToast === "function") showToast("Original note position not found.");
                }
            };
        }
    });

    // 🌟 EXTERNAL LINK HANDLER & LIGHT BLUE HIGHLIGHT
    textContent.querySelectorAll('a.external-link').forEach(link => {
        // Light blue styling + hover indicator
        link.classList.add(
            'text-sky-400',
            'hover:text-sky-300',
            'underline',
            'underline-offset-2',
            'decoration-sky-400/50',
            'hover:decoration-sky-300',
            'cursor-pointer',
            'transition-colors'
        );

        link.addEventListener('click', (e) => {
            e.preventDefault();
            e.stopPropagation();
            const url = link.getAttribute('href');
            if (!url) return;

            const ok = window.confirm(
                `Open external link in browser?\n\n${url}\n\nCaution: External links may be unsafe.`
            );
            if (!ok) return;

            if (window.pywebview && window.pywebview.api && window.pywebview.api.open_external) {
                window.pywebview.api.open_external(url);
            } else {
                window.open(url, '_blank', 'noopener,noreferrer');
            }
        });
    });

    try { updateActiveTOC(); } catch (e) { console.error("TOC Sync Error:", e); }
}

export function updateActiveTOC() {
    const tocList = document.getElementById('tocList');
    if (!tocList || !state.tocMap || state.tocMap.length === 0) return;
    
    const items = tocList.children;
    if (!items || items.length === 0 || items[0].classList.contains('italic')) return;

    let lastPrevIdx = -1;
    for (let i = 0; i < state.tocMap.length; i++) {
        if (state.tocMap[i].page_index < state.viewPageIndex) lastPrevIdx = i;
    }

    const currentPageTOC = [];
    for (let i = 0; i < state.tocMap.length; i++) {
        if (state.tocMap[i].page_index === state.viewPageIndex) {
            currentPageTOC.push({ index: i, item: state.tocMap[i] });
        }
    }

    let activeIdx = lastPrevIdx;

    if (currentPageTOC.length > 0) {
        let matchedIdx = -1;
        const scrollContainer = document.querySelector(".content-area");
        
        let querySelectors = ["#textContent h1", "#textContent h2", "#textContent h3", "#textContent h4", "#textContent h5", "#textContent h6", "#textContent .book-heading"];
        currentPageTOC.forEach(t => {
            const mappedIds = [t.item.target_tts_id, t.item.anchor_id, t.item.id].filter(Boolean);
            mappedIds.forEach(mappedId => {
                const escaped = escapeCssAttr(mappedId);
                querySelectors.push(`#textContent [id="${escaped}"]`);
                querySelectors.push(`#textContent [data-block-id="${escaped}"]`);
                querySelectors.push(`#textContent [data-orig-id="${escaped}"]`);
            });
        });
        
        const rawHeadings = Array.from(document.querySelectorAll(querySelectors.join(", ")));
        const renderedHeadings = [...new Set(rawHeadings)].sort((a, b) => {
            return a.getBoundingClientRect().top - b.getBoundingClientRect().top;
        });
        
        if (renderedHeadings.length > 0 && scrollContainer) {
            const containerTop = scrollContainer.getBoundingClientRect().top;
            const triggerPoint = scrollContainer.scrollTop < 10 ? scrollContainer.clientHeight : scrollContainer.clientHeight * 0.6; 
            
            let activeDomIndex = -1;
            for (let i = renderedHeadings.length - 1; i >= 0; i--) {
                const rect = renderedHeadings[i].getBoundingClientRect();
                const relativeTop = rect.top - containerTop;
                if (relativeTop <= triggerPoint) {
                    activeDomIndex = i;
                    break;
                }
            }

            if (activeDomIndex !== -1) {
                const activeEl = renderedHeadings[activeDomIndex];
                const activeId = activeEl.getAttribute('id');
                const origId = activeEl.getAttribute('data-orig-id');
                const blockId = activeEl.getAttribute('data-block-id');
                
                let foundMatch = false;
                for (let i = currentPageTOC.length - 1; i >= 0; i--) {
                    const tocItem = currentPageTOC[i].item;
                    const mappedIds = [tocItem.target_tts_id, tocItem.anchor_id, tocItem.id].filter(Boolean);
                    if (mappedIds.some(mappedId => (
                        activeId === mappedId || origId === mappedId || blockId === mappedId
                    ))) {
                        matchedIdx = currentPageTOC[i].index;
                        foundMatch = true;
                        break;
                    }
                }
                
                if (!foundMatch) {
                    const fallbackIndex = Math.min(activeDomIndex, currentPageTOC.length - 1);
                    matchedIdx = currentPageTOC[fallbackIndex].index;
                }
            } else {
                if (lastPrevIdx === -1) matchedIdx = currentPageTOC[0].index;
            }
        } else {
             matchedIdx = currentPageTOC[0].index;
        }
        
        if (matchedIdx !== -1) activeIdx = matchedIdx;
    }

    if (activeIdx === -1) activeIdx = 0;

    for (let i = 0; i < items.length; i++) {
        const div = items[i];
        const titleSpan = div.querySelector('span.truncate');
        const isMatch = (i === activeIdx);
        
        div.classList.toggle('bg-blue-600/20', isMatch);
        div.classList.toggle('border-blue-500', isMatch);
        div.classList.toggle('border-transparent', !isMatch);
        
        if (titleSpan) {
            titleSpan.classList.toggle('text-blue-400', isMatch);
            titleSpan.classList.toggle('brightness-125', isMatch);
        }
    }
}